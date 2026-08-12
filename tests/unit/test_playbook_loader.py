"""Tests for playbook schema, loader, and semantic validation."""

from pathlib import Path

import pytest

from cafe.core.human_tasks import HumanTaskCompletion
from cafe.core.playbook import PlaybookDefinition, resolve_playbook_skills
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.ui.human_tasks import (
    resolve_step_human_task,
    resolve_step_human_task_continuation,
    validate_step_human_task_completion,
)


def _write_skill(root: Path, name: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: desc-{name}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def _write_playbook(root: Path, name: str, content: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.yaml").write_text(content, encoding="utf-8")


def test_feedback_target_requires_skill_prompt_exposure(tmp_path: Path) -> None:
    """UT-003: routed feedback reaches a target skill's declared prompt input."""
    builtin_root = tmp_path / "builtin"
    project_root = tmp_path / "project"
    _write_skill(builtin_root / "skills", "source")
    _write_skill(builtin_root / "skills", "receiver")
    _write_playbook(
        project_root / ".cafe" / "playbooks",
        "custom",
        """
playbook: {id: custom}
steps:
  source:
    role: operator
    skill: source
    behavior: {feedback_target: receiver}
    hooks: {prepare_input: [GitHubPRFeedbackSource]}
    on: {await_agent: receiver}
  receiver:
    role: operator
    skill: receiver
    input_artifacts: [workflow_feedback]
    on: {await_agent: _done}
""",
    )
    loader = PlaybookLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="feedback_target.*prompt input.*workflow_feedback"):
        loader.load_model("custom")

    (builtin_root / "skills" / "receiver" / "SKILL.md").write_text(
        """---
name: receiver
description: receiver
workflow:
  prompt_inputs:
    - artifacts: [workflow_feedback]
      placeholder: workflow_feedback_file
      required: false
---

# receiver
""",
        encoding="utf-8",
    )

    assert loader.load_model("custom").model.steps["receiver"].skill == "receiver"


def test_github_feedback_source_requires_declared_feedback_target(tmp_path: Path) -> None:
    """UT-003 — GitHub feedback source steps cannot rely on an implicit destination."""
    builtin_root = tmp_path / "builtin"
    project_root = tmp_path / "project"
    _write_skill(builtin_root / "skills", "source")
    _write_playbook(
        project_root / ".cafe" / "playbooks",
        "custom",
        """
playbook: {id: custom}
steps:
  source:
    role: operator
    skill: source
    hooks: {prepare_input: [GitHubPRFeedbackSource]}
    on: {await_agent: _done}
""",
    )
    loader = PlaybookLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="GitHubPRFeedbackSource.*feedback_target"):
        loader.load_model("custom")


def test_github_feedback_source_rejects_explicit_null_target_override(tmp_path: Path) -> None:
    """UT-003: an explicit null cannot erase direct feedback routing."""
    builtin_root = tmp_path / "builtin"
    project_root = tmp_path / "project"
    _write_skill(builtin_root / "skills", "source")
    _write_skill(builtin_root / "skills", "receiver")
    (builtin_root / "skills" / "receiver" / "SKILL.md").write_text(
        """---
name: receiver
description: receiver
workflow:
  prompt_inputs:
    - artifacts: [workflow_feedback]
      placeholder: workflow_feedback_file
      required: false
---

# receiver
""",
        encoding="utf-8",
    )
    _write_playbook(
        project_root / ".cafe" / "playbooks",
        "custom",
        """
playbook: {id: custom}
behavior: {feedback_target: receiver}
steps:
  source:
    role: operator
    skill: source
    behavior: {feedback_target: null}
    hooks: {prepare_input: [GitHubPRFeedbackSource]}
    on: {await_agent: receiver}
  receiver:
    role: operator
    skill: receiver
    input_artifacts: [workflow_feedback]
    on: {await_agent: _done}
""",
    )
    loader = PlaybookLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="GitHubPRFeedbackSource.*feedback_target"):
        loader.load_model("custom")


@pytest.mark.parametrize("stage", ["before_execute", "after_execute", "publish_output"])
def test_github_feedback_source_rejects_non_intake_stage(tmp_path: Path, stage: str) -> None:
    """UT-003: feedback intake only runs through the prepare-input boundary."""
    builtin_root = tmp_path / "builtin"
    project_root = tmp_path / "project"
    _write_skill(builtin_root / "skills", "source")
    _write_skill(builtin_root / "skills", "receiver")
    (builtin_root / "skills" / "receiver" / "SKILL.md").write_text(
        """---
name: receiver
description: receiver
workflow:
  prompt_inputs:
    - artifacts: [workflow_feedback]
      placeholder: workflow_feedback_file
      required: false
---

# receiver
""",
        encoding="utf-8",
    )
    _write_playbook(
        project_root / ".cafe" / "playbooks",
        "custom",
        f"""
playbook: {{id: custom}}
steps:
  source:
    role: operator
    skill: source
    behavior: {{feedback_target: receiver}}
    hooks: {{{stage}: [GitHubPRFeedbackSource]}}
    on: {{await_agent: receiver}}
  receiver:
    role: operator
    skill: receiver
    input_artifacts: [workflow_feedback]
    on: {{await_agent: _done}}
""",
    )
    loader = PlaybookLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="GitHubPRFeedbackSource.*prepare_input"):
        loader.load_model("custom")


