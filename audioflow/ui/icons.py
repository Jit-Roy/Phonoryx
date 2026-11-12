"""Colorful Microsoft OneNote-inspired vector icon system for AudioFlow Studio."""

from __future__ import annotations

from functools import lru_cache
from typing import Callable, Dict

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)

from audioflow.engine_bridge import DataFlow, NodeType
from audioflow.ui.theme import (
    NODE_ACCENT_COLORS,
    ONENOTE_BRAND_LAVENDER,
    ONENOTE_BRAND_PLUM,
    ONENOTE_BRAND_PURPLE,
    ONENOTE_BRAND_VIOLET,
)


def _pen(color: str, width: float = 1.6) -> QPen:
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _render_icon(draw_fn: Callable[[QPainter, QRectF], None], size: int = 18) -> QIcon:
    icon = QIcon()
    for scale in (1, 2):
        px = int(size * scale)
        pm = QPixmap(px, px)
        pm.fill(Qt.GlobalColor.transparent)
        pm.setDevicePixelRatio(float(scale))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        draw_fn(p, QRectF(0.0, 0.0, float(size), float(size)))
        p.end()
        icon.addPixmap(pm)
    return icon


def _draw_app_brand(p: QPainter, r: QRectF) -> None:
    w, h = r.width(), r.height()
    # OneNote-style purple spine + rounded notebook body
    spine = QRectF(w * 0.10, h * 0.10, w * 0.28, h * 0.80)
    page = QRectF(w * 0.24, h * 0.10, w * 0.66, h * 0.80)
    p.setPen(Qt.PenStyle.NoPen)
    grad = QLinearGradient(page.topLeft(), page.bottomRight())
    grad.setColorAt(0.0, QColor(ONENOTE_BRAND_VIOLET))
    grad.setColorAt(1.0, QColor(ONENOTE_BRAND_PURPLE))
    p.setBrush(grad)
    p.drawRoundedRect(page, w * 0.14, w * 0.14)

    # Colored right section strips (OneNote notebook binder tabs)
    strip_h = h * 0.18
    for idx, col in enumerate(("#0088F0", "#00C753", "#FFC114")):
        p.setBrush(QColor(col))
        p.drawRoundedRect(
            QRectF(w * 0.78, h * (0.18 + idx * 0.22), w * 0.14, strip_h),
            1.5,
            1.5,
        )

    p.setBrush(QColor(ONENOTE_BRAND_PLUM))
    p.drawRoundedRect(spine, w * 0.10, w * 0.10)

    # Crisp white-cyan waveform inside the page
    p.setPen(_pen("#FFFFFF", max(1.4, w * 0.08)))
    for x_rel, y0_rel, y1_rel in (
        (0.36, 0.42, 0.58),
        (0.47, 0.28, 0.72),
        (0.58, 0.35, 0.65),
        (0.69, 0.44, 0.56),
    ):
        p.drawLine(QPointF(w * x_rel, h * y0_rel), QPointF(w * x_rel, h * y1_rel))


def _draw_section_tab(p: QPainter, r: QRectF, color_hex: str, running: bool = False) -> None:
    w, h = r.width(), r.height()
    col = QColor(color_hex)
    p.setPen(Qt.PenStyle.NoPen)
    # Soft card sheet
    p.setBrush(QColor("#2F2B3E"))
    p.drawRoundedRect(QRectF(w * 0.16, h * 0.16, w * 0.72, h * 0.70), 2.5, 2.5)
    # Left colorful OneNote binder stripe
    p.setBrush(col)
    p.drawRoundedRect(QRectF(w * 0.14, h * 0.16, w * 0.20, h * 0.70), 2.0, 2.0)
    # Top bookmark dot / accent bar
    p.setBrush(col.lighter(120))
    p.drawRoundedRect(QRectF(w * 0.42, h * 0.34, w * 0.34, h * 0.12), 1.0, 1.0)
    p.setBrush(QColor("#B8B2CA"))
    p.drawRoundedRect(QRectF(w * 0.42, h * 0.54, w * 0.26, h * 0.10), 1.0, 1.0)
    if running:
        p.setPen(_pen("#1B1B1C", max(1.2, w * 0.08)))
        p.setBrush(QColor("#00C753"))
        p.drawEllipse(QRectF(w * 0.56, h * 0.56, w * 0.34, h * 0.34))


