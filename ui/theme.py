# -*- coding: utf-8 -*-
"""三期 UI 主题化：深色 / 浅色两套全局 QSS
用法：QApplication.setStyleSheet(build_qss(dark=True))；主题偏好存 QSettings。
"""
import os
import sys
from PySide6.QtCore import QSettings

_FONT_UI = "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei"
_ORG = "AIXiaoshuoGongzuotai"
_fonts_configured = False


def configure_fonts():
    """Select an installed UI font without carrying Windows fonts to macOS."""
    global _fonts_configured, _FONT_UI
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None or _fonts_configured:
        return
    if os.name == 'nt' and _FONT_UI not in QFontDatabase.families():
        folder = os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts')
        for filename in ('msyh.ttc', 'msyhbd.ttc'):
            path = os.path.join(folder, filename)
            if os.path.isfile(path):
                QFontDatabase.addApplicationFont(path)
    available = set(QFontDatabase.families())
    if _FONT_UI not in available:
        _FONT_UI = next((name for name in ("PingFang SC", "Microsoft YaHei",
                          "Noto Sans CJK SC", "Heiti SC") if name in available),
                        app.font().family())
    app.setFont(QFont(_FONT_UI, 10))
    _fonts_configured = True


def ui_font_family():
    return _FONT_UI

_DARK = dict(
    BG="#191A1C", PANEL="#222427", INPUT="#27292C", LINE="#3B3E42",
    TXT="#ECEEF0", SUB="#A2A7AD", ACCENT="#D8DADD", ACCENT_HOVER="#ECEEF0",
    ACCENT_TXT="#202226", SEL="#3B3F44", HOVER="#303338",
    EDIT_BG="#1E2023", SCROLL="#494D52",
    # 对话界面
    SIDEBAR="#151618", BUBBLE_USER="#303338", BUBBLE_AGENT="#25272A",
    BUBBLE_AGENT_LINE="#3B3E42", BUBBLE_TXT="#ECEEF0",
    CARD="#232528", CARD_LINE="#3B3E42", CARD_LOG_BG="#1B1D20",
    CARD_LOG_TXT="#BEC3C9", CHIP_BG="#27292C", CHIP_LINE="#404348",
    CHIP_TXT="#D7DADE", LINK_BG="#303338", LINK_LINE="#4B4F55",
    LINK_TXT="#E3E6E9", HEAD_TXT="#F3F4F5", GATE="#D8BC87",
    TITLE_TXT="#A5ABB2", DANGER="#EA9A9C", SUCCESS="#D0D3D6",
)
_LIGHT = dict(
    BG="#F6F7F8", PANEL="#FFFFFF", INPUT="#FFFFFF", LINE="#DADDE1",
    TXT="#22252A", SUB="#6B7178", ACCENT="#303338", ACCENT_HOVER="#1E2023",
    ACCENT_TXT="#FFFFFF", SEL="#E3E6E9", HOVER="#F0F2F4",
    EDIT_BG="#FCFCFD", SCROLL="#BFC4CA",
    # 对话界面
    SIDEBAR="#F0F1F3", BUBBLE_USER="#ECEEF1", BUBBLE_AGENT="#FFFFFF",
    BUBBLE_AGENT_LINE="#DEE1E5", BUBBLE_TXT="#24272C",
    CARD="#FFFFFF", CARD_LINE="#DBDEE2", CARD_LOG_BG="#F7F8F9",
    CARD_LOG_TXT="#555C63", CHIP_BG="#FFFFFF", CHIP_LINE="#DADDE1",
    CHIP_TXT="#35393F", LINK_BG="#EFF1F3", LINK_LINE="#D6DADE",
    LINK_TXT="#30343A", HEAD_TXT="#22252A", GATE="#8B6D34",
    TITLE_TXT="#747B83", DANGER="#B95B5F", SUCCESS="#515A63",
)

