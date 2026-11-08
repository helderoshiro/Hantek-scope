from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QLabel

import pyqtgraph as pg

from hantek_scope.timebase import HORIZONTAL_DIVS, VERTICAL_DIVS

HALF_DIVS = VERTICAL_DIVS // 2
WHEEL_STEP = 120  # Um "clique" da roda do mouse.
BACKGROUND = "#14171c"
MARKER_COLOR = "#9aa0a6"
CURSOR_COLOR = "#e0e0e0"
OVERLAY_MARGIN = (60, 8)  # Distância das leituras até a borda do gráfico.


class ScopePlot(pg.PlotWidget):
    """Tela do osciloscópio, com coordenadas em divisões.

    O eixo esquerdo mostra a escala do CH1 e o direito, a do CH2.
    """

    wheel_scrolled = Signal(int, object)  # Passos (positivo = aproximar), modificadores.
    mouse_moved = Signal(float, float)  # Posição em divisões.
    mouse_left = Signal()
    cursor_remove_requested = Signal(object)

    def __init__(self, channel_colors: tuple[str, ...]) -> None:
        super().__init__(background=BACKGROUND)
        self.setMenuEnabled(False)
        self.setMouseEnabled(x=False, y=False)
        self.hideButtons()
        self.showAxis("right")
        self.setRange(xRange=(0, HORIZONTAL_DIVS), yRange=(-HALF_DIVS, HALF_DIVS), padding=0)
        for side, color in (("left", channel_colors[0]), ("right", channel_colors[1])):
            self.getAxis(side).setTextPen(color)
        self.showGrid(x=True, y=True, alpha=0.25)
        self.set_axis_labels(None, None, None)

        self.trigger_position = self._line(90, HORIZONTAL_DIVS / 2, "T", MARKER_COLOR, 0.97)
        self.trigger_level = self._line(0, 0, "T", channel_colors[0], 0.02)
        self.time_cursors: list[pg.InfiniteLine] = []
        self.volt_cursors: list[pg.InfiniteLine] = []

        # Leituras sobre o gráfico: cursores no alto e medições embaixo.
        self.cursor_readout = self._overlay()
        self.measurement_readout = self._overlay()

        self._wheel_accumulated = 0
        self.viewport().installEventFilter(self)
        self.scene().sigMouseMoved.connect(self._on_scene_mouse_moved)

    def set_axis_labels(
        self,
        bottom: list[str] | None,
        left: list[str] | None,
        right: list[str] | None,
    ) -> None:
        """Cada lista tem um texto por linha da grade; None oculta os valores."""
        self._set_labels("bottom", range(HORIZONTAL_DIVS + 1), bottom)
        self._set_labels("left", range(-HALF_DIVS, HALF_DIVS + 1), left)
        self._set_labels("right", range(-HALF_DIVS, HALF_DIVS + 1), right)

    def set_line_color(self, line: pg.InfiniteLine, color: str) -> None:
        line.setPen(pg.mkPen(color, width=1, style=Qt.PenStyle.DashLine))
        line.label.setColor(color)

    def add_cursor(self, vertical: bool, pos: float, name: str) -> pg.InfiniteLine:
        """Cursor vertical mede tempo; horizontal mede tensão."""
        line = self._line(90 if vertical else 0, pos, name, CURSOR_COLOR, 0.9 if vertical else 0.1)
        line.sigClicked.connect(self._on_cursor_clicked)
        (self.time_cursors if vertical else self.volt_cursors).append(line)
        return line

    def remove_cursor(self, line: pg.InfiniteLine) -> None:
        for cursors in (self.time_cursors, self.volt_cursors):
            if line in cursors:
                cursors.remove(line)
        self.removeItem(line)

    def set_overlay_text(self, label: QLabel, html: str) -> None:
        label.setText(html)
        label.setVisible(bool(html))
        label.adjustSize()
        self._place_overlays()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # O PlotWidget já recebe resizeEvent dentro do próprio __init__.
        if hasattr(self, "measurement_readout"):
            self._place_overlays()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.viewport():
            if event.type() == QEvent.Type.Wheel:
                delta = event.angleDelta()
                # Com Shift, alguns sistemas convertem a rolagem em horizontal.
                self._wheel_accumulated += delta.y() or delta.x()
                steps = int(self._wheel_accumulated / WHEEL_STEP)
                if steps:
                    self._wheel_accumulated -= steps * WHEEL_STEP
                    self.wheel_scrolled.emit(steps, event.modifiers())
                return True
            if event.type() == QEvent.Type.Leave:
                self.mouse_left.emit()
        return super().eventFilter(watched, event)

    def _overlay(self) -> QLabel:
        label = QLabel(self)
        label.setStyleSheet(
            f"background: rgba(20, 23, 28, 210); color: {CURSOR_COLOR}; border: 1px solid #3a3f45;"
            " padding: 4px; font-family: Consolas, monospace;"
        )
        label.hide()
        return label

    def _place_overlays(self) -> None:
        x, y = OVERLAY_MARGIN
        self.cursor_readout.move(x, y)
        bottom_axis = self.getAxis("bottom").height()
        self.measurement_readout.move(x, self.height() - self.measurement_readout.height() - bottom_axis - y)

    def _set_labels(self, side: str, ticks: range, labels: list[str] | None) -> None:
        axis = self.getAxis(side)
        axis.setStyle(showValues=labels is not None, tickLength=0)
        texts = labels if labels is not None else [""] * len(ticks)
        axis.setTicks([list(zip(ticks, texts)), []])

    def _line(self, angle: int, pos: float, name: str, color: str, label_pos: float) -> pg.InfiniteLine:
        bounds = (0, HORIZONTAL_DIVS) if angle == 90 else (-HALF_DIVS, HALF_DIVS)
        line = pg.InfiniteLine(
            pos,
            angle=angle,
            movable=True,
            bounds=bounds,
            label=name,
            labelOpts={"position": label_pos, "fill": BACKGROUND},
        )
        self.set_line_color(line, color)
        line.setHoverPen(pg.mkPen(color, width=2))
        self.addItem(line)
        return line

    def _on_cursor_clicked(self, line: pg.InfiniteLine, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.cursor_remove_requested.emit(line)

    def _on_scene_mouse_moved(self, pos) -> None:
        view = self.getViewBox()
        if view.sceneBoundingRect().contains(pos):
            point = view.mapSceneToView(pos)
            self.mouse_moved.emit(point.x(), point.y())
        else:
            self.mouse_left.emit()
