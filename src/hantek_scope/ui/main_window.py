import math
import time
from collections import deque
from collections.abc import Sequence

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import pyqtgraph as pg

from hantek_scope.acquisition.base import CaptureConfig, Frame, ScopeBackend
from hantek_scope.processing.trigger import find_trigger
from hantek_scope.timebase import HORIZONTAL_DIVS, TIME_PER_DIV, VERTICAL_DIVS, capture_for
from hantek_scope.ui.scope_plot import CURSOR_COLOR, ScopePlot
from hantek_scope.ui.worker import AcquisitionWorker

CHANNEL_COLORS = ("#f2c94c", "#56ccf2", "#6fcf97", "#eb5757")
VOLTS_PER_DIV = (0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0)
DEFAULT_VOLTS_PER_DIV = 1.0
DEFAULT_TIME_PER_DIV = 200e-6
TRIGGER_MODES = (("Auto", "auto"), ("Normal", "normal"), ("Único", "single"))
TRIGGER_HYSTERESIS_DIVS = 0.1


def format_si(value: float, unit: str) -> str:
    if value == 0:
        return f"0 {unit}"
    for scale, prefix in ((1e6, "M"), (1e3, "k"), (1, ""), (1e-3, "m"), (1e-6, "µ")):
        if abs(value) >= scale:
            return f"{value / scale:.4g} {prefix}{unit}"
    return f"{value / 1e-9:.4g} n{unit}"


class ChannelControls(QGroupBox):
    def __init__(self, index: int, color: str, on_change) -> None:
        super().__init__(f"Canal {index + 1}")
        self.setStyleSheet(f"QGroupBox {{ color: {color}; font-weight: bold; }}")

        self.enabled = QCheckBox("Ativo")
        self.enabled.setChecked(True)
        self.enabled.toggled.connect(lambda _: on_change())

        self.volts_per_div = QComboBox()
        for volts in VOLTS_PER_DIV:
            self.volts_per_div.addItem(format_si(volts, "V"), volts)
        self.volts_per_div.setCurrentIndex(VOLTS_PER_DIV.index(DEFAULT_VOLTS_PER_DIV))
        self.volts_per_div.currentIndexChanged.connect(lambda _: on_change())

        form = QFormLayout(self)
        form.addRow(self.enabled)
        form.addRow("Volts/div", self.volts_per_div)


