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
PAN_DIVS_PER_STEP = 0.5


class _DragViewBox(pg.ViewBox):
    """Arrastar o fundo com o botão esquerdo, ou com o do meio (a roda
    pressionada) em qualquer ponto, desloca na horizontal.

    Com o botão esquerdo, linhas e cursores sob o mouse recebem o arrasto antes;
    o botão do meio não os move, então desloca mesmo sobre eles.
    """

    def __init__(self, on_drag) -> None:
        super().__init__()
        self._on_drag = on_drag

    def mouseDragEvent(self, event, axis=None) -> None:
        if event.button() not in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            event.ignore()
            return
        event.accept()
        before = self.mapSceneToView(event.lastScenePos()).x()
        after = self.mapSceneToView(event.scenePos()).x()
        self._on_drag(after - before)


class ScopePlot(pg.PlotWidget):
    """Tela do osciloscópio, com coordenadas em divisões.

    O eixo esquerdo mostra a escala do CH1 e o direito, a do CH2.
    """

    # Passos (positivo = aproximar), modificadores e posição x do mouse, em divisões.
    wheel_scrolled = Signal(int, object, float)
    dragged = Signal(float)  # Deslocamento horizontal, em divisões.
    mouse_moved = Signal(float, float)  # Posição em divisões.
    mouse_left = Signal()
    cursor_remove_requested = Signal(object)

    def __init__(self, channel_colors: tuple[str, ...]) -> None:
        super().__init__(background=BACKGROUND, viewBox=_DragViewBox(lambda dx: self.dragged.emit(dx)))
        self.setMenuEnabled(False)
        self.setMouseEnabled(x=False, y=False)
        self.hideButtons()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # Para receber as setas.
        self.showAxis("right")
        self.setRange(xRange=(0, HORIZONTAL_DIVS), yRange=(-HALF_DIVS, HALF_DIVS), padding=0)
        for side, color in (("left", channel_colors[0]), ("right", channel_colors[1])):
            self.getAxis(side).setTextPen(color)
        self.showGrid(x=True, y=True, alpha=0.25)
        self.set_axis_labels(None, None, None)

        self.trigger_position = self._line(90, HORIZONTAL_DIVS / 2, "T", MARKER_COLOR, 0.97)
        self.trigger_level = self._line(0, 0, "T", channel_colors[0], 0.02)
        # Com o disparo fora da tela, a linha some e esta seta aponta para ele.
        self._trigger_arrow = pg.TextItem(color=MARKER_COLOR)
        self._trigger_arrow.setZValue(10)
        self.addItem(self._trigger_arrow, ignoreBounds=True)
        self._trigger_arrow.hide()
        self.time_cursors: list[pg.InfiniteLine] = []
        self.volt_cursors: list[pg.InfiniteLine] = []
        self._zero_markers: dict[str, pg.TextItem] = {}

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

    def set_trigger_x(self, x_divs: float | None, offscreen_text: str = "") -> None:
        """Põe a linha do disparo em x; fora da tela, mostra uma seta na borda.

        None oculta as duas, como no modo roll, que não tem disparo.
        """
        on_screen = x_divs is not None and 0 <= x_divs <= HORIZONTAL_DIVS
        self.trigger_position.setVisible(on_screen)
        self._trigger_arrow.setVisible(x_divs is not None and not on_screen)
        if x_divs is None:
            return
        if on_screen:
            self.trigger_position.blockSignals(True)
            self.trigger_position.setValue(x_divs)
            self.trigger_position.blockSignals(False)
        elif x_divs < 0:
            self._trigger_arrow.setText(f"◀ T {offscreen_text}")
            self._trigger_arrow.setAnchor((0, 0))
            self._trigger_arrow.setPos(0, HALF_DIVS)
        else:
            self._trigger_arrow.setText(f"{offscreen_text} T ▶")
            self._trigger_arrow.setAnchor((1, 0))
            self._trigger_arrow.setPos(HORIZONTAL_DIVS, HALF_DIVS)

    def set_zero_marker(self, name: str, y_divs: float | None, color: str) -> None:
        """Marca, na borda esquerda, onde fica o 0 V do traço; None oculta.

        Fora da tela, a marca fica na borda e vira uma seta para cima ou baixo.
        """
        marker = self._zero_markers.get(name)
        if marker is None:
            marker = pg.TextItem(anchor=(0, 0.5))
            marker.setZValue(10)
            self.addItem(marker, ignoreBounds=True)
            self._zero_markers[name] = marker
        marker.setVisible(y_divs is not None)
        if y_divs is None:
            return
        arrow = "▲" if y_divs > HALF_DIVS else "▼" if y_divs < -HALF_DIVS else "▶"
        marker.setText(f"{name}{arrow}", color=color)
        # Nas bordas, ancora o texto para ele não ficar cortado pela metade.
        marker.setAnchor((0, 0 if y_divs >= HALF_DIVS else 1 if y_divs <= -HALF_DIVS else 0.5))
        marker.setPos(0, max(-HALF_DIVS, min(HALF_DIVS, y_divs)))

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
                shift = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                if abs(delta.x()) > abs(delta.y()) and not shift:
                    # Rolagem horizontal (touchpad, roda inclinável): desloca o traço,
                    # meia divisão por "clique".
                    self.dragged.emit(delta.x() / WHEEL_STEP * PAN_DIVS_PER_STEP)
                    return True
                # Com Shift, alguns sistemas convertem a rolagem em horizontal.
                self._wheel_accumulated += delta.y() or delta.x()
                steps = int(self._wheel_accumulated / WHEEL_STEP)
                if steps:
                    self._wheel_accumulated -= steps * WHEEL_STEP
                    scene_pos = self.mapToScene(event.position().toPoint())
                    x = self.getViewBox().mapSceneToView(scene_pos).x()
                    self.wheel_scrolled.emit(steps, event.modifiers(), x)
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
        # As leituras não podem bloquear o arrasto nem os cursores embaixo delas.
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        label.hide()
        return label

    def keyPressEvent(self, event) -> None:
        # Setas: uma divisão; com Shift, um décimo. "Direita" mostra o que vem depois.
        steps = {Qt.Key.Key_Left: 1, Qt.Key.Key_Right: -1}.get(event.key())
        if steps is None:
            super().keyPressEvent(event)
            return
        fine = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        self.dragged.emit(steps * (0.1 if fine else 1.0))

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
