"""Single-call Qt worker used by routing, Q&A, and draft previews."""
import traceback
from PySide6.QtCore import QThread, Signal

class SimpleWorker(QThread):
    """一次性调用：fn() → done(obj) / failed(str)。done 用 object 以承载 dict 结果"""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as e:  # noqa
            traceback.print_exc()
            self.failed.emit(str(e))
