"""Static transition-graph analysis for ``playbook simulate`` (no agents, no hooks)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

from cafe.core.playbook import DONE_TARGET, PlaybookDefinition
from cafe.core.status_codes import PhaseStatusCode, transition_map_key


@dataclass(frozen=True)
class PlaybookSimulationResult:
    playbook_id: str
    entry_point: str
    edges: List[Tuple[str, str, str]]
    reachable_steps: Set[str]
    unreachable_steps: Tuple[str, ...]
    dead_end_steps: Tuple[str, ...]
    cycles: Tuple[str, ...]
    missing_intent_handlers: Tuple[str, ...]
    ownership: Tuple[str, ...] = ()


def validate_entry_point(model: PlaybookDefinition) -> None:
    ep = model.entry_point
    if ep is None:
        return
    if ep not in model.steps:
        raise ValueError(f"entry_point {ep!r} is not a defined step")


def _iter_edges(model: PlaybookDefinition) -> List[Tuple[str, str, str]]:
    """(from_step, intent_key, to_target) where to_target is a step id or ``_done``."""
    edges: List[Tuple[str, str, str]] = []
    for step_name, step in model.steps.items():
        for intent_key, target in step.on.items():
            edges.append((step_name, str(intent_key), str(target)))
    return edges


def _reachable_step_names(model: PlaybookDefinition) -> Set[str]:
    """Steps reachable from ``entry_point`` following ``on`` targets (``_done`` excluded)."""
    ep = model.entry_point or next(iter(model.steps.keys()))
    if ep not in model.steps:
        return set()
    seen: Set[str] = {ep}
    stack = [ep]
    while stack:
        cur = stack.pop()
        step = model.steps.get(cur)
        if step is None:
            continue
        for _intent, tgt in step.on.items():
            if tgt == DONE_TARGET:
                continue
            if tgt in model.steps and tgt not in seen:
                seen.add(tgt)
                stack.append(tgt)
    return seen


def _dead_end_steps(model: PlaybookDefinition) -> List[str]:
    return sorted(name for name, step in model.steps.items() if not step.on)


def _missing_intent_handlers(model: PlaybookDefinition) -> List[str]:
    """When ``valid_intents`` is declared on a step, every mapped transition key must exist in ``on`` (or ``default``)."""
    findings: List[str] = []
    for step_name, step in model.steps.items():
        if not step.valid_intents:
            continue
        for raw in step.valid_intents:
            try:
                code = PhaseStatusCode(raw)
            except ValueError:
                continue
            key = transition_map_key(code)
            if key not in step.on and "default" not in step.on:
                findings.append(
                    f"step {step_name!r}: outcome {code.value!r} maps to transition key {key!r} "
                    f"but `on` defines neither that key nor `default`"
                )
    return sorted(findings)


def _step_adjacency(model: PlaybookDefinition) -> Dict[str, Set[str]]:
    """Directed edges among defined steps only (``_done`` dropped)."""
    adj: Dict[str, Set[str]] = {name: set() for name in model.steps}
    for u, _intent, v in _iter_edges(model):
        if v != DONE_TARGET and v in model.steps:
            adj[u].add(v)
    return adj


def _tarjan_sccs(vertices: Set[str], adj: Dict[str, Set[str]]) -> List[List[str]]:
    index_counter = 0
    stack: List[str] = []
    index: Dict[str, int] = {}
    lowlink: Dict[str, int] = {}
    onstack: Set[str] = set()
    components: List[List[str]] = []

    def strongconnect(v: str) -> None:
        nonlocal index_counter
        index[v] = index_counter
        lowlink[v] = index_counter
        index_counter += 1
        stack.append(v)
        onstack.add(v)
        for w in adj.get(v, set()):
            if w not in index:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in onstack:
                lowlink[v] = min(lowlink[v], index[w])
        if lowlink[v] == index[v]:
            comp: List[str] = []
            while True:
                w = stack.pop()
                onstack.remove(w)
                comp.append(w)
                if w == v:
                    break
            components.append(comp)

    for v in sorted(vertices):
        if v not in index:
            strongconnect(v)
    return components


def _multi_step_cycle_summaries(model: PlaybookDefinition) -> List[str]:
    """Report SCCs with >1 node; single-node SCCs with a self-edge are ignored (self-loops only)."""
    vertices = set(model.steps.keys())
    adj = _step_adjacency(model)
    summaries: List[str] = []
    for comp in _tarjan_sccs(vertices, adj):
        if len(comp) > 1:
            summaries.append("directed cycle among steps: " + ", ".join(sorted(comp)))
            continue
        if len(comp) == 1:
            u = comp[0]
            if u in adj.get(u, set()):
                continue
    return sorted(set(summaries))


def _ownership_preview(model: PlaybookDefinition) -> Tuple[str, ...]:
    """Describe declared ownership without invoking owners or creating state."""
    lines: List[str] = []
    for step_name, step in model.steps.items():
        lines.append(f"{step_name}: owner={step.assignee_type}")
        if step.assignee_type == "auto" and step.automatic is not None:
            lines.append(f"  automatic executor={step.automatic.executor}")
        if step.assignee_type == "human":
            lines.append("  human wait=initial")
        if step.assignee_type == "hybrid" and step.hybrid is not None:
            for portion in step.hybrid.portions:
                transitions = ", ".join(
                    f"{key}->{target.portion if target.portion is not None else target.step}"
                    for key, target in sorted(portion.on.items())
                )
                wait = " wait" if portion.owner == "human" else ""
                lines.append(f"  portion={portion.id} owner={portion.owner}{wait} on={transitions}")
    return tuple(lines)


def analyze_playbook(model: PlaybookDefinition) -> PlaybookSimulationResult:
    validate_entry_point(model)
    entry = model.entry_point or next(iter(model.steps.keys()))
    edges = _iter_edges(model)
    reachable = _reachable_step_names(model)
    all_names = set(model.steps.keys())
    unreachable = tuple(sorted(all_names - reachable))
    dead = tuple(_dead_end_steps(model))
    missing = tuple(_missing_intent_handlers(model))
    cycles = tuple(_multi_step_cycle_summaries(model))
    return PlaybookSimulationResult(
        playbook_id=model.playbook.id,
        entry_point=entry,
        edges=edges,
        reachable_steps=reachable,
        unreachable_steps=unreachable,
        dead_end_steps=dead,
        cycles=cycles,
        missing_intent_handlers=missing,
        ownership=_ownership_preview(model),
    )


def _dot_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def format_text_report(result: PlaybookSimulationResult) -> str:
    lines: List[str] = []
    lines.append(f"Playbook: {result.playbook_id}")
    lines.append(f"Entry point: {result.entry_point}")
    lines.append("")
    lines.append("Ownership plan (read-only)")
    if result.ownership:
        lines.extend(f"  {line}" for line in result.ownership)
    else:
        lines.append("  (no declarations)")
    lines.append("")
    lines.append("Transitions (intent -> next step)")
    by_from: Dict[str, List[Tuple[str, str]]] = {}
    for frm, intent, to in result.edges:
        by_from.setdefault(frm, []).append((intent, to))
    for step in sorted(by_from.keys()):
        lines.append(f"  [{step}]")
        for intent, to in sorted(by_from[step], key=lambda x: (x[0], x[1])):
            lines.append(f"    {intent} -> {to}")
    lines.append("")
    lines.append("Unreachable steps (from entry)")
    if result.unreachable_steps:
        for s in result.unreachable_steps:
            lines.append(f"  - {s}")
    else:
        lines.append("  (no findings)")
    lines.append("")
    lines.append("Directed cycles (excluding single-step self-loops)")
    if result.cycles:
        for c in result.cycles:
            lines.append(f"  - {c}")
    else:
        lines.append("  (no findings)")
    lines.append("")
    lines.append("Missing intent handlers and dead-end steps")
    lines.append("  Intent handlers (from declared valid_intents):")
    if result.missing_intent_handlers:
        for m in result.missing_intent_handlers:
            lines.append(f"    - {m}")
    else:
        lines.append("    (no findings)")
    lines.append("  Dead-end steps (empty on:):")
    if result.dead_end_steps:
        for s in result.dead_end_steps:
            lines.append(f"    - {s}")
    else:
        lines.append("    (no findings)")
    return "\n".join(lines)


def format_dot(result: PlaybookSimulationResult) -> str:
    lines = ["digraph playbook {", "  rankdir=LR;"]
    nodes: Set[str] = set()
    for f, _i, t in result.edges:
        nodes.add(f)
        nodes.add(t)
    for n in sorted(nodes):
        lines.append(f'  "{_dot_escape(n)}";')
    for f, intent, t in result.edges:
        lines.append(f'  "{_dot_escape(f)}" -> "{_dot_escape(t)}" [label="{_dot_escape(intent)}"];')
    for line in result.ownership:
        if not line.startswith("  portion="):
            continue
        # The textual report remains the detailed source; DOT mirrors the
        # ownership boundary as a comment without fabricating executable edges.
        lines.append(f"  // {_dot_escape(line.strip())}")
    lines.append("}")
    return "\n".join(lines)
