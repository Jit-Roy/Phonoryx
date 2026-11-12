"""Atomic ComfyUI-style draggable workflow tab strip."""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QFont, QFontMetrics, QIcon
from shiboken6 import isValid
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTabBar,
    QToolButton,
    QWidget,
)


class _WorkflowTabItem(QFrame):
    """Single atomic tab containing [icon] [title] [close button] so all parts move together."""

    def __init__(self, bar: "WorkflowTabBar", icon: QIcon, text: str) -> None:
        super().__init__(bar)
        self._bar = bar
        self._icon = icon
        self._text = text
        self._selected = False

        self.setObjectName("WorkflowTabItem")
        self.setProperty("selected", False)
        self.setFixedHeight(32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 8, 0)
        lay.setSpacing(6)

        self.lbl_icon = QLabel(self)
        self.lbl_icon.setFixedSize(bar.iconSize())
        self.lbl_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.lbl_icon, 0, Qt.AlignmentFlag.AlignVCenter)

        self.lbl_title = QLabel(text, self)
        self.lbl_title.setObjectName("WorkflowTabTitle")
        self.lbl_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.lbl_title, 1)

        self._close_btn: Optional[QToolButton] = None
        self.set_icon(icon)
        self.set_text(text)
        self.installEventFilter(bar)

    def set_icon(self, icon: QIcon) -> None:
        self._icon = icon
        sz = self._bar.iconSize()
        self.lbl_icon.setFixedSize(sz)
        self.lbl_icon.setPixmap(icon.pixmap(sz))

    def icon(self) -> QIcon:
        return self._icon

    def set_text(self, text: str) -> None:
        self._text = text
        self.lbl_title.setText(text)
        self.setToolTip(text)
        self.update_width()

    def text(self) -> str:
        return self._text

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self.setProperty("selected", selected)
        self.lbl_title.setProperty("selected", selected)
        for w in (self, self.lbl_title):
            w.style().unpolish(w)
            w.style().polish(w)
        self.update_width()

    def set_close_button(self, btn: Optional[QToolButton]) -> None:
        lay = self.layout()
        if self._close_btn is not None and self._close_btn is not btn:
            if lay is not None:
                lay.removeWidget(self._close_btn)
            self._close_btn.deleteLater()
            self._close_btn = None
        if btn is not None:
            btn.setParent(self)
            btn.setCursor(Qt.CursorShape.ArrowCursor)
            if lay is not None:
                lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)
            btn.show()
            self._close_btn = btn
        self.update_width()

    def close_button(self) -> Optional[QToolButton]:
        return self._close_btn

    def preferred_width(self) -> int:
        font = QFont(self.lbl_title.font())
        font.setWeight(QFont.Weight.DemiBold if self._selected else QFont.Weight.Normal)
        fm = QFontMetrics(font)
        text_w = fm.horizontalAdvance(self._text or "Untitled")
        icon_w = self._bar.iconSize().width()
        close_w = self._close_btn.sizeHint().width() if self._close_btn is not None else 0
        spacing_count = 2 if self._close_btn is not None else 1
        total = 18 + icon_w + text_w + close_w + spacing_count * 6
        return max(118, min(240, total))

    def update_width(self) -> None:
        w = self.preferred_width()
        self.setFixedWidth(w)


