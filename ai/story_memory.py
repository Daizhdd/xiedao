# -*- coding: utf-8 -*-
"""Evidence-backed story facts. Model output remains a candidate until accepted."""
import hashlib
import json
from datetime import datetime


ATTRIBUTES = ('位置', '身体状态', '人物关系', '持有物', '秘密知情')
AUTO_EXTRACT_SETTING = 'story_memory.auto_extract'
SYSTEM_EXTRACT = (
    '从小说正文提取人物状态变化。只输出 JSON：{"facts":['
    '{"entity":"人物名","attribute":"位置/身体状态/人物关系/持有物/秘密知情",'
    '"value":"具体状态","quote":"正文中逐字存在的短句",'
    '"certainty":"asserted/uncertain"}]}。'
    '梦境、谎言、猜测、否定与传闻标 uncertain。不能编造原文引用；'
    'quote 必须在当前正文片段中只出现一次，包含足够前后文以唯一定位；'
    '不要只引用反复出现的人名或短语，没有唯一证据的事实不要输出。'
    '没有明确事实输出空数组。'
)


class MemoryConflict(ValueError):
    def __init__(self, previous):
        self.previous = previous
        super().__init__(f"与已确认状态冲突：{previous['value']}")


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def body_hash(text):
    return hashlib.sha256((text or '').encode('utf-8')).hexdigest()


def ensure_schema(db):
    db.conn.executescript('''
    CREATE TABLE IF NOT EXISTS story_entities(
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
        name TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(project_id,name));
    CREATE TABLE IF NOT EXISTS story_aliases(
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
        entity_id INTEGER NOT NULL, alias TEXT NOT NULL,
        UNIQUE(project_id,entity_id,alias));
    CREATE TABLE IF NOT EXISTS story_facts(
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
        entity_id INTEGER DEFAULT 0, entity_name TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'story', attribute TEXT NOT NULL,
        value TEXT NOT NULL, source_chapter_id INTEGER DEFAULT 0,
        source_hash TEXT DEFAULT '', quote TEXT DEFAULT '',
        quote_start INTEGER DEFAULT -1, quote_end INTEGER DEFAULT -1,
        certainty TEXT NOT NULL DEFAULT 'asserted',
        status TEXT NOT NULL DEFAULT 'candidate', supersedes_id INTEGER DEFAULT 0,
        created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_story_facts_book ON story_facts(project_id,status,id);
    CREATE TABLE IF NOT EXISTS story_extractions(
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
        chapter_id INTEGER NOT NULL, content_hash TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'done', created_at TEXT NOT NULL,
        UNIQUE(project_id,chapter_id,content_hash));
    ''')
    db.conn.commit()


def chapter_order(db, pid):
    rows = db.conn.execute('SELECT id FROM chapters WHERE project_id=? ORDER BY volume,chapter_no,id',
                           (pid,))
    return {row['id']: i for i, row in enumerate(rows)}


def add_entity(db, pid, name):
    name = (name or '').strip()
    if not name or db.get_project(pid) is None:
        raise ValueError('书籍或人物名无效')
    row = db.conn.execute('SELECT id FROM story_entities WHERE project_id=? AND name=?',
                          (pid, name)).fetchone()
    if row:
        return row['id']
    cur = db.conn.execute('INSERT INTO story_entities(project_id,name,created_at) VALUES(?,?,?)',
                          (pid, name, _now()))
    db.conn.commit()
    return cur.lastrowid


def add_alias(db, pid, entity_id, alias):
    alias = (alias or '').strip()
    row = db.conn.execute('SELECT id FROM story_entities WHERE id=? AND project_id=?',
                          (entity_id, pid)).fetchone()
    if row is None or not alias:
        raise ValueError('人物或别名无效')
    db.conn.execute('INSERT OR IGNORE INTO story_aliases(project_id,entity_id,alias) VALUES(?,?,?)',
                    (pid, entity_id, alias))
    db.conn.commit()