def _draw_new_doc(p: QPainter, r: QRectF) -> None:
    w, h = r.width(), r.height()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#0088F0"))
    p.drawRoundedRect(QRectF(w * 0.18, h * 0.12, w * 0.56, h * 0.74), 2.5, 2.5)
    p.setBrush(QColor("#8AA8E4"))
    p.drawRoundedRect(QRectF(w * 0.26, h * 0.28, w * 0.32, h * 0.10), 1.0, 1.0)
    p.setBrush(QColor("#00C753"))
    p.drawEllipse(QRectF(w * 0.50, h * 0.48, w * 0.40, h * 0.40))
    p.setPen(_pen("#FFFFFF", max(1.3, w * 0.09)))
    p.drawLine(QPointF(w * 0.70, h * 0.56), QPointF(w * 0.70, h * 0.80))
    p.drawLine(QPointF(w * 0.58, h * 0.68), QPointF(w * 0.82, h * 0.68))


def _draw_open_folder(p: QPainter, r: QRectF) -> None:
    w, h = r.width(), r.height()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#F6630D"))
    p.drawRoundedRect(QRectF(w * 0.12, h * 0.22, w * 0.36, h * 0.22), 2.0, 2.0)
    p.setBrush(QColor("#FFC114"))
    p.drawRoundedRect(QRectF(w * 0.12, h * 0.32, w * 0.76, h * 0.48), 2.5, 2.5)
    p.setBrush(QColor("#FFD869"))
    p.drawRoundedRect(QRectF(w * 0.18, h * 0.42, w * 0.64, h * 0.34), 2.0, 2.0)


def _draw_save(p: QPainter, r: QRectF, accent: str = "#0088F0") -> None:
    w, h = r.width(), r.height()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(accent))
    p.drawRoundedRect(QRectF(w * 0.14, h * 0.14, w * 0.72, h * 0.72), 2.5, 2.5)
    p.setBrush(QColor("#E8F2FF"))
    p.drawRoundedRect(QRectF(w * 0.28, h * 0.14, w * 0.38, h * 0.26), 1.0, 1.0)
    p.setBrush(QColor("#1E1C25"))
    p.drawRoundedRect(QRectF(w * 0.50, h * 0.17, w * 0.09, h * 0.18), 0.8, 0.8)
    p.setBrush(QColor("#FFC114"))
    p.drawRoundedRect(QRectF(w * 0.25, h * 0.54, w * 0.50, h * 0.28), 1.5, 1.5)


def _draw_save_as(p: QPainter, r: QRectF) -> None:
    _draw_save(p, r, "#A071EE")


def _draw_undo(p: QPainter, r: QRectF, mirror: bool = False) -> None:
    w, h = r.width(), r.height()
    p.save()
    if mirror:
        p.translate(w, 0.0)
        p.scale(-1.0, 1.0)
    color = "#32A6FF" if mirror else "#FFC114"
    p.setPen(_pen(color, max(1.6, w * 0.11)))
    path = QPainterPath(QPointF(w * 0.28, h * 0.40))
    path.cubicTo(
        QPointF(w * 0.55, h * 0.24),
        QPointF(w * 0.82, h * 0.38),
        QPointF(w * 0.76, h * 0.70),
    )
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawPolygon(
        QPolygonF([
            QPointF(w * 0.18, h * 0.42),
            QPointF(w * 0.40, h * 0.22),
            QPointF(w * 0.42, h * 0.54),
        ])
    )
    p.restore()


