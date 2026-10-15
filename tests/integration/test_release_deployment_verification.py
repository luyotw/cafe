"""I5: approved host verification binds a release deployment to its merge receipt."""

import json

import pytest

from cafe.core.workflow_models import StepWaiting
from tests.integration import test_development_delivery_journey as journey
from tests.unit.test_github_release_deployment_tool import DEPLOYED, responses, verification_plan

local_action = journey.local_action
fake_github = journey.fake_github


def test_approved_release_observer_waits_then_verifies_actual_merge_without_remerging(
    local_action, tmp_path, fake_github
):
    # Replace only the external GitHub executable's API responses; host hooks stay real.
    binary = tmp_path / "bin/gh"
    script = binary.read_text()
    script = script.replace(
        "if '/actions/workflows/' in endpoint:",
        "if endpoint in state.get('deployment_api', {}):\n"
        "    print(json.dumps(state['deployment_api'][endpoint]))\n"
        "elif '/actions/workflows/' in endpoint:",
    )
    binary.write_text(script)
    state = json.loads(fake_github.read_text())
    state["deployment_api"] = {"repos/owner/repo/deployments?environment=pypi&per_page=1": []}
    fake_github.write_text(json.dumps(state))
    ctx = journey.setup_action(
        local_action, tmp_path, github=True, verification=verification_plan()
    )
    journey.approve_action(ctx)
    with pytest.raises(StepWaiting):
        journey.hook(ctx[6], "prepare_input", "DevelopmentDeliveryExecutor", ctx[7])
    report = json.loads(next((ctx[2] / "delivery").glob("*/result.json")).read_text())
    assert report["actions"]["integration"]["commit"] == "c" * 40
    assert report["verification"]["state"] == "pending" and not report["complete"]
    state = json.loads(fake_github.read_text())
    assert state["effects"] == ["merge"]
    state["deployment_api"] = responses()
    fake_github.write_text(json.dumps(state))
    assert (
        journey.hook(
            ctx[6], "prepare_input", "DevelopmentDeliveryExecutor", ctx[7]
        ).context_updates["delivery_complete"]
        == "true"
    )
    report = json.loads(next((ctx[2] / "delivery").glob("*/result.json")).read_text())
    assert report["verification"]["state"] == "succeeded"
    assert report["verification"]["commit"] == "c" * 40
    assert report["verification"]["evidence"]["deployment"]["sha"] == DEPLOYED
    assert json.loads(fake_github.read_text())["effects"] == ["merge"]
