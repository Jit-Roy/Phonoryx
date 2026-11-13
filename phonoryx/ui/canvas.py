"""Interactive PySide6 QGraphicsView node-based audio patchbay canvas."""

from __future__ import annotations

import json
from typing import Dict, Iterable, List, Optional, Tuple

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QMouseEvent,
    QUndoCommand,
    QUndoStack,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsProxyWidget,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from phonoryx.engine_bridge import DeviceInfo, NodeMeter, NodeType, ProcessInfo
from phonoryx.models.graph_model import (
    ConnectionModel,
    GroupModel,
    NodeCategory,
    NodeModel,
    ProjectModel,
)
from phonoryx.ui.icons import draw_node_type_icon
from phonoryx.ui.theme import (
    CANVAS_BG,
    CANVAS_GRID_MAJOR,
    CANVAS_GRID_MINOR,
    CATEGORY_BODY_COLORS,
    CATEGORY_COLORS,
    COMFY_BORDER,
    COMFY_STROKE,
    COMFY_TEXT_PRIMARY,
    COMFY_TEXT_SECONDARY,
    NODE_ACCENT_COLORS,
    NODE_BG,
    NODE_BORDER,
    NODE_SELECTED_BORDER,
    PORT_INPUT_COLOR,
    PORT_OUTPUT_COLOR,
    WIRE_ACTIVE_COLOR,
    WIRE_IDLE_COLOR,
    WIRE_SELECTED_COLOR,
)
from phonoryx.ui.widget_helpers import (
    EQ_BAND_LABELS,
    build_device_combo_entries,
    build_process_combo_entries,
    matching_process_combo_index,
    node_needs_device_selector,
    node_supports_mute,
    node_supports_volume,
    resolve_capture_pid_for_process,
    virtual_driver_missing_text,
)


NODE_WIDTH = 264.0
MIN_NODE_WIDTH = 220.0
RESIZE_HANDLE_SIZE = 15.0
HEADER_HEIGHT = 28.0
PORT_ROW_HEIGHT = 20.0
FOOTER_HEIGHT = 18.0
PORT_RADIUS = 6.0
NODE_DRAG_MIME_TYPE = "application/x-phonoryx-node"

_RESIZE_CURSORS = {
    "NW": Qt.CursorShape.SizeFDiagCursor,
    "SE": Qt.CursorShape.SizeFDiagCursor,
    "NE": Qt.CursorShape.SizeBDiagCursor,
    "SW": Qt.CursorShape.SizeBDiagCursor,
}


class _PopupSceneItem(QGraphicsRectItem):
    """Lightweight scene item representing the popup area in scene coordinates for hit-testing and zoom mapping."""

    def __init__(self, parent_proxy: Optional[QGraphicsItem], cmb: "_NoWheelComboBox") -> None:
        super().__init__(parent_proxy)
        self.cmb = cmb
        self.setPen(Qt.PenStyle.NoPen)
        self.setBrush(Qt.BrushStyle.NoBrush)
        self.setZValue(1005.0)
        w = max(float(self.cmb.width()) if self.cmb else 240.0, 240.0)
        h = 240.0
        self.setRect(0, 0, w, h)
        if self.cmb is not None:
            self.setPos(self.cmb.pos().x(), self.cmb.pos().y() + self.cmb.height())