def test_human_feedback_delivery_requires_effective_skill_prompt_exposure(
    tmp_path: Path,
) -> None:
    """UT-006: fixed and selected change routes receive workflow feedback."""
    data_root = Path(__file__).resolve().parents[2] / "src" / "cafe" / "data"
    project_root = tmp_path / "project"
    _write_skill(project_root / ".cafe" / "skills", "repair")
    _write_skill(project_root / ".cafe" / "skills", "target-review")
    (project_root / ".cafe" / "skills" / "target-review" / "SKILL.md").write_text(
        """---
name: target-review
description: target review
workflow:
  human_tasks:
    - id: choose-repair
      pattern: confirm_output
      prompt: Choose how to continue.
      input_schema: decision
      decisions:
        - id: approve
          label: Approve
        - id: request_changes
          label: Request changes
          requires_feedback: true
          requires_target: true
---

# target-review
""",
        encoding="utf-8",
    )
    _write_playbook(
        project_root / ".cafe" / "playbooks",
        "custom",
        """
playbook: {id: custom}
steps:
  fixed-review:
    role: developer
    skill: cafe-pr
    human_tasks:
      - trigger: confirm_output
        task_id: local-review
        outcomes: {approve: _done, request_changes: repair}
        feedback_delivery: {artifact: workflow_feedback, source_kind: local_review}
    on: {confirm_output: fixed-review, await_agent: _done}
  target-review:
    role: developer
    skill: target-review
    human_tasks:
      - trigger: confirm_output
        task_id: choose-repair
        outcomes: {approve: _done}
        allowed_targets: [repair]
        feedback_delivery: {artifact: workflow_feedback, source_kind: local_review}
    on: {confirm_output: target-review, await_agent: _done}
  repair:
    role: developer
    skill: repair
    input_artifacts: [workflow_feedback]
    on: {await_agent: _done}
""",
    )
    loader = PlaybookLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=data_root,
    )

    with pytest.raises(ValueError, match="feedback_delivery.*prompt input.*workflow_feedback"):
        loader.load_model("custom")

    (project_root / ".cafe" / "skills" / "repair" / "SKILL.md").write_text(
        """---
name: repair
description: repair
workflow:
  prompt_inputs:
    - artifacts: [review_feedback, workflow_feedback]
      placeholder: workflow_feedback_file
      required: false
---

# repair
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="feedback_delivery.*prompt input.*workflow_feedback"):
        loader.load_model("custom")

    (project_root / ".cafe" / "skills" / "repair" / "SKILL.md").write_text(
        """---
name: repair
description: repair
workflow:
  prompt_inputs:
    - artifacts: [workflow_feedback]
      placeholder: workflow_feedback_file
      required: false
---

# repair
""",
        encoding="utf-8",
    )

    loaded = loader.load_model("custom")
    assert loaded.model.steps["repair"].skill == "repair"

    playbook = loader.load("custom")
    skill_loader = SkillLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=data_root,
    )
    fixed_policy, fixed_binding, fixed_completion = validate_step_human_task_completion(
        playbook_data=playbook,
        step_name="fixed-review",
        trigger="confirm_output",
        raw_payload={
            "task": "local-review",
            "decision": "request_changes",
            "feedback": "Repair the implementation.",
        },
        skill_loader=skill_loader,
    )
    assert isinstance(fixed_completion, HumanTaskCompletion)
    assert (
        resolve_step_human_task_continuation(
            playbook_data=playbook,
            policy=fixed_policy,
            binding=fixed_binding,
            completion=fixed_completion,
        )
        == "repair"
    )

    target_policy, target_binding, target_completion = validate_step_human_task_completion(
        playbook_data=playbook,
        step_name="target-review",
        trigger="confirm_output",
        raw_payload={
            "task": "choose-repair",
            "decision": "request_changes",
            "target": "repair",
            "feedback": "Repair the implementation.",
        },
        skill_loader=skill_loader,
    )
    assert isinstance(target_completion, HumanTaskCompletion)
    assert (
        resolve_step_human_task_continuation(
            playbook_data=playbook,
            policy=target_policy,
            binding=target_binding,
            completion=target_completion,
        )
        == "repair"
    )


def test_declared_skill_environment_resolves_layers_with_stable_deduplication() -> None:
    """U1/U2 — workflow skills resolve shared, role, and step layers predictably."""
    model = PlaybookDefinition.model_validate(
        {
            "playbook": {"id": "layered"},
            "roles": {"developer": {}},
            "skills": {
                "workflow": {
                    "shared": ["base", "shared"],
                    "roles": {
                        "developer": {"mode": "extend", "skills": ["shared", "role"]}
                    },
                    "steps": {
                        "build": {"mode": "replace", "skills": ["step", "role", "step"]}
                    },
                },
                "chat": {"shared": []},
            },
            "steps": {
                "build": {"role": "developer", "skill": "phase", "on": {"await_agent": "_done"}}
            },
        }
    )

    assert resolve_playbook_skills(
        model, channel="workflow", role="developer", step_name="build"
    ) == ["step", "role"]
    assert resolve_playbook_skills(
        model, channel="chat", role="developer", step_name="build"
    ) == []


def test_skill_environment_reports_missing_channel_and_missing_skill_before_execution(
    tmp_path: Path,
) -> None:
    """U3/U4 — declarations identify absent channels and unresolved support skills."""
    builtin_root = tmp_path / "builtin"
    project_root = tmp_path / "project"
    _write_skill(builtin_root / "skills", "phase")
    _write_playbook(
        project_root / ".cafe" / "playbooks",
        "custom",
        """
playbook: {id: custom}
commands: {prepare: {prompt_for_spec_plan_config: false}}
steps:
  run:
    role: operator
    skill: phase
    on: {await_agent: _done}
""",
    )
    loader = PlaybookLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    loaded = loader.load_model("custom")
    assert any("skills.workflow" in warning for warning in loaded.warnings)
    assert any("skills.chat" in warning for warning in loaded.warnings)
    with pytest.raises(ValueError, match="skills.workflow"):
        loader.load_model("custom", strict=True)

    _write_playbook(
        project_root / ".cafe" / "playbooks",
        "custom",
        """
playbook: {id: custom}
commands: {prepare: {prompt_for_spec_plan_config: false}}
skills:
  workflow: {shared: [missing-support]}
  chat: {shared: []}
steps:
  run:
    role: operator
    skill: phase
    on: {await_agent: _done}
""",
    )
    with pytest.raises(ValueError, match="skills.workflow.shared\\[0\\]"):
        loader.load_model("custom")


def test_skill_environment_rejects_malformed_and_unknown_overlay_scopes(
    tmp_path: Path,
) -> None:
    """U3 — malformed declarations identify the field that authors must repair."""
    with pytest.raises(ValueError, match="skills.workflow.shared"):
        PlaybookDefinition.model_validate(
            {
                "playbook": {"id": "invalid"},
                "skills": {"workflow": {}, "chat": {"shared": []}},
                "steps": {"run": {"role": "operator", "skill": "phase", "on": {"await_agent": "_done"}}},
            }
        )
    with pytest.raises(ValueError, match="skills.workflow.roles.developer.mode"):
        PlaybookDefinition.model_validate(
            {
                "playbook": {"id": "invalid"},
                "roles": {"developer": {}},
                "skills": {
                    "workflow": {
                        "shared": [],
                        "roles": {"developer": {"mode": "merge", "skills": []}},
                    },
                    "chat": {"shared": []},
                },
                "steps": {
                    "run": {"role": "developer", "skill": "phase", "on": {"await_agent": "_done"}}
                },
            }
        )

    builtin_root = tmp_path / "builtin"
    project_root = tmp_path / "project"
    _write_skill(builtin_root / "skills", "phase")
    _write_playbook(
        project_root / ".cafe" / "playbooks",
        "invalid",
        """
playbook: {id: invalid}
commands: {prepare: {prompt_for_spec_plan_config: false}}
skills:
  workflow:
    shared: []
    roles: {missing-role: {mode: extend, skills: []}}
  chat: {shared: []}
steps:
  run: {role: operator, skill: phase, on: {await_agent: _done}}
""",
    )
    loader = PlaybookLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="skills.workflow.roles.missing-role"):
        loader.load_model("invalid")


def test_declared_skills_share_project_override_catalog_with_phase_skills(tmp_path: Path) -> None:
    """U4/I2 — support declarations use the existing project-over-builtin catalog."""
    builtin_root = tmp_path / "builtin"
    project_root = tmp_path / "project"
    _write_skill(builtin_root / "skills", "phase")
    _write_skill(builtin_root / "skills", "support")
    _write_skill(project_root / ".cafe" / "skills", "support")
    _write_skill(project_root / ".cafe" / "skills", "project-extra")
    _write_playbook(
        project_root / ".cafe" / "playbooks",
        "custom",
        """
playbook: {id: custom}
roles: {operator: {}}
commands: {prepare: {prompt_for_spec_plan_config: false}}
skills:
  workflow:
    shared: [support]
    roles: {operator: {mode: extend, skills: [project-extra]}}
  chat: {shared: []}
steps:
  run: {role: operator, skill: phase, on: {await_agent: _done}}
""",
    )
    loader = PlaybookLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    loaded = loader.load_model("custom", strict=True)
    assert resolve_playbook_skills(
        loaded.model, channel="workflow", role="operator", step_name="run"
    ) == ["support", "project-extra"]
    catalog = SkillLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    assert catalog.get_skill_dir("support") == project_root / ".cafe" / "skills" / "support"


@pytest.mark.parametrize(
    "playbook_id",
    ["default", "simple", "tdd", "hotfix", "editorial", "incident", "research"],
)
def test_bundled_playbooks_preserve_declared_skill_environment_parity(
    tmp_path: Path, playbook_id: str
) -> None:
    """I1 — each bundled playbook declares the historic support skill order."""
    builtin_root = Path(__file__).resolve().parents[2] / "src" / "cafe" / "data"
    model = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    ).load_model(playbook_id, strict=True).model

    assert resolve_playbook_skills(
        model, channel="workflow", role=None, step_name=None
    ) == ["cafe-workflow-common", "cafe-github_sync"]
    assert resolve_playbook_skills(model, channel="chat", role=None, step_name=None) == [
        "cafe-common-chat-handoff",
        "cafe-chat-develop-change",
        "cafe-chat-spec-revision",
        "cafe-chat-plan-revision",
        "cafe-chat-alignment-decision",
    ]


def test_playbook_rejects_human_task_outcome_outside_declared_steps(tmp_path: Path) -> None:
    """A task cannot nominate a continuation absent from its own playbook."""
    builtin_root = tmp_path / "builtin"
    skill_dir = builtin_root / "skills" / "reviewer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: reviewer
description: reviewer
workflow:
  human_tasks:
    - id: review-output
      pattern: confirm_output
      prompt: Review output
      input_schema: decision
      decisions:
        - id: confirm
          label: Approve
---
""",
        encoding="utf-8",
    )
    _write_playbook(
        builtin_root / "playbooks",
        "invalid-human-task",
        """
playbook: {id: invalid-human-task}
steps:
  review:
    role: reviewer
    skill: reviewer
    human_tasks:
      - trigger: confirm_output
        task_id: review-output
        outcomes: {confirm: unknown}
    on: {confirm_output: review, await_agent: _done}
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="human task outcome"):
        loader.load_model("invalid-human-task")


def test_playbook_requires_allowed_targets_for_routed_revision(tmp_path: Path) -> None:
    """A target-bearing decision cannot depend on an undeclared workflow route."""
    builtin_root = tmp_path / "builtin"
    skill_dir = builtin_root / "skills" / "reviewer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: reviewer
description: reviewer
workflow:
  human_tasks:
    - id: review-output
      pattern: confirm_output
      prompt: Review output
      input_schema: decision
      decisions:
        - id: confirm
          label: Approve
        - id: revise
          label: Revise
          requires_feedback: true
          requires_target: true
---
""",
        encoding="utf-8",
    )
    _write_playbook(
        builtin_root / "playbooks",
        "missing-human-task-targets",
        """
playbook: {id: missing-human-task-targets}
steps:
  review:
    role: reviewer
    skill: reviewer
    human_tasks:
      - trigger: confirm_output
        task_id: review-output
        outcomes: {confirm: closeout}
    on: {confirm_output: review, await_agent: closeout}
  closeout:
    role: reviewer
    skill: reviewer
    on: {await_agent: _done}
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="requires allowed_targets"):
        loader.load_model("missing-human-task-targets")


def test_playbook_requires_revision_targets_on_the_binding(tmp_path: Path) -> None:
    """A skill cannot silently own the playbook's correction graph."""
    builtin_root = tmp_path / "builtin"
    skill_dir = builtin_root / "skills" / "reviewer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: reviewer
description: reviewer
workflow:
  human_tasks:
    - id: review-output
      pattern: confirm_output
      prompt: Review output
      input_schema: decision
      decisions:
        - id: confirm
          label: Approve
        - id: revise
          label: Revise
          requires_feedback: true
          requires_target: true
      allowed_targets: [review]
---
""",
        encoding="utf-8",
    )
    _write_playbook(
        builtin_root / "playbooks",
        "skill-owned-human-task-targets",
        """
playbook: {id: skill-owned-human-task-targets}
steps:
  review:
    role: reviewer
    skill: reviewer
    human_tasks:
      - trigger: confirm_output
        task_id: review-output
        outcomes: {confirm: closeout}
    on: {confirm_output: review, await_agent: closeout}
  closeout:
    role: reviewer
    skill: reviewer
    on: {await_agent: _done}
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="allowed_targets on the binding"):
        loader.load_model("skill-owned-human-task-targets")


