"""Shared task creation and recovery, with credential-free execution snapshots."""
import copy
import inspect
import json

import task_ledger as ledger
from ai import prefs, story_memory, style_profile
from ai.usage_budget import UsageBudget


SNAPSHOT_VERSION = 1
OPTION_NAMES = (
    'vol', 'auto_outline', 'skip_existing', 'gen_card', 'min_chars',
    'use_tools', 'review', 'min_score', 'distill_style', 'whole_book',
    'force_rewrite', 'rewrite_instruction', 'only_chapter_no', 'rebuild',
    'rebuild_volumes', 'clear_foreshadows', 'until_no', 'candidate_mode')
INTEGER_OPTIONS = ('vol', 'min_chars', 'min_score', 'only_chapter_no',
                   'rebuild_volumes', 'until_no')
MODEL_FIELDS = ('id', 'name', 'provider', 'base_url', 'model')
USAGE_FIELDS = ('calls', 'prompt_tokens', 'completion_tokens', 'reasoning_tokens',
                'unknown_calls', 'reserved_completion')


class TaskRecoveryError(ValueError):
    pass


class TaskBudgetExhausted(TaskRecoveryError):
    pass


def mode_for(options):
    if options['rebuild']:
        return 'rebuild_book'
    if options['until_no']:
        return 'write_until'
    if options['force_rewrite']:
        return 'rewrite_book' if options['whole_book'] else 'rewrite'
    return 'whole_book' if options['whole_book'] else 'write_volume'


def validate_options(values, *, complete=False):
    from ai.pipeline import PipelineWorker
    parameters = inspect.signature(PipelineWorker.__init__).parameters
    if complete and any(name not in values for name in OPTION_NAMES):
        raise TaskRecoveryError('任务参数快照不完整，请重新发起任务。')
    options = {name: values.get(name, parameters[name].default) for name in OPTION_NAMES}
    if 'vol' not in values:
        options['vol'] = 1
    for name, value in options.items():
        if name == 'rewrite_instruction':
            if not isinstance(value, str):
                raise TaskRecoveryError('任务的重写要求格式损坏，请重新发起任务。')
            options[name] = value.strip()
        elif name in INTEGER_OPTIONS:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TaskRecoveryError(f'任务参数 {name} 无效，请重新发起任务。')
        elif not isinstance(value, bool):
            raise TaskRecoveryError(f'任务选项 {name} 无效，请重新发起任务。')
    if options['vol'] < 1 or options['min_score'] > 100:
        raise TaskRecoveryError('任务的卷号或审稿门槛无效，请重新发起任务。')
    if options['until_no'] and (options['whole_book'] or options['force_rewrite']):
        raise TaskRecoveryError('任务范围冲突，请重新发起任务。')
    return options


def worker_spec(worker):
    options = validate_options({name: getattr(worker, name) for name in OPTION_NAMES},
                               complete=True)
    runtime = getattr(worker, '_runtime_snapshot', None)
    if runtime is not None:
        options['snapshot_version'] = SNAPSHOT_VERSION
        options['runtime'] = copy.deepcopy(runtime)
    return mode_for(options), options


def create_worker(cfg, db_path, project_id, vol=1, *, resume_run_id=0, **values):
    """One constructor and validation path for new and resumed writing work."""
    from ai.pipeline import PipelineWorker
    if any(name not in OPTION_NAMES for name in values):
        raise TaskRecoveryError('任务包含未知选项，请重新发起任务。')
    options = validate_options(dict(values, vol=vol))
    return PipelineWorker(cfg, db_path, project_id, resume_run_id=resume_run_id, **options)


def model_reference(cfg):
    return {name: cfg[name] for name in MODEL_FIELDS if name in cfg}


def budget_settings(db):
    budget = UsageBudget(db.get_setting('task_budget.max_calls', '0'),
                         db.get_setting('task_budget.output_tokens', '0'),
                         db.get_setting('task_budget.retries', '2'))
    return {'max_calls': budget.max_calls, 'output_cap': budget.output_cap,
            'retry_limit': budget.retry_limit}


def capture_runtime(db, worker):
    profile = style_profile.get_profile(db, worker.pid)
    configs = [dict(row) for row in db.get_ai_configs()
               if row['base_url'] and row['model']]
    worker._fallback_configs = configs
    return {
        'model': model_reference(worker.cfg),
        'fallbacks': [model_reference(cfg) for cfg in configs],
        'prefs': prefs.load_from_db(db),
        'budget': budget_settings(db),
        'style': style_profile.effective_style(db, worker.pid),
        'style_revision': profile['revision'] if profile else 0,
        'auto_extract': db.get_setting(story_memory.AUTO_EXTRACT_SETTING, 'off') == 'on',
    }


