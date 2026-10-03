# -*- coding: utf-8 -*-
"""Evidence-backed setting suggestions. Only an author can promote them."""
from ai.story_memory import body_hash
from db import CATEGORIES, now


def ensure_schema(db):
    db.conn.executescript('''
    CREATE TABLE IF NOT EXISTS setting_candidates(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        source_chapter_id INTEGER NOT NULL,
        source_hash TEXT NOT NULL,
        quote TEXT NOT NULL,
        quote_start INTEGER NOT NULL,
        quote_end INTEGER NOT NULL,
        category TEXT NOT NULL,
        term TEXT NOT NULL,
        definition TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'candidate',
        accepted_setting_id INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_setting_candidates_book
        ON setting_candidates(project_id,status,id);
    ''')
    db.conn.commit()


def _fields(content, category, term, definition, quote):
    category, term, definition, quote = (
        str(value or '').strip() for value in (category, term, definition, quote))
    if category not in CATEGORIES:
        raise ValueError('设定分类无效')
    if not 2 <= len(term) <= 30 or not definition or len(definition) > 500:
        raise ValueError('设定词条或定义无效')
    if not quote or len(quote) > 300 or content.count(quote) != 1:
        raise ValueError('设定候选需要正文中唯一可定位的逐字证据')
    start = content.index(quote)
    return category, term, definition, quote, start, start + len(quote)


def get_candidate(db, candidate_id):
    return db.conn.execute('SELECT * FROM setting_candidates WHERE id=?',
                           (candidate_id,)).fetchone()


def list_candidates(db, project_id):
    return db.conn.execute(
        "SELECT * FROM setting_candidates WHERE project_id=? "
        "ORDER BY CASE status WHEN 'candidate' THEN 0 ELSE 1 END, id DESC",
        (project_id,)).fetchall()


def valid_source(db, candidate):
    if candidate is None:
        return False
    chapter = db.get_chapter(candidate['source_chapter_id'])
    if chapter is None or chapter['project_id'] != candidate['project_id']:
        return False
    content = chapter['content'] or ''
    start, end = candidate['quote_start'], candidate['quote_end']
    return (candidate['source_hash'] == body_hash(content)
            and 0 <= start < end <= len(content)
            and content[start:end] == candidate['quote'])


def propose_candidate(db, chapter_id, category, term, definition, quote):
    chapter = db.get_chapter(chapter_id)
    if chapter is None:
        raise ValueError('来源章节不存在')
    content = chapter['content'] or ''
    category, term, definition, quote, start, end = _fields(
        content, category, term, definition, quote)
    pid = chapter['project_id']
    if any(row['term'] == term for row in db.get_settings(pid)):
        raise ValueError('同名设定已由作者确认')
    existing = db.conn.execute(
        "SELECT * FROM setting_candidates WHERE project_id=? AND term=? "
        "AND status='candidate' ORDER BY id DESC LIMIT 1", (pid, term)).fetchone()
    digest = body_hash(content)
    if existing:
        if (existing['source_chapter_id'] == chapter_id
                and existing['source_hash'] == digest
                and existing['quote'] == quote
                and existing['category'] == category
                and existing['definition'] == definition):
            return existing['id']
        raise ValueError('同名设定候选已存在，请先审核')
    stamp = now()
    cur = db.conn.execute(
        'INSERT INTO setting_candidates(project_id,source_chapter_id,source_hash,'
        'quote,quote_start,quote_end,category,term,definition,status,'
        'created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
        (pid, chapter_id, digest, quote, start, end, category, term,
         definition, 'candidate', stamp, stamp))
    db.conn.commit()
    return cur.lastrowid


def update_candidate(db, candidate_id, category, term, definition, quote):
    candidate = get_candidate(db, candidate_id)
    if candidate is None or candidate['status'] != 'candidate':
        raise ValueError('只能编辑待确认的设定候选')
    if not valid_source(db, candidate):
        raise ValueError('来源正文已变化，请重新提取候选')
    chapter = db.get_chapter(candidate['source_chapter_id'])
    category, term, definition, quote, start, end = _fields(
        chapter['content'] or '', category, term, definition, quote)
    if any(row['term'] == term for row in db.get_settings(candidate['project_id'])):
        raise ValueError('同名设定已由作者确认')
    other = db.conn.execute(
        "SELECT 1 FROM setting_candidates WHERE project_id=? AND term=? "
        "AND status='candidate' AND id<>? LIMIT 1",
        (candidate['project_id'], term, candidate_id)).fetchone()
    if other:
        raise ValueError('同名设定候选已存在')
    db.conn.execute(
        'UPDATE setting_candidates SET category=?,term=?,definition=?,quote=?,'
        'quote_start=?,quote_end=?,updated_at=? WHERE id=?',
        (category, term, definition, quote, start, end, now(), candidate_id))
    db.conn.commit()


def confirm_candidate(db, candidate_id):
    if db.conn.in_transaction:
        raise ValueError('数据库有未完成的写入，请稍后确认')
    db.conn.execute('BEGIN IMMEDIATE')
    try:
        candidate = get_candidate(db, candidate_id)
        if candidate is not None and candidate['status'] == 'stale':
            raise ValueError('来源正文已变化，不能确认；请重新提取候选')
        if candidate is None or candidate['status'] != 'candidate':
            raise ValueError('这条设定候选已经处理')
        if not valid_source(db, candidate):
            raise ValueError('来源正文已变化，不能确认；请重新提取候选')
        if any(row['term'] == candidate['term']
               for row in db.get_settings(candidate['project_id'])):
            raise ValueError('同名设定已存在，请先检查正式设定库')
        stamp = now()
        cur = db.conn.execute(
            'INSERT INTO settings_dict(project_id,category,term,definition,updated_at) '
            'VALUES(?,?,?,?,?)',
            (candidate['project_id'], candidate['category'], candidate['term'],
             candidate['definition'], stamp))
        db.conn.execute(
            "UPDATE setting_candidates SET status='accepted',accepted_setting_id=?,"
            'updated_at=? WHERE id=?', (cur.lastrowid, stamp, candidate_id))
        db.conn.commit()
    except Exception:
        db.conn.rollback()
        raise
    return cur.lastrowid


def reject_candidate(db, candidate_id):
    candidate = get_candidate(db, candidate_id)
    if candidate is None or candidate['status'] != 'candidate':
        raise ValueError('这条设定候选已经处理')
    db.conn.execute(
        "UPDATE setting_candidates SET status='rejected',updated_at=? WHERE id=?",
        (now(), candidate_id))
    db.conn.commit()
