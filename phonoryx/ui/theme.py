"""Microsoft OneNote Fluent Dark theme palette and Qt stylesheet for Phonoryx Studio.

Color tokens align with OneNote / Office Fluent dark surfaces (#141414, #1F1F1F,
#242424, #292929), OneNote plum/purple brand accents (#7719AA, #80397B, #A071EE),
and the 16-section color palette extracted from OneNote's shipped resources.
"""

from __future__ import annotations

from phonoryx.engine_bridge import NodeType

# OneNote Brand & Fluent Purple Accents
ONENOTE_BRAND_PURPLE = "#7719AA"
ONENOTE_BRAND_PLUM = "#80397B"
ONENOTE_BRAND_VIOLET = "#A071EE"
ONENOTE_BRAND_IRIS = "#6462E4"
ONENOTE_BRAND_LAVENDER = "#B49EDE"
ONENOTE_BRAND_DEEP_PLUM = "#5B2D90"

# OneNote 16-Section Vibrant & Pastel Accent Swatches (from onmain.DLL ColorMap/ThemeColors)
ONENOTE_SECTION_COLORS = (
    "#A071EE",  # 0: OneNote Violet
    "#0088F0",  # 1: Blue
    "#00C753",  # 2: Emerald Green
    "#FFC114",  # 3: Gold
    "#1EC3B4",  # 4: Teal
    "#EC5158",  # 5: Coral Red
    "#B973D3",  # 6: Orchid
    "#F6630D",  # 7: Orange
    "#32A6FF",  # 8: Azure
    "#7EC400",  # 9: Lime
    "#E45383",  # 10: Rose
    "#00A0D7",  # 11: Cyan
    "#C6A377",  # 12: Tan / Copper
    "#7D7DF2",  # 13: Periwinkle
    "#009E42",  # 14: Forest Green
    "#8AA8E4",  # 15: Mist Blue
)

# OneNote Fluent Dark Neutral Surfaces (pure black / charcoal / grey / white — no purple wash)
COMFY_BG_COLOR = "#141414"          # Primary window / workspace background
COMFY_MENU_BG = "#1B1B1B"           # Top command chrome, menus, dock title bars
COMFY_MENU_SECONDARY_BG = "#252525" # Secondary toolbar / separator fill
COMFY_SURFACE_PANEL = "#1E1E1E"     # Dock panel body surface
COMFY_SURFACE_CARD = "#252525"      # Cards, unselected interactive chips
COMFY_SURFACE_ELEVATED = "#2E2E2E"  # Active tab, pressed/elevated controls
COMFY_SURFACE_HOVER = "#383838"     # Hover state surface
COMFY_INPUT_BG = "#161616"          # Text boxes, combo inputs, slider wells
COMFY_STROKE = "#2D2D2D"            # Panel dividers & hairline borders
COMFY_BORDER = "#424242"            # Control borders
COMFY_WIDGET_OUTLINE = "#595959"    # Inline widget outlines on canvas cards
COMFY_TEXT_PRIMARY = "#FFFFFF"      # Primary high-contrast foreground
COMFY_TEXT_INPUT = "#EDEDED"        # Clean light grey/white UI text
COMFY_TEXT_SECONDARY = "#BCBCBC"    # Muted labels, port names
COMFY_TEXT_MUTED = "#8A8A8A"        # Inactive tabs, secondary metadata
COMFY_ACCENT_BLUE = "#0078D4"       # Fluent neutral accent
COMFY_ACCENT_BLUE_LIGHT = "#32A6FF" # Subtle focus / active underline accent
COMFY_ACCENT_AZURE = "#0088F0"
COMFY_ACCENT_GREEN = "#00C753"
COMFY_ACCENT_RED = "#EC5158"
COMFY_ACCENT_GOLD = "#FFC114"
COMFY_ACCENT_TEAL = "#1EC3B4"
COMFY_ACCENT_CYAN = "#00CCF9"

