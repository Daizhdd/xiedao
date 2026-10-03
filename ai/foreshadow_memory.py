# -*- coding: utf-8 -*-
"""Time-scoped foreshadow evidence and author-confirmed resolution."""
import re
from ai.story_memory import body_hash, chapter_order


def chapter_number(text):
    match = re.search(r'第\s*(\d+)\s*章', text or '')
    return int(match.group(1)) if match else 0


def plan_window(text):
    nums = [int(x) for x in re.findall(r'\d+', text or '')]
    if not nums:
        return 0, 0
    return nums[0], max(nums[0], nums[1]) if len(nums) > 1 else nums[0]


def source_valid(db, row, prefix):
    cid = row[f'{prefix}_chapter_id']
    ch = db.get_chapter(cid) if cid else None
    if ch is None or ch['project_id'] != row['project_id']:
        return False
    body, quote = ch['content'] or '', row[f'{prefix}_quote'] or ''
    return bool(quote and row[f'{prefix}_hash'] == body_hash(body) and body.count(quote) == 1)


def _source(db, pid, chapter_id, quote):
    ch = db.get_chapter(chapter_id)
    if ch is None or ch['project_id'] != pid or not quote:
        raise ValueError('来源章节或证据无效')
    body = ch['content'] or ''
    if body.count(quote) != 1:
        raise ValueError('证据必须逐字且唯一地出现在当前正文中')
    return ch, body.index(quote)


def record_plant(db, fid, chapter_id, quote, entity_id=0):
    row = db.get_foreshadow(fid)
    if row is None:
        raise ValueError('伏笔不存在')
    ch, position = _source(db, row['project_id'], chapter_id, quote)
    db.update_foreshadow(fid, planted_ch=f"第{ch['chapter_no']}章",
                         planted_chapter_id=chapter_id, planted_hash=body_hash(ch['content']),
                         planted_quote=quote, planted_pos=position,
                         entity_id=int(entity_id or 0))


def propose_resolution(db, fid, chapter_id, quote):
    row = db.get_foreshadow(fid)
    if row is None or row['status'] == '已回收':
        raise ValueError('伏笔不存在或已回收')
    ch, position = _source(db, row['project_id'], chapter_id, quote)
    digest = body_hash(ch['content'])
    if row['rejected_hash'] == digest:
        raise ValueError('作者已驳回同一正文版本的回收判断')
    db.update_foreshadow(fid, status='疑似回收', resolve_chapter_id=chapter_id,
                         resolve_hash=digest, resolve_quote=quote, resolve_pos=position)


def confirm_resolution(db, fid):
    row = db.get_foreshadow(fid)
    if row is None or row['status'] != '疑似回收' or not source_valid(db, row, 'resolve'):
        raise ValueError('回收证据已失效或尚无可确认的回收候选')
    db.update_foreshadow(fid, status='已回收', rejected_hash='')


def reject_resolution(db, fid):
    row = db.get_foreshadow(fid)
    if row is None or row['status'] != '疑似回收':
        raise ValueError('没有可驳回的回收候选')
    db.update_foreshadow(fid, status='待回收', rejected_hash=row['resolve_hash'],
                         resolve_chapter_id=0, resolve_hash='', resolve_quote='',
                         resolve_pos=-1)


def visible_before(db, pid, target_chapter_id):
    order = chapter_order(db, pid)
    if target_chapter_id not in order:
        return []
    rows = []
    for f in db.get_foreshadows(pid):
        planted = f['planted_chapter_id']
        if not planted or planted not in order or order[planted] >= order[target_chapter_id]:
            continue
        if not source_valid(db, f, 'planted'):
            continue
        if (f['status'] == '已回收' and source_valid(db, f, 'resolve')
                and order.get(f['resolve_chapter_id'], 10**9) < order[target_chapter_id]):
            continue
        rows.append(f)
    return rows


def reminders(db, pid, target_chapter_id):
    target = db.get_chapter(target_chapter_id)
    if target is None or target['project_id'] != pid:
        return []
    card = target['chapter_card'] or ''
    order = chapter_order(db, pid)
    results = []
    for f in visible_before(db, pid, target_chapter_id):
        due = f['plan_from'] or chapter_number(f['plan_ch'])
        end = f['plan_to'] or due
        entity = db.conn.execute('SELECT name FROM story_entities WHERE id=? AND project_id=?',
                                 (f['entity_id'], pid)).fetchone() if f['entity_id'] else None
        terms = [entity['name']] if entity else []
        if entity:
            terms += [r['alias'] for r in db.conn.execute(
                'SELECT alias FROM story_aliases WHERE project_id=? AND entity_id=?',
                (pid, f['entity_id']))]
        terms += [word for word in re.findall(r'[\u4e00-\u9fff]{2,}', f['content'])
                  if len(word) >= 3][:2]
        relevant = any(term and term in card for term in terms)
        early = (due and target['chapter_no'] < due
                 and len(f['content']) >= 6 and f['content'] in (target['content'] or ''))
        state = ('疑似提前揭底' if early else
                 '逾期' if end and target['chapter_no'] > end else
                 '到期' if due and target['chapter_no'] >= due else
                 '本章相关' if relevant else '待回收')
        results.append({'foreshadow': f, 'state': state,
                        'reason': f"计划第{due}至{end}章" if due else
                        '人物/关键词命中' if relevant else '已埋设、未到计划回收章'})
    priority = {'疑似提前揭底': 0, '逾期': 1, '到期': 2,
                '本章相关': 3, '待回收': 4}
    return sorted(results, key=lambda r: (priority[r['state']], r['foreshadow']['id']))
