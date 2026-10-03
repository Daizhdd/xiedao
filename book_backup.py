# -*- coding: utf-8 -*-
"""Atomic, complete backups and exact restore for one writing project.

Project backups deliberately omit ``ai_configs`` because those credentials are
global application settings, not book data.  Optional task ledger tables are
included when the application has created them.
"""
import json
import os
import uuid
from datetime import datetime


FORMAT = "xiedao.project-backup"
FORMAT_VERSION = 7

# Table name -> JSON key. Keep child rows as explicit snapshots so ids, status,
# summaries, and edit history survive delete/restore without reconstruction.
PROJECT_TABLES = {
    "settings_dict": "settings",
    "setting_candidates": "setting_candidates",
    "outlines": "outlines",
    "chapters": "chapters",
    "foreshadows": "foreshadows",
    "volume_summary": "volume_summaries",
    "chat_messages": "chat_messages",
    "task_runs": "task_runs",
    "task_plans": "task_plans",
    "story_entities": "story_entities",
    "story_aliases": "story_aliases",
    "story_facts": "story_facts",
    "story_extractions": "story_extractions",
    "rewrite_candidates": "rewrite_candidates",
    "rewrite_locks": "rewrite_locks",
    "review_reports": "review_reports",
    "style_profiles": "style_profiles",
    "style_suggestions": "style_suggestions",
    "task_usage": "task_usage",
}
CHILD_TABLES = ("chapter_versions", "task_steps")
_ALL_TABLES = ("projects",) + tuple(PROJECT_TABLES) + CHILD_TABLES


class BackupError(RuntimeError):
    """Backup or exact restoration could not be completed safely."""


def _table_exists(conn, table):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def capture_project(db, project_id, reason="manual"):
    """Return a validated full snapshot of a project and its dependent rows."""
    conn = db.conn
    if conn.in_transaction:
        raise BackupError("数据库已有未提交事务，拒绝生成书目备份")
    project_rows = _rows(conn, "SELECT * FROM projects WHERE id=?", (project_id,))
    if len(project_rows) != 1:
        raise BackupError(f"项目 id={project_id} 不存在或记录不唯一")
    project = project_rows[0]
    data = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "reason": str(reason),
        "project_id": project_id,
        "title": project.get("title", ""),
        "project": project,
    }
    for table, key in PROJECT_TABLES.items():
        if not _table_exists(conn, table):
            data[key] = []
            continue
        data[key] = _rows(
            conn, f"SELECT * FROM {table} WHERE project_id=? ORDER BY id",
            (project_id,))

    chapter_ids = [row["id"] for row in data["chapters"]]
    if chapter_ids and _table_exists(conn, "chapter_versions"):
        version_rows = _rows(
            conn,
            "SELECT v.* FROM chapter_versions v JOIN chapters c "
            "ON c.id=v.chapter_id WHERE c.project_id=? ORDER BY v.id",
            (project_id,))
    else:
        version_rows = []
    # Keep the historic backup_rebuild JSON layout for old tools and smoke
    # scripts, while the table snapshot below is the restore authority.
    data["versions"] = {}
    for row in version_rows:
        data["versions"].setdefault(str(row["chapter_id"]), []).append(row)

    run_ids = [row["id"] for row in data.get("task_runs", [])]
    step_rows = []
    if run_ids and _table_exists(conn, "task_steps"):
        step_rows = _rows(
            conn, "SELECT s.* FROM task_steps s JOIN task_runs r "
            "ON r.id=s.run_id WHERE r.project_id=? ORDER BY s.id", (project_id,))
    data["task_steps"] = step_rows

    # Exact restoration consumes a uniform per-table map, including empty
    # tables so deletion of rows created during an interrupted rebuild is exact.
    data["tables"] = {"projects": [project]}
    for table, key in PROJECT_TABLES.items():
        data["tables"][table] = data[key]
    data["tables"]["chapter_versions"] = version_rows
    data["tables"]["task_steps"] = step_rows
    validate_full_backup(data, project_id=project_id)
    return data


