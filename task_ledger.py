"""Durable, book-scoped record of long-running writing tasks.

This module only stores logical task parameters. Model credentials and prompt
contents are deliberately excluded from the task record.
"""
import json
import os
import hashlib
import sqlite3
from datetime import datetime


_initialized_paths = set()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_schema(db):
    db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS task_runs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            parent_run_id INTEGER DEFAULT 0,
            title TEXT NOT NULL,
            mode TEXT NOT NULL,
            args_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'running',
            error TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            finished_at TEXT DEFAULT '');
        CREATE INDEX IF NOT EXISTS idx_task_runs_book
            ON task_runs(project_id, id);
        CREATE TABLE IF NOT EXISTS task_steps(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            step_key TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT DEFAULT '',
            updated_at TEXT NOT NULL,
            UNIQUE(run_id, step_key));
        CREATE INDEX IF NOT EXISTS idx_task_steps_run
            ON task_steps(run_id, id);
        CREATE TABLE IF NOT EXISTS task_plans(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            args_json TEXT NOT NULL,
            steps TEXT NOT NULL,
            book_revision TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_task_plans_book
            ON task_plans(project_id, status, id);
        CREATE TABLE IF NOT EXISTS task_usage(
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
            run_id INTEGER NOT NULL UNIQUE, calls INTEGER NOT NULL DEFAULT 0,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            unknown_calls INTEGER NOT NULL DEFAULT 0,
            reserved_completion INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL);
    """)
    for statement in (
            "ALTER TABLE task_runs ADD COLUMN book_revision TEXT DEFAULT ''",
            "ALTER TABLE task_steps ADD COLUMN input_hash TEXT DEFAULT ''",
            "ALTER TABLE task_steps ADD COLUMN output_ref TEXT DEFAULT ''",
            "ALTER TABLE task_steps ADD COLUMN attempts INTEGER DEFAULT 0",
            "ALTER TABLE task_steps ADD COLUMN error_kind TEXT DEFAULT ''",
            "ALTER TABLE task_steps ADD COLUMN started_at TEXT DEFAULT ''"):
        try:
            db.conn.execute(statement)
        except sqlite3.OperationalError:
            pass
    db.conn.commit()


def mark_interrupted(db):
    """A single-instance app has no live owner for tasks from a prior launch."""
    db.conn.execute(
        "UPDATE task_runs SET status='interrupted', updated_at=? "
        "WHERE status='running'", (_now(),))
    db.conn.commit()


def mark_resumed(db, run_id):
    """原任务已被一键续跑接管：状态改为 resumed，中断卡不再重复出现。"""
    db.conn.execute(
        "UPDATE task_runs SET status='resumed', updated_at=? WHERE id=?",
        (_now(), run_id))
    db.conn.commit()


def initialize(db):
    """Create tables and mark unfinished work from a previous app process."""
    ensure_schema(db)
    path = os.path.abspath(db.path)
    if path not in _initialized_paths:
        mark_interrupted(db)
        _initialized_paths.add(path)


def create_run(db, project_id, title, mode, args, parent_run_id=0):
    t = _now()
    cur = db.conn.execute(
        "INSERT INTO task_runs(project_id,parent_run_id,title,mode,args_json,"
        "status,created_at,updated_at,book_revision) VALUES(?,?,?,?,?,'running',?,?,?)",
        (project_id, parent_run_id, title, mode,
         json.dumps(args, ensure_ascii=False, sort_keys=True), t, t,
         book_revision(db, project_id, include_style=False)))
    db.conn.commit()
    return cur.lastrowid


def update_run(db, run_id, status, error=""):
    t = _now()
    finished = t if status != "running" else ""
    db.conn.execute(
        "UPDATE task_runs SET status=?, error=?, updated_at=?, finished_at=? "
        "WHERE id=?", (status, error, t, finished, run_id))
    db.conn.commit()


def record_step(db, run_id, key, status, detail=""):
    db.conn.execute(
        "INSERT INTO task_steps(run_id,step_key,status,detail,updated_at) "
        "VALUES(?,?,?,?,?) ON CONFLICT(run_id,step_key) DO UPDATE SET "
        "status=excluded.status,detail=excluded.detail,updated_at=excluded.updated_at",
        (run_id, key, status, detail, _now()))
    db.conn.commit()


def record_phase(db, run_id, chapter_id, phase, status,
                 input_hash='', output_ref='', error_kind=''):
    if not run_id:
        return
    key = f'phase:{chapter_id}:{phase}'
    t = _now()
    db.conn.execute(
        "INSERT INTO task_steps(run_id,step_key,status,detail,updated_at,input_hash,"
        "output_ref,attempts,error_kind,started_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(run_id,step_key) DO UPDATE SET status=excluded.status,"
        "updated_at=excluded.updated_at,input_hash=excluded.input_hash,"
        "output_ref=excluded.output_ref,error_kind=excluded.error_kind,"
        "attempts=task_steps.attempts + excluded.attempts,"
        "started_at=CASE WHEN excluded.status='running' THEN excluded.started_at "
        "ELSE task_steps.started_at END",
        (run_id, key, status, '', t, input_hash, output_ref,
         1 if status == 'running' else 0, error_kind,
         t if status == 'running' else ''))
    db.conn.commit()


def save_usage(db, run_id, project_id, snapshot):
    if not run_id:
        return
    db.conn.execute(
        'INSERT INTO task_usage(project_id,run_id,calls,prompt_tokens,completion_tokens,'
        'reasoning_tokens,unknown_calls,reserved_completion,updated_at) '
        'VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET '
        'calls=excluded.calls,prompt_tokens=excluded.prompt_tokens,'
        'completion_tokens=excluded.completion_tokens,'
        'reasoning_tokens=excluded.reasoning_tokens,'
        'unknown_calls=excluded.unknown_calls,'
        'reserved_completion=excluded.reserved_completion,updated_at=excluded.updated_at',
        (project_id, run_id, snapshot['calls'], snapshot['prompt_tokens'],
         snapshot['completion_tokens'], snapshot['reasoning_tokens'],
         snapshot['unknown_calls'], snapshot['reserved_completion'], _now()))
    db.conn.commit()


def get_usage(db, run_id):
    return db.conn.execute('SELECT * FROM task_usage WHERE run_id=?', (run_id,)).fetchone()


def get_run(db, run_id):
    return db.conn.execute("SELECT * FROM task_runs WHERE id=?", (run_id,)).fetchone()


def checkpoint_run(db, run_id, project_id):
    """Record the latest worker-owned book state for safe recovery after edits."""
    if run_id:
        db.conn.execute('UPDATE task_runs SET book_revision=? WHERE id=? AND project_id=?',
                        (book_revision(db, project_id, include_style=False), run_id, project_id))
        db.conn.commit()


def save_spec(db, run_id, mode, args):
    db.conn.execute('UPDATE task_runs SET mode=?,args_json=? WHERE id=?',
                    (mode, json.dumps(args, ensure_ascii=False, sort_keys=True), run_id))
    db.conn.commit()


def cumulative_usage(db, run_id):
    """Per-attempt usage rows are added once across this book's recovery chain."""
    fields = ('calls', 'prompt_tokens', 'completion_tokens', 'reasoning_tokens',
              'unknown_calls', 'reserved_completion')
    total = dict.fromkeys(fields, 0)
    for ancestor in run_ancestry(db, run_id):
        row = get_usage(db, ancestor)
        if row is not None:
            for field in fields:
                total[field] += max(0, row[field])
    return total


def book_revision(db, project_id, *, include_style=True):
    """Fingerprint data that a plan could overwrite or depend upon."""
    h = hashlib.sha256()
    project = db.get_project(project_id)
    if project is None:
        return ""
    rows = [dict(project)]
    if not include_style:
        rows[0].pop('style_sheet', None)
        rows[0].pop('updated_at', None)
    for getter in (db.get_chapters, db.get_outlines, db.get_settings,
                   db.get_foreshadows):
        rows.extend(dict(row) for row in getter(project_id))
    for table in ("chapter_versions", "volume_summary"):
        if table == "chapter_versions":
            extra = db.conn.execute(
                "SELECT v.* FROM chapter_versions v JOIN chapters c "
                "ON c.id=v.chapter_id WHERE c.project_id=? ORDER BY v.id",
                (project_id,)).fetchall()
        else:
            extra = db.conn.execute(
                "SELECT * FROM volume_summary WHERE project_id=? ORDER BY id",
                (project_id,)).fetchall()
        rows.extend(dict(row) for row in extra)
    # Confirmed narrative facts can change a writing plan's meaning. Pending
    # model candidates are deliberately excluded until the author accepts them.
    if db.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='story_facts'").fetchone():
        rows.extend(dict(row) for row in db.conn.execute(
            "SELECT * FROM story_facts WHERE project_id=? AND status='confirmed' ORDER BY id",
            (project_id,)))
        rows.extend(dict(row) for row in db.conn.execute(
            "SELECT a.* FROM story_aliases a JOIN story_facts f ON f.entity_id=a.entity_id "
            "WHERE f.project_id=? AND f.status='confirmed' ORDER BY a.id", (project_id,)))
    if include_style and db.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='style_profiles'").fetchone():
        rows.extend(dict(row) for row in db.conn.execute(
            'SELECT * FROM style_profiles WHERE project_id=?', (project_id,)))
        rows.extend(dict(row) for row in db.conn.execute(
            "SELECT * FROM style_suggestions WHERE project_id=? AND status='accepted' ORDER BY id",
            (project_id,)))
    h.update(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":")).encode("utf-8"))
    return h.hexdigest()


