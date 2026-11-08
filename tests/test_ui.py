import re

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from hantek_scope.acquisition.base import CaptureConfig, Frame
from hantek_scope.acquisition.simulator import SimulatedScope, SimulatedSignal
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


# Ponteira, posição, acoplamento, inversão, filtro e MATH


def show_frame(window, *channels, rate=1e6):
    """Põe um quadro na tela sem passar pela aquisição."""
    window._last_frame = Frame(sample_rate=rate, channels=np.array(channels, dtype=float))
    window._trigger_at = None
    window._on_settings_changed()


def axis_labels(window, side):
    return dict(window._plot.getAxis(side)._tickLevels[0])


def test_probe_10x_scales_labels_traces_and_trigger(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._trigger_level.setValue(0.5)
    channel = window._channels[0]
    channel.probe.setCurrentIndex(channel.probe.findData(10.0))
    assert channel.volts_per_div.currentText() == "10 V"
    # A faixa de entrada continua a mesma: só a leitura muda.
    assert window._current_config().volts_per_div[0] == 1.0
    assert window._trigger_level.value() == pytest.approx(5.0)
    assert axis_labels(window, "left")[2] == "20 V"
    show_frame(window, np.full(2000, 0.3), np.zeros(2000))
    assert window._traces[0] == pytest.approx(3.0)
    assert window._curves[0].yData == pytest.approx(0.3)  # 3 V a 10 V/div.


def test_position_moves_the_trace_and_the_axis(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._channels[0].position.setValue(2)
    assert axis_labels(window, "left")[2] == "0 V"
    assert axis_labels(window, "left")[-2] == "-4 V"
    show_frame(window, np.full(2000, 1.0), np.zeros(2000))
    assert window._curves[0].yData == pytest.approx(3.0)
    marker = window._plot._zero_markers["1"]
    assert marker.pos().y() == pytest.approx(2)


def test_trigger_line_follows_the_position(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._trigger_level.setValue(1.0)
    window._channels[0].position.setValue(-1)
    assert window._plot.trigger_level.value() == pytest.approx(0)
    window._plot.trigger_level.setValue(1)
    assert window._trigger_level.value() == pytest.approx(2.0)


def test_zero_marker_hides_with_its_trace(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._math_operation.setCurrentIndex(window._math_operation.findData("add"))
    window._math_position.setValue(4)
    assert window._plot._zero_markers["M"].isVisible()
    window._math_operation.setCurrentIndex(0)
    assert not window._plot._zero_markers["M"].isVisible()


def test_inverted_channel(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._channels[1].inverted.setChecked(True)
    show_frame(window, np.zeros(2000), np.full(2000, 0.5))
    assert window._traces[1] == pytest.approx(-0.5)
    assert "inv" in window._scale_label.text()


def test_ac_coupling_removes_dc_and_picks_the_input_range(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    channel = window._channels[0]
    channel.volts_per_div.setCurrentIndex(channel.volts_per_div.findData(0.05))
    channel.coupling.setCurrentIndex(channel.coupling.findData(True))
    t = np.arange(20_000) / 1e6
    signal = 3.0 + 0.1 * np.sin(2 * np.pi * 1e3 * t)
    show_frame(window, signal, np.zeros(t.size))
    assert window._traces[0].mean() == pytest.approx(0, abs=1e-3)
    # Os 3 V de contínuo não cabem na faixa de 50 mV/div: ela sobe com o sinal.
    window._adjust_ac_input_ranges(window._last_frame)
    assert window._current_config().volts_per_div[0] == 0.1
    for _ in range(5):
        window._adjust_ac_input_ranges(window._last_frame)
    assert window._current_config().volts_per_div[0] == 1.0
    channel.coupling.setCurrentIndex(channel.coupling.findData(False))
    assert window._current_config().volts_per_div[0] == 0.05


def test_mains_filter_cleans_the_trace(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._time_per_div.setCurrentIndex(window._time_per_div.findData(2e-3))
    t = np.arange(40_001) / 1e6
    clean = np.sin(2 * np.pi * 1e3 * t)
    hum = 0.5 * np.sin(2 * np.pi * 60 * t + 1)
    show_frame(window, clean + hum, np.zeros(t.size))
    window._channels[0].mains_filter.setChecked(True)
    assert np.abs(window._traces[0] - clean).max() < 1e-3
    assert not window._channels[0].mains_note.isVisibleTo(window)


def test_mains_filter_warns_when_the_capture_is_too_short(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._channels[0].mains_filter.setChecked(True)
    assert window._channels[0].mains_note.isVisibleTo(window)
    assert "2 ms/div" in window._channels[0].mains_note.text()


def test_math_trace_and_its_measurements(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    t = np.arange(2001) / 1e6
    ch1 = np.sin(2 * np.pi * 5e3 * t)
    ch2 = np.full(t.size, 0.5)
    show_frame(window, ch1, ch2)
    window._math_operation.setCurrentIndex(window._math_operation.findData("add"))
    assert window._math == pytest.approx(ch1 + 0.5)
    # "Ajustar escala": Vpp de 2 V cabe em 6 divisões a 500 mV/div, centrado.
    assert window._math_scale.currentData() == 0.5
    assert window._math_position.value() == pytest.approx(-1.0)
    rows = window._measurement_readout.text().split("<br>")
    math_row = rows[-1]
    assert "MATH" in math_row
    assert measured(math_row, "Vmáx", "V") == pytest.approx(1.5, abs=0.01)
    assert measured(math_row, "f", "Hz") == pytest.approx(5e3, rel=1e-3)
    assert "MATH CH1 + CH2 500 mV/div" in window._scale_label.text()


def test_math_units_follow_the_operation(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    show_frame(window, np.full(2000, 2.0), np.full(2000, 3.0))
    window._math_operation.setCurrentIndex(window._math_operation.findData("mul"))
    assert window._math == pytest.approx(6.0)
    assert window._math_scale.currentText().endswith("V²")
    assert "MATH = " in window._position_text(5, 0)
    assert window._position_text(5, 0).endswith("V²")


def test_math_division_leaves_gaps_near_zero(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    ch2 = np.where(np.arange(2000) < 1000, 2.0, 0.0)
    show_frame(window, np.full(2000, 1.0), ch2)
    window._math_operation.setCurrentIndex(window._math_operation.findData("div"))
    assert window._math[:1000] == pytest.approx(0.5)
    assert np.isnan(window._math[1000:]).all()
    assert "MATH" in window._measurement_readout.text()


def test_volt_cursors_on_the_math_trace(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._cursor_channel.setCurrentIndex(window._cursor_channel.findData(2))
    window._add_cursor(vertical=False)
    assert window._cursor_readout.text().splitlines()[-1] == "(MATH desligado)"
    window._math_operation.setCurrentIndex(window._math_operation.findData("sub"))
    window._math_scale.setCurrentIndex(window._math_scale.findData(2.0))
    window._math_position.setValue(1)
    window._plot.volt_cursors[0].setValue(2)
    lines = window._cursor_readout.text().splitlines()
    assert lines[0].split() == ["V1", "2", "V"]
    assert lines[1] == "(tensão do MATH)"


def test_save_data_includes_math_and_channel_settings(qapp, tmp_path):
    window = MainWindow([SimulatedScope(seed=1)])
    window._channels[0].probe.setCurrentIndex(1)
    show_frame(window, np.full(20_001, 0.2), np.full(20_001, 1.0))
    window._math_operation.setCurrentIndex(window._math_operation.findData("mul"))
    path = tmp_path / "dados.csv"
    window.save_data(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    header = [line for line in lines if line.startswith("#")]
    assert any("CH1: 10 V/div, ponteira 10x, acoplamento DC" in line for line in header)
    assert any(line.startswith("# MATH: CH1 × CH2") for line in header)
    assert lines[len(header)] == "tempo_s,CH1_V,CH2_V,MATH_V2"
    first = [float(v) for v in lines[len(header) + 1].split(",")]
    assert first[1:] == pytest.approx([2.0, 1.0, 2.0])


def test_math_turned_on_before_running_fits_on_the_first_frame(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._math_operation.setCurrentIndex(window._math_operation.findData("mul"))
    window._math_scale.setCurrentIndex(window._math_scale.findData(50.0))
    assert window._math_fit_pending
    run_until_triggered(window)
    # Seno de 2 V vezes quadrada de 1 V: de -2 V² a 2 V², cabe em 6 div a 1 V²/div.
    assert window._math_scale.currentData() == 1.0
    assert not window._math_fit_pending


# Zoom no mouse e deslocamento horizontal


def test_wheel_zoom_keeps_the_instant_under_the_mouse(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    assert window._time_at(8) == pytest.approx(600e-6)
    window._on_wheel(1, Qt.KeyboardModifier.NoModifier, 8.0)
    assert window._tdiv() == pytest.approx(100e-6)
    assert window._time_at(8) == pytest.approx(600e-6)
    assert window._trigger_x == pytest.approx(2.0)


def test_zooming_far_from_the_trigger_moves_it_off_screen(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._on_wheel(5, Qt.KeyboardModifier.NoModifier, 9.0)  # 200 µs -> 2 µs/div.
    assert window._time_at(9) == pytest.approx(800e-6)
    assert window._trigger_x < 0
    assert not window._plot.trigger_position.isVisible()
    assert window._plot._trigger_arrow.isVisible()
    # A captura cobre o trecho entre o disparo e a tela.
    config = window._current_config()
    assert config.n_samples / config.sample_rate >= 800e-6 + 10 * 2e-6
    window._on_wheel(-5, Qt.KeyboardModifier.NoModifier, 9.0)
    assert window._trigger_x == pytest.approx(5.0)
    assert window._plot.trigger_position.isVisible()


def test_dragging_the_background_pans_and_the_button_recenters(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._plot.dragged.emit(1.5)
    assert window._trigger_x == pytest.approx(6.5)
    assert window._plot.trigger_position.value() == pytest.approx(6.5)
    assert dict(window._plot.getAxis("bottom")._tickLevels[0])[0] == "-1.3 ms"
    window._set_trigger_x(5.0)
    assert window._trigger_x == 5.0


def test_trigger_offset_is_limited_by_the_capture_memory(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._set_trigger_x(-1e9)
    config = window._current_config()
    assert config.n_samples <= 100_001


def test_off_screen_trigger_shows_the_waveform_after_it(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._time_per_div.setCurrentIndex(window._time_per_div.findData(100e-6))
    # Disparo 10 divisões (1 ms, um período do CH1) antes da borda esquerda.
    window._set_trigger_x(-10)
    run_until_triggered(window)
    curve = window._curves[0]
    assert trace_value_at(curve, 0.05) == pytest.approx(0, abs=0.15)
    assert trace_value_at(curve, 0.5) > trace_value_at(curve, 0.05)


# Modo roll e taxas com um canal


class TwoSpeedScope(SimulatedScope):
    sample_rates = (1e6, 8e6)
    single_channel_rates = (1e6, 8e6, 16e6)


def test_single_channel_unlocks_faster_rates(qapp):
    window = MainWindow([TwoSpeedScope(seed=1)])
    window._time_per_div.setCurrentIndex(window._time_per_div.findData(1e-6))
    assert window._current_config().sample_rate == 8e6
    window._channels[1].enabled.setChecked(False)
    config = window._current_config()
    assert (config.sample_rate, config.channels) == (16e6, 1)
    assert "só CH1" in window._scale_label.text()
    window._math_operation.setCurrentIndex(window._math_operation.findData("add"))
    assert window._current_config().channels == 2
    window._math_operation.setCurrentIndex(0)
    window._trigger_source.setCurrentIndex(1)
    assert window._current_config().channels == 2


def roll_frames(window, count, start=100.0, gap_after=None, gap_s=1.0):
    plan = window._roll_plan()
    config = plan.config
    chunk_s = config.n_samples / config.sample_rate
    frames, end = [], start
    for k in range(count):
        end += chunk_s + (gap_s if k == gap_after else 0)
        t = end - (config.n_samples - np.arange(config.n_samples)) / config.sample_rate
        volts = np.vstack([np.sin(2 * np.pi * 2 * t), np.zeros(t.size)])
        frames.append(Frame(config.sample_rate, volts, end_time=end))
    return frames


def test_roll_mode_scrolls_the_newest_samples_to_the_right(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._time_per_div.setCurrentIndex(window._time_per_div.findData(100e-3))
    assert window._is_roll()
    assert not window._plot.trigger_position.isVisible()
    assert not window._plot.trigger_level.isVisible()
    window._on_roll_frames(roll_frames(window, 30))  # 1,5 s, mais que a tela de 1 s.
    x = window._display_x(window._last_frame)
    assert x[-1] == pytest.approx(10, abs=1e-3)
    assert x[0] <= 0  # O trecho mais antigo já saiu pela esquerda.
    assert window._trigger_status.text() == "Modo roll (sem trigger)"
    # A média de blocos reduz 100 kS/s do simulador para 20 mil pontos por tela.
    visible = window._visible_samples(window._last_frame)
    assert visible.sum() == pytest.approx(20_000, rel=0.01)
    assert dict(window._plot.getAxis("bottom")._tickLevels[0])[10] == "0 s"
    assert dict(window._plot.getAxis("bottom")._tickLevels[0])[0] == "-1 s"


def test_roll_mode_marks_interrupted_reads_as_a_gap(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._time_per_div.setCurrentIndex(window._time_per_div.findData(200e-3))
    window._on_roll_frames(roll_frames(window, 10, gap_after=4, gap_s=0.5))
    trace = window._traces[0]
    assert np.isnan(trace).sum() == 1
    assert "MATH" not in window._measurement_readout.text()
    assert "CH1" in window._measurement_readout.text()


def test_roll_mode_ignores_reads_from_the_old_settings(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    frames = roll_frames(window, 3)  # Feitos com 200 µs/div, antes do roll.
    window._time_per_div.setCurrentIndex(window._time_per_div.findData(100e-3))
    window._on_roll_frames(frames)
    assert window._last_frame is None


def test_roll_mode_runs_with_the_simulator(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._time_per_div.setCurrentIndex(window._time_per_div.findData(100e-3))
    window.start()
    try:
        assert wait_until(lambda: window._last_frame is not None and window._roll_t.size > 3000)
        x = window._display_x(window._last_frame)
        assert x[-1] == pytest.approx(10, abs=1e-3)
        # Uma lacuna eventual vem da espera do teste, que segura a thread de aquisição.
        assert np.isfinite(window._traces[0]).mean() > 0.99
    finally:
        window.stop()
    # Sair do roll limpa o traço, que não tem disparo para alinhar.
    window._time_per_div.setCurrentIndex(window._time_per_div.findData(1e-3))
    assert window._last_frame is None


def test_save_data_in_roll_mode_uses_time_to_the_newest_sample(qapp, tmp_path):
    window = MainWindow([SimulatedScope(seed=1)])
    window._time_per_div.setCurrentIndex(window._time_per_div.findData(100e-3))
    window._on_roll_frames(roll_frames(window, 30))
    path = tmp_path / "roll.csv"
    window.save_data(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert any("Modo roll" in line for line in lines if line.startswith("#"))
    last = lines[-1].split(",")
    assert float(last[0]) == pytest.approx(0, abs=1e-4)


# FFT e modo XY


def sine_frame(window, frequency=5e3, amplitude=1.0, n=2001, rate=1e6):
    t = np.arange(n) / rate
    show_frame(window, amplitude * np.sin(2 * np.pi * frequency * t), 0.5 * np.cos(2 * np.pi * frequency * t))


def test_fft_shows_the_peak_of_the_visible_signal(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    sine_frame(window)
    window._fft_window.setCurrentIndex(window._fft_window.findData("flattop"))
    window._fft_on.setChecked(True)
    assert window._spectrum_plot.isVisibleTo(window)
    text = window._spectrum_plot.readout.text()
    match = re.search(r"pico ([\d.]+) kHz\s+(-?[\d.]+) dBV", text)
    assert float(match.group(1)) == pytest.approx(5.0, abs=0.01)
    # 1 V de pico = 0,707 V RMS = -3 dBV.
    assert match.group(2) == "-3.0"
    assert "resolução 499.8 Hz" in text  # 2 ms visíveis.


def test_fft_linear_scale_and_math_source(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    sine_frame(window)
    window._math_operation.setCurrentIndex(window._math_operation.findData("mul"))
    window._fft_on.setChecked(True)
    window._fft_source.setCurrentIndex(window._fft_source.findData(2))
    window._fft_scale.setCurrentIndex(1)
    text = window._spectrum_plot.readout.text()
    # sen × cos tem o dobro da frequência.
    assert "MATH" in text
    assert "pico 10 kHz" in text
    assert "V² RMS" in text


def test_fft_of_a_disabled_trace_is_empty(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    sine_frame(window)
    window._fft_on.setChecked(True)
    window._fft_source.setCurrentIndex(window._fft_source.findData(2))  # MATH desligado.
    assert window._spectrum_plot.readout.text() == ""


def test_xy_mode_plots_ch2_against_ch1(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._channels[1].enabled.setChecked(False)
    assert window._current_config().channels == 1
    window._xy_mode.setChecked(True)
    assert window._screens.currentWidget() is window._xy_plot
    assert window._current_config().channels == 2  # O XY precisa do CH2.
    sine_frame(window)
    x, y = window._xy_plot._curve.getData()
    assert x.max() == pytest.approx(1.0, abs=0.01)  # 1 V a 1 V/div.
    assert y.max() == pytest.approx(0.5, abs=0.01)
    labels = dict(window._xy_plot.getAxis("bottom")._tickLevels[0])
    assert labels[5] == "5 V"
    window._xy_mode.setChecked(False)
    assert window._screens.currentWidget() is window._plot


def test_saved_image_includes_the_fft(qapp, tmp_path):
    window = MainWindow([SimulatedScope(seed=1)])
    window.resize(1200, 900)
    window.show()
    try:
        sine_frame(window)
        window._fft_on.setChecked(True)
        qapp.processEvents()
        path = tmp_path / "fft.png"
        window.save_image(path)
        image = QImage(str(path))
        assert image.height() == window._display.height() + 26
        assert window._spectrum_plot.height() > 100
    finally:
        window.close()


# Média, autoset e medições de pulsos e atraso


def test_average_reduces_noise_and_resets_on_changes(qapp):
    noisy = (SimulatedSignal("sine", 1_000, 2.0, noise_rms=0.2), SimulatedSignal("square", 2_500, 1.0))
    window = MainWindow([SimulatedScope(noisy, seed=1)])
    window._average_n.setCurrentIndex(window._average_n.findData(16))
    window.start()
    try:
        assert wait_until(lambda: window._average_count >= 8, timeout_ms=8000)
    finally:
        window.stop()
    x = window._display_x(window._last_frame)
    visible = window._visible_samples(window._last_frame)
    t = window._time_at(x[visible])
    residual = window._traces[0][visible] - 2 * np.sin(2 * np.pi * 1e3 * t)
    # Ruído de 0,2 V RMS; com 8 ou mais capturas, cai a menos da metade.
    assert residual.std() < 0.1
    assert "média" in window._scale_label.text()
    window._channels[0].volts_per_div.setCurrentIndex(3)
    assert window._average is None


def test_autoset_finds_scales_timebase_and_trigger(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._time_per_div.setCurrentIndex(window._time_per_div.findData(10e-6))
    for channel in window._channels:
        channel.volts_per_div.setCurrentIndex(0)
        channel.position.setValue(3)
    window._trigger_level.setValue(4.0)
    window.start_autoset()
    try:
        assert wait_until(lambda: window._autoset_stage == 0, timeout_ms=8000)
    finally:
        window.stop()
    assert window._trigger_status.text() in ("Autoset concluído", "Parado")
    # CH1: seno de 4 Vpp -> 1 V/div; CH2: quadrada de 2 Vpp -> 0,5 V/div.
    assert window._vdiv(0) == 1.0
    assert window._vdiv(1) == 0.5
    assert window._channels[0].position.value() == pytest.approx(0, abs=0.1)
    # Dois períodos de 1 kHz na tela: 200 µs/div.
    assert window._tdiv() == pytest.approx(200e-6)
    assert window._trigger_source.currentData() == 0
    assert window._trigger_level.value() == pytest.approx(0, abs=0.1)


def test_autoset_keeps_a_dc_level_inside_the_input_range(qapp):
    signals = (SimulatedSignal("square", 1_000, 0.2, offset=4.0), SimulatedSignal("sine", 1_000, 0.0))
    window = MainWindow([SimulatedScope(signals, seed=1)])
    window.start_autoset()
    try:
        assert wait_until(lambda: window._autoset_stage == 0, timeout_ms=8000)
    finally:
        window.stop()
    # 0,4 Vpp caberia em 0,1 V/div, mas a posição só desce 4 divisões: para
    # trazer os 4 V de contínuo para a tela, a escala vai a 1 V/div.
    assert window._vdiv(0) == 1.0
    assert window._channels[0].position.value() == pytest.approx(-4.0)


def test_autoset_without_signal_says_so(qapp):
    quiet = (SimulatedSignal("sine", 1_000, 0.0), SimulatedSignal("sine", 1_000, 0.0))
    window = MainWindow([SimulatedScope(quiet, seed=1)])
    window.start_autoset()
    try:
        assert wait_until(lambda: window._autoset_stage == 0, timeout_ms=8000)
    finally:
        window.stop()
    assert window._trigger_status.text() in ("Autoset: nenhum sinal encontrado", "Parado")
    assert window._tdiv() == pytest.approx(1e-3)


def test_pulse_and_delay_rows(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    t = np.arange(4001) / 1e6
    phase = (t * 2e3) % 1.0
    ch1 = np.where(phase < 0.25, 2.0, 0.0)  # 2 kHz, 25%.
    ch2 = np.where(((t - 50e-6) * 2e3) % 1.0 < 0.25, 2.0, 0.0)  # 50 µs depois: 36°.
    window._time_per_div.setCurrentIndex(window._time_per_div.findData(500e-6))
    show_frame(window, ch1, ch2)
    window._measure_pulses.setChecked(True)
    window._measure_delay.setChecked(True)
    text = window._measurement_readout.text().replace("&nbsp;", " ")
    assert "ciclo 25.0%" in text
    assert "larg+ 125 µs" in text
    assert re.search(r"CH1→CH2\s+atraso 50 µs\s+fase 36.0°", text)


def test_average_leaves_out_a_capture_that_does_not_match(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window._average_n.setCurrentIndex(window._average_n.findData(8))
    square = np.where(np.arange(2000) % 500 < 250, 1.0, -1.0)
    zeros = np.zeros(2000)
    for _ in range(4):
        averaged, _ = window._accumulate_average([square, zeros], 100.0)
    # Captura com amostras perdidas: as bordas chegam 40 amostras antes.
    broken = np.roll(square, -40)
    averaged, _ = window._accumulate_average([broken, zeros], 100.0)
    assert np.array_equal(averaged[0], square)  # Sem degraus.
    assert window._average_count == 4
    # Três seguidas: o sinal mudou de verdade e a média recomeça com ele.
    for _ in range(2):
        averaged, _ = window._accumulate_average([broken, zeros], 100.0)
    assert np.array_equal(averaged[0], broken)
    assert window._average_count == 1


def test_side_panel_is_wide_enough_for_its_contents(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    scroll = window.centralWidget().layout().itemAt(1).widget()
    panel = scroll.widget()
    assert scroll.width() >= panel.minimumSizeHint().width() + scroll.verticalScrollBar().sizeHint().width()


def test_fft_axis_labels_read_in_hertz(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    axis = window._spectrum_plot.getAxis("bottom")
    axis.setLogMode(True)
    assert axis.tickStrings([2, 3, 6], 1, 1) == ["100 Hz", "1 kHz", "1 MHz"]
    axis.setLogMode(False)
    assert axis.tickStrings([0, 500e3], 1, 1) == ["0 Hz", "500 kHz"]


def test_mouse_drag_on_the_plot_pans(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window.resize(1500, 900)
    window.show()
    try:
        qapp.processEvents()
        viewport = window._plot.viewport()
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(300, 200))
        for k in range(1, 11):
            QTest.mouseMove(viewport, QPoint(300 + 20 * k, 200))
            qapp.processEvents()
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(500, 200))
        assert window._trigger_x > 6
    finally:
        window.close()


def test_horizontal_scroll_arrows_and_buttons_pan(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    event = QWheelEvent(
        QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(-240, 0),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
    )
    QApplication.sendEvent(window._plot.viewport(), event)
    assert window._trigger_x == pytest.approx(4.0)
    assert window._tdiv() == pytest.approx(200e-6)  # Não mexeu no zoom.
    QTest.keyClick(window._plot, Qt.Key.Key_Right)
    assert window._trigger_x == pytest.approx(3.0)
    QTest.keyClick(window._plot, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    assert window._trigger_x == pytest.approx(3.1)
    assert window._view_center_label.text() == "380 µs"
    window._on_dragged(-5.0)
    assert window._trigger_x == pytest.approx(-1.9)


def test_measurement_overlay_lets_the_mouse_through(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    assert window._measurement_readout.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def test_side_panel_fits_without_scrolling(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    scroll = window.centralWidget().layout().itemAt(1).widget()
    assert scroll.widget().minimumSizeHint().height() <= 900


def test_middle_button_drag_pans_even_over_a_cursor(qapp):
    window = MainWindow([SimulatedScope(seed=1)])
    window.resize(1500, 900)
    window.show()
    try:
        window._add_cursor(vertical=True)
        qapp.processEvents()
        viewport = window._plot.viewport()
        # Começa sobre a linha do disparo, no centro: o botão do meio não a move.
        x = window._plot.mapFromScene(
            window._plot.getViewBox().mapViewToScene(QPointF(5, 0))
        ).x()
        start = QPoint(int(x), 200)
        QTest.mousePress(viewport, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, start)
        for k in range(1, 11):
            QTest.mouseMove(viewport, start - QPoint(15 * k, 0))
            qapp.processEvents()
        QTest.mouseRelease(viewport, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, start - QPoint(150, 0))
        assert window._trigger_x < 4.5
        cursor = window._plot.time_cursors[0]
        assert cursor.value() == 3  # O cursor ficou onde estava.
    finally:
        window.close()
