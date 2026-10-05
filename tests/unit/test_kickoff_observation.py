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