_BASE_QSS = """
* { font-family: "__FONT__"; font-size: 13px; color: $TXT; }
QWidget { background: $BG; }
QMainWindow, QDialog { background: $BG; }
QLabel { background: transparent; }
QLabel#metaLabel { color: $SUB; }
QLabel#dialogTitle { font-size: 19px; font-weight: 700; color: $HEAD_TXT; }
QLabel#dialogSubtitle { font-size: 12px; color: $SUB; }
QLabel#editorTitle { font-size: 18px; font-weight: 600; color: $HEAD_TXT; }
QWidget#metadataPanel { background: $PANEL; }
QScrollArea { border: none; background: transparent; }
QFrame#editorTools {
    background: $PANEL; border: 1px solid $LINE; border-radius: 10px;
}

/* ---- 输入控件 ---- */
QLineEdit, QSpinBox, QPlainTextEdit, QTextEdit, QComboBox {
    background: $INPUT; border: 1px solid $LINE; border-radius: 6px;
    padding: 4px 6px; selection-background-color: $SEL; selection-color: $TXT;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus { border-color: $ACCENT; }
QSpinBox::up-button, QSpinBox::down-button { width: 16px; }
QComboBox::drop-down { border-left: 1px solid $LINE; width: 22px; }
QComboBox QAbstractItemView {
    background: $PANEL; border: 1px solid $LINE;
    selection-background-color: $SEL; outline: none;
}

/* ---- 按钮 ---- */
QPushButton {
    background: $PANEL; border: 1px solid $LINE; border-radius: 6px;
    padding: 5px 14px; color: $TXT;
}
QPushButton:hover { border-color: $ACCENT; color: $ACCENT; }
QPushButton:pressed { background: $HOVER; }
QPushButton:focus { border-color: $ACCENT; }
QPushButton#primaryAction {
    background: $ACCENT; border-color: $ACCENT; color: $ACCENT_TXT;
    font-weight: 600;
}
QPushButton#primaryAction:hover { background: $ACCENT_HOVER; color: $ACCENT_TXT; }
QPushButton#primaryAction:disabled { background: $LINE; border-color: $LINE; color: $SUB; }
QPushButton#dangerAction { color: $DANGER; }
QPushButton#dangerAction:hover { border-color: $DANGER; color: $DANGER; }

/* ---- 分组框 ---- */
QGroupBox {
    background: $PANEL; border: 1px solid $LINE; border-radius: 10px;
    margin-top: 12px; padding-top: 4px; font-weight: 500;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 12px; top: 0px;
    padding: 0 4px; color: $SUB; background: transparent;
}

/* ---- 树 / 列表 ---- */
QTreeWidget, QListWidget {
    background: $PANEL; border: 1px solid $LINE; border-radius: 8px;
    padding: 4px; outline: none;
}
QTreeWidget::item, QListWidget::item {
    padding: 4px 2px; border-radius: 4px; color: $TXT;
}
QTreeWidget::item:hover, QListWidget::item:hover { background: $HOVER; }
QTreeWidget::item:selected, QListWidget::item:selected {
    background: $SEL; color: $TXT;
}

/* ---- 编辑器（写作主区） ---- */
QPlainTextEdit#mainEdit, QTextEdit#aiOut {
    background: $EDIT_BG; border: 1px solid $LINE; border-radius: 8px;
    padding: 22px 28px; font-size: 16px; color: $TXT;
    selection-background-color: $SEL; selection-color: $TXT;
}
QPlainTextEdit#cardEdit {
    background: $EDIT_BG; border: 1px solid $LINE; border-radius: 8px;
    padding: 8px 10px; color: $TXT;
}

/* ---- 页签 ---- */
QTabWidget::pane { border: 1px solid $LINE; border-radius: 8px; top: -1px; }
QTabBar::tab {
    background: transparent; color: $SUB; padding: 6px 16px;
    border: 1px solid transparent; border-bottom: none;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
}
QTabBar::tab:selected { background: $EDIT_BG; color: $TXT; }
QTabBar::tab:hover:!selected { background: $HOVER; }

/* ---- 表格 ---- */
QTableWidget {
    background: $PANEL; border: 1px solid $LINE; border-radius: 8px;
    gridline-color: $LINE; alternate-background-color: $HOVER;
}
QTableWidget::item { padding: 4px 7px; }
QHeaderView::section {
    background: $HOVER; color: $SUB; border: none;
    border-bottom: 1px solid $LINE; padding: 5px 8px;
}

/* ---- 菜单 ---- */
QMenuBar { background: $BG; }
QMenuBar::item:selected { background: $HOVER; border-radius: 4px; }
QMenu { background: $PANEL; border: 1px solid $LINE; border-radius: 8px; padding: 4px; }
QMenu::item { padding: 5px 24px 5px 12px; border-radius: 4px; }
QMenu::item:selected { background: $SEL; }
QMenu::separator { height: 1px; background: $LINE; margin: 4px 8px; }

/* ---- 滚动条（细） ---- */
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: $SCROLL; border-radius: 4px; min-height: 30px; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }
QScrollBar::handle:horizontal { background: $SCROLL; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---- 其它 ---- */
QSplitter::handle { background: $BG; }
QStatusBar { background: $BG; color: $SUB; }
QToolTip { background: $PANEL; color: $TXT; border: 1px solid $LINE; }
"""