def _resolve_entity(db, pid, name):
    direct = db.conn.execute('SELECT id FROM story_entities WHERE project_id=? AND name=?',
                             (pid, name)).fetchone()
    if direct:
        return direct['id']
    aliases = db.conn.execute('SELECT DISTINCT entity_id FROM story_aliases '
                              'WHERE project_id=? AND alias=?', (pid, name)).fetchall()
    if len(aliases) > 1:
        raise ValueError(f'别名「{name}」对应多个人物，请先消除歧义')
    return aliases[0]['entity_id'] if aliases else add_entity(db, pid, name)


def _json_facts(raw):
    s = (raw or '').strip()
    if s.startswith('```'):
        s = s.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    start, end = s.find('{'), s.rfind('}')
    try:
        data = json.loads(s[start:end + 1])
    except (ValueError, TypeError) as exc:
        raise ValueError('故事事实输出不是有效 JSON') from exc
    facts = data.get('facts') if isinstance(data, dict) else None
    if not isinstance(facts, list) or len(facts) > 80:
        raise ValueError('故事事实列表无效或过长')
    return facts


def _segment_facts(raw, segment, begin):
    result = []
    for item in _json_facts(raw):
        if not isinstance(item, dict):
            raise ValueError('故事事实条目格式无效')
        name = str(item.get('entity') or '').strip()
        attr = str(item.get('attribute') or '').strip()
        value = str(item.get('value') or '').strip()
        quote = str(item.get('quote') or '').strip()
        certainty = item.get('certainty')
        if (not name or len(name) > 80 or attr not in ATTRIBUTES
                or not value or len(value) > 300 or not quote or len(quote) > 300
                or certainty not in ('asserted', 'uncertain')):
            raise ValueError('故事事实缺少人物、有效类别、状态、原文证据或确定性')
        local = segment.find(quote)
        if local < 0:
            raise ValueError(f'原文中找不到模型提供的证据：{quote[:30]}')
        if segment.rfind(quote) != local:
            raise ValueError(f'同一片段中证据重复（{quote[:80]!r} 出现 {segment.count(quote)} 次），请提供能唯一定位的更长原文')
        start = begin + local
        result.append((name, attr, value, quote, start, start + len(quote), certainty))
    return result


def extract_candidates(db, chapter_id, model_call):
    """One extraction per exact body version. model_call(system, user) is injectable."""
    ch = db.get_chapter(chapter_id)
    if ch is None or not (ch['content'] or '').strip():
        return 0
    pid, content = ch['project_id'], ch['content']
    digest = body_hash(content)
    if db.conn.execute('SELECT 1 FROM story_extractions WHERE project_id=? AND chapter_id=? '
                       'AND content_hash=? AND status=?',
                       (pid, chapter_id, digest, 'done')).fetchone():
        return 0
    parsed = []
    # Most chapters fit in one call. Longer chapters are covered in bounded pieces.
    for begin in range(0, len(content), 7500):
        segment = content[begin:begin + 7500]
        user = '【本章正文片段】\n' + segment
        for attempt in range(2):
            raw = model_call(SYSTEM_EXTRACT, user)
            try:
                parsed.extend(_segment_facts(raw, segment, begin))
                break
            except ValueError as exc:
                if attempt:
                    raise
                user = ('【本章正文片段】\n' + segment + '\n\n【上一轮校验失败】'
                        + str(exc) + '\n请修正引文与字段后重新输出 JSON，引用必须逐字存在且只出现一次；'
                        '无法给出唯一证据的条目不要输出。\n【上一轮输出】\n' + (raw or '')[:5000])
    if db.get_chapter(chapter_id)['content'] != content:
        raise RuntimeError('分析期间正文已改变，请重新分析')
    seen = set()
    try:
        db.conn.execute('BEGIN IMMEDIATE')
        # Hold the write lock while checking the input version and saving facts.
        if db.get_chapter(chapter_id)['content'] != content:
            raise RuntimeError('分析期间正文已改变，请重新分析本章')
        for name, attr, value, quote, start, end, certainty in parsed:
            key = (name, attr, value, quote, start)
            if key in seen:
                continue
            seen.add(key)
            db.conn.execute('INSERT INTO story_facts(project_id,entity_name,kind,attribute,'
                            'value,source_chapter_id,source_hash,quote,quote_start,quote_end,'
                            'certainty,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (pid, name, 'story', attr, value, chapter_id, digest,
                             quote, start, end, certainty, 'candidate', _now()))
        db.conn.execute('INSERT OR IGNORE INTO story_extractions(project_id,chapter_id,'
                        'content_hash,status,created_at) VALUES(?,?,?,?,?)',
                        (pid, chapter_id, digest, 'done', _now()))
        db.conn.commit()
    except Exception:
        db.conn.rollback()
        raise
    return len(seen)


