# -*- coding: utf-8 -*-
"""Persistent revision proposals and conservative, version-checked adoption."""
import difflib
import hashlib
import json
from datetime import datetime


class StaleCandidate(ValueError):
    """The author changed the draft after this proposal was created."""


def digest(text):
    return hashlib.sha256((text or '').encode('utf-8')).hexdigest()


def ensure_schema(db):
    db.conn.executescript('''
    CREATE TABLE IF NOT EXISTS rewrite_candidates(
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
        chapter_id INTEGER NOT NULL, task_id INTEGER DEFAULT 0,
        base_hash TEXT NOT NULL, base_content TEXT NOT NULL,
        candidate_content TEXT NOT NULL, instruction TEXT DEFAULT '',
        mode TEXT NOT NULL DEFAULT 'full', scope_start INTEGER DEFAULT 0,
        scope_end INTEGER DEFAULT 0, locked_ranges_json TEXT NOT NULL DEFAULT '[]',
        accepted_hunks_json TEXT NOT NULL DEFAULT '[]',
        review_status TEXT NOT NULL DEFAULT 'AI草稿·未审',
        status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_rewrite_chapter
        ON rewrite_candidates(project_id,chapter_id,status,id);
    CREATE TABLE IF NOT EXISTS rewrite_locks(
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
        chapter_id INTEGER NOT NULL, base_hash TEXT NOT NULL,
        start_pos INTEGER NOT NULL, end_pos INTEGER NOT NULL,
        created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_rewrite_locks
        ON rewrite_locks(project_id,chapter_id,base_hash);
    ''')
    db.conn.commit()


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def active_locks(db, chapter_id):
    ch = db.get_chapter(chapter_id)
    if ch is None:
        return []
    rows = db.conn.execute('SELECT * FROM rewrite_locks WHERE project_id=? AND chapter_id=? '
                           'AND base_hash=? ORDER BY start_pos,id',
                           (ch['project_id'], chapter_id, digest(ch['content']))).fetchall()
    return [dict(row) for row in rows]


def lock_selection(db, chapter_id, start, end):
    ch = db.get_chapter(chapter_id)
    if ch is None:
        raise ValueError('章节不存在')
    body = ch['content'] or ''
    if not (0 <= start < end <= len(body)):
        raise ValueError('请先选中要锁定的段落')
    begin = body.rfind('\n', 0, start) + 1
    finish = body.find('\n', end - 1)
    finish = len(body) if finish < 0 else finish + 1
    if not body[begin:finish].strip():
        raise ValueError('不能只锁定空行')
    for row in active_locks(db, chapter_id):
        if begin < row['end_pos'] and finish > row['start_pos']:
            raise ValueError('这个段落已经被锁定或与已有锁定范围重叠')
    cur = db.conn.execute('INSERT INTO rewrite_locks(project_id,chapter_id,base_hash,'
                          'start_pos,end_pos,created_at) VALUES(?,?,?,?,?,?)',
                          (ch['project_id'], chapter_id, digest(body), begin, finish, _now()))
    db.conn.commit()
    return cur.lastrowid


def unlock(db, lock_id, chapter_id):
    db.conn.execute('DELETE FROM rewrite_locks WHERE id=? AND chapter_id=?',
                    (lock_id, chapter_id))
    db.conn.commit()


def _offsets(lines):
    result = [0]
    for line in lines:
        result.append(result[-1] + len(line))
    return result


def diff_hunks(base, candidate):
    before = base.splitlines(keepends=True)
    after = candidate.splitlines(keepends=True)
    bo, ao = _offsets(before), _offsets(after)
    result = []
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == 'replace' and a1 - a0 == b1 - b0 and a1 - a0 > 1:
            # Adjacent changed paragraphs should remain separately adoptable.
            for offset in range(a1 - a0):
                result.append({'index': len(result), 'base_start': bo[a0 + offset],
                               'base_end': bo[a0 + offset + 1],
                               'replacement': candidate[ao[b0 + offset]:ao[b0 + offset + 1]],
                               'before': base[bo[a0 + offset]:bo[a0 + offset + 1]],
                               'tag': tag})
        elif tag != 'equal':
            result.append({'index': len(result), 'base_start': bo[a0],
                           'base_end': bo[a1], 'replacement': candidate[ao[b0]:ao[b1]],
                           'before': base[bo[a0]:bo[a1]], 'tag': tag})
    return result


def compose(base, hunks, accepted):
    out, last = [], 0
    for h in hunks:
        if h['index'] not in accepted:
            continue
        out.append(base[last:h['base_start']])
        out.append(h['replacement'])
        last = h['base_end']
    out.append(base[last:])
    return ''.join(out)


