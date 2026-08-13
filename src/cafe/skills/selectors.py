"""Shared iteration-aware skill selector resolution."""

from __future__ import annotations

from typing import Mapping


def skill_selector_names(selector: str | Mapping[str, str]) -> tuple[str, ...]:
    """Return every unique skill named by a selector in deterministic order."""
    if isinstance(selector, str):
        token = selector.strip()
        if not token:
            raise ValueError("skill selector must not be empty")
        return (token,)
    if not selector:
        raise ValueError("skill selector mapping must not be empty")

    numbered = sorted(
        ((int(str(key)), value) for key, value in selector.items() if str(key).isdigit()),
        key=lambda item: item[0],
    )
    ordered_values = [value for _, value in numbered]
    if "default" in selector:
        ordered_values.append(selector["default"])
    ordered_values.extend(
        value
        for key, value in sorted(selector.items(), key=lambda item: str(item[0]))
        if str(key) != "default" and not str(key).isdigit()
    )
    names: list[str] = []
    for value in ordered_values:
        token = str(value).strip()
        if not token:
            raise ValueError("skill selector values must not be empty")
        if token not in names:
            names.append(token)
    return tuple(names)


def resolve_skill_selector(
    selector: str | Mapping[str, str],
    iteration: int,
) -> str:
    """Resolve a selector with the workflow's exact/default/legacy precedence."""
    if isinstance(selector, str):
        token = selector.strip()
        if not token:
            raise ValueError("skill selector must not be empty")
        return token
    exact = selector.get(str(iteration))
    if exact:
        return str(exact).strip()
    default = selector.get("default")
    if default:
        return str(default).strip()
    ordered = sorted(selector.items(), key=lambda item: str(item[0]))
    if ordered:
        return str(ordered[0][1]).strip()
    raise ValueError("skill selector mapping must not be empty")
