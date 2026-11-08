"""Gráficos de análise: espectro (FFT) e modo XY."""

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from hantek_scope.timebase import HORIZONTAL_DIVS, VERTICAL_DIVS
from hantek_scope.ui.scope_plot import BACKGROUND, CURSOR_COLOR, MARKER_COLOR
from hantek_scope.ui.units import format_si

XY_COLOR = "#bb86fc"
HALF_X = HORIZONTAL_DIVS // 2
HALF_Y = VERTICAL_DIVS // 2


def _overlay(parent) -> QLabel:
    label = QLabel(parent)
    label.setStyleSheet(
        f"background: rgba(20, 23, 28, 210); color: {CURSOR_COLOR}; border: 1px solid #3a3f45;"
        " padding: 4px; font-family: Consolas, monospace;"
    )
    label.move(70, 8)
    label.hide()
    return label


class FrequencyAxis(pg.AxisItem):
    """Marcas como "100 Hz" e "1 kHz", também no eixo log (onde o valor é log10)."""

    def tickStrings(self, values, scale, spacing):
        if self.logMode:
            return [format_si(10**v, "Hz") for v in values]
        return [format_si(v, "Hz") for v in values]


class SpectrumPlot(pg.PlotWidget):
    """Espectro com zoom e arrasto livres do pyqtgraph nos dois eixos."""

    def __init__(self) -> None:
        super().__init__(background=BACKGROUND, axisItems={"bottom": FrequencyAxis("bottom")})
        self.setMenuEnabled(False)
        self.hideButtons()
        self.showGrid(x=True, y=True, alpha=0.25)
        self.setLabel("bottom", "Frequência")
        self._curve = self.plot(skipFiniteCheck=True)
        self._curve.setDownsampling(auto=True, method="peak")
        self._curve.setClipToView(True)
        self._peak = pg.ScatterPlotItem(size=9, symbol="t", brush=None)
        self.addItem(self._peak)
        self.readout = _overlay(self)
        self._mouse_text = ""
        self._summary = ""
        self._unit = "dBV"
        self._log_x = False
        self.scene().sigMouseMoved.connect(self._on_mouse_moved)

    def set_spectrum(
        self,
        frequencies: np.ndarray,
        values: np.ndarray,
        color: str,
        unit: str,
        log_x: bool,
        peak: tuple[float, float] | None,
        summary: str,
    ) -> None:
        if log_x:
            # O eixo log não tem lugar para 0 Hz.
            keep = frequencies > 0
            frequencies, values = frequencies[keep], values[keep]
        if log_x != self._log_x:
            self.setLogMode(x=log_x, y=False)
            self._log_x = log_x
        if unit != self._unit:
            self._unit = unit
        self.setLabel("left", unit)
        self._curve.setPen(pg.mkPen(color, width=1))
        self._curve.setData(frequencies, values)
        if peak is None:
            self._peak.setData([], [])
        else:
            x = np.log10(peak[0]) if log_x and peak[0] > 0 else peak[0]
            self._peak.setData([x], [peak[1]], pen=pg.mkPen(color))
        self._summary = summary
        self._show_readout()

    def clear_spectrum(self) -> None:
        self._curve.setData([], [])
        self._peak.setData([], [])
        self._summary = ""
        self._show_readout()

    def fit(self) -> None:
        self.enableAutoRange()
        self.autoRange(padding=0.02)
        self.disableAutoRange()

    def _show_readout(self) -> None:
        text = "\n".join(t for t in (self._summary, self._mouse_text) if t)
        self.readout.setText(text)
        self.readout.setVisible(bool(text))
        self.readout.adjustSize()

    def _on_mouse_moved(self, pos) -> None:
        view = self.getViewBox()
        if view.sceneBoundingRect().contains(pos):
            point = view.mapSceneToView(pos)
            frequency = 10 ** point.x() if self._log_x else point.x()
            self._mouse_text = f"mouse: {format_si(frequency, 'Hz')}  {point.y():.4g} {self._unit}"
        else:
            self._mouse_text = ""
        self._show_readout()


class XYPlot(pg.PlotWidget):
    """CH1 na horizontal e CH2 na vertical, nas escalas e posições dos canais."""

    def __init__(self, x_color: str, y_color: str) -> None:
        super().__init__(background=BACKGROUND)
        self.setMenuEnabled(False)
        self.setMouseEnabled(x=False, y=False)
        self.hideButtons()
        self.showAxis("right", False)
        self.setRange(xRange=(-HALF_X, HALF_X), yRange=(-HALF_Y, HALF_Y), padding=0)
        self.showGrid(x=True, y=True, alpha=0.25)
        self.getAxis("bottom").setTextPen(x_color)
        self.getAxis("left").setTextPen(y_color)
        self._curve = self.plot(pen=pg.mkPen(XY_COLOR, width=1), connect="finite")
        self._curve.setDownsampling(auto=False)
        for value, angle in ((0, 0), (0, 90)):
            self.addItem(pg.InfiniteLine(value, angle=angle, pen=pg.mkPen(MARKER_COLOR, style=Qt.PenStyle.DotLine)))

    def set_axis_labels(self, bottom: list[str], left: list[str]) -> None:
        for side, ticks, labels in (
            ("bottom", range(-HALF_X, HALF_X + 1), bottom),
            ("left", range(-HALF_Y, HALF_Y + 1), left),
        ):
            axis = self.getAxis(side)
            axis.setStyle(tickLength=0)
            axis.setTicks([list(zip(ticks, labels)), []])

    def set_points(self, x_divs: np.ndarray, y_divs: np.ndarray) -> None:
        self._curve.setData(x_divs, y_divs)
