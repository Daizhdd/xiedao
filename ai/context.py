# -*- coding: utf-8 -*-
"""上下文包构建（与 UI 解耦）。
MainWindow._context_pack 与流水线编排器共用同一实现，避免两处逻辑漂移。"""
import hashlib
import json
from ai import prompts
from ai import story_memory
from ai import foreshadow_memory
from ai import style_profile


class MemoryDigest:
    """Incremental encoding identical to the existing cached JSON fingerprint."""
    def __init__(self):
        self._hash = hashlib.sha256(b'[')
        self._count = 0

    def add(self, row):
        if self._count:
            self._hash.update(b', ')
        values = [row[key] for key in ('id', 'volume', 'chapter_no', 'title', 'content', 'summary')]
        self._hash.update(json.dumps(values, ensure_ascii=False).encode('utf-8'))
        self._count += 1

    def hexdigest(self):
        result = self._hash.copy()
        result.update(b']')
        return result.hexdigest()


def memory_fingerprint(rows):
    digest = MemoryDigest()
    for row in rows:
        digest.add(row)
    return digest.hexdigest()


def iter_volume_rows(db, project_id, volume, chapter_no, chapter_id):
    return db.conn.execute(
        'SELECT * FROM chapters WHERE project_id=? AND volume=? AND (chapter_no,id)<=(?,?) '
        'ORDER BY chapter_no,id', (project_id, volume, chapter_no, chapter_id))


def prefix_memory(rows, db=None):
    """只使用已校验的前文缓存；旧库或缓存失效时退回前文章摘要。"""
    if not rows:
        return ''
    last = rows[-1]
    if db is not None and last['prefix_hash'] and last['prefix_summary']:
        source = iter_volume_rows(db, last['project_id'], last['volume'], last['chapter_no'], last['id'])
    else:
        source = rows
    if last['prefix_hash'] and last['prefix_summary'] and last['prefix_hash'] == memory_fingerprint(source):
        return last['prefix_summary']
    return '\n'.join(f"第{r['chapter_no']}章 {r['title']}：{r['summary']}"
                     for r in rows[-16:] if (r['summary'] or '').strip())


def eff_vol(o, vol_outlines):
    """卷纲条目的有效卷号：volume 字段优先，缺省按排序位置"""
    if o["volume"]:
        return o["volume"]
    pos_of = {x["id"]: i + 1 for i, x in enumerate(vol_outlines)}
    return pos_of.get(o["id"], 0)


def sorted_vol_outlines(db, pid):
    return sorted([o for o in db.get_outlines(pid) if o["level"] == "卷纲"],
                  key=lambda x: (x["sort_no"], x["id"]))


def vol_outline_for(db, pid, vol):
    vols = sorted_vol_outlines(db, pid)
    for o in vols:
        if eff_vol(o, vols) == vol:
            return o
    return None


def chapter_outlines_for(db, pid, vol):
    """某卷的全部章纲条目（按 sort_no, id 排序）"""
    vols = sorted_vol_outlines(db, pid)
    rows = [o for o in db.get_outlines(pid)
            if o["level"] == "章纲" and eff_vol(o, vols) == vol]
    rows.sort(key=lambda x: (x["sort_no"], x["id"]))
    return rows


def fh_matches(f, card):
    """Only explicit multi-character evidence can establish topical relevance."""
    if f["plan_ch"] and (f["plan_ch"] in (card or "")):
        return True
    import re
    return any(len(word) >= 3 and word in (card or '')
               for word in re.findall(r'[\u4e00-\u9fff]{2,}', f['content']))


def foreshadow_hint(db, pid, card, chapter=None):
    if chapter is None:
        return ""
    items = foreshadow_memory.reminders(db, pid, chapter['id'])
    return '\n'.join(
        f"- [{entry['state']}] {entry['foreshadow']['content'][:70]}"
        f"（{entry['reason']}；埋设于{entry['foreshadow']['planted_ch']}）"
        for entry in items[:12])


def context_pack_for(db, ch, pid=None, style_override=None):
    """三层记忆上下文包：实时层＝上一章结尾原文；近程层＝最近 5 章摘要；
    远程层＝卷级滚动摘要 + 全书主线。设定按章节卡/近两章正文关键词召回。
    ch: chapters 行（None=无章节视角，仅全书级内容）；pid 缺省取 ch 的所属书。"""
    if ch is not None:
        pid = ch["project_id"]
    if not pid:
        return ""
    project = db.get_project(pid)
    if project is None:
        return ""
    chapters = db.get_chapter_headers(pid)
    if ch is not None:
        # 调用者持有的 Row 可能已过期，以稳定 id 定位，绝不退回整书末尾。
        pos = next((i for i, row in enumerate(chapters) if row['id'] == ch['id']), 0)
    else:
        pos = len(chapters)
    prevs = chapters[:pos]

    tails = {row['id']: db.conn.execute('SELECT substr(content,-2000) FROM chapters WHERE id=?',
                                      (row['id'],)).fetchone()[0] or '' for row in prevs[-2:]}
    prev_tail = tails[prevs[-1]['id']][-800:] if prevs else ''
    recent = [(c["chapter_no"], c["title"], (c["summary"] or "").strip())
              for c in prevs[-5:]]
    vol = ch["volume"] if ch is not None else 1
    vol_sum = (prefix_memory([c for c in prevs if c['volume'] == vol], db)
               if ch is not None else db.get_volume_summary(pid, vol))
    card = (ch["chapter_card"] if ch is not None else "") or ""

    # 设定召回：以章节卡 + 近两章结尾为探测文本
    probe = [card] + list(tails.values())
    recalled = prompts.recall_settings(db.get_settings(pid), probe)

    outlines = db.get_outlines(pid)
    volume_outline = ""
    vo = vol_outline_for(db, pid, vol)
    if vo:
        volume_outline = vo["content"]
    chapter_outline = ""
    if ch is not None and ch["outline_id"]:
        bo = next((o for o in outlines if o["id"] == ch["outline_id"]), None)
        if bo:
            chapter_outline = bo["content"]

    return prompts.build_context_pack(project, recalled, card,
                                      prev_tail=prev_tail, recent_summaries=recent,
                                      volume_summary=vol_sum,
                                      foreshadow_hint=foreshadow_hint(db, pid, card, ch),
                                      volume_outline=volume_outline,
                                      chapter_outline=chapter_outline,
                                      style_override=(style_profile.effective_style(db, pid)
                                                      if style_override is None else style_override),
                                      story_memory=(story_memory.memory_for_chapter(db, ch['id'])['text']
                                                    if ch is not None else ''))
