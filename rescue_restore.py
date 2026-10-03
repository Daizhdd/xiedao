# -*- coding: utf-8 -*-
"""书目备份救援脚本。

新版完整备份按事务精确替换整个项目，绝不把旧库与备份中的同 id 行混合。
旧版 backup_rebuild JSON 仍支持「只补缺失行」的兼容恢复模式。

用法（在项目目录、用 .venv 的 python）：
    python rescue_restore.py dist/data/backup_rebuild_20260927_120000.json
    python rescue_restore.py --db dist/data/novel.db backup_rebuild_xxx.json

"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import DB, DB_PATH, now  # noqa
import book_backup  # noqa

# 恢复顺序：先大纲后章节（outline_id 语义），再版本史/伏笔/设定
TABLE_COLS = {
    "outlines": ["id", "project_id", "level", "title", "content", "chapter_id",
                 "sort_no", "volume", "created_at", "updated_at"],
    "chapters": ["id", "project_id", "volume", "chapter_no", "title",
                 "chapter_card", "content", "summary", "status", "outline_id",
                 "created_at", "updated_at"],
    "chapter_versions": ["id", "chapter_id", "version", "snapshot", "note",
                         "created_at"],
    "foreshadows": ["id", "project_id", "content", "ftype", "planted_ch",
                    "plan_ch", "status", "note", "created_at", "updated_at"],
    "settings_dict": ["id", "project_id", "category", "term", "definition",
                      "updated_at"],
}
# 备份 JSON 键名与表名的映射（versions 在 JSON 里是 {str(chapter_id): [行]}）
BACKUP_KEY = {"outlines": "outlines", "chapters": "chapters",
              "chapter_versions": "versions", "foreshadows": "foreshadows",
              "settings_dict": "settings"}


def _iter_rows(data, table):
    """备份 JSON 里 versions 的结构是 {str(chapter_id): [行, ...]}"""
    key = BACKUP_KEY[table]
    if key == "versions":
        for rows in (data.get("versions") or {}).values():
            yield from (rows or [])
    else:
        yield from data.get(key) or []


def restore_backup(db, data):
    """Restore a full snapshot exactly; legacy payloads retain merge semantics.

    Full snapshots replace all project-scoped rows in one transaction and roll
    back as a unit. Legacy rebuild JSON predates project metadata, volume
    summaries, chat history, and task records, so it remains a conservative
    insert-missing compatibility path.
    """
    if isinstance(data, dict) and data.get("format") == book_backup.FORMAT:
        return book_backup.restore_project(db, data)
    conn = db.conn
    stats = {}
    pid = data.get("project_id")
    try:
        if pid is not None and conn.execute(
                "SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone() is None:
            conn.execute(
                "INSERT INTO projects(id,title,status,created_at,updated_at)"
                " VALUES(?,?,?,?,?)",
                (pid, data.get("title") or f"恢复书{pid}", "恢复", now(), now()))
        for table, cols in TABLE_COLS.items():
            n = 0
            marks = ",".join("?" * len(cols))
            sql = (f"INSERT OR IGNORE INTO {table}({','.join(cols)}) "
                   f"VALUES({marks})")
            for row in _iter_rows(data, table):
                cur = conn.execute(sql, [row.get(c) for c in cols])
                n += 1 if cur.rowcount else 0
            stats[table] = n
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return stats


def main(argv):
    args = list(argv)
    db_path = DB_PATH
    if "--db" in args:
        i = args.index("--db")
        db_path = args[i + 1]
        del args[i:i + 2]
    if not args:
        print(__doc__)
        return 1
    path = args[0]
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    db = DB(db_path)
    try:
        stats = restore_backup(db, data)
    finally:
        db.close()
    print(f"恢复完成（{data.get('title', '?')}）："
          + "，".join(f"{k} {v} 行" for k, v in stats.items())
          + f" ｜ 目标库：{db_path}")
    print("注意：只补了缺失行，库里已存在的行未被改动；请打开软件人工复核。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
