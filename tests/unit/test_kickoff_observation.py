"""U9: preparation completion and independent child evidence are mandatory."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest
from tests.unit.test_compact_contract import compact_request, compact_proposal

spec = importlib.util.spec_from_file_location('kickoff_observation',
    Path(__file__).resolve().parents[2] / 'docs/benchmarks/measure_issue598.py')
observation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observation)


def complete_record(proposal):
    artifact = {'proposal': proposal, 'rendered_text': 'Complete proposal artifact'}
    return {'mode': 'compact', 'terminal': 'rendered', 'elapsed_seconds': 1,
            'children': [{'exit_code': 0}], 'artifact': artifact,
            'artifact_sha256': observation.digest(json.dumps(artifact, sort_keys=True).encode())}


def test_complete_proposal_and_separate_successful_child_evidence_are_accepted(compact_proposal):
    observation.validate_observation(complete_record(compact_proposal))


@pytest.mark.parametrize('change', [
    {'terminal': 'progress'}, {'elapsed_seconds': 0}, {'children': []},
    {'children': [{'exit_code': None}]}, {'children': [{'exit_code': 1}]},
    {'artifact': {}}, {'artifact_sha256': 'wrong'},
])
def test_partial_progress_failed_child_or_changed_artifact_never_counts(compact_proposal, change):
    record = complete_record(compact_proposal)
    record.update(change)
    with pytest.raises(ValueError):
        observation.validate_observation(record)


def test_proposal_without_confirmable_scope_never_counts(compact_proposal):
    proposal = deepcopy(compact_proposal)
    proposal['file_scope']['paths'] = []
    with pytest.raises(ValueError):
        observation.validate_observation(complete_record(proposal))


@pytest.mark.parametrize('changed', ['provider_version', 'model', 'repository_baseline', 'selected_graph_sha256',
                                    'phase_chains', 'review_configuration', 'native_projection'])
def test_unmatched_execution_or_repository_identity_is_not_a_comparison(compact_proposal, changed):
    records = []
    for route in ('pr', 'direct'):
        for cache in ('fresh', 'reused'):
            for mode in ('full', 'compact'):
                record = complete_record(compact_proposal)
                # The paired validator accepts already validated observation records.
                record.update(route=route, cache=cache, mode=mode, provider_version='installed',
                              model='pinned', repository_baseline='baseline', selected_graph_sha256='graph',
                              phase_chains='fixed', review_configuration='fixed', native_projection='fixed')
                records.append(record)
    records[1][changed] = 'different'
    with pytest.raises(ValueError):
        observation.validate_comparison(records)
