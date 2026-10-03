"""Chapter preparation, generation, review, and manuscript persistence."""
import task_ledger as ledger
from ai import prompts, story_memory, evidence_review, rewrite_candidates as rewrite_store, chapter_review
from ai.context import context_pack_for
from ai.task_errors import Stopped

def execute(self, db, o, no, name, cid=0):
    """单章全链路。返回 '跳过' 或 (正文, 章节id)。
    cid>0 = 重写模式指定的既有章节（旧稿由 save_chapter 自动备份为版本）"""
    if cid:
        ch = db.get_chapter(cid)
        if ch is None:
            raise RuntimeError(f"章节 id={cid} 不存在")
    else:
        cid, ch = self._ensure_chapter(db, o, no, name)
    self._active_chapter = cid
    has_old = bool((ch["content"] or "").strip())
    # 断点续跑：父任务台账里本章已完成 → 幂等跳过（以步骤记录为准，
    # 防止「正文在但状态异常」时重复生成）
    if self.resume_run_id and f"chapter:{cid}" in self._resume_steps:
        self.progress.emit("  ↷ 上次任务已完成本章（断点续跑），跳过")
        return "跳过"
    if not self.force_rewrite and self.skip_existing and has_old:
        self._finish_chapter(db, cid, name)
        return "跳过"
    if (has_old and ch['memory_pending'] and self.resume_run_id
            and ch['memory_run_id'] in ledger.run_ancestry(db, self.resume_run_id)):
        # 同一重写任务已保存正文，只补齐中断的后处理。
        self._finish_chapter(db, cid, name)
        return '跳过'

    # 1) 章节卡：没有就按章纲+前情补一张
    if (self.gen_card and not (ch["chapter_card"] or "").strip()
            and not (self.candidate_mode and self.force_rewrite and has_old)):
        chapters = db.get_chapters(self.pid)
        prev_summary = ""
        if ch in chapters:
            i = chapters.index(ch)
            if i > 0:
                prev_summary = chapters[i - 1]["summary"] or ""
        outlines_text = "\n".join(
            f"- {x['title']}：{(x['content'] or '')[:60]}"
            for x in db.get_outlines(self.pid) if x["level"] == "章纲")
        settings_text = "\n".join(
            f"- {r['term']}：{r['definition']}" for r in db.get_settings(self.pid))
        user = prompts.gen_chapter_card_draft(outlines_text, prev_summary,
                                              settings_text, name)
        card = self._call(prompts.SYSTEM_ASSIST, user, temperature=0.7)
        db.update_chapter_meta(cid, chapter_card=card)
        ch = db.get_chapter(cid)
        self.progress.emit("  ✓ 章节卡已补")

    # 2) 正文：三层记忆上下文包 + 章纲原文；重写模式附加用户要求与旧稿
    pack = context_pack_for(db, ch, style_override=self._style_snapshot)
    user = prompts.gen_chapter_prompt(
        pack, outline=(o["content"] or "") if o is not None else "")
    if self.force_rewrite and has_old and self.rewrite_instruction:
        user += ("\n\n【用户修改要求（本次重写必须落实）】\n"
                 + self.rewrite_instruction
                 + "\n\n【现有正文（在它基础上重写，可大幅改动，"
                   "但人物、主线与前后章要连贯）】\n"
                 + (ch["content"] or "")[:4000])
    elif self.rebuild and self.rewrite_instruction:
        user += "\n\n【全书总要求（务必贯彻到本章）】\n" + self.rewrite_instruction
    best = ""
    unchanged_attempts = 0
    for attempt in range(3):
        if self._stop:
            raise Stopped()
        text = (self._call(prompts.SYSTEM_WRITER, user, temperature=0.8)
                or "").strip()
        if self.force_rewrite and has_old and text == (ch['content'] or '').strip():
            unchanged_attempts += 1
            if unchanged_attempts >= 2:
                break
            self.progress.emit('  ⚠ 模型原样返回旧稿，尚未落实修改要求；反馈后再试一次')
            user += ('\n\n【上一轮验收反馈】上一轮输出与现有正文逐字相同，未完成作者的修改要求。'
                     '请保留人物、事件和锁定内容，实际落实上述修改要求；不要再次原样复制旧稿。')
            continue
        if len(text) > len(best):
            best = text
        if len(best) >= self.min_chars:
            break
        self.progress.emit(
            f"  ⚠ 稿件偏短（{len(best)} 字 < {self.min_chars}），重写…")
    if not best:
        if unchanged_attempts:
            raise RuntimeError('模型重复返回旧稿，未生成可采纳差异；原正文保持不变，请调整修改要求后重试。')
        raise RuntimeError("模型未返回正文")
    if len(best) < self.min_chars:
        self.progress.emit(f"  ⚠ 仍偏短（{len(best)} 字），已保留，请复核")

    best, chapter_status = chapter_review.select_draft(self, db, ch, user, best)

    if has_old and self.force_rewrite and self.candidate_mode and not self.rebuild:
        proposal = rewrite_store.create_candidate(
            db, cid, best, self.rewrite_instruction, mode='full',
            task_id=self.task_run_id, base_content=ch['content'],
            review_status=chapter_status)
        self.progress.emit(f'  📄 重写候选 #{proposal} 已保存，当前正文未改动')
        if self.task_run_id:
            ledger.record_step(db, self.task_run_id, f'chapter:{cid}', 'completed',
                               f'候选 #{proposal}')
        self.chapter_done.emit(cid, name)
        return '候选稿', cid

    if has_old:
        note = "流水线重写" + (f"：{self.rewrite_instruction[:40]}"
                               if self.rewrite_instruction else "")
        db.save_chapter(cid, best, note=note, status=chapter_status, summary='',
                        memory_pending=1, memory_run_id=self.task_run_id)
        self.progress.emit("  💾 旧稿已备份为版本（编辑器「版本历史」可回滚）")
    else:
        db.update_chapter_meta(cid, content=best, status=chapter_status,
                               summary='', memory_pending=1,
                               memory_run_id=self.task_run_id)

    reviewed_raw = self._review_payloads.get(story_memory.body_hash(best))
    if reviewed_raw:
        evidence_review.create_report(db, cid, reviewed_raw,
                                      min_score=self.min_score,
                                      expected_hash=story_memory.body_hash(best))

    self._finish_chapter(db, cid, name)
    return best, cid
