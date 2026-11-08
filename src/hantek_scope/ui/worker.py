import threading
import time
from collections import deque
from dataclasses import replace

from PySide6.QtCore import QThread, Signal

from hantek_scope.acquisition.base import CaptureConfig, Frame, ScopeBackend

MIN_FRAME_INTERVAL_S = 1 / 30
# No modo roll a interface usa todos os quadros; este é o atraso máximo tolerado.
MAX_PENDING_FRAMES = 64


class AcquisitionWorker(QThread):
    """Lê quadros do backend sem bloquear a interface.

    take_frame() devolve só o quadro mais recente e descarta os anteriores, para
    a interface não acumular atraso. take_frames() devolve todos os pendentes,
    para o modo roll, que precisa de cada trecho do sinal.
    """

    frame_ready = Signal()
    failed = Signal(str)

    def __init__(self, backend: ScopeBackend, config: CaptureConfig, parent=None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._lock = threading.Lock()
        self._config = config
        self._pending: deque[Frame] = deque(maxlen=MAX_PENDING_FRAMES)

    def set_config(self, config: CaptureConfig) -> None:
        with self._lock:
            self._config = config

    @property
    def _latest(self) -> Frame | None:
        return self._pending[-1] if self._pending else None

    def take_frame(self) -> Frame | None:
        frames = self.take_frames()
        return frames[-1] if frames else None

    def take_frames(self) -> list[Frame]:
        with self._lock:
            frames = list(self._pending)
            self._pending.clear()
        return frames

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
            frame = replace(self._backend.read_frame(), end_time=time.monotonic())
            with self._lock:
                already_notified = bool(self._pending)
                self._pending.append(frame)
            if not already_notified:
                self.frame_ready.emit()
            remaining = MIN_FRAME_INTERVAL_S - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