# Infinite Patchbay Canvas & Grid Colors (true neutral charcoal like OneNote dark page)
CANVAS_BG = "#121212"
CANVAS_GRID_MINOR = "#1A1A1A"
CANVAS_GRID_MAJOR = "#242424"

NODE_BG = "#232323"
NODE_BORDER = "#3C3C3C"
NODE_SELECTED_BORDER = "#E5E5E5"

# Neutral dark card surfaces across categories (color comes from section stripes & icons)
CATEGORY_COLORS = {
    "source": ("#292929", "#32A6FF"),
    "processing": ("#292929", "#FFC114"),
    "output": ("#292929", "#00C753"),
}

CATEGORY_BODY_COLORS = {
    "source": "#212121",
    "processing": "#212121",
    "output": "#212121",
}

# Individual OneNote Section Accents per NodeType (used for section stripes & colorful icons)
NODE_ACCENT_COLORS = {
    NodeType.APP_CAPTURE: "#A071EE",      # Fluent Violet
    NodeType.SYSTEM_CAPTURE: "#00CCF9",   # Vivid Cyan
    NodeType.MIC_INPUT: "#32A6FF",        # Electric Azure
    NodeType.VIRTUAL_INPUT: "#7D7DF2",    # Iris Periwinkle
    NodeType.VOLUME: "#FFC114",           # OneNote Gold
    NodeType.GAIN: "#F6630D",             # Warm Orange
    NodeType.MIXER: "#B973D3",            # Orchid
    NodeType.SPLITTER: "#1EC3B4",         # Teal
    NodeType.MUTE: "#EC5158",             # Coral Red
    NodeType.STEREO_TO_MONO: "#E45383",   # Rose
    NodeType.MONO_TO_STEREO: "#8AA8E4",   # Mist Blue
    NodeType.CHANNEL_MAP: "#F6B078",      # Apricot Copper
    NodeType.EQUALIZER: "#7EC400",        # Lime Chartreuse
    NodeType.SPEAKER_OUTPUT: "#00C753",   # Emerald Green
    NodeType.VIRTUAL_OUTPUT: "#1EC3B4",   # Aqua Teal
    NodeType.FILE_RECORDING: "#EC5158",   # Recording Coral
}

PORT_INPUT_COLOR = "#32A6FF"        # Azure input slot
PORT_OUTPUT_COLOR = "#00C753"       # Emerald output slot
WIRE_IDLE_COLOR = "#A4AAB4"         # Neutral silver-slate wire
WIRE_ACTIVE_COLOR = "#00CCF9"       # Luminous Cyan live audio flow
WIRE_SELECTED_COLOR = "#FFC114"     # Gold selected wire highlight

