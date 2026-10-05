"""Versioned semantic evidence for existing iteration and freshness records."""
from typing import Annotated
from pydantic import Field, StringConstraints

from .models import Context, StrictModel
from .resolver import resolve
from .fingerprint import material_digest

Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class Snapshot(StrictModel):
    version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    context: Context
    digest: Digest


def snapshot(context: Context) -> dict:
    view=resolve(context)
    return Snapshot(context=view.context,digest=material_digest(view)).model_dump(mode='json')


def compare_snapshot(previous, current) -> str:
    try:
        old=Snapshot.model_validate(previous)
        live=Snapshot.model_validate(current)
    except ValueError:
        return 'unknown'
    return 'same_semantics' if old.digest==live.digest else 'material_change'
