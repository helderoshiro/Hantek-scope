import math
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

import numpy as np
from PySide6.QtCore import QRect, Qt, Signal
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
from hantek_scope.processing.channel import (
    MAINS_HZ,
    MIN_MAINS_CYCLES,
    ChannelSettings,
    ac_input_scale,
    can_remove_mains,
    condition,
)
from hantek_scope.processing.math_channel import OPERATIONS, combine
from hantek_scope.processing.measurements import measure
from hantek_scope.processing.trigger import find_trigger
from hantek_scope.timebase import (
    CAPTURE_DIVS,
    HORIZONTAL_DIVS,
    TIME_PER_DIV,
    VERTICAL_DIVS,
    capture_for,
    max_offscreen_divs,
)
from hantek_scope.ui.scope_plot import BACKGROUND, CURSOR_COLOR, ScopePlot
from hantek_scope.ui.worker import AcquisitionWorker

CHANNEL_COLORS = ("#f2c94c", "#56ccf2", "#6fcf97", "#eb5757")
MATH_COLOR = "#6fcf97"
# Volts/div na entrada do aparelho; com a ponteira 10x, a tela mostra 10 vezes mais.
VOLTS_PER_DIV = (0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0)
PROBES = (1.0, 10.0)
MATH_SCALES = tuple(round(m * 10.0**e, 10) for e in range(-3, 5) for m in (1, 2, 5))
MATH_FIT_DIVS = 6  # Altura que o "Ajustar escala" dá ao traço MATH.
# Na divisão, um CH2 mais perto de zero que isto (em divisões) vira lacuna.
MIN_DIVISOR_DIVS = 0.05
# Menor tempo/div em que a captura tem os ciclos da rede que o filtro pede.
MAINS_MIN_TIME_PER_DIV = min(
    t for t in TIME_PER_DIV if CAPTURE_DIVS * t >= MIN_MAINS_CYCLES / MAINS_HZ
)
MATH_CSV_COLUMNS = {"V": "MATH_V", "V²": "MATH_V2", "V/V": "MATH_VporV"}
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
    changed = Signal()
    probe_changed = Signal(float)  # Razão entre a ponteira nova e a anterior.

    def __init__(self, index: int, color: str) -> None:
        super().__init__(f"Canal {index + 1}")
        self.setStyleSheet(f"QGroupBox {{ color: {color}; font-weight: bold; }}")

        self.enabled = QCheckBox("Ativo")
        self.enabled.setChecked(True)
        self.inverted = QCheckBox("Inverter")

        self.volts_per_div = QComboBox()
        for volts in VOLTS_PER_DIV:
            self.volts_per_div.addItem("", volts)
        self.volts_per_div.setCurrentIndex(VOLTS_PER_DIV.index(DEFAULT_VOLTS_PER_DIV))

        self.probe = QComboBox()
        for factor in PROBES:
            self.probe.addItem(f"{factor:g}x", factor)
        self.probe.setToolTip("Atenuação da ponteira; as tensões são corrigidas por ela")
        self._probe_factor = PROBES[0]

        self.coupling = QComboBox()
        self.coupling.addItem("DC", False)
        self.coupling.addItem("AC", True)
        self.coupling.setToolTip(
            "AC remove o nível contínuo, no software. A faixa de entrada passa a ser\n"
            "escolhida pelo sinal completo, para o contínuo não saturar o conversor."
        )

        self.position = QDoubleSpinBox()
        self.position.setRange(-VERTICAL_DIVS / 2, VERTICAL_DIVS / 2)
        self.position.setDecimals(1)
        self.position.setSingleStep(0.1)
        self.position.setSuffix(" div")
        self.position.setToolTip("Desloca o traço na tela; o 0 V fica na marca à esquerda")

        self.mains_filter = QCheckBox(f"Filtro {MAINS_HZ:g} Hz")
        self.mains_filter.setToolTip(
            f"Remove a interferência da rede ({MAINS_HZ:g} Hz e harmônicas até {3 * MAINS_HZ:g} Hz).\n"
            "Um sinal legítimo nessas frequências também é removido."
        )
        self.mains_note = QLabel(
            f"Inativo: o filtro precisa de {format_si(MAINS_MIN_TIME_PER_DIV, 's')}/div ou mais."
        )
        self.mains_note.setWordWrap(True)
        self.mains_note.setStyleSheet("color: #e0a040; font-weight: normal;")
        self.mains_note.hide()

        for checkbox in (self.enabled, self.inverted, self.mains_filter):
            checkbox.toggled.connect(lambda _: self.changed.emit())
        for combo in (self.volts_per_div, self.coupling):
            combo.currentIndexChanged.connect(lambda _: self.changed.emit())
        self.position.valueChanged.connect(lambda _: self.changed.emit())
        self.probe.currentIndexChanged.connect(self._on_probe_changed)
        self._relabel_volts_per_div()

        checks = QHBoxLayout()
        checks.addWidget(self.enabled)
        checks.addWidget(self.inverted)
        form = QFormLayout(self)
        form.addRow(checks)
        form.addRow("Volts/div", self.volts_per_div)
        form.addRow("Ponteira", self.probe)
        form.addRow("Acoplamento", self.coupling)
        form.addRow("Posição", self.position)
        form.addRow(self.mains_filter)
        form.addRow(self.mains_note)

    def vdiv(self) -> float:
        """Volts/div na ponta da ponteira, como aparece na tela."""
        return self.input_vdiv() * self._probe_factor

    def input_vdiv(self) -> float:
        """Volts/div na entrada do aparelho, que define a faixa de entrada."""
        return self.volts_per_div.currentData()

    def is_ac(self) -> bool:
        return self.coupling.currentData()

    def settings(self) -> ChannelSettings:
        return ChannelSettings(
            probe=self._probe_factor,
            inverted=self.inverted.isChecked(),
            mains_filter=self.mains_filter.isChecked(),
            ac=self.is_ac(),
        )

    def summary(self) -> str:
        """Ajustes fora do padrão, para a barra de status."""
        parts = []
        if self._probe_factor != 1:
            parts.append(f"{self._probe_factor:g}x")
        if self.is_ac():
            parts.append("AC")
        if self.inverted.isChecked():
            parts.append("inv")
        if self.mains_filter.isChecked():
            parts.append(f"filtro {MAINS_HZ:g} Hz")
        return " ".join(parts)

    def _on_probe_changed(self) -> None:
        ratio = self.probe.currentData() / self._probe_factor
        self._probe_factor = self.probe.currentData()
        self._relabel_volts_per_div()
        self.probe_changed.emit(ratio)
        self.changed.emit()

    def _relabel_volts_per_div(self) -> None:
        for i, volts in enumerate(VOLTS_PER_DIV):
            self.volts_per_div.setItemText(i, format_si(volts * self._probe_factor, "V"))


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
        self._last_frame: Frame | None = None  # Como veio do aparelho.
        self._traces: list[np.ndarray] = []  # Canais de _last_frame já condicionados.
        self._math: np.ndarray | None = None
        # Ligar o MATH sem quadro na tela adia o "Ajustar escala" para o primeiro.
        self._math_fit_pending = False
        self._trigger_at: float | None = None  # Amostra do disparo em _last_frame.
        self._frame_times: deque[float] = deque(maxlen=30)
        self._syncing_trigger_line = False
        # Posição do disparo na tela, em divisões; pode ficar fora dela (zoom ou arrasto).
        self._trigger_x = HORIZONTAL_DIVS / 2
        # Volts/div de entrada escolhidos pelo sinal, nos canais em AC.
        self._ac_input_vdiv: list[float | None] = [None] * self._backend.n_channels

        self.resize(1200, 760)

        self._plot = ScopePlot(CHANNEL_COLORS)
        self._plot.setToolTip(
            "Roda do mouse: tempo/div, aproximando onde está o mouse\n"
            "Arrastar o fundo: desloca na horizontal\n"
            "Ctrl + roda: volts/div do CH1\nShift + roda: volts/div do CH2\n"
            "Clique direito em um cursor: remover"
        )
        self._cursor_readout = self._plot.cursor_readout
        self._measurement_readout = self._plot.measurement_readout
        self._curves = [
            self._plot.plot(pen=pg.mkPen(CHANNEL_COLORS[i], width=1), skipFiniteCheck=True)
            for i in range(self._backend.n_channels)
        ]
        # A divisão pode gerar lacunas (NaN), que não devem ligar os pontos vizinhos.
        self._math_curve = self._plot.plot(pen=pg.mkPen(MATH_COLOR, width=1), connect="finite")
        for curve in [*self._curves, self._math_curve]:
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
        self._plot.dragged.connect(lambda dx: self._set_trigger_x(self._trigger_x + dx))
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

        center = QPushButton("Disparo ao centro")
        center.setToolTip("Volta o ponto do disparo para o meio da tela")
        center.clicked.connect(lambda: self._set_trigger_x(HORIZONTAL_DIVS / 2))

        timebase = QGroupBox("Horizontal")
        timebase_form = QFormLayout(timebase)
        timebase_form.addRow("Tempo/div", self._time_per_div)
        timebase_form.addRow(center)

        self._channels = []
        for i in range(self._backend.n_channels):
            channel = ChannelControls(i, CHANNEL_COLORS[i])
            channel.changed.connect(self._on_settings_changed)
            channel.probe_changed.connect(lambda ratio, i=i: self._on_probe_changed(i, ratio))
            self._channels.append(channel)

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(source)
        layout.addWidget(self._run_button)
        layout.addWidget(self._pause_button)
        layout.addWidget(save)
        layout.addWidget(timebase)
        for channel in self._channels:
            layout.addWidget(channel)
        layout.addWidget(self._build_math_group())
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

    def _build_math_group(self) -> QGroupBox:
        self._math_operation = QComboBox()
        self._math_operation.addItem("Desligado", None)
        if self._backend.n_channels >= 2:
            for key, (label, _) in OPERATIONS.items():
                self._math_operation.addItem(label, key)
        self._math_operation.currentIndexChanged.connect(self._on_math_operation_changed)

        self._math_scale = QComboBox()
        for scale in MATH_SCALES:
            self._math_scale.addItem("", scale)
        self._math_scale.setCurrentIndex(MATH_SCALES.index(1.0))
        self._math_scale.currentIndexChanged.connect(self._on_settings_changed)

        self._math_position = QDoubleSpinBox()
        self._math_position.setRange(-VERTICAL_DIVS / 2, VERTICAL_DIVS / 2)
        self._math_position.setDecimals(1)
        self._math_position.setSingleStep(0.1)
        self._math_position.setSuffix(" div")
        self._math_position.valueChanged.connect(self._on_settings_changed)

        self._math_fit = QPushButton("Ajustar escala")
        self._math_fit.setToolTip("Escolhe escala e posição para o traço caber na tela")
        self._math_fit.clicked.connect(self._fit_math_scale)
        self._relabel_math_scale()

        group = QGroupBox("Matemática")
        group.setStyleSheet(f"QGroupBox {{ color: {MATH_COLOR}; font-weight: bold; }}")
        form = QFormLayout(group)
        form.addRow("Operação", self._math_operation)
        form.addRow("Escala/div", self._math_scale)
        form.addRow("Posição", self._math_position)
        form.addRow(self._math_fit)
        return group

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
        self._trigger_level.setRange(-500.0, 500.0)
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
        for i in range(self._n_traces):
            self._cursor_channel.addItem(self._trace_name(i), i)
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
            tuple(self._input_vdiv(i) for i in range(len(self._channels))),
            offscreen_divs=self._offscreen_divs(),
        )

    def _offscreen_divs(self) -> float:
        return max(0.0, -self._trigger_x, self._trigger_x - HORIZONTAL_DIVS)

    def _input_vdiv(self, channel: int) -> float:
        """Volts/div que definem a faixa de entrada do aparelho."""
        ac_vdiv = self._ac_input_vdiv[channel]
        return ac_vdiv if ac_vdiv is not None else self._channels[channel].input_vdiv()

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
        self._traces = []
        self._math = None
        self._trigger_at = None
        self._ac_input_vdiv = [None] * len(self._channels)
        for curve in [*self._curves, self._math_curve]:
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
        if frame is None:
            return
        self._adjust_ac_input_ranges(frame)
        if self._pause_button.isChecked():
            return
        traces = self._process(frame)
        trigger_at = self._find_trigger(frame.sample_rate, traces)
        mode = self._trigger_mode.currentData()
        if trigger_at is None and mode != "auto":
            self._trigger_status.setText("Aguardando disparo")
            return

        self._last_frame = frame
        self._traces = traces
        self._math = self._compute_math(traces)
        self._trigger_at = trigger_at
        self._frame_times.append(time.monotonic())
        if self._math_fit_pending and self._math is not None:
            self._math_fit_pending = False
            self._fit_math_scale()
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

    def _adjust_ac_input_ranges(self, frame: Frame) -> None:
        """Nos canais em AC, escolhe a faixa de entrada pelo pico do sinal."""
        changed = False
        for i, channel in enumerate(self._channels):
            if not channel.is_ac():
                continue
            peak = float(np.abs(frame.channels[i]).max())
            current = self._input_vdiv(i)
            chosen = ac_input_scale(peak, current, VOLTS_PER_DIV, VERTICAL_DIVS / 2)
            if chosen != self._ac_input_vdiv[i]:
                self._ac_input_vdiv[i] = chosen
                changed = True
        if changed and self._worker is not None:
            self._worker.set_config(self._current_config())

    def _find_trigger(self, sample_rate: float, traces: list[np.ndarray]) -> float | None:
        source = self._trigger_source.currentData()
        samples_per_div = sample_rate * self._tdiv()
        position = self._trigger_x
        n_samples = traces[source].size
        # O disparo precisa de dados suficientes antes e depois para encher a tela.
        start = max(0, math.ceil(position * samples_per_div))
        stop = min(n_samples, n_samples - math.ceil((HORIZONTAL_DIVS - position) * samples_per_div))
        return find_trigger(
            traces[source],
            level=self._trigger_level.value(),
            rising=self._trigger_edge.currentData(),
            hysteresis=TRIGGER_HYSTERESIS_DIVS * self._vdiv(source),
            start=start,
            stop=stop,
        )

    # Processamento

    def _process(self, frame: Frame) -> list[np.ndarray]:
        """Tensões na ponta da ponteira, com inversão, filtro e acoplamento."""
        return [
            condition(volts, frame.sample_rate, channel.settings())
            for volts, channel in zip(frame.channels, self._channels)
        ]

    def _compute_math(self, traces: list[np.ndarray]) -> np.ndarray | None:
        operation = self._math_operation.currentData()
        if operation is None:
            return None
        return combine(operation, traces[0], traces[1], MIN_DIVISOR_DIVS * self._vdiv(1))

    def _reprocess(self) -> None:
        """Refaz o processamento do quadro na tela depois de um ajuste."""
        if self._last_frame is not None:
            self._traces = self._process(self._last_frame)
            self._math = self._compute_math(self._traces)

    def _on_math_operation_changed(self) -> None:
        self._relabel_math_scale()
        self._reprocess()
        self._fit_math_scale()
        self._on_settings_changed()

    def _relabel_math_scale(self) -> None:
        unit = self._math_unit()
        for i, scale in enumerate(MATH_SCALES):
            self._math_scale.setItemText(i, format_si(scale, unit))

    def _fit_math_scale(self) -> None:
        if self._math is None or self._last_frame is None:
            self._math_fit_pending = self._math_operation.currentData() is not None
            return
        values = self._math[self._visible_samples(self._last_frame)]
        values = values[np.isfinite(values)]
        if not values.size:
            return
        low, high = float(values.min()), float(values.max())
        fitting = [s for s in MATH_SCALES if s * MATH_FIT_DIVS >= high - low]
        scale = fitting[0] if fitting else MATH_SCALES[-1]
        half = VERTICAL_DIVS / 2
        self._math_scale.setCurrentIndex(MATH_SCALES.index(scale))
        self._math_position.setValue(round(max(-half, min(half, -(high + low) / 2 / scale)), 1))

    # Traços: os canais e, depois deles, o MATH.

    @property
    def _n_traces(self) -> int:
        return len(self._channels) + 1

    def _is_math(self, index: int) -> bool:
        return index == len(self._channels)

    def _trace_name(self, index: int) -> str:
        return "MATH" if self._is_math(index) else f"CH{index + 1}"

    def _trace_color(self, index: int) -> str:
        return MATH_COLOR if self._is_math(index) else CHANNEL_COLORS[index]

    def _trace_on(self, index: int) -> bool:
        if self._is_math(index):
            return self._math_operation.currentData() is not None
        return self._enabled(index)

    def _trace_scale(self, index: int) -> float:
        return self._math_scale.currentData() if self._is_math(index) else self._vdiv(index)

    def _trace_offset(self, index: int) -> float:
        """Posição do 0 V do traço, em divisões."""
        widget = self._math_position if self._is_math(index) else self._channels[index].position
        return widget.value()

    def _trace_unit(self, index: int) -> str:
        return self._math_unit() if self._is_math(index) else "V"

    def _trace_values(self, index: int) -> np.ndarray | None:
        if self._is_math(index):
            return self._math
        return self._traces[index] if self._traces else None

    def _math_unit(self) -> str:
        operation = self._math_operation.currentData()
        return OPERATIONS[operation][1] if operation else "V"

    def _to_divs(self, index: int, value: float | np.ndarray) -> float | np.ndarray:
        return value / self._trace_scale(index) + self._trace_offset(index)

    def _from_divs(self, index: int, divs: float) -> float:
        return (divs - self._trace_offset(index)) * self._trace_scale(index)

    # Escalas e trigger

    def _tdiv(self) -> float:
        return self._time_per_div.currentData()

    def _vdiv(self, channel: int) -> float:
        return self._channels[channel].vdiv()

    def _enabled(self, channel: int) -> bool:
        return self._channels[channel].enabled.isChecked()

    def _time_at(self, x_divs: float) -> float:
        """Tempo relativo ao ponto de disparo."""
        return (x_divs - self._trigger_x) * self._tdiv()

    def _on_settings_changed(self) -> None:
        limit = max_offscreen_divs(self._tdiv(), self._backend.sample_rates)
        self._trigger_x = max(-limit, min(HORIZONTAL_DIVS + limit, self._trigger_x))
        self._plot.set_trigger_x(self._trigger_x)
        for i, channel in enumerate(self._channels):
            if not channel.is_ac():
                self._ac_input_vdiv[i] = None
        config = self._current_config()
        if self._worker is not None:
            self._worker.set_config(config)
        mains_possible = can_remove_mains(config.n_samples, config.sample_rate)
        for channel in self._channels:
            channel.mains_note.setVisible(channel.mains_filter.isChecked() and not mains_possible)
        self._reprocess()
        self._sync_trigger_line()
        self._update_axes()
        self._update_zero_markers()
        self._update_cursor_readout()
        self._redraw()
        self._update_scale_label()

    def _on_probe_changed(self, channel: int, ratio: float) -> None:
        # O nível do trigger acompanha a ponteira, para a linha ficar no mesmo lugar.
        if self._trigger_source.currentData() == channel:
            self._trigger_level.setValue(self._trigger_level.value() * ratio)

    def _update_axes(self) -> None:
        bottom = [format_si(self._time_at(i), "s") for i in range(HORIZONTAL_DIVS + 1)]
        half = VERTICAL_DIVS // 2
        vertical = []
        for channel in range(2):
            if channel < len(self._channels) and self._enabled(channel):
                vertical.append(
                    [format_si(self._from_divs(channel, i), "V") for i in range(-half, half + 1)]
                )
            else:
                vertical.append(None)
        self._plot.set_axis_labels(bottom, *vertical)

    def _update_zero_markers(self) -> None:
        for i in range(self._n_traces):
            name = "M" if self._is_math(i) else str(i + 1)
            y = self._trace_offset(i) if self._trace_on(i) else None
            self._plot.set_zero_marker(name, y, self._trace_color(i))

    def _set_trigger_x(self, x_divs: float) -> None:
        self._trigger_x = x_divs
        self._on_settings_changed()

    def _on_trigger_position_changed(self) -> None:
        self._trigger_x = self._plot.trigger_position.value()
        self._update_axes()
        self._update_cursor_readout()
        self._redraw()

    def _on_trigger_source_changed(self) -> None:
        source = self._trigger_source.currentData()
        self._plot.set_line_color(self._plot.trigger_level, CHANNEL_COLORS[source])
        self._sync_trigger_line()

    def _sync_trigger_line(self) -> None:
        """Posiciona a linha do nível conforme o valor em volts e a escala da fonte."""
        source = self._trigger_source.currentData()
        self._trigger_level.setSingleStep(TRIGGER_HYSTERESIS_DIVS * self._vdiv(source))
        self._syncing_trigger_line = True
        self._plot.trigger_level.setValue(self._to_divs(source, self._trigger_level.value()))
        self._syncing_trigger_line = False

    def _on_trigger_line_moved(self) -> None:
        if self._syncing_trigger_line:
            return
        source = self._trigger_source.currentData()
        volts = self._from_divs(source, self._plot.trigger_level.value())
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
            self._plot.set_line_color(line, self._trace_color(self._cursor_channel.currentData()))
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
        color = self._trace_color(self._cursor_channel.currentData())
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
            index = self._cursor_channel.currentData()
            name = self._trace_name(index)
            if self._trace_on(index):
                unit = self._trace_unit(index)
                values = [self._from_divs(index, line.value()) for line in self._plot.volt_cursors]
                for i, v in enumerate(values):
                    text = f"V{i + 1} {format_si(v, unit):>9}"
                    if i > 0:
                        text += f"   ΔV1 {format_si(v - values[0], unit):>9}"
                    lines.append(text)
                lines.append(f"(tensão do {name})")
            else:
                lines.append(f"({name} desligado)")

        self._plot.set_overlay_text(self._cursor_readout, "\n".join(lines))

    # Medições

    def _update_measurements(self) -> None:
        frame = self._last_frame
        if frame is None or not self._measurements_on.isChecked():
            self._plot.set_overlay_text(self._measurement_readout, "")
            return
        visible = self._visible_samples(frame)
        rows = []
        for index in range(self._n_traces):
            values = self._trace_values(index)
            if not self._trace_on(index) or values is None:
                continue
            values = values[visible]
            finite = np.isfinite(values)
            if not finite.any():
                continue
            min_vpp = MIN_VPP_FOR_FREQUENCY_DIVS * self._trace_scale(index)
            m = measure(values[finite], frame.sample_rate, min_vpp)
            if not finite.all():
                # Com lacunas, a contagem de bordas não dá uma frequência confiável.
                m = replace(m, frequency=None)
            unit = self._trace_unit(index)
            frequency = format_si(m.frequency, "Hz") if m.frequency else "—"
            period = format_si(m.period, "s") if m.period else "—"
            rows.append(
                f'<span style="color:{self._trace_color(index)}">{self._trace_name(index)}</span>'
                f"&nbsp; f {frequency} &nbsp; T {period} &nbsp; Vpp {format_si(m.vpp, unit)}"
                f" &nbsp; Vmín {format_si(m.vmin, unit)} &nbsp; Vmáx {format_si(m.vmax, unit)}"
                f" &nbsp; Vméd {format_si(m.vmean, unit)} &nbsp; RMS {format_si(m.vrms, unit)}"
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
        columns = {
            f"CH{i + 1}_V": volts[visible] for i, volts in enumerate(self._traces) if self._enabled(i)
        }
        if self._math is not None:
            columns[MATH_CSV_COLUMNS[self._math_unit()]] = self._math[visible]
        triggered = "sim" if self._trigger_at is not None else "não"
        header = [
            f"Hantek Scope {version('hantek-scope')}",
            f"Data: {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"Fonte: {self._backend.name}",
            f"Taxa de amostragem: {frame.sample_rate:g} S/s",
            f"Tempo/div: {format_si(self._tdiv(), 's')}",
            *(self._channel_header(i, frame) for i in range(len(self._channels))),
        ]
        if self._math is not None:
            header.append(
                f"MATH: {self._math_operation.currentText()},"
                f" {format_si(self._math_scale.currentData(), self._math_unit())}/div"
            )
        header += [
            f"Trigger: {self._trigger_mode.currentText()}, {self._trigger_source.currentText()},"
            f" {self._trigger_edge.currentText().lower()}, {self._trigger_level.value():g} V,"
            f" disparado: {triggered}",
            "Tempo medido a partir do disparo; tensões na ponta da ponteira.",
        ]
        write_csv(path, self._time_at(x), columns, header, excel_ptbr=excel_ptbr)

    def _channel_header(self, index: int, frame: Frame) -> str:
        channel = self._channels[index]
        settings = channel.settings()
        if settings.mains_filter:
            active = can_remove_mains(frame.channels.shape[1], frame.sample_rate)
            mains = "sim" if active else "inativo (captura curta)"
        else:
            mains = "não"
        return (
            f"CH{index + 1}: {format_si(channel.vdiv(), 'V')}/div, ponteira {settings.probe:g}x,"
            f" acoplamento {'AC' if settings.ac else 'DC'},"
            f" invertido: {'sim' if settings.inverted else 'não'}, filtro {MAINS_HZ:g} Hz: {mains}"
        )

    # Mouse

    def _on_wheel(self, steps: int, modifiers, x_divs: float | None = None) -> None:
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            combo = self._channels[0].volts_per_div
        elif modifiers & Qt.KeyboardModifier.ShiftModifier and len(self._channels) > 1:
            combo = self._channels[1].volts_per_div
        else:
            self._zoom_time(steps, x_divs)
            return
        # Rolar para cima aproxima, ou seja, escolhe uma escala menor.
        combo.setCurrentIndex(max(0, min(combo.count() - 1, combo.currentIndex() - steps)))

    def _zoom_time(self, steps: int, x_divs: float | None) -> None:
        """Muda o tempo/div mantendo parado o instante sob o mouse."""
        combo = self._time_per_div
        index = max(0, min(combo.count() - 1, combo.currentIndex() - steps))
        if index == combo.currentIndex():
            return
        anchor = self._trigger_x if x_divs is None else x_divs
        ratio = self._tdiv() / combo.itemData(index)
        self._trigger_x = anchor - (anchor - self._trigger_x) * ratio
        combo.setCurrentIndex(index)

    def _on_mouse_moved(self, x_divs: float, y_divs: float) -> None:
        self._mouse_label.setText(self._position_text(x_divs, y_divs))

    def _position_text(self, x_divs: float, y_divs: float) -> str:
        parts = [f"t = {format_si(self._time_at(x_divs), 's')}"]
        for i in range(self._n_traces):
            if self._trace_on(i):
                value = format_si(self._from_divs(i, y_divs), self._trace_unit(i))
                parts.append(f"{self._trace_name(i)} = {value}")
        return "   ".join(parts)

    # Desenho

    def _display_x(self, frame: Frame) -> np.ndarray:
        """Posição horizontal de cada amostra, em divisões da tela."""
        samples_per_div = frame.sample_rate * self._tdiv()
        index = np.arange(frame.channels.shape[1])
        if self._trigger_at is None:
            return index / samples_per_div
        return (index - self._trigger_at) / samples_per_div + self._trigger_x

    def _visible_samples(self, frame: Frame) -> np.ndarray:
        x = self._display_x(frame)
        return (x >= 0) & (x <= HORIZONTAL_DIVS)

    def _redraw(self) -> None:
        frame = self._last_frame
        if frame is None:
            return
        x = self._display_x(frame)
        for i, (curve, volts) in enumerate(zip(self._curves, self._traces)):
            curve.setVisible(self._enabled(i))
            curve.setData(x, self._to_divs(i, volts))
        math_index = len(self._channels)
        if self._math is None:
            self._math_curve.setData([], [])
        else:
            self._math_curve.setData(x, self._to_divs(math_index, self._math))
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
        for i, channel in enumerate(self._channels):
            if self._enabled(i):
                text = f"CH{i + 1} {format_si(self._vdiv(i), 'V')}/div {channel.summary()}"
                parts.append(text.strip())
        if self._math_operation.currentData() is not None:
            scale = format_si(self._math_scale.currentData(), self._math_unit())
            parts.append(f"MATH {self._math_operation.currentText()} {scale}/div")
        if self._worker is not None and len(self._frame_times) > 1:
            elapsed = self._frame_times[-1] - self._frame_times[0]
            if elapsed > 0:
                parts.append(f"{(len(self._frame_times) - 1) / elapsed:.0f} quadros/s")
        self._scale_label.setText("   |   ".join(parts))
