"""Hash effective behavior, excluding provenance and editorial metadata."""

import hashlib
import json

from .resolver import View


def material_projection(view: View) -> dict:
    context = view.context.model_dump(mode="json")
    for key, value in context.items():
        if isinstance(value, list):
            context[key] = sorted(set(value))
    entries = []
    for e in view.entries:
        if e.status != "active":
            continue
        r = e.record
        scope = e.scope.model_dump(mode="json")
        # Full applicability is material, including variants other than the selected one.
        variants = [v.scope.model_dump(mode="json") for v in r.variants]
        for item in [scope, *variants]:
            for key in item:
                item[key] = sorted(item[key])
        boundary = e.boundary.model_dump(mode="json") if e.boundary else None
        if boundary and boundary["kind"] == "numeric":
            boundary["limits"] = sorted(boundary["limits"], key=lambda quantity: quantity["name"])
        entries.append(
            dict(
                id=e.id,
                enforcement=e.enforcement,
                scope=scope,
                applicability=sorted(variants, key=lambda v: json.dumps(v, sort_keys=True)),
                boundary=boundary,
                validity=e.validity,
                trigger=r.trigger,
                impact=r.impact,
                mitigation=r.mitigation,
                escalation=r.escalation,
                permission_boundary=r.permission_boundary,
                replacements=sorted(r.replacements),
                replacement_behavior=r.replacement_behavior,
            )
        )
    return dict(version=1, context=context, entries=entries)


def material_digest(view: View) -> str:
    raw = json.dumps(
        material_projection(view), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(raw.encode()).hexdigest()
