"""Plans describe methods, never override the user's requests or prove completion."""
import copy
import unicodedata

from .capabilities import BUILTINS, validate

READ_TOOLS = {tool.name for tool in BUILTINS if tool.effect == 'read'}
ACTION_TOOLS = {tool.name for tool in BUILTINS if tool.effect != 'state'}


def validate_plan(args):
    validate(args, next(tool for tool in BUILTINS if tool.name == 'plan_task').parameters)
    if not isinstance(args.get('goal'),str) or not args['goal'].strip():
        raise ValueError('计划需要说明目标。')
    if args.get('mode') not in ('answer','research','action'):
        raise ValueError('无效计划类型。')
    plan = copy.deepcopy(args)
    if 'work_items' not in plan:
        return plan  # Existing checkpoints and clients keep their original shape.
    items = plan['work_items']
    if not items:
        raise ValueError('复杂任务工作清单不能为空；简单任务无需提交计划。')
    by_id = {}
    for item in items:
        ident = item['id']
        description = ''.join(unicodedata.normalize('NFKC', item['description']).casefold().split())
        if not ident.strip() or ident != ident.strip() or ident in by_id:
            raise ValueError('步骤编号须唯一且非空。')
        if not description:
            raise ValueError('步骤描述须非空。')
        by_id[ident] = item
        item.setdefault('depends_on', [])
        item.setdefault('evidence_refs', [])
    visited, visiting = set(), set()

    def visit(ident):
        if ident in visiting:
            raise ValueError('计划依赖不能形成循环。')
        if ident in visited:
            return
        visiting.add(ident)
        item = by_id[ident]
        for dependency in item['depends_on']:
            if dependency not in by_id:
                raise ValueError('依赖的步骤编号不存在。')
            visit(dependency)
            if item['status'] in ('in_progress', 'completed') and by_id[dependency]['status'] != 'completed':
                raise ValueError('前置步骤尚未完成，不能推进依赖它的步骤。')
        visiting.remove(ident)
        visited.add(ident)

    for ident in by_id:
        visit(ident)
    return plan


def plan_progress(plan):
    """Derive actionable state; never promote a checklist to outcome evidence."""
    items = plan.get('work_items', [])
    completed = {item['id'] for item in items if item['status'] == 'completed'}
    return {
        'in_progress': [item['id'] for item in items if item['status'] == 'in_progress'],
        'ready': [item['id'] for item in items if item['status'] == 'pending'
                  and set(item.get('depends_on', [])) <= completed],
        'waiting_dependencies': [item['id'] for item in items if item['status'] == 'pending'
                                 and not set(item.get('depends_on', [])) <= completed],
        'completed': sorted(completed),
        'basis': 'reported_plan_state_not_completion_evidence',
    }


def validate_plan_evidence(store, job, facts, plan):
    """Citations must name verified facts in this task, never arbitrary receipts."""
    cited = {ref for item in plan.get('work_items', []) for ref in item.get('evidence_refs', [])}
    if not cited:
        return
    from .outcome_evidence import evidence_catalog
    catalog = {entry['id']: entry for entry in evidence_catalog(store, job, facts)}
    for ref in cited:
        entry = catalog.get(ref)
        if not entry or entry['status'] != 'verified':
            raise ValueError('计划证据必须引用当前任务内已核实的真实结果。请使用 task_evidence 返回的 id，或省略可选的 evidence_refs 后更新计划；不要自造编号，也无需重做已成功操作。排队、失败和其他会话的记录不能充当完成证明。')


def review_execution(store, job, answer):
    # Legacy import retained for old runner clients. Production uses OutcomeReviewer.
    return None