def _valid_source(db, fact):
    if fact['kind'] == 'rule':
        return fact['source_chapter_id'] == 0
    ch = db.get_chapter(fact['source_chapter_id'])
    if ch is None or ch['project_id'] != fact['project_id']:
        return False
    content = ch['content'] or ''
    start, end = fact['quote_start'], fact['quote_end']
    return (fact['source_hash'] == body_hash(content) and 0 <= start < end <= len(content)
            and content[start:end] == fact['quote'])


def list_facts(db, pid, chapter_id=None):
    sql = 'SELECT * FROM story_facts WHERE project_id=?'
    args = [pid]
    if chapter_id is not None:
        sql += ' AND source_chapter_id=?'
        args.append(chapter_id)
    sql += ' ORDER BY id DESC'
    return db.conn.execute(sql, args).fetchall()


def update_candidate(db, fact_id, name, attribute, value, quote):
    fact = db.conn.execute('SELECT * FROM story_facts WHERE id=?', (fact_id,)).fetchone()
    if fact is None or fact['status'] != 'candidate' or fact['kind'] != 'story':
        raise ValueError('只能修改当前的故事事实候选')
    ch = db.get_chapter(fact['source_chapter_id'])
    if ch is None or not _valid_source(db, fact):
        raise ValueError('原文已变化，候选证据过期')
    name, attribute, value, quote = [str(x).strip() for x in (name, attribute, value, quote)]
    if not name or attribute not in ATTRIBUTES or not value or not quote:
        raise ValueError('人物、类别、值和原文证据都必须填写')
    start = (ch['content'] or '').find(quote)
    if start < 0:
        raise ValueError('编辑后的证据必须逐字出现在当前正文中')
    db.conn.execute('UPDATE story_facts SET entity_name=?,attribute=?,value=?,quote=?,'
                    'quote_start=?,quote_end=? WHERE id=?',
                    (name, attribute, value, quote, start, start + len(quote), fact_id))
    db.conn.commit()


def reject_fact(db, fact_id):
    db.conn.execute("UPDATE story_facts SET status='rejected' WHERE id=? AND status IN ('candidate','conflict')",
                    (fact_id,))
    db.conn.commit()


def confirm_fact(db, fact_id, replace=False, entity_id=None):
    fact = db.conn.execute('SELECT * FROM story_facts WHERE id=?', (fact_id,)).fetchone()
    if fact is None or fact['status'] not in ('candidate', 'conflict'):
        raise ValueError('候选事实不存在或已处理')
    if not _valid_source(db, fact):
        raise ValueError('正文或证据已变化，请重新分析本章')
    pid = fact['project_id']
    if entity_id is not None:
        known = db.conn.execute('SELECT id FROM story_entities WHERE id=? AND project_id=?',
                                (entity_id, pid)).fetchone()
        if known is None:
            raise ValueError('选中的人物不属于这本书')
        if fact['entity_name'] != db.conn.execute(
                'SELECT name FROM story_entities WHERE id=?', (entity_id,)).fetchone()['name']:
            add_alias(db, pid, entity_id, fact['entity_name'])
    else:
        entity_id = _resolve_entity(db, pid, fact['entity_name'])
    others = db.conn.execute("SELECT * FROM story_facts WHERE project_id=? AND entity_id=? "
                             "AND kind=? AND attribute=? AND status='confirmed' AND id!=? ORDER BY id DESC",
                             (pid, entity_id, fact['kind'], fact['attribute'], fact_id)).fetchall()
    conflict = next((r for r in others if r['value'] != fact['value'] and _valid_source(db, r)), None)
    if conflict is not None and not replace:
        db.conn.execute("UPDATE story_facts SET status='conflict',entity_id=? WHERE id=?",
                        (entity_id, fact_id))
        db.conn.commit()
        raise MemoryConflict(conflict)
    order = chapter_order(db, pid)
    prior = (conflict if conflict and
             (fact['kind'] == 'rule' or order.get(conflict['source_chapter_id'], -1)
              < order.get(fact['source_chapter_id'], -1)) else None)
    db.conn.execute("UPDATE story_facts SET status='confirmed',entity_id=?,supersedes_id=? WHERE id=?",
                    (entity_id, prior['id'] if prior else 0, fact_id))
    db.conn.commit()


