"""Codex completion collector: stdlib only, no model subprocess or transcript reads."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

VERSION = 1
EVENTS = ('UserPromptSubmit', 'PostToolUse', 'Stop', 'Interrupt')
OUTCOMES = ('success', 'partial', 'failure', 'abandoned', 'unknown')
EXEMPT = {'autoharness-reflect', 'workshop-feedback', 'commit-provenance'}
CANDIDATE = re.compile(r'/skills/(?:\.system/)?([a-zA-Z0-9_-]+)/SKILL\.md')


def root_dir():
    base = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state')))
    return Path(os.environ.get('AUTOHARNESS_COMPLETION_STORE', str(base / 'autoharness/completion')))


def key_for(session, turn):
    if not isinstance(session, str) or not session or not isinstance(turn, str) or not turn:
        raise ValueError('session_id and turn_id must be nonempty strings')
    return hashlib.sha256((session + '\0' + turn).encode()).hexdigest()


@contextmanager
def locked(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def save(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, sort_keys=True))
    tmp.replace(path)


def append(root, record):
    with (root / 'observations.jsonl').open('a') as stream:
        stream.write(json.dumps(record, sort_keys=True) + '\n')


def handle(event, root=None, now=None):
    root = Path(root) if root is not None else root_dir()
    now = time.time() if now is None else now
    kind = event.get('hook_event_name')
    if kind not in EVENTS:
        return {}
    session, turn = event.get('session_id'), event.get('turn_id')
    key = key_for(session, turn)
    with locked(root):
        path = root / (key + '.json')
        state = json.loads(path.read_text()) if path.exists() else {
            'started_at': None, 'tool_ids': [], 'tool_counts': {},
            'candidates': [], 'closed': False,
        }
        if kind == 'UserPromptSubmit':
            if not state['closed'] and state['started_at'] is None:
                state['started_at'] = now
        elif kind == 'PostToolUse' and not state['closed']:
            tool_id = event.get('tool_use_id')
            if not tool_id or tool_id in state['tool_ids']:
                return {}
            state['tool_ids'].append(tool_id)
            name = str(event.get('tool_name', 'unknown'))[:160]
            state['tool_counts'][name] = state['tool_counts'].get(name, 0) + 1
            # A path in arguments is only a candidate: it could be a write or a failed read.
            text = json.dumps(event.get('tool_input', {}))
            state['candidates'] = sorted(set(state['candidates']) | (set(CANDIDATE.findall(text)) - EXEMPT))
        elif kind in ('Stop', 'Interrupt'):
            if not state['closed']:
                record = {
                    'schema_version': VERSION, 'kind': 'turn', 'id': key,
                    'recorded_at': now, 'event': kind,
                    'duration_seconds': None if state['started_at'] is None else max(0, now - state['started_at']),
                    'duration_scope': 'turn', 'tool_counts': state['tool_counts'],
                    'skill_candidates': state['candidates'],
                    'model': event.get('model'), 'outcome': 'unknown',
                    'measurement': 'host-observed',
                }
                append(root, record)
                state['closed'] = True
        save(path, state)
    return {}


def validate_report(data):
    if not isinstance(data, dict) or set(data) != {'usages'} or not isinstance(data['usages'], list):
        raise ValueError('report must contain only a usages array')
    if len(data['usages']) > 100:
        raise ValueError('at most 100 usage records')
    seen = set()
    required = {'skill', 'purpose', 'outcome', 'reflection'}
    optional = {'skill_duration_seconds', 'friction', 'correction_count', 'feedback_id'}
    for item in data['usages']:
        if not isinstance(item, dict) or not required <= set(item) or set(item) - required - optional:
            raise ValueError('usage has missing or unknown fields')
        for field in ('skill', 'purpose'):
            if not isinstance(item[field], str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._:-]{0,119}', item[field]):
                raise ValueError(field + ' must be a concise identifier')
        if item['skill'] in seen:
            raise ValueError('one record per skill per turn')
        seen.add(item['skill'])
        if item['outcome'] not in OUTCOMES or item['reflection'] not in ('none', 'needed', 'unknown'):
            raise ValueError('invalid outcome or reflection')
        if 'friction' in item and item['friction'] not in ('none', 'instructions', 'tooling', 'missing-context', 'other'):
            raise ValueError('invalid friction')
        if 'skill_duration_seconds' in item:
            value = item['skill_duration_seconds']
            if type(value) not in (float, int) or not 0 <= value < float('inf'):
                raise ValueError('duration must be finite and nonnegative')
        if 'correction_count' in item and (type(item['correction_count']) is not int or item['correction_count'] < 0):
            raise ValueError('correction_count must be a nonnegative integer')
        if 'feedback_id' in item:
            uuid.UUID(item['feedback_id'])
    return data


def report(session, turn, data, root=None):
    data = validate_report(data)
    root = Path(root) if root is not None else root_dir()
    key = key_for(session, turn)
    with locked(root):
        receipt = root / (key + '.report.json')
        if receipt.exists():
            if json.loads(receipt.read_text()) != data:
                raise ValueError('a different report already exists for this turn')
            return {'ok': True, 'duplicate': True}
        record = {'schema_version': VERSION, 'kind': 'usage-report', 'id': key,
                  'recorded_at': time.time(), 'measurement': 'agent-reported', **data}
        append(root, record)
        save(receipt, data)
    return {'ok': True, 'duplicate': False}


def install(config, python):
    config = Path(config).expanduser()
    data = json.loads(config.read_text()) if config.exists() else {'hooks': {}}
    command = shlex.join([str(Path(python).resolve()), str(Path(__file__).resolve()), 'hook'])
    hooks = data.setdefault('hooks', {})
    for event in EVENTS:
        groups = hooks.setdefault(event, [])
        # Exact identity; preserve unrelated handlers, including other Stop hooks.
        if not any(h.get('command') == command for g in groups for h in g.get('hooks', [])):
            groups.append({'hooks': [{'type': 'command', 'command': command, 'timeout': 5}]})
    if config.exists() and json.loads(config.read_text()) == data:
        return {'changed': False, 'config': str(config)}
    config.parent.mkdir(parents=True, exist_ok=True)
    if config.exists():
        backup = config.with_name(config.name + '.backup-' + uuid.uuid4().hex)
        backup.write_bytes(config.read_bytes())
    save(config, data)
    return {'changed': True, 'config': str(config), 'activation': 'review and trust in Codex /hooks'}


def status(root=None):
    root = Path(root) if root is not None else root_dir()
    records = {}
    if (root / 'observations.jsonl').exists():
        with locked(root):
            for line in (root / 'observations.jsonl').read_text().splitlines():
                row = json.loads(line)
                records[(row['kind'], row['id'])] = row
    turns = [r for (kind, _), r in records.items() if kind == 'turn']
    reports = [r for (kind, _), r in records.items() if kind == 'usage-report']
    usages = [u for r in reports for u in r['usages']]
    return {
        'store': str(root), 'observed_turns': len(turns),
        'candidate_turns': sum(bool(r['skill_candidates']) for r in turns),
        'reported_turns': len(reports), 'confirmed_skill_uses': len(usages),
        'empty_reports': sum(not r['usages'] for r in reports),
        'missing_start_events': sum(r['duration_seconds'] is None for r in turns),
        'reflection_needed': sum(u['reflection'] == 'needed' for u in usages),
        'outcomes': {o: sum(u['outcome'] == o for u in usages) for o in OUTCOMES},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('hook')
    sub.add_parser('status')
    rp = sub.add_parser('report')
    rp.add_argument('--session-id', required=True)
    rp.add_argument('--turn-id', required=True)
    rp.add_argument('--input', type=Path, required=True)
    ip = sub.add_parser('install')
    ip.add_argument('--config', default=str(Path.home() / '.codex/hooks.json'))
    ip.add_argument('--python', default=sys.executable)
    args = parser.parse_args()
    try:
        if args.command == 'hook':
            result = handle(json.load(sys.stdin))
        elif args.command == 'report':
            result = report(args.session_id, args.turn_id, json.loads(args.input.read_text()))
        elif args.command == 'status':
            result = status()
        else:
            result = install(args.config, args.python)
        if args.command != 'hook' or result:
            print(json.dumps(result))
        return 0
    except Exception as exc:
        # Collector failure must not prevent normal task completion.
        if args.command == 'hook':
            print(json.dumps({'systemMessage': (
                'AutoHarness feedback could not be saved (' + type(exc).__name__
                + '). Task completion is unaffected.'
            )}))
        else:
            print(json.dumps({'ok': False, 'error': str(exc)}))
            print('AutoHarness completion: ' + type(exc).__name__, file=sys.stderr)
        return 0 if args.command == 'hook' else 1


if __name__ == '__main__':
    sys.exit(main())
