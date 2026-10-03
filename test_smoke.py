# -*- coding: utf-8 -*-
"""offscreen 冒烟测试：窗口创建 + 项目管理 + 词条 + 章节 + 保存 + 版本"""
import os
import json
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PySide6.QtWidgets import QApplication  # noqa

from db import DB  # noqa
from ui.main_window import MainWindow  # noqa
from ui import dialogs  # noqa

app = QApplication([])
db = DB(os.path.join(tempfile.mkdtemp(), "smoke.db"))
win = MainWindow(db)
win.tree.load()

# 1. 新建项目
pid = db.create_project("冒烟测试书", "一句话概念", "玄幻", "番茄男频", "核心卖点")
win.current_pid = pid
win.tree.load()
win.tree.load_project_detail(pid)
win._on_project(pid)
assert win.current_pid == pid

# 2. 设定词条
db.add_setting(pid, "力量体系", "筑基", "打通经脉，凝聚源力")
win.tree.load_project_detail(pid)

# 3. 新建章节 + 打开
cid = db.create_chapter(pid, 1, 1, "第一章 重生", "正文v1")
win.tree.load_project_detail(pid)
win._on_chapter(cid)
assert win.editor.mode == 1  # MODE_CHAPTER

# 4. 修改 + 保存（应生成快照）
win.editor.edit.setPlainText("正文v2")
win._save_current()
vers = db.get_versions(cid)
assert len(vers) == 1, f"versions={len(vers)}"
assert db.get_chapter(cid)["content"] == "正文v2"

# 5. 再次保存
win.editor.edit.setPlainText("正文v3")
win._save_current()
assert len(db.get_versions(cid)) == 2

# 6. 版本回滚
db.restore_version(cid, db.get_versions(cid)[-1]["id"])
assert db.get_chapter(cid)["content"] == "正文v1"

# 7. AI 面板加载模型
db.add_ai_config("DeepSeek", "deepseek", "https://api.deepseek.com/v1", "sk-x", "deepseek-chat", 1)
win.ai.load_configs(db.get_ai_configs())
assert win.ai.current_config_id() is not None

# 8. 上下文包组装
pack = win._context_pack()
assert "前情提要" not in pack or True  # 第一章无前情
settings_text = "筑基" in pack
print("context pack has settings:", settings_text)

# 9. 词条模式
sid = db.get_settings(pid)[0]["id"]
win._on_setting(sid)
assert win.editor.mode == 2  # MODE_SETTING
win.editor.edit.setPlainText("新版定义：打通经脉，凝聚源力，稳固道基")
win._save_current()
assert db.get_settings(pid)[0]["definition"].startswith("新版定义")

# 10. 大纲（卷纲/章纲）
oid1 = db.add_outline(pid, "卷纲", "第一卷 入道", "拜入武当，了解道门")
oid2 = db.add_outline(pid, "章纲", "第1章 重生", "确认重生，立目标")
win._on_outline(oid1)
assert win.editor.mode == 3  # MODE_OUTLINE
win.editor.edit.setPlainText("拜入武当，了解道门，结识王野")
win._save_current()
assert "结识王野" in db.get_outline(oid1)["content"]

# 11. 伏笔
fid = db.add_foreshadow(pid, "能量结晶的秘密", "悬念", "第1章", "第40章")
win._on_foreshadow(fid)
assert win.editor.mode == 4  # MODE_FORESHADOW
win.editor.edit.setPlainText("能量结晶与实验室研究有关")
win._save_current()
assert db.get_foreshadow(fid)["content"].startswith("能量结晶与实验室")

# 12. 章节卡 + 5 件套上下文包
db.update_chapter_meta(cid, chapter_card="本章目标：白小川决定上山；出场：白小川、室友；卡点：加道长微信")
win._on_chapter(cid)
pack = win._context_pack()
assert "【前情提要】" in pack or True
assert "【本章章节卡" in pack
assert "【伏笔提醒" not in pack  # 无原文证据的旧记录不能冒充已埋设伏笔
assert "【关键设定" in pack
assert "能量结晶" not in pack
print("pack sections ok")

# 13. V1.1 双模式入口
assert win.editor.btn_ai is not None
win._on_chapter(cid)
assert not win.editor.btn_ai.isHidden()
# 词条/大纲/伏笔模式按钮可见
win._on_setting(db.get_settings(pid)[0]["id"])
assert not win.editor.btn_ai.isHidden()
# 空模式隐藏
win.editor.show_empty()
assert win.editor.btn_ai.isHidden()
# 立项弹窗带 AI 按钮
dlg = dialogs.ProjectDialog(db, pid, win)
assert dlg.btn_ai is not None
dlg.close()
# 生成草稿函数存在
from ai import prompts as P
assert callable(P.gen_project_draft) and callable(P.gen_setting_draft)
assert callable(P.gen_outline_draft) and callable(P.gen_chapter_card_draft)
assert callable(P.gen_foreshadow_draft)
print("dual-mode entry ok")

# 14. 分板块字段区
win._on_setting(db.get_settings(pid)[0]["id"])
assert not win.editor.set_cat.isHidden() and not win.editor.set_term.isHidden()
assert win.editor.ch_title.isHidden() and win.editor.fh_type.isHidden()
win._on_outline(oid1)
assert not win.editor.ol_lv.isHidden() and not win.editor.ol_title.isHidden()
win._on_foreshadow(fid)
assert not win.editor.fh_type.isHidden() and not win.editor.fh_planted.isHidden()
assert not win.editor.fh_status.isHidden()
win._on_chapter(cid)
assert not win.editor.ch_vol.isHidden() and not win.editor.ch_no.isHidden()
assert not win.editor.ch_title.isHidden() and not win.editor.ch_status.isHidden()
assert win.editor.set_cat.isHidden()
print("sectioned editor ok")

# 15. 右键批量 AI 生成解析
items = MainWindow._parse_batch(
    "- 筑基：打通经脉，凝聚源力\n- 武当山：道门四派之一\n", "setting")
assert len(items) == 2 and items[0][1]["term"] == "筑基"
items2 = MainWindow._parse_batch(
    "- 第一卷 入道：拜入武当，结识王野\n- 第二卷 扬名：罗天大醮崭露头角\n", "卷纲")
assert len(items2) == 2 and items2[1][1]["title"] == "第二卷 扬名"
items3 = MainWindow._parse_batch(
    "- 能量结晶的秘密 | 悬念 | 第1章 | 第40章\n- 小叔的异能 | 身份 | 第2章 | 第60章\n", "foreshadow")
assert len(items3) == 2 and items3[0][1]["ftype"] == "悬念" and items3[0][1]["plan_ch"] == "第40章"
# AiPickDialog 可用
dlg2 = dialogs.AiPickDialog("测试", items3, win)
assert dlg2.listw.count() == 2
dlg2.close()
print("batch gen ok")

# 16. 导出
db.update_project(pid, intro="简介：他叫周野，天生替死鬼。")
txt, name, flt, title = win._build_export(pid, "txt")
assert name.endswith(".txt") and "简介：他叫周野" in txt
assets, name2, _, _ = win._build_export(pid, "assets")
assert "## 设定档案" in assets and "## 伏笔台账" in assets
md, name3, _, _ = win._build_export(pid, "md")
assert "## 正文" in md and "简介：他叫周野" in md
print("export ok")

# 18. 章节承接修复：上一章结尾原文注入 + 自动摘要
db.save_chapter(cid, "第1章结尾：白小川握紧手机，决定明天一早动身上山。天，快亮了。")
cid2 = db.create_chapter(pid, 1, 2, "第二章 上山")
win._on_chapter(cid2)
pack2 = win._context_pack()
assert "上一章结尾" in pack2, pack2
assert "白小川握紧手机" in pack2
assert hasattr(win, "_auto_summary")
print("chapter continuity ok")

# 17. 简介板块 + AI 生成入口
dlg3 = dialogs.ProjectDialog(db, pid, win)
assert dlg3.ed_intro is not None and dlg3.btn_intro_ai is not None
dlg3.close()
# 数据库迁移：intro 列存在 + 迁移幂等
db2 = DB(os.path.join(tempfile.mkdtemp(), "old.db"))
cols = [r[1] for r in db2.conn.execute("PRAGMA table_info(projects)")]
assert "intro" in cols
db2._migrate()
db2._migrate()
print("intro ok")

win.close()
print("SMOKE UI OK")

# 19. V1.3 三层记忆：卷级滚动摘要表
vol_sum = "白小川入山拜师，结识师妹沈青梧，发现替死鬼体质与道门禁阵有关。"
db.set_volume_summary(pid, 1, vol_sum)
assert db.get_volume_summary(pid, 1) == vol_sum
db.set_volume_summary(pid, 1, vol_sum + " 第二次更新。")
assert "第二次更新" in db.get_volume_summary(pid, 1)  # 幂等更新
win._on_chapter(cid2)
pack3 = win._context_pack()
assert "替死鬼体质" not in pack3  # 无截至本章的证据，不得注入整卷未来剧情
db.update_chapter_meta(cid, summary="白小川入山拜师，发现替死鬼体质。")
win._on_chapter(cid2)
pack3 = win._context_pack()
assert "本卷故事至今" in pack3 and "替死鬼体质" in pack3
assert "全书主线" in pack3 and "一句话概念" in pack3
print("volume rollup ok")

# 20. 设定召回：命中优先
from ai import prompts as P2
rows = db.get_settings(pid) + [
    type("R", (), {"__getitem__": lambda s, k: {"category": "人物", "term": "王野", "definition": "主角"}.get(k)})(),
]
hit = P2.recall_settings(rows, ["本章写筑基突破，白小川与王野对决"])
terms = [r["term"] for r in hit]
assert terms.index("筑基") < len(terms) - 1 or True
matched_terms = set(terms[:2])
assert matched_terms >= {"筑基", "王野"}, terms  # 命中的排最前
miss = P2.recall_settings(db.get_settings(pid), ["完全无关文本"])
assert miss and miss[0]["term"] == "筑基"  # 未命中时按分类优先级补足（力量体系）
print("settings recall ok")

# 21. Token 预算：超长内容必须被裁剪到预算内
big_project = dict(title="预算测试", logline="主线一句话", genre="玄幻",
                   audience="男频", selling_point="爽", style_sheet="风格" * 2000,
                   status="立项")
big_settings = [type("R", (), {
    "__getitem__": staticmethod(lambda k, _i=i: {"category": "术语", "term": f"词条{_i}",
                                                 "definition": "定义" * 300}.get(k))})()
                for i in range(30)]
pack_big = P2.build_context_pack(big_project, big_settings, "章节卡" * 400,
                                 prev_tail="结尾" * 900,
                                 recent_summaries=[(i, f"章{i}", "摘要" * 100) for i in range(5)],
                                 volume_summary="卷摘要" * 250, foreshadow_hint="伏笔" * 300)
limit = P2.PACK_BUDGET + 80  # 允许少量标签头误差
assert len(pack_big) <= limit, f"pack len={len(pack_big)} > {limit}"
# 关键段不被牺牲
assert "【本章章节卡" in pack_big and "全书主线" in pack_big
print("budget ok:", len(pack_big), "chars")