_CHAT_QSS = """
QWidget#chatSidebar { background: $SIDEBAR; }
QLabel#sideLabel {
    color: $SUB; font-size: 12px; font-weight: 600;
    letter-spacing: 1px; background: transparent;
}
QListWidget#bookList {
    background: transparent; border: none; color: $TXT; outline: none;
    font-size: 13px;
}
QListWidget#bookList::item { padding: 7px 8px; border-radius: 8px; }
QListWidget#bookList::item:hover { background: $HOVER; }
QListWidget#bookList::item:selected { background: $SEL; color: $TXT; }
QLabel#chatHeader { font-size: 17px; font-weight: 700; color: $HEAD_TXT; }
QLabel#welcomeText {
    color: $SUB; font-size: 14px; background: transparent;
}
QPushButton#chipBtn, QPushButton#chapterBtn {
    background: $CHIP_BG; color: $CHIP_TXT; border: 1px solid $CHIP_LINE;
    border-radius: 15px; padding: 5px 14px;
}
QPushButton#chipBtn:hover, QPushButton#chapterBtn:hover {
    background: $HOVER; border-color: $ACCENT;
}
QPushButton#chapterBtn {
    border-radius: 8px; color: $LINK_TXT; background: $LINK_BG;
    border: 1px solid $LINK_LINE; padding: 3px 10px;
}
QPushButton#logToggleBtn { color: $TITLE_TXT; border: none; background: transparent; }
QFrame#userBubble {
    background: $BUBBLE_USER; border: 1px solid $BUBBLE_USER; border-radius: 16px;
}
QFrame#agentBubble {
    background: $BUBBLE_AGENT; border: 1px solid $BUBBLE_AGENT_LINE; border-radius: 14px;
}
QFrame#userBubble QLabel, QFrame#agentBubble QLabel {
    color: $BUBBLE_TXT; padding: 10px 14px; background: transparent;
    font-size: 14px;
}
QFrame#activityCard { background: $CARD; border: 1px solid $CARD_LINE; border-radius: 14px; }
QLabel#activityTitle { color: $TXT; font-weight: 600; background: transparent; }
QLabel#activityStage { color: $ACCENT; font-size: 12px; background: transparent; }
QLabel#activityUsage { color: $SUB; font-size: 11px; background: transparent; }
QLabel#gateTitle { color: $GATE; background: transparent; font-weight: 600; }
QFrame#emptyGuide { background: $CARD; border: 1px solid $CARD_LINE; border-radius: 16px; }
QLabel#guideEyebrow { color: $ACCENT; font-size: 11px; font-weight: 600; }
QLabel#guideTitle { color: $HEAD_TXT; font-size: 20px; font-weight: 700; }
QLabel#guideText { color: $SUB; font-size: 13px; }
QPlainTextEdit#activityLog {
    background: $CARD_LOG_BG; color: $CARD_LOG_TXT;
    border: 1px solid $CARD_LINE; border-radius: 8px;
}
QPlainTextEdit#composer {
    background: $INPUT; border: 1px solid $LINE; border-radius: 16px;
    padding: 10px 14px; font-size: 13px;
    selection-background-color: $SEL; selection-color: $TXT;
}
QPlainTextEdit#composer:focus { border-color: $ACCENT; }
QPushButton#sendBtn {
    background: $ACCENT; color: $ACCENT_TXT; border: none;
    border-radius: 14px; font-weight: 600; font-size: 14px; padding: 0 22px;
}
QPushButton#sendBtn:hover { background: $ACCENT_HOVER; }
QPushButton#sendBtn:disabled { background: $LINE; color: $BG; }

/* 写作书房：导航、创作、作品概览 */
QWidget#creationWorkspace { background: $BG; }
QLabel#brandMark {
    background: transparent; border: none;
}
QLabel#brandName { color: $HEAD_TXT; font-size: 23px; font-weight: 700; }
QLabel#mutedText { color: $SUB; font-size: 12px; }
QLabel#keyboardHint { color: $SUB; font-size: 10px; }
QLineEdit#bookSearch {
    background: $INPUT; border: 1px solid $LINE;
    border-radius: 8px; padding: 8px 10px;
}
QTreeWidget#resourceTree {
    background: transparent; border: none; padding: 0; font-size: 12px;
}
QTreeWidget#resourceTree::item { padding: 7px 2px; border-radius: 6px; }
QTreeWidget#resourceTree::item:selected { background: $SEL; color: $TXT; }
QLabel#chatHeader { color: $HEAD_TXT; font-size: 23px; font-weight: 700; }
QPushButton#quietBtn {
    border: none; background: transparent; color: $SUB;
    border-radius: 7px; padding: 7px 9px;
}
QPushButton#quietBtn:hover, QPushButton#quietBtn:checked {
    background: $HOVER; color: $TXT;
}
QFrame#composerPanel {
    background: $PANEL; border: 1px solid $LINE; border-radius: 16px;
}
QPlainTextEdit#composer {
    background: transparent; border: none; padding: 3px 2px;
    font-size: 14px; color: $TXT;
}
QPlainTextEdit#composer:focus { border: none; }
QFrame#composerPanel QPushButton#chipBtn {
    background: transparent; border: none; color: $SUB;
    padding: 5px 8px; border-radius: 6px; font-size: 11px;
}
QFrame#composerPanel QPushButton#chipBtn:hover { background: $HOVER; color: $TXT; }
QPushButton#sendBtn { border-radius: 9px; padding: 0 14px; font-size: 12px; }
QScrollArea#overviewScroll { background: $PANEL; border-left: 1px solid $LINE; }
QFrame#bookInspector { background: $PANEL; }
QLabel#overviewTitle { color: $HEAD_TXT; font-size: 14px; font-weight: 600; }
QLabel#progressValue { color: $HEAD_TXT; font-size: 27px; font-weight: 600; }
QLabel#metricValue { color: $HEAD_TXT; font-size: 23px; font-weight: 600; }
QLabel#overviewSynopsis { color: $SUB; font-size: 13px; }
QProgressBar#bookProgress, QProgressBar#taskProgress {
    background: $LINE; border: none; border-radius: 2px;
}
QProgressBar#bookProgress::chunk, QProgressBar#taskProgress::chunk {
    background: $ACCENT; border-radius: 2px;
}
QLabel#taskStatus {
    color: $TXT; background: $SEL; padding: 3px 8px;
    border-radius: 7px; font-size: 10px;
}
QFrame#agentBubble { background: transparent; border: none; border-radius: 0; }
QFrame#agentBubble QLabel { padding: 8px 2px; font-size: 14px; }
QFrame#userBubble { border-radius: 12px; }
QFrame#emptyGuide { background: transparent; border: none; }
QLabel#guideTitle { font-size: 29px; font-weight: 700; }
QLabel#guideEyebrow { color: $SUB; font-size: 12px; }
QLabel#guideText { font-size: 14px; }
QLabel#activityTitle { font-size: 14px; }
"""


def _replace_all(qss, colors):
    """长 key 优先替换，避免前缀冲突（如 $BUBBLE_AGENT vs $BUBBLE_AGENT_LINE）"""
    for k in sorted(colors, key=len, reverse=True):
        qss = qss.replace("$" + k, colors[k])
    return qss


def chat_qss(dark=True):
    """对话界面专属样式（对象名选择器，随深浅主题联动）"""
    return _replace_all(_CHAT_QSS, _DARK if dark else _LIGHT)


def build_qss(dark=True):
    c = _DARK if dark else _LIGHT
    qss = _BASE_QSS.replace("__FONT__", _FONT_UI)
    return _replace_all(qss, c)


def theme_settings():
    return QSettings(_ORG, "ui")


def load_dark_pref():
    return theme_settings().value("dark", True, type=bool)


def save_dark_pref(dark):
    theme_settings().setValue("dark", bool(dark))


if __name__ == "__main__":
    d = build_qss(True)
    l = build_qss(False)
    assert len(d) > 500 and len(l) > 500
    assert "$" not in d and "$" not in l, "存在未替换的占位符"
    print("THEME OK")
