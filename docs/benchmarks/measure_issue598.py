#!/usr/bin/env python3
"""Observe real public kickoff preparation; never activate or execute proposals."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import yaml

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / 'src/cafe/data/skills/use-cafe-workflow'
MODEL = 'claude-sonnet-5-5'
URLS = ['https://code.claude.com/docs/en/model-config',
        'https://code.claude.com/docs/en/sub-agents']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_observation(record):
    """Only a complete, valid proposal with all observed children exited counts."""
    if record.get('terminal') != 'rendered' or record.get('elapsed_seconds', 0) <= 0:
        raise ValueError('incomplete preparation observation')
    if not record.get('children') or any(c.get('exit_code') != 0 for c in record['children']):
        raise ValueError('child completion is missing or unsuccessful')
    artifact = record.get('artifact', {})
    proposal = artifact.get('proposal')
    if not isinstance(proposal, dict) or not artifact.get('rendered_text'):
        raise ValueError('complete proposal and rendered artifact are required')
    from cafe.manager._schema import validate_compact_proposal
    if record['mode'] == 'compact':
        validate_compact_proposal(proposal)
    else:
        from cafe.manager.delivery import normalize_delivery_contract
        normalize_delivery_contract(proposal['delivery_contract'])
        for field in ('phases', 'confirmation_contract', 'checkout', 'manager', 'locales'):
            if field not in proposal:
                raise ValueError('full proposal is incomplete')
    if digest(json.dumps(artifact, sort_keys=True).encode()) != record.get('artifact_sha256'):
        raise ValueError('artifact digest differs')


class Observation:
    def __init__(self, root, trace):
        self.root, self.trace = root, trace
        self.children, self.reads, self.network = [], [], []
        self.active = False

    def run(self, argv):
        print('  observe:', ' '.join(map(str, argv[:4])), flush=True)
        env = dict(os.environ)
        if self.active:
            env['CAFE_MEASUREMENT_TRACE'] = str(self.trace / 'events.jsonl')
            env['PYTHONPATH'] = str(self.trace) + os.pathsep + env.get('PYTHONPATH', '')
        result = subprocess.run(list(map(str, argv)), cwd=self.root, env=env,
                                text=True, capture_output=True, timeout=45)
        if self.active:
            self.children.append({'argv': list(map(str, argv)), 'exit_code': result.returncode,
                                  'stdout_sha256': digest(result.stdout.encode()),
                                  'stderr_sha256': digest(result.stderr.encode())})
        if result.returncode:
            raise RuntimeError(result.stdout[-4000:] + result.stderr[-1000:])
        return result.stdout

    def read(self, path):
        data = path.read_bytes()
        if self.active:
            self.reads.append({'path': str(path), 'bytes': len(data), 'sha256': digest(data)})
        return data

    def evidence(self):
        sources = []
        for url in URLS:
            print('  observe primary source:', url, flush=True)
            body = self.run(["curl", "--fail", "--silent", "--show-error", "--location",
                             "--max-time", "15", url]).encode()
            source = {'url': url, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                      'fingerprint': digest(body)}
            sources.append(source)
            if self.active:
                self.network.append(source)
        now = datetime.now(timezone.utc).isoformat()
        return {'provider': 'claude', 'model': MODEL, 'version': MODEL,
                'assessed_at': now, 'sources': sources,
                'workloads': ['implementation', 'review', 'publication'], 'reasoning': 'standard',
                'capability_bands': {'coding': 'strong', 'implementation': 'strong', 'review': 'strong'},
                'limitations': ['Preparation verifies installed CLI projection only; account/model availability and actual native invocation are not measured.']}

    def preflights(self):
        reports = {}
        for kind, argv in [('update', [ROOT / '.venv/bin/cafe', 'update', 'check', '--json']),
                           ('catalog', [ROOT / '.venv/bin/cafe', 'catalog', 'check', '--json'])]:
            raw = json.loads(self.run(argv))
            path = self.root / '.cafe' / (kind + '.json')
            path.write_text(json.dumps(raw))
            reports[kind] = {'file': str(path), 'checked_at': datetime.now(timezone.utc).isoformat()}
        return reports


def fixtures(base):
    root = base / 'repo'
    root.mkdir()
    trace = base / 'trace'
    trace.mkdir()
    (trace / 'sitecustomize.py').write_text('''import json, os, sys
p = os.environ.get("CAFE_MEASUREMENT_TRACE")
def audit(event, args):
    if p and event == "subprocess.Popen":
        with open(p, "a") as f:
            f.write(json.dumps({"argv": args[1], "cwd": str(args[2])}, default=str) + "\\n")
sys.addaudithook(audit)
''')
    ob = Observation(root, trace)
    ob.run(['git', 'init', '-q'])
    ob.run(['git', 'config', 'user.name', 'Measurement'])
    ob.run(['git', 'config', 'user.email', 'measurement@example.invalid'])
    (root / '.gitignore').write_text('.cafe/\n')
    (root / 'src').mkdir(); (root / 'tests').mkdir()
    (root / 'src/app.py').write_text('def total(values):\n    return sum(values)\n')
    (root / 'tests/test_app.py').write_text('from src.app import total\n\ndef test_empty():\n    assert total([]) == 0\n')
    ob.run(['git', 'add', '.'])
    ob.run(['env', 'GIT_AUTHOR_DATE=2026-10-06T00:00:00+08:00',
            'GIT_COMMITTER_DATE=2026-10-06T00:00:00+08:00', 'git', 'commit', '-qm', 'matched baseline'])
    ob.run(['git', 'checkout', '-qb', 'feature'])
    remote = base / 'remote.git'; ob.run(['git', 'init', '--bare', '-q', str(remote)])
    ob.run(['git', 'remote', 'add', 'origin', str(remote)])
    local = root / '.cafe/playbooks'; local.mkdir(parents=True)
    graph = yaml.safe_load((ROOT / 'src/cafe/data/playbooks/streamlined.yaml').read_text())
    graph['playbook']['id'] = 'benchmark'
    return ob, graph, local / 'benchmark.yaml'


def observe(route, cache, mode, base):
    ob, graph, graph_path = fixtures(base)
    graph['contract'] = {'mode': mode}
    graph_path.write_text(yaml.safe_dump(graph, sort_keys=False))
    version = None; evidence = None; reports = None
    if cache == 'reused':
        version = ob.run(['claude', '--version']).strip()
        evidence = ob.evidence()
        reports = ob.preflights()  # Valid same-fact reports available equally to both paths.
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic(); ob.active = True
    ob.read(SKILL / 'SKILL.md')
    ob.read(SKILL / 'references/playbook_selection.md')
    refs = (['compact_kickoff.md'] if mode == 'compact' else
            ['kickoff_inputs.md', 'kickoff.md', 'model_selection.md', 'strategic_context.md'])
    for name in refs:
        ob.read(SKILL / 'references' / name)
    ob.read(SKILL / 'references/workflow_progress.md')
    baseline = ob.run(['git', 'rev-parse', 'HEAD']).strip()
    ob.run(['git', 'status', '--porcelain'])
    ob.run(['rg', '--files', 'src', 'tests'])
    for path in ('src/app.py', 'tests/test_app.py'):
        ob.read(ob.root / path)
    if version is None:
        version = ob.run(['claude', '--version']).strip()
    if evidence is None:
        evidence = ob.evidence()
    phases = [{'name': phase, 'chain': [{'cli': 'claude', 'model': MODEL}]} for phase in ('develop', 'deliver')]
    endpoint = ({'schema_version': 4, 'route': 'pr', 'remote': 'origin', 'source_branch': 'feature',
                 'target_branch': 'main', 'effects': ['create_pr']} if route == 'pr' else
                {'schema_version': 4, 'route': 'direct', 'remote': 'origin', 'branch': 'feature', 'effects': ['commit', 'push']})
    request = {'schema_version': 1, 'project_root': str(ob.root), 'issue_name': 'benchmark',
               'playbook_id': 'benchmark', 'model_assessments': [evidence], 'conversation_locale': 'zh-TW'}
    if mode == 'compact':
        request['compact_inputs'] = {'files': ['src/app.py', 'tests/test_app.py'], 'phases': phases,
            'review_configuration': {'cli': 'claude', 'model': MODEL, 'provider_version': version,
                'read_only': True, 'model_behavior': 'inherits_parent', 'checkpoint_interface': 'parent_command'},
            'delivery_contract': endpoint}
    else:
        if reports is None:
            reports = ob.preflights()
        request['preflight_files'] = {k: v['file'] for k, v in reports.items()}
        request['preflight_metadata'] = {k: {'checked_at': v['checked_at'], 'decision': 'continue',
                                            'post_change_evidence': None} for k, v in reports.items()}
        deliver = ([] if route == 'pr' else [['git', 'commit', '--allow-empty', '-m', 'Deliver benchmark'],
                                             ['git', 'push', 'origin', 'HEAD:refs/heads/feature']])
        request['formatter_inputs'] = {
            'delivery_contract': {'schema_version': 3, 'outcome': 'Keep empty totals equal to zero.',
                'in_scope': ['src/app.py', 'tests/test_app.py'], 'out_of_scope': ['All other files'],
                'acceptance_invariants': ['Empty totals equal zero.'],
                'implementation_direction': 'Use the existing total function.',
                'permissions': [json.dumps(endpoint, sort_keys=True)], 'constraints': ['No new dependencies.']},
            'deliver': deliver, 'cleanup': [], 'deliver_description': ['Commit approved change', 'Push exact branch'] if deliver else [],
            'cleanup_description': [], 'phase_chain': [f'{p}=claude:{MODEL}' for p in ('develop', 'deliver')],
            'effective_locale': 'zh-TW', 'locale_source': 'explicit', 'repository_content_locale': 'en-US',
            'current_checkout': True, 'manager_mode': 'unattended', 'user_required': [], 'manager_confirmable': [],
            'need_clarification': 'user_required', 'need_permission': 'user_required', 'alignment_checkpoint': 'user_required',
            'proactive_review_decision': ['develop=not_required', 'deliver=not_required'], 'capability_choice': []}
    request_file = ob.root / '.cafe/request.json'; request_file.write_text(json.dumps(request))
    command = [sys.executable, SKILL / 'scripts/prepare_kickoff.py']
    flags = ['--request-file', request_file, '--config-dir', base / 'preferences', '--cache-dir', base / 'cache']
    for stage in ('discover', 'assemble', 'render'):
        result = json.loads(ob.run([*command, stage, *flags]))
        (base / (stage + '.json')).write_text(json.dumps(result, indent=2))
        if stage == 'assemble' and result.get('status') != 'ready':
            raise RuntimeError(json.dumps(result.get('missing_decisions')))
    rendered = result['render']
    elapsed = time.monotonic() - started; ob.active = False
    artifact = {'proposal': rendered.get('proposal'), 'rendered_text': rendered.get('output')}
    events = [json.loads(line) for line in (ob.trace / 'events.jsonl').read_text().splitlines()]
    record = {'route': route, 'cache': cache, 'mode': mode, 'started_at': started_at,
              'elapsed_seconds': elapsed, 'repository_baseline': baseline, 'provider_version': version, 'model': MODEL,
              'terminal': rendered['status'], 'children': ob.children, 'reference_and_repository_reads': ob.reads,
              'network': ob.network, 'nested_subprocesses': events,
              'tool_count': len(ob.children) + len(ob.reads) + len(events),
              'artifact': artifact, 'artifact_sha256': digest(json.dumps(artifact, sort_keys=True).encode()),
              'selected_graph_sha256': digest(json.dumps({k: v for k, v in graph.items() if k != 'contract'}, sort_keys=True).encode()),
              'model_evidence': evidence, 'preflight_reports': {k: json.loads(Path(v['file']).read_text()) for k, v in (reports or {}).items()}}
    validate_observation(record)
    print('  terminal:', rendered['status'], 'children exit 0;', round(elapsed, 3), 'seconds;', record['tool_count'], 'tools', flush=True)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--only', choices=('full', 'compact'))
    args = parser.parse_args()
    records = []
    revision = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip()
    with tempfile.TemporaryDirectory(prefix='cafe-issue598-observation-') as workspace:
        for route in ('pr', 'direct'):
            for cache in ('fresh', 'reused'):
                for mode in ('full', 'compact'):
                    if args.only and mode != args.only:
                        continue
                    base = Path(workspace) / f'{route}-{cache}-{mode}'; base.mkdir()
                    print('CASE', base.name, flush=True)
                    records.append(observe(route, cache, mode, base))
                    args.output.write_text(json.dumps({'schema_version': 1, 'revision': revision,
                        'recorder_sha256': digest(Path(__file__).read_bytes()), 'records': records}, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