def create_plan(db, project_id, action, args, steps):
    t = _now()
    cur = db.conn.execute(
        "INSERT INTO task_plans(project_id,action,args_json,steps,book_revision,"
        "status,created_at,updated_at) VALUES(?,?,?,?,?,'pending',?,?)",
        (project_id, action, json.dumps(args, ensure_ascii=False, sort_keys=True),
         steps, book_revision(db, project_id), t, t))
    db.conn.commit()
    return cur.lastrowid


def get_plan(db, plan_id):
    return db.conn.execute("SELECT * FROM task_plans WHERE id=?", (plan_id,)).fetchone()


def list_pending_plans(db, project_id):
    return db.conn.execute(
        "SELECT * FROM task_plans WHERE project_id=? AND status='pending' "
        "ORDER BY id DESC LIMIT 3", (project_id,)).fetchall()


def update_plan(db, plan_id, status):
    db.conn.execute("UPDATE task_plans SET status=?,updated_at=? WHERE id=?",
                    (status, _now(), plan_id))
    db.conn.commit()


def supersede_pending_plans(db, project_id):
    db.conn.execute(
        "UPDATE task_plans SET status='superseded',updated_at=? "
        "WHERE project_id=? AND status='pending'", (_now(), project_id))
    db.conn.commit()


