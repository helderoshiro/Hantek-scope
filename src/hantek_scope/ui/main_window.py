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
    QSplitter,
    QStackedWidget,
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
from hantek_scope.processing.spectrum import find_peak, spectrum
from hantek_scope.processing.timing import measure_delay, measure_timing
from hantek_scope.processing.trigger import find_trigger
from hantek_scope.timebase import (
    CAPTURE_DIVS,
    HORIZONTAL_DIVS,
    TIME_PER_DIV,
    VERTICAL_DIVS,
    RollPlan,
    capture_for,
    is_roll,
    max_offscreen_divs,
    roll_plan,
)
from hantek_scope.ui.analysis_plots import SpectrumPlot, XYPlot
from hantek_scope.ui.scope_plot import BACKGROUND, CURSOR_COLOR, ScopePlot
from hantek_scope.ui.units import format_si
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
FFT_WINDOWS = (
    ("Hann", "hann"),
    ("Flat top (amplitude)", "flattop"),
    ("Blackman-Harris", "blackmanharris"),
    ("Retangular", "rect"),
)
MIN_FFT_SAMPLES = 16
AVERAGE_COUNTS = (1, 2, 4, 8, 16, 32, 64, 128)
# Autoset: altura do sinal na tela, folga da faixa de entrada e quadros descartados
# depois de cada mudança (podem ter sido lidos com a configuração anterior).
AUTOSET_SCREEN_DIVS = 6
AUTOSET_HEADROOM = 1.05
AUTOSET_SKIP_FRAMES = 2
AUTOSET_MIN_VPP = 0.15  # Na entrada do aparelho, acima do ruído de 8 bits.
AUTOSET_SEARCH_TIME_PER_DIV = 5e-3
AUTOSET_PERIODS_ON_SCREEN = 2
# Sem frequência confiável na escala de busca (rápida demais para a taxa dela),
# o autoset mede de novo nesta escala, com a taxa máxima.
AUTOSET_FAST_TIME_PER_DIV = 20e-6
AUTOSET_MIN_SAMPLES_PER_PERIOD = 10
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
        # Modo roll: trechos (tempos, tensões) e o fim do último, em time.monotonic.
        self._roll_chunks: list[tuple[np.ndarray, np.ndarray]] = []
        self._roll_end = 0.0
        self._roll_t: np.ndarray | None = None  # Tempos das amostras de _last_frame.
        self._roll_key: tuple | None = None
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

        self._xy_plot = XYPlot(CHANNEL_COLORS[0], CHANNEL_COLORS[1])
        self._spectrum_plot = SpectrumPlot()
        self._spectrum_plot.hide()
        self._fft_fit_pending = False
        # Média: soma corrida dos traços alinhados pelo disparo.
        self._average: list[np.ndarray] | None = None
        self._average_trigger = 0.0
        self._average_count = 0
        # Autoset em andamento: etapa (1 = procurando, 2 = refinando) e quadros a pular.
        self._autoset_stage = 0
        self._autoset_skip = 0
        self._screens = QStackedWidget()
        self._screens.addWidget(self._plot)
        self._screens.addWidget(self._xy_plot)
        # Área mostrada e salva como imagem: a tela principal e, abaixo, a FFT.
        self._display = QSplitter(Qt.Orientation.Vertical)
        self._display.addWidget(self._screens)
        self._display.addWidget(self._spectrum_plot)
        self._display.setStretchFactor(0, 3)
        self._display.setStretchFactor(1, 2)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(self._display, stretch=1)
        layout.addWidget(self._build_side_panel())
        self.setCentralWidget(central)

        self._source_label = QLabel()
        self._scale_label = QLabel()
        self._mouse_label = QLabel()
        self.statusBar().addWidget(self._source_label)
        self.statusBar().addWidget(self._scale_label)
        self.statusBar().addPermanentWidget(self._mouse_label)

        self._plot.wheel_scrolled.connect(self._on_wheel)
        self._plot.dragged.connect(self._on_dragged)
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
        autoset = QPushButton("Autoset")
        autoset.setToolTip("Ajusta escalas, posição, tempo/div e trigger a partir dos sinais")
        autoset.clicked.connect(self.start_autoset)
        self._average_n = QComboBox()
        for count in AVERAGE_COUNTS:
            self._average_n.addItem("Desligada" if count == 1 else f"{count} capturas", count)
        self._average_n.setToolTip(
            "Média das capturas alinhadas pelo disparo: reduz o ruído de um sinal repetitivo"
        )
        self._average_n.currentIndexChanged.connect(self._on_settings_changed)
        acquisition = QGroupBox("Aquisição")
        acquisition_form = QFormLayout(acquisition)
        acquisition_form.addRow(autoset)
        acquisition_form.addRow("Média", self._average_n)

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
        layout.addWidget(acquisition)
        layout.addWidget(save)
        layout.addWidget(timebase)
        for channel in self._channels:
            layout.addWidget(channel)
        layout.addWidget(self._build_math_group())
        layout.addWidget(self._build_analysis_group())
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

    def _build_analysis_group(self) -> QGroupBox:
        self._xy_mode = QCheckBox("Modo XY (CH1 × CH2)")
        self._xy_mode.setToolTip("CH1 na horizontal e CH2 na vertical, como em uma figura de Lissajous")
        self._xy_mode.toggled.connect(self._on_xy_toggled)

        self._fft_on = QCheckBox("FFT")
        self._fft_on.setToolTip("Espectro do trecho visível na tela")
        self._fft_on.toggled.connect(self._on_fft_toggled)
        self._fft_source = QComboBox()
        for i in range(self._n_traces):
            self._fft_source.addItem(self._trace_name(i), i)
        self._fft_window = QComboBox()
        for label, key in FFT_WINDOWS:
            self._fft_window.addItem(label, key)
        self._fft_window.setToolTip(
            "Hann: uso geral. Flat top: lê a amplitude certa, com picos largos.\n"
            "Blackman-Harris: vê componentes fracos perto de fortes. Retangular: sem janela."
        )
        self._fft_scale = QComboBox()
        self._fft_scale.addItem("dB", True)
        self._fft_scale.addItem("Linear (RMS)", False)
        self._fft_log = QCheckBox("Frequência em escala log")
        # Em escala linear, um sinal de áudio fica espremido no canto: a FFT vai até
        # a metade da taxa, que costuma ser milhares de vezes maior.
        self._fft_log.setChecked(True)
        fit = QPushButton("Ajustar FFT")
        fit.setToolTip("Enquadra o espectro; no gráfico, roda e arrasto dão zoom")
        fit.clicked.connect(self._spectrum_plot.fit)
        for combo in (self._fft_source, self._fft_scale):
            combo.currentIndexChanged.connect(self._refit_fft)
        self._fft_window.currentIndexChanged.connect(self._update_spectrum)
        self._fft_log.toggled.connect(self._refit_fft)

        group = QGroupBox("Análise")
        form = QFormLayout(group)
        form.addRow(self._xy_mode)
        form.addRow(self._fft_on)
        form.addRow("Fonte", self._fft_source)
        form.addRow("Janela", self._fft_window)
        form.addRow("Escala", self._fft_scale)
        form.addRow(self._fft_log)
        form.addRow(fit)
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
        self._measure_pulses = QCheckBox("Pulsos: ciclo, larguras, subida e descida")
        self._measure_delay = QCheckBox("Atraso e fase do CH2 em relação ao CH1")
        group = QGroupBox("Medições")
        layout = QVBoxLayout(group)
        for checkbox in (self._measurements_on, self._measure_pulses, self._measure_delay):
            checkbox.toggled.connect(self._update_measurements)
            layout.addWidget(checkbox)
        for checkbox in (self._measure_pulses, self._measure_delay):
            checkbox.setStyleSheet("QCheckBox { margin-left: 16px; }")
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
        if self._is_roll():
            return self._roll_plan().config
        return capture_for(
            self._tdiv(),
            self._rates(),
            self._input_vdivs(),
            offscreen_divs=self._offscreen_divs(),
            channels=self._capture_channels(),
        )

    def _roll_plan(self) -> RollPlan:
        return roll_plan(self._tdiv(), self._rates(), self._input_vdivs(), self._capture_channels())

    def _is_roll(self) -> bool:
        return is_roll(self._tdiv())

    def _input_vdivs(self) -> tuple[float, ...]:
        return tuple(self._input_vdiv(i) for i in range(len(self._channels)))

    def _single_channel(self) -> bool:
        """Só o CH1 é necessário: o aparelho pode amostrar mais rápido."""
        if len(self._channels) < 2:
            return True
        return (
            not self._enabled(1)
            and not self._xy_mode.isChecked()
            and self._math_operation.currentData() is None
            and self._trigger_source.currentData() == 0
        )

    def _capture_channels(self) -> int:
        return 1 if self._single_channel() else self._backend.n_channels

    def _rates(self) -> tuple[float, ...]:
        if self._single_channel():
            return self._backend.single_channel_rates
        return self._backend.sample_rates

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
        self._clear_display()
        self._update_source_labels()
        self._update_scale_label()
        if was_running:
            self.start()

    def _clear_display(self) -> None:
        self._last_frame = None
        self._traces = []
        self._math = None
        self._trigger_at = None
        self._roll_chunks = []
        self._roll_t = None
        for curve in [*self._curves, self._math_curve]:
            curve.setData([], [])
        self._update_measurements()
        self._update_analysis()

    def _matches_config(self, frame: Frame, config: CaptureConfig) -> bool:
        """Descarta quadros lidos antes da última mudança de configuração."""
        return frame.sample_rate == config.sample_rate and frame.channels.shape[1] == config.n_samples

    def _on_frame_ready(self) -> None:
        if self._worker is None:
            return
        if self._is_roll():
            self._on_roll_frames(self._worker.take_frames())
            return
        frame = self._worker.take_frame()
        if frame is None or not self._matches_config(frame, self._current_config()):
            return
        self._adjust_ac_input_ranges(frame)
        if self._pause_button.isChecked():
            return
        traces = self._process(frame)
        if self._autoset_stage:
            self._autoset_step(frame, traces)
            return
        trigger_at = self._find_trigger(frame.sample_rate, traces)
        mode = self._trigger_mode.currentData()
        if trigger_at is None and mode != "auto":
            self._trigger_status.setText("Aguardando disparo")
            return
        if trigger_at is not None and self._average_n.currentData() > 1:
            traces, trigger_at = self._accumulate_average(traces, trigger_at)
        else:
            self._average = None

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

    def _on_roll_frames(self, frames: list[Frame]) -> None:
        """Acrescenta os trechos lidos ao fim do traço, que corre para a esquerda."""
        plan = self._roll_plan()
        rate = plan.output_rate
        chunk_s = plan.config.n_samples / plan.config.sample_rate
        added = False
        for frame in frames:
            if not self._matches_config(frame, plan.config):
                continue
            self._adjust_ac_input_ranges(frame)
            if self._pause_button.isChecked():
                continue
            n_channels = frame.channels.shape[0]
            volts = frame.channels.reshape(n_channels, -1, plan.decimation).mean(axis=2)
            n = volts.shape[1]
            end = frame.end_time if frame.end_time is not None else time.monotonic()
            start = end - n / rate
            if self._roll_chunks:
                gap = start - self._roll_end
                if gap > chunk_s / 2:
                    # Leitura interrompida: uma lacuna, em vez de ligar os trechos.
                    self._roll_chunks.append(
                        (np.array([self._roll_end]), np.full((n_channels, 1), np.nan))
                    )
                else:
                    # A latência do USB varia; trechos seguidos ficam encostados.
                    start = self._roll_end
            self._roll_chunks.append((start + np.arange(n) / rate, volts))
            self._roll_end = start + n / rate
            added = True
        if not added:
            return

        oldest = self._roll_end - HORIZONTAL_DIVS * self._tdiv()
        while len(self._roll_chunks) > 1 and self._roll_chunks[1][0][0] <= oldest:
            self._roll_chunks.pop(0)
        self._roll_t = np.concatenate([t for t, _ in self._roll_chunks])
        volts = np.concatenate([v for _, v in self._roll_chunks], axis=1)
        self._last_frame = Frame(rate, volts, end_time=self._roll_end)
        self._traces = self._process(self._last_frame)
        self._math = self._compute_math(self._traces)
        self._trigger_at = None
        self._frame_times.append(time.monotonic())
        if self._math_fit_pending and self._math is not None:
            self._math_fit_pending = False
            self._fit_math_scale()
        self._redraw()
        self._update_scale_label()
        self._trigger_status.setText("Modo roll (sem trigger)")

    # Média e autoset

    def _accumulate_average(self, traces: list[np.ndarray], trigger_at: float) -> tuple[list[np.ndarray], float]:
        """Soma a captura à média, deslocada para o disparo cair no mesmo lugar.

        Até juntar N capturas é a média simples; depois, cada nova pesa 1/N e
        as antigas vão saindo aos poucos (média exponencial).
        """
        limit = self._average_n.currentData()
        if self._average is None or self._average[0].shape != traces[0].shape:
            self._average = [trace.copy() for trace in traces]
            self._average_trigger = trigger_at
            self._average_count = 1
        else:
            shift = round(trigger_at - self._average_trigger)
            self._average_count = min(self._average_count + 1, limit)
            for average, trace in zip(self._average, traces):
                aligned = np.full(trace.shape, np.nan)
                source = trace[max(0, shift) : trace.size + min(0, shift)]
                aligned[max(0, -shift) : max(0, -shift) + source.size] = source
                valid = np.isfinite(aligned) & np.isfinite(average)
                average[valid] += (aligned[valid] - average[valid]) / self._average_count
        return [average.copy() for average in self._average], self._average_trigger

    def start_autoset(self) -> None:
        """Procura os sinais com escalas largas e depois ajusta tudo a eles."""
        self._autoset_stage = 1
        self._autoset_skip = AUTOSET_SKIP_FRAMES
        self._average = None
        self._trigger_status.setText("Autoset…")
        self._trigger_mode.setCurrentIndex(self._trigger_mode.findData("auto"))
        self._trigger_x = HORIZONTAL_DIVS / 2
        largest = VOLTS_PER_DIV[-1]
        for channel in self._channels:
            channel.volts_per_div.blockSignals(True)
            channel.volts_per_div.setCurrentIndex(VOLTS_PER_DIV.index(largest))
            channel.volts_per_div.blockSignals(False)
        self._time_per_div.blockSignals(True)
        self._time_per_div.setCurrentIndex(TIME_PER_DIV.index(AUTOSET_SEARCH_TIME_PER_DIV))
        self._time_per_div.blockSignals(False)
        self._on_settings_changed()
        if not self._run_button.isChecked():
            self.start()

    def _autoset_step(self, frame: Frame, traces: list[np.ndarray]) -> None:
        if self._autoset_skip > 0:
            self._autoset_skip -= 1
            return
        found = []
        for i, (raw, trace) in enumerate(zip(frame.channels, traces)):
            m = measure(trace, frame.sample_rate)
            if float(raw.max() - raw.min()) >= AUTOSET_MIN_VPP:
                found.append((i, m))
        if not found:
            self._autoset_stage = 0
            self._time_per_div.setCurrentIndex(TIME_PER_DIV.index(1e-3))
            self._trigger_status.setText("Autoset: nenhum sinal encontrado")
            return

        for i, m in found:
            self._autoset_channel(i, m, frame)
        source, reference = next(((i, m) for i, m in found if m.frequency), found[0])
        old_tdiv = self._tdiv()
        reliable = (
            reference.frequency is not None
            and reference.frequency * AUTOSET_MIN_SAMPLES_PER_PERIOD <= frame.sample_rate
        )
        if self._autoset_stage == 1 and not reliable:
            tdiv = AUTOSET_FAST_TIME_PER_DIV
        elif reference.frequency:
            wanted = AUTOSET_PERIODS_ON_SCREEN / (HORIZONTAL_DIVS * reference.frequency)
            candidates = [t for t in TIME_PER_DIV if not is_roll(t) and t >= wanted]
            tdiv = candidates[0] if candidates else max(t for t in TIME_PER_DIV if not is_roll(t))
        else:
            tdiv = 1e-3  # Nível contínuo: qualquer escala serve.
        self._trigger_source.setCurrentIndex(self._trigger_source.findData(source))
        self._trigger_edge.setCurrentIndex(0)
        self._trigger_level.setValue((reference.vmax + reference.vmin) / 2)
        self._time_per_div.setCurrentIndex(TIME_PER_DIV.index(tdiv))
        # Na escala de busca, uma frequência alta pode ter sido mal medida: a
        # segunda passada mede de novo na escala nova e corrige.
        if self._autoset_stage == 1 and tdiv != old_tdiv:
            self._autoset_stage = 2
            self._autoset_skip = AUTOSET_SKIP_FRAMES
        else:
            self._autoset_stage = 0
            self._trigger_status.setText("Autoset concluído")

    def _autoset_channel(self, index: int, m, frame: Frame) -> None:
        """Volts/div para o sinal ocupar ~6 divisões sem saturar a entrada, e centrado.

        A posição vai até 4 divisões: um nível contínuo maior que isso obriga a
        uma escala mais larga (ou ao acoplamento AC).
        """
        channel = self._channels[index]
        probe = channel.settings().probe
        peak_input = max(abs(m.vmax), abs(m.vmin)) / probe
        half = VERTICAL_DIVS / 2

        def position(vdiv: float) -> float:
            return round(max(-half, min(half, -(m.vmax + m.vmin) / 2 / vdiv)), 1)

        def on_screen(vdiv: float) -> bool:
            p = position(vdiv)
            return m.vmax / vdiv + p <= half and m.vmin / vdiv + p >= -half

        fitting = [
            v
            for v in VOLTS_PER_DIV
            if v * probe * AUTOSET_SCREEN_DIVS >= m.vpp
            and on_screen(v * probe)
            and (channel.is_ac() or self._backend.full_scale(v) >= peak_input * AUTOSET_HEADROOM)
        ]
        base = fitting[0] if fitting else VOLTS_PER_DIV[-1]
        channel.volts_per_div.setCurrentIndex(VOLTS_PER_DIV.index(base))
        channel.position.setValue(position(base * probe))
        channel.enabled.setChecked(True)

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
        """Tensões na ponta da ponteira, com inversão, filtro e acoplamento.

        As lacunas (NaN) do modo roll ficam de fora do processamento.
        """
        traces = []
        for volts, channel in zip(frame.channels, self._channels):
            finite = np.isfinite(volts)
            if finite.all():
                traces.append(condition(volts, frame.sample_rate, channel.settings()))
            else:
                trace = np.full(volts.shape, np.nan)
                if finite.any():
                    trace[finite] = condition(volts[finite], frame.sample_rate, channel.settings())
                traces.append(trace)
        return traces

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
        """Tempo relativo ao disparo; no modo roll, à amostra mais recente."""
        origin = HORIZONTAL_DIVS if self._is_roll() else self._trigger_x
        return (x_divs - origin) * self._tdiv()

    def _on_settings_changed(self) -> None:
        self._average = None
        roll = self._is_roll()
        if not roll:
            limit = max_offscreen_divs(self._tdiv(), self._rates())
            self._trigger_x = max(-limit, min(HORIZONTAL_DIVS + limit, self._trigger_x))
        self._plot.set_trigger_x(None if roll else self._trigger_x)
        self._plot.trigger_level.setVisible(not roll)
        for i, channel in enumerate(self._channels):
            if not channel.is_ac():
                self._ac_input_vdiv[i] = None
        config = self._current_config()
        self._check_roll_change(config)
        if self._worker is not None:
            self._worker.set_config(config)
        if roll:
            # No roll o filtro atua no traço inteiro, que tem vários ciclos da rede.
            mains_possible = self._roll_plan().output_rate > 2 * MAINS_HZ
        else:
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

    def _check_roll_change(self, config: CaptureConfig) -> None:
        """Recomeça o traço ao entrar ou sair do roll, ou ao mudar a taxa dele.

        Parado, o traço do roll continua na tela e acompanha o tempo/div.
        """
        roll = self._is_roll()
        key = (config.sample_rate, self._roll_plan().decimation, config.channels) if roll else None
        was_roll = self._roll_key is not None
        if roll != was_roll and self._last_frame is not None:
            self._clear_display()
        elif roll and key != self._roll_key and self._worker is not None:
            self._clear_display()
        self._roll_key = key

    def _on_dragged(self, dx_divs: float) -> None:
        if not self._is_roll():
            self._set_trigger_x(self._trigger_x + dx_divs)

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
        self._average = None  # Outro nível desloca o ponto de alinhamento.
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
            name = f'<span style="color:{self._trace_color(index)}">{self._trace_name(index)}</span>'
            rows.append(
                f"{name}&nbsp; f {frequency} &nbsp; T {period} &nbsp; Vpp {format_si(m.vpp, unit)}"
                f" &nbsp; Vmín {format_si(m.vmin, unit)} &nbsp; Vmáx {format_si(m.vmax, unit)}"
                f" &nbsp; Vméd {format_si(m.vmean, unit)} &nbsp; RMS {format_si(m.vrms, unit)}"
            )
            if self._measure_pulses.isChecked() and finite.all():
                rows.append(self._pulse_row(index, values, frame.sample_rate, min_vpp))
        if self._measure_delay.isChecked():
            rows.append(self._delay_row(visible, frame.sample_rate))
        self._plot.set_overlay_text(self._measurement_readout, "<br>".join(r for r in rows if r))

    def _pulse_row(self, index: int, values: np.ndarray, sample_rate: float, min_amplitude: float) -> str:
        t = measure_timing(values, sample_rate, min_amplitude)

        def seconds(value: float | None) -> str:
            return format_si(value, "s") if value is not None else "—"

        duty = f"{100 * t.duty_cycle:.1f}%" if t.duty_cycle is not None else "—"
        indent = "&nbsp;" * (len(self._trace_name(index)) + 1)
        return (
            f"{indent} ciclo {duty} &nbsp; larg+ {seconds(t.width_positive)}"
            f" &nbsp; larg− {seconds(t.width_negative)} &nbsp; subida {seconds(t.rise_time)}"
            f" &nbsp; descida {seconds(t.fall_time)}"
        )

    def _delay_row(self, visible: np.ndarray, sample_rate: float) -> str:
        label = "CH1→CH2"
        if len(self._traces) < 2:
            return ""
        a, b = self._traces[0][visible], self._traces[1][visible]
        if not (np.isfinite(a).all() and np.isfinite(b).all()) or a.size < 3:
            return f"{label} &nbsp; atraso — &nbsp; fase —"
        period = measure(a, sample_rate, MIN_VPP_FOR_FREQUENCY_DIVS * self._vdiv(0)).period
        result = measure_delay(
            a,
            b,
            sample_rate,
            period,
            min_amplitude=MIN_VPP_FOR_FREQUENCY_DIVS * min(self._vdiv(0), self._vdiv(1)),
        )
        if result is None:
            return f"{label} &nbsp; atraso — &nbsp; fase —"
        delay, phase = result
        phase_text = f"{phase:.1f}°" if phase is not None else "—"
        return f"{label} &nbsp; atraso {format_si(delay, 's')} &nbsp; fase {phase_text}"

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
        plot = self._display.grab()
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
            "Modo roll: tempo medido até a amostra mais recente."
            if self._is_roll()
            else "Tempo medido a partir do disparo; tensões na ponta da ponteira.",
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
        if self._is_roll() and self._roll_t is not None:
            return HORIZONTAL_DIVS - (frame.end_time - self._roll_t) / self._tdiv()
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
        self._update_analysis()

    # Análise: modo XY e FFT

    def _on_xy_toggled(self, on: bool) -> None:
        self._screens.setCurrentWidget(self._xy_plot if on else self._plot)
        # O XY precisa dos dois canais, mesmo com o CH2 desligado na tela.
        self._on_settings_changed()

    def _on_fft_toggled(self, on: bool) -> None:
        self._spectrum_plot.setVisible(on)
        if on:
            # Abre a FFT com 40% da altura, em vez do mínimo que o divisor daria.
            total = sum(self._display.sizes()) or self._display.height()
            self._display.setSizes([round(total * 0.6), round(total * 0.4)])
        self._refit_fft()

    def _refit_fft(self) -> None:
        self._fft_fit_pending = True
        self._update_spectrum()

    def _update_analysis(self) -> None:
        self._update_xy()
        self._update_spectrum()

    def _update_xy(self) -> None:
        if not self._xy_mode.isChecked():
            return
        half_x, half_y = HORIZONTAL_DIVS // 2, VERTICAL_DIVS // 2
        self._xy_plot.set_axis_labels(
            [format_si(self._from_divs(0, i), "V") for i in range(-half_x, half_x + 1)],
            [format_si(self._from_divs(1, i), "V") for i in range(-half_y, half_y + 1)],
        )
        frame = self._last_frame
        if frame is None or len(self._traces) < 2:
            self._xy_plot.set_points(np.array([]), np.array([]))
            return
        visible = self._visible_samples(frame)
        self._xy_plot.set_points(
            self._to_divs(0, self._traces[0][visible]), self._to_divs(1, self._traces[1][visible])
        )

    def _update_spectrum(self) -> None:
        if not self._fft_on.isChecked():
            return
        frame = self._last_frame
        index = self._fft_source.currentData()
        values = self._trace_values(index) if frame is not None else None
        if values is None or not self._trace_on(index):
            self._spectrum_plot.clear_spectrum()
            return
        values = values[self._visible_samples(frame)]
        values = values[np.isfinite(values)]
        if values.size < MIN_FFT_SAMPLES:
            self._spectrum_plot.clear_spectrum()
            return
        window_name = self._fft_window.currentData()
        spec = spectrum(values, frame.sample_rate, window_name)
        peak = find_peak(spec, window_name)
        in_db = self._fft_scale.currentData()
        unit = self._trace_unit(index)
        db_unit = "dBV" if unit == "V" else f"dB (1 {unit})"
        to_display = (lambda v: 20 * np.log10(np.maximum(v, 1e-12))) if in_db else (lambda v: v)
        summary = f"{self._trace_name(index)}   resolução {format_si(spec.resolution, 'Hz')}"
        marker = None
        if peak is not None:
            level = f"{to_display(peak.vrms):.1f} {db_unit}" if in_db else f"{format_si(peak.vrms, unit)} RMS"
            summary += f"\npico {format_si(peak.frequency, 'Hz')}   {level}"
            marker = (peak.frequency, float(to_display(peak.vrms)))
        self._spectrum_plot.set_spectrum(
            spec.frequencies,
            to_display(spec.vrms),
            self._trace_color(index),
            db_unit if in_db else f"{unit} RMS",
            self._fft_log.isChecked(),
            marker,
            summary,
        )
        if self._fft_fit_pending:
            self._fft_fit_pending = False
            self._spectrum_plot.fit()

    def _update_source_labels(self) -> None:
        self.setWindowTitle(f"Hantek Scope ({self._backend.name})")
        self._source_label.setText(f"Fonte: {self._backend.name}")

    def _update_scale_label(self) -> None:
        config = self._current_config()
        parts = [
            f"{format_si(self._tdiv(), 's')}/div",
            f"{format_si(config.sample_rate, 'S/s')}",
        ]
        if self._is_roll():
            parts[0] += " (roll)"
        elif config.channels == 1 and len(self._channels) > 1:
            parts[1] += " (só CH1)"
        for i, channel in enumerate(self._channels):
            if self._enabled(i):
                text = f"CH{i + 1} {format_si(self._vdiv(i), 'V')}/div {channel.summary()}"
                parts.append(text.strip())
        if self._math_operation.currentData() is not None:
            scale = format_si(self._math_scale.currentData(), self._math_unit())
            parts.append(f"MATH {self._math_operation.currentText()} {scale}/div")
        average = self._average_n.currentData()
        if average > 1 and not self._is_roll():
            count = self._average_count if self._average is not None else 0
            parts.append(f"média {count}/{average}")
        if self._worker is not None and len(self._frame_times) > 1:
            elapsed = self._frame_times[-1] - self._frame_times[0]
            if elapsed > 0:
                parts.append(f"{(len(self._frame_times) - 1) / elapsed:.0f} quadros/s")
        self._scale_label.setText("   |   ".join(parts))
