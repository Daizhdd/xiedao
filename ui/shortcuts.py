"""Qt maps Ctrl in portable key sequences to Command on macOS."""
import sys
from PySide6.QtGui import QKeySequence


def shortcut_hint(key):
    return ("Command" if sys.platform == "darwin" else "Ctrl") + "+" + key


def save_sequence():
    return QKeySequence(QKeySequence.StandardKey.Save)
