import numpy as np
import pytest
from PySide6.QtTest import QTest

from hantek_scope.acquisition.base import CaptureConfig, Frame
from hantek_scope.acquisition.simulator import SimulatedScope
from hantek_scope.ui.main_window import MainWindow, format_si
from hantek_scope.ui.worker import AcquisitionWorker


class FailingBackend(SimulatedScope):
    def read_frame(self) -> Frame:
        raise OSError("aparelho desconectado")


def wait_until(predicate, timeout_ms=3000):
    waited = 0
    while not predicate() and waited < timeout_ms:
        QTest.qWait(20)
        waited += 20
    return predicate()


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [(200e-6, "s", "200 µs"), (48e6, "S/s", "48 MS/s"), (0.05, "V", "50 mV"), (1.0, "V", "1 V")],
)
def test_format_si(value, unit, expected):
    assert format_si(value, unit) == expected


def test_window_draws_frames_and_stops(qapp):
    window = MainWindow(SimulatedScope(seed=1))
    window.start()
    try:
        assert wait_until(lambda: window._curves[0].xData is not None)
        x = window._curves[0].xData
        # A captura padrão ocupa as 10 divisões horizontais.
        assert x[-1] == pytest.approx(10, rel=1e-2)
    finally:
        window.stop()
    assert window._worker is None


def test_volts_per_div_rescales_the_stopped_trace(qapp):
    window = MainWindow(SimulatedScope(seed=1))
    window.start()
    assert wait_until(lambda: window._curves[0].yData is not None)
    window.stop()

    before = window._curves[0].yData.copy()
    combo = window._channels[0].volts_per_div
    combo.setCurrentIndex(combo.findData(2.0))

    np.testing.assert_allclose(window._curves[0].yData, before / 2)


def test_disabled_channel_is_hidden(qapp):
    window = MainWindow(SimulatedScope(seed=1))
    window.start()
    assert wait_until(lambda: window._curves[1].yData is not None)
    window.stop()

    window._channels[1].enabled.setChecked(False)

    assert not window._curves[1].isVisible()
    assert window._curves[0].isVisible()


def test_worker_reports_backend_failure(qapp):
    backend = FailingBackend(realtime=False)
    worker = AcquisitionWorker(backend, CaptureConfig(1e6, 100))
    errors = []
    worker.failed.connect(errors.append)
    worker.start()
    assert wait_until(lambda: errors)
    worker.wait()
    assert errors == ["aparelho desconectado"]


def test_worker_keeps_only_the_latest_frame(qapp):
    worker = AcquisitionWorker(SimulatedScope(realtime=False, seed=1), CaptureConfig(1e6, 100))
    worker.start()
    try:
        assert wait_until(lambda: worker._latest is not None)
    finally:
        worker.requestInterruption()
        worker.wait()
    assert worker.take_frame() is not None
    assert worker.take_frame() is None
