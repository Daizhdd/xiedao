"""Offline verification entry for frozen binaries; always uses a temporary database."""
import json
import os
from pathlib import Path
import platform
import sqlite3
import sys
import tempfile
import traceback


def run(report_directory):
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    from app_paths import data_directory, resource_path
    from book_backup import backup_project, read_backup, restore_project
    from db import DB
    from ui.chat_window import ChatWindow
    from ui.main_window import MainWindow
    from ui.theme import configure_fonts, build_qss, ui_font_family

    output = Path(report_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {'platform': sys.platform, 'architecture': platform.machine(),
              'python': platform.python_version(), 'pyside6': pyside_version,
              'sqlite': sqlite3.sqlite_version, 'frozen': bool(getattr(sys, 'frozen', False)),
              'default_data_directory': str(data_directory()), 'passed': False}
    app = QApplication([])
    app.setApplicationName('写道')
    configure_fonts()
    app.setStyleSheet(build_qss(dark=False))
    app.setWindowIcon(QIcon(resource_path('assets/brand-logo.png')))
    windows = []
    database = None
    temporary = tempfile.TemporaryDirectory(prefix='xiedao-bundle-')

    def verify():
        nonlocal database
        try:
            for asset in ('assets/brand-logo.png', 'assets/icon.ico'):
                if QIcon(resource_path(asset)).isNull():
                    raise AssertionError(f'Bundled image cannot be loaded: {asset}')
            path = str(Path(temporary.name) / '中文数据' / 'novel.db')
            database = DB(path)
            if database.conn.execute('SELECT count(*) FROM projects').fetchone()[0]:
                raise AssertionError('Initial database is not empty')
            if database.conn.execute('SELECT count(*) FROM ai_configs').fetchone()[0]:
                raise AssertionError('Initial model configuration is not empty')
            pid = database.create_project('Mac 兼容验证', '这是一部虚构的测试作品。')
            cid = database.create_chapter(pid, 1, 1, '第一章 雨后的书房', '测试初稿。')
            chat = ChatWindow(database)
            windows.append(chat)
            chat._set_current_book(pid)
            chat.show()
            app.processEvents()
            if not chat.grab().save(str(output / 'bookroom.png')):
                raise AssertionError('Cannot save bookroom screenshot')
            editor = MainWindow(database)
            windows.append(editor)
            editor.current_pid = pid
            editor.tree.load()
            editor.tree.load_project_detail(pid)
            editor._on_chapter(cid)
            body = '雨停了，作者打开书房的窗。\n中文输入与保存：你好，写道。'
            editor.editor.edit.setPlainText(body)
            editor._save_current()
            if database.get_chapter(cid)['content'] != body or not database.get_versions(cid):
                raise AssertionError('Editor save or version snapshot failed')
            editor.show()
            app.processEvents()
            if not editor.grab().save(str(output / 'editor.png')):
                raise AssertionError('Cannot save editor screenshot')
            backup = backup_project(database, pid, reason='bundle-smoke')
            snapshot = read_backup(backup)
            restore_project(database, snapshot)
            for window in windows:
                window.close()
            database.close()
            database = DB(path)
            if database.get_chapter(cid)['content'] != body:
                raise AssertionError('Saved manuscript did not survive reopening')
            report.update(passed=True, ui_font=ui_font_family(),
                          checks=['empty-database', 'bundled-images', 'bookroom-render',
                                  'editor-save', 'version-snapshot', 'backup-restore',
                                  'database-reopen'])
        except Exception:
            report['error'] = traceback.format_exc()
        finally:
            for window in windows:
                window.close()
            if database is not None:
                database.close()
            (output / 'smoke.json').write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                               encoding='utf-8')
            temporary.cleanup()
            app.exit(0 if report['passed'] else 1)

    QTimer.singleShot(0, verify)
    return app.exec()