def _draw_refresh(p: QPainter, r: QRectF) -> None:
    w, h = r.width(), r.height()
    arc = QRectF(w * 0.18, h * 0.18, w * 0.64, h * 0.64)
    p.setPen(_pen("#1EC3B4", max(1.6, w * 0.11)))
    p.drawArc(arc, 35 * 16, 130 * 16)
    p.setPen(_pen("#00CCF9", max(1.6, w * 0.11)))
    p.drawArc(arc, 215 * 16, 130 * 16)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#1EC3B4"))
    p.drawPolygon(QPolygonF([
        QPointF(w * 0.84, h * 0.38),
        QPointF(w * 0.64, h * 0.34),
        QPointF(w * 0.78, h * 0.18),
    ]))
    p.setBrush(QColor("#00CCF9"))
    p.drawPolygon(QPolygonF([
        QPointF(w * 0.16, h * 0.62),
        QPointF(w * 0.36, h * 0.66),
        QPointF(w * 0.22, h * 0.82),
    ]))


def _draw_play(p: QPainter, r: QRectF, color: str = "#00C753") -> None:
    w, h = r.width(), r.height()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawPolygon(QPolygonF([
        QPointF(w * 0.26, h * 0.18),
        QPointF(w * 0.82, h * 0.50),
        QPointF(w * 0.26, h * 0.82),
    ]))


def _draw_stop(p: QPainter, r: QRectF, color: str = "#EC5158") -> None:
    w, h = r.width(), r.height()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(w * 0.24, h * 0.24, w * 0.52, h * 0.52), 2.2, 2.2)


def _draw_select_all(p: QPainter, r: QRectF) -> None:
    w, h = r.width(), r.height()
    p.setPen(QPen(QColor("#A071EE"), max(1.3, w * 0.08), Qt.PenStyle.DashLine))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(w * 0.16, h * 0.16, w * 0.68, h * 0.68), 2.0, 2.0)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#00CCF9"))
    p.drawRoundedRect(QRectF(w * 0.30, h * 0.30, w * 0.40, h * 0.40), 1.5, 1.5)


def _draw_group(p: QPainter, r: QRectF) -> None:
    w, h = r.width(), r.height()
    p.setPen(_pen("#7D7DF2", max(1.4, w * 0.09)))
    p.setBrush(QColor(125, 125, 242, 40))
    p.drawRoundedRect(QRectF(w * 0.14, h * 0.14, w * 0.72, h * 0.72), 3.0, 3.0)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#0088F0"))
    p.drawRoundedRect(QRectF(w * 0.24, h * 0.28, w * 0.22, h * 0.22), 1.5, 1.5)
    p.setBrush(QColor("#00C753"))
    p.drawRoundedRect(QRectF(w * 0.54, h * 0.50, w * 0.22, h * 0.22), 1.5, 1.5)


def _draw_delete(p: QPainter, r: QRectF) -> None:
    w, h = r.width(), r.height()
    p.setPen(_pen("#EC5158", max(1.4, w * 0.09)))
    p.drawLine(QPointF(w * 0.22, h * 0.28), QPointF(w * 0.78, h * 0.28))
    p.drawLine(QPointF(w * 0.38, h * 0.18), QPointF(w * 0.62, h * 0.18))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#E45383"))
    p.drawRoundedRect(QRectF(w * 0.28, h * 0.34, w * 0.44, h * 0.48), 2.0, 2.0)
    p.setPen(_pen("#1E1C25", max(1.1, w * 0.07)))
    p.drawLine(QPointF(w * 0.42, h * 0.44), QPointF(w * 0.42, h * 0.72))
    p.drawLine(QPointF(w * 0.58, h * 0.44), QPointF(w * 0.58, h * 0.72))


def _draw_zoom(p: QPainter, r: QRectF, mode: str) -> None:
    w, h = r.width(), r.height()
    col = "#00CCF9" if mode == "in" else ("#FFC114" if mode == "out" else "#B49EDE")
    p.setPen(_pen(col, max(1.4, w * 0.09)))
    p.drawEllipse(QRectF(w * 0.18, h * 0.18, w * 0.46, h * 0.46))
    p.drawLine(QPointF(w * 0.58, h * 0.58), QPointF(w * 0.82, h * 0.82))
    if mode in ("in", "out"):
        p.drawLine(QPointF(w * 0.31, h * 0.41), QPointF(w * 0.51, h * 0.41))
    if mode == "in":
        p.drawLine(QPointF(w * 0.41, h * 0.31), QPointF(w * 0.41, h * 0.51))