def validate_full_backup(data, project_id=None):
    """Reject incomplete or cross-book snapshots before writing/restoring."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BackupError("不是写道完整书目备份")
    if data.get("format_version") not in (1, 2, 3, 4, 5, 6, FORMAT_VERSION):
        raise BackupError(f"不支持的书目备份版本：{data.get('format_version')!r}")
    pid = data.get("project_id")
    if pid is None or (project_id is not None and pid != project_id):
        raise BackupError("备份中的项目 id 无效或与目标不匹配")
    tables = data.get("tables")
    required = set(_ALL_TABLES)
    if data['format_version'] == 1:
        required.difference_update(('story_entities', 'story_aliases',
                                    'story_facts', 'story_extractions'))
    if data['format_version'] < 3:
        required.difference_update(('rewrite_candidates', 'rewrite_locks'))
    if data['format_version'] < 4:
        required.discard('review_reports')
    if data['format_version'] < 5:
        required.difference_update(('style_profiles', 'style_suggestions'))
    if data['format_version'] < 6:
        required.discard('task_usage')
    if data['format_version'] < 7:
        required.discard('setting_candidates')
    if not isinstance(tables, dict) or set(tables) != required:
        raise BackupError("完整备份缺少数据表，拒绝进行不完整恢复")
    projects = tables.get("projects")
    if not isinstance(projects, list) or len(projects) != 1:
        raise BackupError("完整备份必须包含且仅包含一条项目元数据")
    if projects[0].get("id") != pid:
        raise BackupError("备份项目元数据与项目 id 不一致")
    chapter_ids = {r.get("id") for r in tables.get("chapters", [])}
    for row in tables.get('setting_candidates', []):
        if (row.get('status') == 'candidate'
                and row.get('source_chapter_id') not in chapter_ids):
            raise BackupError('设定候选引用了其他书的章节')
    for table in required.intersection(PROJECT_TABLES):
        rows = tables.get(table)
        if not isinstance(rows, list):
            raise BackupError(f"备份表 {table} 格式无效")
        if any(r.get("project_id") != pid for r in rows):
            raise BackupError(f"备份表 {table} 混入了其他项目的数据")
    for row in tables.get("chapter_versions", []):
        if row.get("chapter_id") not in chapter_ids:
            raise BackupError("章节版本引用了备份中不存在的章节")
    run_ids = {r.get("id") for r in tables.get("task_runs", [])}
    for row in tables.get("task_steps", []):
        if row.get("run_id") not in run_ids:
            raise BackupError("任务步骤引用了备份中不存在的任务")
    for row in tables.get('task_usage', []):
        if row.get('run_id') not in run_ids:
            raise BackupError('任务用量引用了其他书的任务')
    entity_ids = {r.get('id') for r in tables.get('story_entities', [])}
    for row in tables.get('story_aliases', []):
        if row.get('entity_id') not in entity_ids:
            raise BackupError('故事记忆别名引用了其他书的人物')
    for row in tables.get('story_facts', []):
        if row.get('entity_id') and row.get('entity_id') not in entity_ids:
            raise BackupError('故事事实引用了其他书的人物')
        if (row.get('status') != 'stale' and row.get('source_chapter_id')
                and row.get('source_chapter_id') not in chapter_ids):
            raise BackupError('有效故事事实引用了其他书的章节')
    fact_ids = {r.get('id') for r in tables.get('story_facts', [])}
    if any(r.get('supersedes_id') and r['supersedes_id'] not in fact_ids
           for r in tables.get('story_facts', [])):
        raise BackupError('故事事实覆盖关系指向其他书')
    for row in tables.get('story_extractions', []):
        if row.get('chapter_id') not in chapter_ids:
            raise BackupError('故事提取记录引用了其他书的章节')
    for table in ('rewrite_candidates', 'rewrite_locks'):
        for row in tables.get(table, []):
            if row.get('chapter_id') not in chapter_ids:
                raise BackupError(f'{table} 引用了其他书的章节')
    for row in tables.get('review_reports', []):
        if row.get('chapter_id') not in chapter_ids:
            raise BackupError('审稿报告引用了其他书的章节')
    return pid


def _atomic_json(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temp_path = os.path.join(directory, ".tmp_" + uuid.uuid4().hex + ".json")
    try:
        with open(temp_path, "x", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, allow_nan=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        # Verify the completed temp file before publishing it as a backup.
        with open(temp_path, encoding="utf-8") as fh:
            check = json.load(fh)
        validate_full_backup(check)
        os.replace(temp_path, path)
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
    return os.path.abspath(path)


def backup_project(db, project_id, reason="manual", prefix=None):
    """Write a complete snapshot to an fsynced temp file then atomically rename."""
    data = capture_project(db, project_id, reason=reason)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_prefix = prefix or ("backup_rebuild" if reason == "rebuild" else "backup_delete")
    name = f"{safe_prefix}_{project_id}_{stamp}_{uuid.uuid4().hex[:8]}.json"
    path = os.path.join(os.path.dirname(os.path.abspath(db.path)), name)
    return _atomic_json(path, data)


def read_backup(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise BackupError(f"读取书目备份失败：{exc}") from exc
    validate_full_backup(data)
    return data


def _table_columns(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _insert_rows(conn, table, rows):
    if not rows:
        return
    columns = _table_columns(conn, table)
    for row in rows:
        if not isinstance(row, dict) or not row:
            raise BackupError(f"备份表 {table} 含无效行")
        unknown = set(row) - columns
        if unknown:
            raise BackupError(
                f"目标数据库表 {table} 缺少备份列：{', '.join(sorted(unknown))}")
        names = list(row)
        marks = ",".join("?" for _ in names)
        conn.execute(
            f"INSERT INTO {table} ({','.join(names)}) VALUES ({marks})",
            [row[name] for name in names])


def delete_project_rows(conn, project_id):
    """Delete one project's rows using the caller's transaction."""
    if _table_exists(conn, "task_runs") and _table_exists(conn, "task_steps"):
        conn.execute(
            "DELETE FROM task_steps WHERE run_id IN "
            "(SELECT id FROM task_runs WHERE project_id=?)", (project_id,))
    if _table_exists(conn, "task_plans"):
        conn.execute("DELETE FROM task_plans WHERE project_id=?", (project_id,))
    if _table_exists(conn, "task_runs"):
        conn.execute("DELETE FROM task_runs WHERE project_id=?", (project_id,))
    if _table_exists(conn, "chat_messages"):
        conn.execute("DELETE FROM chat_messages WHERE project_id=?", (project_id,))
    if _table_exists(conn, "volume_summary"):
        conn.execute("DELETE FROM volume_summary WHERE project_id=?", (project_id,))
    for table in ('task_usage', 'style_suggestions', 'style_profiles', 'review_reports',
                  'setting_candidates',
                  'rewrite_candidates', 'rewrite_locks', 'story_extractions',
                  'story_facts', 'story_aliases', 'story_entities'):
        if _table_exists(conn, table):
            conn.execute(f'DELETE FROM {table} WHERE project_id=?', (project_id,))
    for table in ("settings_dict", "outlines", "foreshadows"):
        if _table_exists(conn, table):
            conn.execute(f"DELETE FROM {table} WHERE project_id=?", (project_id,))
    if _table_exists(conn, "chapter_versions"):
        conn.execute(
            "DELETE FROM chapter_versions WHERE chapter_id IN "
            "(SELECT id FROM chapters WHERE project_id=?)", (project_id,))
    conn.execute("DELETE FROM chapters WHERE project_id=?", (project_id,))
    conn.execute("DELETE FROM projects WHERE id=?", (project_id,))


def restore_project(db, data):
    """Atomically replace one project's current data with an exact full snapshot.

    Existing rows are removed inside the same transaction. Any failed insert or
    verification rolls the entire replacement back to the pre-restore state.
    """
    pid = validate_full_backup(data)
    conn = db.conn
    if conn.in_transaction:
        raise BackupError("数据库已有未提交事务，拒绝开始精确恢复")

    # Task ledger was added after the original schema. Create it only when the
    # backup actually carries ledger rows and the target DB has no ledger schema.
    ledger_rows = (data["tables"].get("task_runs") or
                   data["tables"].get("task_steps") or
                   data["tables"].get("task_plans"))
    if ledger_rows and not _table_exists(conn, "task_runs"):
        try:
            import task_ledger
            task_ledger.ensure_schema(db)
        except Exception as exc:
            raise BackupError(f"无法准备任务台账表以恢复完整备份：{exc}") from exc

    try:
        conn.execute("BEGIN IMMEDIATE")
        delete_project_rows(conn, pid)
        # Parent rows precede dependent records.
        _insert_rows(conn, "projects", data["tables"]["projects"])
        for table in ("settings_dict", "outlines", "foreshadows",
                       "volume_summary", "chat_messages", "task_runs", "task_plans",
                       "chapters", "chapter_versions", "task_steps",
                       "setting_candidates",
                      "story_entities", "story_aliases", "story_facts", "story_extractions",
                      "rewrite_candidates", "rewrite_locks", "review_reports",
                      "style_profiles", "style_suggestions", "task_usage"):
            rows = data["tables"].get(table) or []
            if rows and not _table_exists(conn, table):
                raise BackupError(f"目标数据库缺少数据表 {table}")
            if _table_exists(conn, table):
                _insert_rows(conn, table, rows)
        _verify_restored(conn, data)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {table: len(rows) for table, rows in data["tables"].items()}


def _verify_restored(conn, data):
    pid = data["project_id"]
    for table, rows in data["tables"].items():
        if table == "projects":
            actual = _rows(conn, "SELECT * FROM projects WHERE id=?", (pid,))
        elif table == "chapter_versions":
            if _table_exists(conn, "chapter_versions"):
                actual = _rows(
                    conn, "SELECT v.* FROM chapter_versions v JOIN chapters c "
                    "ON c.id=v.chapter_id WHERE c.project_id=? ORDER BY v.id", (pid,))
            else:
                actual = []
        elif table == "task_steps":
            if (_table_exists(conn, "task_runs")
                    and _table_exists(conn, "task_steps")):
                actual = _rows(
                    conn, "SELECT s.* FROM task_steps s JOIN task_runs r "
                    "ON r.id=s.run_id WHERE r.project_id=? ORDER BY s.id", (pid,))
            else:
                actual = []
        elif not _table_exists(conn, table):
            actual = []
        else:
            actual = _rows(conn, f"SELECT * FROM {table} WHERE project_id=? ORDER BY id", (pid,))
        expected = sorted(rows, key=lambda r: r.get("id", 0))
        actual = sorted(actual, key=lambda r: r.get("id", 0))
        if data['format_version'] < FORMAT_VERSION:
            # New schemas fill default values for columns absent in older
            # snapshots. Compare every archived field exactly.
            actual = [{k: row[k] for k in archived}
                      for row, archived in zip(actual, expected)] if len(actual) == len(expected) else actual
        if actual != expected:
            raise BackupError(f"恢复校验失败：{table} 行内容与备份不一致")


def write_pending_rebuild(db, project_id, backup_path):
    """Persist a crash-recovery marker before destructive rebuild mutations."""
    folder = os.path.dirname(os.path.abspath(db.path))
    backup_abs = os.path.abspath(backup_path)
    try:
        if os.path.commonpath([folder, backup_abs]) != folder:
            raise BackupError("重构备份必须与数据库位于同一目录")
    except ValueError as exc:
        raise BackupError("重构备份路径无效") from exc
    manifest = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "project_id": project_id,
        "backup_file": os.path.basename(backup_abs),
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    path = os.path.join(folder, f"rebuild_pending_{project_id}.json")
    if os.path.exists(path):
        raise BackupError(
            f"项目已有未完成重构标记：{path}；请先重新启动以自动恢复原书")
    _atomic_manifest(path, manifest)
    return path


def _atomic_manifest(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temp_path = os.path.join(directory, ".pending_" + uuid.uuid4().hex + ".tmp")
    try:
        with open(temp_path, "x", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, allow_nan=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


def clear_pending_rebuild(marker_path):
    os.remove(marker_path)


def recover_pending_rebuilds(db):
    """Restore any interrupted rebuilds from markers in the database folder.

    A malformed/missing backup is a hard error. Keeping the marker allows a
    later retry after the underlying file or permissions are repaired.
    """
    folder = os.path.dirname(os.path.abspath(db.path))
    recovered = []
    for marker_path in sorted(
            os.path.join(folder, n) for n in os.listdir(folder)
            if n.startswith("rebuild_pending_") and n.endswith(".json")):
        try:
            with open(marker_path, encoding="utf-8") as fh:
                marker = json.load(fh)
            if (marker.get("format") != FORMAT
                    or marker.get("format_version") not in (1, 2, 3, 4, 5, 6, FORMAT_VERSION)):
                raise BackupError("重构恢复标记版本无效")
            pid = marker.get("project_id")
            expected = f"rebuild_pending_{pid}.json"
            if os.path.basename(marker_path) != expected:
                raise BackupError("重构恢复标记中的项目 id 与文件名不一致")
            backup_name = marker.get("backup_file")
            if (not isinstance(backup_name, str) or not backup_name
                    or os.path.basename(backup_name) != backup_name):
                raise BackupError("重构恢复标记中的备份路径无效")
            backup_path = os.path.join(folder, backup_name)
            data = read_backup(backup_path)
            validate_full_backup(data, project_id=pid)
            restore_project(db, data)
            clear_pending_rebuild(marker_path)
            recovered.append({"project_id": pid, "title": data.get("title", ""),
                              "backup_path": backup_path})
        except Exception as exc:
            if isinstance(exc, BackupError):
                raise
            raise BackupError(f"无法自动恢复未完成的重构 {marker_path}：{exc}") from exc
    return recovered
