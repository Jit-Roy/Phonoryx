"""Dark professional audio-studio theme and color palette."""

from audioflow.models.graph_model import NodeCategory

CANVAS_BG = "#111318"
CANVAS_GRID_MINOR = "#1A1E27"
CANVAS_GRID_MAJOR = "#242A36"

NODE_BG = "#1B1F2A"
NODE_BORDER = "#2F3647"
NODE_SELECTED_BORDER = "#F59E0B"

CATEGORY_COLORS = {
    NodeCategory.SOURCE: "#3B82F6",      # Studio blue
    NodeCategory.PROCESSING: "#A855F7",  # DSP purple
    NodeCategory.OUTPUT: "#10B981",      # Output emerald
}

PORT_INPUT_COLOR = "#38BDF8"
PORT_OUTPUT_COLOR = "#34D399"
WIRE_IDLE_COLOR = "#64748B"
WIRE_ACTIVE_COLOR = "#22D3EE"
WIRE_SELECTED_COLOR = "#F59E0B"

STUDIO_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #14171F;
    color: #E2E8F0;
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 12px;
}
QDockWidget {
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
    color: #E2E8F0;
    font-weight: 600;
}
QDockWidget::title {
    background-color: #1B202B;
    border-bottom: 1px solid #2A3142;
    padding: 6px 10px;
}
QTabWidget::pane {
    border: 1px solid #262C3B;
    background-color: #161A23;
}
QTabBar::tab {
    background-color: #1B202B;
    color: #94A3B8;
    padding: 6px 12px;
    border: 1px solid #262C3B;
    border-bottom: none;
}
QTabBar::tab:selected {
    background-color: #232A38;
    color: #F8FAFC;
    border-bottom: 2px solid #3B82F6;
}
QToolBar {
    background-color: #181C26;
    border-bottom: 1px solid #262C3B;
    spacing: 6px;
    padding: 4px 8px;
}
QPushButton {
    background-color: #242B3A;
    color: #E2E8F0;
    border: 1px solid #333C50;
    border-radius: 4px;
    padding: 5px 10px;
}
QPushButton:hover {
    background-color: #2E374B;
    border-color: #475569;
}
QPushButton:pressed {
    background-color: #1D2330;
}
QPushButton:checked {
    background-color: #1D4ED8;
    border-color: #3B82F6;
    color: #FFFFFF;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #0F1219;
    color: #F1F5F9;
    border: 1px solid #2D3548;
    border-radius: 4px;
    padding: 4px 6px;
}
QListWidget, QTreeWidget, QTableWidget, QPlainTextEdit {
    background-color: #0F1219;
    color: #E2E8F0;
    border: 1px solid #252B3A;
    alternate-background-color: #151923;
}
QHeaderView::section {
    background-color: #1B202B;
    color: #CBD5E1;
    border: none;
    border-right: 1px solid #262C3B;
    border-bottom: 1px solid #262C3B;
    padding: 4px 8px;
}
QSlider::groove:horizontal {
    height: 6px;
    background: #252C3D;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #3B82F6;
    border: 1px solid #60A5FA;
    width: 14px;
    margin: -4px 0;
    border-radius: 7px;
}
QStatusBar {
    background-color: #11141B;
    color: #94A3B8;
    border-top: 1px solid #262C3B;
}
"""

