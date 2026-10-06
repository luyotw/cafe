"""Isolated, reproducible delivery fixtures for implementation tests and QA.

Git history setup represents external human work. GitHub responses model external
PR state at the process boundary; no live PR is published or merged.
"""

import json
import subprocess
from pathlib import Path

import yaml

from cafe.core.blackboard import ArtifactEntry, ArtifactKind, BlackboardStore
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.integration import IntegrationService
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui.human_tasks import apply_human_task_payload


def create_journey(root: Path, monkeypatch):
    root.mkdir(exist_ok=True)
    monkeypatch.chdir(root)
    monkeypatch.setattr("cafe.utils.config.get_global_cafe_dir", lambda **kw: root / "global")

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "human@example.invalid")
    git("config", "user.name", "Human fixture")
    (root / ".gitignore").write_text(".cafe/\nglobal/\n")
    git("add", ".gitignore")
    git("commit", "-m", "baseline")
    base = git("rev-parse", "HEAD")
    git("checkout", "-b", "feature")
    (root / "change").write_text("approved\n")
    git("add", "change")
    git("commit", "-m", "approved change")
    source = git("rev-parse", "HEAD")
    skill = root / ".cafe/skills/custom-delivery/SKILL.md"
    skill.parent.mkdir(parents=True)
    tasks = []
    for name, decisions in [
        ("judge", ["ship", "fix"]),
        ("choose", ["confirm", "revise"]),
        ("human-delivery", ["performed", "already_performed", "blocked"]),
    ]:
        tasks.append(
            dict(
                id=name,
                pattern="confirm_output",
                prompt="Review the task identity and choose an outcome",
                input_schema="decision",
                decisions=[dict(id=d, label=d) for d in decisions],
            )
        )
    skill.write_text(
        "---\n"
        + yaml.safe_dump(
            dict(
                name="custom-delivery",
                description="isolated journey",
                workflow=dict(human_tasks=tasks),
            )
        )
        + "---\n"
    )

    def step(**extra):
        return dict(skill="custom-delivery", role="operator", **extra)

    declaration = dict(
        review_step="verdict",
        review_task="judge",
        accepted_decisions=["ship"],
        source_artifact="approved_delta",
        source_step="forge",
        delivery_artifact="delivery_note",
        delivery_step="package",
        selection_step="destination",
        selection_task="choose",
        action_step="land",
        action_task="human-delivery",
        correction_step="forge",
        verified_continuation="_done",
    )
    data = dict(
        playbook=dict(
            id="custom-delivery",
            applicability=dict(
                summary="Verified custom delivery",
                use_when=["Human integration is required"],
                avoid_when=["Review-only delivery"],
            ),
        ),
        roles=dict(operator={}),
        skills=dict(workflow=dict(shared=[]), chat=dict(shared=[])),
        commands=dict(prepare=dict(prompt_for_spec_plan_config=False)),
        integration=declaration,
        steps=dict(
            forge=step(
                output_artifact="code_delta",
                workspace_artifact="approved_delta",
                on=dict(await_agent="package"),
            ),
            package=step(output_artifact="delivery_note", on=dict(await_agent="verdict")),
            verdict=step(
                assignee_type="human",
                human_tasks=[
                    dict(
                        trigger="initial",
                        task_id="judge",
                        outcomes=dict(ship="destination", fix="forge"),
                    )
                ],
                on=dict(await_agent="destination"),
            ),
            destination=step(
                assignee_type="human",
                human_tasks=[
                    dict(
                        trigger="initial",
                        task_id="choose",
                        outcomes=dict(confirm="land", revise="destination"),
                    )
                ],
                on=dict(await_agent="land"),
            ),
            land=step(
                assignee_type="human",
                human_tasks=[
                    dict(
                        trigger="initial",
                        task_id="human-delivery",
                        outcomes=dict(performed="land", already_performed="land", blocked="land"),
                    )
                ],
                on=dict(await_agent="_done"),
            ),
        ),
    )
    playbook_path = root / ".cafe/playbooks/custom-delivery.yaml"
    playbook_path.parent.mkdir(parents=True)
    playbook_path.write_text(yaml.safe_dump(data, sort_keys=False))
    playbook = PlaybookLoader(project_root=root).load("custom-delivery", strict=True)
    issue_dir = root / ".cafe/issues/delivery"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text("playbook: custom-delivery\npr:\n  auto_create: false\n")
    source_file = issue_dir / "reviewed.json"
    source_file.write_text(
        json.dumps(
            dict(
                schema_version=1,
                name="approved_delta",
                version=1,
                repository=str(root),
                base_sha=base,
                head_sha=source,
                changed_files=[dict(status="A", path="change")],
                producer_step="forge",
            )
        )
    )
    prepared_file = issue_dir / "prepared.md"
    prepared_file.write_text("Prepared local or published delivery for the reviewed source.\n")
    board_store = BlackboardStore(issue_dir)
    board = board_store.load_or_create("verdict", playbook_id="custom-delivery")
    for name, producer, path, kind in [
        ("approved_delta", "forge", source_file, ArtifactKind.WORKSPACE),
        ("delivery_note", "package", prepared_file, ArtifactKind.DOCUMENT),
    ]:
        board_store.put_artifact(
            board,
            ArtifactEntry(name=name, updated_by=producer, path=str(path), kind=kind, version=1),
        )

    class Journey:
        def __init__(self):
            self.root, self.issue_dir, self.playbook, self.source, self.base = (
                root,
                issue_dir,
                playbook,
                source,
                base,
            )
            self.git = git
            self.observation = dict(
                repository="owner/repo",
                pr=17,
                source_commit=source,
                target_branch="main",
                merged=False,
                state="open",
                merge_commit=None,
            )

        def state(self):
            return board_store.load_or_create("verdict", playbook_id="custom-delivery")

        def service(self):
            return IntegrationService(issue_dir, playbook, self.state())

        def runtime(self):
            return BlackboardWorkflowRuntime(
                issue_dir=issue_dir,
                playbook=playbook,
                executor=lambda *a, **kw: (_ for _ in ()).throw(
                    AssertionError("No agent or integration executor expected")
                ),
            )

        def pending(self):
            return [
                t for t in HumanTaskRecordStore(issue_dir).tasks() if t.status.value == "pending"
            ][-1]

        def complete(self, decision, task=None):
            task = task or self.pending()
            return apply_human_task_payload(
                issue_dir=issue_dir,
                playbook_data=playbook,
                blackboard=self.state(),
                from_step=task.step,
                trigger=task.trigger,
                raw_payload=dict(human_task_id=task.id, task=task.policy_id, decision=decision),
                source="fixture-human",
            )

        def select(self, target="local_branch"):
            if target == "github_pr":
                snapshot = self.service().records.read()["reviews"]
                # Fixture publication is an existing trusted receipt, never a network mutation.
                with self.service().records.transaction() as record:
                    for value in record["reviews"].values():
                        value["publication"] = dict(
                            capability="cafe.pr.publish",
                            success=True,
                            step="package",
                            outputs=dict(
                                pr_url="https://github.com/owner/repo/pull/17", pr_number=17
                            ),
                        )
                return self.service().propose(
                    target=target, repository="owner/repo", target_branch="main", pr=17
                )
            return self.service().propose(
                target=target, repository=str(root), target_branch="main", feature_branch="feature"
            )

        def human_integrate(self, target="local_branch"):
            if target == "local_branch":
                git("update-ref", "refs/heads/main", source)
            else:
                self.observation.update(merged=True, state="closed", merge_commit="c" * 40)

    journey = Journey()
    journey.runtime().run(start_step="verdict")
    return journey
