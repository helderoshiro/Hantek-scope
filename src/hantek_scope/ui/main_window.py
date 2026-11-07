import time
from collections import deque
from collections.abc import Sequence

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import pyqtgraph as pg

from hantek_scope.acquisition.base import CaptureConfig, Frame, ScopeBackend
from hantek_scope.timebase import HORIZONTAL_DIVS, TIME_PER_DIV, VERTICAL_DIVS, capture_for
from hantek_scope.ui.worker import AcquisitionWorker

CHANNEL_COLORS = ("#f2c94c", "#56ccf2", "#6fcf97", "#eb5757")
VOLTS_PER_DIV = (0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0)
DEFAULT_VOLTS_PER_DIV = 1.0
DEFAULT_TIME_PER_DIV = 200e-6


def format_si(value: float, unit: str) -> str:
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
        self._frame_times: deque[float] = deque(maxlen=30)

        self.resize(1100, 680)

        self._plot = self._build_plot()
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
        self.statusBar().addWidget(self._source_label)
        self.statusBar().addWidget(self._scale_label)
        self._update_source_labels()
        self._update_scale_label()

    def _build_plot(self) -> pg.PlotWidget:
        plot = pg.PlotWidget(background="#14171c")
        plot.setMenuEnabled(False)
        plot.setMouseEnabled(x=False, y=False)
        plot.hideButtons()
        plot.setRange(
            xRange=(0, HORIZONTAL_DIVS),
            yRange=(-VERTICAL_DIVS / 2, VERTICAL_DIVS / 2),
            padding=0,
        )
        # A grade representa divisões, como na tela de um osciloscópio de bancada.
        for side, start, stop in (
            ("bottom", 0, HORIZONTAL_DIVS),
            ("left", -VERTICAL_DIVS // 2, VERTICAL_DIVS // 2),
        ):
            axis = plot.getAxis(side)
            axis.setTicks([[(tick, "") for tick in range(start, stop + 1)], []])
            axis.setStyle(showValues=False, tickLength=0)
        plot.showGrid(x=True, y=True, alpha=0.25)
        return plot

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
        panel.setFixedWidth(220)
        layout = QVBoxLayout(panel)
        layout.addWidget(source)
        layout.addWidget(self._run_button)
        layout.addWidget(timebase)
        for channel in self._channels:
            layout.addWidget(channel)
        layout.addStretch(1)
        return panel

    def start(self) -> None:
        self._run_button.setChecked(True)

    def stop(self) -> None:
        self._run_button.setChecked(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.stop()
        super().closeEvent(event)

    def _current_config(self) -> CaptureConfig:
        return capture_for(
            self._time_per_div.currentData(),
            self._backend.sample_rates,
            tuple(c.volts_per_div.currentData() for c in self._channels),
        )

    def _on_source_changed(self) -> None:
        was_running = self._run_button.isChecked()
        self.stop()
        self._backend = self._source.currentData()
        # O traço antigo não pode continuar na tela identificado como da nova fonte.
        self._last_frame = None
        for curve in self._curves:
            curve.setData([], [])
        self._update_source_labels()
        self._update_scale_label()
        if was_running:
            self.start()

    def _update_source_labels(self) -> None:
        self.setWindowTitle(f"Hantek Scope ({self._backend.name})")
        self._source_label.setText(f"Fonte: {self._backend.name}")

    def _on_run_toggled(self, running: bool) -> None:
        self._run_button.setText("Parar" if running else "Executar")
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

    def _on_settings_changed(self) -> None:
        if self._worker is not None:
            self._worker.set_config(self._current_config())
        self._redraw()
        self._update_scale_label()

    def _on_frame_ready(self) -> None:
        if self._worker is None:
            return
        frame = self._worker.take_frame()
        if frame is None:
            return
        self._last_frame = frame
        self._frame_times.append(time.monotonic())
        self._redraw()
        self._update_scale_label()

    def _on_failed(self, message: str) -> None:
        self.stop()
        QMessageBox.critical(self, "Falha na aquisição", message)

    def _redraw(self) -> None:
        frame = self._last_frame
        if frame is None:
            return
        n_samples = frame.channels.shape[1]
        sample_period_divs = 1 / (frame.sample_rate * self._time_per_div.currentData())
        x = np.arange(n_samples) * sample_period_divs
        for curve, controls, volts in zip(self._curves, self._channels, frame.channels):
            curve.setVisible(controls.enabled.isChecked())
            curve.setData(x, volts / controls.volts_per_div.currentData())

    def _update_scale_label(self) -> None:
        config = self._current_config()
        parts = [
            f"{format_si(self._time_per_div.currentData(), 's')}/div",
            f"{format_si(config.sample_rate, 'S/s')}",
        ]
        for i, controls in enumerate(self._channels):
            if controls.enabled.isChecked():
                parts.append(f"CH{i + 1} {format_si(controls.volts_per_div.currentData(), 'V')}/div")
        if self._worker is not None and len(self._frame_times) > 1:
            elapsed = self._frame_times[-1] - self._frame_times[0]
            if elapsed > 0:
                parts.append(f"{(len(self._frame_times) - 1) / elapsed:.0f} quadros/s")
        self._scale_label.setText("   |   ".join(parts))
