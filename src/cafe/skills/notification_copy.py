"""Resolve notification presentation from actual selected workflow contributors."""

from typing import Any, Mapping

from cafe.catalogs.resolver import global_catalog_lock
from cafe.core.human_task_notifications import NotificationPresentation
from cafe.core.playbook import resolve_playbook_skills
from cafe.core.runtime_locales import render_text
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
        task_owner_found = False
        for contributor in composition.contributors:
            declaration = contributor.declaration
            copy = declaration.notification
            root = contributor.source.skill_root.resolve() / "locales"
            if contributor.primary and copy and copy.step_label:
                step_label = render_text(
                    copy.step_label.message_key, locale=locale, catalog_root=root
                )
            # Composition keeps the first identical task declaration; use that
            # same producer, including when it deliberately has no action label.
            if not task_owner_found and any(task.id == task_id for task in declaration.human_tasks):
                task_owner_found = True
                reference = copy.task_labels.get(task_id) if copy else None
                if reference:
                    action_label = render_text(
                        reference.message_key, locale=locale, catalog_root=root
                    )
        return NotificationPresentation(step_label=step_label, action_label=action_label)
