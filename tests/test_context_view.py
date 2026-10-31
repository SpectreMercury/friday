import copy
import json

import pytest

from local_ai.context_view import bounded_json_view, bounded_task_facts, read_task_input, task_reference


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')))


def request(index, text, **extra):
    return {'job_id': f'job-{index}', 'text': text, 'created': index, 'status': 'running', 'outcome': None, **extra}


def facts(*requests, plans=None):
    return {'task_id': 'task-fixture', 'user_requests': list(requests), 'plans': plans or []}


def test_small_json_view_is_complete_and_does_not_share_mutable_source():
    source = {'records': [{'required': 'exact value'}]}
    view = bounded_json_view(source, task_reference('job-1'), max_chars=1000)
    assert view['complete'] and view['can_verify_all_requirements']
    assert view['value'] == source and size(view) <= 1000
    view['value']['records'][0]['required'] = 'modified view'
    assert source['records'][0]['required'] == 'exact value'


def test_long_json_view_has_hard_size_limit_and_never_claims_complete_evidence():
    source = {'required': '中文\\"' * 3000 + 'IMPORTANT CONDITION AT END'}
    view = bounded_json_view(source, task_reference('job-1'), max_chars=1200)
    assert size(view) <= 1200
    assert not view['complete'] and not view['overall_complete'] and not view['can_verify_all_requirements']
    assert view['value']['truncated'] and 'excerpt' in view['value']
    assert view['reference']['arguments'] == {'job_id': 'job-1', 'offset': 0}
    assert view['total_characters'] == size(source)
    assert 'IMPORTANT CONDITION AT END' not in view['value']['excerpt']


def test_single_normal_12000_character_request_remains_complete_with_declared_bounded_exception():
    source = facts(request(1, '中' * 12000))
    view = bounded_task_facts(source, 'job-1')
    assert view['user_requests'][0]['text'] == source['user_requests'][0]['text']
    assert view['user_requests'][0]['complete']
    assert view['overall_complete'] and view['current_inputs_complete'] and view['can_verify_all_requirements']
    assert 12000 < view['budget_characters'] <= 14000
    assert size(view) <= view['budget_characters']


def test_multiple_current_inputs_over_budget_preserve_recent_input_and_report_missing_requirements():
    source = facts(request(1, 'OLD CURRENT CONDITION ' * 600), request(2, '新' * 12000))
    original = copy.deepcopy(source)
    view = bounded_task_facts(source, 'job-1', current_input_ids=['job-2'])
    latest = next(row for row in view['user_requests'] if row['job_id'] == 'job-2')
    assert latest['text'] == '新' * 12000 and latest['complete']
    assert not view['overall_complete'] and not view['current_inputs_complete']
    assert not view['can_verify_all_requirements']
    assert view['coverage']['current_input_count'] == 2
    assert view['coverage']['complete_current_inputs'] == 1
    assert view['coverage']['omitted_user_requests'] + view['coverage']['truncated_user_requests'] >= 1
    assert size(view) <= view['budget_characters'] <= 14000
    assert source == original


def test_long_history_is_bounded_and_incomplete_history_cannot_prove_all_requirements():
    source = facts(*(request(index, f'Historical condition {index} ' * 1000) for index in range(20)),
                   request(20, 'Please continue with the earlier constraints'))
    view = bounded_task_facts(source, 'job-20', max_chars=3000)
    current = next(row for row in view['user_requests'] if row['job_id'] == 'job-20')
    assert current['complete'] and view['current_inputs_complete']
    assert not view['overall_complete'] and not view['can_verify_all_requirements']
    assert view['coverage']['omitted_user_requests'] > 0
    assert any(not row['complete'] for row in view['user_requests'])
    assert view['original']['arguments']['job_id'] == 'job-20'
    for row in view['user_requests']:
        assert row['source_job_id'] == row['job_id']
        assert row['original']['arguments']['job_id'] == row['job_id']
        assert row['text_characters'] == len(source['user_requests'][int(row['job_id'].split('-')[1])]['text'])
    assert size(view) <= view['budget_characters']


def test_missing_current_input_id_does_not_claim_current_coverage():
    view = bounded_task_facts(facts(request(1, 'A prior request')), 'job-missing')
    assert not view['current_inputs_complete'] and not view['can_verify_all_requirements']


def test_plan_omission_is_explicit_and_preserves_original_plan():
    source = facts(request(1, 'A complete request'), plans=[{'steps': ['long plan ' * 4000]}])
    view = bounded_task_facts(source, 'job-1', max_chars=1500)
    assert view['current_inputs_complete'] and view['can_verify_all_requirements']
    assert not view['overall_complete'] and view['coverage']['omitted_plans'] == 1
    assert len(source['plans'][0]['steps'][0]) == 40000


def test_paging_recovers_exact_original_unicode_and_late_condition():
    original = '字符🌟' * 3000 + '\nMUST INCLUDE THE FINAL CONDITION'
    pages = []
    offset = 0
    while True:
        page = read_task_input(original, 'job-1', offset, limit=1200)
        pages.append(page)
        assert page['job_id'] == 'job-1' and page['offset'] == offset
        assert page['total_characters'] == len(original)
        assert not page['complete_input']
        if page['next_offset'] is None:
            break
        assert page['next_offset'] > offset
        offset = page['next_offset']
    assert ''.join(page['text'] for page in pages) == original
    assert pages[-1]['text'].endswith('MUST INCLUDE THE FINAL CONDITION')
    assert read_task_input('short', 'job-1')['complete_input']
    assert not read_task_input('short', 'job-1', 2)['complete_input']


@pytest.mark.parametrize('offset,limit', [(-1, 10), (True, 10), (0, 0), (0, 12001), (0, True)])
def test_paging_rejects_invalid_bounds(offset, limit):
    with pytest.raises(ValueError):
        read_task_input('original', 'job-1', offset, limit)


def test_invalid_budget_cannot_silently_remove_source_reference():
    with pytest.raises(ValueError):
        bounded_json_view('large' * 500, {'reference': 'x' * 1000}, max_chars=256)
    with pytest.raises(ValueError):
        bounded_task_facts(facts({'job_id': 'job-1'}), 'job-1')