def _verify_locks(base, candidate, locks):
    if base == candidate or not locks:
        return
    before = base.splitlines(keepends=True)
    after = candidate.splitlines(keepends=True)
    offsets = _offsets(before)
    opcodes = difflib.SequenceMatcher(a=before, b=after, autojunk=False).get_opcodes()
    for lock in locks:
        lo, hi = lock['start_pos'], lock['end_pos']
        if not (0 <= lo < hi <= len(base)):
            raise ValueError('锁定段落位置无效，请重新锁定')
        text = base[lo:hi]
        # Repeated paragraphs have ambiguous diff alignment. Reject instead
        # of accidentally treating the other copy as the protected one.
        if base.count(text) != 1 or candidate.count(text) != 1:
            raise ValueError('锁定段落有重复文本，无法安全比对，请改用局部重写')
        protected = False
        for tag, a0, a1, _b0, _b1 in opcodes:
            begin, end = offsets[a0], offsets[a1]
            if tag == 'equal' and begin <= lo and hi <= end:
                protected = True
            elif tag == 'insert' and lo < begin < hi:
                raise ValueError('候选稿改动了锁定段落')
            elif tag != 'equal' and begin < hi and end > lo:
                raise ValueError('候选稿改动了锁定段落')
        if not protected:
            raise ValueError('候选稿未原样保留锁定段落')


def _scope_preserves_locks(base, candidate, scope, locks):
    if scope is None:
        return False
    lo, hi = scope
    return (all(hi <= lock['start_pos'] or lo >= lock['end_pos'] for lock in locks)
            and candidate.startswith(base[:lo]) and candidate.endswith(base[hi:]))


def rebase_active_locks(db, chapter_id, old, new, scope=None):
    """Move protected paragraph offsets as an adopted edit shifts other text.

    The caller holds the same write transaction as the chapter update.
    """
    locks = active_locks(db, chapter_id)
    if not locks or old == new:
        return
    safe_scope = _scope_preserves_locks(old, new, scope, locks)
    if not safe_scope:
        _verify_locks(old, new, locks)
    hunks = diff_hunks(old, new) if not safe_scope else []
    for lock in locks:
        lo, hi = lock['start_pos'], lock['end_pos']
        if safe_scope:
            delta = len(new) - len(old) if scope[1] <= lo else 0
        else:
            delta = sum(len(h['replacement']) - len(h['before']) for h in hunks
                        if h['base_end'] <= lo)
        db.conn.execute('UPDATE rewrite_locks SET base_hash=?,start_pos=?,end_pos=? WHERE id=?',
                        (digest(new), lo + delta, hi + delta, lock['id']))


def create_candidate(db, chapter_id, candidate_content, instruction='', *,
                     mode='full', scope=None, task_id=0,
                     base_content=None, review_status='AI草稿·未审'):
    ch = db.get_chapter(chapter_id)
    if ch is None:
        raise ValueError('章节不存在')
    base = ch['content'] or '' if base_content is None else base_content
    if ch['content'] != base:
        raise StaleCandidate('生成期间正文发生变化，候选稿不会覆盖当前正文')
    if mode not in ('full', 'selection', 'dialogue', 'pace', 'review_issue'):
        raise ValueError('重写方式无效')
    candidate = str(candidate_content or '')
    if not candidate.strip():
        raise ValueError('模型没有返回可用的候选稿')
    if candidate == base:
        raise ValueError('候选稿与当前正文完全相同，无需采纳')
    lo, hi = scope if scope is not None else (0, len(base))
    if not (0 <= lo <= hi <= len(base)):
        raise ValueError('选区已过期')
    if scope is not None and (not candidate.startswith(base[:lo])
                              or not candidate.endswith(base[hi:])):
        raise ValueError('局部重写改动了选区之外的内容')
    locks = [{'start_pos': r['start_pos'], 'end_pos': r['end_pos']}
             for r in active_locks(db, chapter_id)]
    if not _scope_preserves_locks(base, candidate, scope, locks):
        _verify_locks(base, candidate, locks)
    stamp = _now()
    cur = db.conn.execute('INSERT INTO rewrite_candidates(project_id,chapter_id,task_id,'
                          'base_hash,base_content,candidate_content,instruction,mode,'
                          'scope_start,scope_end,locked_ranges_json,review_status,created_at,updated_at) '
                          'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (ch['project_id'], chapter_id, task_id, digest(base), base,
                           candidate, (instruction or '').strip(), mode, lo, hi,
                           json.dumps(locks), review_status, stamp, stamp))
    db.conn.commit()
    return cur.lastrowid


