"""Contract tests for bundled write-cafe-phase guidance."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "write-cafe-phase"


def test_write_cafe_phase_repairs_only_its_declarative_layer() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    spec = (SKILL_ROOT / "references" / "skill-spec.md").read_text(encoding="utf-8")
    normalized_skill = " ".join(skill.split())
    normalized_spec = " ".join(spec.split())

    assert "## Declarative Repair Boundary" in skill
    assert ".cafe/skills/<skill-name>/" in skill
    assert "src/cafe/data/skills/<skill-name>/" in skill
    assert "return the classification to the driver for `write-cafe-playbook`" in normalized_skill
    assert "return a CAFE core-defect diagnosis" in normalized_skill
    assert "Do not edit driver/meta skills such as `use-cafe-workflow`" in normalized_skill
    assert "This skill is not a general CAFE self-modifier" in normalized_skill
    assert "Frontmatter and Repair Boundary" in spec
    assert "create an implicit `write-cafe-driver` fallback" in normalized_spec
    assert "External issue creation, comments, or closing require explicit user authorization" in normalized_spec
    assert "playbook `skills.workflow`" in spec
    assert "playbook `skills.chat`" in spec
    assert "workflow.execution_profile" in skill
    assert "provider-neutral execution-requirement metadata" in normalized_spec
    assert "Do not name a CLI provider, model, pricing tier" in normalized_spec
    assert "conservatively aggregate every declared variant" in normalized_spec
    assert "### Workflow metadata contract" in spec
    assert "### Human-task policy contract" in spec
    assert "Prompt references name files under `references/`" in normalized_spec
    assert "A template catalog belongs to the owning skill's" in normalized_spec
    assert "A mandatory HumanTask binding with `feedback_delivery`" in normalized_spec


def test_write_cafe_phase_requires_confirmed_supporting_skill_selection() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    spec = (SKILL_ROOT / "references" / "skill-spec.md").read_text(encoding="utf-8")
    normalized_skill = " ".join(skill.split())

    native = spec.index("**CLI-native Skill**")
    open_source = spec.index("**Open-source Skill**")
    self_authored = spec.index("**Self-authored**")

    assert native < open_source < self_authored
    assert "## Supporting Skill Selection" in skill
    assert "independently for every supported target CLI" in normalized_skill
    assert "Stop at the first suitable tier for that CLI" in normalized_skill
    assert "unresolved CLIs may continue to lower tiers" in normalized_skill
    assert "Build one proposed selection matrix covering every target CLI" in normalized_skill
    assert "advance only that CLI to its next tier" in normalized_skill
    assert "wait for explicit confirmation" in normalized_skill
    assert "authoring time" in normalized_skill
    assert "Do not make workflow execution search the network" in normalized_skill
    normalized_spec = " ".join(spec.split())
    assert "Evaluate each target CLI independently" in normalized_spec
    assert "only for unresolved CLIs" in normalized_spec
    assert "only after native and open-source options are unsuitable" in normalized_spec
    assert "Prepare one proposed selection matrix covering every target CLI" in normalized_spec
    assert "before adopting, installing, vendoring, or writing" in normalized_spec
    assert (
        "obtain explicit user confirmation before adopting, installing, vendoring, or writing"
        in normalized_spec
    )
    assert "Runtime does not search the network, download mutable latest content, or guess substitutes" in normalized_spec
    assert "Runtime resolves shared skills" in normalized_spec
    assert "does not search the network" in normalized_spec
    assert "update the complete matrix and ask again" in normalized_spec


def test_write_cafe_phase_requires_interrupt_safe_batch_progress() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    spec = (SKILL_ROOT / "references" / "skill-spec.md").read_text(encoding="utf-8")
    normalized_skill = " ".join(skill.split())
    normalized_spec = " ".join(spec.split())

    assert "version: 2.12.0" in skill
    assert "## Interruptible and Batch Phases" in skill
    assert "it is not a per-target resume ledger" in normalized_skill
    assert (
        "output template, downstream consumers, finalizers, and publish hooks"
        in normalized_skill
    )
    assert "per-target/stage dependency fingerprints" in normalized_skill
    assert "including relevant dirty or mutable content" in normalized_skill
    assert "loses at most the unit currently running" in normalized_skill
    assert "when impact is ambiguous" in normalized_skill
    assert "a separate ledger hashes the complete final-artifact bytes" in normalized_skill
    assert (
        "canonical domain-payload projection that excludes ledger/finalization metadata"
        in normalized_skill
    )
    assert "Record the algorithm and scope/projection version" in normalized_skill
    assert "post-success runtime/host hook or later retention policy" in normalized_skill
    assert "migrate only deterministic local evidence" in normalized_skill
    assert (
        "Phase preparation refreshes an existing iteration's derived `checklist.md`"
        in normalized_skill
    )
    assert "new or changed gates reopen" in normalized_skill
    assert "do not add an internal infinite retry loop" in normalized_skill

    assert "## 17. Interruptible and Batch Phase Checkpoint/Resume Contract" in spec
    assert "A simple atomic phase does not need a ledger" in normalized_spec
    assert "default interruption budget is at most one target" in normalized_spec
    assert "If the output is exact-shape, public" in normalized_spec
    assert "dependency fingerprints including dirty/untracked content" in normalized_spec
    assert "Trust a completed stage only when the fingerprint" in normalized_spec
    assert "otherwise reopen all dependent rows" in normalized_spec
    assert "a finalized digest receipt" in normalized_spec
    assert "Do not hash a file that contains its own digest field" in normalized_spec
    assert "scope/projection version" in normalized_spec
    assert "cleanup belongs to a post-success hook or retention policy" in normalized_spec
    assert "migrate only deterministic local evidence" in normalized_spec
    assert "leave review, approval, push, import, or publication pending" in normalized_spec
    assert "Critical resume behavior belongs in the active `SKILL.md`" in normalized_spec


def test_write_cafe_phase_requires_topology_neutral_todo_routing() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    spec = (SKILL_ROOT / "references" / "skill-spec.md").read_text(encoding="utf-8")
    normalized_skill = " ".join(skill.split())
    normalized_spec = " ".join(spec.split())

    assert "Custom workflows may use any safe artifact" in normalized_skill
    assert "`feedback_todo_source`" in normalized_skill
    assert "`feedback_todo_id_prefix`" in normalized_skill
    assert "generic runtime must not infer" in normalized_skill
    assert "custom artifact" in normalized_spec
    assert "stable ID prefix" in normalized_spec
    assert "Never infer correction mode from destination, artifact name" in normalized_spec


def test_write_cafe_phase_keeps_incoming_plans_immutable() -> None:
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    spec = (SKILL_ROOT / "references" / "skill-spec.md").read_text(encoding="utf-8")

    assert "runtime `checklist.md` owns the derived gates" in skill
    assert "consumer `{output_file}` owns the only mutable per-item ledger" in skill
    assert "execution phase marks those same items `- [x]`" not in skill
    assert "The accepted `{plan_file}` is immutable input" in spec
    assert "its `{output_file}` `## Todo Progress` ledger" in spec
    assert "marks the incoming plan's checkboxes" not in spec
    assert "incoming plan remains immutable" in spec or "immutable incoming" in spec