class WorkflowTabBar(QWidget):
    """Draggable tab bar where each tab's icon, label, and close button move atomically."""

    currentChanged = Signal(int)
    tabMoved = Signal(int, int)
    tabBarDoubleClicked = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("WorkflowTabs")
        self._items: List[_WorkflowTabItem] = []
        self._current_index: int = -1
        self._icon_size = QSize(14, 14)

        self._dragging_item: Optional[_WorkflowTabItem] = None
        self._drag_active: bool = False
        self._drag_press_pos = QPoint()
        self._drag_offset_x: int = 0
        self._anims: Dict[_WorkflowTabItem, QPropertyAnimation] = {}

        self.setFixedHeight(32)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    # ----- QTabBar-compatible API -----

    def count(self) -> int:
        return len(self._items)

    def currentIndex(self) -> int:
        return self._current_index

    def setCurrentIndex(self, index: int) -> None:
        if not (0 <= index < len(self._items)):
            return
        changed = index != self._current_index
        self._current_index = index
        self._sync_selection_styles()
        self._relayout(animate=False)
        if changed and not self.signalsBlocked():
            self.currentChanged.emit(index)

    def setIconSize(self, size: QSize) -> None:
        self._icon_size = QSize(size)
        for item in self._items:
            item.set_icon(item.icon())
        self._relayout(animate=False)

    def iconSize(self) -> QSize:
        return QSize(self._icon_size)

    def addTab(self, icon: QIcon, text: str) -> int:
        item = _WorkflowTabItem(self, icon, text)
        self._items.append(item)
        idx = len(self._items) - 1
        item.show()
        if len(self._items) == 1:
            self._current_index = 0
        self._sync_selection_styles()
        self._relayout(animate=False)
        return idx

    def setTabButton(
        self,
        index: int,
        position: QTabBar.ButtonPosition,
        widget: Optional[QToolButton],
    ) -> None:
        _ = position
        if 0 <= index < len(self._items):
            self._items[index].set_close_button(widget)
            self._relayout(animate=False)

    def tabButton(
        self, index: int, position: QTabBar.ButtonPosition
    ) -> Optional[QToolButton]:
        _ = position
        if 0 <= index < len(self._items):
            return self._items[index].close_button()
        return None

    def setTabIcon(self, index: int, icon: QIcon) -> None:
        if 0 <= index < len(self._items):
            self._items[index].set_icon(icon)

    def tabIcon(self, index: int) -> QIcon:
        if 0 <= index < len(self._items):
            return self._items[index].icon()
        return QIcon()

    def setTabText(self, index: int, text: str) -> None:
        if 0 <= index < len(self._items):
            self._items[index].set_text(text)
            self._relayout(animate=False)

    def tabText(self, index: int) -> str:
        if 0 <= index < len(self._items):
            return self._items[index].text()
        return ""

    def tabRect(self, index: int) -> QRect:
        if 0 <= index < len(self._items):
            return self._items[index].geometry()
        return QRect()

    def removeTab(self, index: int) -> None:
        if not (0 <= index < len(self._items)):
            return
        item = self._items.pop(index)
        anim = self._anims.pop(item, None)
        if anim is not None and isValid(anim):
            anim.stop()
        if self._dragging_item is item:
            self._dragging_item = None
            self._drag_active = False
        item.removeEventFilter(self)
        item.hide()
        item.deleteLater()

        if not self._items:
            self._current_index = -1
            self._relayout(animate=False)
            return

        was_current = index == self._current_index
        if was_current:
            self._current_index = min(index, len(self._items) - 1)
        elif index < self._current_index:
            self._current_index -= 1

        self._sync_selection_styles()
        self._relayout(animate=False)
        if was_current and not self.signalsBlocked():
            self.currentChanged.emit(self._current_index)

    def moveTab(self, from_idx: int, to_idx: int) -> None:
        if (
            from_idx == to_idx
            or not (0 <= from_idx < len(self._items))
            or not (0 <= to_idx < len(self._items))
        ):
            return
        active_item = (
            self._items[self._current_index]
            if 0 <= self._current_index < len(self._items)
            else None
        )
        moved = self._items.pop(from_idx)
        self._items.insert(to_idx, moved)
        if active_item is not None:
            self._current_index = self._items.index(active_item)
        self._relayout(animate=False)
        if not self.signalsBlocked():
            self.tabMoved.emit(from_idx, to_idx)

    # ----- Sizing & Atomic Layout -----

    def sizeHint(self) -> QSize:
        total_w = sum(item.width() for item in self._items)
        return QSize(max(1, total_w), 32)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _sync_selection_styles(self) -> None:
        for i, item in enumerate(self._items):
            item.set_selected(i == self._current_index)

    def _target_positions(self) -> List[int]:
        xs: List[int] = []
        x = 0
        for item in self._items:
            xs.append(x)
            x += item.width()
        return xs

    def _relayout(self, animate: bool = False) -> None:
        xs = self._target_positions()
        total_w = xs[-1] + self._items[-1].width() if self._items else 0
        self.setFixedWidth(max(1, total_w))
        for item, target_x in zip(self._items, xs):
            if item is self._dragging_item and self._drag_active:
                continue
            target_pos = QPoint(target_x, 0)
            prev = self._anims.pop(item, None)
            if prev is not None and isValid(prev):
                prev.stop()
            if animate and item.pos() != target_pos:
                anim = QPropertyAnimation(item, b"pos", self)
                anim.setDuration(110)
                anim.setStartValue(item.pos())
                anim.setEndValue(target_pos)
                anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                self._anims[item] = anim
                anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
            else:
                item.move(target_pos)
        self.updateGeometry()

    # ----- Unified Mouse & Drag Handling -----

    def _item_at_x(self, x: int) -> int:
        for i, item in enumerate(self._items):
            if item.x() <= x < item.x() + item.width():
                return i
        return -1

    def _handle_press(self, bar_pos: QPoint) -> bool:
        idx = self._item_at_x(bar_pos.x())
        if idx < 0:
            return False
        item = self._items[idx]
        self._dragging_item = item
        self._drag_active = False
        self._drag_press_pos = bar_pos
        self._drag_offset_x = item.x() - bar_pos.x()
        self.setCurrentIndex(idx)
        return True

    def _handle_move(self, bar_pos: QPoint) -> bool:
        item = self._dragging_item
        if item is None or item not in self._items:
            return False
        if not self._drag_active:
            if abs(bar_pos.x() - self._drag_press_pos.x()) < QApplication.startDragDistance():
                return True
            self._drag_active = True
            prev = self._anims.pop(item, None)
            if prev is not None and isValid(prev):
                prev.stop()
            item.raise_()

        total_w = sum(it.width() for it in self._items)
        max_x = max(0, total_w - item.width())
        new_x = max(0, min(max_x, bar_pos.x() + self._drag_offset_x))
        item.move(new_x, 0)

        cur_idx = self._items.index(item)
        xs = self._target_positions()

        target_idx = cur_idx
        if cur_idx > 0:
            left_item = self._items[cur_idx - 1]
            left_mid = xs[cur_idx - 1] + left_item.width() * 0.5
            if new_x < left_mid - 2.0:
                target_idx = cur_idx - 1
        if target_idx == cur_idx and cur_idx < len(self._items) - 1:
            right_item = self._items[cur_idx + 1]
            right_mid = xs[cur_idx + 1] + right_item.width() * 0.5
            if new_x + item.width() > right_mid + 2.0:
                target_idx = cur_idx + 1

        if target_idx != cur_idx:
            active_item = (
                self._items[self._current_index]
                if 0 <= self._current_index < len(self._items)
                else None
            )
            self._items.pop(cur_idx)
            self._items.insert(target_idx, item)
            if active_item is not None:
                self._current_index = self._items.index(active_item)
            self._relayout(animate=True)
            item.raise_()
            if not self.signalsBlocked():
                self.tabMoved.emit(cur_idx, target_idx)
        return True

    def _handle_release(self, _bar_pos: QPoint) -> bool:
        had_item = self._dragging_item is not None
        self._dragging_item = None
        self._drag_active = False
        if had_item:
            # Snap immediately so all tabs (including close buttons & icons) settle in final geometry
            self._relayout(animate=False)
        return had_item

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if isinstance(obj, _WorkflowTabItem) and obj in self._items:
            etype = event.type()
            if etype == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                if self._handle_press(obj.mapTo(self, event.position().toPoint())):
                    event.accept()
                    return True
            elif etype == QEvent.Type.MouseMove and (event.buttons() & Qt.MouseButton.LeftButton):
                if self._handle_move(obj.mapTo(self, event.position().toPoint())):
                    event.accept()
                    return True
            elif etype == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                if self._handle_release(obj.mapTo(self, event.position().toPoint())):
                    event.accept()
                    return True
            elif etype == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                idx = self._items.index(obj)
                self.tabBarDoubleClicked.emit(idx)
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if self._handle_press(event.position().toPoint()):
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.MouseButton.LeftButton:
            if self._handle_move(event.position().toPoint()):
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if self._handle_release(event.position().toPoint()):
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self._item_at_x(event.position().toPoint().x())
            if idx >= 0:
                self.tabBarDoubleClicked.emit(idx)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)
