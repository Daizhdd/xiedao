# -*- coding: utf-8 -*-
"""Evidence-checked, versioned review reports for an accepted chapter."""
import json
import sqlite3
from datetime import datetime

from ai import story_memory
from ai import client as aiclient


CATEGORIES = ('人物一致性', '事件因果', '章纲完成度', '文风', '节奏')
SEVERITIES = ('严重', '一般', '建议')
SYSTEM = ('你是小说审稿编辑。审查人物一致性、事件因果、章纲完成度、文风、节奏。'
          '只输出 JSON：{"score":0-100,"categories":{"人物一致性":0-100,'
          '"事件因果":0-100,"章纲完成度":0-100,"文风":0-100,"节奏":0-100},'
          '"issues":[{"kind":"上述类别之一","severity":"严重/一般/建议",'
          '"body_quote":"正文逐字引文","source_ref":"fact:数字/outline:数字/空串",'
          '"source_quote":"依据原文逐字引文或空串","explanation":"原因",'
          '"suggestion":"可执行修改建议"}]}。'
          '客观冲突必须引用给定依据，主观建议不冒充事实冲突。没有依据时 severity 必须为建议，'
          'source_ref 与 source_quote 均为空；有依据时两者必须逐字匹配给定来源。'
          '正文引文必须在本章出现且只出现一次，字符串内双引号必须正确转义。')


def ensure_schema(db):
    db.conn.executescript('''
    CREATE TABLE IF NOT EXISTS review_reports(
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
        chapter_id INTEGER NOT NULL, body_hash TEXT NOT NULL,
        status TEXT NOT NULL, score INTEGER, min_score INTEGER NOT NULL DEFAULT 75,
        categories_json TEXT NOT NULL DEFAULT '{}',
        issues_json TEXT NOT NULL DEFAULT '[]', error TEXT DEFAULT '',
        created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_review_reports
        ON review_reports(project_id,chapter_id,id);
    ''')
    try:
        db.conn.execute('ALTER TABLE review_reports ADD COLUMN min_score INTEGER NOT NULL DEFAULT 75')
    except sqlite3.OperationalError as exc:
        if 'duplicate column' not in str(exc).lower():
            raise
    db.conn.commit()


def sources_for(db, chapter):
    pid, cid = chapter['project_id'], chapter['id']
    result = {}
    for fact in story_memory.state_before(db, pid, cid):
        if fact['kind'] == 'story':
            result[f"fact:{fact['id']}"] = fact['quote']
    if chapter['outline_id']:
        outline = db.get_outline(chapter['outline_id'])
        if outline is not None and outline['project_id'] == pid:
            result[f"outline:{outline['id']}"] = outline['content'] or ''
    return result


def review_prompt(db, chapter):
    from ai import style_profile
    evidence = '\n'.join(f'- {key}：{value[:450]}'
                         for key, value in sources_for(db, chapter).items())
    return ('【可引用依据】\n' + (evidence or '（没有已确认的前文事实或绑定章纲）')
            + '\n【本书作品文风】\n' + style_profile.effective_style(db, chapter['project_id'])
            + '\n【当前正文】\n' + (chapter['content'] or '')[:12000]
            + '\n只能引用上述依据，不得编造来源。请按 JSON 输出审稿结果。')


def parse_report(raw, body, sources):
    s = (raw or '').strip()
    if s.startswith('```'):
        s = s.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    first, last = s.find('{'), s.rfind('}')
    try:
        obj = json.loads(s[first:last + 1])
    except (ValueError, TypeError) as exc:
        raise ValueError('审稿输出不是有效 JSON') from exc
    if not isinstance(obj, dict) or type(obj.get('score')) is not int or not 0 <= obj['score'] <= 100:
        raise ValueError('审稿总分无效')
    categories = obj.get('categories')
    if (not isinstance(categories, dict) or set(categories) != set(CATEGORIES)
            or any(type(v) is not int or not 0 <= v <= 100 for v in categories.values())):
        raise ValueError('审稿分项分数缺失或无效')
    raw_issues = obj.get('issues')
    if not isinstance(raw_issues, list) or len(raw_issues) > 40:
        raise ValueError('审稿问题列表无效')
    issues = []
    for item in raw_issues:
        if not isinstance(item, dict):
            raise ValueError('审稿问题格式无效')
        kind, severity = item.get('kind'), item.get('severity')
        quote, ref = item.get('body_quote'), item.get('source_ref') or ''
        source_quote = item.get('source_quote') or ''
        explanation, suggestion = item.get('explanation'), item.get('suggestion')
        if (kind not in CATEGORIES or severity not in SEVERITIES
                or not isinstance(quote, str) or not quote.strip()
                or not isinstance(explanation, str) or not explanation.strip()
                or not isinstance(suggestion, str) or not suggestion.strip()
                or body.count(quote) != 1):
            raise ValueError('审稿问题缺少可定位的正文引文或必要字段')
        if kind in CATEGORIES[:3] and severity != '建议' and (ref not in sources or not isinstance(source_quote, str)
                                       or not source_quote or source_quote not in sources[ref]):
            raise ValueError('客观冲突缺少可核查依据')
        if ref and (ref not in sources or not isinstance(source_quote, str)
                    or not source_quote or source_quote not in sources[ref]):
            raise ValueError('审稿引用了不存在的依据')
        if not ref and source_quote:
            raise ValueError('审稿依据没有来源编号')
        issues.append({'kind': kind, 'severity': severity, 'body_quote': quote,
                       'body_start': body.index(quote), 'source_ref': ref,
                       'source_quote': source_quote, 'explanation': explanation,
                       'suggestion': suggestion, 'state': 'open'})
    return {'score': obj['score'], 'categories': categories, 'issues': issues}