def _draw_terminal(p: QPainter, r: QRectF) -> None:
    w, h = r.width(), r.height()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#2F2646"))
    p.drawRoundedRect(QRectF(w * 0.10, h * 0.18, w * 0.80, h * 0.64), 2.5, 2.5)
    p.setPen(_pen("#00CCF9", max(1.4, w * 0.09)))
    p.drawLine(QPointF(w * 0.24, h * 0.36), QPointF(w * 0.40, h * 0.50))
    p.drawLine(QPointF(w * 0.40, h * 0.50), QPointF(w * 0.24, h * 0.64))
    p.setPen(_pen("#00C753", max(1.4, w * 0.09)))
    p.drawLine(QPointF(w * 0.50, h * 0.64), QPointF(w * 0.74, h * 0.64))


def _draw_palette(p: QPainter, r: QRectF) -> None:
    w, h = r.width(), r.height()
    p.setPen(Qt.PenStyle.NoPen)
    for y_rel, col in ((0.18, "#32A6FF"), (0.42, "#A071EE"), (0.66, "#00C753")):
        p.setBrush(QColor(col))
        p.drawRoundedRect(QRectF(w * 0.16, h * y_rel, w * 0.22, h * 0.16), 1.5, 1.5)
        p.setBrush(QColor("#CFC8E0"))
        p.drawRoundedRect(QRectF(w * 0.44, h * (y_rel + 0.02), w * 0.40, h * 0.12), 1.5, 1.5)


def _draw_tab_nodes(p: QPainter, r: QRectF) -> None:
    w, h = r.width(), r.height()
    p.setPen(_pen("#B49EDE", max(1.2, w * 0.08)))
    p.drawLine(QPointF(w * 0.28, h * 0.30), QPointF(w * 0.70, h * 0.50))
    p.drawLine(QPointF(w * 0.28, h * 0.70), QPointF(w * 0.70, h * 0.50))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#32A6FF"))
    p.drawEllipse(QRectF(w * 0.14, h * 0.18, w * 0.24, h * 0.24))
    p.setBrush(QColor("#A071EE"))
    p.drawEllipse(QRectF(w * 0.14, h * 0.58, w * 0.24, h * 0.24))
    p.setBrush(QColor("#00C753"))
    p.drawEllipse(QRectF(w * 0.60, h * 0.38, w * 0.26, h * 0.26))


def _draw_tab_devices(p: QPainter, r: QRectF) -> None:
    w, h = r.width(), r.height()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#1EC3B4"))
    p.drawRoundedRect(QRectF(w * 0.16, h * 0.22, w * 0.34, h * 0.56), 2.5, 2.5)
    p.setBrush(QColor("#FFC114"))
    p.drawEllipse(QRectF(w * 0.24, h * 0.46, w * 0.18, h * 0.18))
    p.setPen(_pen("#32A6FF", max(1.3, w * 0.08)))
    p.drawArc(QRectF(w * 0.44, h * 0.30, w * 0.34, h * 0.40), -55 * 16, 110 * 16)
    p.drawArc(QRectF(w * 0.52, h * 0.20, w * 0.36, h * 0.60), -50 * 16, 100 * 16)


def _draw_tab_apps(p: QPainter, r: QRectF) -> None:
    w, h = r.width(), r.height()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#6462E4"))
    p.drawRoundedRect(QRectF(w * 0.14, h * 0.16, w * 0.72, h * 0.68), 2.5, 2.5)
    p.setBrush(QColor("#A071EE"))
    p.drawRoundedRect(QRectF(w * 0.14, h * 0.16, w * 0.72, h * 0.18), 2.0, 2.0)
    p.setBrush(QColor("#FFC114"))
    p.drawEllipse(QRectF(w * 0.34, h * 0.58, w * 0.18, h * 0.16))
    p.setPen(_pen("#FFC114", max(1.4, w * 0.08)))
    p.drawLine(QPointF(w * 0.50, h * 0.64), QPointF(w * 0.50, h * 0.38))
    p.drawLine(QPointF(w * 0.50, h * 0.38), QPointF(w * 0.66, h * 0.44))


