# -*- coding: utf-8 -*-
"""流水线编排器（垂直智能体第一阶段）：一键写完一卷。

链路：章纲校验（可自动补）→ 逐章［建章/补章节卡 → 三层记忆上下文包 → 正文
→ 章摘要 → 卷滚动摘要］。

人机边界：产物直接入库但状态标记为「AI草稿」，人在编辑器/树里复核；
已有人工内容的章节默认跳过（断点续跑），不会覆盖。
"""
import re
import hashlib
import threading
import time
import traceback

from PySide6.QtCore import QThread, Signal

from db import DB, now
import task_ledger as ledger
import task_execution as execution
from book_backup import (backup_project, clear_pending_rebuild,
                         read_backup, restore_project,
                         write_pending_rebuild)
from ai import client as aiclient
from ai import prompts
from ai import parse as aparse
from ai import tools as atools
from ai import review as areview
from ai import fallback as fb
from ai import prefs as aprefs
from ai import story_memory
from ai import rewrite_candidates as rewrite_store
from ai import evidence_review
from ai import style_profile
from ai import model_calls, chapter_execution, chapter_review, chapter_memory
from ai.task_errors import Stopped
from ai.usage_budget import UsageBudget, BudgetPaused
from ai.context import (context_pack_for, vol_outline_for, chapter_outlines_for,
                        eff_vol, sorted_vol_outlines, memory_fingerprint)

DEFAULT_MIN_CHARS = 800
DEFAULT_MIN_SCORE = 75
MAX_TOKENS = 8192
CALL_TIMEOUT = 180


def _thinking_role(system):
    """system 角色在写作偏好里的归类（thinking_for 按它决定思考开关）"""
    return {prompts.SYSTEM_WRITER: "writer",
            prompts.SYSTEM_ASSIST: "planner",
            prompts.SYSTEM_SUMMARY: "summary",
            prompts.SYSTEM_ROLLUP: "rollup"}.get(system, "other")


def _kind_of(system):
    """按 system 角色给调用分类（降级链按类统计连败）"""
    return {prompts.SYSTEM_WRITER: "写作正文",
            prompts.SYSTEM_ASSIST: "草案生成",
            prompts.SYSTEM_SUMMARY: "章节摘要",
            prompts.SYSTEM_ROLLUP: "卷滚动摘要"}.get(system, "生成")




