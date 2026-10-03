# -*- coding: utf-8 -*-
"""Book-scoped author rules, defaults, and opt-in style suggestions."""
from datetime import datetime


FIELDS = ('genre', 'audience', 'point_of_view', 'tense', 'pacing',
          'density', 'dialogue', 'sample', 'banned', 'rules')
LABELS = {'genre': '题材', 'audience': '目标读者', 'point_of_view': '叙述视角',
          'tense': '时间表达', 'pacing': '节奏', 'density': '描写密度',
          'dialogue': '对话倾向', 'sample': '参考样章',
          'banned': '禁用表达', 'rules': '作者确认规则'}


def ensure_schema(db):
    db.conn.executescript('''
    CREATE TABLE IF NOT EXISTS style_profiles(
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL UNIQUE,
        genre TEXT DEFAULT '', audience TEXT DEFAULT '', point_of_view TEXT DEFAULT '',
        tense TEXT DEFAULT '', pacing TEXT DEFAULT '', density TEXT DEFAULT '',
        dialogue TEXT DEFAULT '', sample TEXT DEFAULT '', banned TEXT DEFAULT '',
        rules TEXT DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS style_suggestions(
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
        text TEXT NOT NULL, reason TEXT NOT NULL, example TEXT NOT NULL,
        scope TEXT NOT NULL DEFAULT '下一卷', status TEXT NOT NULL DEFAULT 'candidate',
        created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_style_suggestions
        ON style_suggestions(project_id,status,id);
    ''')
    db.conn.commit()


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def get_profile(db, pid):
    return db.conn.execute('SELECT * FROM style_profiles WHERE project_id=?',
                           (pid,)).fetchone()


def save_profile(db, pid, values):
    if db.get_project(pid) is None:
        raise ValueError('作品不存在')
    fields = {name: str(values.get(name) or '').strip()[:2500] for name in FIELDS}
    if len(fields['sample']) > 1600:
        fields['sample'] = fields['sample'][:1600]
    old = get_profile(db, pid)
    if old and all(old[name] == fields[name] for name in FIELDS):
        return old['revision']
    if old:
        sets = ','.join(f'{name}=?' for name in FIELDS)
        db.conn.execute(f'UPDATE style_profiles SET {sets},revision=?,updated_at=? '
                        'WHERE project_id=?',
                        (*fields.values(), old['revision'] + 1, _now(), pid))
    else:
        cols = ','.join(FIELDS)
        marks = ','.join('?' for _ in FIELDS)
        db.conn.execute(f'INSERT INTO style_profiles(project_id,{cols},revision,updated_at) '
                        f'VALUES(?,{marks},1,?)', (pid, *fields.values(), _now()))
    db.conn.commit()
    return get_profile(db, pid)['revision']


def suggestions(db, pid):
    return db.conn.execute('SELECT * FROM style_suggestions WHERE project_id=? ORDER BY id DESC',
                           (pid,)).fetchall()


def add_suggestion(db, pid, text, reason, example, scope='下一卷'):
    text, reason, example = [str(x or '').strip() for x in (text, reason, example)]
    if not text or not reason or not example or db.get_project(pid) is None:
        raise ValueError('风格建议需要内容、依据和示例')
    old = db.conn.execute('SELECT id FROM style_suggestions WHERE project_id=? AND text=? '
                          "AND status IN ('candidate','accepted','rejected') LIMIT 1",
                          (pid, text)).fetchone()
    if old:
        return old['id']
    cur = db.conn.execute('INSERT INTO style_suggestions(project_id,text,reason,example,'
                          'scope,status,created_at) VALUES(?,?,?,?,?,?,?)',
                          (pid, text[:450], reason[:800], example[:800],
                           scope[:40], 'candidate', _now()))
    db.conn.commit()
    return cur.lastrowid


def decide_suggestion(db, pid, suggestion_id, accept):
    row = db.conn.execute('SELECT * FROM style_suggestions WHERE id=? AND project_id=?',
                          (suggestion_id, pid)).fetchone()
    if row is None or row['status'] != 'candidate':
        raise ValueError('建议不存在或已经处理')
    db.conn.execute('UPDATE style_suggestions SET status=? WHERE id=?',
                    ('accepted' if accept else 'rejected', suggestion_id))
    db.conn.commit()


def effective_style(db, pid, task_override=''):
    project = db.get_project(pid)
    if project is None:
        return ''
    profile = get_profile(db, pid)
    parts = []
    if task_override:
        parts.append('【本次明确要求，优先执行】' + task_override.strip())
    if project['style_sheet']:
        parts.append('【作者原有风格规范】\n' + project['style_sheet'].strip())
    if profile:
        # Put explicit author rules before optional defaults and examples so
        # a bounded context never drops the highest-priority instructions.
        for field in ('rules', 'banned', 'point_of_view', 'tense', 'genre',
                      'audience', 'pacing', 'density', 'dialogue', 'sample'):
            if profile[field]:
                parts.append(f"【{LABELS[field]}】{profile[field]}")
    accepted = [row for row in suggestions(db, pid) if row['status'] == 'accepted']
    if accepted:
        parts.append('【作者已采纳的风格建议】\n' + '\n'.join(
            f"- {r['text']}（范围：{r['scope']}）" for r in accepted[:12]))
    return '\n'.join(parts)