def _draw_tab_meters(p: QPainter, r: QRectF) -> None:
    w, h = r.width(), r.height()
    p.setPen(Qt.PenStyle.NoPen)
    for x_rel, top_rel, col in (
        (0.18, 0.46, "#00C753"),
        (0.38, 0.24, "#1EC3B4"),
        (0.58, 0.34, "#FFC114"),
        (0.76, 0.18, "#EC5158"),
    ):
        p.setBrush(QColor(col))
        p.drawRoundedRect(QRectF(w * x_rel, h * top_rel, w * 0.14, h * (0.86 - top_rel)), 1.5, 1.5)


def _draw_tab_connections(p: QPainter, r: QRectF) -> None:
    w, h = r.width(), r.height()
    p.setPen(_pen("#00CCF9", max(1.5, w * 0.09)))
    path = QPainterPath(QPointF(w * 0.22, h * 0.32))
    path.cubicTo(QPointF(w * 0.55, h * 0.32), QPointF(w * 0.45, h * 0.68), QPointF(w * 0.78, h * 0.68))
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#32A6FF"))
    p.drawEllipse(QRectF(w * 0.12, h * 0.22, w * 0.20, h * 0.20))
    p.setBrush(QColor("#00C753"))
    p.drawEllipse(QRectF(w * 0.68, h * 0.58, w * 0.20, h * 0.20))


def _draw_tab_engine(p: QPainter, r: QRectF) -> None:
    w, h = r.width(), r.height()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#A071EE"))
    p.drawRoundedRect(QRectF(w * 0.24, h * 0.24, w * 0.52, h * 0.52), 2.5, 2.5)
    p.setBrush(QColor("#FFC114"))
    p.drawEllipse(QRectF(w * 0.38, h * 0.38, w * 0.24, h * 0.24))
    p.setPen(_pen("#1EC3B4", max(1.2, w * 0.07)))
    for pos in (0.36, 0.64):
        p.drawLine(QPointF(w * pos, h * 0.12), QPointF(w * pos, h * 0.22))
        p.drawLine(QPointF(w * pos, h * 0.78), QPointF(w * pos, h * 0.88))
        p.drawLine(QPointF(w * 0.12, h * pos), QPointF(w * 0.22, h * pos))
        p.drawLine(QPointF(w * 0.78, h * pos), QPointF(w * 0.88, h * pos))


def _draw_tab_logs(p: QPainter, r: QRectF) -> None:
    w, h = r.width(), r.height()
    p.setPen(Qt.PenStyle.NoPen)
    for y_rel, w_rel, col in (
        (0.24, 0.68, "#00CCF9"),
        (0.46, 0.52, "#B49EDE"),
        (0.68, 0.62, "#7EC400"),
    ):
        p.setBrush(QColor(col))
        p.drawRoundedRect(QRectF(w * 0.16, h * y_rel, w * w_rel, h * 0.13), 1.5, 1.5)