class PipelineWorker(QThread):
    progress = Signal(str)           # 日志行
    tick = Signal(int, int)          # (已完成, 总章数)
    chapter_done = Signal(int, str)  # (chapter_id, 章节名)
    checkpoint_req = Signal(str, str)  # 全书模式检查点 (阶段标题, 草案正文)
    finished_ok = Signal(str)        # 结束摘要
    failed = Signal(str)

    def __init__(self, cfg, db_path, pid, vol, auto_outline=True,
                 skip_existing=True, gen_card=True,
                 min_chars=DEFAULT_MIN_CHARS, use_tools=True,
                 review=True, min_score=DEFAULT_MIN_SCORE,
                 distill_style=True, whole_book=False,
                 force_rewrite=False, rewrite_instruction="",
                 only_chapter_no=0, rebuild=False, rebuild_volumes=0,
                 clear_foreshadows=False, until_no=0, resume_run_id=0,
                 candidate_mode=False, parent=None):
        super().__init__(parent)
        self.cfg = dict(cfg)
        self.db_path = db_path
        self.pid = pid
        self.vol = int(vol)
        self.auto_outline = auto_outline
        self.skip_existing = skip_existing
        self.gen_card = gen_card
        self.min_chars = min_chars
        self.use_tools = use_tools
        self.review = review
        self.min_score = min_score
        self.distill_style = distill_style
        self.whole_book = whole_book
        self.force_rewrite = force_rewrite        # 重写模式：已有正文也重新生成
        self.candidate_mode = bool(candidate_mode) # 对话重写默认先供作者审阅
        self.rewrite_instruction = (rewrite_instruction or "").strip()
        self.only_chapter_no = int(only_chapter_no or 0)   # 只重写这一章（0=不限）
        self.rebuild = rebuild                    # 全书重构：删旧卷纲/章纲/正文后全部重来
        self.rebuild_volumes = int(rebuild_volumes or 0)   # 重构时规划几卷（0=按项目规划）
        self.clear_foreshadows = bool(clear_foreshadows)   # 重构时是否连伏笔台账一起清空
        self.until_no = int(until_no or 0)        # 『写到第N章』：补章纲补正文直到该章
        self.resume_run_id = int(resume_run_id or 0)   # 断点续跑：父任务 run id
        self.task_run_id = 0                     # UI 创建任务台账后赋值
        self._resume_steps = set()                # run() 时从台账加载
        self._tools_ok = use_tools   # 模型不支持工具时自动降级为 False
        self._prefs = dict(aprefs.DEFAULT_PREFS)   # run() 时从 app_settings 加载
        self._style_snapshot = None
        self._budget = None
        self._runtime_snapshot = None
        self._fallback_configs = None
        self._usage_start = {}
        self._auto_extract = None
        self._active_db = None
        self._active_chapter = 0
        self._review_stats = {"passed": 0, "blocked": 0, "low": 0,
                              "unreviewed": 0}
        self._review_payloads = {}
        self._review_blocks = {}
        self.n_rewrites = 0          # 审稿触发的重写次数（本次 run 内累计）
        self._issue_pool = []        # 本 run 收集的审稿意见（沉淀风格用）
        self._stop = False
        self._cancel_event = threading.Event()
        self._gate = threading.Event()      # 全书模式检查点门控
        self._skip_stage = False

    def stop(self):
        self._stop = True
        self._cancel_event.set()
        self._gate.set()   # 若正阻塞在检查点，放行以便感知停止

    # ---------- AI 调用 ----------
    def _safe_review(self, user, text, db=None, chapter=None):
        return chapter_review.safe_review(self, user, text, db, chapter)

    def _configure_run(self, db):
        self._active_db = db
        execution.prepare_worker(db, self)
        snapshot = self._runtime_snapshot
        self._prefs = dict(snapshot['prefs'])
        self._usage_start = ledger.cumulative_usage(db, self.resume_run_id)
        self._budget = UsageBudget(**snapshot['budget'], initial_usage=self._usage_start)
        self._style_snapshot = snapshot['style']
        self._auto_extract = snapshot['auto_extract']
        if self.task_run_id:
            mode, args = execution.worker_spec(self)
            ledger.save_spec(db, self.task_run_id, mode, args)
        revision = snapshot['style_revision'] or '旧版默认'
        self.progress.emit(f'作品文风快照：版本 {revision}')
        if self.resume_run_id:
            self.progress.emit(f"累计用量：已调用 {self._usage_start['calls']} 次，"
                               f"已计输出 {self._usage_start['reserved_completion']} token")

    def _save_usage(self, db):
        if self.task_run_id and self._budget is not None:
            ledger.save_usage(db, self.task_run_id, self.pid,
                              self._budget.snapshot_since(self._usage_start))

    def _finalize_run(self):
        if not self.task_run_id:
            return
        db = DB(self.db_path)
        try:
            self._save_usage(db)
            ledger.checkpoint_run(db, self.task_run_id, self.pid)
        finally:
            db.close()

    def _distill_style(self):
        """卷末自学习：把高频审稿意见提炼成风格规范追加进 style_sheet，
        下一卷生成上下文包时自动带上（build_context_pack 的风格段）"""
        seen, uniq = set(), []
        for x in self._issue_pool:
            key = x[:20]
            if key not in seen:
                seen.add(key)
                uniq.append(x)
        if len(uniq) < 3:
            return
        db = DB(self.db_path)
        prior_db, prior_chapter = self._active_db, self._active_chapter
        self._active_db, self._active_chapter = db, 0
        try:
            project = db.get_project(self.pid)
            user = prompts.style_distill_prompt(
                style_profile.effective_style(db, self.pid), uniq[:24])
            try:
                text = self._call(prompts.SYSTEM_ASSIST, user, temperature=0.4)
            except Exception as e:  # noqa
                self.progress.emit(f"  ⚠ 风格沉淀失败（{e}），不影响其他结果")
                return
            lines = [l.strip().lstrip("-•*· ").strip()
                     for l in (text or "").splitlines()]
            add = [l for l in lines if l][:6]
            if not add:
                return
            for item in add:
                rule, marker, example = item.partition('｜')
                style_profile.add_suggestion(
                    db, self.pid, rule.strip(), '；'.join(uniq[:3])[:500],
                    example.strip() if marker and example.strip()
                    else '（模型未给出示例，请作者复核）', scope='下一卷')
            self.progress.emit(
                f"  🧠 已提出 {len(add)} 条风格建议，请在作品文风中审核")
        finally:
            self._active_db, self._active_chapter = prior_db, prior_chapter
            db.close()

    def _call(self, system, user, temperature=0.8, retries=2):
        return model_calls.call(self, system, user, temperature, retries)

    def _call_chain(self, cfg, system, user, temperature, retries):
        return model_calls.call_chain(self, cfg, system, user, temperature, retries,
                                      call_timeout=CALL_TIMEOUT, max_tokens=MAX_TOKENS)

    # ---------- 计划 ----------
    def _plan(self, db):
        """返回 (任务列表, 提示列表)。任务 = (章纲行|None, 章节号, 章名, 章节id|0)"""
        pid, vol = self.pid, self.vol
        project = db.get_project(pid)
        if project is None:
            raise RuntimeError("项目不存在")
        hints = []
        vo = vol_outline_for(db, pid, vol)
        if vo is None:
            hints.append(f"第{vol}卷没有卷纲，正文只按章纲与三层记忆推进")

        if self.force_rewrite:
            # 重写模式：以现有章节为准（含未绑章纲的），旧稿由 save_chapter 自动备份
            chs = [c for c in db.get_chapters(pid) if c["volume"] == vol]
            if self.only_chapter_no:
                chs = [c for c in chs if c["chapter_no"] == self.only_chapter_no]
            elif self.candidate_mode:
                # 批量候选只针对现有正文；单章明确点名空章时仍可直接补写。
                chs = [c for c in chs if (c['content'] or '').strip()]
            chs.sort(key=lambda c: c["chapter_no"])
            if not chs:
                hints.append(f"第{vol}卷没有可重写的章节")
                return [], hints
            self.progress.emit(
                f"== 重写模式：第{vol}卷共 {len(chs)} 章，"
                + (f"用户要求：{self.rewrite_instruction}" if self.rewrite_instruction
                   else "整体重新生成") + " ==")
            tasks = [(None, c["chapter_no"], c["title"], c["id"]) for c in chs]
            return tasks, hints

        zg = chapter_outlines_for(db, pid, vol)
        if not zg and self.auto_outline:
            self.progress.emit(f"第{vol}卷没有章纲，自动生成中…")
            zg = self._auto_outline(db, project)
        if not zg:
            raise RuntimeError(
                f"第{vol}卷没有章纲。请先在树中右键「AI 生成本卷章纲草案」入库，"
                "或在流水线里勾选「自动生成本卷章纲」")

        tasks = []
        for o in zg:
            m = re.match(r"^第(\d+)章\s*(.*)$", o["title"].strip())
            if m:
                no, name = int(m.group(1)), m.group(2).strip() or o["title"]
            else:
                no, name = 0, o["title"]
            tasks.append((o, no, name, 0))
        return tasks, hints

    def _auto_outline(self, db, project):
        """缺章纲时按卷区间自动生成并直接入库，返回入库后的章纲行"""
        pid, vol = self.pid, self.vol
        settings_text = "\n".join(
            f"- {r['term']}：{r['definition']}" for r in db.get_settings(pid))
        M = max(int(project["plan_chapters"] or 10), 1)
        s0, s1 = (vol - 1) * M + 1, vol * M
        vo = vol_outline_for(db, pid, vol)
        user = prompts.gen_volume_chapters(
            dict(project), settings_text, vol,
            vo["title"] if vo else f"第{vol}卷",
            vo["content"] if vo else "", s0, s1)
        text = self._call(prompts.SYSTEM_ASSIST, user, temperature=0.8)
        items = self._parse_with_repair(text, "章纲", start_num=s0,
                                        what="章纲自动生成输出", expected_range=(s0, s1))
        for _line, data in items:
            db.add_outline(pid, "章纲", data["title"], data.get("content", ""),
                           volume=vol)
        self.progress.emit(f"  ✓ 自动生成 {len(items)} 条章纲（第{s0}-{s1}章区间）")
        return chapter_outlines_for(db, pid, vol)

    # ---------- 『写到第N章』补写模式 ----------
    def _ensure_outlines_range(self, db, project, vol, lo, hi):
        """补齐第 lo-hi 章缺失的章纲；模型必须完整覆盖每个缺号。"""
        pid = self.pid
        M = max(int(project["plan_chapters"] or 10), 1)
        have = set()
        for o in db.get_outlines(pid):
            if o["level"] != "章纲":
                continue
            m = re.match(r"^第(\d+)章", (o["title"] or "").strip())
            if not m:
                continue
            no = int(m.group(1))
            if not lo <= no <= hi:
                continue
            outline_vol = o["volume"] or ((no - 1) // M + 1)
            if outline_vol != vol:
                continue
            have.add(no)
            if not o["volume"]:
                # Older chapter outlines may predate their volume field.
                db.update_outline(o["id"], volume=vol)
        missing = [n for n in range(lo, hi + 1) if n not in have]
        if not missing:
            return
        runs, start, prev = [], missing[0], missing[0]
        for n in missing[1:]:
            if n == prev + 1:
                prev = n
            else:
                runs.append((start, prev))
                start = prev = n
        runs.append((start, prev))
        settings_text = "\n".join(
            f"- {r['term']}：{r['definition']}" for r in db.get_settings(pid))
        vo = vol_outline_for(db, pid, vol)
        for a, b in runs:
            if self._stop:
                raise Stopped()
            user = prompts.gen_volume_chapters(
                dict(project), settings_text, vol,
                vo["title"] if vo else f"第{vol}卷",
                vo["content"] if vo else "", a, b)
            text = self._call(prompts.SYSTEM_ASSIST, user, temperature=0.8)
            items = self._parse_with_repair(text, "章纲", start_num=a,
                                            what=f"第{a}-{b}章章纲生成输出", expected_range=(a, b))
            planned = {}
            for _line, data in items:
                m = re.match(r"^第(\d+)章", (data["title"] or "").strip())
                no = int(m.group(1)) if m else 0
                if not a <= no <= b or no in planned:
                    continue
                planned[no] = data
            expected = set(range(a, b + 1))
            if set(planned) != expected:
                missing_no = sorted(expected - set(planned))
                raise RuntimeError(
                    f"第{a}-{b}章章纲未完整覆盖目标范围；缺少："
                    f"{', '.join(map(str, missing_no)) or '无'}"
                    "（原文见活动卡与 api_debug.log），可重试或换模型")
            for no in sorted(planned):
                data = planned[no]
                db.add_outline(pid, "章纲", data["title"],
                               data.get("content", ""), volume=vol)
                have.add(no)
            inserted = len(planned)
            if inserted != b - a + 1:
                raise RuntimeError(f"第{a}-{b}章章纲只补齐 {inserted}/{b-a+1} 条")
            self.progress.emit(f"  ✓ 补齐 {inserted} 条章纲（第{a}-{b}章）")

    def _run_until(self, db):
        """Write every chapter 1..N with non-empty content, filling holes/shells."""
        N = int(self.until_no)
        if N < 1:
            raise RuntimeError("目标章号必须大于 0")
        chapters = db.get_chapters(self.pid)
        complete = {c["chapter_no"] for c in chapters
                    if 1 <= c["chapter_no"] <= N
                    and (c["content"] or "").strip()}
        missing_all = [n for n in range(1, N + 1) if n not in complete]
        # 即使目标范围正文齐全，也要恢复已落盘章节的摘要阶段。
        for ch in chapters:
            if 1 <= ch['chapter_no'] <= N and (ch['content'] or '').strip():
                self._finish_chapter(db, ch['id'], ch['title'])
        if not missing_all:
            self.progress.emit(f"ℹ 第1-{N}章均已有正文，摘要已检查补齐，无需补写")
            return
        project = db.get_project(self.pid)
        if project is None:
            raise RuntimeError("项目不存在")
        M = max(int(project["plan_chapters"] or 10), 1)
        self.progress.emit(
            f"════ 补写任务：检查第1-{N}章，缺正文 {len(missing_all)} 章 ════")
        v_end = (N - 1) // M + 1
        for v in range(1, v_end + 1):
            if self._stop:
                raise Stopped()
            chapter_lo = (v - 1) * M + 1
            chapter_hi = min(N, v * M)
            needed = [n for n in missing_all if chapter_lo <= n <= chapter_hi]
            if not needed:
                continue
            start = previous = needed[0]
            for number in needed[1:]:
                if number == previous + 1:
                    previous = number
                    continue
                self._ensure_outlines_range(db, project, v, start, previous)
                start = previous = number
            self._ensure_outlines_range(db, project, v, start, previous)
            failed_before = self._stats["failed"]
            self._write_volume(db, v, up_to_no=N)
            if self._stats["failed"] != failed_before:
                raise RuntimeError(f"第{v}卷有章节失败，补写任务未完成")

        chapters = db.get_chapters(self.pid)
        complete = {c["chapter_no"] for c in chapters
                    if 1 <= c["chapter_no"] <= N
                    and (c["content"] or "").strip()}
        remaining = [n for n in range(1, N + 1) if n not in complete]
        if remaining:
            raise RuntimeError(
                "补写后仍缺少正文的章节："
                + ", ".join(f"第{n}章" for n in remaining))

    # ---------- 主循环 ----------
    def _reset_stats(self):
        self._stats = {"written": 0, "candidates": 0, "skipped": 0, "cards": 0, "failed": 0,
                       "total": 0}
        self._review_stats = {"passed": 0, "blocked": 0, "low": 0,
                              "unreviewed": 0}

    def _summary(self, prefix="完成"):
        s = self._stats
        r = self._review_stats
        extra = ""
        if r["blocked"] or r["low"] or r["unreviewed"]:
            extra = (f"（审稿通过 {r['passed']}，严重问题待核 {r['blocked']}，低分 {r['low']}，"
                     f"未完成审稿 {r['unreviewed']}）")
        return (f"{prefix}：新写 {s['written']} 章，待采纳候选 {s['candidates']} 章"
                f"（审稿重写 {self.n_rewrites} 章）"
                f"{extra}，跳过 {s['skipped']} 章，失败 {s['failed']} 章，"
                f"补章节卡 {s['cards']} 次。"
                "「AI草稿·未审/待核/低分」的章节请优先在编辑器复核。")

    def run(self):
        if self.whole_book:
            self._run_book()
            return
        self._reset_stats()
        db = None
        try:
            db = DB(self.db_path)
            self._configure_run(db)
            if self.resume_run_id:
                self._resume_steps = ledger.completed_chapter_keys(
                    db, self.resume_run_id)
                if self._resume_steps:
                    self.progress.emit(
                        f"↶ 断点续跑：台账显示上次已完成 {len(self._resume_steps)} 章，"
                        "这些章节将自动跳过")
            if self.until_no:
                self._run_until(db)
            else:
                self._write_volume(db, self.vol)
            db.close()
            db = None
            self._active_db = None
            if self.review and self.distill_style and not self.candidate_mode:
                self._distill_style()
            self.finished_ok.emit(self._summary())
        except Stopped:
            self.progress.emit("⏹ 已停止（已完成章节保留，可再次运行断点续跑）")
            self.finished_ok.emit(
                f"已停止：新写 {self._stats['written']} 章，"
                f"跳过 {self._stats['skipped']} 章。")
        except BudgetPaused as exc:
            self.progress.emit(f'⏸ {exc}')
            self.finished_ok.emit(f'预算已用尽：{exc}。已完成内容保留，可提高任务预算后续跑。')
        except Exception as e:  # noqa
            traceback.print_exc()
            self.failed.emit(str(e))
        finally:
            self._active_db = None
            if db is not None:
                db.close()
            self._finalize_run()

    def _write_volume(self, db, vol, up_to_no=0):
        """单卷章节循环（单卷模式与全书模式共用）。卷号写入 self.vol。
        up_to_no>0 时只写章号不超过它的任务（『写到第N章』的口径）。"""
        self.vol = int(vol)
        vol = self.vol
        tasks, hints = self._plan(db)
        if up_to_no:
            tasks = [t for t in tasks if t[1] and t[1] <= up_to_no]
        for h in hints:
            self.progress.emit(f"ℹ {h}")
        self._stats["total"] += len(tasks)
        self.progress.emit(f"== 第{vol}卷流水线启动：共 {len(tasks)} 章任务 ==")

        for i, (o, no, name, cid) in enumerate(tasks, 1):
            if self._stop:
                raise Stopped()
            title = f"第{no}章 {name}" if no else name
            self.progress.emit(f"—— [{i}/{len(tasks)}] {title}")
            try:
                outcome = self._do_chapter(db, o, no, name, cid)
            except Stopped:
                raise
            except BudgetPaused:
                raise
            except Exception as e:  # noqa
                traceback.print_exc()
                self._stats["failed"] += 1
                self.progress.emit(f"  ❌ 本章失败：{e}")
                self.tick.emit(i, len(tasks))
                raise RuntimeError(
                    f"{title}失败，已暂停后续章节；修复后可从本章续跑：{e}") from e
            if outcome == "跳过":
                self._stats["skipped"] += 1
                self.progress.emit("  ↷ 已有正文，跳过（断点续跑）")
            elif self.force_rewrite and self.candidate_mode and outcome[0] == '候选稿':
                self._stats['candidates'] += 1
                self.progress.emit('  ✓ 候选稿已保存；在章节编辑器比较并采纳')
            else:
                self._stats["written"] += 1
                self.progress.emit(f"  ✓ 正文 {len(outcome[0])} 字｜摘要已更新")
            self.tick.emit(i, len(tasks))

    # ---------- 全书模式 ----------
    def _checkpoint(self, title, body):
        """向对话框提交检查点并阻塞等待人工放行。
        返回 True=采纳入库；False=跳过本阶段。停止请求以 Stopped 异常抛出。"""
        if self._stop:
            raise Stopped()
        self.checkpoint_req.emit(title, body)
        self._gate.wait()
        if self._stop:
            raise Stopped()
        if self._skip_stage:
            self._skip_stage = False
            return False
        return True

    def gate_continue(self):
        self._gate.set()

    def gate_skip(self):
        self._skip_stage = True
        self._gate.set()

    def _book_volumes(self, db, project):
        """全书卷号列表：plan_volumes 优先，缺省从卷纲推断，再缺省 1 卷"""
        V = int(project["plan_volumes"] or 0)
        if not V:
            vols = sorted_vol_outlines(db, self.pid)
            nums = [eff_vol(o, vols) for o in vols]
            V = max(nums) if nums else 1
        return list(range(1, V + 1))

    def _gen_outline_draft_items(self, db, project, vol,
                                 vol_title=None, vol_content=None):
        """生成某卷章纲草案文本与解析条目（不入库）。
        vol_title/vol_content：重构时传新卷纲的内容，覆盖库里旧卷纲。"""
        settings_text = "\n".join(
            f"- {r['term']}：{r['definition']}" for r in db.get_settings(self.pid))
        M = max(int(project["plan_chapters"] or 10), 1)
        s0, s1 = (vol - 1) * M + 1, vol * M
        vo = vol_title is None and vol_outline_for(db, self.pid, vol) or None
        user = prompts.gen_volume_chapters(
            dict(project), settings_text, vol,
            vol_title or (vo["title"] if vo else f"第{vol}卷"),
            vol_content or (vo["content"] if vo else ""), s0, s1)
        if self.rebuild and self.rewrite_instruction:
            user += "\n\n【全书总要求（章纲必须贯彻）】\n" + self.rewrite_instruction
        text = self._call(prompts.SYSTEM_ASSIST, user, temperature=0.8)
        items = self._parse_with_repair(text, "章纲", start_num=s0,
                                        what="章纲生成输出", expected_range=(s0, s1))
        return items, s0, s1

    def _stage_settings(self, db, project):
        existing = db.get_settings(self.pid)
        if len(existing) >= 3:
            self.progress.emit(f"ℹ 已有 {len(existing)} 条设定，跳过设定阶段")
            return
        self.progress.emit("== 全书模式 · 阶段 1/4：设定草案 ==")
        settings_text = "\n".join(f"- {r['term']}：{r['definition']}"
                                  for r in existing)
        text = self._call(prompts.SYSTEM_ASSIST,
                          prompts.gen_settings_batch(dict(project), settings_text),
                          temperature=0.8)
        items = aparse.parse_batch(text, "setting")
        if not items:
            self.progress.emit("  ⚠ 设定草案解析失败，跳过该阶段")
            return
        body = "\n".join(line for line, _ in items)
        if not self._checkpoint(
                f"阶段 1/4 设定草案（{len(items)} 条，将归入「术语」分类，可后续改）",
                body):
            self.progress.emit("↷ 已跳过设定阶段")
            return
        for _line, data in items:
            db.add_setting(self.pid, "术语", data["term"],
                           data.get("definition", ""))
        self.progress.emit(f"  ✓ 已入库 {len(items)} 条设定")

    def _stage_volumes(self, db, project):
        if sorted_vol_outlines(db, self.pid):
            self.progress.emit("ℹ 已有卷纲，跳过卷纲阶段")
            return
        self.progress.emit("== 全书模式 · 阶段 2/4：全书分卷大纲 ==")
        settings_text = "\n".join(f"- {r['term']}：{r['definition']}"
                                  for r in db.get_settings(self.pid))
        V = int(project["plan_volumes"] or 0)
        text = self._call(prompts.SYSTEM_ASSIST,
                          prompts.gen_volumes_plan(dict(project), settings_text,
                                                   total=V or None),
                          temperature=0.8)
        items = aparse.parse_batch(text, "卷纲")
        if not items:
            self.progress.emit("  ⚠ 卷纲草案解析失败，跳过该阶段")
            return
        body = "\n".join(line for line, _ in items)
        if not self._checkpoint(f"阶段 2/4 全书分卷大纲（{len(items)} 卷）", body):
            self.progress.emit("↷ 已跳过卷纲阶段")
            return
        for seq, (_line, data) in enumerate(items, 1):
            m = re.match(r"^第(\d+)卷", data["title"])
            vnum = int(m.group(1)) if m else seq
            name = aparse.strip_num(data["title"], "卷")
            title_out = f"第{vnum}卷 {name}" if name else data["title"]
            db.add_outline(self.pid, "卷纲", title_out,
                           data.get("content", ""), volume=vnum)
        self.progress.emit(f"  ✓ 已入库 {len(items)} 卷卷纲")

    def _stage_chapter_outlines(self, db, project, vol):
        """某卷缺章纲时生成并经检查点入库。返回 True=该卷可写"""
        if chapter_outlines_for(db, self.pid, vol):
            return True
        self.progress.emit(f"== 全书模式 · 第{vol}卷章纲 ==")
        items, s0, s1 = self._gen_outline_draft_items(db, project, vol)
        body = "\n".join(f"{data['title']}：{data.get('content', '')}"
                         for _line, data in items)
        if not self._checkpoint(
                f"第{vol}卷章纲草案（{len(items)} 条，第{s0}-{s1}章区间）", body):
            self.progress.emit(f"↷ 已跳过第{vol}卷（无章纲不写作）")
            return False
        for _line, data in items:
            db.add_outline(self.pid, "章纲", data["title"],
                           data.get("content", ""), volume=vol)
        self.progress.emit(f"  ✓ 已入库 {len(items)} 条章纲")
        return True

    def _stage_intro(self, db, project):
        if (project["intro"] or "").strip():
            return
        self.progress.emit("== 全书模式 · 阶段 4/4：作品简介 ==")
        text = self._call(
            prompts.SYSTEM_ASSIST,
            prompts.gen_intro_draft(project["title"], project["logline"],
                                    project["genre"], project["selling_point"]),
            temperature=0.8)
        if not (text or "").strip():
            return
        if not self._checkpoint("阶段 4/4 平台上传用简介", text.strip()):
            self.progress.emit("↷ 已跳过简介阶段")
            return
        db.update_project(self.pid, intro=text.strip())
        self.progress.emit("  ✓ 简介已入库")

    def _run_book(self):
        self._reset_stats()
        db = None
        try:
            db = DB(self.db_path)
            self._configure_run(db)
            if self.resume_run_id:
                self._resume_steps = ledger.completed_chapter_keys(
                    db, self.resume_run_id)
                if self._resume_steps:
                    self.progress.emit(
                        f"↶ 断点续跑：台账显示上次已完成 {len(self._resume_steps)} 章，"
                        "这些章节将自动跳过")
            if self.rebuild:
                self._run_rebuild(db)
                db.close()
                db = None
                self._active_db = None
                if self.review and self.distill_style and not self.candidate_mode:
                    self._distill_style()
                self.finished_ok.emit(self._summary("全书重构完成"))
                return
            project = db.get_project(self.pid)
            if project is None:
                raise RuntimeError("项目不存在")
            if self.force_rewrite and self.candidate_mode:
                # 候选全书重写只阅读现有书稿。规划/设定/简介属于当前正式
                # 作品，不能复用“从零写书”的入库阶段来改动它们。
                volumes = sorted({c['volume'] for c in db.get_chapters(self.pid)
                                  if (c['content'] or '').strip()})
                for volume in volumes:
                    if self._stop:
                        raise Stopped()
                    self._write_volume(db, volume)
                db.close()
                db = None
                self._active_db = None
                self.finished_ok.emit(self._summary('全书候选生成完成'))
                return
            self.progress.emit("════ 全书模式启动 ════")
            self._stage_settings(db, project)
            self._stage_volumes(db, project)
            project = db.get_project(self.pid)   # 卷纲入库后重新读规划
            vols = self._book_volumes(db, project)
            for vol in vols:
                if self._stop:
                    raise Stopped()
                if not self._stage_chapter_outlines(db, project, vol):
                    continue
                self._write_volume(db, vol)
            if self.review and self.distill_style and not self.candidate_mode:
                self._distill_style()
            self._stage_intro(db, project)
            db.close()
            db = None
            self._active_db = None
            self.finished_ok.emit(self._summary("全书完成"))
        except Stopped:
            if self.rebuild:
                self.progress.emit("⏹ 全书重构已停止，重构前原书已保留或恢复")
                self.finished_ok.emit("全书重构已停止；重构前原书已保留或恢复。")
                return
            self.progress.emit("⏹ 全书模式已停止（已完成部分保留）")
            self.finished_ok.emit(
                f"已停止：新写 {self._stats['written']} 章，"
                f"跳过 {self._stats['skipped']} 章。")
        except BudgetPaused as exc:
            self.progress.emit(f'⏸ {exc}')
            self.finished_ok.emit(f'预算已用尽：{exc}。已完成内容保留，可提高任务预算后续跑。')
        except Exception as e:  # noqa
            traceback.print_exc()
            self.failed.emit(str(e))
        finally:
            self._active_db = None
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass
            self._finalize_run()

    def _run_rebuild(self, db):
        """Back up, prepare the entire new plan, then rebuild as one recoverable job."""
        project = db.get_project(self.pid)
        if project is None:
            raise RuntimeError("项目不存在")
        V = self.rebuild_volumes or int(project["plan_volumes"] or 0) or 2
        if V < 1:
            raise RuntimeError("全书重构至少需要一卷")
        self.progress.emit(
            f"════ 全书重构启动：重新规划 {V} 卷并全部重写 ════")

        # Keep a restorable complete snapshot even if outline generation fails.
        backup_path = backup_project(
            db, self.pid, reason="rebuild", prefix="backup_rebuild")
        self.progress.emit(f"  💾 完整书目数据已原子备份：{backup_path}")
        marker_path = None

        try:
            # Generate and validate the whole plan before changing the database.
            settings_text = "\n".join(
                f"- {r['term']}：{r['definition']}" for r in db.get_settings(self.pid))
            user = prompts.gen_volumes_plan(dict(project), settings_text, total=V)
            if self.rewrite_instruction:
                user += "\n\n【全书总要求（卷纲必须贯彻）】\n" + self.rewrite_instruction
            text = self._call(prompts.SYSTEM_ASSIST, user, temperature=0.8)
            vol_items = self._parse_with_repair(text, "卷纲", what="卷纲生成输出", expected_range=(1, V))
            new_plan = {}
            for seq, (_line, data) in enumerate(vol_items, 1):
                match = re.match(r"^第(\d+)卷", data["title"])
                vnum = int(match.group(1)) if match else seq
                if vnum in new_plan:
                    raise RuntimeError(f"新卷纲包含重复卷号：第{vnum}卷")
                vname = aparse.strip_num(data["title"], "卷")
                new_plan[vnum] = (f"第{vnum}卷 {vname}".strip(),
                                  data.get("content", ""))
            expected_vols = set(range(1, V + 1))
            if set(new_plan) != expected_vols:
                raise RuntimeError(
                    f"新卷纲卷号不完整：需要 1-{V} 卷，实际为 "
                    f"{', '.join(map(str, sorted(new_plan))) or '空'}")
            self.progress.emit(f"  ✓ 新卷纲 {len(new_plan)} 卷（草拟完成，尚未入库）")

            vol_zg = {}
            for vol in range(1, V + 1):
                if self._stop:
                    raise Stopped()
                v_title, v_content = new_plan[vol]
                zg_items, _s0, _s1 = self._gen_outline_draft_items(
                    db, project, vol, vol_title=v_title, vol_content=v_content)
                if not zg_items:
                    raise RuntimeError(f"第{vol}卷没有生成章纲，已取消重构")
                vol_zg[vol] = zg_items
                self.progress.emit(f"  ✓ 第{vol}卷新章纲 {len(zg_items)} 条（草拟完成）")

            if self._stop:
                raise Stopped()
            self.progress.emit("  ✓ 全部大纲生成通过，准备替换旧内容")

            # The marker is durable before the first destructive transaction.
            marker_path = write_pending_rebuild(db, self.pid, backup_path)

            # Cutover the old work and insert the new outlines atomically.
            conn = db.conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                old_chapter_ids = [r[0] for r in conn.execute(
                    "SELECT id FROM chapters WHERE project_id=?", (self.pid,))]
                if old_chapter_ids:
                    marks = ",".join("?" for _ in old_chapter_ids)
                    conn.execute(
                        f"DELETE FROM chapter_versions WHERE chapter_id IN ({marks})",
                        old_chapter_ids)
                conn.execute(
                    "UPDATE setting_candidates SET status='stale',updated_at=? "
                    "WHERE project_id=? AND status='candidate'",
                    (now(), self.pid))
                conn.execute("DELETE FROM chapters WHERE project_id=?", (self.pid,))
                conn.execute("DELETE FROM outlines WHERE project_id=?", (self.pid,))
                old_vols = {r[0] for r in conn.execute(
                    "SELECT volume FROM volume_summary WHERE project_id=?", (self.pid,))}
                old_vols.update(range(1, V + 1))
                for volume in old_vols:
                    conn.execute(
                        "UPDATE volume_summary SET summary='',updated_at=? "
                        "WHERE project_id=? AND volume=?",
                        (now(), self.pid, volume))
                if self.clear_foreshadows:
                    conn.execute("DELETE FROM foreshadows WHERE project_id=?",
                                 (self.pid,))

                inserted = 0
                for vnum in range(1, V + 1):
                    title_out, content = new_plan[vnum]
                    t = now()
                    conn.execute(
                        "INSERT INTO outlines(project_id,level,title,content,chapter_id,"
                        "sort_no,volume,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (self.pid, "卷纲", title_out, content, 0, 0, vnum, t, t))
                    inserted += 1
                for vol in range(1, V + 1):
                    for _line, data in vol_zg[vol]:
                        t = now()
                        conn.execute(
                            "INSERT INTO outlines(project_id,level,title,content,chapter_id,"
                            "sort_no,volume,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                            (self.pid, "章纲", data["title"],
                             data.get("content", ""), 0, 0, vol, t, t))
                conn.execute(
                    "UPDATE projects SET plan_volumes=?,updated_at=? WHERE id=?",
                    (inserted, now(), self.pid))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

            if self.clear_foreshadows:
                self.progress.emit("  🗑 伏笔台账已清空（完整台账在备份里）")
            else:
                self.progress.emit("  ℹ 伏笔台账按计划保留")
            self.progress.emit("  🗑 旧卷纲/章纲/章节已移除（内容在完整备份里）")

            # _write_volume logs and continues after individual chapter errors.
            # A rebuild is successful only if every planned chapter completed.
            for vol in range(1, V + 1):
                if self._stop:
                    raise Stopped()
                failed_before = self._stats["failed"]
                written_before = self._stats["written"]
                skipped_before = self._stats["skipped"]
                total_before = self._stats["total"]
                self._write_volume(db, vol)
                planned = len(vol_zg[vol])
                processed = self._stats["written"] - written_before
                processed += self._stats["skipped"] - skipped_before
                scheduled = self._stats["total"] - total_before
                if self._stats["failed"] != failed_before:
                    raise RuntimeError(
                        f"第{vol}卷有章节写作失败，已恢复重构前的原书")
                if scheduled != planned or processed != planned:
                    raise RuntimeError(
                        f"第{vol}卷只完成 {processed}/{planned} 章，已恢复重构前的原书")

            # Removing the marker is part of success. If it fails, the catch
            # path restores the original snapshot and leaves the marker retryable.
            clear_pending_rebuild(marker_path)
            marker_path = None
        except Exception as failure:
            if marker_path:
                try:
                    restore_project(db, read_backup(backup_path))
                except Exception as recovery_error:
                    raise RuntimeError(
                        f"重构失败：{failure}；自动恢复也失败：{recovery_error}。"
                        f"恢复标记仍保留：{marker_path}；备份：{backup_path}") from recovery_error
                try:
                    clear_pending_rebuild(marker_path)
                    marker_path = None
                except Exception as cleanup_error:
                    raise RuntimeError(
                        f"重构失败：{failure}；原书已完整恢复，但恢复标记无法清理："
                        f"{cleanup_error}。下次启动会再次校验恢复；备份：{backup_path}") from cleanup_error
                self.progress.emit("  ↶ 重构失败或停止，已完整恢复原书")
            raise

    @staticmethod
    def _items_sane(items, kind):
        """卷纲/章纲条目必须有内容或带「第N卷/章」编号——纯标题的垃圾行不算；
        标题去掉编号后必须含汉字（拦「第10章 by ___」这类坏解析进库变章节名）"""
        if kind not in ("卷纲", "章纲"):
            return bool(items)
        seen = set()
        unit = '卷' if kind == '卷纲' else '章'
        for _l, d in items:
            t = d.get("title", "")
            if not ((d.get("content") or "").strip()
                    or re.match(r"^第\d+[章卷]", t)):
                return False
            if not aparse.sane_outline_title(t):
                return False
            if kind == '卷纲' and re.match(r'^第\d+卷\s*阶段目标\s*\d+', t):
                return False
            number = re.match(rf'^第(\d+){unit}', t)
            if number:
                if int(number.group(1)) in seen:
                    return False
                seen.add(int(number.group(1)))
        return True

    def _parse_with_repair(self, text, kind, start_num=None, what="输出", expected_range=None):
        """解析模型批量输出；为空或不合理（如纯标题垃圾行）则把原文退回模型
        按模板重排一次。仍失败则把原始输出打到日志并抛错（绝不静默吞）。"""
        def valid(items):
            if not items or not self._items_sane(items, kind):
                return False
            if expected_range is None:
                return True
            unit = '卷' if kind == '卷纲' else '章'
            numbers = [re.match(rf'^第(\d+){unit}', data.get('title', '')) for _, data in items]
            return (all(numbers) and {int(match.group(1)) for match in numbers}
                    == set(range(expected_range[0], expected_range[1] + 1)))
        items = aparse.parse_batch(text, kind, start_num=start_num)
        if valid(items):
            return items
        self.progress.emit(f"  ⚠ {what}不合格式，退回模型按模板重排…")
        fmt = ("『第N卷 卷名：一句话主线』" if kind == "卷纲"
               else "『第N章 章名：本章要点＋结尾卡点』" if kind == "章纲"
               else "『词条名：定义』")
        constraint = (f'只保留第{expected_range[0]}至第{expected_range[1]}条，编号各出现一次，不增减条目。'
                      if expected_range is not None else '编号不得重复。')
        fix_user = (f"以下是你输出的{what}，程序无法解析。{constraint}请严格按照每条一行、"
                    f"以「- 」开头、格式为{fmt}重新输出全部条目；"
                    "不要标题、序号、加粗、解释或任何其他文字。\n\n"
                    f"原文：\n{(text or '')[:2000]}")
        fixed = self._call(prompts.SYSTEM_ASSIST, fix_user,
                           temperature=0.2, retries=1)
        items = aparse.parse_batch(fixed, kind, start_num=start_num)
        if valid(items):
            self.progress.emit("  ✓ 格式修复成功")
            return items
        self.progress.emit(f"  ⚠ 模型原始输出前 300 字：{(text or '')[:300]}")
        raise RuntimeError(
            f"{what}两次解析失败（原始输出见上方与 api_debug.log），请重试或换模型")

    def _ensure_chapter(self, db, o, no, name):
        """找到或创建该章纲对应的章节，返回 (cid, ch行)"""
        chapters = db.get_chapters(self.pid)
        outline_id = o["id"] if o is not None else 0
        for c in chapters:
            if outline_id and c["outline_id"] == outline_id:
                return c["id"], c
        if no:
            target_volume = (o["volume"] if o is not None else self.vol) or self.vol or 1
            same_number = [c for c in chapters
                           if c["volume"] == target_volume
                           and c["chapter_no"] == no]
            if same_number:
                # Reuse an empty shell instead of creating a duplicate chapter.
                ch = next((c for c in same_number
                           if not (c["content"] or "").strip()), same_number[0])
                if outline_id and ch["outline_id"] != outline_id:
                    db.update_chapter_meta(ch["id"], outline_id=outline_id)
                    ch = db.get_chapter(ch["id"])
                return ch["id"], ch
        if not no:
            no = max([c["chapter_no"] for c in chapters], default=0) + 1
        vol = (o["volume"] if o is not None else self.vol) or self.vol or 1
        card = (f"- 本章目标：{o['content']}"
                if o is not None and (o["content"] or "").strip() else "")
        cid = db.create_chapter(self.pid, vol, no, name, chapter_card=card,
                                outline_id=outline_id)
        self.progress.emit(f"  ✓ 新建章节（第{no}章，绑定章纲）")
        return cid, db.get_chapter(cid)

    def _do_chapter(self, db, o, no, name, cid=0):
        return chapter_execution.execute(self, db, o, no, name, cid)

    def _finish_chapter(self, db, cid, name):
        return chapter_memory.finish(self, db, cid, name)
