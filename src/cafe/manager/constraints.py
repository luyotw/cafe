"""Manager-owned refresh adapter; runtime evidence is not confirmed policy."""

from cafe.constraints import Context
from cafe.constraints.context import context_for_tools
from cafe.constraints.evidence import Snapshot, snapshot


def capture_constraints(policy):
    entries = {}
    for phase in policy["phases"]:
        for entry in phase["chain"]:
            consumers = ["authority"]
            if len(phase["chain"]) == 1:
                consumers.append("single-chain")
            context = context_for_tools(entry["cli"], structured=True, consumers=consumers)
            entries[phase["name"] + "/" + entry["cli"]] = snapshot(context)
    if policy["manager"]["mode"] == "event-driven":
        for entry in policy["manager"].get("clis", []):
            cli = entry.get("cli") if isinstance(entry, dict) else str(entry).split(":", 1)[0]
            if cli:
                entries["callback/" + cli] = snapshot(
                    context_for_tools(
                        cli, operation="event-driver", consumers=["callback", "authority"]
                    )
                )
    return {"version": 1, "entries": entries}


def validate_evidence(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "entries"}
        or type(value["version"]) is not int
        or value["version"] != 1
    ):
        raise ValueError("Invalid runtime constraints evidence envelope")
    entries = value["entries"]
    if not isinstance(entries, dict) or len(entries) > 256:
        raise ValueError("Invalid runtime constraints evidence entries")
    for name, entry in entries.items():
        if not isinstance(name, str) or not name or len(name) > 256:
            raise ValueError("Invalid runtime constraints context identity")
        Snapshot.model_validate(entry)
    return value


def refresh_constraints(contract):
    evidence = contract["provenance"].get("runtime_constraints")
    if evidence is None:
        return None
    validate_evidence(evidence)
    return {
        "version": 1,
        "entries": {
            name: snapshot(Context.model_validate(item["context"]))
            for name, item in evidence["entries"].items()
        },
    }