def test_load_uses_project_override(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    global_root = tmp_path / "global"
    project_root = tmp_path / "project"

    _write_skill(builtin_root / "skills", "spec_first")
    _write_playbook(
        builtin_root / "playbooks",
        "default",
        """
playbook: {id: default}
steps:
  spec:
    role: pm
    skill: spec_first
    valid_intents: [confirmed]
    on:
      await_agent: _done
""",
    )
    _write_playbook(
        global_root / "playbooks",
        "default",
        """
playbook: {id: default}
steps:
  spec:
    role: developer
    skill: spec_first
    valid_intents: [confirmed]
    on:
      await_agent: _done
""",
    )
    _write_playbook(
        project_root / ".cafe" / "playbooks",
        "default",
        """
playbook: {id: default}
roles:
  reviewer:
    description: review
steps:
  spec:
    role: reviewer
    skill: spec_first
    valid_intents: [confirmed]
    on:
      await_agent: _done
""",
    )

    loader = PlaybookLoader(
        project_root=project_root,
        global_root=global_root,
        builtin_root=builtin_root,
    )
    result = loader.load_model("default")
    assert result.source == "project"
    assert result.model.steps["spec"].role == "reviewer"


def test_entry_step_initial_input_accepts_declared_providers_and_bindings(tmp_path: Path) -> None:
    """A non-development entry step owns its declared initial-input contract."""
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "intake")
    _write_playbook(
        builtin_root / "playbooks",
        "intake-flow",
        """
playbook: {id: intake-flow}
entry_point: intake
commands:
  prepare:
    prompt_for_spec_plan_config: false
steps:
  intake:
    role: pm
    skill: intake
    output_artifact: intake_brief
    initial_input:
      providers: [manual_text, github_issue]
      bind:
        artifact: intake_brief
        prompt_context: user_input
    hooks:
      prepare_input: [InitialInputProviderResolver]
    on: {await_agent: _done}
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    model = loader.load_model("intake-flow").model

    assert model.steps["intake"].initial_input.providers == ["manual_text", "github_issue"]
    assert model.steps["intake"].initial_input.bind.artifact == "intake_brief"
    assert model.steps["intake"].initial_input.bind.prompt_context == "user_input"


@pytest.mark.parametrize(
    ("initial_input", "error_token"),
    [
        (
            """
    initial_input:
      providers: [manual_text]
      bind: {artifact: unrelated}
""",
            "bind.artifact",
        ),
        (
            """
    initial_input:
      providers: [manual_text, manual_text]
      bind: {artifact: intake_brief}
""",
            "duplicates",
        ),
        (
            """
    initial_input:
      providers: [url]
      bind: {artifact: intake_brief}
""",
            "unsupported provider",
        ),
    ],
)
def test_initial_input_rejects_invalid_provider_or_binding_before_execution(
    tmp_path: Path, initial_input: str, error_token: str
) -> None:
    """U2 — invalid entry declarations fail with their actionable field token."""
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "intake")
    _write_playbook(
        builtin_root / "playbooks",
        "invalid-input",
        f"""
playbook: {{id: invalid-input}}
entry_point: intake
steps:
  intake:
    role: pm
    skill: intake
    output_artifact: intake_brief
{initial_input}    on: {{await_agent: _done}}
commands:
  prepare:
    prompt_for_spec_plan_config: false
""",
    )
    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match=error_token):
        loader.load_model("invalid-input")


def test_initial_input_rejects_empty_artifact_binding_before_execution(tmp_path: Path) -> None:
    """U2 — an empty artifact binding remains invalid even with an empty output name."""
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "intake")
    _write_playbook(
        builtin_root / "playbooks",
        "empty-artifact",
        """
playbook: {id: empty-artifact}
entry_point: intake
steps:
  intake:
    role: pm
    skill: intake
    output_artifact: ""
    initial_input:
      providers: [manual_text]
      bind: {artifact: ""}
    hooks:
      prepare_input: [InitialInputProviderResolver]
    on: {await_agent: _done}
commands:
  prepare:
    prompt_for_spec_plan_config: false
""",
    )
    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="initial_input.bind.artifact"):
        loader.load_model("empty-artifact")


def test_initial_input_rejects_non_entry_or_unimplemented_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """U3 — declarations are entry-only and must map to a host provider."""
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "intake")
    _write_playbook(
        builtin_root / "playbooks",
        "invalid-placement",
        """
playbook: {id: invalid-placement}
entry_point: intake
steps:
  intake:
    role: pm
    skill: intake
    output_artifact: intake_brief
    on: {await_agent: _done}
  refinement:
    role: pm
    skill: intake
    output_artifact: refined_brief
    initial_input:
      providers: [manual_text]
      bind: {artifact: refined_brief}
    on: {await_agent: _done}
""",
    )
    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="only allowed on entry_point"):
        loader.load_model("invalid-placement")

    _write_playbook(
        builtin_root / "playbooks",
        "unimplemented-provider",
        """
playbook: {id: unimplemented-provider}
entry_point: intake
steps:
  intake:
    role: pm
    skill: intake
    output_artifact: intake_brief
    initial_input:
      providers: [github_issue]
      bind: {artifact: intake_brief}
    on: {await_agent: _done}
""",
    )
    monkeypatch.setattr(
        "cafe.core.playbook.registered_initial_input_providers", lambda: frozenset()
    )

    with pytest.raises(ValueError, match="no trusted host implementation"):
        loader.load_model("unimplemented-provider")


@pytest.mark.parametrize("source", ["project", "global"])
def test_initial_input_rejects_legacy_presentation_outside_bundled_playbooks(
    tmp_path: Path, source: str
) -> None:
    """U2/I4 — custom playbooks cannot opt into the built-in empty-input compatibility path."""
    builtin_root = tmp_path / "builtin"
    global_root = tmp_path / "global"
    project_root = tmp_path / "project"
    _write_skill(builtin_root / "skills", "intake")
    playbook_root = (
        project_root / ".cafe" / "playbooks"
        if source == "project"
        else global_root / "playbooks"
    )
    _write_playbook(
        playbook_root,
        "intake-flow",
        """
playbook: {id: intake-flow}
entry_point: intake
steps:
  intake:
    role: pm
    skill: intake
    output_artifact: intake_brief
    initial_input:
      providers: [manual_text]
      bind: {artifact: intake_brief}
      legacy_presentation: true
    hooks:
      prepare_input: [InitialInputProviderResolver]
    on: {await_agent: _done}
""",
    )
    loader = PlaybookLoader(
        project_root=project_root,
        global_root=global_root,
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="legacy_presentation"):
        loader.load_model("intake-flow")


@pytest.mark.parametrize("playbook_name", ["default", "simple", "tdd"])
def test_builtin_entry_steps_use_declared_initial_input_resolver(playbook_name: str) -> None:
    """I3 — built-in development flows retain the provider contract."""
    model = PlaybookLoader().load_model(playbook_name).model
    entry = model.steps[model.entry_point]

    assert entry.initial_input.providers == ["manual_text", "github_issue"]
    assert entry.initial_input.bind.artifact == entry.output_artifact
    assert entry.initial_input.legacy_presentation is True
    assert "InitialInputProviderResolver" in entry.hooks.prepare_input
    assert "GitHubIssueFetcher" not in entry.hooks.prepare_input


def test_initial_input_declaration_requires_generic_resolver_hook(tmp_path: Path) -> None:
    """I4 — a declared provider cannot silently bypass trusted delivery."""
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "intake")
    _write_playbook(
        builtin_root / "playbooks",
        "missing-resolver",
        """
playbook: {id: missing-resolver}
entry_point: intake
commands:
  prepare:
    prompt_for_spec_plan_config: false
steps:
  intake:
    role: pm
    skill: intake
    output_artifact: intake_brief
    initial_input:
      providers: [manual_text]
      bind: {artifact: intake_brief}
    on: {await_agent: _done}
""",
    )
    loader = PlaybookLoader(builtin_root=builtin_root)

    with pytest.raises(ValueError, match="InitialInputProviderResolver"):
        loader.load_model("missing-resolver")


def test_playbook_conversation_locale_supports_bcp47_and_defaults_to_auto(
    tmp_path: Path,
) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "spec_first")
    _write_playbook(
        builtin_root / "playbooks",
        "localized",
        """
playbook: {id: localized, conversation_locale: zh-TW}
steps:
  spec:
    role: pm
    skill: spec_first
    on:
      await_agent: _done
commands:
  prepare:
    prompt_for_spec_plan_config: false
""",
    )
    _write_playbook(
        builtin_root / "playbooks",
        "legacy",
        """
playbook: {id: legacy}
steps:
  spec:
    role: pm
    skill: spec_first
    on:
      await_agent: _done
commands:
  prepare:
    prompt_for_spec_plan_config: false
""",
    )
    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    assert loader.load_model("localized").model.playbook.conversation_locale == "zh-TW"
    assert loader.load_model("legacy").model.playbook.conversation_locale == "auto"


def test_playbook_conversation_locale_rejects_non_bcp47_value(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "spec_first")
    _write_playbook(
        builtin_root / "playbooks",
        "invalid-conversation-locale",
        """
playbook: {id: invalid-conversation-locale, conversation_locale: zh_TW}
steps:
  spec:
    role: pm
    skill: spec_first
    on:
      await_agent: _done
""",
    )
    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="playbook.conversation_locale"):
        loader.load_model("invalid-conversation-locale")


def test_playbook_rejects_ambiguous_locale_field(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "spec_first")
    _write_playbook(
        builtin_root / "playbooks",
        "legacy-locale",
        """
playbook: {id: legacy-locale, locale: en-US}
steps:
  spec:
    role: pm
    skill: spec_first
    on:
      await_agent: _done
""",
    )
    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="locale"):
        loader.load_model("legacy-locale")


def test_load_supports_iteration_aware_skill_and_defaults(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "spec_first")
    _write_skill(builtin_root / "skills", "spec_revise")
    _write_playbook(
        builtin_root / "playbooks",
        "default",
        """
playbook:
  id: default
roles:
  pm:
    description: product
steps:
  spec:
    skill:
      1: spec_first
      default: spec_revise
    role: pm
    valid_intents: [confirmed]
    on:
      await_agent: _done
commands:
  prepare:
    prompt_for_spec_plan_config: false
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    result = loader.load_model("default")

    assert result.model.entry_point == "spec"
    assert result.model.steps["spec"].type == "skill"
    assert result.model.steps["spec"].assignee_type == "agent"
    assert result.model.steps["spec"].auto_snapshot is True
    assert result.model.steps["spec"].skill == {
        "1": "spec_first",
        "default": "spec_revise",
    }


def test_load_supports_step_handoff_label_and_chat_role(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "cafe-brief_first")
    _write_playbook(
        builtin_root / "playbooks",
        "editorial",
        """
playbook:
  id: editorial
roles:
  editor:
    description: editor
  writer:
    description: writer
steps:
  brief:
    skill: cafe-brief_first
    role: editor
    handoff_label: Refine editorial brief
    chat_role: writer
    valid_intents: [confirmed]
    on:
      await_agent: _done
commands:
  prepare:
    prompt_for_spec_plan_config: false
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    result = loader.load_model("editorial")

    assert result.model.steps["brief"].handoff_label == "Refine editorial brief"
    assert result.model.steps["brief"].chat_role == "writer"


def test_load_supports_alignment_config(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "cafe-develop")
    _write_playbook(
        builtin_root / "playbooks",
        "default",
        """
playbook:
  id: default
steps:
  develop:
    skill: cafe-develop
    role: developer
    alignment:
      trigger_policy: policy
      pause_threshold: 5
      note_threshold: 2
      affected_document_categories: [roadmap, positioning]
      reuse_approved: true
    on:
      await_agent: _done
      alignment_checkpoint: develop
commands:
  prepare:
    prompt_for_spec_plan_config: false
""",
    )

    result = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    ).load_model("default")

    alignment = result.model.steps["develop"].alignment
    assert alignment is not None
    assert alignment.pause_threshold == 5
    assert alignment.affected_document_categories == ["roadmap", "positioning"]


def test_load_supports_capability_requests(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "pr")
    _write_playbook(
        builtin_root / "playbooks",
        "default",
        """
playbook:
  id: default
steps:
  pr:
    skill: pr
    role: developer
    capability_requests: [cafe.pr.publish]
    on:
      await_agent: _done
commands:
  prepare:
    prompt_for_spec_plan_config: false
""",
    )

    result = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    ).load_model("default")

    assert result.model.steps["pr"].capability_requests == ["cafe.pr.publish"]


def test_load_rejects_duplicate_capability_requests(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "pr")
    _write_playbook(
        builtin_root / "playbooks",
        "default",
        """
playbook:
  id: default
steps:
  pr:
    skill: pr
    role: developer
    capability_requests: [cafe.pr.publish, cafe.pr.publish]
    on:
      await_agent: _done
""",
    )

    with pytest.raises(ValueError, match="duplicate capability_requests"):
        PlaybookLoader(
            project_root=tmp_path / "project",
            global_root=tmp_path / "global",
            builtin_root=builtin_root,
        ).load_model("default")


def test_load_rejects_unknown_alignment_config_key(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "cafe-develop")
    _write_playbook(
        builtin_root / "playbooks",
        "default",
        """
playbook:
  id: default
steps:
  develop:
    skill: cafe-develop
    role: developer
    alignment:
      unknown: true
    on:
      await_agent: _done
""",
    )

    with pytest.raises(ValueError, match="unknown"):
        PlaybookLoader(
            project_root=tmp_path / "project",
            global_root=tmp_path / "global",
            builtin_root=builtin_root,
        ).load_model("default")


def test_load_rejects_unknown_step_chat_role(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "cafe-brief_first")
    _write_playbook(
        builtin_root / "playbooks",
        "editorial",
        """
playbook:
  id: editorial
roles:
  editor:
    description: editor
steps:
  brief:
    skill: cafe-brief_first
    role: editor
    chat_role: writer
    valid_intents: [confirmed]
    on:
      await_agent: _done
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="unknown chat_role"):
        loader.load_model("editorial")


def test_load_supports_dictionary_script_hook_declarations(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "cafe-plan")
    _write_playbook(
        builtin_root / "playbooks",
        "default",
        """
playbook:
  id: default
roles:
  developer:
    description: dev
steps:
  plan:
    skill: cafe-plan
    role: developer
    hooks:
      after_execute:
        - script: sync_github.sh
          when_intents: [confirmed]
          args:
            phase: plan
            output: "{output_file}"
          schema:
            type: object
            required: [phase, output]
            additionalProperties: false
            properties:
              phase:
                type: string
                enum: [spec, plan]
              output:
                type: string
    valid_intents: [ready_for_review, confirmed]
    on:
      confirm_output: plan
      await_agent: _done
commands:
  prepare:
    prompt_for_spec_plan_config: false
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    result = loader.load_model("default")

    hook_entry = result.model.steps["plan"].hooks.after_execute[0]
    assert isinstance(hook_entry, dict)
    assert hook_entry["script"] == "sync_github.sh"


def test_load_rejects_script_hook_dict_in_unsupported_stage(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "cafe-pr")
    _write_playbook(
        builtin_root / "playbooks",
        "default",
        """
playbook:
  id: default
roles:
  developer:
    description: dev
steps:
  pr:
    skill: cafe-pr
    role: developer
    hooks:
      publish_output:
        - script: sync_pr.sh
    valid_intents: [confirmed]
    on:
      await_agent: _done
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="unsupported stage 'publish_output'"):
        loader.load_model("default")


def test_load_missing_skill_raises(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_playbook(
        builtin_root / "playbooks",
        "bad",
        """
playbook: {id: bad}
steps:
  spec:
    role: pm
    skill: missing_skill
    valid_intents: [confirmed]
    on:
      await_agent: _done
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    with pytest.raises(ValueError, match="unknown skill"):
        loader.load("bad")


def test_load_invalid_allowed_goto_raises(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "cafe-develop")
    _write_playbook(
        builtin_root / "playbooks",
        "bad",
        """
playbook: {id: bad}
steps:
  develop:
    role: developer
    skill: cafe-develop
    valid_intents: [confirmed]
    allowed_goto: [review]
    on:
      await_agent: _done
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    with pytest.raises(ValueError, match="invalid allowed_goto target"):
        loader.load("bad")


def test_load_invalid_transition_raises(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "cafe-review")
    _write_playbook(
        builtin_root / "playbooks",
        "bad",
        """
playbook: {id: bad}
steps:
  review:
    role: reviewer
    skill: cafe-review
    valid_intents: [confirmed]
    on:
      await_agent: not_exist
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    with pytest.raises(ValueError, match="invalid transition"):
        loader.load("bad")


def test_custom_playbook_reports_redundant_tool_warning(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    project_root = tmp_path / "project"
    _write_skill(builtin_root / "skills", "cafe-develop")
    _write_playbook(
        project_root / ".cafe" / "playbooks",
        "custom",
        """
playbook: {id: custom}
steps:
  develop:
    role: developer
    skill: cafe-develop
    allowed_tools: [Bash, "Bash(git:*)"]
    valid_intents: [confirmed]
    on:
      await_agent: _done
""",
    )

    loader = PlaybookLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    result = loader.load_model("custom")

    assert any("redundant allowed_tools entry" in warning for warning in result.warnings)


def test_strict_mode_upgrades_custom_warning_to_error(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    project_root = tmp_path / "project"
    _write_skill(builtin_root / "skills", "cafe-develop")
    _write_playbook(
        project_root / ".cafe" / "playbooks",
        "custom",
        """
playbook: {id: custom}
steps:
  develop:
    role: developer
    skill: cafe-develop
    allowed_tools: [Bash, "Bash(git:*)"]
    valid_intents: [confirmed]
    on:
      await_agent: _done
""",
    )

    loader = PlaybookLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )
    with pytest.raises(ValueError, match="redundant allowed_tools entry"):
        loader.load_model("custom", strict=True)


@pytest.mark.parametrize(
    ("allowed_tools", "should_pass"),
    [
        ("[]", False),
        ('["Bash(cafe verification check:*)"]', True),
        ("[Bash]", True),
        ('["Bash(*)"]', True),
        ('["Bash(cafe verification focus:*)"]', False),
    ],
)
def test_custom_playbook_must_grant_skill_required_tools(
    tmp_path: Path, allowed_tools: str, should_pass: bool
) -> None:
    """Custom bindings fail early unless mandatory skill tools are available."""
    builtin_root = tmp_path / "builtin"
    project_root = tmp_path / "project"
    skill_dir = builtin_root / "skills" / "reviewer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: reviewer
description: reviewer
workflow:
  required_tools:
    - "Bash(cafe verification check:*)"
---
""",
        encoding="utf-8",
    )
    _write_playbook(
        project_root / ".cafe" / "playbooks",
        "custom",
        f"""
playbook: {{id: custom}}
commands: {{prepare: {{prompt_for_spec_plan_config: false}}}}
skills:
  workflow: {{shared: []}}
  chat: {{shared: []}}
steps:
  review:
    role: reviewer
    skill: reviewer
    allowed_tools: {allowed_tools}
    on: {{await_agent: _done}}
""",
    )
    loader = PlaybookLoader(
        project_root=project_root,
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    if should_pass:
        loader.load_model("custom", strict=True)
    else:
        with pytest.raises(ValueError, match="allowed_tools is missing required"):
            loader.load_model("custom", strict=True)


def test_builtin_catalog_includes_hotfix_and_simple() -> None:
    loader = PlaybookLoader()

    playbooks = loader.list_playbooks()

    assert "default" in playbooks
    assert "hotfix" in playbooks
    assert "simple" in playbooks
    assert "editorial" in playbooks
    assert "research" in playbooks
    assert "incident" in playbooks


def test_builtin_playbooks_declare_en_us_conversation_locale() -> None:
    loader = PlaybookLoader()

    for playbook_id in (
        "default",
        "simple",
        "tdd",
        "hotfix",
        "editorial",
        "incident",
        "research",
    ):
        assert loader.load_model(playbook_id).model.playbook.conversation_locale == "en-US"


def test_builtin_hotfix_and_simple_playbooks_load() -> None:
    loader = PlaybookLoader()

    hotfix = loader.load_model("hotfix").model
    tdd = loader.load_model("tdd").model
    simple = loader.load_model("simple").model

    assert hotfix.entry_point == "develop"
    assert list(hotfix.steps.keys()) == ["develop", "review", "pr"]
    assert hotfix.steps["review"].max_iterations == 1
    assert hotfix.steps["develop"].input_artifacts == [
        "review_feedback",
        "pr_result",
        "workflow_feedback",
    ]
    assert tdd.steps["develop"].input_artifacts == [
        "spec",
        "plan",
        "review_feedback",
        "pr_result",
        "workflow_feedback",
    ]

    assert simple.entry_point == "spec"
    assert list(simple.steps.keys()) == ["spec", "develop", "pr"]
    assert simple.steps["develop"].on["await_agent"] == "pr"


def test_legacy_playbook_omits_input_artifact_scope_after_loading(tmp_path: Path) -> None:
    """An omitted field remains distinguishable from explicit input_artifacts: []."""
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "spec_first")
    _write_playbook(
        builtin_root / "playbooks",
        "legacy",
        """
playbook: {id: legacy}
steps:
  draft:
    role: pm
    skill: spec_first
    on:
      await_agent: _done
commands:
  prepare:
    prompt_for_spec_plan_config: false
""",
    )

    loaded = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    ).load("legacy")

    assert "input_artifacts" not in loaded["steps"]["draft"]


def test_playbook_rejects_explicit_null_input_artifact_scope(tmp_path: Path) -> None:
    """Explicit null must not bypass an isolated artifact scope."""
    builtin_root = tmp_path / "builtin"
    _write_skill(builtin_root / "skills", "spec_first")
    _write_playbook(
        builtin_root / "playbooks",
        "invalid-null-scope",
        """
playbook: {id: invalid-null-scope}
steps:
  draft:
    role: pm
    skill: spec_first
    input_artifacts: null
    on:
      await_agent: _done
""",
    )

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=builtin_root,
    )

    with pytest.raises(ValueError, match="input_artifacts must be a list when specified"):
        loader.load("invalid-null-scope")


def test_builtin_non_software_playbooks_define_non_default_handoff_metadata() -> None:
    loader = PlaybookLoader()

    research = loader.load_model("research").model
    editorial = loader.load_model("editorial").model

    assert research.steps["question"].handoff_label == "Refine research question"
    assert research.steps["question"].chat_role == "researcher"
    assert editorial.steps["draft"].handoff_label == "Continue manuscript draft"
    assert editorial.steps["draft"].chat_role == "writer"
    assert "implementation" not in (research.steps["question"].handoff_label or "").lower()
    assert "requirements" not in (editorial.steps["draft"].handoff_label or "").lower()


def test_builtin_user_handoffs_resolve_nonempty_declared_policies() -> None:
    """Builtin user pauses must not fall back to implicit development behavior."""
    loader = PlaybookLoader()
    triggers = {"confirm_output", "need_clarification", "no_changes_needed"}

    for playbook_id in ("default", "simple", "tdd", "hotfix", "editorial", "incident", "research"):
        playbook = loader.load(playbook_id)
        for step_name, step in playbook["steps"].items():
            for trigger in triggers.intersection(step.get("on", {})):
                policy, binding = resolve_step_human_task(
                    playbook_data=playbook,
                    step_name=step_name,
                    trigger=trigger,
                )

                assert policy.prompt
                assert binding.task_id == policy.id