def draw_node_type_icon(p: QPainter, rect: QRectF, node_type: NodeType) -> None:
    """Draw a miniature colorful vector badge representing ``node_type`` inside ``rect``."""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    accent = NODE_ACCENT_COLORS.get(node_type, ONENOTE_BRAND_VIOLET)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    if node_type == NodeType.APP_CAPTURE:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(accent))
        p.drawRoundedRect(QRectF(x + w * 0.12, y + h * 0.16, w * 0.76, h * 0.68), 2.5, 2.5)
        p.setBrush(QColor("#00CCF9"))
        p.drawEllipse(QRectF(x + w * 0.30, y + h * 0.50, w * 0.20, h * 0.20))
        p.setPen(_pen("#FFFFFF", max(1.2, w * 0.08)))
        p.drawLine(QPointF(x + w * 0.48, y + h * 0.58), QPointF(x + w * 0.48, y + h * 0.30))
        p.drawLine(QPointF(x + w * 0.48, y + h * 0.30), QPointF(x + w * 0.68, y + h * 0.36))

    elif node_type == NodeType.SYSTEM_CAPTURE:
        p.setPen(_pen(accent, max(1.3, w * 0.09)))
        p.setBrush(QColor("#1B2D48"))
        p.drawRoundedRect(QRectF(x + w * 0.12, y + h * 0.18, w * 0.76, h * 0.50), 2.0, 2.0)
        p.drawLine(QPointF(x + w * 0.34, y + h * 0.80), QPointF(x + w * 0.66, y + h * 0.80))
        p.setPen(_pen("#00C753", max(1.3, w * 0.08)))
        p.drawLine(QPointF(x + w * 0.32, y + h * 0.36), QPointF(x + w * 0.32, y + h * 0.50))
        p.drawLine(QPointF(x + w * 0.50, y + h * 0.28), QPointF(x + w * 0.50, y + h * 0.56))
        p.drawLine(QPointF(x + w * 0.68, y + h * 0.34), QPointF(x + w * 0.68, y + h * 0.52))

    elif node_type in (NodeType.MIC_INPUT, NodeType.VIRTUAL_INPUT):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(accent))
        p.drawRoundedRect(QRectF(x + w * 0.34, y + h * 0.14, w * 0.32, h * 0.46), 3.0, 3.0)
        p.setPen(_pen("#FFC114" if node_type == NodeType.MIC_INPUT else "#00CCF9", max(1.3, w * 0.08)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(x + w * 0.24, y + h * 0.28, w * 0.52, h * 0.42), 180 * 16, 180 * 16)
        p.drawLine(QPointF(x + w * 0.50, y + h * 0.70), QPointF(x + w * 0.50, y + h * 0.84))
        p.drawLine(QPointF(x + w * 0.34, y + h * 0.84), QPointF(x + w * 0.66, y + h * 0.84))

    elif node_type == NodeType.VOLUME:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(accent))
        p.drawPolygon(QPolygonF([
            QPointF(x + w * 0.16, y + h * 0.38),
            QPointF(x + w * 0.34, y + h * 0.38),
            QPointF(x + w * 0.54, y + h * 0.20),
            QPointF(x + w * 0.54, y + h * 0.80),
            QPointF(x + w * 0.34, y + h * 0.62),
            QPointF(x + w * 0.16, y + h * 0.62),
        ]))
        p.setPen(_pen("#00CCF9", max(1.3, w * 0.08)))
        p.drawArc(QRectF(x + w * 0.48, y + h * 0.30, w * 0.26, h * 0.40), -55 * 16, 110 * 16)
        p.drawArc(QRectF(x + w * 0.54, y + h * 0.20, w * 0.34, h * 0.60), -50 * 16, 100 * 16)

    elif node_type == NodeType.GAIN:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(accent))
        p.drawPolygon(QPolygonF([
            QPointF(x + w * 0.56, y + h * 0.12),
            QPointF(x + w * 0.26, y + h * 0.54),
            QPointF(x + w * 0.48, y + h * 0.54),
            QPointF(x + w * 0.40, y + h * 0.88),
            QPointF(x + w * 0.74, y + h * 0.44),
            QPointF(x + w * 0.52, y + h * 0.44),
        ]))

    elif node_type == NodeType.MIXER:
        p.setPen(_pen("#B49EDE", max(1.2, w * 0.08)))
        for idx, (xr, yr, col) in enumerate((
            (0.24, 0.34, "#32A6FF"),
            (0.50, 0.62, accent),
            (0.76, 0.42, "#00C753"),
        )):
            p.setPen(_pen("#7D7594", max(1.2, w * 0.07)))
            p.drawLine(QPointF(x + w * xr, y + h * 0.16), QPointF(x + w * xr, y + h * 0.84))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(col))
            p.drawEllipse(QRectF(x + w * (xr - 0.10), y + h * (yr - 0.10), w * 0.20, h * 0.20))

    elif node_type == NodeType.SPLITTER:
        p.setPen(_pen(accent, max(1.4, w * 0.09)))
        p.drawLine(QPointF(x + w * 0.16, y + h * 0.50), QPointF(x + w * 0.44, y + h * 0.50))
        p.drawLine(QPointF(x + w * 0.44, y + h * 0.50), QPointF(x + w * 0.76, y + h * 0.26))
        p.drawLine(QPointF(x + w * 0.44, y + h * 0.50), QPointF(x + w * 0.76, y + h * 0.74))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FFC114"))
        p.drawEllipse(QRectF(x + w * 0.70, y + h * 0.18, w * 0.16, h * 0.16))
        p.setBrush(QColor("#00C753"))
        p.drawEllipse(QRectF(x + w * 0.70, y + h * 0.66, w * 0.16, h * 0.16))

    elif node_type == NodeType.MUTE:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(accent))
        p.drawPolygon(QPolygonF([
            QPointF(x + w * 0.16, y + h * 0.38),
            QPointF(x + w * 0.34, y + h * 0.38),
            QPointF(x + w * 0.54, y + h * 0.20),
            QPointF(x + w * 0.54, y + h * 0.80),
            QPointF(x + w * 0.34, y + h * 0.62),
            QPointF(x + w * 0.16, y + h * 0.62),
        ]))
        p.setPen(_pen("#FFFFFF", max(1.4, w * 0.09)))
        p.drawLine(QPointF(x + w * 0.66, y + h * 0.34), QPointF(x + w * 0.84, y + h * 0.66))
        p.drawLine(QPointF(x + w * 0.84, y + h * 0.34), QPointF(x + w * 0.66, y + h * 0.66))

    elif node_type in (NodeType.STEREO_TO_MONO, NodeType.MONO_TO_STEREO):
        p.setPen(_pen(accent, max(1.4, w * 0.09)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(x + w * 0.14, y + h * 0.28, w * 0.44, h * 0.44))
        p.setPen(_pen("#00CCF9", max(1.4, w * 0.09)))
        p.drawEllipse(QRectF(x + w * 0.42, y + h * 0.28, w * 0.44, h * 0.44))

    elif node_type == NodeType.CHANNEL_MAP:
        p.setPen(_pen(accent, max(1.4, w * 0.09)))
        p.drawLine(QPointF(x + w * 0.18, y + h * 0.32), QPointF(x + w * 0.82, y + h * 0.68))
        p.setPen(_pen("#32A6FF", max(1.4, w * 0.09)))
        p.drawLine(QPointF(x + w * 0.18, y + h * 0.68), QPointF(x + w * 0.82, y + h * 0.32))

    elif node_type == NodeType.EQUALIZER:
        p.setPen(Qt.PenStyle.NoPen)
        for xr, top_rel, col in (
            (0.14, 0.48, "#32A6FF"),
            (0.33, 0.22, "#A071EE"),
            (0.52, 0.38, "#FFC114"),
            (0.71, 0.26, "#00C753"),
        ):
            p.setBrush(QColor(col))
            p.drawRoundedRect(QRectF(x + w * xr, y + h * top_rel, w * 0.14, h * (0.86 - top_rel)), 1.2, 1.2)

    elif node_type in (NodeType.SPEAKER_OUTPUT, NodeType.VIRTUAL_OUTPUT):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(accent))
        p.drawRoundedRect(QRectF(x + w * 0.20, y + h * 0.14, w * 0.60, h * 0.72), 2.5, 2.5)
        p.setBrush(QColor("#1E1C25"))
        p.drawEllipse(QRectF(x + w * 0.32, y + h * 0.40, w * 0.36, h * 0.36))
        p.setBrush(QColor("#FFC114" if node_type == NodeType.SPEAKER_OUTPUT else "#00CCF9"))
        p.drawEllipse(QRectF(x + w * 0.42, y + h * 0.50, w * 0.16, h * 0.16))

    elif node_type == NodeType.FILE_RECORDING:
        p.setPen(_pen("#EC5158", max(1.3, w * 0.09)))
        p.setBrush(QColor("#2F1B24"))
        p.drawEllipse(QRectF(x + w * 0.16, y + h * 0.16, w * 0.68, h * 0.68))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#EC5158"))
        p.drawEllipse(QRectF(x + w * 0.34, y + h * 0.34, w * 0.32, h * 0.32))

    p.restore()


