"""Interactive PySide6 QGraphicsView node-based audio patchbay canvas."""

from __future__ import annotations

import json
from typing import Dict, Iterable, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QUndoCommand,
    QUndoStack,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QMenu,
)

from audioflow.engine_bridge import NodeMeter, NodeType
from audioflow.models.graph_model import (
    ConnectionModel,
    GroupModel,
    NodeCategory,
    NodeModel,
    ProjectModel,
)
from audioflow.ui.theme import (
    CANVAS_BG,
    CANVAS_GRID_MAJOR,
    CANVAS_GRID_MINOR,
    CATEGORY_COLORS,
    NODE_BG,
    NODE_BORDER,
    NODE_SELECTED_BORDER,
    PORT_INPUT_COLOR,
    PORT_OUTPUT_COLOR,
    WIRE_ACTIVE_COLOR,
    WIRE_IDLE_COLOR,
    WIRE_SELECTED_COLOR,
)


NODE_WIDTH = 224.0
HEADER_HEIGHT = 28.0
PORT_ROW_HEIGHT = 22.0
FOOTER_HEIGHT = 26.0
PORT_RADIUS = 6.5
NODE_DRAG_MIME_TYPE = "application/x-audioflow-node"


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
    """Visual card representing one NodeModel on the patchbay canvas."""

    def __init__(self, model: NodeModel, canvas: "NodeCanvas") -> None:
        super().__init__()
        self.model = model
        self.canvas = canvas
        self.input_ports: List[PortItem] = []
        self.output_ports: List[PortItem] = []
        self._peak_l = 0.0
        self._peak_r = 0.0
        self._drag_start_pos: Optional[QPointF] = None

        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setPos(model.x, model.y)
        self.setZValue(2)
        self._build_ports()

    def _compute_height(self) -> float:
        spec = self.model.spec()
        rows = max(len(spec.input_ports), len(spec.output_ports), 1)
        return HEADER_HEIGHT + 12.0 + rows * PORT_ROW_HEIGHT + FOOTER_HEIGHT

    def _build_ports(self) -> None:
        spec = self.model.spec()
        y0 = HEADER_HEIGHT + 16.0
        for idx, label in enumerate(spec.input_ports):
            p = PortItem(self, idx, is_output=False, label=label)
            p.setPos(0.0, y0 + idx * PORT_ROW_HEIGHT)
            self.input_ports.append(p)
        for idx, label in enumerate(spec.output_ports):
            p = PortItem(self, idx, is_output=True, label=label)
            p.setPos(NODE_WIDTH, y0 + idx * PORT_ROW_HEIGHT)
            self.output_ports.append(p)

    def set_meter(self, meter: Optional[NodeMeter]) -> None:
        if meter is None:
            self._peak_l = 0.0
            self._peak_r = 0.0
        else:
            self._peak_l = min(1.0, max(0.0, meter.peak_left))
            self._peak_r = min(1.0, max(0.0, meter.peak_right))
        self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(-2, -2, NODE_WIDTH + 4, self._compute_height() + 4)

    def _subtitle_text(self) -> str:
        nt = self.model.node_type
        if nt == NodeType.APP_CAPTURE:
            if self.model.process_name:
                return f"{self.model.process_name} (PID {self.model.pid})"
            return "Select application..."
        if nt == NodeType.FILE_RECORDING:
            return self.model.file_path.rsplit("\\", 1)[-1] if self.model.file_path else "No WAV path"
        if nt in (NodeType.VOLUME, NodeType.GAIN, NodeType.MUTE):
            if self.model.muted:
                return "MUTED"
            if nt == NodeType.VOLUME:
                return f"Vol {int(round(self.model.volume * 100))}%"
            if nt == NodeType.GAIN:
                return f"Gain {self.model.gain_db:+.1f} dB"
            return "Active"
        if self.model.device_name:
            prefix = "[OFFLINE] " if not self.model.device_available else ""
            return prefix + self.model.device_name
        if self.model.device_id:
            prefix = "[OFFLINE] " if not self.model.device_available else ""
            return prefix + self.model.device_id[:28]
        return self.model.spec().category.value.upper()

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self._compute_height()
        spec = self.model.spec()
        accent = QColor(CATEGORY_COLORS[spec.category])

        # Main card body
        card_rect = QRectF(0, 0, NODE_WIDTH, h)
        border_color = QColor(NODE_SELECTED_BORDER if self.isSelected() else NODE_BORDER)
        if not self.model.device_available:
            border_color = QColor("#EF4444")

        painter.setPen(QPen(border_color, 2.0 if self.isSelected() else 1.3))
        painter.setBrush(QBrush(QColor(NODE_BG)))
        painter.drawRoundedRect(card_rect, 8, 8)

        # Header band
        header_path = QPainterPath()
        header_path.addRoundedRect(QRectF(0, 0, NODE_WIDTH, HEADER_HEIGHT), 8, 8)
        painter.save()
        painter.setClipRect(QRectF(0, 0, NODE_WIDTH, HEADER_HEIGHT))
        painter.fillPath(header_path, accent.darker(135))
        painter.restore()

        painter.setPen(QColor("#F8FAFC"))
        title_font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        painter.setFont(title_font)
        title = painter.fontMetrics().elidedText(
            f"#{self.model.id} {self.model.display_title()}",
            Qt.TextElideMode.ElideRight,
            int(NODE_WIDTH - 16),
        )
        painter.drawText(QRectF(10, 4, NODE_WIDTH - 20, HEADER_HEIGHT - 6), Qt.AlignmentFlag.AlignVCenter, title)

        # Port labels
        label_font = QFont("Segoe UI", 8)
        painter.setFont(label_font)
        painter.setPen(QColor("#CBD5E1"))
        for p in self.input_ports:
            painter.drawText(QRectF(12, p.y() - 9, 85, 18), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, p.label)
        for p in self.output_ports:
            painter.drawText(QRectF(NODE_WIDTH - 97, p.y() - 9, 85, 18), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, p.label)

        # Footer: subtitle + stereo mini meter
        footer_y = h - FOOTER_HEIGHT
        painter.setPen(QColor("#EF4444" if not self.model.device_available else "#94A3B8"))
        sub_font = QFont("Segoe UI")
        sub_font.setPointSizeF(7.5)
        painter.setFont(sub_font)
        sub = painter.fontMetrics().elidedText(
            self._subtitle_text(), Qt.TextElideMode.ElideRight, int(NODE_WIDTH - 20)
        )
        painter.drawText(QRectF(10, footer_y - 2, NODE_WIDTH - 20, 14), Qt.AlignmentFlag.AlignVCenter, sub)

        # Mini L/R level bars
        bar_w = NODE_WIDTH - 20
        bar_y = h - 10.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0B0E14"))
        painter.drawRoundedRect(QRectF(10, bar_y, bar_w, 3), 1.5, 1.5)
        painter.drawRoundedRect(QRectF(10, bar_y + 4, bar_w, 3), 1.5, 1.5)

        if self._peak_l > 0.002:
            painter.setBrush(QColor("#EF4444" if self._peak_l > 0.92 else "#22C55E"))
            painter.drawRoundedRect(QRectF(10, bar_y, bar_w * self._peak_l, 3), 1.5, 1.5)
        if self._peak_r > 0.002:
            painter.setBrush(QColor("#EF4444" if self._peak_r > 0.92 else "#22C55E"))
            painter.drawRoundedRect(QRectF(10, bar_y + 4, bar_w * self._peak_r, 3), 1.5, 1.5)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            pos = self.pos()
            self.model.x = float(pos.x())
            self.model.y = float(pos.y())
            self.canvas.update_connections_for_node(self.model.id)
            self.canvas.update_groups_geometry()
        return super().itemChange(change, value)


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
            base_pen = QPen(QColor("#155E75"), 3.0)
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
        border = QColor(NODE_SELECTED_BORDER if self.isSelected() else "#475569")
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
    selection_changed = Signal(object)  # Optional[NodeModel]
    validation_error = Signal(str)

    def __init__(self, project: ProjectModel, parent=None) -> None:
        super().__init__(parent)
        self.project = project
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
        self.setAcceptDrops(True)

        self.node_items: Dict[int, NodeItem] = {}
        self.conn_items: Dict[ConnectionModel, ConnectionItem] = {}
        self.group_items: Dict[int, GroupItem] = {}

        self._panning = False
        self._pan_start = None
        self._drag_port: Optional[PortItem] = None
        self._detaching_conn: Optional[ConnectionModel] = None
        self._temp_wire: Optional[QGraphicsPathItem] = None
        self._move_snapshot: Dict[int, Tuple[float, float]] = {}
        self._dash_phase = 0.0
        self._last_meters: Dict[int, NodeMeter] = {}

        self.scene_obj.selectionChanged.connect(self._on_scene_selection_changed)
        self.graph_changed.connect(self.workspace_modified.emit)

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(66)
        self._anim_timer.timeout.connect(self._tick_animation)
        self._anim_timer.start()

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

    def zoom_in(self) -> None:
        self.scale(1.15, 1.15)

    def zoom_out(self) -> None:
        self.scale(1.0 / 1.15, 1.0 / 1.15)

    def reset_zoom(self) -> None:
        self.resetTransform()

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
        self.scale(factor, factor)

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
            if isinstance(item, NodeItem):
                return item
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
        pos = event.position().toPoint()
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
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
                self._temp_wire.setPen(QPen(QColor("#38BDF8"), 2.4, Qt.PenStyle.DashLine))
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
                    self._pan_start = pos
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)
                    event.accept()
                    return

            # Record node positions for move undo tracking
            self._move_snapshot = {
                nid: (it.model.x, it.model.y) for nid, it in self.node_items.items()
            }

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        if self._panning and self._pan_start is not None:
            delta = pos - self._pan_start
            self._pan_start = pos
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
        if self._panning:
            self._panning = False
            self._pan_start = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
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
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected_action()
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:
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
            act_del = menu.addAction("Delete Selected")
            chosen = menu.exec(event.globalPos())
            if chosen == act_group:
                self.group_selected_action()
            elif chosen == act_del:
                self.delete_selected_action()
            return

        # Empty canvas right-click quick add
        src_menu = menu.addMenu("Add Source Node")
        proc_menu = menu.addMenu("Add Processing Node")
        out_menu = menu.addMenu("Add Output Node")
        actions = {}
        from audioflow.models.graph_model import NODE_SPECS

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

