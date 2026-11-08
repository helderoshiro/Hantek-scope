import math
import time
from collections import deque
from collections.abc import Sequence
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

import numpy as np
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QCloseEvent, QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
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
from hantek_scope.export import write_csv
from hantek_scope.processing.measurements import measure
from hantek_scope.processing.trigger import find_trigger
from hantek_scope.timebase import HORIZONTAL_DIVS, TIME_PER_DIV, VERTICAL_DIVS, capture_for
from hantek_scope.ui.scope_plot import BACKGROUND, CURSOR_COLOR, ScopePlot
from hantek_scope.ui.worker import AcquisitionWorker

CHANNEL_COLORS = ("#f2c94c", "#56ccf2", "#6fcf97", "#eb5757")
VOLTS_PER_DIV = (0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0)
DEFAULT_VOLTS_PER_DIV = 1.0
DEFAULT_TIME_PER_DIV = 200e-6
TRIGGER_MODES = (("Auto", "auto"), ("Normal", "normal"), ("Único", "single"))
TRIGGER_HYSTERESIS_DIVS = 0.1
MIN_VPP_FOR_FREQUENCY_DIVS = 0.2
MAX_CURSORS = 6
# Posições iniciais, em divisões, de cada cursor novo.
TIME_CURSOR_SLOTS = (3, 7, 5, 2, 8, 4)
VOLT_CURSOR_SLOTS = (1, -1, 2, -2, 3, -3)
CSV_FILTER = "CSV (*.csv)"
CSV_PTBR_FILTER = "CSV para Excel em português (*.csv)"
IMAGE_FOOTER_HEIGHT = 26


