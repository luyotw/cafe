"""Resolve notification presentation from actual selected workflow contributors."""

from typing import Any, Mapping

from cafe.catalogs.resolver import global_catalog_lock
from cafe.core.human_task_notifications import NotificationPresentation
from cafe.core.playbook import resolve_playbook_skills
from cafe.core.runtime_locales import owner_catalog_renderer
from cafe.skills.loader import SkillLoader
from cafe.skills.selectors import resolve_skill_selector
from cafe.skills.workflow_composition import resolve_step_workflow_composition


def resolve_step_notification_presentation(
    *,
    playbook_data: Mapping[str, Any],
    step_name: str,
    locale: str | None,
    task_id: str | None = None,
    iteration: int = 1,
    skill_loader: SkillLoader | None = None,
) -> NotificationPresentation:
    """Use primary step copy and the contributing declaration's actual task copy.

    No step, skill or task-name registry infers ownership. The structural reader
    keeps unrelated task presentation out of notification resolution; the selected
    notification references still strictly validate both owner resources here.
    """
    step = playbook_data.get("steps", {}).get(step_name, {})
    if not step.get("skill"):
        return NotificationPresentation()
    loader = skill_loader or SkillLoader(resolve_presentation=False)
    with global_catalog_lock(loader.global_root):
        composition = resolve_step_workflow_composition(
            loader,
            primary_skill=resolve_skill_selector(step["skill"], iteration),
            step_name=step_name,
            workflow_skills=resolve_playbook_skills(
                playbook_data,
                channel="workflow",
                step_name=step_name,
                role=step.get("role"),
            ),
        )
        step_label = action_label = None
        renderers = {}

        def render(contributor, reference):
            root = contributor.source.skill_root.resolve() / "locales"
            if root not in renderers:
                renderers[root] = owner_catalog_renderer(root)
            return renderers[root](reference.message_key, locale=locale)

        primary = composition.contributors[0]
        copy = primary.declaration.notification
        if copy and copy.step_label:
            step_label = render(primary, copy.step_label)
        # Ownership and identical-task precedence come exclusively from the
        # composition that selected the effective policy, including no label.
        producer = composition.human_task_producers.get(task_id)
        copy = producer.declaration.notification if producer else None
        reference = copy.task_labels.get(task_id) if copy else None
        if reference:
            action_label = render(producer, reference)
        return NotificationPresentation(step_label=step_label, action_label=action_label)