STUDIO_STYLESHEET = f"""
QMainWindow, QDialog {{
    background-color: {COMFY_BG_COLOR};
    color: {COMFY_TEXT_INPUT};
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: 12px;
}}

QToolTip {{
    background-color: {COMFY_SURFACE_CARD};
    color: {COMFY_TEXT_PRIMARY};
    border: 1px solid {COMFY_BORDER};
    border-radius: 5px;
    padding: 4px 8px;
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
    padding: 5px 9px;
    margin: 0px;
    border-radius: 5px;
}}
QMenuBar::item:selected {{
    background-color: {COMFY_SURFACE_HOVER};
    color: {COMFY_TEXT_PRIMARY};
}}
QMenu {{
    background-color: {COMFY_MENU_BG};
    color: {COMFY_TEXT_INPUT};
    border: 1px solid {COMFY_BORDER};
    border-radius: 7px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 22px 6px 10px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background-color: {COMFY_SURFACE_ELEVATED};
    color: {COMFY_TEXT_PRIMARY};
}}
QMenu::separator {{
    height: 1px;
    background: {COMFY_STROKE};
    margin: 4px 8px;
}}

/* OneNote Fluent Command Topbar & Section-Style Workflow Tabs */
QWidget#ComfyTopBar {{
    background-color: {COMFY_MENU_BG};
    border-bottom: 1px solid {COMFY_STROKE};
}}
QTabBar#WorkflowTabs {{
    background-color: transparent;
    border: none;
}}
QWidget#WorkflowTabs {{
    background-color: transparent;
    border: none;
}}
QFrame#WorkflowTabItem {{
    background-color: transparent;
    border: none;
    border-right: 1px solid {COMFY_STROKE};
}}
QFrame#WorkflowTabItem:hover {{
    background-color: {COMFY_SURFACE_CARD};
}}
QFrame#WorkflowTabItem[selected="true"] {{
    background-color: {COMFY_SURFACE_ELEVATED};
    border-bottom: 2px solid #D0D0D0;
}}
QLabel#WorkflowTabTitle {{
    background-color: transparent;
    color: {COMFY_TEXT_MUTED};
    font-size: 12px;
    font-weight: 400;
}}
QLabel#WorkflowTabTitle[selected="true"] {{
    color: {COMFY_TEXT_PRIMARY};
    font-weight: 600;
}}
QTabBar#WorkflowTabs::tab {{
    background-color: transparent;
    color: {COMFY_TEXT_MUTED};
    padding: 7px 12px;
    margin: 0px;
    border: none;
    border-right: 1px solid {COMFY_STROKE};
    font-size: 12px;
    min-width: 118px;
    max-width: 240px;
}}
QTabBar#WorkflowTabs::tab:hover {{
    background-color: {COMFY_SURFACE_CARD};
    color: {COMFY_TEXT_INPUT};
}}
QTabBar#WorkflowTabs::tab:selected {{
    background-color: {COMFY_SURFACE_ELEVATED};
    color: {COMFY_TEXT_PRIMARY};
    font-weight: 600;
    border-bottom: 2px solid #D0D0D0;
}}
QToolButton#NewWorkflowButton {{
    background-color: transparent;
    color: {COMFY_TEXT_SECONDARY};
    border: none;
    border-radius: 6px;
    padding: 0px;
    margin: 0px 2px;
    font-size: 15px;
    font-weight: 600;
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
    padding: 4px 9px;
    min-height: 26px;
}}
QToolButton#ComfyIconBtn:hover {{
    background-color: {COMFY_SURFACE_HOVER};
    border-color: {COMFY_BORDER};
    color: {COMFY_TEXT_PRIMARY};
}}
QToolButton#ComfyIconBtn:disabled {{
    color: #5A5A5A;
    background-color: {COMFY_MENU_BG};
}}

QPushButton#ComfyRunButton {{
    background-color: #107C41;
    color: #FFFFFF;
    border: 1px solid #21A35B;
    border-radius: 6px;
    padding: 4px 14px;
    font-weight: 600;
    min-height: 26px;
}}
QPushButton#ComfyRunButton:hover {{
    background-color: #16934F;
    border-color: #35BC71;
}}
QPushButton#ComfyRunButton[running="true"] {{
    background-color: {COMFY_ACCENT_RED};
    border-color: #FF7A80;
}}
QPushButton#ComfyRunButton[running="true"]:hover {{
    background-color: #F46269;
}}

/* Floating Bottom-Right Canvas Controls (GraphCanvasMenu) */
QFrame#CanvasFloatingBar {{
    background-color: {COMFY_MENU_BG};
    border: 1px solid {COMFY_BORDER};
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
    padding: 7px 13px;
    margin: 2px 3px;
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
    border-bottom: 2px solid #D0D0D0;
    font-weight: 600;
}}

QTabWidget::pane {{
    border: 1px solid {COMFY_STROKE};
    border-radius: 6px;
    background-color: {COMFY_SURFACE_PANEL};
}}
QTabBar::tab {{
    background-color: {COMFY_MENU_BG};
    color: {COMFY_TEXT_MUTED};
    padding: 6px 11px;
    border: 1px solid {COMFY_STROKE};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
}}
QTabBar::tab:hover {{
    background-color: {COMFY_SURFACE_CARD};
    color: {COMFY_TEXT_INPUT};
}}
QTabBar::tab:selected {{
    background-color: {COMFY_SURFACE_ELEVATED};
    color: {COMFY_TEXT_PRIMARY};
    border-top: 2px solid #D0D0D0;
    font-weight: 600;
}}
QTabWidget#PaletteTabs > QTabBar::tab {{
    padding: 5px 7px;
    font-size: 11px;
    margin-right: 2px;
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
    background-color: #4A2431;
    color: #FF8B90;
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
    border-color: #707070;
}}

QListWidget, QTableWidget, QTextEdit, QPlainTextEdit {{
    background-color: {COMFY_BG_COLOR};
    color: {COMFY_TEXT_INPUT};
    border: 1px solid {COMFY_STROKE};
    border-radius: 6px;
    selection-background-color: {COMFY_SURFACE_ELEVATED};
    selection-color: {COMFY_TEXT_PRIMARY};
}}
QListWidget::item {{
    padding: 5px 8px;
    border-radius: 5px;
    margin: 1px 2px;
}}
QListWidget::item:hover {{
    background-color: {COMFY_SURFACE_CARD};
}}
QListWidget::item:selected {{
    background-color: {COMFY_SURFACE_ELEVATED};
    color: {COMFY_TEXT_PRIMARY};
    border-left: 2px solid #D0D0D0;
}}

QHeaderView::section {{
    background-color: {COMFY_SURFACE_CARD};
    color: {COMFY_TEXT_SECONDARY};
    padding: 4px 8px;
    border: none;
    border-right: 1px solid {COMFY_STROKE};
    border-bottom: 1px solid {COMFY_STROKE};
    font-weight: 600;
}}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {COMFY_INPUT_BG};
    color: {COMFY_TEXT_INPUT};
    border: 1px solid {COMFY_BORDER};
    border-radius: 5px;
    padding: 4px 6px;
}}
QLineEdit:focus, QComboBox:focus {{
    border-color: #8A8A8A;
}}
QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox QAbstractItemView {{
    background-color: {COMFY_MENU_BG};
    color: {COMFY_TEXT_INPUT};
    border: 1px solid {COMFY_BORDER};
    selection-background-color: {COMFY_SURFACE_HOVER};
    selection-color: {COMFY_TEXT_PRIMARY};
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
    border: 1px solid #909090;
    width: 12px;
    height: 12px;
    margin: -5px 0;
    border-radius: 6px;
}}
QSlider::sub-page:horizontal {{
    background: #888888;
    border-radius: 2px;
}}

QCheckBox {{
    spacing: 6px;
    color: {COMFY_TEXT_INPUT};
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border-radius: 4px;
    border: 1px solid {COMFY_BORDER};
    background: {COMFY_INPUT_BG};
}}
QCheckBox::indicator:checked {{
    background: #0078D4;
    border-color: #32A6FF;
}}

QProgressBar {{
    background-color: {COMFY_INPUT_BG};
    border: 1px solid {COMFY_STROKE};
    border-radius: 4px;
    text-align: right;
    color: {COMFY_TEXT_SECONDARY};
    height: 12px;
}}
QProgressBar::chunk {{
    background-color: {COMFY_ACCENT_GREEN};
    border-radius: 3px;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {COMFY_SURFACE_HOVER};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: #666666;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

/* Inline OneNote-Styled Node Widgets hosted inside NodeItem proxy */
QWidget#NodeInlineWidgetRoot {{
    background-color: transparent;
}}
QWidget#NodeInlineWidgetRoot QLabel {{
    color: {COMFY_TEXT_SECONDARY};
    font-size: 11px;
    background-color: transparent;
}}
QWidget#NodeInlineWidgetRoot QComboBox {{
    combobox-popup: 0;
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
QWidget#NodeInlineWidgetRoot QComboBox:hover,
QWidget#NodeInlineWidgetRoot QLineEdit:focus {{
    border-color: #9A9A9A;
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
