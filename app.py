# -*- coding: utf-8 -*-
"""写道 · 入口（对话界面为主，经典工作台在视图菜单）。
单实例保护（P2）：重复启动时唤出已有窗口后立即退出，不重复开进程。"""
import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from book_backup import recover_pending_rebuilds
from db import DB
from ui.chat_window import ChatWindow
from ui.theme import build_qss, load_dark_pref, configure_fonts

SINGLE_KEY = "xiedao_single_instance_v1"   # Windows 下即命名管道 \\.\pipe\<key>


def _already_running():
    """已有实例在跑则给它发一条唤出消息并返回 True。"""
    sock = QLocalSocket()
    sock.connectToServer(SINGLE_KEY)
    if not sock.waitForConnected(300):
        return False
    sock.write(b"activate\n")
    sock.flush()
    sock.waitForBytesWritten(300)
    sock.disconnectFromServer()
    return True


def _start_instance_server(win):
    """首个实例：监听管道，后续启动发消息过来就把窗口提到前台。
    挂在 win 上防 GC；监听失败（极端竞态）只影响唤出功能，不影响运行。"""
    server = QLocalServer()
    QLocalServer.removeServer(SINGLE_KEY)   # 清掉异常退出残留（Windows 上通常无效但无害）
    if not server.listen(SINGLE_KEY):
        return

    def _activate():
        sock = server.nextPendingConnection()
        if sock is None:
            return
        win.showNormal()
        win.raise_()
        win.activateWindow()
        sock.disconnectFromServer()

    server.newConnection.connect(_activate)
    win._single_server = server


def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def main():
    app = QApplication(sys.argv)
    configure_fonts()
    app.setApplicationName("写道")
    app.setStyleSheet(build_qss(load_dark_pref()))  # 全局主题
    icon = resource_path(os.path.join("assets", "icon.ico"))
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    if _already_running():
        return     # 已唤出现有窗口，静默退出
    db = DB()
    try:
        recovered = recover_pending_rebuilds(db)
    except Exception as e:
        db.close()
        QMessageBox.critical(
            None, "书目自动恢复失败",
            f"检测到未完成的全书重构，但自动恢复未能完成：\n{e}\n\n"
            "恢复标记和备份文件已保留。修复文件或权限后重新启动，"
            "也可使用 rescue_restore.py 指定备份恢复。")
        return 1
    if recovered:
        names = "、".join(f"《{item['title']}》" for item in recovered)
        QMessageBox.information(
            None, "已恢复未完成的重构",
            f"检测到上次未完成的全书重构，已从完整备份恢复：{names}\n\n"
            "原备份文件仍保留在 data 目录。")
    win = ChatWindow(db)
    _start_instance_server(win)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    sys.exit(main())