def get_candidate(db, candidate_id):
    return db.conn.execute('SELECT * FROM rewrite_candidates WHERE id=?',
                           (candidate_id,)).fetchone()


def list_candidates(db, chapter_id, pending_only=False):
    ch = db.get_chapter(chapter_id)
    if ch is None:
        return []
    sql = 'SELECT * FROM rewrite_candidates WHERE project_id=? AND chapter_id=?'
    args = [ch['project_id'], chapter_id]
    if pending_only:
        sql += " AND status='pending'"
    return db.conn.execute(sql + ' ORDER BY id DESC', args).fetchall()


def abandon(db, candidate_id):
    row = get_candidate(db, candidate_id)
    if row is None:
        raise ValueError('候选稿不存在')
    db.conn.execute("UPDATE rewrite_candidates SET status='discarded',updated_at=? "
                    "WHERE id=? AND status='pending'", (_now(), candidate_id))
    db.conn.commit()


def accept_hunks(db, candidate_id, selected=None):
    row = get_candidate(db, candidate_id)
    if row is None or row['status'] != 'pending':
        raise ValueError('没有可采纳的候选稿')
    ch = db.get_chapter(row['chapter_id'])
    if ch is None or ch['project_id'] != row['project_id']:
        raise ValueError('候选稿与章节绑定不一致')
    base = row['base_content']
    if digest(base) != row['base_hash']:
        raise ValueError('候选稿基准版本损坏')
    hunks = diff_hunks(base, row['candidate_content'])
    all_ids = {h['index'] for h in hunks}
    accepted = set(json.loads(row['accepted_hunks_json'] or '[]'))
    if selected is None:
        selected = all_ids - accepted
    selected = set(selected)
    if not selected or not selected <= all_ids or selected & accepted:
        raise ValueError('请选择尚未采纳的差异段落')
    expected = compose(base, hunks, accepted)
    if ch['content'] != expected:
        raise StaleCandidate('当前正文已被其他编辑修改；候选稿保留，请重新比较')
    updated = accepted | selected
    result = compose(base, hunks, updated)
    locks = json.loads(row['locked_ranges_json'])
    scope = ((row['scope_start'], row['scope_end'])
             if row['mode'] == 'selection' or
             (row['mode'] in ('dialogue', 'pace') and row['scope_end'] - row['scope_start']
              < len(base)) else None)
    if not _scope_preserves_locks(base, result, scope, locks):
        _verify_locks(base, result, locks)
    completed = updated == all_ids
    current_scope = (scope[0], scope[1] + len(expected) - len(base)) if scope else None
    db.save_chapter(row['chapter_id'], result, note=f"采纳重写候选 #{candidate_id}",
                    expected_hash=digest(expected), accept_candidate_id=candidate_id,
                    lock_scope=current_scope,
                    accepted_hunks_json=json.dumps(sorted(updated)),
                    candidate_status='accepted' if completed else 'pending',
                    status=row['review_status'] if completed else 'AI草稿·未审')
    return result


def accept_full(db, candidate_id):
    return accept_hunks(db, candidate_id, None)


def selection_prompt(base, start, end, mode, instruction, locks):
    if not (0 <= start < end <= len(base)):
        raise ValueError('请先选择要修改的文字')
    if mode not in ('selection', 'dialogue', 'pace'):
        raise ValueError('局部修改类型无效')
    for lock in locks:
        if start < lock['end_pos'] and end > lock['start_pos']:
            raise ValueError('选区碰到锁定段落，先解除锁定或改选区')
    purpose = {'selection': '只改选中部分', 'dialogue': '只改善对话',
               'pace': '保留情节并调整节奏'}[mode]
    user = (f'【修改方式】{purpose}\n【作者要求】{instruction}\n'
            f'【选区前文】{base[max(0, start - 1000):start]}\n'
            f'【选中文字】{base[start:end]}\n'
            f'【选区后文】{base[end:end + 1000]}\n'
            '只输出选中文字的替换内容，不要输出前后文、标题、说明或 Markdown。')
    return user


def compose_selection(base, start, end, replacement):
    if not (0 <= start < end <= len(base)) or not (replacement or '').strip():
        raise ValueError('选区或替换内容无效')
    return base[:start] + replacement + base[end:]


def utf16_to_py(text, position):
    """Convert a QTextCursor UTF-16 position to a Python character offset."""
    raw = text.encode('utf-16-le')
    if not 0 <= position * 2 <= len(raw):
        raise ValueError('选区已超出正文范围')
    try:
        return len(raw[:position * 2].decode('utf-16-le'))
    except UnicodeDecodeError as exc:
        raise ValueError('选区落在字符中间，请重新选择') from exc