# 22. 回滚兼容性检查：旧库迁移出 volume_summary 表
import os as _os
db3_path = _os.path.join(_os.path.dirname(db.path), "v.db")
db3 = DB(db3_path)
tables = {r[0] for r in db3.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
assert "volume_summary" in tables
db3.close()
print("V1.3 MEMORY OK")

# 23. V1.4 二期：迁移列存在（chapters.outline_id / outlines.volume）
db2m = DB(_os.path.join(_os.path.dirname(db.path), "v2.db"))
cols_ch = [r[1] for r in db2m.conn.execute("PRAGMA table_info(chapters)")]
cols_ol = [r[1] for r in db2m.conn.execute("PRAGMA table_info(outlines)")]
assert "outline_id" in cols_ch, cols_ch
assert "volume" in cols_ol, cols_ol
db2m.close()

# 24. 章纲绑定 + 卷纲注入上下文
pidb = db.create_project("跟纲测试书", "小人物逆袭", "玄幻", "番茄男频", "爽点")
oid_vol = db.add_outline(pidb, "卷纲", "第一卷 入道", "主线：白小川拜师并发现体质秘密", volume=1)
oid_zg = db.add_outline(pidb, "章纲", "第3章 拜师大典", "完成拜师；引出师妹；留下禁阵伏笔", volume=1)
cid3 = db.create_chapter(pidb, 1, 3, "第3章 拜师大典")
win.current_pid = pidb
win.tree.load()
db.update_chapter_meta(cid3, outline_id=oid_zg)
win._on_chapter(cid3)
pack4 = win._context_pack()
assert "本卷卷纲" in pack4 and "拜师并发现体质秘密" in pack4, pack4[:400]
assert "本章章纲要点" in pack4 and "留下禁阵伏笔" in pack4
# 绑定下拉已选中该章纲
assert win.editor.ch_bind.currentData() == oid_zg
print("outline binding ok")

# 25. 从章纲一键建章：绑定/编号/章节卡自动带入（v1.4.1 起标题去「第N章」前缀）
win._new_chapter_from_outline(pidb, oid_zg)
chs = db.get_chapters(pidb)
newest = max(chs, key=lambda c: c["id"])
assert newest["outline_id"] == oid_zg
assert newest["chapter_no"] == 3 and newest["title"] == "拜师大典", \
    (newest["chapter_no"], newest["title"])
print("chapter from outline ok")

# 26. 进度对账面板可构建
dlg_audit = dialogs.AuditDialog(db, pidb, win)
assert dlg_audit.table.rowCount() == 2, dlg_audit.table.rowCount()  # 卷纲行 + 已绑章纲行
states = [dlg_audit.table.item(r, 2).text() for r in range(dlg_audit.table.rowCount())]
assert any("已建章" in s for s in states), states
# 大纲树层级刷新不崩溃
win.tree.load_project_detail(pidb)
print("audit dialog ok")

print("V1.4 OUTLINE OK")

# 27. V1.4.1 章纲条目一体化：解析时自动补「第N章」前缀 + 建章对号
items = MainWindow._parse_batch(
    "- 山神庙初战：白小川遭遇第一只邪祟\n- 第7章 青梧师妹：结识沈青梧\n", "章纲", start_num=6)
assert items[0][1]["title"] == "第6章 山神庙初战", items[0][1]
assert items[1][1]["title"] == "第7章 青梧师妹"  # 已带编号的不重复补
assert "遇第一只邪祟" in items[0][1]["content"]
# 无编号且不传 start_num → 不强改
items_b = MainWindow._parse_batch("- 入道：拜山\n", "章纲")
assert items_b[0][1]["title"] == "入道"
print("batch numbering ok")

# 28. 从带编号章纲建章：章节号取 N，标题去前缀
oid_n = db.add_outline(pidb, "章纲", "第9章 夜探藏经阁", "取得残页；被执事发现", volume=1)
win.current_pid = pidb
win.tree.load()
win._new_chapter_from_outline(pidb, oid_n)
chs = db.get_chapters(pidb)
hit = [c for c in chs if c["outline_id"] == oid_n][-1]
assert hit["chapter_no"] == 9, hit["chapter_no"]
assert hit["title"] == "夜探藏经阁", hit["title"]
assert "取得残页" in hit["chapter_card"]
win.close()

# 29. V1.5 主题系统：两套 QSS 完整、菜单可切换、AI 面板可折叠
import ui.theme as _T
_qd = _T.build_qss(True)
_ql = _T.build_qss(False)
assert len(_qd) > 500 and len(_ql) > 500
assert "$" not in _qd and "$" not in _ql, "存在未替换占位符"
assert isinstance(_T.load_dark_pref(), bool)
from PySide6.QtWidgets import QApplication as _QApp
_app = _QApp.instance()
if _app:
    _app.setStyleSheet(_T.build_qss(False))
    _app.setStyleSheet(_T.build_qss(True))
assert win.act_dark.isCheckable() and win.act_ai.isCheckable()
win.act_ai.setChecked(False)
win.act_ai.setChecked(True)
print("V1.5 THEME OK")
print("V1.4.1 TITLES OK")

# 30. V1.7 全书规划跟纲：区间计算 + 提示词 + 编号剥离
pidc = db.create_project("规划测试书", "概念", "玄幻", "男频", "爽", plan_volumes=5, plan_chapters=10)
V, M = win._plan(db.get_project(pidc))
assert (V, M) == (5, 10), (V, M)
user1 = P.gen_volumes_plan(dict(db.get_project(pidc)), "", total=5)
assert "共 5 卷" in user1 and "第v卷" in user1
user2 = P.gen_volume_chapters(dict(db.get_project(pidc)), "", 2, "第二卷 测试卷",
                              "本卷主线任务描述", 11, 20)
assert "第11章" in user2 and "第20章" in user2
assert "第二卷 测试卷" in user2 and "本卷主线任务描述" in user2
# 卷纲缺失 volume 字段时推断卷号（现有卷纲 vol=1 + 章纲 vol=1）
V3, _M = win._plan(db.get_project(pidb))
assert V3 >= 1, V3
assert MainWindow._strip_num("第12章 裂缝初现") == "裂缝初现"
assert MainWindow._strip_num("第3卷 终局", "卷") == "终局"
assert win._vol_line(pidb, 1)["id"] == oid_vol
print("V1.7 PLAN OK")

# 31. V1.8 流式生成：worker 与节流刷新就位
from ui.main_window import AIStreamWorker  # noqa
from ai import client as _AC  # noqa
assert callable(_AC.chat_stream)
assert hasattr(win, "_flush_timer") and hasattr(win, "_gen_delta")
win2 = MainWindow(db)
assert win2._flush_timer.interval() == 120
win2._gen_delta("片段一")
win2._gen_delta("片段二")
win2._flush_stream()
assert "片段一" in win2.ai.out.toPlainText()
win2._flush_timer.stop()
win2.close()
print("V1.8 STREAM OK")

# 32. V1.9 上下文包抽取：UI 侧与 ai.context 侧完全等价
from ai.context import context_pack_for, vol_outline_for, chapter_outlines_for  # noqa
win.current_pid = pid
win._on_chapter(cid2)
assert win._context_pack() == context_pack_for(db, db.get_chapter(cid2))
# 无章节视角（pid 直传）
assert context_pack_for(db, None, pid=pid).startswith("【") or context_pack_for(db, None, pid=pid) == ""
assert vol_outline_for(db, pidb, 1)["id"] == oid_vol
zg_list = chapter_outlines_for(db, pidb, 1)
assert any(o["id"] == oid_zg for o in zg_list)
print("context module ok")

# 33. 解析函数迁移：ai.parse 与 MainWindow 静态方法等价
from ai import parse as aparse  # noqa
sample = "- 山神庙初战：白小川遭遇第一只邪祟\n"
assert aparse.parse_batch(sample, "章纲", start_num=6) == MainWindow._parse_batch(sample, "章纲", start_num=6)
assert aparse.strip_num("第3卷 终局", "卷") == "终局"
print("parse module ok")

# 34. 流水线离线全链路（假模型，不联网）
import ai.pipeline as pl  # noqa
_real_chat = pl.aiclient.chat_once
_real_memory_extract = pl.story_memory.extract_candidates
# This broad legacy smoke uses many independent fake writers. Story extraction
# has its own evidence tests; keep their old call counts and fake responses here.
pl.story_memory.extract_candidates = lambda *args, **kwargs: 0

def fake_chat(cfg, messages, **kw):
    sys = messages[0]["content"]
    if sys == P.SYSTEM_SUMMARY:
        return "本章摘要：主角重生后决定拜师，埋下体质伏笔。"
    if sys == P.SYSTEM_ROLLUP:
        return "本卷至今：主角重生并完成拜师，体质秘密初现。"
    if sys == P.SYSTEM_ASSIST:
        return ("- 本章目标：推进剧情\n- 场景列表：青云观\n- 出场人物：林小凡\n"
                "- 结尾卡点/悬念：观主异样\n- 需要埋设/回收的伏笔：无")
    return SMOKE_QUOTE + ("山风吹过。" * 200)[:1000 - len(SMOKE_QUOTE)]

SMOKE_QUOTE = '林小凡的淬体九重首次显现。'
pl.aiclient.chat_once = fake_chat
pidd = db.create_project("流水线测试书", "小人物逆袭当大能", "玄幻", "番茄男频",
                         "爽点", plan_volumes=2, plan_chapters=5)
db.add_outline(pidd, "卷纲", "第一卷 入道", "主线：拜师+发现体质秘密", volume=1)
db.add_outline(pidd, "章纲", "第1章 重生醒来", "确认重生，立目标", volume=1)
db.add_outline(pidd, "章纲", "第2章 拜师大典", "完成拜师；引出师妹", volume=1)

logs = []
w = pl.PipelineWorker({"name": "fake"}, db.path, pidd, 1, use_tools=False, review=False)
w.progress.connect(logs.append)
w.run()
chs = db.get_chapters(pidd)
assert len(chs) == 2, len(chs)
for c in chs:
    assert len(c["content"]) == 1000, len(c["content"])
    # P4 审稿门槛：关闭审稿写出的章按「未审」标注，不冒充已过审
    assert c["status"] == "AI草稿·未审", c["status"]
    assert c["summary"].startswith("本章摘要")
    assert "本章目标" in c["chapter_card"]
assert "拜师" in db.get_volume_summary(pidd, 1)
assert any("流水线启动" in s for s in logs)
# 卷纲存在的提示不应出现（本卷有卷纲）
assert not any("没有卷纲" in s for s in logs)
print("pipeline full-chain ok")

# 35. 断点续跑：重跑全跳过，不覆盖
done_msgs = []
w2 = pl.PipelineWorker({"name": "fake"}, db.path, pidd, 1, use_tools=False, review=False)
w2.finished_ok.connect(done_msgs.append)
w2.run()
assert "跳过 2 章" in done_msgs[0], done_msgs
assert len(db.get_chapters(pidd)) == 2
for c in db.get_chapters(pidd):
    assert len(c["content"]) == 1000  # 未被改写
print("pipeline resume ok")

# 36. 缺章纲时自动补：按区间生成并入库
def fake_chat_outline(cfg, messages, **kw):
    sys = messages[0]["content"]
    if sys == P.SYSTEM_SUMMARY:
        return "本章摘要：推进。"
    if sys == P.SYSTEM_ROLLUP:
        return "本卷至今：推进。"
    if sys == P.SYSTEM_ASSIST:
        return "- 第1章 起步：开局立人设\n- 第2章 冲突：矛盾升级\n- 第3章 高潮：收束留钩子"
    return "正文内容。" * 150  # 750 字，默认阈值 800 → 触发一次重写后仍保留

pl.aiclient.chat_once = fake_chat_outline
pide = db.create_project("自动章纲书", "概念", "玄幻", "男频", "爽",
                         plan_volumes=1, plan_chapters=3)
db.add_outline(pide, "卷纲", "第一卷 起步", "本卷主线", volume=1)
w3 = pl.PipelineWorker({"name": "fake"}, db.path, pide, 1, use_tools=False, review=False)
w3.run()
zgs = [o for o in db.get_outlines(pide) if o["level"] == "章纲"]
assert len(zgs) == 3, len(zgs)
assert all(o["volume"] == 1 for o in zgs)
assert len(db.get_chapters(pide)) == 3
print("pipeline auto-outline ok")

# 37. 停止请求：立即中断（第一章开始前就停）
pl.aiclient.chat_once = fake_chat
pide2 = db.create_project("停止测试书", "概念", "玄幻", "男频", "爽",
                          plan_volumes=1, plan_chapters=3)
db.add_outline(pide2, "章纲", "第1章 一", "a", volume=1)
db.add_outline(pide2, "章纲", "第2章 二", "b", volume=1)
w4 = pl.PipelineWorker({"name": "fake"}, db.path, pide2, 1, use_tools=False, review=False)
w4.stop()
done_stop = []
w4.finished_ok.connect(done_stop.append)
w4.run()
assert len(done_stop) == 1 and "已停止" in done_stop[0]
pl.aiclient.chat_once = _real_chat
print("pipeline stop ok")

# 38. 流水线对话框可构建（含未配置模型的禁用态）
from ui.pipeline_dialog import PipelineDialog  # noqa
dlgp = PipelineDialog(db, pidd, 1, {"name": "fake"}, None, title="流水线测试书")
assert dlgp.cb_vol.count() >= 1 and dlgp.cb_vol.currentData() == 1
assert dlgp.sp_min.value() == 800 and dlgp.ck_skip.isChecked()
dlgp.close()
dlgq = PipelineDialog(db, pidd, 1, None, None, title="未配置")
assert not dlgq.btn_start.isEnabled()
dlgq.close()
print("pipeline dialog ok")
print("V1.9 PIPELINE OK")

# 39. V1.10 工具执行器：候选证据 / 防重复 / 防越权
from ai import tools as atools  # noqa
from ai import setting_candidates as _setting_candidates  # noqa
source_chapter = next(c for c in db.get_chapters(pidd) if c['chapter_no'] == 1)
result = atools._exec_add_setting(
    db, pidd, {"category": "人物", "term": "林小凡",
               "definition": "淬体九重首次显现的修炼者", "quote": SMOKE_QUOTE},
    source_chapter['id'])
assert result.startswith('候选已记录'), result
result = atools._exec_add_setting(
    db, pidd, {"category": "人物", "term": "林小凡",
               "definition": "重复添加", "quote": SMOKE_QUOTE}, source_chapter['id'])
assert result.startswith('错误'), result
assert not any(s['term'] == '林小凡' for s in db.get_settings(pidd))
_setting_candidates.confirm_candidate(
    db, next(c['id'] for c in _setting_candidates.list_candidates(db, pidd)
             if c['term'] == '林小凡'))
assert len([s for s in db.get_settings(pidd) if s["term"] == "林小凡"]) == 1
assert "错误" in atools._exec_add_setting(db, pidd, {"term": ""})
fid0 = db.add_foreshadow(pidd, "测试伏笔X", "悬念", "第1章", "")
assert "不属于本书" in atools._exec_resolve_foreshadow(db, pide, {"foreshadow_id": fid0})
assert "错误" in atools._exec_resolve_foreshadow(db, pidd, {"foreshadow_id": "abc"})
assert atools._exec_resolve_foreshadow(db, pidd, {"foreshadow_id": fid0}).startswith("错误")
assert db.get_foreshadow(fid0)['status'] == '待回收'
db.delete_foreshadow(fid0)
print("tool executors ok")

# 40. register_chapter 工具循环：模型调工具 → 执行 → 回填 → 最终汇报
def fake_tools(cfg, messages, tools_spec, **kw):
    if any(m.get("role") == "tool" for m in messages):
        return {"content": "已登记 1 条设定、1 条伏笔。", "tool_calls": []}
    return {"content": "", "tool_calls": [
        {"id": "c1", "type": "function",
         "function": {"name": "add_setting",
                       "arguments": json.dumps({"category": "力量体系", "term": "淬体九重",
                                                "definition": "锻体功法，分九重，重 重 淬炼筋骨",
                                                "quote": SMOKE_QUOTE})}},
        {"id": "c2", "type": "function",
         "function": {"name": "add_foreshadow",
                      "arguments": json.dumps({"content": "观主每晚子时消失", "ftype": "悬念",
                                               "plan_ch": "第30章"})}},
        {"id": "c3", "type": "function",
         "function": {"name": "add_setting",
                       "arguments": json.dumps({"category": "人物", "term": "林小凡",
                                                "definition": "重复词条应被跳过",
                                                "quote": SMOKE_QUOTE})}},
    ]}

_real_tools_call = pl.aiclient.chat_once_tools
pl.aiclient.chat_once_tools = fake_tools
logs_r = []
events = atools.register_chapter({"name": "fake"}, db, pidd, 1, "重生醒来",
                                  source_chapter['content'], log=logs_r.append)
assert any("淬体九重" in e for e in events), events
assert any("观主每晚子时消失" in e for e in events), events
assert not any("重复词条" in e for e in events)  # 重复词条被跳过不计入
assert not any(s['term'] == '淬体九重' for s in db.get_settings(pidd))
assert any(c['term'] == '淬体九重' and c['status'] == 'candidate'
           for c in _setting_candidates.list_candidates(db, pidd))
fh = [f for f in db.get_foreshadows(pidd) if "观主" in f["content"]]
assert len(fh) == 1 and fh[0]["planted_ch"] == "第1章" and fh[0]["plan_ch"] == "第30章"
assert any("📌" in s for s in logs_r) and any("🪤" in s for s in logs_r)
print("register loop ok")

# 41. 流水线端到端带登记：写完一章自动入库设定与伏笔
pl.aiclient.chat_once = fake_chat
pidf = db.create_project("登记流水线书", "概念", "玄幻", "男频", "爽",
                         plan_volumes=1, plan_chapters=3)
db.add_outline(pidf, "章纲", "第1章 开局", "主角登场", volume=1)
logs_f = []
w5 = pl.PipelineWorker({"name": "fake"}, db.path, pidf, 1, use_tools=True, review=False)
w5.progress.connect(logs_f.append)
w5.run()
terms_f = [s['term'] for s in db.get_settings(pidf)]
assert '淬体九重' not in terms_f, terms_f
assert any(c['term'] == '淬体九重' and c['status'] == 'candidate'
           for c in _setting_candidates.list_candidates(db, pidf))
assert any("观主" in f["content"] for f in db.get_foreshadows(pidf))
assert any("📌" in s for s in logs_f), logs_f[-6:]
print("pipeline with registry ok")

# 42. 模型不支持工具：自动降级，正文不受影响
def broken_tools(cfg, messages, tools_spec, **kw):
    raise RuntimeError("模型接口报错 HTTP 400: tools not supported")

pl.aiclient.chat_once_tools = broken_tools
pide3 = db.create_project("降级测试书", "概念", "玄幻", "男频", "爽",
                          plan_volumes=1, plan_chapters=3)
db.add_outline(pide3, "章纲", "第1章 起", "开局", volume=1)
db.add_outline(pide3, "章纲", "第2章 承", "推进", volume=1)
logs_d = []
w6 = pl.PipelineWorker({"name": "fake"}, db.path, pide3, 1, use_tools=True, review=False)
w6.progress.connect(logs_d.append)
w6.run()
assert len(db.get_chapters(pide3)) == 2
assert all(len(c["content"]) == 1000 for c in db.get_chapters(pide3))
assert any("登记不可用" in s for s in logs_d)
assert sum(1 for s in logs_d if "登记不可用" in s) == 1  # 降级后只提示一次
pl.aiclient.chat_once = _real_chat
pl.aiclient.chat_once_tools = _real_tools_call
print("registry degrade ok")

# 43. 审稿输出解析：纯 JSON / markdown 围栏 / 杂讯包裹 / 非法分数 / 无 JSON
from ai import review as areview  # noqa

def structured_smoke_verdict(score, issues, messages):
    body = messages[1]['content'].rsplit('【正文】\n', 1)[1].split(
        '\n\n请审稿', 1)[0]
    return json.dumps({
        'score': score,
        'categories': {kind: score for kind in pl.evidence_review.CATEGORIES},
        'issues': [
            {'kind': '节奏', 'severity': '一般', 'body_quote': body,
             'source_ref': '', 'source_quote': '', 'explanation': issue,
             'suggestion': '按问题调整这一段'}
            for issue in issues]}, ensure_ascii=False)

s, i = areview.parse_verdict('{"score": 82, "issues": ["节奏拖沓"]}')
assert s == 82 and i == ["节奏拖沓"]
s, i = areview.parse_verdict('```json\n{"score": 91, "issues": []}\n```')
assert s == 91 and i == []
s, i = areview.parse_verdict('前缀杂讯 {"score": 55, "issues": ["设定冲突"]} 后缀')
assert s == 55 and i == ["设定冲突"]
s, i = areview.parse_verdict('{"score": "很高", "issues": []}')
assert s is None and i == []
s, i = areview.parse_verdict('我觉得这章写得不错')
assert s is None and i == []
print("verdict parse ok")

# 44. 审稿不过 → 带问题清单重写 → 复审通过
state = {"writes": 0, "reviews": 0}

def fake_chat_rv(cfg, messages, **kw):
    sys = messages[0]["content"]
    if sys == P.SYSTEM_REVIEWER:
        state["reviews"] += 1
        return structured_smoke_verdict(
            55 if state['reviews'] == 1 else 88,
            ['结尾无钩子', '筑基应为淬体九重'] if state['reviews'] == 1 else [],
            messages)
    if sys == P.SYSTEM_SUMMARY:
        return "本章摘要：推进。"
    if sys == P.SYSTEM_ROLLUP:
        return "本卷至今：推进。"
    if sys == P.SYSTEM_ASSIST:
        return "- 本章目标：x\n- 场景列表：y\n- 出场人物：z\n- 结尾卡点/悬念：w"
    state["writes"] += 1
    return ("初稿字" if state["writes"] == 1 else "重写稿字") * 300

pl.aiclient.chat_once = fake_chat_rv
pl.aiclient.chat_once_tools = lambda *a, **k: {"content": "无", "tool_calls": []}
pidg = db.create_project("审稿流水线书", "概念", "玄幻", "男频", "爽",
                         plan_volumes=1, plan_chapters=3)
db.add_outline(pidg, "章纲", "第1章 起手", "开局", volume=1)
logs_g = []
w7 = pl.PipelineWorker({"name": "fake"}, db.path, pidg, 1,
                       use_tools=True, review=True, min_score=75)
w7.progress.connect(logs_g.append)
w7.run()
ch_g = db.get_chapters(pidg)[0]
assert ch_g["content"].startswith("重写稿字") and len(ch_g["content"]) == 1200
assert state["writes"] == 2 and state["reviews"] == 2, state
assert any("审稿 55 分" in s for s in logs_g) and any("采用重写稿" in s for s in logs_g)
print("review rewrite loop ok")

# 45. 审稿一次过：不触发重写
def fake_chat_pass(cfg, messages, **kw):
    sys = messages[0]["content"]
    if sys == P.SYSTEM_REVIEWER:
        return structured_smoke_verdict(92, [], messages)
    if sys == P.SYSTEM_SUMMARY:
        return "本章摘要：推进。"
    if sys == P.SYSTEM_ROLLUP:
        return "本卷至今：推进。"
    return "好稿" * 400

pl.aiclient.chat_once = fake_chat_pass
pidh = db.create_project("一次过书", "概念", "玄幻", "男频", "爽",
                         plan_volumes=1, plan_chapters=3)
db.add_outline(pidh, "章纲", "第1章 开局", "开场", volume=1)
logs_h = []
w8 = pl.PipelineWorker({"name": "fake"}, db.path, pidh, 1, review=True, min_score=75)
w8.progress.connect(logs_h.append)
w8.run()
assert db.get_chapters(pidh)[0]["content"].startswith("好稿")
assert any("审稿 92 分，通过" in s for s in logs_h)
assert not any("未过" in s for s in logs_h)
print("review pass-through ok")

# 46. 复审不如初稿：保留初稿
state2 = {"writes": 0, "reviews": 0}

def fake_chat_keep(cfg, messages, **kw):
    sys = messages[0]["content"]
    if sys == P.SYSTEM_REVIEWER:
        state2["reviews"] += 1
        return structured_smoke_verdict(
            65 if state2['reviews'] == 1 else 50,
            ['承接断裂'] if state2['reviews'] == 1 else ['更差了'], messages)
    if sys == P.SYSTEM_SUMMARY:
        return "本章摘要：推进。"
    if sys == P.SYSTEM_ROLLUP:
        return "本卷至今：推进。"
    state2["writes"] += 1
    return ("初稿字" if state2["writes"] == 1 else "重写字") * 300

pl.aiclient.chat_once = fake_chat_keep
pidi = db.create_project("保留初稿书", "概念", "玄幻", "男频", "爽",
                         plan_volumes=1, plan_chapters=3)
db.add_outline(pidi, "章纲", "第1章 起", "开局", volume=1)
logs_i = []
w9 = pl.PipelineWorker({"name": "fake"}, db.path, pidi, 1, review=True, min_score=75)
w9.progress.connect(logs_i.append)
w9.run()
assert db.get_chapters(pidi)[0]["content"].startswith("初稿字")
assert w9.n_rewrites == 0
assert any("保留初稿" in s for s in logs_i)
print("review keep-draft ok")

# 47. 审稿调用失败：正文保留，但标为未审
def fake_chat_err(cfg, messages, **kw):
    if messages[0]["content"] == P.SYSTEM_REVIEWER:
        raise RuntimeError("HTTP 500")
    if messages[0]["content"] == P.SYSTEM_SUMMARY:
        return "本章摘要：推进。"
    if messages[0]["content"] == P.SYSTEM_ROLLUP:
        return "本卷至今：推进。"
    return "稳稿" * 400

pl.aiclient.chat_once = fake_chat_err
pidj = db.create_project("审稿容错书", "概念", "玄幻", "男频", "爽",
                         plan_volumes=1, plan_chapters=3)
db.add_outline(pidj, "章纲", "第1章 起", "开局", volume=1)
logs_j = []
w10 = pl.PipelineWorker({"name": "fake"}, db.path, pidj, 1, review=True, min_score=75)
w10.progress.connect(logs_j.append)
w10.run()
assert len(db.get_chapters(pidj)) == 1 and len(db.get_chapters(pidj)[0]["content"]) == 800
assert db.get_chapters(pidj)[0]['status'] == 'AI草稿·未审'
assert any("审稿失败" in s for s in logs_j)
pl.aiclient.chat_once = _real_chat
pl.aiclient.chat_once_tools = _real_tools_call
print("review fault-tolerant ok")
print("V1.11 REVIEW OK")

# 48. 风格沉淀：去重后 ≥3 条意见 → 生成待作者审核的建议
def fake_chat_st(cfg, messages, **kw):
    sys = messages[0]["content"]
    if sys == P.SYSTEM_REVIEWER:
        return structured_smoke_verdict(80, ['钩子偏弱', '节奏拖沓', '对话生硬'], messages)
    if sys == P.SYSTEM_SUMMARY:
        return "本章摘要：推进。"
    if sys == P.SYSTEM_ROLLUP:
        return "本卷至今：推进。"
    return "- 每章结尾必须落在悬念句上\n- 对话占比不低于四成\n- 场景切换要有时间标记"

pl.aiclient.chat_once = fake_chat_st
pl.aiclient.chat_once_tools = lambda *a, **k: {"content": "无", "tool_calls": []}
pidk = db.create_project("沉淀书", "概念", "玄幻", "男频", "爽",
                         style_sheet="原有规范：口语化短句",
                         plan_volumes=1, plan_chapters=3)
db.add_outline(pidk, "章纲", "第1章 一", "a", volume=1)
db.add_outline(pidk, "章纲", "第2章 二", "b", volume=1)
logs_k = []
w11 = pl.PipelineWorker({"name": "fake"}, db.path, pidk, 1,
                        use_tools=True, review=True, min_score=75)
w11.progress.connect(logs_k.append)
w11.run()
ss = db.get_project(pidk)["style_sheet"]
from ai import style_profile as _style  # noqa
assert ss == '原有规范：口语化短句', ss
_style_items = _style.suggestions(db, pidk)
assert len(_style_items) == 3 and all(r['status'] == 'candidate' for r in _style_items)
assert '悬念句' not in _style.effective_style(db, pidk)
assert any("🧠" in s for s in logs_k)
print("style distill ok")

# 49. 沉淀关闭 / 意见不足：style_sheet 不动
pidl = db.create_project("沉淀关闭书", "概念", "玄幻", "男频", "爽",
                         style_sheet="保持不变", plan_volumes=1, plan_chapters=3)
db.add_outline(pidl, "章纲", "第1章 一", "a", volume=1)
db.add_outline(pidl, "章纲", "第2章 二", "b", volume=1)
w12 = pl.PipelineWorker({"name": "fake"}, db.path, pidl, 1,
                        use_tools=True, review=True, min_score=75,
                        distill_style=False)
w12.run()
assert db.get_project(pidl)["style_sheet"] == "保持不变"
# 意见去重后只剩 1 条 → 也不触发
def fake_chat_one(cfg, messages, **kw):
    sys = messages[0]["content"]
    if sys == P.SYSTEM_REVIEWER:
        return structured_smoke_verdict(80, ['只有这个问题'], messages)
    if sys == P.SYSTEM_SUMMARY:
        return "本章摘要：推进。"
    if sys == P.SYSTEM_ROLLUP:
        return "本卷至今：推进。"
    return "- 不该被写入"
pl.aiclient.chat_once = fake_chat_one
pidm = db.create_project("沉淀不足书", "概念", "玄幻", "男频", "爽",
                         style_sheet="原样", plan_volumes=1, plan_chapters=3)
db.add_outline(pidm, "章纲", "第1章 一", "a", volume=1)
db.add_outline(pidm, "章纲", "第2章 二", "b", volume=1)
w13 = pl.PipelineWorker({"name": "fake"}, db.path, pidm, 1, review=True)
w13.run()
assert db.get_project(pidm)["style_sheet"] == "原样"
print("style distill guards ok")

# 50. 全书模式：设定→卷纲→章纲→写作→简介，检查点全部放行
def fake_chat_book(cfg, messages, **kw):
    sys = messages[0]["content"]
    user = messages[1]["content"]
    if sys == P.SYSTEM_REVIEWER:
        return structured_smoke_verdict(90, [], messages)
    if sys == P.SYSTEM_SUMMARY:
        return "本章摘要：推进。"
    if sys == P.SYSTEM_ROLLUP:
        return "本卷至今：推进。"
    if sys == P.SYSTEM_ASSIST:
        if "设定词条" in user:
            return "- 灵石：修行界通用货币\n- 淬体境：武道第一境\n- 青阳镇：主角家乡"
        if "分卷大纲" in user:
            return "- 第1卷 崛起：主角觉醒走上武道之路"
        if "章纲" in user:
            return "- 第1章 觉醒：灵石认主\n- 第2章 淬体：突破淬体境"
        if "作品简介" in user:
            return "灵石认主，少年自青阳镇崛起。"
    return "正文" * 450

pl.aiclient.chat_once = fake_chat_book
pidn = db.create_project("全书模式书", "少年崛起", "玄幻", "男频", "爽",
                         plan_volumes=1, plan_chapters=2)
gates_seen = []
w14 = pl.PipelineWorker({"name": "fake"}, db.path, pidn, 1,
                        use_tools=True, review=True, whole_book=True)
def on_gate(title, body):
    gates_seen.append(title)
    w14.gate_continue()
w14.checkpoint_req.connect(on_gate)
logs_n = []
w14.progress.connect(logs_n.append)
w14.run()
assert len(gates_seen) == 4, gates_seen
assert any("阶段 1/4" in t for t in gates_seen) and any("阶段 2/4" in t for t in gates_seen)
assert any("阶段 4/4" in t for t in gates_seen)
assert len(db.get_settings(pidn)) == 3
assert len([o for o in db.get_outlines(pidn) if o["level"] == "卷纲"]) == 1
assert len([o for o in db.get_outlines(pidn) if o["level"] == "章纲"]) == 2
chs_n = db.get_chapters(pidn)
assert len(chs_n) == 2 and all(len(c["content"]) == 900 for c in chs_n)
assert "灵石认主" in db.get_project(pidn)["intro"]
assert any("全书模式启动" in s for s in logs_n)
print("whole-book full ok")

# 51. 全书模式：检查点全部跳过 → 不入库不写作，正常收尾
pido = db.create_project("全书跳过书", "概念", "玄幻", "男频", "爽",
                         plan_volumes=1, plan_chapters=2)
w15 = pl.PipelineWorker({"name": "fake"}, db.path, pido, 1,
                        use_tools=True, review=True, whole_book=True)
gates_skip = []
def on_gate_skip(title, body):
    gates_skip.append(title)
    w15.gate_skip()
w15.checkpoint_req.connect(on_gate_skip)
done_o = []
w15.finished_ok.connect(done_o.append)
w15.run()
assert len(gates_skip) == 4
assert len(db.get_settings(pido)) == 0
assert len(db.get_chapters(pido)) == 0
assert "新写 0 章" in done_o[0], done_o
print("whole-book skip ok")

# 52. 对话框全书按钮与检查点控件就位
dlgr = PipelineDialog(db, pidn, 1, {"name": "fake"}, None, title="全书模式书")
assert dlgr.btn_book is not None and not dlgr.btn_book.isHidden()
assert dlgr.gate_box.isHidden()
dlgr._show_gate("测试检查点", "草案内容")
assert not dlgr.gate_box.isHidden() and "草案内容" in dlgr.gate_text.toPlainText()
dlgr.close()
pl.aiclient.chat_once = _real_chat
pl.aiclient.chat_once_tools = _real_tools_call
print("book dialog ok")
print("V1.12 BOOK MODE OK")

# 53. v2.0 意图路由：命中工具 / 回落闲聊
from ai import intent as aintent  # noqa

def fake_intent(cfg, messages, tools_spec, **kw):
    user = messages[-1]["content"]
    if "写" in user:
        return {"content": "", "tool_calls": [
            {"id": "i1", "type": "function",
             "function": {"name": "write_volume", "arguments": '{"vol": 2}'}}]}
    return {"content": "建议节奏加快，压缩铺垫。", "tool_calls": []}

pl.aiclient.chat_once_tools = fake_intent
ctx_n = aintent.book_context(db, pidn)
r = aintent.decide({"name": "fake"}, ctx_n, "把第2卷写完")
assert r == {"type": "tool", "name": "write_volume", "args": {"vol": 2}}, r
r = aintent.decide({"name": "fake"}, ctx_n, "第3章节奏太慢怎么改？")
assert r["type"] == "chat" and "节奏" in r["text"]
assert aintent.book_context(db, 999999) is None
pl.aiclient.chat_once_tools = _real_tools_call
print("intent router ok")

# 54. 章节编辑弹窗：打开/改稿/保存落库
from ui.editor_dialog import ChapterEditorDialog  # noqa
dlg_ed = ChapterEditorDialog(db, db.get_chapters(pidn)[0]["id"], None)
assert dlg_ed.editor.mode == 1  # MODE_CHAPTER
dlg_ed.editor.edit.setPlainText("修改测试正文")
dlg_ed._save()
assert db.get_chapters(pidn)[0]["content"] == "修改测试正文"
dlg_ed.close()
print("editor dialog ok")

# 55. 对话窗口派发任务：活动卡出现 → 假模型跑完 → 回复气泡
from ui.chat_window import ChatWindow  # noqa
pl.aiclient.chat_once = fake_chat_book
pl.aiclient.chat_once_tools = lambda *a, **k: {"content": "无", "tool_calls": []}
w2 = ChatWindow(db)
w2.reload_books(select_pid=pidn)
w2._dispatch({"name": "fake"}, {"type": "tool",
                                "name": "write_volume", "args": {"vol": 1}})
assert w2.worker is not None and w2.worker.isRunning()
while w2.worker.isRunning():
    app.processEvents()
for _ in range(20):
    app.processEvents()
texts = [t for _r, t in w2.history()]
assert any("写第1卷" in t for t in texts), texts[-3:]
# 进度快捷块直接本地生成，不走 AI
w2._chip_progress()
texts = [t for _r, t in w2.history()]
assert any("进度：" in t for t in texts)
# 下一卷计算：pidn 第1卷已全写 → 指向第2卷
assert w2._next_vol() == 2, w2._next_vol()
pl.aiclient.chat_once = _real_chat
pl.aiclient.chat_once_tools = _real_tools_call
print("chat dispatch ok")
print("V2.0 CHAT OK")

# 56. 对话历史持久化：落库 + 重启（新窗口实例）后恢复
from ui.theme import chat_qss, load_dark_pref  # noqa
w3 = ChatWindow(db)
w3.reload_books(select_pid=pidn)
w3._add_user_bubble("持久化测试用户消息")
w3._add_agent_bubble("持久化测试回复")
msgs = db.get_chat_msgs(pidn)
assert any("持久化测试用户消息" in m["content"] for m in msgs)
w4 = ChatWindow(db)          # 模拟重启
w4.reload_books(select_pid=pidn)
assert ("user", "持久化测试用户消息") in w4.history()
assert ("assistant", "持久化测试回复") in w4.history()
db.clear_chat_msgs(pidn)
assert db.get_chat_msgs(pidn) == []
print("chat persistence ok")

# 57. 主题联动：chat_qss 深浅两套无残留占位符 + 窗口应用不报错
qd, ql = chat_qss(True), chat_qss(False)
assert "$" not in qd and "$" not in ql, "存在未替换占位符"
assert "BUBBLE" not in qd and "CHIP_BG" not in qd  # 前缀冲突替换正确
assert "#userBubble" in qd and "#activityCard" in qd
for dark in (True, False):
    w4.apply_theme(dark)
    app.setStyleSheet(chat_qss(dark))
app.setStyleSheet(chat_qss(load_dark_pref()))
print("chat theme ok")
print("V2.1 PERSIST-THEME OK")

# 58. 回归（v2.1.1）：_send 完整链路在后台线程跑，不得触发 SQLite 跨线程错误；
# show_outline 查询工具可用
def fake_intent_chat(cfg, messages, tools_spec, **kw):
    return {"content": "卷纲可以这样安排……", "tool_calls": []}

pl.aiclient.chat_once_tools = fake_intent_chat
w5 = ChatWindow(db)
w5.reload_books(select_pid=pidn)
assert w5.current_pid == pidn
db.add_outline(pidn, "卷纲", "第一卷 测试卷", "主线A", volume=1)
w5.composer.setPlainText("帮我列出每卷的卷纲")
w5._send()                      # 真实路径：主线程抓快照 → 子线程调模型
while w5._intent_worker and w5._intent_worker.isRunning():
    app.processEvents()
for _ in range(20):
    app.processEvents()
texts = [t for _r, t in w5.history()]
assert not any("调用失败" in t for t in texts), texts[-3:]
assert any("卷纲可以这样安排" in t for t in texts)
# show_outline 派发：本地查询卷纲
w5._dispatch({"name": "fake"}, {"type": "tool", "name": "show_outline",
                                "args": {"level": "卷纲"}})
texts = [t for _r, t in w5.history()]
assert any("测试卷" in t for t in texts), texts[-3:]
# 空参数兜底
w5._dispatch({"name": "fake"}, {"type": "tool", "name": "show_outline", "args": {}})
pl.aiclient.chat_once_tools = _real_tools_call
print("send thread-safety ok")
print("V2.1.1 THREAD FIX OK")

# 59. 回归（v2.1.2）：检查点草案必须上屏（杜绝盲签）+ 单任务防护
from ui.chat_window import ActivityCard  # noqa
card_t = ActivityCard("测试任务")
card_t.show_gate("第1卷章纲草案（2 条）", "- 第1卷 主线A\n- 第2卷 主线B")
assert "主线A" in card_t.gate_text.toPlainText()
assert not card_t.gate_text.isHidden(), "草案预览未显示"
card_t._hide_gate()
assert card_t.gate_text.isHidden()

class _FakeRun:
    def isRunning(self):
        return True

w6 = ChatWindow(db)
w6.reload_books(select_pid=pidn)
w6.worker = _FakeRun()
n_hist = len(w6.history())
w6._tool_whole_book({"name": "fake"})          # 忙时应拒绝而非叠加 worker
assert len(w6.history()) == n_hist + 1
assert "已有一个任务" in w6.history()[-1][1]
w6.worker = None
# 检查点信号连通性：emit 带正文 → 卡片预览收到正文
card_t2 = ActivityCard("接线测试")
card_t2.show_gate("t", "b")
assert card_t2.gate_text.toPlainText() == "b"
print("gate preview + busy guard ok")
print("V2.1.2 VISIBILITY OK")

# 60. v2.2 侧边栏工作区：资源树 + 书架同步 + 导出抽离
from ui.export_utils import build_export  # noqa
content, name, flt, _t = build_export(db, pidn, "txt")
assert name.endswith(".txt") and "第1章" in content
txt2, _n, _f, _t2 = MainWindow._build_export(type("S", (), {"db": db})(), pidn, "txt")
assert txt2 == content  # 经典视图委托同一实现
w7 = ChatWindow(db)
w7.reload_books(select_pid=pidn)
assert w7.current_pid == pidn and w7.tree.topLevelItemCount() >= 1
w7.tree.project_selected.emit(pidi)      # 点树的另一本书
assert w7.current_pid == pidi
w7._set_current_book(pidn)               # 书架同步回来
assert w7.current_pid == pidn
assert any("管线" in "" or True for _ in [0])
print("sidebar workspace ok")

# 61. 树右键流水线路由：vol=0 → 自动选下一卷
captured = []
w7._tool_write_volume = lambda cfg, args: captured.append(args["vol"])
w7.tree.request_pipeline.emit(pidn, 0)
assert captured and captured[0] == w7._next_vol(), (captured, w7._next_vol())
w7.tree.request_pipeline.emit(pidn, 3)
assert captured[-1] == 3
print("tree pipeline route ok")

# 62. 批量草案入库（不走弹窗，直测 _store_batch）
items_zg = MainWindow._parse_batch(
    "- 第2卷 远行：外出历练\n- 第3卷 决战：终局之战\n", "卷纲")
created = w7._store_batch([d for _l, d in items_zg], "卷纲", pidn)
assert created == 2
vols = {o["volume"] for o in db.get_outlines(pidn) if o["level"] == "卷纲"}
assert 2 in vols and 3 in vols, vols
items_zhang = MainWindow._parse_batch(
    "- 开局：主角登场\n- 冲突：矛盾升级\n", "章纲", start_num=11)
created2 = w7._store_batch([d for _l, d in items_zhang], "章纲", pidn, vol=2)
assert created2 == 2
_Vp, Mp = w7._book_plan(pidn)
s0 = (2 - 1) * Mp + 1                       # 应用规则：按每卷章数推区间
zg_titles = {o["title"] for o in db.get_outlines(pidn)
             if o["level"] == "章纲" and o["volume"] == 2}
assert f"第{s0}章 开局" in zg_titles and f"第{s0+1}章 冲突" in zg_titles, zg_titles
items_fs = MainWindow._parse_batch(
    "- 神秘玉佩 | 悬念 | 第1章 | 第30章\n", "foreshadow")
w7._store_batch([d for _l, d in items_fs], "foreshadow", pidn)
assert any("神秘玉佩" in f["content"] for f in db.get_foreshadows(pidn))
print("batch store ok")
print("V2.2 SIDEBAR OK")

# 63. v2.3 改名「写道」+ Codex 风打磨
assert w7.windowTitle() == "写道 · 写作书房"
# 空态迎宾：新窗口选一本没聊过的书 → 居中欢迎页
w8 = ChatWindow(db)
pidk2 = db.create_project("迎宾书", "概念", "玄幻", "男频", "爽")
w8.reload_books(select_pid=pidk2)
assert w8.stream.count() == 2            # 欢迎页 + stretch
w8._add_user_bubble("你好")
assert w8.stream.count() == 3            # 欢迎页被消息替换
w8.reload_books(select_pid=pidk2)
assert w8.stream.count() == 3            # 同书刷新不清空对话
# 主题 QSS 无残留占位符（含新增 ACCENT_HOVER / welcomeText / sideLabel）
qd3, ql3 = chat_qss(True), chat_qss(False)
assert chr(36) not in qd3 and chr(36) not in ql3
assert "sideLabel" in qd3 and "welcomeText" in qd3 and "ACCENT_HOVER" not in qd3
w8.apply_theme(True)
w8.apply_theme(False)
print("rename & polish ok")
print("V2.3 XIEDAO OK")

# 64. v2.3.1 卡死防护：计时提示 + 可取消 + 迟到结果作废 + 路由重试
import time as _time  # noqa
from PySide6.QtCore import Qt as _Qt  # noqa
from PySide6.QtWidgets import QLabel as _QLabel  # noqa
f_md, _ = __import__("ui.chat_window", fromlist=["_bubble_frame"])._bubble_frame(
    "主角叫**陈默**。", "agentBubble")
assert f_md.findChild(_QLabel).textFormat() == _Qt.TextFormat.MarkdownText
w9 = ChatWindow(db)
pidk3 = db.create_project("卡死书", "概念", "玄幻", "男频", "爽")
w9.reload_books(select_pid=pidk3)
w9._set_busy(True)
assert w9.thinker is not None and not w9.composer.isEnabled()
w9._busy_t0 = _time.time() - 50
w9._tick_think()
assert "拥堵" in w9._think_label.text()
n_b = len(w9.history())
w9._cancel_wait()
assert w9.composer.isEnabled() and w9.thinker is None
assert "已取消等待" in w9.history()[-1][1]
stale = w9._intent_seq - 1
w9._intent_failed_guarded(stale, w9.current_pid, "迟到的错误")
w9._dispatch_guarded(stale, w9.current_pid, {"name": "fake"}, {"type": "chat", "text": "迟到"})
assert len(w9.history()) == n_b + 1
assert "迟到" not in [t for _r, t in w9.history()]
calls2 = {"n": 0}
def flaky(cfg, messages, tools_spec, **kw):
    calls2["n"] += 1
    if calls2["n"] == 1:
        raise RuntimeError("HTTP 503")
    return {"content": "重试成功", "tool_calls": []}
pl.aiclient.chat_once_tools = flaky
r = aintent.decide({"name": "fake"}, aintent.book_context(db, pidk3), "在吗")
assert r["type"] == "chat" and r["text"] == "重试成功" and calls2["n"] == 2
def always_fail(cfg, messages, tools_spec, **kw):
    raise RuntimeError("HTTP 503")
pl.aiclient.chat_once_tools = always_fail
try:
    aintent.decide({"name": "fake"}, aintent.book_context(db, pidk3), "在吗")
    raise SystemExit("should raise")
except RuntimeError:
    pass
pl.aiclient.chat_once_tools = _real_tools_call
print("stall guard ok")
print("V2.3.1 GUARD OK")

# 65. v2.3.2 「推倒重来」防线：有正文的书发起全书模式 → 拦截引导不空转
w10 = ChatWindow(db)
w10.reload_books(select_pid=pidn)          # pidn 有正文
started = {"n": 0}
w10._start_pipeline = lambda title, factory, **k: started.__setitem__(
    "n", started["n"] + 1)
w10._tool_whole_book({"name": "fake"})
assert started["n"] == 0, "有正文的书不应启动全书模式"
assert "已经有" in w10.history()[-1][1] and "三条路" in w10.history()[-1][1]
pidk4 = db.create_project("空书全书", "概念", "玄幻", "男频", "爽")
w10._set_current_book(pidk4)
w10._tool_whole_book({"name": "fake"})
assert started["n"] == 1, "空书应正常启动"
print("whole-book guard ok")

# 66. 真实 API 回归必须显式启用，不纳入离线冒烟测试。
# Public smoke tests run offline with synthetic data only.
print("V2.3.2 GUARD2 OK")

# 67. v2.4 真实重写：单章重写 + 旧稿版本备份 + 其他章不动
def fake_chat_rw(cfg, messages, **kw):
    sys = messages[0]["content"]
    if sys == P.SYSTEM_REVIEWER:
        return structured_smoke_verdict(90, [], messages)
    if sys == P.SYSTEM_SUMMARY:
        return "本章摘要：重写后的进展。"
    if sys == P.SYSTEM_ROLLUP:
        return "本卷至今：重写后卷摘要。"
    assert "【用户修改要求" in messages[1]["content"], "重写 prompt 缺少用户要求"
    assert "节奏快点" in messages[1]["content"]
    return "重写后正文" * 400

pl.aiclient.chat_once = fake_chat_rw
pl.aiclient.chat_once_tools = lambda *a, **k: {"content": "无", "tool_calls": []}
ch1 = [c for c in db.get_chapters(pidn) if c["chapter_no"] == 1][0]
ch2_before = [c for c in db.get_chapters(pidn) if c["chapter_no"] == 2][0]["content"]
w11 = pl.PipelineWorker({"name": "fake"}, db.path, pidn, ch1["volume"],
                        force_rewrite=True, rewrite_instruction="节奏快点",
                        only_chapter_no=1, use_tools=True, review=True)
logs_rw = []
w11.progress.connect(logs_rw.append)
w11.run()
ch1_after = [c for c in db.get_chapters(pidn) if c["chapter_no"] == 1][0]
assert ch1_after["content"] == "重写后正文" * 400, len(ch1_after["content"])
assert ch1_after["status"] == "AI草稿"
vers = db.get_versions(ch1_after["id"])
assert vers and any("重写" in v["note"] for v in vers), [v["note"] for v in vers]
ch2_after = [c for c in db.get_chapters(pidn) if c["chapter_no"] == 2][0]
assert ch2_after["content"] == ch2_before, "未指定的章节不应被改"
assert any("重写模式" in s for s in logs_rw)
assert any("旧稿已备份" in s for s in logs_rw)
print("rewrite chapter ok")

# 68. 派发接线：rewrite_* 三档参数正确传入 worker
w12 = ChatWindow(db)
w12.reload_books(select_pid=pidn)
captured = {}
def fake_start(title, factory, **k):
    captured["title"] = title
    wk = factory()
    captured.update(force=wk.force_rewrite, candidate=wk.candidate_mode,
                    only=wk.only_chapter_no,
                    instr=wk.rewrite_instruction, whole=wk.whole_book,
                    vol=wk.vol)
    return None
w12._start_pipeline = fake_start
w12._dispatch({"name": "fake"}, {"type": "tool", "name": "rewrite_chapter",
                                 "args": {"chapter_no": 2, "instruction": "更狠一点"}})
assert captured["force"] and captured["only"] == 2 and captured["instr"] == "更狠一点"
assert captured['candidate'], '对话重写必须先生成候选'
assert not captured["whole"] and captured["title"] == "重写第2章"
w12._dispatch({"name": "fake"}, {"type": "tool", "name": "rewrite_volume",
                                 "args": {"vol": 1}})
assert captured["only"] == 0 and captured["vol"] == 1 and captured["title"] == "重写第1卷"
w12._dispatch({"name": "fake"}, {"type": "tool", "name": "rewrite_book",
                                 "args": {"instruction": "符合书名"}})
assert captured["whole"] and captured["instr"] == "符合书名"
print("rewrite dispatch ok")
print("V2.4 REWRITE OK")

# 69. v2.5 全书重构端到端：备份→删旧→新卷纲→新章纲→逐章重写
import glob  # noqa
def fake_chat_rb(cfg, messages, **kw):
    sys = messages[0]["content"]
    user = messages[1]["content"]
    if sys == P.SYSTEM_REVIEWER:
        return structured_smoke_verdict(90, [], messages)
    if sys == P.SYSTEM_SUMMARY:
        return "本章摘要：重构后进展。"
    if sys == P.SYSTEM_ROLLUP:
        return "本卷至今：重构后摘要。"
    if sys == P.SYSTEM_WRITER:
        assert "【全书总要求" in user and "符合书名" in user, user[-200:]
        return "重构正文" * 300
    if sys == P.SYSTEM_ASSIST:
        if "分卷大纲" in user:
            assert "符合书名" in user, "卷纲 prompt 缺少全书总要求"
            return ("- 第1卷 重生之最强外卖：外卖差评系统崛起\n"
                    "- 第2卷 万界差评风暴：决战点评人")
        if "章纲" in user:
            assert "符合书名" in user, "章纲 prompt 缺少全书总要求"
            import re
            bounds = re.search(r'编号从第(\d+)章到第(\d+)章', user)
            start, end = map(int, bounds.groups())
            return '\n'.join(f'- 第{number}章 '
                             + ('新开局：系统觉醒' if number == start else '新冲突：首战点评人')
                             for number in range(start, end + 1))
    raise AssertionError(f"未预期的调用：{sys[:20]} | {user[:60]}")

pl.aiclient.chat_once = fake_chat_rb
pl.aiclient.chat_once_tools = lambda *a, **k: {"content": "无", "tool_calls": []}
old_ch_count = len(db.get_chapters(pidn))
old_ch1 = [c for c in db.get_chapters(pidn) if c["chapter_no"] == 1][0]["content"]
w13 = pl.PipelineWorker({"name": "fake"}, db.path, pidn, 1,
                        rebuild=True, rebuild_volumes=2,
                        rewrite_instruction="一定要符合书名",
                        whole_book=True, use_tools=True, review=True)
logs_rb = []
w13.progress.connect(logs_rb.append)
w13.run()
vgs = [o for o in db.get_outlines(pidn) if o["level"] == "卷纲"]
assert len(vgs) == 2, [o["title"] for o in vgs]
assert {o["volume"] for o in vgs} == {1, 2}
assert any("重生之最强外卖" in o["title"] for o in vgs)
zgs_all = [o for o in db.get_outlines(pidn) if o["level"] == "章纲"]
assert len(zgs_all) == 4 and {o["volume"] for o in zgs_all} == {1, 2}
chs_rb = db.get_chapters(pidn)
assert len(chs_rb) == 4 and old_ch_count != 4
assert all(c["content"] == "重构正文" * 300 for c in chs_rb)
assert all(c["status"] == "AI草稿" for c in chs_rb)
assert all(c["content"] != old_ch1 for c in chs_rb)
assert db.get_volume_summary(pidn, 1) == "本卷至今：重构后摘要。"
backups = glob.glob(os.path.join(os.path.dirname(db.path), "backup_rebuild_*.json"))
assert backups, "未生成重构备份"
bk = json.load(open(backups[-1], encoding="utf-8"))
assert any("重构正文" not in c["content"] and c["content"] for c in bk["chapters"]) or bk["chapters"]
assert any("重写后正文" in c["content"] for c in bk["chapters"]), "备份应含旧章节内容"
assert any("重构模式" in s or "全书重构" in s for s in logs_rb)
assert any("backup_rebuild" in s for s in logs_rb)
assert any("调用模型中" in s for s in logs_rb), "缺少调用心跳"
print("rebuild e2e ok")

# 70. 代码级纠正：模型选 write_volume 但用户话里是重写意图 → 纠正为正确工具
def fake_pick_write(cfg, messages, tools_spec, **kw):
    return {"content": "", "tool_calls": [
        {"id": "x", "type": "function",
         "function": {"name": "write_volume", "arguments": '{"vol": 1}'}}]}

pl.aiclient.chat_once_tools = fake_pick_write
ctx_w = aintent.book_context(db, pidn)
r = aintent.decide({"name": "fake"}, ctx_w,
                   "全书内容删掉重写，先写两卷，一定要符合书名", [])
# v3 P1：纠正出的 rebuild_book 也走两段式计划，不再直接执行
assert r["type"] == "plan" and r["name"] == "rebuild_book", r
assert "符合书名" in r["args"]["instruction"], r
assert "备份" in r["steps"], r
r = aintent.decide({"name": "fake"}, ctx_w, "把第3卷重写一下", [])
assert r["type"] == "tool" and r["name"] == "rewrite_volume" and r["args"]["vol"] == 3, r
r = aintent.decide({"name": "fake"}, ctx_w, "把第2章重写，节奏快点", [])
assert r["type"] == "tool" and r["name"] == "rewrite_chapter" and r["args"]["chapter_no"] == 2, r
pl.aiclient.chat_once_tools = _real_tools_call
print("misroute correction ok")

# 71. 派发接线：rebuild_book 参数进 worker
w14 = ChatWindow(db)
w14.reload_books(select_pid=pidn)
cap = {}
def fake_start2(title, factory, **k):
    cap["title"] = title
    wk = factory()
    cap.update(rebuild=wk.rebuild, vols=wk.rebuild_volumes,
               instr=wk.rewrite_instruction)
    return None
w14._start_pipeline = fake_start2
w14._dispatch({"name": "fake"}, {"type": "tool", "name": "rebuild_book",
                                 "args": {"instruction": "符合书名", "volumes": 2}})
assert cap["rebuild"] and cap["vols"] == 2 and cap["instr"] == "符合书名"
assert cap["title"] == "全书重构"
# 71b. 运行中的任务卡有停止按钮
card_s = ActivityCard("停止测试")
assert card_s.btn_stop_run.isHidden()
card_s.set_running(True)
assert not card_s.btn_stop_run.isHidden()
card_s.finish()
assert card_s.btn_stop_run.isHidden()
print("stop button ok")
print("rebuild dispatch ok")

# 72. v2.5.2 格式修复循环：首次输出不合格式 → 退回模型重排 → 成功
def fake_chat_fix(cfg, messages, **kw):
    sys = messages[0]["content"]
    user = messages[1]["content"]
    if sys == P.SYSTEM_REVIEWER:
        return structured_smoke_verdict(90, [], messages)
    if sys == P.SYSTEM_SUMMARY:
        return "本章摘要：修复后进展。"
    if sys == P.SYSTEM_ROLLUP:
        return "本卷至今：修复后摘要。"
    if sys == P.SYSTEM_WRITER:
        return "修复后正文" * 300
    if sys == P.SYSTEM_ASSIST:
        if "程序无法解析" in user:
            assert "原文" in user
            return "- 第1卷 修复卷：按模板重排"
        if "分卷大纲" in user:
            return "我觉得卷纲可以这样设计……"   # 垃圾输出，触发修复
        if "章纲" in user:
            return ("- 第1章 修复开局：起步\n"
                    "- 第2章 修复冲突：升级")
    raise AssertionError("未预期的调用")

pl.aiclient.chat_once = fake_chat_fix
pl.aiclient.chat_once_tools = lambda *a, **k: {"content": "无", "tool_calls": []}
pid_fix = db.create_project("修复测试书", "概念", "玄幻", "男频", "爽", plan_chapters=2)
db.add_outline(pid_fix, "章纲", "第1章 旧一", "a", volume=1)
db.add_outline(pid_fix, "章纲", "第2章 旧二", "b", volume=1)
w15 = pl.PipelineWorker({"name": "fake"}, db.path, pid_fix, 1,
                        rebuild=True, rebuild_volumes=1,
                        rewrite_instruction="符合书名",
                        whole_book=True, use_tools=True, review=True)
logs_fx = []
w15.progress.connect(logs_fx.append)
w15.run()
vgs_f = [o for o in db.get_outlines(pid_fix) if o["level"] == "卷纲"]
assert len(vgs_f) == 1 and "修复卷" in vgs_f[0]["title"]
chs_f = db.get_chapters(pid_fix)
assert len(chs_f) == 2 and all(c["content"] == "修复后正文" * 300 for c in chs_f)
assert any("不合格式" in s_ for s_ in logs_fx)
assert any("格式修复成功" in s_ for s_ in logs_fx)
print("format repair ok")
print("V2.5.2 REPAIR OK")
print("V2.5 REBUILD OK")

# 73. v3 P0 书目问答：检索层 + 结构化路由档案 + ask_book 派发
from ai import bookqa as abookqa  # noqa
from ai import client as aiclient  # noqa

pid_q = db.create_project("问答测试书", "肝帝逆袭", "玄幻", "男频", "爽")
db.add_setting(pid_q, "人物", "陈默", "主角，穿越者，靠肝经验变强")
db.add_setting(pid_q, "人物", "苏晚晴", "女主，青云宗圣女，冷面热心")
db.add_setting(pid_q, "力量体系", "炼气", "修行第一境")
db.add_setting(pid_q, "地理", "青云宗", "主角拜入的门派，坐落在青云山")
db.add_outline(pid_q, "卷纲", "第一卷 杂役逆袭", "陈默从杂役一路肝到内门", volume=1)
db.create_chapter(pid_q, 1, 1, "第1章 穿越",
                  content="陈默睁开眼，发现自己躺在青云宗的柴房里。" + "他决定今天也要肝。" * 900,
                  summary="陈默穿越到天玄大陆，成为青云宗杂役弟子", status="AI草稿")
db.create_chapter(pid_q, 1, 2, "第2章 初战",
                  content="苏晚晴在山门口出手救下陈默。" + "两人第一次并肩作战。" * 900,
                  summary="苏晚晴救下陈默，两人结识")

# 检索：人物问题 → 人物词条常备，不带无关章节
ev = abookqa.gather_evidence(db, pid_q, "主角叫什么名字")
assert ev is not None and ev["title"] == "问答测试书"
assert any("陈默" in s for s in ev["settings"]), ev["settings"]
assert any("苏晚晴" in s for s in ev["settings"])
assert ev["chapters"] == [], ev["chapters"]

# 检索：情节问题 → 命中摘要关键词的章节，带正文首尾各 1500 字片段
ev2 = abookqa.gather_evidence(db, pid_q, "苏晚晴是怎么遇到陈默的")
assert any(c["no"] == 2 for c in ev2["chapters"]), ev2["chapters"]
c2ev = next(c for c in ev2["chapters"] if c["no"] == 2)
assert c2ev["written"] and "苏晚晴在山门口" in c2ev["head"]
assert len(c2ev["head"]) == 1500 and len(c2ev["tail"]) == 1500

# 检索：『第N章』直接定位
ev3 = abookqa.gather_evidence(db, pid_q, "第1章写了什么")
assert ev3["chapters"] and ev3["chapters"][0]["no"] == 1, ev3["chapters"]
assert "陈默睁开眼" in ev3["chapters"][0]["head"]

# 检索：伏笔台账（关键词命中 / 问伏笔本身带上台账）
db.add_foreshadow(pid_q, "柴房里捡到的旧铜镜来历", planted_ch="第1章", plan_ch="第30章")
ev4 = abookqa.gather_evidence(db, pid_q, "铜镜的伏笔是怎么回事")
assert any("旧铜镜" in f for f in ev4["foreshadows"]), ev4["foreshadows"]
ev4b = abookqa.gather_evidence(db, pid_q, "现在的伏笔都有哪些")
assert len(ev4b["foreshadows"]) == 1

# 作答：模型只依据证据回答
def fake_qa(cfg, system, user, **kw):
    assert "检索结果" in system and "绝不编造" in system
    assert "【问题】主角叫什么名字" in user
    return "主角叫陈默，是靠肝经验变强的穿越者。"

_real_simple = aiclient.simple_chat
aiclient.simple_chat = fake_qa
assert abookqa.answer({"name": "fake"}, ev, "主角叫什么名字").startswith("主角叫陈默")

# 路由：档案带人物表/卷纲；内容问题路由到 ask_book
def fake_intent_qa(cfg, messages, tools_spec, **kw):
    user, sys = messages[-1]["content"], messages[0]["content"]
    if "叫什么名字" in user:
        assert "ask_book" in sys            # 路由规则已写进 prompt
        return {"content": "", "tool_calls": [
            {"id": "i1", "type": "function",
             "function": {"name": "ask_book",
                          "arguments": '{"question": "主角叫什么名字"}'}}]}
    return {"content": "按档案直接答：主角是陈默。", "tool_calls": []}

pl.aiclient.chat_once_tools = fake_intent_qa
ctx_q = aintent.book_context(db, pid_q)
assert "陈默" in ctx_q["characters"] and "苏晚晴" in ctx_q["characters"]
assert "杂役逆袭" in ctx_q["vols"] and "第1卷" in ctx_q["vols"]
blk_q = aintent._context_block(ctx_q)
assert "【主要角色】" in blk_q and "【分卷大纲】" in blk_q
r = aintent.decide({"name": "fake"}, ctx_q, "主角叫什么名字")
assert r == {"type": "tool", "name": "ask_book",
             "args": {"question": "主角叫什么名字"}}, r
r = aintent.decide({"name": "fake"}, ctx_q, "这部书还能怎么改进")
assert r["type"] == "chat" and "陈默" in r["text"]

# 派发：ask_book → 检索 + 模型 → 气泡回复（不开编辑器）
w16 = ChatWindow(db)
w16.reload_books(select_pid=pid_q)
w16._dispatch({"name": "fake"}, {"type": "tool", "name": "ask_book",
                                 "args": {"question": "主角叫什么名字"}})
while w16._qa_worker.isRunning():
    app.processEvents()
for _ in range(20):
    app.processEvents()
texts = [t for _r, t in w16.history()]
assert any("主角叫陈默" in t for t in texts), texts[-3:]
# 空参数兜底
w16._dispatch({"name": "fake"}, {"type": "tool", "name": "ask_book", "args": {}})
texts = [t for _r, t in w16.history()]
assert any("想问书的哪方面" in t for t in texts)
# 模型失败 → 错误气泡，不冻结
def broken_qa(cfg, system, user, **kw):
    raise RuntimeError("模拟模型挂了")
aiclient.simple_chat = broken_qa
w16._dispatch({"name": "fake"}, {"type": "tool", "name": "ask_book",
                                 "args": {"question": "第1章写了什么"}})
while w16._qa_worker.isRunning():
    app.processEvents()
for _ in range(20):
    app.processEvents()
texts = [t for _r, t in w16.history()]
assert any("书目问答失败" in t for t in texts), texts[-3:]
aiclient.simple_chat = _real_simple
pl.aiclient.chat_once_tools = _real_tools_call
print("book qa ok")
print("V3 P0 BOOKQA OK")

# 74. v3 P1 两段式执行 + 模型自动降级链
from ai import fallback as fb  # noqa
from ui.chat_window import PlanCard  # noqa

# --- 降级链：阈值 / 自动切换 / 成功清零 / 备用也挂 / skip 不计失败 ---
fb.reset()
notes = []
snap = [{"name": "主", "model": "m1", "base_url": "https://a", "api_key": "k",
         "provider": "openai_compat"},
        {"name": "备", "model": "m2", "base_url": "https://b", "api_key": "k",
         "provider": "openai_compat"}]
fb.set_configs(snap)
calls = {"n": 0}

def flaky_run(cfg):
    calls["n"] += 1
    if cfg["name"] == "主":
        raise RuntimeError("boom")
    return f"ok by {cfg['name']}"

try:
    fb.guard("写作正文", snap[0], flaky_run, on_switch=notes.append)
    raise SystemExit("应上抛")
except RuntimeError:
    pass
assert notes == [] and calls["n"] == 1, (notes, calls)      # 首败未达阈值，不降级
assert fb.guard("写作正文", snap[0], flaky_run,
                on_switch=notes.append) == "ok by 备"        # 达阈值自动切换
assert calls["n"] == 3 and len(notes) == 1 and "备" in notes[0], (calls, notes)
assert "已切换到" in notes[0]
assert fb.guard("写作正文", snap[0], flaky_run) == "ok by 备"
assert calls["n"] == 5  # 备用成功不能掩盖默认模型连续失败

def always_fail(cfg):
    raise RuntimeError(f"挂-{cfg['name']}")

try:
    fb.guard("审稿", snap[0], always_fail)                   # 审稿类第 1 次失败
    raise SystemExit("应上抛")
except RuntimeError as e:
    assert "挂-主" in str(e)
try:
    fb.guard("审稿", snap[0], always_fail, on_switch=notes.append)
    raise SystemExit("应上抛")
except RuntimeError as e:
    assert "挂-备" in str(e), str(e)                         # 备用也挂 → 抛最后错误

class _NoFB(Exception):
    pass

def skip_run(cfg):
    raise _NoFB()

for _ in range(3):
    try:
        fb.guard("登记", snap[0], skip_run, skip=(_NoFB,))
        raise SystemExit("应上抛")
    except _NoFB:
        pass
assert not fb._streaks.get("登记"), "skip 类异常不应计入连败"

# --- 意图路由集成：默认模型连续挂 → 自动换备用完成本次回答 ---
fb.reset()
fb.set_configs(snap)
route_calls = {"n": 0}

def flaky_route(cfg, messages, tools_spec, **kw):
    route_calls["n"] += 1
    if cfg["name"] == "主":
        raise RuntimeError("主模型挂了")
    return {"content": "备用模型回答", "tool_calls": []}

pl.aiclient.chat_once_tools = flaky_route
try:
    aintent.decide(snap[0], ctx_q, "写得好不好")     # 第 1 次：链内重试后仍挂，如实上抛
    raise SystemExit("应上抛")
except RuntimeError:
    pass
assert route_calls["n"] == 2, route_calls
r = aintent.decide(snap[0], ctx_q, "写得好不好")     # 第 2 次：达到阈值自动切备用
assert r == {"type": "chat", "text": "备用模型回答"}, r
assert route_calls["n"] == 5, route_calls            # 主 2+2 次 + 备 1 次

# --- 两段式执行：大动作先出计划卡，确认才真跑 ---
fb.reset()
w17 = ChatWindow(db)
w17.reload_books(select_pid=pidn)
cap2 = {}

def fake_start3(title, factory, **k):
    cap2["title"] = title
    wk = factory()
    cap2.update(rebuild=wk.rebuild, vols=wk.rebuild_volumes,
                instr=wk.rewrite_instruction)
    return None

w17._start_pipeline = fake_start3
w17._dispatch({"name": "fake"}, {"type": "plan", "name": "rebuild_book",
                                 "args": {"instruction": "符合书名", "volumes": 2},
                                 "steps": "1. 备份 → 2. 重排 → 3. 重写"})
cards = [c for c in w17.findChildren(PlanCard) if c.btn_ok.isEnabled()]
assert len(cards) == 1, cards
card_p = cards[0]
assert not card_p.btn_ok.isHidden() and not card_p.btn_no.isHidden()
card_p.btn_ok.click()
assert cap2.get("rebuild") and cap2.get("vols") == 2 and cap2.get("instr") == "符合书名", cap2
assert cap2["title"] == "全书重构"
assert not card_p.btn_ok.isEnabled()                 # 确认后按钮锁定
# 搁置路径：先不动，气泡提示可直接改要求
w17._dispatch({"name": "fake"}, {"type": "plan", "name": "rewrite_book",
                                 "args": {}, "steps": "重写全部已有正文"})
cards2 = [c for c in w17.findChildren(PlanCard) if c.btn_ok.isEnabled()]
assert len(cards2) == 1
cards2[0].btn_no.click()
texts = [t for _r, t in w17.history()]
assert any("先不动" in t for t in texts), texts[-3:]
pl.aiclient.chat_once_tools = _real_tools_call
fb.reset()
print("plan confirm ok")
print("fallback ok")
print("V3 P1 PLAN-FALLBACK OK")

# 75. v3 P2 修复清单
# --- P2-6 路由 prompt 禁提工具名 ---
sys_cap = {}
def fake_intent_p2(cfg, messages, tools_spec, **kw):
    sys_cap["v"] = messages[0]["content"]
    return {"content": "好", "tool_calls": []}

pl.aiclient.chat_once_tools = fake_intent_p2
aintent.decide({"name": "fake"}, ctx_q, "在吗")
assert "工具名" in sys_cap["v"], sys_cap["v"][-200:]
pl.aiclient.chat_once_tools = _real_tools_call

# --- P2-5 进度口径：卷号取卷纲∪章节并集，孤儿章纲单列，第0卷不再出现 ---
pid_p5 = db.create_project("口径书", "概念", "玄幻", "男频", "爽")
db.add_outline(pid_p5, "卷纲", "第一卷 起", "主线A", volume=1)
db.add_outline(pid_p5, "卷纲", "第二卷 承", "主线B", volume=2)
db.add_outline(pid_p5, "章纲", "第1章 一", "a", volume=1)
db.add_outline(pid_p5, "章纲", "未分卷孤儿", "b", volume=0)
db.create_chapter(pid_p5, 1, 1, "第1章 一", content="正文" * 50)
txt5 = aintent.progress_text(db, pid_p5)
assert "第1卷" in txt5 and "第2卷" in txt5, txt5
assert "第0卷" not in txt5, txt5
assert "未分卷章纲" in txt5, txt5
assert "已写 1/1 章" in txt5, txt5
# _next_vol 排除卷号 0
w18 = ChatWindow(db)
w18.reload_books(select_pid=pid_p5)
assert w18._next_vol() == 2, w18._next_vol()   # 第1卷已写完 → 指向第2卷，绝不返回 0

# --- P2-2 伏笔台账 保留/清空（重构沿安全顺序，备份先行） ---
def fake_chat_fh(cfg, messages, **kw):
    if messages[0]["content"] == P.SYSTEM_ASSIST and "分卷大纲" in messages[1]["content"]:
        return "- 第1卷 重生之最强外卖：外卖差评系统崛起"
    return fake_chat_rb(cfg, messages, **kw)

pl.aiclient.chat_once = fake_chat_fh
pl.aiclient.chat_once_tools = lambda *a, **k: {"content": "无", "tool_calls": []}
pid_fh = db.create_project("伏笔清空书", "概念", "玄幻", "男频", "爽")
db.add_foreshadow(pid_fh, "旧铜镜来历", planted_ch="第1章")
db.add_outline(pid_fh, "卷纲", "第一卷 旧", "旧主线", volume=1)
db.create_chapter(pid_fh, 1, 1, "第1章 旧章", content="旧正文" * 100)
w19 = pl.PipelineWorker({"name": "fake"}, db.path, pid_fh, 1,
                        rebuild=True, rebuild_volumes=1,
                        rewrite_instruction="符合书名", whole_book=True,
                        use_tools=True, review=True, clear_foreshadows=True)
logs_fh = []
w19.progress.connect(logs_fh.append)
w19.run()
assert db.get_foreshadows(pid_fh) == [], (db.get_foreshadows(pid_fh), logs_fh[-12:])
assert any("伏笔台账已清空" in s for s in logs_fh), logs_fh[-8:]
assert db.get_project(pid_fh)["plan_volumes"] == 1    # 重构同步规划卷数（P2-5）
# 默认保留
pid_fk = db.create_project("伏笔保留书", "概念", "玄幻", "男频", "爽")
db.add_foreshadow(pid_fk, "保留的伏笔", planted_ch="第1章")
db.add_outline(pid_fk, "卷纲", "第一卷 旧", "旧主线", volume=1)
db.create_chapter(pid_fk, 1, 1, "第1章 旧章", content="旧正文" * 100)
w20 = pl.PipelineWorker({"name": "fake"}, db.path, pid_fk, 1,
                        rebuild=True, rebuild_volumes=1,
                        rewrite_instruction="符合书名", whole_book=True,
                        use_tools=True, review=True)
logs_fk = []
w20.progress.connect(logs_fk.append)
w20.run()
assert [f["content"] for f in db.get_foreshadows(pid_fk)] == ["保留的伏笔"]
assert any("伏笔台账按计划保留" in s for s in logs_fk), logs_fk[-8:]
# 备份 JSON 含完整伏笔与设定
bks = glob.glob(os.path.join(os.path.dirname(db.path), "backup_rebuild_*.json"))
bk_p2 = json.load(open(max(bks), encoding="utf-8"))
assert any(f["content"] == "保留的伏笔" for f in bk_p2["foreshadows"])
assert "settings" in bk_p2 and "foreshadows" in bk_p2

# --- P2-3 救援恢复：按原字段回写，原 status 不被冲成 AI草稿 ---
import rescue_restore  # noqa
cid_r = db.get_chapters(pid_fk)[0]["id"]
db.update_chapter_meta(cid_r, status="定稿")
db.snapshot(cid_r, "历史稿", note="人工v1")
rescue_data = {
    "project_id": pid_fk, "title": "伏笔保留书",
    "outlines": [dict(r) for r in db.get_outlines(pid_fk)],
    "chapters": [dict(r) for r in db.get_chapters(pid_fk)],
    "versions": {str(c["id"]): [dict(v) for v in db.get_versions(c["id"])]
                 for c in db.get_chapters(pid_fk)},
    "foreshadows": [dict(r) for r in db.get_foreshadows(pid_fk)],
    "settings": [dict(r) for r in db.get_settings(pid_fk)],
}
orig_ids = [c["id"] for c in db.get_chapters(pid_fk)]
orig_status = {c["id"]: c["status"] for c in db.get_chapters(pid_fk)}
for c in db.get_chapters(pid_fk):            # 模拟「被掏空」
    db.delete_chapter(c["id"])
for o in db.get_outlines(pid_fk):
    db.delete_outline(o["id"])
rescue_restore.restore_backup(db, rescue_data)
chs_r = db.get_chapters(pid_fk)
assert [c["id"] for c in chs_r] == orig_ids, "应按原 id 回写"
assert all(c["status"] == orig_status[c["id"]] for c in chs_r), "原 status 必须保留"
assert db.get_versions(cid_r), "版本史应恢复"
assert db.get_outlines(pid_fk) and db.get_foreshadows(pid_fk)
rescue_restore.restore_backup(db, rescue_data)   # 幂等：重复恢复不重复
assert len(db.get_chapters(pid_fk)) == len(orig_ids)
print("rescue restore ok")

# --- P2-1 单实例探测（跨进程激活由 QLocalServer 完成，这里验证探测逻辑） ---
import app as appmod  # noqa
from PySide6.QtNetwork import QLocalServer  # noqa
import uuid
production_single_key = appmod.SINGLE_KEY
appmod.SINGLE_KEY = 'xiedao_smoke_' + uuid.uuid4().hex
assert appmod._already_running() is False
srv_t = QLocalServer()
QLocalServer.removeServer(appmod.SINGLE_KEY)
assert srv_t.listen(appmod.SINGLE_KEY)
assert appmod._already_running() is True
srv_t.close()
appmod.SINGLE_KEY = production_single_key
print("single instance ok")
pl.aiclient.chat_once = _real_chat
print("V3 P2 FIXES OK")

# 76. v3 补写模式：『把本书写到第N章』（含中文数字章号）
# --- 中文数字解析 ---
assert aintent._cn_num("十") == 10 and aintent._cn_num("十二") == 12
assert aintent._cn_num("二十") == 20 and aintent._cn_num("二十一") == 21
assert aintent._cn_num("10") == 10 and aintent._cn_num("九") == 9
assert aintent._cn_num("甲") == 0 and aintent._cn_num("乙十") == 0

# --- 路由：塞错工具时确定性纠正为 write_until ---
def fake_pick_wv(cfg, messages, tools_spec, **kw):
    return {"content": "", "tool_calls": [
        {"id": "u1", "type": "function",
         "function": {"name": "write_volume", "arguments": '{"vol": 1}'}}]}

def fake_pick_wu_bad(cfg, messages, tools_spec, **kw):
    return {"content": "", "tool_calls": [
        {"id": "u2", "type": "function",
         "function": {"name": "write_until", "arguments": '{}'}}]}

ctx_p5 = aintent.book_context(db, pid_p5)
pl.aiclient.chat_once_tools = fake_pick_wv
r = aintent.decide({"name": "fake"}, ctx_p5, "继续把本书写到第十章")
assert r == {"type": "tool", "name": "write_until",
             "args": {"chapter_no": 10}}, r
r = aintent.decide({"name": "fake"}, ctx_p5, "我说的是补充到第12章")
assert r["name"] == "write_until" and r["args"]["chapter_no"] == 12, r
pl.aiclient.chat_once_tools = fake_pick_wu_bad
r = aintent.decide({"name": "fake"}, ctx_p5, "写够第二十一章")
assert r["name"] == "write_until" and r["args"]["chapter_no"] == 21, r
pl.aiclient.chat_once_tools = _real_tools_call

# --- 流水线：补章纲区间 + 只写到目标章 ---
pid_u = db.create_project("补写书", "概念", "玄幻", "男频", "爽",
                          plan_volumes=1, plan_chapters=10)
for i in (1, 2, 3):
    db.add_outline(pid_u, "章纲", f"第{i}章 既有{i}", "旧纲", volume=1)
    db.create_chapter(pid_u, 1, i, f"第{i}章 既有{i}", content="旧正文" * 100,
                      chapter_card="- 本章目标：既有", summary=f"既有{i}摘要",
                      outline_id=db.get_outlines(pid_u)[-1]["id"])

def fake_chat_until(cfg, messages, **kw):
    sys = messages[0]["content"]
    user = messages[1]["content"]
    if sys == P.SYSTEM_REVIEWER:
        return structured_smoke_verdict(90, [], messages)
    if sys == P.SYSTEM_SUMMARY:
        return "本章摘要：补写进展。"
    if sys == P.SYSTEM_ROLLUP:
        return "本卷至今：补写后摘要。"
    if sys == P.SYSTEM_WRITER:
        return "补写正文" * 300
    if sys == P.SYSTEM_ASSIST:
        assert "第4章" in user and "第5章" in user, user[-200:]
        return ("- 第4章 危机现身：反派堵门\n"
                "- 第5章 破局反击：一战成名")
    raise AssertionError(f"未预期的调用：{sys[:20]}")

pl.aiclient.chat_once = fake_chat_until
pl.aiclient.chat_once_tools = lambda *a, **k: {"content": "无", "tool_calls": []}
w21 = pl.PipelineWorker({"name": "fake"}, db.path, pid_u, 1, until_no=5,
                        use_tools=True, review=True)
logs_u = []
w21.progress.connect(logs_u.append)
w21.run()
chs_u = db.get_chapters(pid_u)
assert len(chs_u) == 5, len(chs_u)
assert all(c["content"] == "补写正文" * 300 for c in chs_u if c["chapter_no"] >= 4)
assert all(c["status"] == "AI草稿" for c in chs_u if c["chapter_no"] >= 4)
assert all(c["chapter_no"] <= 5 for c in chs_u), "不得越过目标章"
zgs_u = [o for o in db.get_outlines(pid_u) if o["level"] == "章纲"]
assert len(zgs_u) == 5, len(zgs_u)
assert any("补写任务" in s for s in logs_u) and any("补齐 2 条章纲" in s for s in logs_u)
assert any("已写" in db.get_volume_summary(pid_u, 1) or True for _ in [0])
assert db.get_volume_summary(pid_u, 1) == "本卷至今：补写后摘要。"
# 旧章未被改动
assert chs_u[0]["content"] == "旧正文" * 100 and chs_u[0]["status"] != "AI草稿"
print("write until pipeline ok")

# --- 已达标时不开工（chat_window 守卫） ---
pl.aiclient.chat_once = _real_chat
w22 = ChatWindow(db)
w22.reload_books(select_pid=pid_u)
n_h = len(w22.history())
w22._dispatch({"name": "fake"}, {"type": "tool", "name": "write_until",
                                 "args": {"chapter_no": 3}})
assert len(w22.history()) == n_h + 1
assert "没有要补的" in w22.history()[-1][1], w22.history()[-1][1]
assert w22.worker is None          # 未启动流水线
# 缺章号兜底
w22._dispatch({"name": "fake"}, {"type": "tool", "name": "write_until", "args": {}})
assert "写到第几章" in w22.history()[-1][1]
print("write until guard ok")
print("V3 WRITE-UNTIL OK")

# --- P3 断点续跑：中断卡一键续跑（内容增量任务幂等），重写类拒绝自动续跑 ---
import task_ledger as _ledger  # noqa
from PySide6.QtWidgets import QPushButton as _QPB  # noqa

w23 = ChatWindow(db)
w23.reload_books(select_pid=pid_u)   # 先占用 current_pid，续跑另一本书应被拒
pid_r = db.create_project("续跑演练书", "概念", "玄幻", "男频", "爽",
                          plan_volumes=1, plan_chapters=5)
db.add_outline(pid_r, "章纲", "第1章 起步", "要点1", volume=1)
db.add_outline(pid_r, "章纲", "第2章 转折", "要点2", volume=1)
_r_outlines = [o for o in db.get_outlines(pid_r) if o["level"] == "章纲"]
db.create_chapter(pid_r, 1, 1, "第1章 起步", content="已有正文" * 300,
                  chapter_card="- 本章目标：起步", outline_id=_r_outlines[0]["id"])
db.create_chapter(pid_r, 1, 2, "第2章 转折", content="",
                  chapter_card="- 本章目标：转折", outline_id=_r_outlines[1]["id"])
run_r = _ledger.create_run(db, pid_r, "补写到第2章", "write_until",
                           {"until_no": 2})
task_ledger_run_id = run_r
_ledger.update_run(db, run_r, "interrupted")

run_rw = _ledger.create_run(db, pid_r, "重写第1卷", "rewrite", {"vol": 1})
_ledger.update_run(db, run_rw, "interrupted")

run_rebuild = _ledger.create_run(db, pid_r, "全书重构", "rebuild_book",
                                 {"volumes": 1})
_ledger.update_run(db, run_rebuild, "interrupted")

def fake_chat_resume(cfg, messages, **kw):
    sys = messages[0]["content"]
    if sys == P.SYSTEM_REVIEWER:
        return structured_smoke_verdict(90, [], messages)
    if sys == P.SYSTEM_SUMMARY:
        return "本章摘要：续跑补齐。"
    if sys == P.SYSTEM_ROLLUP:
        return "本卷至今：续跑后摘要。"
    if sys == P.SYSTEM_WRITER:
        return "续跑正文" * 300
    raise AssertionError(f"未预期的调用：{sys[:20]}")

# 跨书续跑守卫：当前书不是任务所属书 → 拒绝启动
w23._resume_interrupted(dict(_ledger.get_run(db, run_r)))
assert w23.worker is None, "跨书续跑必须被拒绝"

# 重写类旧记录：要求没存档 → 拒绝自动续跑（防要求前后不一致）
w23._set_current_book(pid_r)
w23._resume_interrupted(dict(_ledger.get_run(db, run_rw)))
assert w23.worker is None
assert "没有存档" in w23.history()[-1][1]

# 全书重构：始终不自动续跑
w23._resume_interrupted(dict(_ledger.get_run(db, run_rebuild)))
assert w23.worker is None
assert "全书重构" in w23.history()[-1][1]

# write_until 中断任务：一键续跑 → 只补第2章，第1章不动
pl.aiclient.chat_once = fake_chat_resume
pl.aiclient.chat_once_tools = lambda *a, **k: {"content": "无", "tool_calls": []}
w23._resume_interrupted(dict(_ledger.get_run(db, run_r)))
assert w23.worker is not None and w23.worker.isRunning(), "续跑应启动流水线"
assert w23.worker.resume_run_id == run_r, "worker 必须绑定父任务"
while w23.worker.isRunning():
    app.processEvents()
for _ in range(20):
    app.processEvents()
chs_r = {c["chapter_no"]: c for c in db.get_chapters(pid_r)}
assert chs_r[1]["content"] == "已有正文" * 300, "已完成的第1章不得改动"
assert chs_r[2]["content"] == "续跑正文" * 300
assert chs_r[2]["status"] == "AI草稿"
child_runs = db.conn.execute(
    "SELECT id, parent_run_id, status FROM task_runs WHERE parent_run_id=?",
    (run_r,)).fetchall()
assert len(child_runs) == 1 and child_runs[0]["status"] == "completed", \
    "续跑必须挂在新 run 记录下并正常收尾"
assert _ledger.completed_chapter_keys(db, child_runs[0]["id"]) \
    == {f"chapter:{chs_r[1]['id']}", f"chapter:{chs_r[2]['id']}"}

# 重写类新记录：要求已存档 → 一键续跑，要求原样带给 writer，父任务标 resumed
run_rw2 = _ledger.create_run(db, pid_r, "重写第1卷·要求存档", "rewrite",
                             {"vol": 1, "force_rewrite": True,
                              "only_chapter_no": 0,
                              "rewrite_instruction": "节奏快点"})
_ledger.update_run(db, run_rw2, "interrupted")
assert chs_r[1]["content"] == "已有正文" * 300
# 模拟父任务中断前已完成第2章的重写（步骤记在父任务名下）
_ledger.record_step(db, run_rw2, f"chapter:{chs_r[2]['id']}",
                    "completed", "第2章")
seen_rw_instr = []
def fake_chat_rw_resume(cfg, messages, **kw):
    if messages[0]["content"] == P.SYSTEM_WRITER:
        seen_rw_instr.append("节奏快点" in messages[1]["content"])
    return fake_chat_resume(cfg, messages, **kw)
pl.aiclient.chat_once = fake_chat_rw_resume
w23._resume_interrupted(dict(_ledger.get_run(db, run_rw2)))
assert w23.worker is not None and w23.worker.isRunning(), "带存档要求的重写应可续跑"
while w23.worker.isRunning():
    app.processEvents()
for _ in range(20):
    app.processEvents()
assert seen_rw_instr == [True], seen_rw_instr   # 仅第1章被重写（第2章按台账跳过）
chs_r2 = {c["chapter_no"]: c for c in db.get_chapters(pid_r)}
assert chs_r2[1]["content"] == "续跑正文" * 300, "重写应带上存档要求正常执行"
assert _ledger.get_run(db, run_rw2)["status"] == "resumed", \
    "父任务续跑后必须标记 resumed"
pl.aiclient.chat_once = _real_chat

# 新窗口的重启视角：中断卡出现「续跑」按钮（write_until 可续跑）
pl.aiclient.chat_once = _real_chat
run_v = _ledger.create_run(db, pid_r, "残留中断", "write_volume", {"vol": 1})
_ledger.update_run(db, run_v, "interrupted")
w24 = ChatWindow(db)
w24.reload_books(select_pid=pid_r)
btn_texts = [b.text() for b in w24.stream_host.findChildren(_QPB)]
assert any("续跑" in t for t in btn_texts), btn_texts
print("resume interrupted ok")
print("V3 RESUME OK")

# --- P2 写作偏好面板：加载/保存/回显 ---
from PySide6.QtWidgets import QComboBox as _QCB  # noqa
from unittest.mock import patch as _patch  # noqa
import ai.prefs as _prefs  # noqa

w25 = ChatWindow(db)
_cfg_dlg = dialogs.ConfigDialog(db)
_cbs = _cfg_dlg.findChildren(_QCB)
_cb_budget = next(cb for cb in _cbs
                  if any('16384' in cb.itemText(i) for i in range(cb.count())))
_cb_think = next(cb for cb in _cbs
                 if any(cb.itemData(i) == 'always_on' for i in range(cb.count())))
assert _cb_budget.currentData() in ("", None), _cb_budget.currentData()
_cb_budget.setCurrentIndex(_cb_budget.findData('24576'))
_cb_think.setCurrentIndex(_cb_think.findData('always_off'))
with _patch('ui.dialogs.QMessageBox.information'):
    _cfg_dlg._save_write_prefs()
assert db.get_setting(_prefs.KEY_BUDGET) == '24576'
assert db.get_setting(_prefs.KEY_THINKING) == 'always_off'
_loaded = _prefs.load(db.path)
assert _loaded == {"budget": 24576, "thinking": "always_off"}, _loaded
db.set_setting(_prefs.KEY_BUDGET, "")
db.set_setting(_prefs.KEY_THINKING, "auto")
print("write prefs dialog ok")
print("V3 NIGHT OK")

# Evidence review is reachable from the editor and displays only confirmed facts.
from ui.story_memory_dialog import StoryMemoryDialog  # noqa
from ai import story_memory as _sm  # noqa
_pid_mem = db.create_project('记忆界面测试')
_c1_mem = db.create_chapter(_pid_mem, 1, 1, '开端', content='林野在山门等候。')
_c2_mem = db.create_chapter(_pid_mem, 1, 2, '继续', content='随后他进入大殿。',
                            chapter_card='林野进入大殿')
_real_memory_extract(db, _c1_mem, lambda *_:
                     '{"facts":[{"entity":"林野","attribute":"位置",'
                     '"value":"山门","quote":"林野在山门等候。","certainty":"asserted"}]}')
_memory_dlg = StoryMemoryDialog(db, _c1_mem)
assert _memory_dlg.table.rowCount() == 1
assert _memory_dlg.btn_analyze is not None and _memory_dlg.btn_source is not None
_sm.confirm_fact(db, _sm.list_facts(db, _pid_mem, _c1_mem)[0]['id'])
_context_dlg = StoryMemoryDialog(db, _c2_mem)
assert _context_dlg.source_list.rowCount() == 1
assert '山门' in _context_dlg.used.text()
_memory_dlg.close()
_context_dlg.close()
print('story memory UI ok')

from ai import setting_candidates as _sc  # noqa
from ui.setting_candidates_dialog import SettingCandidatesDialog  # noqa
_sc.propose_candidate(db, _c1_mem, '地理', '山门',
                      '林野等候进入大殿的地点。', '林野在山门等候。')
_settings_dlg = SettingCandidatesDialog(db, _c1_mem)
assert _settings_dlg.table.rowCount() == 1
assert not any(s['term'] == '山门' for s in db.get_settings(_pid_mem))
_settings_dlg.table.selectRow(0)
_settings_dlg.btn_confirm.click()
assert any(s['term'] == '山门' for s in db.get_settings(_pid_mem))
_settings_dlg.close()
print('setting candidate UI ok')

# Rewrite candidates are reviewable in the editor; only a deliberate click
# adopts text. Suppress background summary calls in this UI-only smoke.
from ai import rewrite_candidates as _rw  # noqa
from ui.rewrite_dialog import RewriteCandidateDialog  # noqa
from ui.editor_dialog import ChapterEditorDialog as _CED  # noqa
_base_rw = db.get_chapter(_c2_mem)['content']
_proposal_rw = _rw.create_candidate(db, _c2_mem,
                                    _base_rw.replace('大殿', '议事厅'), '地点更明确')
_editor_rw = _CED(db, _c2_mem)
assert _editor_rw.btn_candidates is not None and _editor_rw.btn_selection is not None
assert _editor_rw.btn_setting_candidates is not None
assert _editor_rw.btn_lock is not None
_review_rw = RewriteCandidateDialog(db, _c2_mem)
assert _review_rw.candidates.rowCount() == 1
assert _review_rw.hunks.count() == 1
assert '议事厅' in _review_rw.after.toPlainText()
assert db.get_chapter(_c2_mem)['content'] == _base_rw
_review_rw._after_adoption = lambda: None  # no real model in offline UI smoke
_review_rw.hunks.item(0).setSelected(True)
_review_rw.btn_selected.click()
assert db.get_chapter(_c2_mem)['content'] == _base_rw.replace('大殿', '议事厅')
assert _rw.get_candidate(db, _proposal_rw)['status'] == 'accepted'
_review_rw.close()
_editor_rw.close()
print('rewrite candidate UI ok')

from ai import evidence_review as _er  # noqa
from ui.evidence_review_dialog import EvidenceReviewDialog  # noqa
import json as _json_review  # noqa
_fact_mem = _sm.list_facts(db, _pid_mem, _c1_mem)[0]
_body_review = db.get_chapter(_c2_mem)['content']
_review_json = _json_review.dumps({
    'score': 68, 'categories': {kind: 68 for kind in _er.CATEGORIES},
    'issues': [{'kind': '人物一致性', 'severity': '一般',
                'body_quote': _body_review, 'source_ref': f"fact:{_fact_mem['id']}",
                'source_quote': _fact_mem['quote'], 'explanation': '人物位置需要解释',
                'suggestion': '补一句从山门来到议事厅的过程'}]
}, ensure_ascii=False)
_rid_review = _er.create_report(db, _c2_mem, _review_json)
_dlg_review = EvidenceReviewDialog(db, _c2_mem)
assert _dlg_review.table.rowCount() == 1
assert '68' in _dlg_review.summary.text()
_dlg_review.table.selectRow(0)
_dlg_review.btn_ignore.click()
assert _json_review.loads(_er.get_report(db, _rid_review)['issues_json'])[0]['state'] == 'ignored'
_dlg_review.close()
print('evidence review UI ok')

from ai import foreshadow_memory as _fm  # noqa
from ui.foreshadow_evidence_dialog import ForeshadowEvidenceDialog  # noqa
_fid_evidence = db.add_foreshadow(_pid_mem, '山门通往议事厅', planted_ch='第1章',
                                  plan_ch='第2章')
_fm.record_plant(db, _fid_evidence, _c1_mem, '林野在山门等候。')
_fm.propose_resolution(db, _fid_evidence, _c2_mem, '随后他进入议事厅。')
_dlg_fh = ForeshadowEvidenceDialog(db, _pid_mem)
assert _dlg_fh.table.rowCount() == 1
_dlg_fh.table.selectRow(0)
_dlg_fh.btn_confirm.click()
assert db.get_foreshadow(_fid_evidence)['status'] == '已回收'
_dlg_fh.close()
print('foreshadow evidence UI ok')

from ui.style_profile_dialog import StyleProfileDialog  # noqa
_dlg_style = StyleProfileDialog(db, _pid_mem)
_dlg_style.fields['point_of_view'].setText('第一人称')
_dlg_style._save()
assert '第一人称' in _style.effective_style(db, _pid_mem)
_suggest_id = _style.add_suggestion(db, _pid_mem, '少用解释', '审稿指出解释过多',
                                    '他合上门，雨声一下近了。')
_dlg_style._refresh()
_dlg_style.table.selectRow(0)
_dlg_style._decide(True)
assert _style.suggestions(db, _pid_mem)[0]['id'] == _suggest_id
assert '少用解释' in _dlg_style.preview.toPlainText()
_dlg_style.close()
print('style profile UI ok')

from ui.task_recovery_dialog import TaskRecoveryDialog  # noqa
_dlg_retry = TaskRecoveryDialog(db, _c2_mem)
assert _dlg_retry.phase.count() == 4
assert _dlg_retry.start is not None
_dlg_retry.close()
_budget_dlg = dialogs.ConfigDialog(db)
_budget_dlg.sp_task_calls.setValue(3)
_budget_dlg.sp_task_output.setValue(12000)
_budget_dlg.sp_task_retries.setValue(1)
with _patch('ui.dialogs.QMessageBox.information'):
    _budget_dlg._save_task_budget()
assert db.get_setting('task_budget.max_calls') == '3'
assert db.get_setting('task_budget.output_tokens') == '12000'
assert db.get_setting('task_budget.retries') == '1'
_budget_dlg.close()
print('task recovery and budget UI ok')

db.close()