class MainWindow(QMainWindow):
    def __init__(
        self,
        backends: Sequence[ScopeBackend],
        initial: ScopeBackend | None = None,
    ) -> None:
        """Todos os backends devem ter o mesmo número de canais."""
        super().__init__()
        self._backends = backends
        self._backend = initial if initial is not None else backends[0]
        self._worker: AcquisitionWorker | None = None
        self._last_frame: Frame | None = None
        self._trigger_at: float | None = None  # Amostra do disparo em _last_frame.
        self._frame_times: deque[float] = deque(maxlen=30)
        self._syncing_trigger_line = False

        self.resize(1200, 760)

        self._plot = ScopePlot(CHANNEL_COLORS)
        self._plot.setToolTip(
            "Roda do mouse: tempo/div\nCtrl + roda: volts/div do CH1\nShift + roda: volts/div do CH2"
        )
        self._curves = [
            self._plot.plot(pen=pg.mkPen(CHANNEL_COLORS[i], width=1), skipFiniteCheck=True)
            for i in range(self._backend.n_channels)
        ]
        for curve in self._curves:
            # Mantém a fluidez nas capturas longas sem perder os picos do sinal.
            curve.setDownsampling(auto=True, method="peak")
            curve.setClipToView(True)

        # A leitura dos cursores fica sobre o canto do gráfico, como em um osciloscópio.
        self._cursor_readout = QLabel(self._plot)
        self._cursor_readout.setStyleSheet(
            f"background: rgba(20, 23, 28, 210); color: {CURSOR_COLOR}; border: 1px solid #3a3f45;"
            " padding: 4px; font-family: Consolas, monospace;"
        )
        self._cursor_readout.move(60, 8)
        self._cursor_readout.hide()

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(self._plot, stretch=1)
        layout.addWidget(self._build_side_panel())
        self.setCentralWidget(central)

        self._source_label = QLabel()
        self._scale_label = QLabel()
        self._mouse_label = QLabel()
        self.statusBar().addWidget(self._source_label)
        self.statusBar().addWidget(self._scale_label)
        self.statusBar().addPermanentWidget(self._mouse_label)

        self._plot.wheel_scrolled.connect(self._on_wheel)
        self._plot.mouse_moved.connect(self._on_mouse_moved)
        self._plot.mouse_left.connect(lambda: self._mouse_label.setText(""))
        self._plot.trigger_position.sigPositionChanged.connect(self._on_trigger_position_changed)
        self._plot.trigger_level.sigPositionChanged.connect(self._on_trigger_line_moved)
        for line in self._plot.time_cursors + self._plot.volt_cursors:
            line.sigPositionChanged.connect(self._update_cursor_readout)

        self._update_source_labels()
        self._on_settings_changed()

    # Construção da interface

    def _build_side_panel(self) -> QWidget:
        self._source = QComboBox()
        for backend in self._backends:
            self._source.addItem(backend.name, backend)
        self._source.setCurrentIndex(self._backends.index(self._backend))
        self._source.currentIndexChanged.connect(self._on_source_changed)

        source = QGroupBox("Fonte")
        QVBoxLayout(source).addWidget(self._source)

        self._run_button = QPushButton("Executar")
        self._run_button.setCheckable(True)
        self._run_button.setMinimumHeight(40)
        self._run_button.toggled.connect(self._on_run_toggled)

        self._time_per_div = QComboBox()
        for seconds in TIME_PER_DIV:
            self._time_per_div.addItem(format_si(seconds, "s"), seconds)
        self._time_per_div.setCurrentIndex(TIME_PER_DIV.index(DEFAULT_TIME_PER_DIV))
        self._time_per_div.currentIndexChanged.connect(self._on_settings_changed)

        timebase = QGroupBox("Horizontal")
        QFormLayout(timebase).addRow("Tempo/div", self._time_per_div)

        self._channels = [
            ChannelControls(i, CHANNEL_COLORS[i], self._on_settings_changed)
            for i in range(self._backend.n_channels)
        ]

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(source)
        layout.addWidget(self._run_button)
        layout.addWidget(timebase)
        for channel in self._channels:
            layout.addWidget(channel)
        layout.addWidget(self._build_trigger_group())
        layout.addWidget(self._build_cursor_group())
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setFixedWidth(240)
        return scroll

    def _build_trigger_group(self) -> QGroupBox:
        self._trigger_mode = QComboBox()
        for label, mode in TRIGGER_MODES:
            self._trigger_mode.addItem(label, mode)

        self._trigger_source = QComboBox()
        for i in range(self._backend.n_channels):
            self._trigger_source.addItem(f"CH{i + 1}", i)
        self._trigger_source.currentIndexChanged.connect(self._on_trigger_source_changed)

        self._trigger_edge = QComboBox()
        self._trigger_edge.addItem("Subida", True)
        self._trigger_edge.addItem("Descida", False)

        self._trigger_level = QDoubleSpinBox()
        self._trigger_level.setRange(-50.0, 50.0)
        self._trigger_level.setDecimals(3)
        self._trigger_level.setSuffix(" V")
        self._trigger_level.valueChanged.connect(self._sync_trigger_line)

        self._trigger_status = QLabel("Parado")

        group = QGroupBox("Trigger")
        form = QFormLayout(group)
        form.addRow("Modo", self._trigger_mode)
        form.addRow("Fonte", self._trigger_source)
        form.addRow("Borda", self._trigger_edge)
        form.addRow("Nível", self._trigger_level)
        form.addRow("Estado", self._trigger_status)
        return group

    def _build_cursor_group(self) -> QGroupBox:
        self._time_cursors_on = QCheckBox("Tempo (T1 e T2)")
        self._volt_cursors_on = QCheckBox("Tensão (V1 e V2)")
        self._cursor_channel = QComboBox()
        for i in range(self._backend.n_channels):
            self._cursor_channel.addItem(f"CH{i + 1}", i)
        for widget in (self._time_cursors_on, self._volt_cursors_on):
            widget.toggled.connect(self._on_cursors_changed)
        self._cursor_channel.currentIndexChanged.connect(self._on_cursors_changed)

        group = QGroupBox("Cursores")
        form = QFormLayout(group)
        form.addRow(self._time_cursors_on)
        form.addRow(self._volt_cursors_on)
        form.addRow("Tensão em", self._cursor_channel)
        return group

    # Execução

    def start(self) -> None:
        self._run_button.setChecked(True)

    def stop(self) -> None:
        self._run_button.setChecked(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.stop()
        super().closeEvent(event)

    def _current_config(self) -> CaptureConfig:
        return capture_for(
            self._tdiv(),
            self._backend.sample_rates,
            tuple(self._vdiv(i) for i in range(len(self._channels))),
        )

    def _on_run_toggled(self, running: bool) -> None:
        self._run_button.setText("Parar" if running else "Executar")
        self._trigger_status.setText("Aguardando disparo" if running else "Parado")
        if running:
            self._frame_times.clear()
            self._worker = AcquisitionWorker(self._backend, self._current_config(), self)
            self._worker.frame_ready.connect(self._on_frame_ready, Qt.QueuedConnection)
            self._worker.failed.connect(self._on_failed)
            self._worker.start()
        elif self._worker is not None:
            self._worker.requestInterruption()
            self._worker.wait()
            self._worker = None
        self._update_scale_label()

    def _on_source_changed(self) -> None:
        was_running = self._run_button.isChecked()
        self.stop()
        self._backend = self._source.currentData()
        # O traço antigo não pode continuar na tela identificado como da nova fonte.
        self._last_frame = None
        self._trigger_at = None
        for curve in self._curves:
            curve.setData([], [])
        self._update_source_labels()
        self._update_scale_label()
        if was_running:
            self.start()

    def _on_frame_ready(self) -> None:
        if self._worker is None:
            return
        frame = self._worker.take_frame()
        if frame is None:
            return
        trigger_at = self._find_trigger(frame)
        mode = self._trigger_mode.currentData()
        if trigger_at is None and mode != "auto":
            self._trigger_status.setText("Aguardando disparo")
            return

        self._last_frame = frame
        self._trigger_at = trigger_at
        self._frame_times.append(time.monotonic())
        self._redraw()
        self._update_scale_label()
        if trigger_at is None:
            self._trigger_status.setText("Sem disparo (auto)")
        elif mode == "single":
            self.stop()
            self._trigger_status.setText("Capturado (único)")
        else:
            self._trigger_status.setText("Disparado")

    def _on_failed(self, message: str) -> None:
        self.stop()
        QMessageBox.critical(self, "Falha na aquisição", message)

    def _find_trigger(self, frame: Frame) -> float | None:
        source = self._trigger_source.currentData()
        samples_per_div = frame.sample_rate * self._tdiv()
        position = self._plot.trigger_position.value()
        n_samples = frame.channels.shape[1]
        # O disparo precisa de dados suficientes antes e depois para encher a tela.
        start = math.ceil(position * samples_per_div)
        stop = n_samples - math.ceil((HORIZONTAL_DIVS - position) * samples_per_div)
        return find_trigger(
            frame.channels[source],
            level=self._trigger_level.value(),
            rising=self._trigger_edge.currentData(),
            hysteresis=TRIGGER_HYSTERESIS_DIVS * self._vdiv(source),
            start=start,
            stop=stop,
        )

    # Escalas, trigger e cursores

    def _tdiv(self) -> float:
        return self._time_per_div.currentData()

    def _vdiv(self, channel: int) -> float:
        return self._channels[channel].volts_per_div.currentData()

    def _enabled(self, channel: int) -> bool:
        return self._channels[channel].enabled.isChecked()

    def _time_at(self, x_divs: float) -> float:
        """Tempo relativo ao ponto de disparo."""
        return (x_divs - self._plot.trigger_position.value()) * self._tdiv()

    def _on_settings_changed(self) -> None:
        if self._worker is not None:
            self._worker.set_config(self._current_config())
        self._sync_trigger_line()
        self._update_axes()
        self._update_cursor_readout()
        self._redraw()
        self._update_scale_label()

    def _update_axes(self) -> None:
        bottom = [format_si(self._time_at(i), "s") for i in range(HORIZONTAL_DIVS + 1)]
        half = VERTICAL_DIVS // 2
        vertical = []
        for channel in range(2):
            if channel < len(self._channels) and self._enabled(channel):
                vdiv = self._vdiv(channel)
                vertical.append([format_si(i * vdiv, "V") for i in range(-half, half + 1)])
            else:
                vertical.append(None)
        self._plot.set_axis_labels(bottom, *vertical)

    def _on_trigger_position_changed(self) -> None:
        self._update_axes()
        self._update_cursor_readout()
        self._redraw()

    def _on_trigger_source_changed(self) -> None:
        source = self._trigger_source.currentData()
        self._plot.set_line_color(self._plot.trigger_level, CHANNEL_COLORS[source])
        self._sync_trigger_line()

    def _sync_trigger_line(self) -> None:
        """Posiciona a linha do nível conforme o valor em volts e a escala da fonte."""
        vdiv = self._vdiv(self._trigger_source.currentData())
        self._trigger_level.setSingleStep(TRIGGER_HYSTERESIS_DIVS * vdiv)
        self._syncing_trigger_line = True
        self._plot.trigger_level.setValue(self._trigger_level.value() / vdiv)
        self._syncing_trigger_line = False

    def _on_trigger_line_moved(self) -> None:
        if self._syncing_trigger_line:
            return
        volts = self._plot.trigger_level.value() * self._vdiv(self._trigger_source.currentData())
        self._trigger_level.blockSignals(True)
        self._trigger_level.setValue(volts)
        self._trigger_level.blockSignals(False)

    def _on_cursors_changed(self) -> None:
        for line in self._plot.time_cursors:
            line.setVisible(self._time_cursors_on.isChecked())
        channel = self._cursor_channel.currentData()
        for line in self._plot.volt_cursors:
            line.setVisible(self._volt_cursors_on.isChecked())
            self._plot.set_line_color(line, CHANNEL_COLORS[channel])
        self._update_cursor_readout()

    def _update_cursor_readout(self) -> None:
        lines = []
        if self._time_cursors_on.isChecked():
            x1, x2 = (line.value() for line in self._plot.time_cursors)
            delta = abs(x2 - x1) * self._tdiv()
            lines += [
                f"T1   {format_si(self._time_at(x1), 's')}",
                f"T2   {format_si(self._time_at(x2), 's')}",
                f"ΔT   {format_si(delta, 's')}",
                f"1/ΔT {format_si(1 / delta, 'Hz') if delta else '—'}",
            ]
        if self._volt_cursors_on.isChecked():
            vdiv = self._vdiv(self._cursor_channel.currentData())
            y1, y2 = (line.value() for line in self._plot.volt_cursors)
            lines += [
                f"V1   {format_si(y1 * vdiv, 'V')}",
                f"V2   {format_si(y2 * vdiv, 'V')}",
                f"ΔV   {format_si(abs(y2 - y1) * vdiv, 'V')}",
            ]
        self._cursor_readout.setText("\n".join(lines))
        self._cursor_readout.adjustSize()
        self._cursor_readout.setVisible(bool(lines))

    # Mouse

    def _on_wheel(self, steps: int, modifiers) -> None:
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            combo = self._channels[0].volts_per_div
        elif modifiers & Qt.KeyboardModifier.ShiftModifier and len(self._channels) > 1:
            combo = self._channels[1].volts_per_div
        else:
            combo = self._time_per_div
        # Rolar para cima aproxima, ou seja, escolhe uma escala menor.
        combo.setCurrentIndex(max(0, min(combo.count() - 1, combo.currentIndex() - steps)))

    def _on_mouse_moved(self, x_divs: float, y_divs: float) -> None:
        self._mouse_label.setText(self._position_text(x_divs, y_divs))

    def _position_text(self, x_divs: float, y_divs: float) -> str:
        parts = [f"t = {format_si(self._time_at(x_divs), 's')}"]
        for i in range(len(self._channels)):
            if self._enabled(i):
                parts.append(f"CH{i + 1} = {format_si(y_divs * self._vdiv(i), 'V')}")
        return "   ".join(parts)

    # Desenho

    def _redraw(self) -> None:
        frame = self._last_frame
        if frame is None:
            return
        samples_per_div = frame.sample_rate * self._tdiv()
        index = np.arange(frame.channels.shape[1])
        if self._trigger_at is None:
            x = index / samples_per_div
        else:
            x = (index - self._trigger_at) / samples_per_div + self._plot.trigger_position.value()
        for i, (curve, volts) in enumerate(zip(self._curves, frame.channels)):
            curve.setVisible(self._enabled(i))
            curve.setData(x, volts / self._vdiv(i))

    def _update_source_labels(self) -> None:
        self.setWindowTitle(f"Hantek Scope ({self._backend.name})")
        self._source_label.setText(f"Fonte: {self._backend.name}")

    def _update_scale_label(self) -> None:
        config = self._current_config()
        parts = [
            f"{format_si(self._tdiv(), 's')}/div",
            f"{format_si(config.sample_rate, 'S/s')}",
        ]
        for i in range(len(self._channels)):
            if self._enabled(i):
                parts.append(f"CH{i + 1} {format_si(self._vdiv(i), 'V')}/div")
        if self._worker is not None and len(self._frame_times) > 1:
            elapsed = self._frame_times[-1] - self._frame_times[0]
            if elapsed > 0:
                parts.append(f"{(len(self._frame_times) - 1) / elapsed:.0f} quadros/s")
        self._scale_label.setText("   |   ".join(parts))