def has_unresolved_severe(issues):
    return any(issue['severity'] == '严重'
               and issue.get('state', 'open') not in ('ignored', 'not_issue')
               for issue in issues)


def chapter_status(report_status, score, issues, min_score):
    if report_status != 'reviewed':
        return 'AI草稿·未审'
    if has_unresolved_severe(issues):
        return 'AI草稿·待核'
    return 'AI草稿·低分' if score < min_score else 'AI草稿'


def create_report(db, chapter_id, raw, min_score=75, expected_hash=None):
    if db.conn.in_transaction:
        raise ValueError('数据库有未完成的写入，请稍后审稿')
    db.conn.execute('BEGIN IMMEDIATE')
    try:
        ch = db.get_chapter(chapter_id)
        if ch is None or not (ch['content'] or '').strip():
            raise ValueError('章节没有可审的正文')
        digest = story_memory.body_hash(ch['content'])
        if expected_hash is not None and digest != expected_hash:
            raise ValueError('审稿对应的正文版本已变化，本次结果不入库')
        min_score = int(min_score)
        if not 0 <= min_score <= 100:
            raise ValueError('审稿门槛无效')
        try:
            parsed = parse_report(raw, ch['content'], sources_for(db, ch))
            status, error = 'reviewed', ''
        except ValueError as exc:
            parsed = {'score': None, 'categories': {}, 'issues': []}
            status, error = 'unreviewed', str(exc)
        stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cur = db.conn.execute(
            'INSERT INTO review_reports(project_id,chapter_id,body_hash,status,score,min_score,'
            'categories_json,issues_json,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (ch['project_id'], chapter_id, digest, status, parsed['score'], min_score,
             json.dumps(parsed['categories'], ensure_ascii=False),
             json.dumps(parsed['issues'], ensure_ascii=False), error, stamp))
        if (ch['status'] or '').startswith('AI草稿'):
            new_status = chapter_status(status, parsed['score'], parsed['issues'], min_score)
            db.conn.execute('UPDATE chapters SET status=?,updated_at=? WHERE id=?',
                            (new_status, stamp, chapter_id))
        db.conn.commit()
    except Exception:
        db.conn.rollback()
        raise
    return cur.lastrowid


def review_saved_chapter(db, chapter_id, cfg, model_call=None):
    ch = db.get_chapter(chapter_id)
    if ch is None or not (ch['content'] or '').strip():
        raise ValueError('章节没有可审的正文')
    call = model_call or (lambda system, user: aiclient.simple_chat(
        cfg, system, user, temperature=0.2, max_tokens=3000,
        timeout=300, thinking='disabled', **aiclient.structured_output_options(cfg)))
    body_hash = story_memory.body_hash(ch['content'])
    raw = call(SYSTEM, review_prompt(db, ch))
    if story_memory.body_hash(db.get_chapter(chapter_id)['content']) != body_hash:
        raise RuntimeError('审稿过程中正文已修改，本次结果不入库')
    return create_report(db, chapter_id, raw, expected_hash=body_hash)


def get_report(db, report_id):
    return db.conn.execute('SELECT * FROM review_reports WHERE id=?', (report_id,)).fetchone()


def latest_report(db, chapter_id):
    return db.conn.execute('SELECT * FROM review_reports WHERE chapter_id=? ORDER BY id DESC LIMIT 1',
                           (chapter_id,)).fetchone()


def report_current(db, report):
    ch = db.get_chapter(report['chapter_id']) if report else None
    return bool(ch and ch['project_id'] == report['project_id']
                and story_memory.body_hash(ch['content']) == report['body_hash'])


def set_issue_state(db, report_id, index, state):
    if state not in ('ignored', 'not_issue', 'proposed'):
        raise ValueError('问题处理状态无效')
    if db.conn.in_transaction:
        raise ValueError('数据库有未完成的写入，请稍后处理')
    db.conn.execute('BEGIN IMMEDIATE')
    try:
        report = get_report(db, report_id)
        if report is None or not report_current(db, report):
            raise ValueError('审稿报告已过期')
        latest = latest_report(db, report['chapter_id'])
        if latest is None or latest['id'] != report_id:
            raise ValueError('只能处理当前最新的审稿报告')
        issues = json.loads(report['issues_json'])
        if not isinstance(index, int) or not 0 <= index < len(issues):
            raise ValueError('问题序号无效')
        issues[index]['state'] = state
        chapter = db.get_chapter(report['chapter_id'])
        db.conn.execute('UPDATE review_reports SET issues_json=? WHERE id=?',
                        (json.dumps(issues, ensure_ascii=False), report_id))
        if (chapter['status'] or '').startswith('AI草稿'):
            new_status = chapter_status(
                report['status'], report['score'], issues, report['min_score'])
            db.conn.execute('UPDATE chapters SET status=?,updated_at=? WHERE id=?',
                            (new_status, datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                             report['chapter_id']))
        db.conn.commit()
    except Exception:
        db.conn.rollback()
        raise
