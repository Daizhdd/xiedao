# -*- coding: utf-8 -*-
"""AI 小说工作台 · SQLite 数据层
表：projects / settings_dict / chapters / chapter_versions / ai_configs
"""
import os
import sqlite3
from datetime import datetime
from app_paths import data_directory

DB_PATH = str(data_directory() / "novel.db")

CATEGORIES = ("力量体系", "地理", "历史", "术语", "人物")


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class DB:
    def __init__(self, path=None):
        self.path = path or DB_PATH
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.init_db()

    def init_db(self):
        c = self.conn
        c.executescript("""
        CREATE TABLE IF NOT EXISTS projects(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            logline TEXT DEFAULT '',
            genre TEXT DEFAULT '',
            audience TEXT DEFAULT '',
            selling_point TEXT DEFAULT '',
            style_sheet TEXT DEFAULT '',
            status TEXT DEFAULT '立项',
            created_at TEXT, updated_at TEXT);

        CREATE TABLE IF NOT EXISTS settings_dict(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            category TEXT DEFAULT '术语',
            term TEXT NOT NULL,
            definition TEXT DEFAULT '',
            updated_at TEXT);

        CREATE TABLE IF NOT EXISTS chapters(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            volume INTEGER DEFAULT 1,
            chapter_no INTEGER DEFAULT 1,
            title TEXT DEFAULT '',
            chapter_card TEXT DEFAULT '',
            content TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            status TEXT DEFAULT '草稿',
            created_at TEXT, updated_at TEXT);

        CREATE TABLE IF NOT EXISTS chapter_versions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            snapshot TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TEXT);

        CREATE TABLE IF NOT EXISTS ai_configs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            provider TEXT DEFAULT 'openai_compat',
            base_url TEXT DEFAULT '',
            api_key TEXT DEFAULT '',
            model TEXT DEFAULT '',
            is_default INTEGER DEFAULT 0);

        CREATE TABLE IF NOT EXISTS outlines(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            level TEXT DEFAULT '章纲',
            title TEXT NOT NULL,
            content TEXT DEFAULT '',
            chapter_id INTEGER DEFAULT 0,
            sort_no INTEGER DEFAULT 0,
            created_at TEXT, updated_at TEXT);

        CREATE TABLE IF NOT EXISTS foreshadows(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            ftype TEXT DEFAULT '悬念',
            planted_ch TEXT DEFAULT '',
            plan_ch TEXT DEFAULT '',
            status TEXT DEFAULT '待回收',
            note TEXT DEFAULT '',
            created_at TEXT, updated_at TEXT);

        CREATE TABLE IF NOT EXISTS volume_summary(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            volume INTEGER NOT NULL DEFAULT 1,
            summary TEXT DEFAULT '',
            updated_at TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_volsum ON volume_summary(project_id, volume);

        CREATE TABLE IF NOT EXISTS chat_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            role TEXT NOT NULL,                -- user / assistant
            content TEXT DEFAULT '',
            created_at TEXT);
        CREATE INDEX IF NOT EXISTS idx_chatmsg ON chat_messages(project_id, id);

        CREATE TABLE IF NOT EXISTS app_settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '');
        """)
        self.conn.commit()
        self._migrate()
        from ai.story_memory import ensure_schema
        ensure_schema(self)
        from ai.rewrite_candidates import ensure_schema as ensure_rewrite_schema
        ensure_rewrite_schema(self)
        from ai.evidence_review import ensure_schema as ensure_review_schema
        ensure_review_schema(self)
        from ai.style_profile import ensure_schema as ensure_style_schema
        ensure_style_schema(self)
        from ai.setting_candidates import ensure_schema as ensure_setting_candidate_schema
        ensure_setting_candidate_schema(self)

    def _migrate(self):
        """兼容旧库：补加新增列"""
        for stmt in (
            "ALTER TABLE projects ADD COLUMN intro TEXT DEFAULT ''",
            "ALTER TABLE chapters ADD COLUMN outline_id INTEGER DEFAULT 0",  # 本章绑定的章纲条目
            "ALTER TABLE chapters ADD COLUMN memory_pending INTEGER DEFAULT 0",
            "ALTER TABLE chapters ADD COLUMN memory_run_id INTEGER DEFAULT 0",
            "ALTER TABLE chapters ADD COLUMN prefix_summary TEXT DEFAULT ''",
            "ALTER TABLE chapters ADD COLUMN prefix_hash TEXT DEFAULT ''",
            "ALTER TABLE outlines ADD COLUMN volume INTEGER DEFAULT 0",      # 大纲所属卷号（0=自动/未分）
            "ALTER TABLE projects ADD COLUMN plan_volumes INTEGER DEFAULT 0",  # 全书规划总卷数（0=未设）
            "ALTER TABLE projects ADD COLUMN plan_chapters INTEGER DEFAULT 10",  # 每卷章数
            "ALTER TABLE foreshadows ADD COLUMN planted_chapter_id INTEGER DEFAULT 0",
            "ALTER TABLE foreshadows ADD COLUMN planted_hash TEXT DEFAULT ''",
            "ALTER TABLE foreshadows ADD COLUMN planted_quote TEXT DEFAULT ''",
            "ALTER TABLE foreshadows ADD COLUMN planted_pos INTEGER DEFAULT -1",
            "ALTER TABLE foreshadows ADD COLUMN resolve_chapter_id INTEGER DEFAULT 0",
            "ALTER TABLE foreshadows ADD COLUMN resolve_hash TEXT DEFAULT ''",
            "ALTER TABLE foreshadows ADD COLUMN resolve_quote TEXT DEFAULT ''",
            "ALTER TABLE foreshadows ADD COLUMN resolve_pos INTEGER DEFAULT -1",
            "ALTER TABLE foreshadows ADD COLUMN rejected_hash TEXT DEFAULT ''",
            "ALTER TABLE foreshadows ADD COLUMN entity_id INTEGER DEFAULT 0",
            "ALTER TABLE foreshadows ADD COLUMN plan_from INTEGER DEFAULT 0",
            "ALTER TABLE foreshadows ADD COLUMN plan_to INTEGER DEFAULT 0",
        ):
            try:
                self.conn.execute(stmt)
                self.conn.commit()
            except sqlite3.OperationalError:
                pass  # 列已存在

    # ---------- projects ----------
    def create_project(self, title, logline="", genre="", audience="",
                       selling_point="", style_sheet="", status="立项", intro="",
                       plan_volumes=0, plan_chapters=10):
        t = now()
        cur = self.conn.execute(
            "INSERT INTO projects(title,logline,genre,audience,selling_point,style_sheet,status,intro,"
            "plan_volumes,plan_chapters,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (title, logline, genre, audience, selling_point, style_sheet, status, intro,
             plan_volumes, plan_chapters, t, t))
        self.conn.commit()
        return cur.lastrowid

    def get_projects(self):
        return self.conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()

    def get_project(self, pid):
        return self.conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()

    def update_project(self, pid, **kw):
        if not kw:
            return
        kw["updated_at"] = now()
        cols = ", ".join(f"{k}=?" for k in kw)
        self.conn.execute(f"UPDATE projects SET {cols} WHERE id=?", (*kw.values(), pid))
        self.conn.commit()

    def delete_project(self, pid):
        """Back up a complete book, then delete all of its rows atomically.

        Returns the durable JSON backup path. If writing the backup fails, or
        deleting any row fails, the project remains intact.
        """
        if self.conn.in_transaction:
            raise RuntimeError("数据库已有未提交事务，拒绝删除项目")
        has_task_runs = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_runs'"
        ).fetchone()
        if has_task_runs and self.conn.execute(
                "SELECT 1 FROM task_runs WHERE project_id=? AND status='running' LIMIT 1",
                (pid,)).fetchone():
            raise RuntimeError("该项目仍有写作任务运行，任务结束后才能删除")
        # Lazy import avoids making the generic DB schema module depend on file
        # backup code during import. Backup publication completes before BEGIN.
        from book_backup import backup_project, delete_project_rows

        backup_path = backup_project(self, pid, reason="delete")
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            delete_project_rows(self.conn, pid)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return backup_path

    # ---------- settings_dict ----------
    def add_setting(self, project_id, category, term, definition=""):
        t = now()
        cur = self.conn.execute(
            "INSERT INTO settings_dict(project_id,category,term,definition,updated_at) VALUES(?,?,?,?,?)",
            (project_id, category, term, definition, t))
        self.conn.commit()
        return cur.lastrowid

    def get_settings(self, project_id, category=None):
        if category:
            return self.conn.execute(
                "SELECT * FROM settings_dict WHERE project_id=? AND category=? ORDER BY term",
                (project_id, category)).fetchall()
        return self.conn.execute(
            "SELECT * FROM settings_dict WHERE project_id=? ORDER BY category, term",
            (project_id,)).fetchall()

    def update_setting(self, sid, **kw):
        kw["updated_at"] = now()
        cols = ", ".join(f"{k}=?" for k in kw)
        self.conn.execute(f"UPDATE settings_dict SET {cols} WHERE id=?", (*kw.values(), sid))
        self.conn.commit()

    def delete_setting(self, sid):
        self.conn.execute("DELETE FROM settings_dict WHERE id=?", (sid,))
        self.conn.commit()

    # ---------- chapters ----------
    def create_chapter(self, project_id, volume=1, chapter_no=1, title="",
                       content="", chapter_card="", summary="", status="草稿",
                       outline_id=0):
        t = now()
        cur = self.conn.execute(
            "INSERT INTO chapters(project_id,volume,chapter_no,title,chapter_card,content,summary,status,outline_id,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, volume, chapter_no, title, chapter_card, content, summary,
             status, outline_id, t, t))
        self.conn.commit()
        return cur.lastrowid

    def get_chapters(self, project_id):
        return self.conn.execute(
            "SELECT * FROM chapters WHERE project_id=? ORDER BY volume, chapter_no, id",
            (project_id,)).fetchall()

    def get_chapter(self, cid):
        return self.conn.execute("SELECT * FROM chapters WHERE id=?", (cid,)).fetchone()

    def get_chapter_headers(self, project_id):
        return self.conn.execute(
            'SELECT id,project_id,volume,chapter_no,title,chapter_card,summary,outline_id,'
            "prefix_hash,prefix_summary,memory_pending,memory_run_id,"
            "length(trim(content,char(9)||char(10)||char(13)||' ')) content_length "
            'FROM chapters WHERE project_id=? ORDER BY volume,chapter_no,id',
            (project_id,)).fetchall()

    def save_chapter(self, cid, content, note="自动保存", *, expected_hash=None,
                     accept_candidate_id=0, accepted_hunks_json='[]',
                     candidate_status='pending', lock_scope=None, **meta):
        """保存正文：先把当前内容快照进版本表，再更新正文"""
        if expected_hash is not None:
            from ai.rewrite_candidates import digest
            if self.conn.in_transaction:
                raise RuntimeError('数据库已有未提交事务，拒绝采纳候选稿')
            try:
                self.conn.execute('BEGIN IMMEDIATE')
                row = self.get_chapter(cid)
                if row is None or digest(row['content']) != expected_hash:
                    raise ValueError('正文已变化，候选稿不能覆盖当前内容')
                if accept_candidate_id:
                    proposal = self.conn.execute(
                        "SELECT id FROM rewrite_candidates WHERE id=? AND chapter_id=? "
                        "AND project_id=? AND status='pending'",
                        (accept_candidate_id, cid, row['project_id'])).fetchone()
                    if proposal is None:
                        raise ValueError('候选稿不属于当前章节或已经处理')
                if row['content'] != content:
                    from ai.rewrite_candidates import rebase_active_locks
                    rebase_active_locks(self, cid, row['content'], content,
                                        scope=lock_scope)
                    ver = self.conn.execute('SELECT COALESCE(MAX(version),0)+1 AS v '
                                            'FROM chapter_versions WHERE chapter_id=?',
                                            (cid,)).fetchone()['v']
                    self.conn.execute('INSERT INTO chapter_versions(chapter_id,version,snapshot,'
                                      'note,created_at) VALUES(?,?,?,?,?)',
                                      (cid, ver, row['content'], note, now()))
                    meta.setdefault('summary', '')
                    meta.setdefault('memory_pending', 1)
                    meta.setdefault('memory_run_id', 0)
                    meta['prefix_summary'] = ''
                    meta['prefix_hash'] = ''
                    self.conn.execute('DELETE FROM volume_summary WHERE project_id=? AND volume=?',
                                      (row['project_id'], row['volume']))
                meta['content'] = content
                meta['updated_at'] = now()
                cols = ', '.join(f'{key}=?' for key in meta)
                self.conn.execute(f'UPDATE chapters SET {cols} WHERE id=?',
                                  (*meta.values(), cid))
                if accept_candidate_id:
                    self.conn.execute('UPDATE rewrite_candidates SET accepted_hunks_json=?,'
                                      'status=?,updated_at=? WHERE id=?',
                                      (accepted_hunks_json, candidate_status, now(), accept_candidate_id))
                self.conn.commit()
                return
            except Exception:
                self.conn.rollback()
                raise
        row = self.get_chapter(cid)
        if row is None:
            return
        if row["content"].strip() != (content or "").strip():
            self.snapshot(cid, row["content"], note)
        self.update_chapter_meta(cid, content=content, **meta)

    def update_chapter_meta(self, cid, **kw):
        row = self.get_chapter(cid)
        if row is not None:
            changed_body = 'content' in kw and kw['content'] != row['content']
            if changed_body:
                # 摘要与正文必须同版本，保存正文时同步失效，避免崩溃窗口。
                kw.setdefault('summary', '')
                kw.setdefault('memory_pending', 1)
                kw.setdefault('memory_run_id', 0)
                kw['prefix_summary'] = ''
                kw['prefix_hash'] = ''
                self.conn.execute(
                    "UPDATE setting_candidates SET status='stale',updated_at=? "
                    "WHERE source_chapter_id=? AND status='candidate'",
                    (now(), cid))
            if changed_body or any(k in kw and kw[k] != row[k]
                                   for k in ('summary', 'volume', 'chapter_no', 'title')):
                self.conn.execute('DELETE FROM volume_summary WHERE project_id=? AND volume IN (?,?)',
                                  (row['project_id'], row['volume'], kw.get('volume', row['volume'])))
        kw["updated_at"] = now()
        cols = ", ".join(f"{k}=?" for k in kw)
        self.conn.execute(f"UPDATE chapters SET {cols} WHERE id=?", (*kw.values(), cid))
        self.conn.commit()

    def delete_chapter(self, cid):
        row = self.get_chapter(cid)
        if row is not None:
            self.conn.execute('DELETE FROM volume_summary WHERE project_id=? AND volume=?',
                              (row['project_id'], row['volume']))
            self.conn.execute("UPDATE story_facts SET status='stale' "
                              "WHERE source_chapter_id=? AND status!='stale'", (cid,))
            self.conn.execute(
                "UPDATE setting_candidates SET status='stale',updated_at=? "
                "WHERE source_chapter_id=? AND status='candidate'",
                (now(), cid))
            self.conn.execute('DELETE FROM story_extractions WHERE chapter_id=?', (cid,))
            self.conn.execute('DELETE FROM rewrite_candidates WHERE chapter_id=?', (cid,))
            self.conn.execute('DELETE FROM rewrite_locks WHERE chapter_id=?', (cid,))
            self.conn.execute('DELETE FROM review_reports WHERE chapter_id=?', (cid,))
        self.conn.execute("DELETE FROM chapter_versions WHERE chapter_id=?", (cid,))
        self.conn.execute("DELETE FROM chapters WHERE id=?", (cid,))
        self.conn.commit()

    # ---------- 重复章号检测与合并 ----------
    def find_duplicate_chapters(self, project_id):
        """同卷同章号出现多章的分组：[(volume, chapter_no, [chapters行...]), ...]"""
        groups = {}
        for c in self.get_chapters(project_id):
            groups.setdefault((c["volume"], c["chapter_no"]), []).append(c)
        return [(v, n, rows) for (v, n), rows in sorted(groups.items())
                if len(rows) > 1]

    def merge_duplicate_chapters(self, keep_id, drop_ids):
        """把同章号的多余章节并入保留章（调用方必须先做整书备份）：
        - 多余章的全部版本史与当前正文逐份快照进保留章的版本历史，零丢失；
        - 只有正文完全相同才补齐元数据，不把另一版剧情的摘要带入当前稿；
        - 删除多余章。返回动作描述列表。"""
        keep = self.get_chapter(keep_id)
        if keep is None:
            raise RuntimeError("要保留的章节不存在")
        for did in drop_ids:
            drop = self.get_chapter(did)
            if drop is not None and any(drop[k] != keep[k]
                                        for k in ('project_id', 'volume', 'chapter_no')):
                raise RuntimeError('只能合并同一本书、同卷、同章号的章节')
        actions = []
        for did in drop_ids:
            if did == keep_id:
                continue
            drop = self.get_chapter(did)
            if drop is None:
                continue
            for v in self.get_versions(did):
                if (v["snapshot"] or "").strip():
                    self.snapshot(keep_id, v["snapshot"],
                                  f"合并重复章号存档：{drop['title']}(id={did})"
                                  f" 历史v{v['version']}")
            if (drop["content"] or "").strip():
                self.snapshot(keep_id, drop["content"],
                              f"合并重复章号存档：{drop['title']}(id={did}) 正文")
                actions.append(f"《{drop['title']}》(id={did}) 正文已存为保留章的版本")
            same_text = bool((keep['content'] or '').strip()) and keep['content'] == drop['content']
            if (same_text and not (keep["chapter_card"] or "").strip()
                    and (drop["chapter_card"] or "").strip()):
                self.update_chapter_meta(keep_id, chapter_card=drop["chapter_card"])
                keep = self.get_chapter(keep_id)
                actions.append("章节卡取自被合并章")
            if (same_text and not (keep["summary"] or "").strip()
                    and (drop["summary"] or "").strip()):
                self.update_chapter_meta(keep_id, summary=drop["summary"])
                keep = self.get_chapter(keep_id)
                actions.append("摘要取自被合并章")
            if same_text and not keep["outline_id"] and drop["outline_id"]:
                self.update_chapter_meta(keep_id, outline_id=drop["outline_id"])
                keep = self.get_chapter(keep_id)
                actions.append("章纲绑定取自被合并章")
            if same_text:
                self.conn.execute('UPDATE story_facts SET source_chapter_id=? '
                                  'WHERE source_chapter_id=? AND project_id=?',
                                  (keep_id, did, keep['project_id']))
                self.conn.execute('INSERT OR IGNORE INTO story_extractions('
                                  'project_id,chapter_id,content_hash,status,created_at) '
                                  'SELECT project_id,?,content_hash,status,created_at '
                                  'FROM story_extractions WHERE chapter_id=? AND project_id=?',
                                  (keep_id, did, keep['project_id']))
                self.conn.execute('UPDATE rewrite_candidates SET chapter_id=? '
                                  'WHERE chapter_id=? AND project_id=?',
                                  (keep_id, did, keep['project_id']))
                self.conn.execute('UPDATE rewrite_locks SET chapter_id=? '
                                  'WHERE chapter_id=? AND project_id=?',
                                  (keep_id, did, keep['project_id']))
                self.conn.commit()
            self.delete_chapter(did)
            actions.append(f"已删除多余章《{drop['title']}》(id={did})")
        if (keep['content'] or '').strip() and not (keep['summary'] or '').strip():
            self.update_chapter_meta(keep_id, memory_pending=1, memory_run_id=0)
            actions.append('保留稿摘要待补全；下次写作会按保留正文生成')
        return actions

    # ---------- chapter_versions ----------
    def snapshot(self, chapter_id, content, note="自动保存"):
        cur = self.conn.execute(
            "SELECT COALESCE(MAX(version),0)+1 AS v FROM chapter_versions WHERE chapter_id=?",
            (chapter_id,))
        v = cur.fetchone()["v"]
        self.conn.execute(
            "INSERT INTO chapter_versions(chapter_id,version,snapshot,note,created_at) VALUES(?,?,?,?,?)",
            (chapter_id, v, content or "", note, now()))
        self.conn.commit()
        return v

    def get_versions(self, chapter_id):
        return self.conn.execute(
            "SELECT * FROM chapter_versions WHERE chapter_id=? ORDER BY version DESC",
            (chapter_id,)).fetchall()

    def get_version(self, vid):
        return self.conn.execute("SELECT * FROM chapter_versions WHERE id=?", (vid,)).fetchone()

    def restore_version(self, chapter_id, vid):
        v = self.get_version(vid)
        if v is None:
            return
        cur = self.get_chapter(chapter_id)
        if cur:
            self.snapshot(chapter_id, cur["content"], f"回滚前备份(v{v['version']})")
            self.update_chapter_meta(chapter_id, content=v["snapshot"])

    # ---------- ai_configs ----------
    def add_ai_config(self, name, provider, base_url, api_key, model, is_default=0):
        if is_default:
            self.conn.execute("UPDATE ai_configs SET is_default=0")
        cur = self.conn.execute(
            "INSERT INTO ai_configs(name,provider,base_url,api_key,model,is_default) VALUES(?,?,?,?,?,?)",
            (name, provider, base_url, api_key, model, is_default))
        self.conn.commit()
        return cur.lastrowid

    def get_ai_configs(self):
        return self.conn.execute("SELECT * FROM ai_configs ORDER BY is_default DESC, id").fetchall()

    def get_default_config(self):
        row = self.conn.execute("SELECT * FROM ai_configs WHERE is_default=1").fetchone()
        if row is None:
            row = self.conn.execute("SELECT * FROM ai_configs ORDER BY id LIMIT 1").fetchone()
        return row

    def update_ai_config(self, cid, **kw):
        if kw.get("is_default"):
            self.conn.execute("UPDATE ai_configs SET is_default=0")
        cols = ", ".join(f"{k}=?" for k in kw)
        self.conn.execute(f"UPDATE ai_configs SET {cols} WHERE id=?", (*kw.values(), cid))
        self.conn.commit()

    def delete_ai_config(self, cid):
        self.conn.execute("DELETE FROM ai_configs WHERE id=?", (cid,))
        self.conn.commit()

    # ---------- outlines ----------
    def add_outline(self, project_id, level, title, content="", chapter_id=0,
                    sort_no=0, volume=0):
        t = now()
        cur = self.conn.execute(
            "INSERT INTO outlines(project_id,level,title,content,chapter_id,sort_no,volume,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (project_id, level, title, content, chapter_id, sort_no, volume, t, t))
        self.conn.commit()
        return cur.lastrowid

    def get_outline(self, oid):
        return self.conn.execute("SELECT * FROM outlines WHERE id=?", (oid,)).fetchone()

    def get_outlines(self, project_id, level=None):
        if level:
            return self.conn.execute(
                "SELECT * FROM outlines WHERE project_id=? AND level=? ORDER BY sort_no, id",
                (project_id, level)).fetchall()
        return self.conn.execute(
            "SELECT * FROM outlines WHERE project_id=? ORDER BY level, sort_no, id",
            (project_id,)).fetchall()

    def update_outline(self, oid, **kw):
        kw["updated_at"] = now()
        cols = ", ".join(f"{k}=?" for k in kw)
        self.conn.execute(f"UPDATE outlines SET {cols} WHERE id=?", (*kw.values(), oid))
        self.conn.commit()

    def delete_outline(self, oid):
        self.conn.execute("DELETE FROM outlines WHERE id=?", (oid,))
        self.conn.commit()

    # ---------- foreshadows ----------
    def add_foreshadow(self, project_id, content, ftype="悬念", planted_ch="",
                       plan_ch="", status="待回收", note=""):
        t = now()
        cur = self.conn.execute(
            "INSERT INTO foreshadows(project_id,content,ftype,planted_ch,plan_ch,status,note,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (project_id, content, ftype, planted_ch, plan_ch, status, note, t, t))
        self.conn.commit()
        return cur.lastrowid

    def get_foreshadow(self, fid):
        return self.conn.execute("SELECT * FROM foreshadows WHERE id=?", (fid,)).fetchone()

    def get_foreshadows(self, project_id, status=None):
        if status:
            return self.conn.execute(
                "SELECT * FROM foreshadows WHERE project_id=? AND status=? ORDER BY id",
                (project_id, status)).fetchall()
        return self.conn.execute(
            "SELECT * FROM foreshadows WHERE project_id=? ORDER BY status, id",
            (project_id,)).fetchall()

    def update_foreshadow(self, fid, **kw):
        current = self.get_foreshadow(fid)
        if (kw.get('status') == '已回收' and current is not None
                and current['status'] != '已回收'):
            from ai.foreshadow_memory import source_valid
            if current['status'] != '疑似回收' or not source_valid(self, current, 'resolve'):
                raise ValueError('请先在伏笔证据窗口核对有效回收引文')
        if 'plan_ch' in kw and 'plan_from' not in kw and 'plan_to' not in kw:
            from ai.foreshadow_memory import plan_window
            kw['plan_from'], kw['plan_to'] = plan_window(kw['plan_ch'])
        kw["updated_at"] = now()
        cols = ", ".join(f"{k}=?" for k in kw)
        self.conn.execute(f"UPDATE foreshadows SET {cols} WHERE id=?", (*kw.values(), fid))
        self.conn.commit()

    def delete_foreshadow(self, fid):
        self.conn.execute("DELETE FROM foreshadows WHERE id=?", (fid,))
        self.conn.commit()

    # ---------- volume_summary（卷级滚动摘要）----------
    def get_volume_summary(self, project_id, volume):
        r = self.conn.execute(
            "SELECT summary FROM volume_summary WHERE project_id=? AND volume=?",
            (project_id, volume)).fetchone()
        return r["summary"] if r else ""

    def set_volume_summary(self, project_id, volume, summary):
        t = now()
        cur = self.conn.execute(
            "SELECT id FROM volume_summary WHERE project_id=? AND volume=?",
            (project_id, volume)).fetchone()
        if cur:
            self.conn.execute(
                "UPDATE volume_summary SET summary=?, updated_at=? WHERE id=?",
                (summary, t, cur["id"]))
        else:
            self.conn.execute(
                "INSERT INTO volume_summary(project_id,volume,summary,updated_at) VALUES(?,?,?,?)",
                (project_id, volume, summary, t))
        self.conn.commit()

    # ---------- app_settings（全局应用设置，键值对）----------
    def get_setting(self, key, default=""):
        row = self.conn.execute(
            "SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row is not None else default

    def set_setting(self, key, value):
        self.conn.execute(
            "INSERT INTO app_settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)))
        self.conn.commit()

    # ---------- chat_messages（对话历史持久化）----------
    def add_chat_msg(self, project_id, role, content):
        self.conn.execute(
            "INSERT INTO chat_messages(project_id,role,content,created_at) VALUES(?,?,?,?)",
            (project_id, role, content, now()))
        self.conn.commit()

    def get_chat_msgs(self, project_id, limit=300):
        rows = self.conn.execute(
            "SELECT role, content FROM chat_messages WHERE project_id=? "
            "ORDER BY id DESC LIMIT ?", (project_id, int(limit))).fetchall()
        rows.reverse()
        return rows

    def clear_chat_msgs(self, project_id):
        self.conn.execute(
            "DELETE FROM chat_messages WHERE project_id=?", (project_id,))
        self.conn.commit()

    def close(self):
        self.conn.close()


if __name__ == "__main__":
    # 冒烟测试（脱离 GUI）
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    db = DB(p)
    pid = db.create_project("测试书", "一句话概念", "玄幻", "起点男频", "爽点")
    print("project:", db.get_project(pid)["title"])
    db.add_setting(pid, "力量体系", "筑基", "打通经脉，凝聚源力")
    print("settings:", [r["term"] for r in db.get_settings(pid)])
    cid = db.create_chapter(pid, 1, 1, "第一章 重生", "正文v1")
    db.save_chapter(cid, "正文v2")
    db.save_chapter(cid, "正文v3")
    print("versions:", [v["version"] for v in db.get_versions(cid)])
    db.restore_version(cid, db.get_versions(cid)[-1]["id"])
    print("restored content:", db.get_chapter(cid)["content"][:20])
    db.add_ai_config("DeepSeek", "deepseek", "https://api.deepseek.com/v1", "sk-x", "deepseek-chat", 1)
    print("default ai:", db.get_default_config()["name"])
    db.close()
    print("SMOKE OK")
