import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SKILL = Path(__file__).resolve().parents[1] / 'native-skills/skills/autoharness-reflect'
spec = importlib.util.spec_from_file_location('completion', SKILL / 'scripts/completion.py')
completion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(completion)


def event(kind, **extra):
    return {'hook_event_name': kind, 'session_id': 'session', 'turn_id': 'turn', **extra}


def observe(root):
    completion.handle(event('UserPromptSubmit', prompt='secret prompt'), root, now=10)
    tool = event('PostToolUse', tool_use_id='tool1', tool_name='Bash',
                 tool_input={'command': 'cat /home/me/.agents/skills/duct/SKILL.md'},
                 tool_response='secret response')
    completion.handle(tool, root, now=12)
    completion.handle(tool, root, now=13)


def test_completion_is_quiet_and_does_not_claim_usage(tmp_path):
    observe(tmp_path)
    first = completion.handle(event('Stop', model='test-model'), tmp_path, now=20)
    assert first == {}
    assert completion.handle(event('Stop'), tmp_path, now=21) == {}
    records = [json.loads(x) for x in (tmp_path / 'observations.jsonl').read_text().splitlines()]
    assert len(records) == 1
    assert records[0]['outcome'] == 'unknown'
    assert records[0]['duration_seconds'] == 10
    assert records[0]['tool_counts'] == {'Bash': 1}
    assert records[0]['skill_candidates'] == ['duct']
    all_text = ''.join(p.read_text() for p in tmp_path.iterdir() if p.is_file())
    assert 'secret prompt' not in all_text and 'secret response' not in all_text
    schema = json.loads((SKILL / 'schemas/observation-v1.schema.json').read_text())
    Draft202012Validator(schema).validate(records[0])


@pytest.mark.parametrize('extra', [{'stop_hook_active': True}, {'permission_mode': 'plan'}])
def test_no_recursive_or_plan_prompt(tmp_path, extra):
    observe(tmp_path)
    assert completion.handle(event('Stop', **extra), tmp_path) == {}


def test_interrupt_and_no_skill_are_quiet(tmp_path):
    assert completion.handle(event('Stop'), tmp_path) == {}
    other = tmp_path / 'other'
    observe(other)
    assert completion.handle(event('Interrupt'), other) == {}


def test_reports_are_idempotent_and_schema_conformant(tmp_path):
    data = {'usages': [{'skill': 'duct', 'purpose': 'test-execution',
                       'outcome': 'success', 'reflection': 'none'}]}
    schema = json.loads((SKILL / 'schemas/report-v1.schema.json').read_text())
    Draft202012Validator(schema).validate(data)
    assert not completion.report('s', 't', data, tmp_path)['duplicate']
    assert completion.report('s', 't', data, tmp_path)['duplicate']
    assert len((tmp_path / 'observations.jsonl').read_text().splitlines()) == 1
    with pytest.raises(ValueError):
        completion.report('s', 't', {'usages': []}, tmp_path)
    record = json.loads((tmp_path / 'observations.jsonl').read_text())
    Draft202012Validator(json.loads((SKILL / 'schemas/observation-v1.schema.json').read_text())).validate(record)


@pytest.mark.parametrize('item', [
    {'outcome': 'made-up'}, {'skill_duration_seconds': -1},
    {'skill_duration_seconds': float('nan')}, {'correction_count': True},
    {'extra_field': 'not allowed'},
])
def test_invalid_reports_rejected_before_write(tmp_path, item):
    usage = {'skill': 'duct', 'purpose': 'tests', 'outcome': 'unknown', 'reflection': 'none', **item}
    with pytest.raises(ValueError):
        completion.report('s', 't', {'usages': [usage]}, tmp_path)
    assert not (tmp_path / 'observations.jsonl').exists()


def test_install_preserves_existing_hooks_and_is_idempotent(tmp_path):
    path = tmp_path / 'hooks.json'
    existing = {'description': 'existing', 'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'existing-hook'}]}]}}
    path.write_text(json.dumps(existing))
    assert completion.install(path, '/usr/bin/python3')['changed']
    current = json.loads(path.read_text())
    assert current['hooks']['Stop'][0] == existing['hooks']['Stop'][0]
    assert len(current['hooks']['Stop']) == 2
    assert not completion.install(path, '/usr/bin/python3')['changed']
    assert len(list(tmp_path.glob('hooks.json.backup-*'))) == 1


def test_status_deduplicates_crash_retries(tmp_path):
    observe(tmp_path)
    completion.handle(event('Stop'), tmp_path)
    path = tmp_path / 'observations.jsonl'
    path.write_text(path.read_text() * 2)
    completion.report('session', 'turn', {'usages': []}, tmp_path)
    out = completion.status(tmp_path)
    assert out['observed_turns'] == 1
    assert out['candidate_turns'] == 1
    assert out['empty_reports'] == 1
    assert out['confirmed_skill_uses'] == 0


def test_hook_process_success_has_no_output(tmp_path):
    env = {**os.environ, 'AUTOHARNESS_COMPLETION_STORE': str(tmp_path)}
    for item in (event('UserPromptSubmit'),
                 event('PostToolUse', tool_use_id='1', tool_name='Bash',
                       tool_input={'command': 'cat /skills/duct/SKILL.md'}),
                 event('Stop')):
        result = subprocess.run([sys.executable, str(SKILL / 'scripts/completion.py'), 'hook'],
                                input=json.dumps(item), text=True, capture_output=True, env=env)
        assert result.returncode == 0
        assert result.stdout == result.stderr == ''
    assert completion.status(tmp_path)['candidate_turns'] == 1


def test_hook_failure_warns_without_continuation_or_sensitive_details(tmp_path):
    store = tmp_path / 'private-store'
    store.write_text('not a directory')
    result = subprocess.run([sys.executable, str(SKILL / 'scripts/completion.py'), 'hook'],
                            input=json.dumps(event('Stop')), text=True, capture_output=True,
                            env={**os.environ, 'AUTOHARNESS_COMPLETION_STORE': str(store)})
    assert result.returncode == 0
    warning = json.loads(result.stdout)
    assert set(warning) == {'systemMessage'}
    assert 'could not be saved' in warning['systemMessage']
    assert 'Task completion is unaffected' in warning['systemMessage']
    assert str(store) not in result.stdout
    assert result.stderr == ''
