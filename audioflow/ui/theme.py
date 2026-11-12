"""ComfyUI-faithful dark theme palette and Qt stylesheet for AudioFlow Studio."""

from __future__ import annotations

# ComfyUI Core Dark Theme Tokens (extracted from comfyui_frontend_package/static dark palette)
COMFY_BG_COLOR = "#202020"          # --bg-color
COMFY_MENU_BG = "#171718"           # --comfy-menu-bg / --color-charcoal-800
COMFY_MENU_SECONDARY_BG = "#303030" # --comfy-menu-secondary-bg
COMFY_SURFACE_PANEL = "#202121"     # --color-charcoal-700
COMFY_SURFACE_CARD = "#262729"      # --color-charcoal-600
COMFY_SURFACE_ELEVATED = "#2D2E32"  # --color-charcoal-500
COMFY_SURFACE_HOVER = "#313235"     # --color-charcoal-400
COMFY_INPUT_BG = "#222222"          # --comfy-input-bg / WIDGET_BGCOLOR
COMFY_STROKE = "#313235"            # --interface-stroke
COMFY_BORDER = "#4E4E4E"            # --border-color
COMFY_WIDGET_OUTLINE = "#666666"    # WIDGET_OUTLINE_COLOR
COMFY_TEXT_PRIMARY = "#FFFFFF"      # --fg-color
COMFY_TEXT_INPUT = "#DDDDDD"        # WIDGET_TEXT_COLOR
COMFY_TEXT_SECONDARY = "#B2B7BD"    # WIDGET_SECONDARY_TEXT_COLOR / --descrip-text
COMFY_TEXT_MUTED = "#999999"        # NODE_TITLE_COLOR
COMFY_ACCENT_BLUE = "#0B8CE9"       # --color-azure-600
COMFY_ACCENT_BLUE_LIGHT = "#31B9F4" # --color-azure-400
COMFY_ACCENT_GREEN = "#00CD72"      # --color-jade-600
COMFY_ACCENT_RED = "#E04E48"        # --color-coral-600

# ComfyUI LiteGraph Canvas & Link Colors
CANVAS_BG = "#141414"               # CLEAR_BACKGROUND_COLOR
CANVAS_GRID_MINOR = "#1C1C1E"
CANVAS_GRID_MAJOR = "#27272A"

NODE_BG = "#353535"                 # NODE_DEFAULT_BGCOLOR
NODE_BORDER = "#4E4E4E"             # NODE_DEFAULT_BOXCOLOR / --border-color
NODE_SELECTED_BORDER = "#FFFFFF"    # NODE_BOX_OUTLINE_COLOR / --node-component-outline

# ComfyUI LGraphCanvas.node_colors header/body conventions by AudioFlow category
CATEGORY_COLORS = {
    "source": ("#2A363B", "#64B5F6"),      # pale_blue header + IMAGE blue slot
    "processing": ("#333333", "#B39DDB"),  # default ComfyUI header + MODEL violet slot
    "output": ("#223322", "#6EE7B7"),      # green header + CONTROL_NET mint slot
}

CATEGORY_BODY_COLORS = {
    "source": "#2F3D44",     # muted pale_blue body
    "processing": "#353535", # NODE_DEFAULT_BGCOLOR
    "output": "#2A3A2B",     # muted LiteGraph green body
}

PORT_INPUT_COLOR = "#64B5F6"        # ComfyUI IMAGE slot blue
PORT_OUTPUT_COLOR = "#6EE7B7"       # ComfyUI CONTROL_NET slot mint
WIRE_IDLE_COLOR = "#99AA99"         # ComfyUI LINK_COLOR (#9A9)
WIRE_ACTIVE_COLOR = "#AAFFAA"       # ComfyUI CONNECTING_LINK_COLOR (#AFA)
WIRE_SELECTED_COLOR = "#FFFFFF"     # Selected outline white