_STATIC_DRAWERS: Dict[str, Callable[[QPainter, QRectF], None]] = {
    "app_brand": _draw_app_brand,
    "new_workflow": _draw_new_doc,
    "open_file": _draw_open_folder,
    "save_file": _draw_save,
    "save_as": _draw_save_as,
    "undo": lambda p, r: _draw_undo(p, r, False),
    "redo": lambda p, r: _draw_undo(p, r, True),
    "refresh": _draw_refresh,
    "play": _draw_play,
    "stop": _draw_stop,
    "play_btn": lambda p, r: _draw_play(p, r, "#FFFFFF"),
    "stop_btn": lambda p, r: _draw_stop(p, r, "#FFFFFF"),
    "select_all": _draw_select_all,
    "group": _draw_group,
    "delete": _draw_delete,
    "zoom_in": lambda p, r: _draw_zoom(p, r, "in"),
    "zoom_out": lambda p, r: _draw_zoom(p, r, "out"),
    "zoom_fit": lambda p, r: _draw_zoom(p, r, "fit"),
    "terminal": _draw_terminal,
    "palette": _draw_palette,
    "tab_nodes": _draw_tab_nodes,
    "tab_devices": _draw_tab_devices,
    "tab_apps": _draw_tab_apps,
    "tab_meters": _draw_tab_meters,
    "tab_connections": _draw_tab_connections,
    "tab_engine": _draw_tab_engine,
    "tab_logs": _draw_tab_logs,
}