def list_interrupted(db, project_id):
    return db.conn.execute(
        "SELECT * FROM task_runs WHERE project_id=? AND status='interrupted' "
        "ORDER BY id DESC LIMIT 5", (project_id,)).fetchall()


def list_recoverable(db, project_id):
    return db.conn.execute(
        "SELECT * FROM task_runs WHERE project_id=? "
        "AND status IN ('interrupted','stopped','failed','partial','paused_budget') "
        "ORDER BY id DESC LIMIT 8", (project_id,)).fetchall()


# 可一键续跑的任务：内容增量三类天然幂等（已有正文的章节被流水线跳过）；
# 重写三类在 rewrite_instruction 存档进 args_json 后也幂等（父任务台账里
# 已完成的章节按步骤跳过，未完成的带原要求续写）。rebuild_book 涉及整书
# 备份/恢复语义，自动续跑风险高，始终要求用户重新下指令。
RESUMABLE_MODES = ("write_volume", "write_until", "whole_book",
                   "rewrite", "rewrite_volume", "rewrite_book")


def run_ancestry(db, run_id):
    """同一本书的续跑链；损坏的跨书链接和循环不能扩散。"""
    ids, pid = [], None
    while run_id and run_id not in ids:
        row = get_run(db, run_id)
        if row is None or (pid is not None and row['project_id'] != pid):
            break
        pid = row['project_id']
        ids.append(run_id)
        run_id = row['parent_run_id']
    return ids


def completed_chapter_keys(db, run_id):
    """汇总整条续跑链，不丢失祖先任务已经完成的章节。"""
    keys = set()
    for ancestor in run_ancestry(db, run_id):
        rows = db.conn.execute(
            "SELECT step_key FROM task_steps WHERE run_id=? AND status='completed' "
            "AND step_key LIKE 'chapter:%'", (ancestor,)).fetchall()
        keys.update(r['step_key'] for r in rows)
    return keys


def worker_spec(worker):
    from task_execution import worker_spec as serialize
    return serialize(worker)
