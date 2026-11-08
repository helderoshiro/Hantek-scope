import re

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

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
    [
        (200e-6, "s", "200 µs"),
        (48e6, "S/s", "48 MS/s"),
        (0.05, "V", "50 mV"),
        (1.0, "V", "1 V"),
        (-0.2, "V", "-200 mV"),
        (0.0, "s", "0 s"),
        (999.97, "Hz", "1 kHz"),
        (999.94e-6, "s", "999.9 µs"),
    ],
)
def test_format_si(value, unit, expected):
    assert format_si(value, unit) == expected


def test_window_draws_frames_and_stops(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window.start()
    try:
        assert wait_until(lambda: window._curves[0].xData is not None)
        x = window._curves[0].xData
        # A captura cobre as 10 divisões da tela, com ou sem disparo.
        assert x[0] <= 0.01
        assert x[-1] >= 9.99
    finally:
        window.stop()
    assert window._worker is None


def test_volts_per_div_rescales_the_stopped_trace(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window.start()
    assert wait_until(lambda: window._curves[0].yData is not None)
    window.stop()

    before = window._curves[0].yData.copy()
    combo = window._channels[0].volts_per_div
    combo.setCurrentIndex(combo.findData(2.0))

    np.testing.assert_allclose(window._curves[0].yData, before / 2)


def test_disabled_channel_is_hidden(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
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


def test_changing_source_clears_the_old_trace(qapp):
    first = SimulatedScope(seed=1)
    second = SimulatedScope(seed=2)
    second.name = "Outra fonte"
    window = MainWindow([first, second])
    window.start()
    assert wait_until(lambda: window._curves[0].yData is not None)
    window.stop()

    window._source.setCurrentIndex(1)

    assert window._last_frame is None
    assert window._curves[0].yData is None
    assert "Outra fonte" in window.windowTitle()
    assert not window._run_button.isChecked()


def test_volts_per_div_reaches_the_capture_config(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    combo = window._channels[1].volts_per_div
    combo.setCurrentIndex(combo.findData(0.1))
    assert window._current_config().volts_per_div == (1.0, 0.1)


def trace_value_at(curve, x):
    return np.interp(x, curve.xData, curve.yData)


def test_trigger_holds_the_edge_at_the_trigger_position(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window.start()
    try:
        for _ in range(3):
            window._trigger_at = None
            assert wait_until(lambda: window._trigger_at is not None)
            position = window._plot.trigger_position.value()
            curve = window._curves[0]
            # CH1 é um seno de 2 V: na posição do disparo ele cruza 0 V subindo.
            assert trace_value_at(curve, position) == pytest.approx(0, abs=0.1)
            assert trace_value_at(curve, position + 0.2) > trace_value_at(curve, position - 0.2)
    finally:
        window.stop()


def test_normal_mode_waits_when_level_is_out_of_reach(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._trigger_mode.setCurrentIndex(window._trigger_mode.findData("normal"))
    window._trigger_level.setValue(3.0)  # Acima do pico de 2 V do CH1.
    window.start()
    QTest.qWait(400)
    window.stop()
    assert window._last_frame is None


def test_auto_mode_draws_without_trigger(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._trigger_level.setValue(3.0)
    window.start()
    try:
        assert wait_until(lambda: window._last_frame is not None)
        assert window._trigger_at is None
        assert window._trigger_status.text() == "Sem disparo (auto)"
    finally:
        window.stop()


def test_single_mode_stops_after_one_trigger(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._trigger_mode.setCurrentIndex(window._trigger_mode.findData("single"))
    window.start()
    assert wait_until(lambda: not window._run_button.isChecked())
    assert window._trigger_at is not None
    assert window._trigger_status.text() == "Capturado (único)"


def test_dragging_the_level_line_updates_the_level_in_volts(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    combo = window._channels[0].volts_per_div
    combo.setCurrentIndex(combo.findData(0.5))
    window._plot.trigger_level.setValue(2)
    assert window._trigger_level.value() == pytest.approx(1.0)


def test_axes_show_time_from_trigger_and_volts(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    ticks = window._plot.getAxis("bottom")._tickLevels[0]
    labels = dict(ticks)
    assert labels[5] == "0 s"
    assert labels[6] == "200 µs"
    assert labels[4] == "-200 µs"
    left = dict(window._plot.getAxis("left")._tickLevels[0])
    assert left[2] == "2 V"


def test_disabled_channel_hides_its_axis_values(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._channels[1].enabled.setChecked(False)
    right = dict(window._plot.getAxis("right")._tickLevels[0])
    assert right[2] == ""


def test_time_cursors_report_difference_to_the_first(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    for _ in range(3):
        window._add_cursor(vertical=True)
    t1, t2, t3 = window._plot.time_cursors
    t1.setValue(5)  # 0 s, no ponto do disparo.
    t2.setValue(10)  # 1 ms
    t3.setValue(6)  # 200 µs
    lines = window._cursor_readout.text().splitlines()
    assert lines[0].split() == ["T1", "0", "s"]
    assert lines[1].split()[4:] == ["1", "ms", "1/ΔT1", "1", "kHz"]
    assert lines[2].split()[4:] == ["200", "µs", "1/ΔT1", "5", "kHz"]
    assert window._cursor_readout.isVisibleTo(window._plot)


def test_volt_cursors_use_the_selected_channel_scale(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    combo = window._channels[1].volts_per_div
    combo.setCurrentIndex(combo.findData(0.5))
    window._cursor_channel.setCurrentIndex(1)
    window._add_cursor(vertical=False)
    window._add_cursor(vertical=False)
    window._plot.volt_cursors[0].setValue(2)
    window._plot.volt_cursors[1].setValue(-1)
    lines = window._cursor_readout.text().splitlines()
    assert lines[0].split() == ["V1", "1", "V"]
    assert lines[1].split() == ["V2", "-500", "mV", "ΔV1", "-1.5", "V"]
    assert lines[2] == "(tensão do CH2)"


def test_removing_a_cursor_renumbers_the_rest(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    for _ in range(3):
        window._add_cursor(vertical=True)
    middle = window._plot.time_cursors[1]
    window._plot.cursor_remove_requested.emit(middle)
    assert len(window._plot.time_cursors) == 2
    assert [line.label.format for line in window._plot.time_cursors] == ["T1", "T2"]
    assert middle not in window._plot.items()


def test_cursor_count_is_limited(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    for _ in range(10):
        window._add_cursor(vertical=False)
    assert len(window._plot.volt_cursors) == 6
    assert not window._add_volt_cursor.isEnabled()
    window._clear_cursors()
    assert window._plot.volt_cursors == []
    assert window._add_volt_cursor.isEnabled()
    assert not window._cursor_readout.isVisibleTo(window._plot)


def measured(row, name, unit):
    """Lê um valor da linha de medições, como 'f 1 kHz', em unidades base."""
    match = re.search(rf"{name} (-?[\d.]+) ([kmµ]?){unit}", row)
    prefix = {"": 1, "k": 1e3, "m": 1e-3, "µ": 1e-6}[match.group(2)]
    return float(match.group(1)) * prefix


def run_until_triggered(window):
    window.start()
    try:
        assert wait_until(lambda: window._trigger_at is not None)
    finally:
        window.stop()


def test_measurements_show_frequency_and_levels(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    run_until_triggered(window)
    ch1, ch2 = window._measurement_readout.text().split("<br>")
    # CH1: seno de 1 kHz e 2 V; CH2: quadrada de 2,5 kHz e ±1 V.
    assert measured(ch1, "f", "Hz") == pytest.approx(1e3, rel=1e-3)
    assert measured(ch1, "T", "s") == pytest.approx(1e-3, rel=1e-3)
    assert measured(ch1, "RMS", "V") == pytest.approx(2 / np.sqrt(2), rel=0.02)
    assert measured(ch2, "f", "Hz") == pytest.approx(2.5e3, rel=1e-3)
    assert measured(ch2, "Vpp", "V") == pytest.approx(2, abs=0.15)


def test_measurements_can_be_hidden(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    run_until_triggered(window)
    assert window._measurement_readout.isVisibleTo(window._plot)
    window._measurements_on.setChecked(False)
    assert not window._measurement_readout.isVisibleTo(window._plot)


def test_pause_freezes_the_screen_while_acquiring(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    assert not window._pause_button.isEnabled()
    window.start()
    try:
        assert wait_until(lambda: window._last_frame is not None)
        window._pause_button.setChecked(True)
        frozen = window._last_frame
        QTest.qWait(300)
        assert window._last_frame is frozen
        assert window._worker is not None
        window._pause_button.setChecked(False)
        assert wait_until(lambda: window._last_frame is not frozen)
    finally:
        window.stop()
    assert not window._pause_button.isChecked()
    assert not window._pause_button.isEnabled()


def test_save_image_adds_a_footer(qapp, tmp_path):
    window = MainWindow([SimulatedScope(seed=1)])
    run_until_triggered(window)
    path = tmp_path / "tela.png"
    window.save_image(path)
    image = QImage(str(path))
    assert not image.isNull()
    assert image.height() == window._plot.height() + 26


def test_save_data_writes_the_visible_window(qapp, tmp_path):
    window = MainWindow([SimulatedScope(seed=1)])
    run_until_triggered(window)
    path = tmp_path / "dados.csv"
    window.save_data(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    header = [line for line in lines if line.startswith("#")]
    assert any("disparado: sim" in line for line in header)
    assert lines[len(header)] == "tempo_s,CH1_V,CH2_V"
    values = np.array([[float(v) for v in line.split(",")] for line in lines[len(header) + 1 :]])
    # A 200 µs/div, a tela vai de -1 ms a +1 ms em torno do disparo.
    assert values[0, 0] == pytest.approx(-1e-3, abs=1e-6)
    assert values[-1, 0] == pytest.approx(1e-3, abs=1e-6)
    assert values[:, 1].max() == pytest.approx(2, abs=0.1)


def test_save_data_for_excel_in_portuguese(qapp, tmp_path):
    window = MainWindow([SimulatedScope(seed=1)])
    window._channels[1].enabled.setChecked(False)
    run_until_triggered(window)
    path = tmp_path / "dados.csv"
    window.save_data(path, excel_ptbr=True)
    lines = [line for line in path.read_text(encoding="utf-8-sig").splitlines() if not line.startswith("#")]
    assert lines[0] == "tempo_s;CH1_V"
    for line in lines[1:20]:
        time_text, volts_text = line.split(";")
        assert "." not in time_text + volts_text
        float(time_text.replace(",", "."))
        float(volts_text.replace(",", "."))


def test_mouse_position_text(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    assert window._position_text(6, 2) == "t = 200 µs   CH1 = 2 V   CH2 = 2 V"


@pytest.mark.parametrize(
    ("modifier", "combo_of"),
    [
        (Qt.KeyboardModifier.NoModifier, lambda w: w._time_per_div),
        (Qt.KeyboardModifier.ControlModifier, lambda w: w._channels[0].volts_per_div),
        (Qt.KeyboardModifier.ShiftModifier, lambda w: w._channels[1].volts_per_div),
    ],
)
def test_wheel_up_zooms_in(qapp, modifier, combo_of):
    window = MainWindow([SimulatedScope(seed=1)])
    combo = combo_of(window)
    before = combo.currentIndex()
    event = QWheelEvent(
        QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, 120),
        Qt.MouseButton.NoButton, modifier, Qt.ScrollPhase.NoScrollPhase, False,
    )
    QApplication.sendEvent(window._plot.viewport(), event)
    assert combo.currentIndex() == before - 1


def test_wheel_does_not_go_past_the_last_scale(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._on_wheel(-100, Qt.KeyboardModifier.NoModifier)
    assert window._time_per_div.currentIndex() == window._time_per_div.count() - 1