@lru_cache(maxsize=96)
def ui_icon(name: str, size: int = 18) -> QIcon:
    """Return a cached colorful HiDPI QIcon for a named UI action or tab."""
    drawer = _STATIC_DRAWERS.get(name)
    if drawer is None:
        return QIcon()
    return _render_icon(drawer, size)


@lru_cache(maxsize=64)
def workflow_tab_icon(color_hex: str, size: int = 15, running: bool = False) -> QIcon:
    """Return a OneNote-section-colored page tab icon for workflow tabs."""
    return _render_icon(lambda p, r: _draw_section_tab(p, r, color_hex, running=running), size)


@lru_cache(maxsize=32)
def node_type_icon(node_type: NodeType, size: int = 16) -> QIcon:
    """Return a cached colorful HiDPI QIcon for ``node_type``."""
    return _render_icon(lambda p, r: draw_node_type_icon(p, r, node_type), size)


@lru_cache(maxsize=16)
def device_icon(is_capture: bool, is_virtual: bool, size: int = 16) -> QIcon:
    """Return a colorful device item icon matching capture/render and physical/virtual."""
    if is_capture:
        nt = NodeType.VIRTUAL_INPUT if is_virtual else NodeType.MIC_INPUT
    else:
        nt = NodeType.VIRTUAL_OUTPUT if is_virtual else NodeType.SPEAKER_OUTPUT
    return node_type_icon(nt, size)


@lru_cache(maxsize=8)
def app_session_icon(has_audio: bool, size: int = 16) -> QIcon:
    """Return a colorful icon for running app items in the Apps palette."""
    def _draw(p: QPainter, r: QRectF) -> None:
        w, h = r.width(), r.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#A071EE" if has_audio else "#5C5872"))
        p.drawRoundedRect(QRectF(w * 0.12, h * 0.16, w * 0.76, h * 0.68), 2.5, 2.5)
        if has_audio:
            p.setBrush(QColor("#00C753"))
            p.drawEllipse(QRectF(w * 0.28, h * 0.50, w * 0.22, h * 0.22))
            p.setPen(_pen("#FFC114", max(1.3, w * 0.08)))
            p.drawLine(QPointF(w * 0.48, y_top := h * 0.30), QPointF(w * 0.48, h * 0.60))
            p.drawLine(QPointF(w * 0.48, y_top), QPointF(w * 0.68, h * 0.38))
        else:
            p.setBrush(QColor("#B49EDE"))
            p.drawRoundedRect(QRectF(w * 0.24, h * 0.36, w * 0.52, h * 0.14), 1.0, 1.0)
            p.drawRoundedRect(QRectF(w * 0.24, h * 0.56, w * 0.34, h * 0.12), 1.0, 1.0)

    return _render_icon(_draw, size)

