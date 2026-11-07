import threading
import time

from PySide6.QtCore import QThread, Signal

from hantek_scope.acquisition.base import CaptureConfig, Frame, ScopeBackend

MIN_FRAME_INTERVAL_S = 1 / 30


class AcquisitionWorker(QThread):
    """Lê quadros do backend sem bloquear a interface.

    Só o quadro mais recente é guardado. Se a interface atrasar, os quadros
    intermediários são descartados em vez de formar uma fila.
    """

    frame_ready = Signal()
    failed = Signal(str)

    def __init__(self, backend: ScopeBackend, config: CaptureConfig, parent=None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._lock = threading.Lock()
        self._config = config
        self._latest: Frame | None = None

    def set_config(self, config: CaptureConfig) -> None:
        with self._lock:
            self._config = config

    def take_frame(self) -> Frame | None:
        with self._lock:
            frame, self._latest = self._latest, None
        return frame

    def run(self) -> None:
        try:
            self._backend.open()
            try:
                self._acquire_until_interrupted()
            finally:
                self._backend.close()
        except Exception as exc:
            self.failed.emit(str(exc))

    def _acquire_until_interrupted(self) -> None:
        applied = None
        while not self.isInterruptionRequested():
            started = time.monotonic()
            with self._lock:
                config = self._config
            if config != applied:
                self._backend.configure(config)
                applied = config
            frame = self._backend.read_frame()
            with self._lock:
                already_notified = self._latest is not None
                self._latest = frame
            if not already_notified:
                self.frame_ready.emit()
            remaining = MIN_FRAME_INTERVAL_S - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