def format_si(value: float, unit: str) -> str:
    if value == 0:
        return f"0 {unit}"
    for scale, prefix in ((1e6, "M"), (1e3, "k"), (1, ""), (1e-3, "m"), (1e-6, "µ")):
        # Compara já arredondado, para 999,97 virar "1 k" e não "1000".
        if abs(float(f"{value / scale:.4g}")) >= 1:
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
            "Roda do mouse: tempo/div\nCtrl + roda: volts/div do CH1\nShift + roda: volts/div do CH2\n"
            "Clique direito em um cursor: remover"
        )
        self._cursor_readout = self._plot.cursor_readout
        self._measurement_readout = self._plot.measurement_readout
        self._curves = [
            self._plot.plot(pen=pg.mkPen(CHANNEL_COLORS[i], width=1), skipFiniteCheck=True)
            for i in range(self._backend.n_channels)
        ]
        for curve in self._curves:
            # Mantém a fluidez nas capturas longas sem perder os picos do sinal.
            curve.setDownsampling(auto=True, method="peak")
            curve.setClipToView(True)

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
        self._plot.cursor_remove_requested.connect(self._remove_cursor)

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

        self._pause_button = QPushButton("Pausar tela")
        self._pause_button.setCheckable(True)
        self._pause_button.setEnabled(False)
        self._pause_button.setToolTip("Congela a tela; a aquisição continua")
        self._pause_button.toggled.connect(self._on_pause_toggled)

        save_image = QPushButton("Salvar imagem…")
        save_image.clicked.connect(self._ask_save_image)
        save_data = QPushButton("Salvar dados…")
        save_data.clicked.connect(self._ask_save_data)
        save = QGroupBox("Salvar a tela")
        save_layout = QHBoxLayout(save)
        save_layout.addWidget(save_image)
        save_layout.addWidget(save_data)

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
        layout.addWidget(self._pause_button)
        layout.addWidget(save)
        layout.addWidget(timebase)
        for channel in self._channels:
            layout.addWidget(channel)
        layout.addWidget(self._build_trigger_group())
        layout.addWidget(self._build_cursor_group())
        layout.addWidget(self._build_measurement_group())
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setFixedWidth(250)
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
        self._add_time_cursor = QPushButton("+ Tempo")
        self._add_time_cursor.clicked.connect(lambda: self._add_cursor(vertical=True))
        self._add_volt_cursor = QPushButton("+ Tensão")
        self._add_volt_cursor.clicked.connect(lambda: self._add_cursor(vertical=False))
        clear = QPushButton("Remover todos")
        clear.clicked.connect(self._clear_cursors)

        self._cursor_channel = QComboBox()
        for i in range(self._backend.n_channels):
            self._cursor_channel.addItem(f"CH{i + 1}", i)
        self._cursor_channel.currentIndexChanged.connect(self._on_cursor_channel_changed)

        hint = QLabel("Arraste para mover; clique direito para remover.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9aa0a6;")

        buttons = QHBoxLayout()
        buttons.addWidget(self._add_time_cursor)
        buttons.addWidget(self._add_volt_cursor)

        group = QGroupBox("Cursores")
        form = QFormLayout(group)
        form.addRow(buttons)
        form.addRow(clear)
        form.addRow("Tensão em", self._cursor_channel)
        form.addRow(hint)
        return group

    def _build_measurement_group(self) -> QGroupBox:
        self._measurements_on = QCheckBox("Mostrar no gráfico")
        self._measurements_on.setChecked(True)
        self._measurements_on.toggled.connect(self._update_measurements)
        group = QGroupBox("Medições")
        QVBoxLayout(group).addWidget(self._measurements_on)
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
        self._pause_button.setChecked(False)
        self._pause_button.setEnabled(running)
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

    def _on_pause_toggled(self, paused: bool) -> None:
        self._pause_button.setText("Continuar" if paused else "Pausar tela")
        if paused:
            self._trigger_status.setText("Tela pausada")

    def _on_source_changed(self) -> None:
        was_running = self._run_button.isChecked()
        self.stop()
        self._backend = self._source.currentData()
        # O traço antigo não pode continuar na tela identificado como da nova fonte.
        self._last_frame = None
        self._trigger_at = None
        for curve in self._curves:
            curve.setData([], [])
        self._update_measurements()
        self._update_source_labels()
        self._update_scale_label()
        if was_running:
            self.start()

    def _on_frame_ready(self) -> None:
        if self._worker is None:
            return
        frame = self._worker.take_frame()
        if frame is None or self._pause_button.isChecked():
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

    # Escalas e trigger

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

    # Cursores

    def _add_cursor(self, vertical: bool) -> None:
        cursors = self._plot.time_cursors if vertical else self._plot.volt_cursors
        if len(cursors) >= MAX_CURSORS:
            return
        slots = TIME_CURSOR_SLOTS if vertical else VOLT_CURSOR_SLOTS
        prefix = "T" if vertical else "V"
        line = self._plot.add_cursor(vertical, slots[len(cursors)], f"{prefix}{len(cursors) + 1}")
        if not vertical:
            self._plot.set_line_color(line, CHANNEL_COLORS[self._cursor_channel.currentData()])
        line.sigPositionChanged.connect(self._update_cursor_readout)
        self._after_cursors_changed()

    def _remove_cursor(self, line: pg.InfiniteLine) -> None:
        self._plot.remove_cursor(line)
        self._after_cursors_changed()

    def _clear_cursors(self) -> None:
        for line in self._plot.time_cursors + self._plot.volt_cursors:
            self._plot.remove_cursor(line)
        self._after_cursors_changed()

    def _after_cursors_changed(self) -> None:
        # Renumera para não deixar lacunas depois de uma remoção.
        for prefix, cursors in (("T", self._plot.time_cursors), ("V", self._plot.volt_cursors)):
            for i, line in enumerate(cursors):
                line.label.setFormat(f"{prefix}{i + 1}")
        self._add_time_cursor.setEnabled(len(self._plot.time_cursors) < MAX_CURSORS)
        self._add_volt_cursor.setEnabled(len(self._plot.volt_cursors) < MAX_CURSORS)
        self._update_cursor_readout()

    def _on_cursor_channel_changed(self) -> None:
        color = CHANNEL_COLORS[self._cursor_channel.currentData()]
        for line in self._plot.volt_cursors:
            self._plot.set_line_color(line, color)
        self._update_cursor_readout()

    def _update_cursor_readout(self) -> None:
        """Cada cursor mostra seu valor e a diferença em relação ao primeiro."""
        lines = []
        times = [self._time_at(line.value()) for line in self._plot.time_cursors]
        for i, t in enumerate(times):
            text = f"T{i + 1} {format_si(t, 's'):>9}"
            if i > 0:
                delta = abs(t - times[0])
                frequency = format_si(1 / delta, "Hz") if delta else "—"
                text += f"   ΔT1 {format_si(delta, 's'):>9}   1/ΔT1 {frequency}"
            lines.append(text)

        if self._plot.volt_cursors:
            channel = self._cursor_channel.currentData()
            vdiv = self._vdiv(channel)
            volts = [line.value() * vdiv for line in self._plot.volt_cursors]
            for i, v in enumerate(volts):
                text = f"V{i + 1} {format_si(v, 'V'):>9}"
                if i > 0:
                    text += f"   ΔV1 {format_si(v - volts[0], 'V'):>9}"
                lines.append(text)
            lines.append(f"(tensão do CH{channel + 1})")

        self._plot.set_overlay_text(self._cursor_readout, "\n".join(lines))

    # Medições

    def _update_measurements(self) -> None:
        frame = self._last_frame
        if frame is None or not self._measurements_on.isChecked():
            self._plot.set_overlay_text(self._measurement_readout, "")
            return
        visible = self._visible_samples(frame)
        rows = []
        for i, volts in enumerate(frame.channels):
            if not self._enabled(i) or not visible.any():
                continue
            m = measure(volts[visible], frame.sample_rate, MIN_VPP_FOR_FREQUENCY_DIVS * self._vdiv(i))
            frequency = format_si(m.frequency, "Hz") if m.frequency else "—"
            period = format_si(m.period, "s") if m.period else "—"
            rows.append(
                f'<span style="color:{CHANNEL_COLORS[i]}">CH{i + 1}</span>'
                f"&nbsp; f {frequency} &nbsp; T {period} &nbsp; Vpp {format_si(m.vpp, 'V')}"
                f" &nbsp; Vmín {format_si(m.vmin, 'V')} &nbsp; Vmáx {format_si(m.vmax, 'V')}"
                f" &nbsp; Vméd {format_si(m.vmean, 'V')} &nbsp; RMS {format_si(m.vrms, 'V')}"
            )
        self._plot.set_overlay_text(self._measurement_readout, "<br>".join(rows))

    # Salvar

    def _ask_save_image(self) -> None:
        if self._nothing_to_save():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Salvar imagem da tela", self._default_file_name("png"), "Imagem PNG (*.png)"
        )
        if path:
            self.save_image(Path(path))

    def _ask_save_data(self) -> None:
        if self._nothing_to_save():
            return
        path, chosen = QFileDialog.getSaveFileName(
            self,
            "Salvar dados da tela",
            self._default_file_name("csv"),
            f"{CSV_FILTER};;{CSV_PTBR_FILTER}",
        )
        if path:
            self.save_data(Path(path), excel_ptbr=chosen == CSV_PTBR_FILTER)

    def _nothing_to_save(self) -> bool:
        if self._last_frame is None:
            QMessageBox.information(self, "Nada para salvar", "Ainda não há uma forma de onda na tela.")
            return True
        return False

    def _default_file_name(self, extension: str) -> str:
        return f"hantek_{datetime.now():%Y-%m-%d_%H%M%S}.{extension}"

    def save_image(self, path: Path) -> None:
        """Salva o gráfico com uma faixa de rodapé com as escalas e a data."""
        plot = self._plot.grab()
        image = QPixmap(plot.width(), plot.height() + IMAGE_FOOTER_HEIGHT)
        image.fill(QColor(BACKGROUND))
        painter = QPainter(image)
        painter.drawPixmap(0, 0, plot)
        painter.setPen(QColor(CURSOR_COLOR))
        footer = QRect(8, plot.height(), plot.width() - 16, IMAGE_FOOTER_HEIGHT)
        text = f"{self._backend.name}   |   {self._scale_label.text()}   |   {datetime.now():%d/%m/%Y %H:%M:%S}"
        painter.drawText(footer, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        painter.end()
        image.save(str(path), "PNG")

    def save_data(self, path: Path, excel_ptbr: bool = False) -> None:
        """Salva as amostras visíveis na tela, com o tempo a partir do disparo."""
        frame = self._last_frame
        visible = self._visible_samples(frame)
        x = self._display_x(frame)[visible]
        channels = {
            f"CH{i + 1}": volts[visible] for i, volts in enumerate(frame.channels) if self._enabled(i)
        }
        triggered = "sim" if self._trigger_at is not None else "não"
        header = [
            f"Hantek Scope {version('hantek-scope')}",
            f"Data: {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"Fonte: {self._backend.name}",
            f"Taxa de amostragem: {frame.sample_rate:g} S/s",
            f"Tempo/div: {format_si(self._tdiv(), 's')}",
            *(f"CH{i + 1}: {format_si(self._vdiv(i), 'V')}/div" for i in range(len(self._channels))),
            f"Trigger: {self._trigger_mode.currentText()}, {self._trigger_source.currentText()},"
            f" {self._trigger_edge.currentText().lower()}, {self._trigger_level.value():g} V,"
            f" disparado: {triggered}",
            "Tempo medido a partir do disparo.",
        ]
        write_csv(path, self._time_at(x), channels, header, excel_ptbr=excel_ptbr)

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

    def _display_x(self, frame: Frame) -> np.ndarray:
        """Posição horizontal de cada amostra, em divisões da tela."""
        samples_per_div = frame.sample_rate * self._tdiv()
        index = np.arange(frame.channels.shape[1])
        if self._trigger_at is None:
            return index / samples_per_div
        return (index - self._trigger_at) / samples_per_div + self._plot.trigger_position.value()

    def _visible_samples(self, frame: Frame) -> np.ndarray:
        x = self._display_x(frame)
        return (x >= 0) & (x <= HORIZONTAL_DIVS)

    def _redraw(self) -> None:
        frame = self._last_frame
        if frame is None:
            return
        x = self._display_x(frame)
        for i, (curve, volts) in enumerate(zip(self._curves, frame.channels)):
            curve.setVisible(self._enabled(i))
            curve.setData(x, volts / self._vdiv(i))
        self._update_measurements()

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