STUDIO_STYLESHEET = f"""
QMainWindow, QDialog {{
    background-color: {COMFY_BG_COLOR};
    color: {COMFY_TEXT_INPUT};
    font-family: 'Inter', 'Segoe UI', sans-serif;
    font-size: 12px;
}}

QMenuBar {{
    background-color: transparent;
    color: {COMFY_TEXT_SECONDARY};
    border: none;
    padding: 0px 4px;
    margin: 0px;
    font-size: 12px;
}}
QMenuBar::item {{
    background: transparent;
    padding: 5px 8px;
    margin: 0px;
    border-radius: 4px;
}}
QMenuBar::item:selected {{
    background-color: {COMFY_SURFACE_HOVER};
    color: {COMFY_TEXT_PRIMARY};
}}
QMenu {{
    background-color: {COMFY_MENU_BG};
    color: {COMFY_TEXT_INPUT};
    border: 1px solid {COMFY_STROKE};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 22px 6px 12px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: {COMFY_SURFACE_HOVER};
    color: {COMFY_TEXT_PRIMARY};
}}
QMenu::separator {{
    height: 1px;
    background: {COMFY_STROKE};
    margin: 4px 6px;
}}

/* ComfyUI Topbar & Workflow Tabs */
QWidget#ComfyTopBar {{
    background-color: {COMFY_MENU_BG};
    border-bottom: 1px solid {COMFY_STROKE};
}}
QTabBar#WorkflowTabs {{
    background-color: transparent;
    border: none;
}}
QTabBar#WorkflowTabs::tab {{
    background-color: transparent;
    color: {COMFY_TEXT_MUTED};
    padding: 7px 12px;
    margin: 0px;
    border: none;
    border-right: 1px solid {COMFY_STROKE};
    font-size: 12px;
    min-width: 110px;
    max-width: 220px;
}}
QTabBar#WorkflowTabs::tab:hover {{
    background-color: {COMFY_SURFACE_CARD};
    color: {COMFY_TEXT_INPUT};
}}
QTabBar#WorkflowTabs::tab:selected {{
    background-color: {COMFY_SURFACE_ELEVATED};
    color: {COMFY_TEXT_PRIMARY};
    font-weight: 600;
    border-bottom: 2px solid {COMFY_ACCENT_BLUE_LIGHT};
}}
QToolButton#NewWorkflowButton {{
    background-color: transparent;
    color: {COMFY_TEXT_SECONDARY};
    border: none;
    border-radius: 6px;
    padding: 0px;
    margin: 0px 2px;
    font-size: 15px;
    font-weight: 500;
}}
QToolButton#NewWorkflowButton:hover {{
    background-color: {COMFY_SURFACE_HOVER};
    color: {COMFY_TEXT_PRIMARY};
}}

QToolButton#ComfyIconBtn {{
    background-color: {COMFY_SURFACE_CARD};
    color: {COMFY_TEXT_INPUT};
    border: 1px solid {COMFY_STROKE};
    border-radius: 6px;
    padding: 4px 8px;
    min-height: 26px;
}}
QToolButton#ComfyIconBtn:hover {{
    background-color: {COMFY_SURFACE_HOVER};
    color: {COMFY_TEXT_PRIMARY};
}}
QToolButton#ComfyIconBtn:disabled {{
    color: #5C626D;
    background-color: {COMFY_MENU_BG};
}}

QPushButton#ComfyRunButton {{
    background-color: {COMFY_ACCENT_BLUE};
    color: #FFFFFF;
    border: 1px solid {COMFY_ACCENT_BLUE_LIGHT};
    border-radius: 6px;
    padding: 4px 14px;
    font-weight: 600;
    min-height: 26px;
}}
QPushButton#ComfyRunButton:hover {{
    background-color: #1997F2;
}}
QPushButton#ComfyRunButton[running="true"] {{
    background-color: {COMFY_ACCENT_RED};
    border-color: #F75951;
}}
QPushButton#ComfyRunButton[running="true"]:hover {{
    background-color: #F75951;
}}

/* Floating Bottom-Right Canvas Controls (GraphCanvasMenu) */
QFrame#CanvasFloatingBar {{
    background-color: {COMFY_MENU_BG};
    border: 1px solid {COMFY_STROKE};
    border-radius: 8px;
}}
QToolButton#CanvasMenuBtn {{
    background-color: transparent;
    color: {COMFY_TEXT_SECONDARY};
    border: none;
    border-radius: 5px;
    padding: 4px 7px;
    font-size: 12px;
}}
QToolButton#CanvasMenuBtn:hover {{
    background-color: {COMFY_SURFACE_HOVER};
    color: {COMFY_TEXT_PRIMARY};
}}

/* Dock Panels */
QDockWidget {{
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
    color: {COMFY_TEXT_PRIMARY};
    font-weight: 600;
}}
QDockWidget::title {{
    background-color: {COMFY_MENU_BG};
    padding: 6px 10px;
    border-bottom: 1px solid {COMFY_STROKE};
    text-align: left;
}}

QWidget#PanelBody, QWidget#BottomTerminal {{
    background-color: {COMFY_MENU_BG};
}}

QTabWidget#BottomTerminalTabs::pane {{
    border: none;
    border-top: 1px solid {COMFY_STROKE};
    background-color: {COMFY_BG_COLOR};
}}
QTabWidget#BottomTerminalTabs > QTabBar::tab {{
    background-color: transparent;
    color: {COMFY_TEXT_MUTED};
    padding: 7px 14px;
    margin: 2px 4px;
    border: none;
    border-radius: 6px;
    font-size: 12px;
}}
QTabWidget#BottomTerminalTabs > QTabBar::tab:hover {{
    background-color: {COMFY_SURFACE_CARD};
    color: {COMFY_TEXT_INPUT};
}}
QTabWidget#BottomTerminalTabs > QTabBar::tab:selected {{
    background-color: {COMFY_SURFACE_ELEVATED};
    color: {COMFY_TEXT_PRIMARY};
    font-weight: 600;
}}

QTabWidget::pane {{
    border: 1px solid {COMFY_STROKE};
    background-color: {COMFY_MENU_BG};
}}
QTabBar::tab {{
    background-color: {COMFY_SURFACE_PANEL};
    color: {COMFY_TEXT_MUTED};
    padding: 6px 12px;
    border: 1px solid {COMFY_STROKE};
    border-bottom: none;
    border-top-left-radius: 5px;
    border-top-right-radius: 5px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background-color: {COMFY_SURFACE_ELEVATED};
    color: {COMFY_TEXT_PRIMARY};
    font-weight: 600;
}}

QToolButton#TerminalCloseButton {{
    background-color: transparent;
    color: {COMFY_TEXT_MUTED};
    border: none;
    border-radius: 5px;
    font-size: 15px;
    font-weight: 600;
    padding: 2px 8px;
    margin: 2px 6px;
}}
QToolButton#TerminalCloseButton:hover {{
    background-color: {COMFY_SURFACE_HOVER};
    color: {COMFY_TEXT_PRIMARY};
}}

/* Terminal Reopen Strip / Button */
QWidget#BottomToggleStrip {{
    background-color: {COMFY_MENU_BG};
    border-top: 1px solid {COMFY_STROKE};
}}
QToolButton#TerminalToggleBtn {{
    background-color: {COMFY_SURFACE_CARD};
    color: {COMFY_TEXT_SECONDARY};
    border: 1px solid {COMFY_STROKE};
    border-radius: 5px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 500;
}}
QToolButton#TerminalToggleBtn:hover {{
    background-color: {COMFY_SURFACE_HOVER};
    color: {COMFY_TEXT_PRIMARY};
}}
QToolButton#TerminalToggleBtn:checked {{
    background-color: {COMFY_SURFACE_ELEVATED};
    color: {COMFY_TEXT_PRIMARY};
    border-color: {COMFY_ACCENT_BLUE};
}}

QListWidget, QTableWidget, QTextEdit {{
    background-color: {COMFY_BG_COLOR};
    color: {COMFY_TEXT_INPUT};
    border: 1px solid {COMFY_STROKE};
    border-radius: 5px;
}}
QListWidget::item {{
    padding: 5px 8px;
    border-radius: 4px;
}}
QListWidget::item:hover {{
    background-color: {COMFY_SURFACE_CARD};
}}
QListWidget::item:selected {{
    background-color: {COMFY_SURFACE_ELEVATED};
    color: {COMFY_TEXT_PRIMARY};
}}

QHeaderView::section {{
    background-color: {COMFY_SURFACE_CARD};
    color: {COMFY_TEXT_SECONDARY};
    padding: 4px 8px;
    border: none;
    border-right: 1px solid {COMFY_STROKE};
    border-bottom: 1px solid {COMFY_STROKE};
}}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {COMFY_INPUT_BG};
    color: {COMFY_TEXT_INPUT};
    border: 1px solid {COMFY_BORDER};
    border-radius: 4px;
    padding: 4px 6px;
}}
QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox QAbstractItemView {{
    background-color: {COMFY_MENU_BG};
    color: {COMFY_TEXT_INPUT};
    border: 1px solid {COMFY_BORDER};
    selection-background-color: {COMFY_ACCENT_BLUE};
}}

QPushButton {{
    background-color: {COMFY_SURFACE_ELEVATED};
    color: {COMFY_TEXT_INPUT};
    border: 1px solid {COMFY_BORDER};
    border-radius: 5px;
    padding: 5px 10px;
}}
QPushButton:hover {{
    background-color: {COMFY_SURFACE_HOVER};
    color: {COMFY_TEXT_PRIMARY};
}}

QSlider::groove:horizontal {{
    height: 4px;
    background: {COMFY_INPUT_BG};
    border: 1px solid {COMFY_STROKE};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {COMFY_TEXT_PRIMARY};
    border: 1px solid {COMFY_BORDER};
    width: 12px;
    height: 12px;
    margin: -5px 0;
    border-radius: 6px;
}}
QSlider::sub-page:horizontal {{
    background: {COMFY_ACCENT_BLUE};
    border-radius: 2px;
}}

QCheckBox {{
    spacing: 6px;
    color: {COMFY_TEXT_INPUT};
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border-radius: 3px;
    border: 1px solid {COMFY_BORDER};
    background: {COMFY_INPUT_BG};
}}
QCheckBox::indicator:checked {{
    background: {COMFY_ACCENT_BLUE};
    border-color: {COMFY_ACCENT_BLUE_LIGHT};
}}

QProgressBar {{
    background-color: {COMFY_INPUT_BG};
    border: 1px solid {COMFY_STROKE};
    border-radius: 3px;
    text-align: right;
    color: {COMFY_TEXT_SECONDARY};
    height: 12px;
}}
QProgressBar::chunk {{
    background-color: {COMFY_ACCENT_GREEN};
    border-radius: 2px;
}}

/* Inline ComfyUI Node Widgets hosted inside NodeItem proxy */
QWidget#NodeInlineWidgetRoot {{
    background-color: transparent;
}}
QWidget#NodeInlineWidgetRoot QLabel {{
    color: {COMFY_TEXT_SECONDARY};
    font-size: 11px;
    background-color: transparent;
}}
QWidget#NodeInlineWidgetRoot QComboBox,
QWidget#NodeInlineWidgetRoot QLineEdit {{
    background-color: {COMFY_INPUT_BG};
    color: {COMFY_TEXT_INPUT};
    border: 1px solid {COMFY_WIDGET_OUTLINE};
    border-radius: 4px;
    padding: 2px 5px;
    font-size: 11px;
    min-height: 20px;
}}
QWidget#NodeInlineWidgetRoot QPushButton {{
    background-color: {COMFY_SURFACE_ELEVATED};
    color: {COMFY_TEXT_INPUT};
    border: 1px solid {COMFY_WIDGET_OUTLINE};
    border-radius: 4px;
    padding: 2px 7px;
    font-size: 11px;
    min-height: 20px;
}}
QWidget#NodeInlineWidgetRoot QPushButton:hover {{
    background-color: {COMFY_SURFACE_HOVER};
}}
QWidget#NodeInlineWidgetRoot QCheckBox {{
    color: {COMFY_TEXT_INPUT};
    font-size: 11px;
    background-color: transparent;
}}
"""