def add_author_rule(db, pid, entity_name, attribute, value):
    if attribute not in ATTRIBUTES or not (value or '').strip():
        raise ValueError('规则类别或内容无效')
    entity_id = _resolve_entity(db, pid, entity_name.strip())
    cur = db.conn.execute('INSERT INTO story_facts(project_id,entity_id,entity_name,kind,'
                          'attribute,value,status,created_at) VALUES(?,?,?,?,?,?,?,?)',
                          (pid, entity_id, entity_name.strip(), 'rule', attribute,
                           value.strip(), 'confirmed', _now()))
    db.conn.commit()
    return cur.lastrowid


def state_before(db, pid, chapter_id):
    order = chapter_order(db, pid)
    if chapter_id not in order:
        raise ValueError('目标章节不属于本书')
    selected = {}
    rows = db.conn.execute("SELECT * FROM story_facts WHERE project_id=? AND status='confirmed' ORDER BY id",
                           (pid,)).fetchall()
    for fact in rows:
        if not _valid_source(db, fact):
            continue
        source = fact['source_chapter_id']
        if source and (source not in order or order[source] >= order[chapter_id]):
            continue
        key = (fact['entity_id'], fact['kind'], fact['attribute'])
        position = -1 if fact['kind'] == 'rule' else order[source]
        old = selected.get(key)
        if old is None or (position, fact['id']) > old[0]:
            selected[key] = ((position, fact['id']), fact)
    return [pair[1] for pair in selected.values()]


def memory_for_chapter(db, chapter_id, max_chars=850):
    ch = db.get_chapter(chapter_id)
    if ch is None:
        return {'text': '', 'used': [], 'omitted': []}
    facts = state_before(db, ch['project_id'], chapter_id)
    names = {r['id']: [r['name']] for r in db.conn.execute(
        'SELECT id,name FROM story_entities WHERE project_id=?', (ch['project_id'],))}
    for r in db.conn.execute('SELECT entity_id,alias FROM story_aliases WHERE project_id=?',
                             (ch['project_id'],)):
        names.setdefault(r['entity_id'], []).append(r['alias'])
    previous = db.conn.execute(
        'SELECT substr(content,-1000) tail FROM chapters WHERE project_id=? '
        'AND (volume,chapter_no,id)<(?,?,?) ORDER BY volume DESC,chapter_no DESC,id DESC LIMIT 2',
        (ch['project_id'], ch['volume'], ch['chapter_no'], chapter_id)).fetchall()
    probe = (ch['chapter_card'] or '') + '\n' + '\n'.join(
        r['tail'] or '' for r in reversed(previous))
    def rank(f):
        hit = any(n and n in probe for n in names.get(f['entity_id'], []))
        return (2 if hit else 0) + (1 if f['kind'] == 'rule' else 0)
    facts.sort(key=lambda f: (rank(f), f['id']), reverse=True)
    used, omitted, lines = [], [], []
    for fact in facts:
        entity = db.conn.execute('SELECT name FROM story_entities WHERE id=?',
                                 (fact['entity_id'],)).fetchone()
        name = entity['name'] if entity else fact['entity_name']
        source = (f"作者规则" if fact['kind'] == 'rule' else
                  f"第{db.get_chapter(fact['source_chapter_id'])['chapter_no']}章")
        line = f"- {name}｜{fact['attribute']}：{fact['value']}（{source}）"
        if len('\n'.join(lines + [line])) <= max_chars and len(used) < 10:
            lines.append(line)
            used.append({'fact': fact, 'reason': '本章相关' if rank(fact) >= 2 else '近期状态',
                         'source': source})
        else:
            omitted.append(fact)
    return {'text': '\n'.join(lines), 'used': used, 'omitted': omitted}