def prepare_worker(db, worker):
    if db.get_project(worker.pid) is None:
        raise TaskRecoveryError('作品不存在，无法启动任务。')
    validate_options({name: getattr(worker, name) for name in OPTION_NAMES}, complete=True)
    if getattr(worker, '_runtime_snapshot', None) is None:
        worker._runtime_snapshot = capture_runtime(db, worker)
    return worker


def start_task(db, worker, title, parent_run_id=0):
    """Both desktop entry points and recovered tasks use this before starting Qt."""
    ledger.ensure_schema(db)
    prepare_worker(db, worker)
    if parent_run_id:
        parent = ledger.get_run(db, parent_run_id)
        if parent is None or parent['project_id'] != worker.pid:
            raise TaskRecoveryError('续跑记录不属于当前作品。')
        if worker.resume_run_id != parent_run_id:
            raise TaskRecoveryError('续跑任务的来源不一致。')
    mode, args = worker_spec(worker)
    worker.task_run_id = ledger.create_run(db, worker.pid, title, mode, args,
                                          parent_run_id=parent_run_id)
    return worker.task_run_id


def _resolve_model(db, reference):
    if (not isinstance(reference, dict) or not reference.get('base_url')
            or not reference.get('model')):
        raise TaskRecoveryError('原任务的模型记录损坏，请重新发起任务。')
    for row in db.get_ai_configs():
        cfg = dict(row)
        if reference.get('id') and reference['id'] != cfg['id']:
            continue
        if all(cfg.get(name) == value for name, value in reference.items()
               if name in MODEL_FIELDS and (name != 'name' or not reference.get('id'))):
            # Keys are always read from the current configuration, never from a run.
            return cfg
    raise TaskRecoveryError('原任务的模型配置已删除或模型/地址已改变；请恢复原配置或重新发起任务。')


def _validate_runtime(runtime):
    if not isinstance(runtime, dict) or not all(key in runtime for key in
            ('model', 'fallbacks', 'prefs', 'budget', 'style', 'style_revision', 'auto_extract')):
        raise TaskRecoveryError('任务执行快照损坏，请重新发起任务。')
    if (not isinstance(runtime['style'], str)
            or not isinstance(runtime['style_revision'], int)
            or not isinstance(runtime['auto_extract'], bool)
            or not isinstance(runtime['fallbacks'], list)):
        raise TaskRecoveryError('任务文风或模型快照格式损坏，请重新发起任务。')
    p = runtime['prefs']
    if (not isinstance(p, dict) or p.get('thinking') not in prefs.THINK_MODES
            or (p.get('budget') is not None and
                (type(p['budget']) is not int or not prefs.MIN_BUDGET <= p['budget'] <= prefs.MAX_BUDGET))):
        raise TaskRecoveryError('任务写作偏好损坏，请重新发起任务。')
    b = runtime['budget']
    if not isinstance(b, dict) or any(type(b.get(key)) is not int or b[key] < 0
                                     for key in ('max_calls', 'output_cap', 'retry_limit')):
        raise TaskRecoveryError('任务预算快照损坏，请重新发起任务。')


def budget_exhausted(limits, usage):
    return ((limits['max_calls'] and usage['calls'] >= limits['max_calls'])
            or (limits['output_cap'] and limits['output_cap'] - usage['reserved_completion'] < 256))


def restart_rebuild_worker(db, run_id, project_id):
    """A failed rebuild rolls back, so retry its saved plan as a fresh whole job."""
    run = ledger.get_run(db, run_id)
    if run is None or run['project_id'] != project_id or run['mode'] != 'rebuild_book':
        raise TaskRecoveryError('没有这本书的全书重构记录，不能沿用其他书的要求。')
    if run['status'] not in ('failed', 'interrupted', 'stopped', 'partial', 'paused_budget'):
        raise TaskRecoveryError('这项重构已经完成或正在执行，请先查看实际任务状态。')
    try:
        args = json.loads(run['args_json'])
    except (ValueError, TypeError) as exc:
        raise TaskRecoveryError('原重构要求记录损坏，请重新说明要求。') from exc
    if not isinstance(args, dict) or args.get('snapshot_version') != SNAPSHOT_VERSION:
        raise TaskRecoveryError('这条旧重构记录没有完整执行快照，请重新说明重构要求。')
    options = validate_options(args, complete=True)
    if not options['rebuild'] or not options['whole_book']:
        raise TaskRecoveryError('原重构模式与参数不一致，请重新生成计划。')
    if run['book_revision'] and ledger.book_revision(db, project_id, include_style=False) != run['book_revision']:
        raise TaskRecoveryError('作品内容已在上次任务后改变，请重新生成重构计划。')
    runtime = copy.deepcopy(args.get('runtime'))
    _validate_runtime(runtime)
    cfg = _resolve_model(db, runtime['model'])
    fallbacks = [_resolve_model(db, reference) for reference in runtime['fallbacks']]
    worker = create_worker(cfg, db.path, project_id, **options)
    worker._runtime_snapshot = runtime
    worker._fallback_configs = fallbacks
    return worker


