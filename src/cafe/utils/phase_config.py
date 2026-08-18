"""Utilities for loading and validating phase-level execution config."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

import yaml


SOURCE_WORKTREE = "worktree"
SOURCE_REPO = "repo"

SUPPORTED_CLI_VALUES = {"claude", "gemini", "cursor-agent", "codex", "copilot"}


@dataclass(frozen=True)
class PhaseStepModelResolution:
    """Resolved phase config fields for one workflow step."""

    name: Optional[str]
    role: Optional[str]
    clis: tuple[tuple[str, Optional[str]], ...]
    model: Optional[str]
    source: Optional[str]
    chain: tuple[str, ...]
    name_source: Optional[str] = None
    role_source: Optional[str] = None
    clis_source: Optional[str] = None


def _as_stripped_scalar(value: object, *, field: str, step: str, source: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"invalid phase config for step '{step}' in '{source}': field='{field}': expected non-empty string"
        )

    model_value = value.strip()
    if not model_value:
        raise ValueError(
            f"invalid phase config for step '{step}' in '{source}': field='{field}': expected non-empty string"
        )
    return model_value


def _validation_error(source: str, *, step: str, field: str, detail: str) -> ValueError:
    return ValueError(
        f"invalid phase config in '{source}': step='{step}': field='{field}': {detail}"
    )


def _load_yaml_file(path: Path) -> Mapping:
    """Load YAML from `path` and validate top-level mapping shape."""
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive parse guard
        raise _validation_error(
            path.as_posix(),
            step="unknown",
            field="document",
            detail=f"failed to parse YAML: {exc}",
        ) from exc

    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise _validation_error(
            path.as_posix(),
            step="unknown",
            field="root",
            detail="expected top-level mapping",
        )
    return payload


def _validate_phase_doc(payload: Mapping, source: str) -> None:
    """Validate the whole YAML document, including inactive step keys."""
    for step_key, step_config in payload.items():
        if not isinstance(step_key, str):
            raise _validation_error(
                source,
                step="unknown",
                field="step",
                detail="top-level keys must be strings",
            )
        step_name = step_key.strip()
        if not step_name:
            raise _validation_error(
                source,
                step="unknown",
                field="step",
                detail="step name cannot be empty",
            )

        if not isinstance(step_config, Mapping):
            raise ValueError(
                f"invalid phase config for step '{step_name}' in '{source}': field='{step_name}': expected mapping"
            )

        if not step_config:
            continue

        invalid_fields = set(step_config.keys()) - {"name", "role", "clis"}
        if invalid_fields:
            field = next(iter(sorted(invalid_fields)))
            raise ValueError(
                f"invalid phase config for step '{step_name}' in '{source}': field='{step_name}.{field}': unknown field"
            )

        raw_name = step_config.get("name")
        if raw_name is not None:
            _as_stripped_scalar(raw_name, field=f"{step_name}.name", step=step_name, source=source)

        raw_role = step_config.get("role")
        if raw_role is not None:
            _as_stripped_scalar(raw_role, field=f"{step_name}.role", step=step_name, source=source)

        clis = step_config.get("clis")
        if clis is not None:
            if not isinstance(clis, list):
                raise ValueError(
                    f"invalid phase config for step '{step_name}' in '{source}': field='{step_name}.clis': expected non-empty list"
                )
            if not clis:
                raise ValueError(
                    f"invalid phase config for step '{step_name}' in '{source}': field='{step_name}.clis': expected non-empty list"
                )

            seen: set[str] = set()
            for index, entry in enumerate(clis):
                if not isinstance(entry, Mapping):
                    raise ValueError(
                        f"invalid phase config for step '{step_name}' in '{source}': field='{step_name}.clis[{index}]': expected map"
                    )
                unknown = set(entry.keys()) - {"cli", "model"}
                if unknown:
                    extra = next(iter(sorted(unknown)))
                    raise ValueError(
                        f"invalid phase config for step '{step_name}' in '{source}': field='{step_name}.clis[{index}].{extra}': unknown key '{extra}'"
                    )

                cli = _as_stripped_scalar(
                    entry.get("cli"),
                    field=f"{step_name}.clis[{index}].cli",
                    step=step_name,
                    source=source,
                )
                if cli is None:
                    raise ValueError(
                        f"invalid phase config for step '{step_name}' in '{source}': field='{step_name}.clis[{index}].cli': expected non-empty string"
                    )
                if cli not in SUPPORTED_CLI_VALUES:
                    raise ValueError(
                        f"invalid phase config for step '{step_name}' in '{source}': field='{step_name}.clis[{index}].cli': unsupported cli '{cli}'"
                    )
                if cli in seen:
                    raise ValueError(
                        f"invalid phase config for step '{step_name}' in '{source}': field='{step_name}.clis': duplicate cli '{cli}'"
                    )
                seen.add(cli)

                _as_stripped_scalar(
                    entry.get("model"),
                    field=f"{step_name}.clis[{index}].model",
                    step=step_name,
                    source=source,
                )
                if entry.get("model") is None:
                    raise _validation_error(
                        source,
                        step=step_name,
                        field=f"{step_name}.clis[{index}].model",
                        detail="expected non-empty string",
                    )


def _resolved_step_payload(
    *,
    payload: Mapping,
    step_name: str,
    source: str,
) -> tuple[Optional[str], Optional[str], Optional[tuple[tuple[str, Optional[str]], ...]], Optional[str]]:
    if not step_name:
        return None, None, None, None

    step_config = payload.get(step_name)
    if not isinstance(step_config, Mapping):
        return None, None, None, None

    name = _as_stripped_scalar(
        step_config.get("name"),
        field=f"{step_name}.name",
        step=step_name,
        source=source,
    )
    role = _as_stripped_scalar(
        step_config.get("role"),
        field=f"{step_name}.role",
        step=step_name,
        source=source,
    )

    clis = None
    raw_clis = step_config.get("clis")
    if raw_clis:
        parsed: list[tuple[str, Optional[str]]] = []
        for index, entry in enumerate(raw_clis):
            if not isinstance(entry, Mapping):
                raise ValueError(
                    f"invalid phase config for step '{step_name}' in '{source}': field='{step_name}.clis[{index}]': expected map"
                )
            cli = _as_stripped_scalar(
                entry.get("cli"),
                field=f"{step_name}.clis[{index}].cli",
                step=step_name,
                source=source,
            )
            if cli is None:
                raise ValueError(
                    f"invalid phase config for step '{step_name}' in '{source}': field='{step_name}.clis[{index}].cli': expected non-empty string"
                )
            model = _as_stripped_scalar(
                entry.get("model"),
                field=f"{step_name}.clis[{index}].model",
                step=step_name,
                source=source,
            )
            parsed.append((cli, model))
        clis = tuple(parsed)

    return name, role, clis, model_from_cli_clis(clis)


def model_from_cli_clis(clis: Optional[tuple[tuple[str, Optional[str]], ...]]) -> Optional[str]:
    if not clis:
        return None
    return clis[0][1]


def load_phase_step_model(
    *,
    step_name: str,
    local_path: Optional[Path],
    repo_path: Optional[Path] = None,
) -> PhaseStepModelResolution:
    """Resolve model + metadata across phase config source chain.

    Resolution order (high -> low):
    1. local worktree override (`local_path`)
    2. repo fallback (`repo_path`)
    """
    if not isinstance(step_name, str) or not step_name.strip():
        raise ValueError("invalid phase config step_name: expected non-empty string")

    active_step = step_name.strip()
    chain: list[str] = []
    payloads: dict[str, tuple[Mapping, Path]] = {}

    for source_label, path in (
        (SOURCE_WORKTREE, local_path),
        (SOURCE_REPO, repo_path),
    ):
        if path is None or not path.exists():
            continue

        payload = _load_yaml_file(path)
        _validate_phase_doc(payload, source=path.as_posix())
        chain.append(source_label)
        payloads[source_label] = (payload, path)

    resolved_name: Optional[str] = None
    resolved_role: Optional[str] = None
    resolved_clis: Optional[tuple[tuple[str, Optional[str]], ...]] = None
    resolved_model: Optional[str] = None
    resolved_source: Optional[str] = None
    resolved_name_source: Optional[str] = None
    resolved_role_source: Optional[str] = None
    resolved_clis_source: Optional[str] = None

    for source_label in chain:
        payload, source_path = payloads[source_label]
        name, role, clis, model = _resolved_step_payload(
            payload=payload,
            step_name=active_step,
            source=source_path.as_posix(),
        )
        if resolved_name is None and name is not None:
            resolved_name = name
            resolved_name_source = source_label
            resolved_source = source_label
        if resolved_role is None and role is not None:
            resolved_role = role
            resolved_role_source = source_label
            if resolved_source is None:
                resolved_source = source_label
        if resolved_clis is None and clis is not None:
            resolved_clis = clis
            resolved_model = model
            resolved_clis_source = source_label
            if resolved_source is None:
                resolved_source = source_label

    error_path = next(
        (path for path in (local_path, repo_path) if path is not None and path.exists()),
        local_path or repo_path or Path(".cafe/phases.yaml"),
    )
    if not chain or not any(active_step in payload for payload, _path in payloads.values()):
        raise _validation_error(
            error_path.as_posix(),
            step=active_step,
            field=active_step,
            detail="required step configuration is missing",
        )
    if resolved_name is None:
        raise _validation_error(
            error_path.as_posix(),
            step=active_step,
            field=f"{active_step}.name",
            detail="required agent name is missing",
        )
    if resolved_clis is None:
        raise _validation_error(
            error_path.as_posix(),
            step=active_step,
            field=f"{active_step}.clis",
            detail="required execution chain is missing",
        )

    return PhaseStepModelResolution(
        name=resolved_name,
        role=resolved_role,
        clis=resolved_clis or (),
        model=resolved_model,
        source=resolved_source,
        chain=tuple(chain),
        name_source=resolved_name_source,
        role_source=resolved_role_source,
        clis_source=resolved_clis_source,
    )