class _FloatingComboPopup(QFrame):
    """High-precision, rock-solid floating popup overlay for node comboboxes.

    Bypasses Qt 6's buggy QGraphicsProxyWidget embedded popup mechanism which causes
    fatal C++ Access Violations (0xC0000005) in Qt6Widgets.dll, item selection drift,
    scrollbar drag blockage, and click penetration to the Mute checkbox underneath.
    """

    def __init__(self, combo: "_NoWheelComboBox") -> None:
        canvas = combo.node_item.canvas if combo.node_item else combo.window()
        parent_widget = canvas.viewport() if canvas and hasattr(canvas, "viewport") else None
        super().__init__(
            parent_widget,
            Qt.WindowType.FramelessWindowHint,
        )
        self.combo = combo
        self.setObjectName("FloatingComboPopup")
        self.setStyleSheet("""
            QFrame#FloatingComboPopup {
                background-color: #1B1B1B;
                border: 1px solid #424242;
                border-radius: 4px;
            }
            QLineEdit {
                background-color: #161616;
                color: #EDEDED;
                border: 1px solid #424242;
                border-radius: 3px;
                padding: 2px 6px;
                font-size: 11px;
            }
            QLineEdit:focus {
                border-color: #8A8A8A;
            }
            QListWidget, QListWidget QWidget {
                background-color: #1B1B1B;
                border: none;
                color: #EDEDED;
                outline: none;
            }
            QListWidget::item {
                height: 24px;
                padding: 0px 6px;
                border-radius: 3px;
                margin: 0px;
                color: #EDEDED;
                background: transparent;
            }
            QListWidget::item:hover {
                background-color: #252525;
                color: #FFFFFF;
            }
            QListWidget::item:selected {
                background-color: #383838;
                color: #FFFFFF;
            }
            QListWidget::item:selected:hover {
                background-color: #404040;
                color: #FFFFFF;
            }
            QScrollBar:vertical {
                background: #141414;
                width: 6px;
                margin: 0px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #383838;
                min-height: 20px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: #606060;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)

        if combo.count() > 7:
            self.search = QLineEdit(self)
            self.search.setPlaceholderText("Filter...")
            self.search.textChanged.connect(self._on_search_text_changed)
            layout.addWidget(self.search)
        else:
            self.search = None

        self.list_widget = QListWidget(self)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setSpacing(2)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setMaximumHeight(175)
        layout.addWidget(self.list_widget)

        for i in range(combo.count()):
            it = QListWidgetItem(combo.itemText(i), self.list_widget)
            it.setData(Qt.ItemDataRole.UserRole, combo.itemData(i))
            it.setSizeHint(QSize(0, 24))

        cur = combo.currentIndex()
        if 0 <= cur < self.list_widget.count():
            self.list_widget.setCurrentRow(cur)
            self.list_widget.scrollToItem(self.list_widget.item(cur))

        self.list_widget.itemClicked.connect(self._on_item_clicked)

    def repopulate(self) -> None:
        self.list_widget.clear()
        for i in range(self.combo.count()):
            it = QListWidgetItem(self.combo.itemText(i), self.list_widget)
            it.setData(Qt.ItemDataRole.UserRole, self.combo.itemData(i))
            it.setSizeHint(QSize(0, 24))

        cur = self.combo.currentIndex()
        if 0 <= cur < self.list_widget.count():
            self.list_widget.setCurrentRow(cur)
            self.list_widget.scrollToItem(self.list_widget.item(cur))

        if self.search is not None:
            self.search.clear()

    def handle_click_at_global(self, global_pos: QPoint) -> bool:
        local_pt = self.mapFromGlobal(global_pos)
        if not self.rect().contains(local_pt):
            return False
        if self.search is not None:
            search_pt = self.search.mapFromGlobal(global_pos)
            if self.search.rect().contains(search_pt):
                self.search.setFocus()
                return True
        vp = self.list_widget.viewport()
        vp_pt = vp.mapFromGlobal(global_pos)
        idx = self.list_widget.indexAt(vp_pt)
        if idx.isValid():
            it = self.list_widget.item(idx.row())
            if it is not None and not it.isHidden():
                self._on_item_clicked(it)
                return True
        return True

    def _on_search_text_changed(self, text: str) -> None:
        t = text.lower().strip()
        for i in range(self.list_widget.count()):
            it = self.list_widget.item(i)
            it.setHidden(t not in it.text().lower())

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        row = self.list_widget.row(item)
        self.combo.setCurrentIndex(row)
        self.combo.activated.emit(row)
        self.combo.hidePopup()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.combo.hidePopup()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            cur_item = self.list_widget.currentItem()
            if cur_item is not None and not cur_item.isHidden():
                self._on_item_clicked(cur_item)
                return
        if event.key() == Qt.Key.Key_Down and self.search is not None and self.search.hasFocus():
            self.list_widget.setFocus()
            if self.list_widget.currentRow() < 0:
                self.list_widget.setCurrentRow(0)
            return
        if event.key() == Qt.Key.Key_Up and self.list_widget.hasFocus() and self.list_widget.currentRow() == 0 and self.search is not None:
            self.search.setFocus()
            return
        super().keyPressEvent(event)

    def wheelEvent(self, event) -> None:
        sb = self.list_widget.verticalScrollBar()
        if sb is not None:
            dy = event.angleDelta().y()
            steps = -int((dy / 120.0) * 3) if dy != 0 else 0
            sb.setValue(sb.value() + steps)
        event.accept()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)

    def show_for_combo(self) -> None:
        self.repopulate()
        canvas = self.combo.node_item.canvas if self.combo.node_item else None
        if canvas is not None and hasattr(canvas, "mapFromScene"):
            proxy = self.combo.graphicsProxyWidget()
            if proxy is None and hasattr(self.combo.node_item, "_proxy"):
                proxy = self.combo.node_item._proxy
            if proxy is not None:
                root = getattr(self.combo.node_item, "_widget_root", None)
                if root is not None:
                    p_tl = self.combo.mapTo(root, QPoint(0, 0))
                    p_bl = self.combo.mapTo(root, QPoint(0, self.combo.height()))
                    p_tr = self.combo.mapTo(root, QPoint(self.combo.width(), 0))
                else:
                    p_tl = QPoint(self.combo.pos().x(), self.combo.pos().y())
                    p_bl = QPoint(self.combo.pos().x(), self.combo.pos().y() + self.combo.height())
                    p_tr = QPoint(self.combo.pos().x() + self.combo.width(), self.combo.pos().y())
                s_tl = proxy.mapToScene(QPointF(p_tl))
                s_bl = proxy.mapToScene(QPointF(p_bl))
                s_tr = proxy.mapToScene(QPointF(p_tr))
                v_tl = canvas.mapFromScene(s_tl)
                v_bl = canvas.mapFromScene(s_bl)
                v_tr = canvas.mapFromScene(s_tr)
                computed_w = max(int(round(abs(v_tr.x() - v_tl.x()))), 60)
            else:
                v_bl = canvas.viewport().mapFromGlobal(self.combo.mapToGlobal(QPoint(0, self.combo.height()))) if hasattr(canvas, "viewport") else QPoint(0, self.combo.height())
                v_tl = canvas.viewport().mapFromGlobal(self.combo.mapToGlobal(QPoint(0, 0))) if hasattr(canvas, "viewport") else QPoint(0, 0)
                v_tr = canvas.viewport().mapFromGlobal(self.combo.mapToGlobal(QPoint(self.combo.width(), 0))) if hasattr(canvas, "viewport") else QPoint(self.combo.width(), 0)
                computed_w = max(int(round(abs(v_tr.x() - v_tl.x()))), self.combo.width(), 60)
        else:
            v_bl = QPoint(0, 0)
            v_tl = QPoint(0, 0)
            computed_w = max(self.combo.width(), 60)

        vp_rect = canvas.viewport().rect() if canvas and hasattr(canvas, "viewport") else QRect(0, 0, 1920, 1080)
        cnt = self.combo.count()
        item_h = 24
        target_h = min(cnt * (item_h + 2) + (28 if self.search else 0) + 8, 180)
        target_h = max(target_h, 36)

        # Check if bottom overflows viewport
        if v_bl.y() + target_h > vp_rect.bottom() - 10:
            target_y = max(5, v_tl.y() - target_h)
        else:
            target_y = v_bl.y()

        target_x = v_bl.x()

        self.setGeometry(target_x, target_y, computed_w, target_h)
        self.raise_()
        self.show()
        if self.search:
            self.search.setFocus()
        else:
            self.list_widget.setFocus()


class _NoWheelComboBox(QComboBox):
    """Combo box that never changes selection on wheel hover, isolates popup scrolling,
    and opens a clean floating popup overlay instead of scene-embedded QGraphicsProxyWidget popups.
    """

    def __init__(self, parent=None, node_item: Optional["NodeItem"] = None) -> None:
        super().__init__(parent)
        self.node_item: Optional["NodeItem"] = node_item
        self._floating_popup: Optional[_FloatingComboPopup] = None
        self._popup_scene_item: Optional[_PopupSceneItem] = None
        self._prev_node_z: Optional[float] = None
        self.setMaxVisibleItems(7)
        super().view().setMaximumHeight(180)

    def view(self) -> QAbstractItemView:
        if self._floating_popup is not None:
            return self._floating_popup.list_widget
        return super().view()

    def _resolve_node_item(self) -> Optional["NodeItem"]:
        if self.node_item is not None:
            return self.node_item
        try:
            win = self.window()
            if win is not None and win.graphicsProxyWidget() is not None:
                parent_it = win.graphicsProxyWidget().parentItem()
                if isinstance(parent_it, NodeItem):
                    self.node_item = parent_it
                    return parent_it
        except Exception:
            pass
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.showPopup()
            event.accept()
            return
        super().mousePressEvent(event)

    def showPopup(self) -> None:  # noqa: N802
        if self.count() == 0:
            return
        if self._floating_popup is not None and self._floating_popup.isVisible():
            self.hidePopup()
            return
        if self.node_item is not None:
            self._prev_node_z = self.node_item.zValue()
            self.node_item.setZValue(1000.0)
            proxy = getattr(self.node_item, "_proxy", None)
            if proxy is not None:
                self._popup_scene_item = _PopupSceneItem(proxy, self)

        if self._floating_popup is None:
            self._floating_popup = _FloatingComboPopup(self)
        self._floating_popup.show_for_combo()

    def hidePopup(self) -> None:  # noqa: N802
        if self.node_item is not None and self._prev_node_z is not None:
            try:
                self.node_item.setZValue(self._prev_node_z)
            except RuntimeError:
                pass
            self._prev_node_z = None
        if self._popup_scene_item is not None:
            scene = self._popup_scene_item.scene()
            if scene is not None:
                try:
                    scene.removeItem(self._popup_scene_item)
                except Exception:
                    pass
            self._popup_scene_item = None
        if self._floating_popup is not None:
            try:
                self._floating_popup.hide()
            except Exception:
                pass

    def wheelEvent(self, event) -> None:  # noqa: N802
        event.accept()


class _NoWheelSlider(QSlider):
    """Horizontal slider that passes wheel scrolling through to zoom the canvas."""

    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


def _find_popup_graphics_item(open_cmb: Optional[QComboBox]) -> Optional[QGraphicsItem]:
    if open_cmb is None:
        return None
    it = getattr(open_cmb, "_popup_scene_item", None)
    if it is not None:
        try:
            if it.scene() is not None:
                return it
        except Exception:
            pass
    node = getattr(open_cmb, "node_item", None)
    if node is not None and hasattr(node, "_proxy") and node._proxy is not None:
        return node._proxy
    return None


def _is_scene_pos_on_popup(open_cmb: Optional[QComboBox], scene_pt: QPointF) -> bool:
    if open_cmb is None:
        return False
    pw = _find_popup_graphics_item(open_cmb)
    if pw is not None:
        try:
            return pw.sceneBoundingRect().contains(scene_pt)
        except Exception:
            pass
    return False


def _is_view_pos_on_popup(canvas: QGraphicsView, open_cmb: Optional[QComboBox], view_pos: Optional[QPoint]) -> bool:
    if canvas is None or open_cmb is None or view_pos is None:
        return False
    try:
        p = getattr(open_cmb, "_floating_popup", None)
        if p is not None and p.isVisible():
            if p.geometry().contains(view_pos):
                return True
            gpos = canvas.viewport().mapToGlobal(view_pos)
            if p.rect().contains(p.mapFromGlobal(gpos)):
                return True
        scene_pt = canvas.mapToScene(view_pos)
        return _is_scene_pos_on_popup(open_cmb, scene_pt)
    except Exception:
        return False


def _handle_popup_click(canvas: QGraphicsView, open_cmb: Optional[QComboBox], view_pos: Optional[QPoint]) -> bool:
    if canvas is None or open_cmb is None or view_pos is None:
        return False
    try:
        p = getattr(open_cmb, "_floating_popup", None)
        if p is not None and p.isVisible():
            gpos = canvas.viewport().mapToGlobal(view_pos)
            if p.rect().contains(p.mapFromGlobal(gpos)) or p.geometry().contains(view_pos):
                return p.handle_click_at_global(gpos)
        pw = _find_popup_graphics_item(open_cmb)
        if pw is not None:
            scene_pt = canvas.mapToScene(view_pos)
            if pw.sceneBoundingRect().contains(scene_pt):
                pt_in_pw = pw.mapFromScene(scene_pt)
                v = open_cmb.view()
                if v is not None:
                    local_pt = QPoint(int(pt_in_pw.x() - v.x()), int(pt_in_pw.y() - v.y()))
                    idx = v.indexAt(local_pt)
                    if idx.isValid():
                        row = idx.row()
                        open_cmb.setCurrentIndex(row)
                        open_cmb.activated.emit(row)
                        open_cmb.hidePopup()
                        return True
    except Exception:
        pass
    return False


def _is_point_inside_widget(widget: Optional[QWidget], global_pos: Optional[QPoint]) -> bool:
    if widget is None or global_pos is None:
        return False
    try:
        if not widget.isVisible():
            return False
        local_pos = widget.mapFromGlobal(global_pos)
        return widget.rect().contains(local_pos)
    except Exception:
        return False


def _is_view_pos_on_combo(canvas: QGraphicsView, open_cmb: Optional[QComboBox], view_pos: Optional[QPoint]) -> bool:
    if open_cmb is None or canvas is None or view_pos is None:
        return False
    try:
        scene_pt = canvas.mapToScene(view_pos)
        pw = open_cmb.graphicsProxyWidget()
        if pw is None:
            node = getattr(open_cmb, "node_item", None)
            if node is not None and hasattr(node, "_proxy") and node._proxy is not None:
                pw = node._proxy
        if pw is not None:
            local_pt = pw.mapFromScene(scene_pt).toPoint()
            if open_cmb.geometry().contains(local_pt):
                return True
            w = pw.widget().childAt(local_pt)
            while w is not None:
                if w is open_cmb:
                    return True
                if w is pw.widget():
                    break
                w = w.parentWidget()
    except Exception:
        pass
    return False


def _is_global_pos_on_combo(canvas: QGraphicsView, open_cmb: Optional[QComboBox], global_pos: Optional[QPoint]) -> bool:
    if canvas is None or open_cmb is None or global_pos is None:
        return False
    try:
        vp_pos = canvas.viewport().mapFromGlobal(global_pos)
        return _is_view_pos_on_combo(canvas, open_cmb, vp_pos)
    except Exception:
        return False


def _is_global_pos_on_popup(canvas: QGraphicsView, open_cmb: Optional[QComboBox], global_pos: Optional[QPoint]) -> bool:
    if open_cmb is None or global_pos is None:
        return False
    try:
        p = getattr(open_cmb, "_floating_popup", None)
        if p is not None and p.isVisible():
            return p.rect().contains(p.mapFromGlobal(global_pos))
    except Exception:
        pass
    return False


class PortItem(QGraphicsObject):
    """Clickable input or output port circle on a NodeItem."""

    def __init__(
        self,
        node_item: "NodeItem",
        port_index: int,
        is_output: bool,
        label: str,
    ) -> None:
        super().__init__(node_item)
        self.node_item = node_item
        self.port_index = port_index
        self.is_output = is_output
        self.label = label
        self._hovered = False
        self.setAcceptHoverEvents(True)
        self.setZValue(3)

    def boundingRect(self) -> QRectF:
        return QRectF(-12.0, -12.0, 24.0, 24.0)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        color = QColor(PORT_OUTPUT_COLOR if self.is_output else PORT_INPUT_COLOR)
        if self._hovered:
            color = color.lighter(135)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#0B0E14"), 1.5))
        painter.setBrush(QBrush(color))
        painter.drawEllipse(QPointF(0, 0), PORT_RADIUS, PORT_RADIUS)

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def center_scene_pos(self) -> QPointF:
        return self.scenePos()


class NodeItem(QGraphicsObject):
    """ComfyUI-style visual card representing one NodeModel with inline argument widgets."""

    def __init__(self, model: NodeModel, canvas: "NodeCanvas") -> None:
        super().__init__()
        self.model = model
        self.canvas = canvas
        self.input_ports: List[PortItem] = []
        self.output_ports: List[PortItem] = []
        self._peak_l = 0.0
        self._peak_r = 0.0
        self._drag_start_pos: Optional[QPointF] = None
        self._collapsed = False
        self._updating_widgets = False
        self._widget_height = 0.0
        self._resizing_dir: Optional[str] = None
        self._hovered_resize_dir: Optional[str] = None
        self._resize_start_scene: Optional[QPointF] = None
        self._resize_start_rect: Optional[Tuple[float, float, float, float]] = None

        # Inline widget handles (populated dynamically based on node type)
        self._proxy: Optional[QGraphicsProxyWidget] = None
        self._widget_root: Optional[QWidget] = None
        self.cmb_device: Optional[QComboBox] = None
        self.lbl_warn: Optional[QLabel] = None
        self.cmb_process: Optional[QComboBox] = None
        self.edit_file: Optional[QLineEdit] = None
        self.btn_browse: Optional[QPushButton] = None
        self.slider_vol: Optional[QSlider] = None
        self.lbl_vol_val: Optional[QLabel] = None
        self.slider_gain: Optional[QSlider] = None
        self.lbl_gain_val: Optional[QLabel] = None
        self.chk_mute: Optional[QCheckBox] = None
        self.cmb_map_l: Optional[QComboBox] = None
        self.cmb_map_r: Optional[QComboBox] = None
        self.eq_sliders: List[QSlider] = []
        self.lbl_eq_vals: List[QLabel] = []

        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setPos(model.x, model.y)
        self.setZValue(2)
        self._build_ports()
        self._build_inline_widgets()
        self._sync_ports_and_proxy_geometry()
        self.sync_from_model()

    def _port_area_height(self) -> float:
        spec = self.model.spec()
        rows = max(len(spec.input_ports), len(spec.output_ports), 1)
        return 6.0 + rows * PORT_ROW_HEIGHT

    def compute_min_size(self) -> Tuple[float, float]:
        """Compute minimum non-clipping node content size (ComfyUI computeSize pattern)."""
        min_w = (
            240.0
            if self.model.node_type
            in (NodeType.EQUALIZER, NodeType.FILE_RECORDING, NodeType.CHANNEL_MAP)
            else MIN_NODE_WIDTH
        )
        port_h = self._port_area_height()
        widget_h = (self._widget_height + 6.0) if self._widget_height > 0 else 0.0
        return (min_w, HEADER_HEIGHT + port_h + widget_h + FOOTER_HEIGHT)

    def _node_width(self) -> float:
        if self.model.width is None:
            return NODE_WIDTH
        min_w, _ = self.compute_min_size()
        return max(min_w, float(self.model.width))

    def _compute_height(self) -> float:
        port_h = self._port_area_height()
        if self._collapsed:
            return HEADER_HEIGHT + port_h + FOOTER_HEIGHT
        _, min_h = self.compute_min_size()
        if self.model.height is None:
            return min_h
        return max(min_h, float(self.model.height))

    def _sync_ports_and_proxy_geometry(self) -> None:
        w = self._node_width()
        for p in self.output_ports:
            p.setPos(w, p.y())
        self._layout_inline_proxy()

    def apply_geometry(
        self,
        x: float,
        y: float,
        width: Optional[float],
        height: Optional[float],
        *,
        emit_modified: bool = True,
    ) -> None:
        """Apply node bounds & position, keeping ports, widgets, wires and groups aligned."""
        self.prepareGeometryChange()
        min_w, min_h = self.compute_min_size()
        self.model.width = max(min_w, float(width)) if width is not None else None
        self.model.height = max(min_h, float(height)) if height is not None else None
        if abs(self.pos().x() - x) > 1e-6 or abs(self.pos().y() - y) > 1e-6:
            self.setPos(x, y)
        self._sync_ports_and_proxy_geometry()
        self.canvas.update_connections_for_node(self.model.id)
        self.canvas.update_groups_geometry()
        self.update()
        if emit_modified:
            self.canvas.workspace_modified.emit()

    def _find_resize_direction(self, pos: QPointF) -> Optional[str]:
        """Return SE/SW/NE/NW resize corner under ``pos`` (ComfyUI 15px handle convention)."""
        if self._collapsed:
            return None
        w = self._node_width()
        h = self._compute_height()
        r = RESIZE_HANDLE_SIZE
        px, py = pos.x(), pos.y()
        if w - r <= px <= w + 2.0 and h - r <= py <= h + 2.0:
            return "SE"
        if -2.0 <= px <= r and h - r <= py <= h + 2.0:
            return "SW"
        if w - r <= px <= w + 2.0 and -2.0 <= py <= r:
            return "NE"
        # Top-left corner grip stops before collapse chevron button (x: 8..26)
        if -2.0 <= px <= 8.0 and -2.0 <= py <= 8.0:
            return "NW"
        return None

    def _build_ports(self) -> None:
        spec = self.model.spec()
        y0 = HEADER_HEIGHT + 12.0
        for idx, label in enumerate(spec.input_ports):
            p = PortItem(self, idx, is_output=False, label=label)
            p.setPos(0.0, y0 + idx * PORT_ROW_HEIGHT)
            self.input_ports.append(p)
        for idx, label in enumerate(spec.output_ports):
            p = PortItem(self, idx, is_output=True, label=label)
            p.setPos(NODE_WIDTH, y0 + idx * PORT_ROW_HEIGHT)
            self.output_ports.append(p)

    def _build_inline_widgets(self) -> None:
        root = QWidget()
        root.setObjectName("NodeInlineWidgetRoot")
        root.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(4)

        nt = self.model.node_type
        has_widgets = False

        if node_needs_device_selector(nt):
            has_widgets = True
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            lbl = QLabel("Device")
            lbl.setFixedWidth(46)
            self.cmb_device = _NoWheelComboBox(root, node_item=self)
            self.cmb_device.setObjectName(f"Node_{self.model.id}_cmb_device")
            self.cmb_device.currentIndexChanged.connect(self._on_device_changed)
            row.addWidget(lbl)
            row.addWidget(self.cmb_device, 1)
            lay.addLayout(row)

            self.lbl_warn = QLabel(root)
            self.lbl_warn.setWordWrap(True)
            self.lbl_warn.setStyleSheet("color: #FCBB00; font-size: 10px;")
            self.lbl_warn.hide()
            lay.addWidget(self.lbl_warn)

        if nt == NodeType.APP_CAPTURE:
            has_widgets = True
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            lbl = QLabel("App")
            lbl.setFixedWidth(30)
            self.cmb_process = _NoWheelComboBox(root, node_item=self)
            self.cmb_process.setObjectName(f"Node_{self.model.id}_cmb_process")
            self.cmb_process.currentIndexChanged.connect(self._on_process_changed)
            row.addWidget(lbl)
            row.addWidget(self.cmb_process, 1)
            lay.addLayout(row)

        if nt == NodeType.FILE_RECORDING:
            has_widgets = True
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            self.edit_file = QLineEdit(root)
            self.edit_file.setPlaceholderText("recording.wav...")
            self.edit_file.editingFinished.connect(self._on_file_changed)
            self.btn_browse = QPushButton("Browse", root)
            self.btn_browse.clicked.connect(self._browse_wav)
            row.addWidget(self.edit_file, 1)
            row.addWidget(self.btn_browse)
            lay.addLayout(row)

        if node_supports_volume(nt):
            has_widgets = True
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            lbl = QLabel("Volume")
            lbl.setFixedWidth(44)
            self.slider_vol = _NoWheelSlider(Qt.Orientation.Horizontal, root)
            self.slider_vol.setRange(0, 200)
            self.slider_vol.valueChanged.connect(self._on_volume_slider)
            self.lbl_vol_val = QLabel("100%", root)
            self.lbl_vol_val.setFixedWidth(34)
            self.lbl_vol_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(lbl)
            row.addWidget(self.slider_vol, 1)
            row.addWidget(self.lbl_vol_val)
            lay.addLayout(row)

        if nt == NodeType.GAIN:
            has_widgets = True
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            lbl = QLabel("Gain")
            lbl.setFixedWidth(34)
            self.slider_gain = _NoWheelSlider(Qt.Orientation.Horizontal, root)
            self.slider_gain.setRange(-300, 300)
            self.slider_gain.valueChanged.connect(self._on_gain_slider)
            self.lbl_gain_val = QLabel("+0.0dB", root)
            self.lbl_gain_val.setFixedWidth(46)
            self.lbl_gain_val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(lbl)
            row.addWidget(self.slider_gain, 1)
            row.addWidget(self.lbl_gain_val)
            lay.addLayout(row)

        if nt == NodeType.CHANNEL_MAP:
            has_widgets = True
            for title, attr in (("Left <-", "cmb_map_l"), ("Right <-", "cmb_map_r")):
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(6)
                lbl = QLabel(title)
                lbl.setFixedWidth(48)
                cmb = _NoWheelComboBox(root, node_item=self)
                cmb.addItem("0 (Left)", 0)
                cmb.addItem("1 (Right)", 1)
                cmb.currentIndexChanged.connect(self._on_channel_map_changed)
                row.addWidget(lbl)
                row.addWidget(cmb, 1)
                lay.addLayout(row)
                setattr(self, attr, cmb)

        if nt == NodeType.EQUALIZER:
            has_widgets = True
            eq_hdr = QLabel("Band Gains (-12 to +12 dB)", root)
            eq_hdr.setStyleSheet("color: #999999; font-size: 10px;")
            lay.addWidget(eq_hdr)
            for b, freq in enumerate(EQ_BAND_LABELS):
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(6)
                lbl = QLabel(freq, root)
                lbl.setFixedWidth(64)
                sl = _NoWheelSlider(Qt.Orientation.Horizontal, root)
                sl.setRange(-120, 120)
                sl.valueChanged.connect(lambda val, band=b: self._on_eq_changed(band, val))
                v_lbl = QLabel("+0.0 dB", root)
                v_lbl.setFixedWidth(48)
                v_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                row.addWidget(lbl)
                row.addWidget(sl, 1)
                row.addWidget(v_lbl)
                lay.addLayout(row)
                self.eq_sliders.append(sl)
                self.lbl_eq_vals.append(v_lbl)

        if node_supports_mute(nt):
            has_widgets = True
            self.chk_mute = QCheckBox("Muted" if nt == NodeType.MUTE else "Mute", root)
            self.chk_mute.setObjectName(f"Node_{self.model.id}_chk_mute")
            self.chk_mute.stateChanged.connect(self._on_mute_changed)
            lay.addWidget(self.chk_mute)

        if not has_widgets:
            root.deleteLater()
            self._widget_root = None
            self._proxy = None
            self._widget_height = 0.0
            return

        self._widget_root = root
        self._proxy = QGraphicsProxyWidget(self)
        self._proxy.setWidget(root)
        self._proxy.setZValue(3)
        self._layout_inline_proxy()

    def _layout_inline_proxy(self) -> None:
        if self._proxy is None or self._widget_root is None:
            self._widget_height = 0.0
            return
        w = self._node_width()
        self._widget_root.setFixedWidth(int(w - 16))
        if self._widget_root.layout() is not None:
            self._widget_root.layout().activate()
        self._widget_root.adjustSize()
        h = float(max(22, self._widget_root.sizeHint().height()))
        self._widget_height = h
        widgets_top = HEADER_HEIGHT + self._port_area_height()
        self._proxy.setPos(8.0, widgets_top)
        self._proxy.resize(w - 16.0, h)
        self._proxy.setVisible(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        if self._collapsed == collapsed:
            return
        self.prepareGeometryChange()
        self._collapsed = collapsed
        if self._proxy is not None:
            self._proxy.setVisible(not collapsed)
        self.canvas.update_connections_for_node(self.model.id)
        self.canvas.update_groups_geometry()
        self.update()

    def refresh_catalog(self, devices: List[DeviceInfo], processes: List[ProcessInfo]) -> None:
        self._updating_widgets = True
        try:
            if self.cmb_device is not None:
                is_open = (
                    self.cmb_device.view() is not None
                    and self.cmb_device.view().isVisible()
                )
                if not is_open:
                    new_dev_entries = build_device_combo_entries(self.model, devices)
                    cur_dev_entries = [
                        (self.cmb_device.itemText(i), self.cmb_device.itemData(i))
                        for i in range(self.cmb_device.count())
                    ]
                    if new_dev_entries != cur_dev_entries:
                        self.cmb_device.clear()
                        for label, data in new_dev_entries:
                            self.cmb_device.addItem(label, data)
                        idx = 0
                        for i in range(self.cmb_device.count()):
                            data = self.cmb_device.itemData(i)
                            if data and data[0] == self.model.device_id:
                                idx = i
                                break
                        self.cmb_device.setCurrentIndex(idx)

                if self.lbl_warn is not None:
                    if not self.model.device_available and self.model.device_id:
                        self.lbl_warn.setText(
                            f"Offline: {self.model.device_name or self.model.device_id}"
                        )
                        self.lbl_warn.show()
                    else:
                        warn = virtual_driver_missing_text(self.model.node_type, devices)
                        if warn:
                            self.lbl_warn.setText("Install VirtualDrivers/Virtual-Audio-Driver")
                            self.lbl_warn.setToolTip(warn)
                            self.lbl_warn.show()
                        else:
                            self.lbl_warn.hide()

            if self.cmb_process is not None:
                is_open = (
                    self.cmb_process.view() is not None
                    and self.cmb_process.view().isVisible()
                )
                if not is_open:
                    new_proc_entries = build_process_combo_entries(self.model, processes)
                    cur_proc_entries = [
                        (self.cmb_process.itemText(i), self.cmb_process.itemData(i))
                        for i in range(self.cmb_process.count())
                    ]
                    if new_proc_entries != cur_proc_entries:
                        self.cmb_process.clear()
                        for label, data in new_proc_entries:
                            self.cmb_process.addItem(label, data)
                        self.cmb_process.setCurrentIndex(
                            matching_process_combo_index(self.cmb_process, self.model)
                        )
                cap_pid = self.model.capture_pid
                if cap_pid and cap_pid != self.model.pid:
                    child_proc = next((p for p in processes if p.pid == cap_pid), None)
                    child_label = (
                        f"{child_proc.exe_name} (PID {cap_pid})"
                        if child_proc is not None
                        else f"PID {cap_pid}"
                    )
                    self.cmb_process.setToolTip(
                        f"Host: {self.model.process_name or 'PID'} (PID {self.model.pid}) -> Capturing child audio {child_label}"
                    )
                else:
                    self.cmb_process.setToolTip("")
        finally:
            self._updating_widgets = False

        self.prepareGeometryChange()
        self._layout_inline_proxy()
        self.canvas.update_connections_for_node(self.model.id)
        self.canvas.update_groups_geometry()
        self.update()

    def sync_from_model(self) -> None:
        self._updating_widgets = True
        try:
            if self.edit_file is not None:
                self.edit_file.setText(self.model.file_path)
            if self.slider_vol is not None and self.lbl_vol_val is not None:
                pct = int(round(self.model.volume * 100))
                self.slider_vol.setValue(pct)
                self.lbl_vol_val.setText(f"{pct}%")
            if self.slider_gain is not None and self.lbl_gain_val is not None:
                self.slider_gain.setValue(int(round(self.model.gain_db * 10)))
                self.lbl_gain_val.setText(f"{self.model.gain_db:+.1f}dB")
            if self.chk_mute is not None:
                self.chk_mute.setChecked(self.model.muted)
            ch_l = self.model.channel_map[0] if len(self.model.channel_map) > 0 else 0
            ch_r = self.model.channel_map[1] if len(self.model.channel_map) > 1 else 1
            if self.cmb_map_l is not None:
                self.cmb_map_l.setCurrentIndex(0 if ch_l == 0 else 1)
            if self.cmb_map_r is not None:
                self.cmb_map_r.setCurrentIndex(0 if ch_r == 0 else 1)
            for idx, sl in enumerate(self.eq_sliders):
                val_db = self.model.eq_gains_db[idx] if idx < len(self.model.eq_gains_db) else 0.0
                sl.setValue(int(round(val_db * 10)))
                self.lbl_eq_vals[idx].setText(f"{val_db:+.1f} dB")
        finally:
            self._updating_widgets = False
        self.refresh_catalog(self.canvas.devices, self.canvas.processes)

    def _on_device_changed(self) -> None:
        if self._updating_widgets or self.cmb_device is None:
            return
        data = self.cmb_device.currentData()
        if data:
            self.model.device_id = data[0]
            self.model.device_name = data[1]
            self.model.device_available = True
            self.canvas.graph_changed.emit()
            self.update()

    def _on_process_changed(self) -> None:
        if self._updating_widgets or self.cmb_process is None:
            return
        data = self.cmb_process.currentData()
        if data:
            self.model.pid = int(data[0])
            self.model.process_name = str(data[1])
            self.model.capture_pid = resolve_capture_pid_for_process(
                self.model.pid, self.canvas.processes
            )
            self.canvas.graph_changed.emit()
            self.update()

    def _browse_wav(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self.canvas,
            "Select Output WAV Recording Path",
            self.model.file_path or "phonoryx_recording.wav",
            "WAV Files (*.wav)",
        )
        if path and self.edit_file is not None:
            self.edit_file.setText(path)
            self._on_file_changed()

    def _on_file_changed(self) -> None:
        if self._updating_widgets or self.edit_file is None:
            return
        new_path = self.edit_file.text().strip()
        if new_path != self.model.file_path:
            self.model.file_path = new_path
            self.canvas.graph_changed.emit()
            self.update()

    def _on_volume_slider(self, val: int) -> None:
        if self._updating_widgets:
            return
        self.model.volume = val / 100.0
        if self.lbl_vol_val is not None:
            self.lbl_vol_val.setText(f"{val}%")
        self.canvas.float_param_changed.emit(self.model.id, "volume", self.model.volume)
        self.canvas.workspace_modified.emit()

    def _on_gain_slider(self, val: int) -> None:
        if self._updating_widgets:
            return
        self.model.gain_db = val / 10.0
        if self.lbl_gain_val is not None:
            self.lbl_gain_val.setText(f"{self.model.gain_db:+.1f}dB")
        self.canvas.float_param_changed.emit(self.model.id, "gain_db", self.model.gain_db)
        self.canvas.workspace_modified.emit()

    def _on_mute_changed(self) -> None:
        if self._updating_widgets or self.chk_mute is None:
            return
        self.model.muted = self.chk_mute.isChecked()
        self.canvas.int_param_changed.emit(self.model.id, "muted", 1 if self.model.muted else 0)
        self.canvas.workspace_modified.emit()
        self.update()

    def _on_channel_map_changed(self) -> None:
        if self._updating_widgets or self.cmb_map_l is None or self.cmb_map_r is None:
            return
        left_src = int(self.cmb_map_l.currentData() or 0)
        right_src = int(self.cmb_map_r.currentData() or 0)
        self.model.channel_map = [left_src, right_src]
        self.canvas.graph_changed.emit()

    def _on_eq_changed(self, band: int, tenths_db: int) -> None:
        if self._updating_widgets:
            return
        db = tenths_db / 10.0
        while len(self.model.eq_gains_db) < 5:
            self.model.eq_gains_db.append(0.0)
        self.model.eq_gains_db[band] = db
        if band < len(self.lbl_eq_vals):
            self.lbl_eq_vals[band].setText(f"{db:+.1f} dB")
        self.canvas.float_param_changed.emit(self.model.id, f"eq_band_{band}", db)
        self.canvas.workspace_modified.emit()

    def set_meter(self, meter: Optional[NodeMeter]) -> None:
        if meter is None:
            self._peak_l = 0.0
            self._peak_r = 0.0
        else:
            self._peak_l = min(1.0, max(0.0, meter.peak_left))
            self._peak_r = min(1.0, max(0.0, meter.peak_right))
        self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(-2, -2, self._node_width() + 4, self._compute_height() + 4)

    def hoverMoveEvent(self, event) -> None:  # noqa: N802
        rdir = self._find_resize_direction(event.pos())
        if rdir != self._hovered_resize_dir:
            self._hovered_resize_dir = rdir
            if rdir is not None:
                self.setCursor(_RESIZE_CURSORS[rdir])
            else:
                self.unsetCursor()
            self.update()
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802
        if self._hovered_resize_dir is not None:
            self._hovered_resize_dir = None
            self.unsetCursor()
            self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()
            rdir = self._find_resize_direction(pos)
            if rdir is not None:
                self._resizing_dir = rdir
                self._resize_start_scene = event.scenePos()
                self._resize_start_rect = (
                    float(self.pos().x()),
                    float(self.pos().y()),
                    self._node_width(),
                    self._compute_height(),
                )
                self.canvas._move_snapshot.clear()
                if not self.isSelected() and self.scene() is not None:
                    self.scene().clearSelection()
                    self.setSelected(True)
                self.setCursor(_RESIZE_CURSORS[rdir])
                event.accept()
                return
            if 0.0 <= pos.y() <= HEADER_HEIGHT and 0.0 <= pos.x() <= 26.0:
                self.set_collapsed(not self._collapsed)
                event.accept()
                return
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.setSelected(not self.isSelected())
                event.accept()
                return
        if self.canvas is not None and hasattr(self.canvas, "_top_node_z"):
            self.canvas._top_node_z += 0.001
            self.setZValue(self.canvas._top_node_z)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self._resizing_dir is not None
            and self._resize_start_scene is not None
            and self._resize_start_rect is not None
        ):
            dx = event.scenePos().x() - self._resize_start_scene.x()
            dy = event.scenePos().y() - self._resize_start_scene.y()
            rx, ry, rw, rh = self._resize_start_rect
            nx, ny, nw, nh = rx, ry, rw, rh
            d = self._resizing_dir
            if d == "SE":
                nw = rw + dx
                nh = rh + dy
            elif d == "NE":
                ny = ry + dy
                nw = rw + dx
                nh = rh - dy
            elif d == "SW":
                nx = rx + dx
                nw = rw - dx
                nh = rh + dy
            elif d == "NW":
                nx = rx + dx
                ny = ry + dy
                nw = rw - dx
                nh = rh - dy

            min_w, min_h = self.compute_min_size()
            if nw < min_w:
                if "W" in d:
                    nx = rx + rw - min_w
                nw = min_w
            if nh < min_h:
                if "N" in d:
                    ny = ry + rh - min_h
                nh = min_h

            self.apply_geometry(nx, ny, nw, nh, emit_modified=False)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._resizing_dir is not None and self._resize_start_rect is not None:
            old_rect = self._resize_start_rect
            new_rect = (
                float(self.pos().x()),
                float(self.pos().y()),
                self._node_width(),
                self._compute_height(),
            )
            self._resizing_dir = None
            self._resize_start_scene = None
            self._resize_start_rect = None
            hover_dir = self._find_resize_direction(event.pos())
            self._hovered_resize_dir = hover_dir
            if hover_dir is not None:
                self.setCursor(_RESIZE_CURSORS[hover_dir])
            else:
                self.unsetCursor()
            if any(abs(a - b) > 0.5 for a, b in zip(old_rect, new_rect)):
                self.canvas.undo_stack.push(
                    ResizeNodeCommand(self.canvas, self.model.id, old_rect, new_rect)
                )
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self._node_width()
        h = self._compute_height()
        spec = self.model.spec()
        header_hex, cat_accent = CATEGORY_COLORS[spec.category]
        body_hex = CATEGORY_BODY_COLORS[spec.category]
        section_hex = NODE_ACCENT_COLORS.get(self.model.node_type, cat_accent)

        card_rect = QRectF(0, 0, w, h)
        card_path = QPainterPath()
        card_path.addRoundedRect(card_rect, 8, 8)
        selected = self.isSelected()
        border_color = QColor(NODE_SELECTED_BORDER if selected else NODE_BORDER)
        if not self.model.device_available and not selected:
            border_color = QColor("#EC5158")

        painter.setPen(QPen(border_color, 2.2 if selected else 1.1))
        painter.setBrush(QBrush(QColor(body_hex)))
        painter.drawRoundedRect(card_rect, 8, 8)

        # Header band + OneNote left section color stripe
        painter.save()
        painter.setClipPath(card_path)
        painter.fillRect(QRectF(0, 0, w, HEADER_HEIGHT), QColor(header_hex))
        painter.fillRect(QRectF(0, 0, 3.5, h), QColor(section_hex))
        painter.restore()

        painter.setPen(QPen(QColor(COMFY_STROKE), 1.0))
        painter.drawLine(QPointF(3.5, HEADER_HEIGHT), QPointF(w - 1, HEADER_HEIGHT))

        # Collapse chevron + Colorful node icon + Title
        painter.setPen(QColor(COMFY_TEXT_SECONDARY))
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(
            QRectF(6, 2, 16, HEADER_HEIGHT - 4),
            Qt.AlignmentFlag.AlignCenter,
            "▸" if self._collapsed else "▾",
        )

        draw_node_type_icon(
            painter,
            QRectF(23.0, (HEADER_HEIGHT - 15.0) / 2.0, 15.0, 15.0),
            self.model.node_type,
        )

        painter.setPen(QColor(COMFY_TEXT_PRIMARY if selected else "#ECEAF3"))
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        title = painter.fontMetrics().elidedText(
            f"#{self.model.id} {self.model.display_title()}",
            Qt.TextElideMode.ElideRight,
            int(w - 62),
        )
        painter.drawText(
            QRectF(42, 3, w - 62, HEADER_HEIGHT - 6),
            Qt.AlignmentFlag.AlignVCenter,
            title,
        )

        # Small active/muted/waiting status indicator pill on far right of header
        painter.setPen(Qt.PenStyle.NoPen)
        if self.model.muted or not self.model.device_available:
            status_col = "#EC5158"
        elif (
            self.model.node_type == NodeType.APP_CAPTURE
            and self.model.process_name.strip()
            and not self.model.pid
        ):
            status_col = "#FFC114"
        else:
            status_col = "#00C753"
        painter.setBrush(QColor(status_col))
        painter.drawEllipse(QPointF(w - 13.0, HEADER_HEIGHT / 2.0), 3.5, 3.5)

        # Port labels
        label_font = QFont("Segoe UI", 8)
        painter.setFont(label_font)
        painter.setPen(QColor(COMFY_TEXT_SECONDARY))
        for p in self.input_ports:
            painter.drawText(
                QRectF(12, p.y() - 9, 95, 18),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                p.label,
            )
        for p in self.output_ports:
            painter.drawText(
                QRectF(w - 107, p.y() - 9, 95, 18),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                p.label,
            )

        # Mini stereo L/R signal meter at bottom (leave space on right for resize grip)
        bar_w = max(40.0, w - 28.0)
        bar_y = h - 11.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#181620"))
        painter.drawRoundedRect(QRectF(10, bar_y, bar_w, 3), 1.5, 1.5)
        painter.drawRoundedRect(QRectF(10, bar_y + 4, bar_w, 3), 1.5, 1.5)

        if self._peak_l > 0.002:
            col = "#EC5158" if self._peak_l > 0.92 else ("#FFC114" if self._peak_l > 0.72 else "#00C753")
            painter.setBrush(QColor(col))
            painter.drawRoundedRect(QRectF(10, bar_y, bar_w * self._peak_l, 3), 1.5, 1.5)
        if self._peak_r > 0.002:
            col = "#EC5158" if self._peak_r > 0.92 else ("#FFC114" if self._peak_r > 0.72 else "#00C753")
            painter.setBrush(QColor(col))
            painter.drawRoundedRect(QRectF(10, bar_y + 4, bar_w * self._peak_r, 3), 1.5, 1.5)

        # ComfyUI-style subtle bottom-right resize grip when expanded
        if not self._collapsed:
            grip_col = "#E5E5E5" if (self._hovered_resize_dir or self._resizing_dir) else "#7A7A7A"
            painter.setPen(QPen(QColor(grip_col), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for offset in (3.5, 6.5, 9.5):
                painter.drawLine(QPointF(w - offset, h - 2.5), QPointF(w - 2.5, h - offset))

    def itemChange(self, change, value):
        try:
            if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
                if hasattr(self, "model") and hasattr(self, "canvas") and self.canvas is not None:
                    pos = self.pos()
                    self.model.x = float(pos.x())
                    self.model.y = float(pos.y())
                    self.canvas.update_connections_for_node(self.model.id)
                    self.canvas.update_groups_geometry()
        except Exception:
            pass
        try:
            return super().itemChange(change, value)
        except Exception:
            return value


class ConnectionItem(QGraphicsPathItem):
    """Bezier audio stream wire between an output PortItem and an input PortItem."""

    def __init__(self, conn: ConnectionModel, src_port: PortItem, dst_port: PortItem) -> None:
        super().__init__()
        self.conn = conn
        self.src_port = src_port
        self.dst_port = dst_port
        self._active_level = 0.0
        self._dash_offset = 0.0
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(1)
        self.rebuild_path()

    def rebuild_path(self) -> None:
        p1 = self.src_port.center_scene_pos()
        p2 = self.dst_port.center_scene_pos()
        dx = max(abs(p2.x() - p1.x()) * 0.5, 60.0)
        path = QPainterPath(p1)
        path.cubicTo(QPointF(p1.x() + dx, p1.y()), QPointF(p2.x() - dx, p2.y()), p2)
        self.setPath(path)

    def set_activity(self, level: float, dash_offset: float) -> None:
        self._active_level = level
        self._dash_offset = dash_offset
        self.update()

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.isSelected():
            pen = QPen(QColor(WIRE_SELECTED_COLOR), 3.0)
            painter.setPen(pen)
            painter.drawPath(self.path())
            return

        if self._active_level > 0.003:
            base_pen = QPen(QColor("#234452"), 3.0)
            painter.setPen(base_pen)
            painter.drawPath(self.path())

            flow_pen = QPen(QColor(WIRE_ACTIVE_COLOR), 2.4, Qt.PenStyle.DashLine)
            flow_pen.setDashPattern([8, 8])
            flow_pen.setDashOffset(-self._dash_offset)
            painter.setPen(flow_pen)
            painter.drawPath(self.path())
        else:
            painter.setPen(QPen(QColor(WIRE_IDLE_COLOR), 2.2))
            painter.drawPath(self.path())


class GroupItem(QGraphicsRectItem):
    """Visual container box grouping multiple NodeItems."""

    def __init__(self, group: GroupModel) -> None:
        super().__init__()
        self.group = group
        self.setZValue(0)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)

    def update_bounds(self, node_items: Dict[int, NodeItem]) -> None:
        rects = [
            node_items[nid].sceneBoundingRect()
            for nid in self.group.node_ids
            if nid in node_items
        ]
        if not rects:
            return
        union = rects[0]
        for r in rects[1:]:
            union = union.united(r)
        self.setRect(union.adjusted(-18, -34, 18, 18))

    def paint(self, painter: QPainter, option, widget=None) -> None:
        r = self.rect()
        if r.isEmpty():
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        base = QColor(self.group.color)
        fill = QColor(base.red(), base.green(), base.blue(), 42)
        border = QColor(NODE_SELECTED_BORDER if self.isSelected() else "#5E5773")
        painter.setPen(QPen(border, 1.5, Qt.PenStyle.DashLine))
        painter.setBrush(fill)
        painter.drawRoundedRect(r, 10, 10)

        painter.setPen(QColor("#E2E8F0"))
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.drawText(r.adjusted(10, 6, -10, -6), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, self.group.title)


# ---------------------------------------------------------------------------
# Undo / Redo Commands
# ---------------------------------------------------------------------------

class AddNodeCommand(QUndoCommand):
    def __init__(self, canvas: "NodeCanvas", node: NodeModel) -> None:
        super().__init__(f"Add {node.display_title()}")
        self.canvas = canvas
        self.node = node

    def redo(self) -> None:
        self.canvas._insert_node_direct(self.node)

    def undo(self) -> None:
        self.canvas._remove_node_direct(self.node.id)


class AddConnectionCommand(QUndoCommand):
    def __init__(self, canvas: "NodeCanvas", conn: ConnectionModel) -> None:
        super().__init__("Connect Nodes")
        self.canvas = canvas
        self.conn = conn

    def redo(self) -> None:
        self.canvas._insert_connection_direct(self.conn)

    def undo(self) -> None:
        self.canvas._remove_connection_direct(self.conn)


class MoveNodesCommand(QUndoCommand):
    def __init__(
        self,
        canvas: "NodeCanvas",
        moves: List[Tuple[int, Tuple[float, float], Tuple[float, float]]],
    ) -> None:
        super().__init__("Move Nodes")
        self.canvas = canvas
        self.moves = moves

    def redo(self) -> None:
        for nid, _, new_pos in self.moves:
            self.canvas._set_node_pos_direct(nid, new_pos[0], new_pos[1])
        self.canvas.workspace_modified.emit()

    def undo(self) -> None:
        for nid, old_pos, _ in self.moves:
            self.canvas._set_node_pos_direct(nid, old_pos[0], old_pos[1])
        self.canvas.workspace_modified.emit()


class ResizeNodeCommand(QUndoCommand):
    """Undoable corner resize command preserving node position and custom dimensions."""

    def __init__(
        self,
        canvas: "NodeCanvas",
        node_id: int,
        old_rect: Tuple[float, float, Optional[float], Optional[float]],
        new_rect: Tuple[float, float, Optional[float], Optional[float]],
        label: str = "Resize Node",
    ) -> None:
        super().__init__(label)
        self.canvas = canvas
        self.node_id = node_id
        self.old_rect = old_rect
        self.new_rect = new_rect

    def redo(self) -> None:
        self.canvas._set_node_rect_direct(self.node_id, *self.new_rect)
        self.canvas.workspace_modified.emit()

    def undo(self) -> None:
        self.canvas._set_node_rect_direct(self.node_id, *self.old_rect)
        self.canvas.workspace_modified.emit()


class ReplaceConnectionsCommand(QUndoCommand):
    """Atomic undoable wire move or replacement (ComfyUI MovingInputLink / connectSlots style)."""

    def __init__(
        self,
        canvas: "NodeCanvas",
        remove_conns: List[ConnectionModel],
        add_conn: Optional[ConnectionModel],
        label: str = "Update Connection",
    ) -> None:
        super().__init__(label)
        self.canvas = canvas
        self.remove_conns = list(remove_conns)
        self.add_conn = add_conn

    def redo(self) -> None:
        for c in self.remove_conns:
            self.canvas._remove_connection_direct(c)
        if self.add_conn is not None:
            self.canvas._insert_connection_direct(self.add_conn)

    def undo(self) -> None:
        if self.add_conn is not None:
            self.canvas._remove_connection_direct(self.add_conn)
        for c in self.remove_conns:
            self.canvas._insert_connection_direct(c)


class RemoveSelectionCommand(QUndoCommand):
    def __init__(
        self,
        canvas: "NodeCanvas",
        nodes: List[NodeModel],
        connections: List[ConnectionModel],
    ) -> None:
        super().__init__("Delete Selection")
        self.canvas = canvas
        self.nodes = nodes
        self.connections = connections

    def redo(self) -> None:
        for c in self.connections:
            self.canvas._remove_connection_direct(c)
        for n in self.nodes:
            self.canvas._remove_node_direct(n.id)

    def undo(self) -> None:
        for n in self.nodes:
            self.canvas._insert_node_direct(n)
        for c in self.connections:
            self.canvas._insert_connection_direct(c)


class GroupNodesCommand(QUndoCommand):
    def __init__(self, canvas: "NodeCanvas", group: GroupModel) -> None:
        super().__init__(f"Group ({len(group.node_ids)} nodes)")
        self.canvas = canvas
        self.group = group

    def redo(self) -> None:
        self.canvas._insert_group_direct(self.group)

    def undo(self) -> None:
        self.canvas._remove_group_direct(self.group.id)


# ---------------------------------------------------------------------------
# NodeCanvas View & Scene
# ---------------------------------------------------------------------------

class NodeCanvas(QGraphicsView):
    """Infinite zoomable/pannable node editor canvas backed by ProjectModel + QUndoStack."""

    graph_changed = Signal()
    workspace_modified = Signal()
    viewport_changed = Signal()
    selection_changed = Signal(object)  # Optional[NodeModel]
    validation_error = Signal(str)
    float_param_changed = Signal(int, str, float)
    int_param_changed = Signal(int, str, int)

    def __init__(self, project: ProjectModel, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.devices: List[DeviceInfo] = []
        self.processes: List[ProcessInfo] = []
        self.undo_stack = QUndoStack(self)
        self.scene_obj = QGraphicsScene(self)
        self.scene_obj.setSceneRect(-10000, -10000, 20000, 20000)
        self.setScene(self.scene_obj)

        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setAcceptDrops(True)
        self.centerOn(260, 180)

        self.node_items: Dict[int, NodeItem] = {}
        self.conn_items: Dict[ConnectionModel, ConnectionItem] = {}
        self.group_items: Dict[int, GroupItem] = {}

        self._panning = False
        self._pan_moved = False
        self._pan_start = None
        self._restoring_viewport = False
        self._view_state_seq = 0
        self._pending_view_state: Optional[Tuple[int, Dict[str, float]]] = None
        self._drag_port: Optional[PortItem] = None
        self._detaching_conn: Optional[ConnectionModel] = None
        self._temp_wire: Optional[QGraphicsPathItem] = None
        self._move_snapshot: Dict[int, Tuple[float, float]] = {}
        self._dash_phase = 0.0
        self._last_meters: Dict[int, NodeMeter] = {}
        self._top_node_z = 2.0
        self._dragging_popup_scrollbar: Optional[Tuple[Any, ...]] = None

        self.scene_obj.selectionChanged.connect(self._on_scene_selection_changed)
        self.graph_changed.connect(self.workspace_modified.emit)

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(66)
        self._anim_timer.timeout.connect(self._tick_animation)
        self._anim_timer.start()

        self._build_floating_canvas_menu()

    def _build_floating_canvas_menu(self) -> None:
        """ComfyUI GraphCanvasMenu-style bottom-right floating toolbar."""
        self.floating_bar = QFrame(self)
        self.floating_bar.setObjectName("CanvasFloatingBar")
        lay = QHBoxLayout(self.floating_bar)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(2)

        def _btn(text: str, tip: str, slot) -> QToolButton:
            b = QToolButton(self.floating_bar)
            b.setObjectName("CanvasMenuBtn")
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            return b

        self.btn_float_fit = _btn("Fit", "Fit / Reset View", self.fit_view)
        self.btn_float_zout = _btn("−", "Zoom Out", self.zoom_out)
        self.btn_float_zoom = _btn("100%", "Reset Zoom (100%)", self.reset_zoom)
        self.btn_float_zin = _btn("+", "Zoom In", self.zoom_in)

        for w in (
            self.btn_float_fit,
            self.btn_float_zout,
            self.btn_float_zoom,
            self.btn_float_zin,
        ):
            lay.addWidget(w)

        self.floating_bar.adjustSize()
        self._reposition_floating_bar()

    def _reposition_floating_bar(self) -> None:
        if not hasattr(self, "floating_bar"):
            return
        self.floating_bar.adjustSize()
        bw = self.floating_bar.width()
        bh = self.floating_bar.height()
        self.floating_bar.move(max(8, self.width() - bw - 14), max(8, self.height() - bh - 14))
        self.floating_bar.raise_()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._reposition_floating_bar()
        if self._pending_view_state is not None:
            seq, state = self._pending_view_state
            self._apply_view_transform(state)
            QTimer.singleShot(0, lambda sq=seq: self._finalize_pending_view_state(sq))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._reposition_floating_bar()
        if self._pending_view_state is not None:
            self._apply_view_transform(self._pending_view_state[1])

    def _update_zoom_label(self) -> None:
        if hasattr(self, "btn_float_zoom"):
            pct = int(round(self.transform().m11() * 100.0))
            self.btn_float_zoom.setText(f"{pct}%")
            self._reposition_floating_bar()

    def get_view_state(self) -> Dict[str, float]:
        if self._pending_view_state is not None:
            return dict(self._pending_view_state[1])
        center = self.mapToScene(self.viewport().rect().center())
        return {
            "center_x": float(center.x()),
            "center_y": float(center.y()),
            "zoom": float(self.transform().m11()),
        }

    def _apply_view_transform(self, state: Dict[str, float]) -> None:
        self._restoring_viewport = True
        try:
            zoom = max(0.15, min(4.0, float(state.get("zoom", 1.0))))
            cx = float(state.get("center_x", 260.0))
            cy = float(state.get("center_y", 180.0))
            self.resetTransform()
            self.scale(zoom, zoom)
            self.centerOn(cx, cy)
            self._update_zoom_label()
        finally:
            self._restoring_viewport = False

    def _finalize_pending_view_state(self, seq: int) -> None:
        if self._pending_view_state is not None and self._pending_view_state[0] == seq:
            state = self._pending_view_state[1]
            self._pending_view_state = None
            self._apply_view_transform(state)

    def set_view_state(self, state: Optional[Dict[str, float]]) -> None:
        if not isinstance(state, dict):
            return
        try:
            norm = {
                "center_x": float(state.get("center_x", 260.0)),
                "center_y": float(state.get("center_y", 180.0)),
                "zoom": max(0.15, min(4.0, float(state.get("zoom", 1.0)))),
            }
        except (TypeError, ValueError):
            return
        self._view_state_seq += 1
        seq = self._view_state_seq
        self._apply_view_transform(norm)
        if not self.isVisible():
            self._pending_view_state = (seq, norm)
        else:
            self._pending_view_state = None

    def _emit_viewport_changed(self) -> None:
        if self._restoring_viewport:
            return
        self._view_state_seq += 1
        self._pending_view_state = None
        self.viewport_changed.emit()

    def update_catalog(self, devices: List[DeviceInfo], processes: List[ProcessInfo]) -> None:
        self.devices = list(devices)
        self.processes = list(processes)
        for item in self.node_items.values():
            item.refresh_catalog(self.devices, self.processes)

    def select_all_nodes(self) -> None:
        for item in self.node_items.values():
            item.setSelected(True)

    def fit_view(self) -> None:
        self.resetTransform()
        self._update_zoom_label()
        if self.node_items:
            bounds = self.scene_obj.itemsBoundingRect()
            if not bounds.isNull():
                self.centerOn(bounds.center())
        self._emit_viewport_changed()

    # ----- Background Grid -----

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, QColor(CANVAS_BG))
        minor = 24
        major = minor * 5

        left = int(rect.left()) - (int(rect.left()) % minor)
        top = int(rect.top()) - (int(rect.top()) % minor)

        minor_pen = QPen(QColor(CANVAS_GRID_MINOR), 1.0)
        major_pen = QPen(QColor(CANVAS_GRID_MAJOR), 1.2)

        for x in range(left, int(rect.right()) + minor, minor):
            painter.setPen(major_pen if x % major == 0 else minor_pen)
            painter.drawLine(x, int(rect.top()), x, int(rect.bottom()))
        for y in range(top, int(rect.bottom()) + minor, minor):
            painter.setPen(major_pen if y % major == 0 else minor_pen)
            painter.drawLine(int(rect.left()), y, int(rect.right()), y)

    # ----- Direct / Undoable Operations -----

    def add_node_action(self, node_type: NodeType, scene_pos: Optional[QPointF] = None, **kwargs) -> NodeModel:
        if scene_pos is None:
            center = self.mapToScene(self.viewport().rect().center())
            offset = (len(self.project.nodes) % 6) * 28.0
            scene_pos = QPointF(center.x() - NODE_WIDTH / 2 + offset, center.y() - 50 + offset)
        nid = self.project.allocate_node_id()
        node = NodeModel(
            id=nid,
            node_type=node_type,
            x=float(scene_pos.x()),
            y=float(scene_pos.y()),
            **kwargs,
        )
        if node.node_type == NodeType.APP_CAPTURE and node.pid > 0 and not node.capture_pid:
            node.capture_pid = resolve_capture_pid_for_process(node.pid, self.processes)
        self.undo_stack.push(AddNodeCommand(self, node))
        return node

    def connect_ports_action(
        self,
        src_node: int,
        src_port: int,
        dst_node: int,
        dst_port: int,
        replace_existing: bool = False,
        detach_conn: Optional[ConnectionModel] = None,
    ) -> bool:
        to_remove: List[ConnectionModel] = []
        if detach_conn is not None and detach_conn in self.project.connections:
            to_remove.append(detach_conn)

        if replace_existing and dst_node in self.project.nodes:
            dn = self.project.nodes[dst_node]
            if dn.node_type != NodeType.MIXER:
                for c in self.project.connections:
                    if c.dst_node == dst_node and c not in to_remove:
                        to_remove.append(c)
            else:
                for c in self.project.connections:
                    if c.dst_node == dst_node and c.dst_port == dst_port and c not in to_remove:
                        to_remove.append(c)

        ok, err = self.project.validate_connection(
            src_node,
            src_port,
            dst_node,
            dst_port,
            ignore_connections=to_remove,
        )
        if not ok:
            self.validation_error.emit(err)
            return False

        new_conn = ConnectionModel(src_node, src_port, dst_node, dst_port)
        if to_remove:
            self.undo_stack.push(
                ReplaceConnectionsCommand(self, to_remove, new_conn, "Update Wire")
            )
        else:
            self.undo_stack.push(AddConnectionCommand(self, new_conn))
        return True

    def delete_selected_action(self) -> None:
        selected = self.scene_obj.selectedItems()
        if not selected:
            return
        sel_nodes: List[NodeModel] = []
        sel_conns: List[ConnectionModel] = []
        node_ids_to_del = set()

        for it in selected:
            if isinstance(it, NodeItem):
                sel_nodes.append(it.model)
                node_ids_to_del.add(it.model.id)
            elif isinstance(it, ConnectionItem):
                sel_conns.append(it.conn)
            elif isinstance(it, GroupItem):
                self._remove_group_direct(it.group.id)

        for c in list(self.project.connections):
            if (c.src_node in node_ids_to_del or c.dst_node in node_ids_to_del) and c not in sel_conns:
                sel_conns.append(c)

        if sel_nodes or sel_conns:
            self.undo_stack.push(RemoveSelectionCommand(self, sel_nodes, sel_conns))

    def group_selected_action(self, title: str = "Signal Group") -> Optional[GroupModel]:
        nids = [it.model.id for it in self.scene_obj.selectedItems() if isinstance(it, NodeItem)]
        if len(nids) < 2:
            self.validation_error.emit("Select at least 2 nodes to create a group.")
            return None
        gid = self.project.allocate_group_id()
        grp = GroupModel(id=gid, title=title, node_ids=nids)
        self.undo_stack.push(GroupNodesCommand(self, grp))
        return grp

    def _insert_node_direct(self, node: NodeModel) -> None:
        self.project.nodes[node.id] = node
        item = NodeItem(node, self)
        self.node_items[node.id] = item
        self.scene_obj.addItem(item)
        self.update_groups_geometry()
        self.graph_changed.emit()

    def _remove_node_direct(self, node_id: int) -> None:
        for c in list(self.project.connections):
            if c.src_node == node_id or c.dst_node == node_id:
                self._remove_connection_direct(c)
        self.project.remove_node(node_id)
        item = self.node_items.pop(node_id, None)
        if item is not None:
            self.scene_obj.removeItem(item)
        self.update_groups_geometry()
        self.graph_changed.emit()

    def _set_node_pos_direct(self, node_id: int, x: float, y: float) -> None:
        item = self.node_items.get(node_id)
        if item is not None:
            item.setPos(x, y)

    def _set_node_rect_direct(
        self,
        node_id: int,
        x: float,
        y: float,
        width: Optional[float],
        height: Optional[float],
    ) -> None:
        item = self.node_items.get(node_id)
        if item is not None:
            item.apply_geometry(x, y, width, height, emit_modified=False)

    def resize_selected_to_fit_action(self) -> None:
        """Reset selected nodes back to their default computed size (Comfy.Canvas.Resize)."""
        sel = [it for it in self.scene_obj.selectedItems() if isinstance(it, NodeItem)]
        for it in sel:
            if it.model.width is None and it.model.height is None:
                continue
            old_rect = (
                float(it.pos().x()),
                float(it.pos().y()),
                it.model.width,
                it.model.height,
            )
            new_rect = (float(it.pos().x()), float(it.pos().y()), None, None)
            self.undo_stack.push(
                ResizeNodeCommand(self, it.model.id, old_rect, new_rect, "Resize Node to Fit")
            )

    def _insert_connection_direct(self, conn: ConnectionModel) -> None:
        if conn not in self.project.connections:
            self.project.connections.append(conn)
        src_item = self.node_items.get(conn.src_node)
        dst_item = self.node_items.get(conn.dst_node)
        if not src_item or not dst_item:
            return
        if conn.src_port >= len(src_item.output_ports) or conn.dst_port >= len(dst_item.input_ports):
            return
        ci = ConnectionItem(
            conn,
            src_item.output_ports[conn.src_port],
            dst_item.input_ports[conn.dst_port],
        )
        self.conn_items[conn] = ci
        self.scene_obj.addItem(ci)
        self.graph_changed.emit()

    def _remove_connection_direct(self, conn: ConnectionModel) -> None:
        self.project.remove_connection(conn)
        ci = self.conn_items.pop(conn, None)
        if ci is not None:
            self.scene_obj.removeItem(ci)
        self.graph_changed.emit()

    def _insert_group_direct(self, grp: GroupModel) -> None:
        self.project.groups[grp.id] = grp
        for nid in grp.node_ids:
            if nid in self.project.nodes:
                self.project.nodes[nid].group_id = grp.id
        gi = GroupItem(grp)
        gi.update_bounds(self.node_items)
        self.group_items[grp.id] = gi
        self.scene_obj.addItem(gi)

    def _remove_group_direct(self, group_id: int) -> None:
        self.project.remove_group(group_id)
        gi = self.group_items.pop(group_id, None)
        if gi is not None:
            self.scene_obj.removeItem(gi)

    def rebuild_from_project(self) -> None:
        self.undo_stack.clear()
        self.scene_obj.clear()
        self.node_items.clear()
        self.conn_items.clear()
        self.group_items.clear()

        for node in self.project.nodes.values():
            item = NodeItem(node, self)
            self.node_items[node.id] = item
            self.scene_obj.addItem(item)

        for conn in self.project.connections:
            src_item = self.node_items.get(conn.src_node)
            dst_item = self.node_items.get(conn.dst_node)
            if src_item and dst_item:
                if conn.src_port < len(src_item.output_ports) and conn.dst_port < len(dst_item.input_ports):
                    ci = ConnectionItem(
                        conn,
                        src_item.output_ports[conn.src_port],
                        dst_item.input_ports[conn.dst_port],
                    )
                    self.conn_items[conn] = ci
                    self.scene_obj.addItem(ci)

        for grp in self.project.groups.values():
            gi = GroupItem(grp)
            gi.update_bounds(self.node_items)
            self.group_items[grp.id] = gi
            self.scene_obj.addItem(gi)

    def update_connections_for_node(self, node_id: int) -> None:
        for conn, ci in self.conn_items.items():
            if conn.src_node == node_id or conn.dst_node == node_id:
                ci.rebuild_path()

    def update_groups_geometry(self) -> None:
        for gi in self.group_items.values():
            gi.update_bounds(self.node_items)

    def update_meters(self, meters: Dict[int, NodeMeter]) -> None:
        self._last_meters = meters
        for nid, item in self.node_items.items():
            item.set_meter(meters.get(nid))

    def _tick_animation(self) -> None:
        win = self.window()
        if not self.isVisible() or (win is not None and win.isMinimized()):
            return
        self._dash_phase = (self._dash_phase + 2.5) % 32.0
        for conn, ci in self.conn_items.items():
            m = self._last_meters.get(conn.src_node)
            lvl = max(m.peak_left, m.peak_right) if m else 0.0
            ci.set_activity(lvl, self._dash_phase)

    def _on_scene_selection_changed(self) -> None:
        selected_nodes = [
            it.model for it in self.scene_obj.selectedItems() if isinstance(it, NodeItem)
        ]
        self.selection_changed.emit(selected_nodes[0] if len(selected_nodes) == 1 else None)

    # ----- Mouse & Keyboard Interaction -----

    def _apply_zoom_factor(self, factor: float) -> None:
        cur = float(self.transform().m11())
        target = max(0.15, min(4.0, cur * factor))
        if cur > 0.0:
            step = target / cur
            self.scale(step, step)
        self._update_zoom_label()
        self._emit_viewport_changed()

    def zoom_in(self) -> None:
        self._apply_zoom_factor(1.15)

    def zoom_out(self) -> None:
        self._apply_zoom_factor(1.0 / 1.15)

    def reset_zoom(self) -> None:
        self.resetTransform()
        self._update_zoom_label()
        self._emit_viewport_changed()

    def _find_open_combo(self) -> Tuple[Optional[QComboBox], Optional[QWidget]]:
        for item in self.node_items.values():
            for cmb in (item.cmb_device, item.cmb_process, item.cmb_map_l, item.cmb_map_r):
                if cmb is not None:
                    p = getattr(cmb, "_floating_popup", None)
                    if p is not None and p.isVisible():
                        return cmb, p
                    if hasattr(cmb, "view") and cmb.view() is not None and cmb.view().isVisible():
                        return cmb, cmb.view()
        return None, None

    def _find_open_combo_view(self) -> Optional[QWidget]:
        _, view = self._find_open_combo()
        return view

    def hide_open_popups(self, global_pos: Optional[QPoint] = None) -> bool:
        """Dismiss any open inline node combo dropdown when clicking outside it (ComfyUI pointerdown rule)."""
        self._dragging_popup_scrollbar = None
        open_cmb, open_popup = self._find_open_combo()
        if open_cmb is None or open_popup is None:
            return False

        if global_pos is None:
            open_cmb.hidePopup()
            return True

        if _is_global_pos_on_combo(self, open_cmb, global_pos) or _is_global_pos_on_popup(self, open_cmb, global_pos):
            return False

        open_cmb.hidePopup()
        return True

    def _is_wheel_over_dropdown(self, view_pos: QPoint) -> bool:
        scene_pt = self.mapToScene(view_pos)
        for item in self.items(view_pos):
            if isinstance(item, QGraphicsProxyWidget) and item.widget() is not None:
                local_pt = item.mapFromScene(scene_pt).toPoint()
                w = item.widget().childAt(local_pt)
                while w is not None:
                    if isinstance(w, (QComboBox, QAbstractItemView)):
                        return True
                    if w is item.widget():
                        break
                    w = w.parentWidget()
        return False

    def viewportEvent(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel:
            self.wheelEvent(event)
            return True
        return super().viewportEvent(event)

    def wheelEvent(self, event) -> None:
        open_cmb, open_popup = self._find_open_combo()
        view_pt = event.position().toPoint()
        global_pt = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else QPoint(0, 0)
        if open_cmb is not None and open_popup is not None:
            # Check if wheel is over the popup or its list widget
            is_over = False
            if open_popup.geometry().contains(view_pt) or open_popup.geometry().contains(global_pt):
                is_over = True
            pop_grect = QRect(open_popup.mapToGlobal(QPoint(0, 0)), open_popup.size())
            if not is_over and pop_grect.contains(global_pt):
                is_over = True
            v = open_cmb.view()
            if not is_over and v is not None and v.viewport() is not None:
                v_rect = v.viewport().rect()
                v_tl = v.viewport().mapToGlobal(v_rect.topLeft())
                v_br = v.viewport().mapToGlobal(v_rect.bottomRight())
                if QRect(v_tl, v_br).contains(global_pt) or v.viewport().geometry().contains(view_pt):
                    is_over = True
            pw = _find_popup_graphics_item(open_cmb)
            if not is_over and pw is not None:
                scene_pt = self.mapToScene(view_pt)
                if pw.sceneBoundingRect().contains(scene_pt):
                    is_over = True

            if is_over:
                list_w = getattr(open_popup, "list_widget", None) or getattr(open_cmb, "view", lambda: None)()
                if list_w is not None:
                    sb = list_w.verticalScrollBar()
                    if sb is not None:
                        dy = event.angleDelta().y()
                        steps = -int((dy / 120.0) * 3) if dy != 0 else 0
                        sb.setValue(sb.value() + steps)
                event.accept()
                return

            open_cmb.hidePopup()
            event.accept()
            return

        if self._is_wheel_over_dropdown(event.position().toPoint()):
            event.accept()
            return

        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
        self._apply_zoom_factor(factor)
        event.accept()

    def _find_port_at(self, view_pos) -> Optional[PortItem]:
        search_rect = QRect(view_pos.x() - 10, view_pos.y() - 10, 20, 20)
        scene_pt = self.mapToScene(view_pos)
        ports = [item for item in self.items(search_rect) if isinstance(item, PortItem)]
        if not ports:
            return None
        ports.sort(key=lambda p: (p.center_scene_pos() - scene_pt).manhattanLength())
        return ports[0]

    def _find_node_at(self, view_pos) -> Optional[NodeItem]:
        for item in self.items(view_pos):
            cur = item
            while cur is not None:
                if isinstance(cur, NodeItem):
                    return cur
                cur = cur.parentItem()
        return None

    def _find_connection_at_input(self, node_id: int, port_index: int) -> Optional[ConnectionModel]:
        for c in self.project.connections:
            if c.dst_node == node_id and c.dst_port == port_index:
                return c
        return None

    def _pick_target_port_on_node(
        self, node_item: NodeItem, origin_is_output: bool
    ) -> Optional[PortItem]:
        if origin_is_output:
            if not node_item.input_ports:
                return None
            # Prefer first unconnected input port (e.g. Mixer In 1..4), else fall back to port 0
            occupied = {
                c.dst_port
                for c in self.project.connections
                if c.dst_node == node_item.model.id and c != self._detaching_conn
            }
            for p in node_item.input_ports:
                if p.port_index not in occupied:
                    return p
            return node_item.input_ports[0]
        if not node_item.output_ports:
            return None
        return node_item.output_ports[0]

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(NODE_DRAG_MIME_TYPE):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(NODE_DRAG_MIME_TYPE):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasFormat(NODE_DRAG_MIME_TYPE):
            raw = bytes(event.mimeData().data(NODE_DRAG_MIME_TYPE)).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
                nt = NodeType(int(payload["node_type"]))
                kwargs = dict(payload.get("kwargs", {}))
            except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                event.ignore()
                return
            scene_pos = self.mapToScene(event.position().toPoint())
            # Center horizontally and land on node header bar (ComfyUI drop UX)
            drop_pos = QPointF(scene_pos.x() - NODE_WIDTH / 2.0, scene_pos.y() - HEADER_HEIGHT / 2.0)
            self.add_node_action(nt, scene_pos=drop_pos, **kwargs)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def mousePressEvent(self, event) -> None:
        open_cmb, open_popup = self._find_open_combo()
        if open_cmb is not None:
            view_pos = event.position().toPoint()
            gpos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else QPoint(0, 0)

            # A. If clicked on the combobox itself, toggle closed
            if _is_global_pos_on_combo(self, open_cmb, gpos) or _is_view_pos_on_combo(self, open_cmb, view_pos):
                open_cmb.hidePopup()
                event.accept()
                return

            # B. If clicked in scene coordinates via _find_popup_graphics_item (synthetic tests under zoom)
            pw = _find_popup_graphics_item(open_cmb)
            if pw is not None:
                scene_pt = self.mapToScene(view_pos)
                if pw.sceneBoundingRect().contains(scene_pt):
                    pt_in_pw = pw.mapFromScene(scene_pt)
                    v = open_cmb.view()
                    if v is not None:
                        local_pt = QPoint(int(pt_in_pw.x() - v.x()), int(pt_in_pw.y() - v.y()))
                        idx = v.indexAt(local_pt)
                        if idx.isValid():
                            row = idx.row()
                            open_cmb.setCurrentIndex(row)
                            open_cmb.activated.emit(row)
                            open_cmb.hidePopup()
                            event.accept()
                            return

            # C. If clicked inside the floating popup in viewport or global screen coordinates
            pop_grect = QRect(open_popup.mapToGlobal(QPoint(0, 0)), open_popup.size()) if open_popup is not None else QRect()
            if open_popup is not None and (open_popup.geometry().contains(view_pos) or pop_grect.contains(gpos)):
                click_gpos = gpos if not gpos.isNull() and gpos != QPoint(0, 0) else self.viewport().mapToGlobal(view_pos)
                if open_popup.handle_click_at_global(click_gpos):
                    event.accept()
                    return

            # D. Outside click -> dismiss popup and absorb click
            open_cmb.hidePopup()
            event.accept()
            return

        pos = event.position().toPoint()
        if not isinstance(self.itemAt(pos), QGraphicsProxyWidget):
            self.setFocus(Qt.FocusReason.MouseFocusReason)
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_moved = False
            self._pan_start = pos
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            port = self._find_port_at(pos)
            if port is not None:
                # ComfyUI MovingInputLink: dragging a connected input port detaches/moves its wire
                if not port.is_output:
                    existing = self._find_connection_at_input(
                        port.node_item.model.id, port.port_index
                    )
                    if existing is not None:
                        src_item = self.node_items.get(existing.src_node)
                        if src_item and existing.src_port < len(src_item.output_ports):
                            self._detaching_conn = existing
                            ci = self.conn_items.get(existing)
                            if ci is not None:
                                ci.hide()
                            port = src_item.output_ports[existing.src_port]

                self._drag_port = port
                start = port.center_scene_pos()
                self._temp_wire = QGraphicsPathItem(QPainterPath(start))
                self._temp_wire.setPen(QPen(QColor("#FFC114"), 2.4, Qt.PenStyle.DashLine))
                self._temp_wire.setZValue(10)
                self.scene_obj.addItem(self._temp_wire)
                event.accept()
                return

            if self.itemAt(pos) is None:
                if event.modifiers() & (
                    Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
                ):
                    self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
                else:
                    self.scene_obj.clearSelection()
                    self._panning = True
                    self._pan_moved = False
                    self._pan_start = pos
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)
                    event.accept()
                    return
            elif event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                node_hit = self._find_node_at(pos)
                if node_hit is not None:
                    node_hit.setSelected(not node_hit.isSelected())
                    event.accept()
                    return

            # Record node positions for move undo tracking
            self._move_snapshot = {
                nid: (it.model.x, it.model.y) for nid, it in self.node_items.items()
            }

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        if getattr(self, "_dragging_popup_scrollbar", None) is not None:
            open_cmb, sb, pw, container_widget = self._dragging_popup_scrollbar
            if open_cmb.view() is not None and open_cmb.view().isVisible() and sb.isVisible():
                scene_pt = self.mapToScene(pos)
                pt_in_pw = pw.mapFromScene(scene_pt).toPoint()
                sb_pt = sb.mapFrom(container_widget, pt_in_pw)
                if sb.height() > 0:
                    ratio = max(0.0, min(1.0, float(sb_pt.y()) / float(sb.height())))
                    val = int(ratio * (sb.maximum() - sb.minimum()) + sb.minimum())
                    sb.setValue(val)
                event.accept()
                return
            else:
                self._dragging_popup_scrollbar = None

        if self._panning and self._pan_start is not None:
            delta = pos - self._pan_start
            self._pan_start = pos
            if delta.x() != 0 or delta.y() != 0:
                self._pan_moved = True
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return

        if self._drag_port is not None and self._temp_wire is not None:
            p1 = self._drag_port.center_scene_pos()
            p2 = self.mapToScene(pos)
            dx = max(abs(p2.x() - p1.x()) * 0.5, 50.0)
            path = QPainterPath(p1)
            if self._drag_port.is_output:
                path.cubicTo(QPointF(p1.x() + dx, p1.y()), QPointF(p2.x() - dx, p2.y()), p2)
            else:
                path.cubicTo(QPointF(p1.x() - dx, p1.y()), QPointF(p2.x() + dx, p2.y()), p2)
            self._temp_wire.setPath(path)
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if getattr(self, "_dragging_popup_scrollbar", None) is not None:
            self._dragging_popup_scrollbar = None
            event.accept()
            return

        if self._panning:
            moved = self._pan_moved
            self._panning = False
            self._pan_moved = False
            self._pan_start = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if moved:
                self._emit_viewport_changed()
            event.accept()
            return

        if self._drag_port is not None:
            pos = event.position().toPoint()
            if self._temp_wire is not None:
                self.scene_obj.removeItem(self._temp_wire)
                self._temp_wire = None
            target = self._find_port_at(pos)
            origin = self._drag_port
            detaching = self._detaching_conn
            self._drag_port = None
            self._detaching_conn = None

            if target is None:
                # ComfyUI connectToNode fallback: allow dropping anywhere on a valid node card
                node_under = self._find_node_at(pos)
                if node_under is not None and node_under is not origin.node_item:
                    target = self._pick_target_port_on_node(node_under, origin.is_output)

            if target is not None and target.is_output != origin.is_output:
                out_p = origin if origin.is_output else target
                in_p = target if origin.is_output else origin
                same_target = (
                    detaching is not None
                    and detaching.src_node == out_p.node_item.model.id
                    and detaching.src_port == out_p.port_index
                    and detaching.dst_node == in_p.node_item.model.id
                    and detaching.dst_port == in_p.port_index
                )
                if same_target:
                    ci = self.conn_items.get(detaching)
                    if ci is not None:
                        ci.show()
                else:
                    ok = self.connect_ports_action(
                        out_p.node_item.model.id,
                        out_p.port_index,
                        in_p.node_item.model.id,
                        in_p.port_index,
                        replace_existing=True,
                        detach_conn=detaching,
                    )
                    if not ok and detaching is not None:
                        ci = self.conn_items.get(detaching)
                        if ci is not None:
                            ci.show()
            elif detaching is not None:
                # Dropped on empty canvas -> ComfyUI drag-off disconnects the wire
                self.undo_stack.push(RemoveSelectionCommand(self, [], [detaching]))
            event.accept()
            return

        open_cmb, open_view = self._find_open_combo()
        if open_cmb is not None and open_view is not None:
            view_pos = event.position().toPoint()
            if _is_view_pos_on_popup(self, open_cmb, view_pos) or _is_view_pos_on_combo(self, open_cmb, view_pos):
                event.accept()
                return

        super().mouseReleaseEvent(event)
        if self.dragMode() != QGraphicsView.DragMode.NoDrag:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)

        if self._move_snapshot:
            moves = []
            for nid, old_xy in self._move_snapshot.items():
                it = self.node_items.get(nid)
                if it is not None:
                    new_xy = (it.model.x, it.model.y)
                    if abs(new_xy[0] - old_xy[0]) > 0.5 or abs(new_xy[1] - old_xy[1]) > 0.5:
                        moves.append((nid, old_xy, new_xy))
            self._move_snapshot.clear()
            if moves:
                self.undo_stack.push(MoveNodesCommand(self, moves))

    def keyPressEvent(self, event) -> None:
        fw = QApplication.focusWidget()
        in_text_edit = isinstance(self.scene_obj.focusItem(), QGraphicsProxyWidget) and isinstance(
            fw, QLineEdit
        )
        if not in_text_edit:
            if event.matches(QKeySequence.StandardKey.SelectAll) or (
                event.key() == Qt.Key.Key_A
                and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            ):
                self.select_all_nodes()
                event.accept()
                return
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self.delete_selected_action()
                event.accept()
                return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.hide_open_popups(event.globalPos())
        item = self.itemAt(event.pos())
        menu = QMenu(self)
        if isinstance(item, ConnectionItem):
            act_del = menu.addAction("Disconnect Wire")
            if menu.exec(event.globalPos()) == act_del:
                self.undo_stack.push(RemoveSelectionCommand(self, [], [item.conn]))
            return

        scene_pos = self.mapToScene(event.pos())
        if isinstance(item, NodeItem) and not item.isSelected():
            self.scene_obj.clearSelection()
            item.setSelected(True)

        sel_nodes = [it for it in self.scene_obj.selectedItems() if isinstance(it, NodeItem)]
        if sel_nodes:
            act_group = menu.addAction("Group Selected Nodes")
            act_fit_size = (
                menu.addAction("Resize to Fit")
                if any(it.model.width is not None or it.model.height is not None for it in sel_nodes)
                else None
            )
            act_del = menu.addAction("Delete Selected")
            chosen = menu.exec(event.globalPos())
            if chosen == act_group:
                self.group_selected_action()
            elif act_fit_size is not None and chosen == act_fit_size:
                self.resize_selected_to_fit_action()
            elif chosen == act_del:
                self.delete_selected_action()
            return

        # Empty canvas right-click quick add
        src_menu = menu.addMenu("Add Source Node")
        proc_menu = menu.addMenu("Add Processing Node")
        out_menu = menu.addMenu("Add Output Node")
        actions = {}
        from phonoryx.models.graph_model import NODE_SPECS

        for nt, spec in NODE_SPECS.items():
            target_menu = (
                src_menu
                if spec.category == NodeCategory.SOURCE
                else (proc_menu if spec.category == NodeCategory.PROCESSING else out_menu)
            )
            actions[target_menu.addAction(spec.title)] = nt

        chosen = menu.exec(event.globalPos())
        if chosen in actions:
            self.add_node_action(actions[chosen], scene_pos=scene_pos)