def resume_worker(db, run_id, project_id, *, budget_override=None):
    """Validate a persisted run, then build a worker using its original options."""
    run = ledger.get_run(db, run_id)
    if run is None or run['project_id'] != project_id:
        raise TaskRecoveryError('这条中断记录属于另一本书，切换到那本书再续跑。')
    if run['mode'] not in ledger.RESUMABLE_MODES:
        raise TaskRecoveryError('全书重构涉及整书备份/恢复，请重新发起指令。')
    if run['status'] not in ('interrupted', 'stopped', 'failed', 'partial', 'paused_budget'):
        raise TaskRecoveryError('这条任务已经完成、正在运行或已由续跑接管。')
    try:
        args = json.loads(run['args_json'])
    except (ValueError, TypeError) as exc:
        raise TaskRecoveryError('任务参数记录损坏，请重新发起任务。') from exc
    if not isinstance(args, dict):
        raise TaskRecoveryError('任务参数记录损坏，请重新发起任务。')
    modern = 'snapshot_version' in args
    warnings = []
    if modern:
        if type(args['snapshot_version']) is not int or args['snapshot_version'] != SNAPSHOT_VERSION:
            raise TaskRecoveryError('此任务的快照版本不受支持，请使用对应版本的软件。')
        options = validate_options(args, complete=True)
        if mode_for(options) != run['mode']:
            raise TaskRecoveryError('任务模式与参数不一致，请重新发起任务。')
        runtime = copy.deepcopy(args.get('runtime'))
        _validate_runtime(runtime)
        cfg = _resolve_model(db, runtime['model'])
        fallbacks = [_resolve_model(db, ref) for ref in runtime['fallbacks']]
        if runtime['style'] != style_profile.effective_style(db, project_id):
            warnings.append('作品文风已更新；这次续跑仍沿用原任务存档的文风。')
    else:
        mode = run['mode']
        if mode == 'write_until' and (type(args.get('until_no')) is not int or args['until_no'] < 1):
            raise TaskRecoveryError('这条记录缺少目标章号，请重新说「写到第N章」。')
        if mode in ('write_volume', 'rewrite', 'rewrite_volume') and (
                type(args.get('vol')) is not int or args['vol'] < 1):
            raise TaskRecoveryError('这条记录缺少卷号，请重新发起任务。')
        if mode.startswith('rewrite') and (not isinstance(args.get('rewrite_instruction'), str)
                                           or not args['rewrite_instruction'].strip()):
            raise TaskRecoveryError('这条旧重写记录的原要求没有存档，请重新说一次要求。')
        values = dict(args, whole_book=mode in ('whole_book', 'rewrite_book'),
                      force_rewrite=mode.startswith('rewrite'), rebuild=False)
        options = validate_options(values)
        cfg_row = db.get_default_config()
        if cfg_row is None:
            raise TaskRecoveryError('还没有配置 AI 模型，请先配置模型。')
        cfg, runtime, fallbacks = dict(cfg_row), None, None
        warnings.append('旧任务没有完整参数快照：未记录的选项采用当前默认值、模型和预算；本次会完整存档。')
    if run['book_revision'] and ledger.book_revision(db, project_id, include_style=False) != run['book_revision']:
        raise TaskRecoveryError('作品内容已在任务结束后改变，请重新发起任务，以免沿用过期计划。')
    worker = create_worker(cfg, db.path, project_id, resume_run_id=run_id, **options)
    worker._runtime_snapshot = runtime
    worker._fallback_configs = fallbacks
    prepare_worker(db, worker)
    limits = worker._runtime_snapshot['budget']
    usage = ledger.cumulative_usage(db, run_id)
    if budget_override is not None:
        proposed = {key: budget_override[key] for key in ('max_calls', 'output_cap')}
        if any(type(value) is not int or value < 0 for value in proposed.values()):
            raise TaskRecoveryError('追加的任务预算无效。')
        if any((limits[key] == 0 and proposed[key] != 0) or
               (limits[key] and proposed[key] and proposed[key] < limits[key])
               for key in proposed):
            raise TaskRecoveryError('续跑只能提高原任务预算，不能降低已存档的上限。')
        limits.update(proposed)
    if budget_exhausted(limits, usage):
        raise TaskBudgetExhausted('原任务预算已用尽；请在模型配置中提高任务预算，再继续。累计用量会保留。')
    return worker, warnings
