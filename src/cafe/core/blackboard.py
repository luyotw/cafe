"""Shared workflow blackboard state and persistence."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from cafe.core.workflow_models import BatonRejected

BLACKBOARD_FILENAME = "blackboard.json"
BLACKBOARD_SCHEMA_VERSION = 3
NEXT_STEP_FILENAME = "next_step.txt"
HANDOFF_CONTRACT_VERSION = 1
OPERATION_ARTIFACT_FILENAME = "operation.json"
OPERATION_RECEIPT_FILENAME = "operation_receipt.json"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _legacy_workflow_id(data: Dict[str, Any], initial_step: str) -> str:
    """Provide a deterministic in-memory id before the store persists a legacy state."""
    identity = {
        "current_step": str(data.get("current_step", initial_step)),
        "playbook_id": str(data.get("playbook_id", "default")),
        "updated_at": str(data.get("updated_at", "")),
    }
    return str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(identity, sort_keys=True)))


class ArtifactKind(str, Enum):
    """Supported artifact kinds for v0.2."""

    DOCUMENT = "document"
    WORKSPACE = "workspace"
    METADATA = "metadata"


class HandoffOwner(str, Enum):
    """Allowed baton owners."""

    AGENT = "agent"
    USER = "user"
    DONE = "done"


class HandoffIntent(str, Enum):
    """Supported baton intents."""

    AWAIT_AGENT = "await_agent"
    CONFIRM_OUTPUT = "confirm_output"
    ALIGNMENT_CHECKPOINT = "alignment_checkpoint"
    NEED_CLARIFICATION = "need_clarification"
    NEED_PERMISSION = "need_permission"
    NO_CHANGES_NEEDED = "no_changes_needed"
    MANUAL_HANDOFF = "manual_handoff"
    WORKFLOW_COMPLETE = "workflow_complete"


class LongRunningOperationState(str, Enum):
    """Strict four-state model for a long-running phase operation.

    Exactly these four values are accepted. Unknown values are schema
    errors; there are no aliases or fallback names.
    """

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    LOST = "lost"


class OperationRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class OperationMonitoring(str, Enum):
    FINAL_ONLY = "final-only"
    PERIODIC = "periodic"
    ACTIVE = "active"


class OperationLogPolicy(str, Enum):
    SUMMARY_ONLY = "summary-only"
    INCREMENTAL_TAIL = "incremental-tail"
    FILTERED_STREAM = "filtered-stream"


def operation_artifact_path(iteration_dir: Path) -> Path:
    """Fixed one-per-iteration path: ``iteration_dir/operation.json``."""
    return Path(iteration_dir) / OPERATION_ARTIFACT_FILENAME


def operation_receipt_path(iteration_dir: Path) -> Path:
    """Fixed terminal receipt path for one long-running operation."""
    return Path(iteration_dir) / OPERATION_RECEIPT_FILENAME


@dataclass
class LongRunningOperationArtifact:
    """Durable record of one long-running phase operation.

    ``reason`` and ``exit_code`` are explanatory only; they never change
    which of the four states is in effect.
    """

    state: LongRunningOperationState
    risk: OperationRisk
    monitoring: OperationMonitoring
    log_policy: OperationLogPolicy
    stop_condition: str
    recovery: str
    execution_class: str = "sandbox"
    trust_source: str = "workflow"
    effective_boundary: Dict[str, Any] = field(default_factory=dict)
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex[:20])
    command_fingerprint: str = ""
    reason: str = ""
    exit_code: Optional[int] = None
    operation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        if self.execution_class != "sandbox" or self.trust_source != "workflow":
            raise ValueError("long-running operations require sandbox workflow trust")
        validate_operation_decision(
            risk=self.risk,
            monitoring=self.monitoring,
            log_policy=self.log_policy,
            stop_condition=self.stop_condition,
            recovery=self.recovery,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "state": self.state.value,
            "reason": self.reason,
            "exit_code": self.exit_code,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "risk": self.risk.value,
            "monitoring": self.monitoring.value,
            "log_policy": self.log_policy.value,
            "stop_condition": self.stop_condition,
            "recovery": self.recovery,
            "execution_class": self.execution_class,
            "trust_source": self.trust_source,
            "effective_boundary": self.effective_boundary,
            "correlation_id": self.correlation_id,
            "command_fingerprint": self.command_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LongRunningOperationArtifact":
        if not isinstance(data, dict):
            raise ValueError("operation.json must be a JSON object")
        if "state" not in data:
            raise ValueError("operation.json is missing required field 'state'")
        if "operation_id" not in data:
            raise ValueError("operation.json is missing required field 'operation_id'")
        operation_id = str(data["operation_id"]).strip()
        if not operation_id:
            raise ValueError("operation.json operation_id must be non-empty")

        # Direct enum construction only: no alias map, no migration fallback.
        try:
            state = LongRunningOperationState(str(data["state"]))
        except ValueError as exc:
            raise ValueError(
                f"operation.json state has unsupported value {data['state']!r}"
            ) from exc

        raw_exit_code = data.get("exit_code")
        exit_code: Optional[int]
        if raw_exit_code is None:
            exit_code = None
        elif isinstance(raw_exit_code, bool):
            raise ValueError(f"operation.json exit_code must be an integer, got {raw_exit_code!r}")
        elif isinstance(raw_exit_code, int):
            exit_code = raw_exit_code
        else:
            raise ValueError(f"operation.json exit_code must be an integer, got {raw_exit_code!r}")

        artifact = cls(
            state=state,
            reason=str(data.get("reason", "")),
            exit_code=exit_code,
            operation_id=operation_id,
            created_at=str(data.get("created_at", _now_iso())),
            updated_at=str(data.get("updated_at", _now_iso())),
            risk=_strict_operation_value(data, "risk", OperationRisk),
            monitoring=_strict_operation_value(data, "monitoring", OperationMonitoring),
            log_policy=_strict_operation_value(data, "log_policy", OperationLogPolicy),
            stop_condition=_required_operation_text(data.get("stop_condition"), "stop_condition"),
            recovery=_required_operation_text(data.get("recovery"), "recovery"),
            execution_class=str(data.get("execution_class", "sandbox")),
            trust_source=str(data.get("trust_source", "workflow")),
            effective_boundary=dict(data.get("effective_boundary") or {}),
            correlation_id=str(data.get("correlation_id") or data["operation_id"]),
            command_fingerprint=str(data.get("command_fingerprint") or ""),
        )
        validate_operation_decision(
            risk=artifact.risk,
            monitoring=artifact.monitoring,
            log_policy=artifact.log_policy,
            stop_condition=artifact.stop_condition,
            recovery=artifact.recovery,
        )
        return artifact


def _strict_operation_value(data: Dict[str, Any], field_name: str, enum: Any) -> Any:
    if field_name not in data:
        raise ValueError(f"operation.json is missing required field {field_name!r}")
    value = data[field_name]
    try:
        return enum(str(value))
    except ValueError as exc:
        raise ValueError(f"operation.json {field_name} has unsupported value {value!r}") from exc


def _bounded_operation_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or len(value) > 240:
        raise ValueError(f"operation.json {field_name} must be bounded text")
    return value


def _required_operation_text(value: Any, field_name: str) -> str:
    text = _bounded_operation_text(value, field_name)
    if not text.strip():
        raise ValueError(f"operation.json {field_name} must be non-empty")
    return text


def validate_operation_decision(
    *,
    risk: OperationRisk,
    monitoring: OperationMonitoring,
    log_policy: OperationLogPolicy,
    stop_condition: str,
    recovery: str,
) -> None:
    """Validate an agent-owned risk decision before an operation is claimed."""
    expected = {
        OperationRisk.LOW: (OperationMonitoring.FINAL_ONLY, OperationLogPolicy.SUMMARY_ONLY),
        OperationRisk.MEDIUM: (OperationMonitoring.PERIODIC, OperationLogPolicy.INCREMENTAL_TAIL),
        OperationRisk.HIGH: (OperationMonitoring.ACTIVE, OperationLogPolicy.FILTERED_STREAM),
    }[risk]
    if (monitoring, log_policy) != expected:
        raise ValueError(f"operation decision monitoring/log_policy must match risk={risk.value}")
    _required_operation_text(stop_condition, "stop_condition")
    _required_operation_text(recovery, "recovery")


@dataclass
class ArtifactEntry:
    """One persisted artifact pointer."""

    name: str
    kind: ArtifactKind
    version: int
    updated_by: str
    path: str
    updated_at: str = field(default_factory=_now_iso)
    summary: str = ""
    base_sha: Optional[str] = None
    head_sha: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArtifactEntry":
        return cls(
            name=str(data["name"]),
            kind=ArtifactKind(str(data["kind"])),
            version=int(data["version"]),
            updated_by=str(data.get("updated_by", "")),
            updated_at=str(data.get("updated_at", _now_iso())),
            path=str(data.get("path", "")),
            summary=str(data.get("summary", "")),
            base_sha=data.get("base_sha"),
            head_sha=data.get("head_sha"),
        )


@dataclass
class EventEntry:
    """One blackboard event."""

    timestamp: str
    step: str
    event_type: str
    message: str
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EventEntry":
        payload = dict(data)
        if "event_type" not in payload and "type" in payload:
            payload["event_type"] = payload.pop("type")
        if "step" not in payload:
            payload["step"] = str(payload.get("payload", {}).get("step", "system"))
        if "message" not in payload:
            payload["message"] = str(payload.get("payload", {}))
        if "data" not in payload:
            payload["data"] = payload.pop("payload", {})
        return cls(
            timestamp=str(payload.get("timestamp", _now_iso())),
            step=str(payload.get("step", "system")),
            event_type=str(payload.get("event_type", "event")),
            message=str(payload.get("message", "")),
            data=dict(payload.get("data", {})),
        )


@dataclass
class DecisionEntry:
    """One recorded decision."""

    timestamp: str
    step: str
    decision: str
    rationale: str
    made_by: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DecisionEntry":
        if "decision" in data:
            return cls(
                timestamp=str(data.get("timestamp", _now_iso())),
                step=str(data.get("step", "system")),
                decision=str(data["decision"]),
                rationale=str(data.get("rationale", "")),
                made_by=str(data.get("made_by", data.get("step", "system"))),
            )

        return cls(
            timestamp=str(data.get("timestamp", _now_iso())),
            step=str(data.get("from", "system")),
            decision="transition",
            rationale=json.dumps(
                {k: v for k, v in data.items() if k != "timestamp"}, ensure_ascii=False
            ),
            made_by=str(data.get("from", "system")),
        )


@dataclass
class HandoffContract:
    """Structured baton contract persisted in next_step.txt."""

    version: int
    to_owner: HandoffOwner
    to_step: str
    intent: HandoffIntent
    from_step: str = ""
    status_code: str = ""
    created_at: str = field(default_factory=_now_iso)
    source: str = "unknown"

    @property
    def has_meaningful_source(self) -> bool:
        """Whether this baton was explicitly authored rather than bootstrapped."""
        return self.source not in {"", "unknown", "bootstrap", "chat.bootstrap"}

    def to_next_step_dict(self) -> Dict[str, Any]:
        """Return the strict persisted next_step.txt contract."""
        return {
            "version": self.version,
            "to_owner": self.to_owner.value,
            "to_step": self.to_step,
            "intent": self.intent.value,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "to_owner": self.to_owner.value,
            "to_step": self.to_step,
            "intent": self.intent.value,
            "status_code": self.status_code,
            "created_at": self.created_at,
            "source": self.source,
            "from_step": self.from_step,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HandoffContract":
        return cls.from_dict_with_current_step(data, current_step=None)

    @classmethod
    def from_dict_with_current_step(
        cls,
        data: Dict[str, Any],
        *,
        current_step: str | None,
    ) -> "HandoffContract":
        required_fields = ["version", "to_owner", "to_step", "intent"]
        for required_field in required_fields:
            if required_field not in data:
                raise BatonRejected(field=required_field, invalid_value="", valid_values=[])

        try:
            version = int(data["version"])
        except (TypeError, ValueError) as exc:
            raise BatonRejected(
                field="version",
                invalid_value=str(data["version"]),
                valid_values=["integer"],
            ) from exc

        to_owner_raw = str(data["to_owner"])
        try:
            to_owner = HandoffOwner(to_owner_raw)
        except ValueError as exc:
            raise BatonRejected(
                field="to_owner",
                invalid_value=to_owner_raw,
                valid_values=[owner.value for owner in HandoffOwner],
            ) from exc

        intent_raw = str(data["intent"])
        try:
            intent = HandoffIntent(intent_raw)
        except ValueError as exc:
            raise BatonRejected(
                field="intent",
                invalid_value=intent_raw,
                valid_values=[intent.value for intent in HandoffIntent],
            ) from exc

        return cls(
            version=version,
            from_step=str(data.get("from_step", current_step or "")),
            to_owner=to_owner,
            to_step=str(data["to_step"]),
            intent=intent,
            status_code=str(data.get("status_code", "")),
            created_at=str(data.get("created_at", _now_iso())),
            source=str(data.get("source", "unknown")),
        )

    def validate(self, *, allowed_steps: List[str]) -> None:
        allowed_targets = set(allowed_steps) | {"user", "done"}
        if self.version != HANDOFF_CONTRACT_VERSION:
            raise BatonRejected(
                field="version",
                invalid_value=str(self.version),
                valid_values=[str(HANDOFF_CONTRACT_VERSION)],
            )
        if self.to_step not in allowed_targets:
            raise BatonRejected(
                field="to_step",
                invalid_value=self.to_step,
                valid_values=sorted(allowed_targets),
            )

        if self.to_owner == HandoffOwner.AGENT and self.to_step in {"user", "done"}:
            raise BatonRejected(
                field="to_step",
                invalid_value=self.to_step,
                valid_values=sorted(allowed_steps),
            )
        if self.to_owner == HandoffOwner.USER and self.to_step != "user":
            raise BatonRejected(
                field="to_step",
                invalid_value=self.to_step,
                valid_values=["user"],
            )
        if self.to_owner == HandoffOwner.DONE and self.to_step != "done":
            raise BatonRejected(
                field="to_step",
                invalid_value=self.to_step,
                valid_values=["done"],
            )

        if self.intent == HandoffIntent.CONFIRM_OUTPUT and self.from_step not in allowed_steps:
            raise BatonRejected(
                field="from_step",
                invalid_value=self.from_step,
                valid_values=sorted(allowed_steps),
            )
        if self.intent == HandoffIntent.ALIGNMENT_CHECKPOINT:
            if self.to_owner != HandoffOwner.USER:
                raise BatonRejected(
                    field="to_owner",
                    invalid_value=self.to_owner.value,
                    valid_values=[HandoffOwner.USER.value],
                )
            if self.to_step != "user":
                raise BatonRejected(
                    field="to_step",
                    invalid_value=self.to_step,
                    valid_values=["user"],
                )
            if self.from_step not in allowed_steps:
                raise BatonRejected(
                    field="from_step",
                    invalid_value=self.from_step,
                    valid_values=sorted(allowed_steps),
                )

        intents_by_owner = {
            HandoffOwner.AGENT: {
                HandoffIntent.AWAIT_AGENT,
                HandoffIntent.MANUAL_HANDOFF,
            },
            HandoffOwner.USER: {
                HandoffIntent.CONFIRM_OUTPUT,
                HandoffIntent.ALIGNMENT_CHECKPOINT,
                HandoffIntent.NEED_CLARIFICATION,
                HandoffIntent.NEED_PERMISSION,
                HandoffIntent.NO_CHANGES_NEEDED,
                HandoffIntent.MANUAL_HANDOFF,
            },
            HandoffOwner.DONE: {HandoffIntent.WORKFLOW_COMPLETE},
        }
        valid_intents = intents_by_owner[self.to_owner]
        if self.intent not in valid_intents:
            raise BatonRejected(
                field="intent",
                invalid_value=self.intent.value,
                valid_values=sorted(intent.value for intent in valid_intents),
            )


@dataclass
class BlackboardState:
    """Shared state across workflow steps."""

    current_step: str
    playbook_id: str = "default"
    workflow_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    schema_version: int = BLACKBOARD_SCHEMA_VERSION
    artifacts: Dict[str, ArtifactEntry] = field(default_factory=dict)
    events: List[EventEntry] = field(default_factory=list)
    decisions: List[DecisionEntry] = field(default_factory=list)
    capability_receipts: List[Dict[str, Any]] = field(default_factory=list)
    handoff_summary: str = ""
    handoff_contract: Optional[HandoffContract] = None
    ownership_cursor: Optional[Dict[str, Any]] = None
    step_visit_counts: Dict[str, int] = field(default_factory=dict)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "current_step": self.current_step,
            "playbook_id": self.playbook_id,
            "workflow_id": self.workflow_id,
            "artifacts": {name: entry.to_dict() for name, entry in self.artifacts.items()},
            "events": [entry.to_dict() for entry in self.events],
            "decisions": [entry.to_dict() for entry in self.decisions],
            "capability_receipts": list(self.capability_receipts),
            "handoff_summary": self.handoff_summary,
            "handoff_contract": (
                self.handoff_contract.to_dict() if self.handoff_contract is not None else None
            ),
            "ownership_cursor": dict(self.ownership_cursor) if self.ownership_cursor else None,
            "step_visit_counts": dict(self.step_visit_counts),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], *, initial_step: str) -> "BlackboardState":
        raw_version = data.get("schema_version", 1)
        if not isinstance(raw_version, int):
            raise ValueError("blackboard schema_version must be an integer")
        if raw_version > BLACKBOARD_SCHEMA_VERSION:
            raise ValueError(
                f"blackboard schema version {raw_version} is from an unsupported future runtime"
            )
        raw_artifacts = data.get("artifacts", {})
        artifacts: Dict[str, ArtifactEntry] = {}
        if isinstance(raw_artifacts, dict):
            for name, value in raw_artifacts.items():
                if isinstance(value, dict):
                    value.setdefault("name", name)
                    artifacts[name] = ArtifactEntry.from_dict(value)
                else:
                    artifacts[name] = ArtifactEntry(
                        name=name,
                        kind=ArtifactKind.DOCUMENT,
                        version=1,
                        updated_by="unknown",
                        path=str(value),
                    )

        raw_receipts = data.get("capability_receipts", [])
        receipts: List[Dict[str, Any]] = []
        if isinstance(raw_receipts, list):
            for item in raw_receipts:
                if isinstance(item, dict):
                    receipts.append(dict(item))

        raw_cursor = data.get("ownership_cursor")
        if raw_cursor is not None and not isinstance(raw_cursor, dict):
            raise ValueError("blackboard ownership_cursor must be an object or null")
        raw_visits = data.get("step_visit_counts", {})
        if not isinstance(raw_visits, dict):
            raise ValueError("blackboard step_visit_counts must be an object")
        visits: Dict[str, int] = {}
        for step, count in raw_visits.items():
            if not isinstance(count, int) or count < 0:
                raise ValueError(
                    "blackboard step_visit_counts values must be non-negative integers"
                )
            visits[str(step)] = count

        return cls(
            current_step=str(data.get("current_step", initial_step)),
            playbook_id=str(data.get("playbook_id", "default")),
            workflow_id=str(data.get("workflow_id") or _legacy_workflow_id(data, initial_step)),
            schema_version=BLACKBOARD_SCHEMA_VERSION,
            artifacts=artifacts,
            events=[EventEntry.from_dict(entry) for entry in data.get("events", [])],
            decisions=[DecisionEntry.from_dict(entry) for entry in data.get("decisions", [])],
            capability_receipts=receipts,
            handoff_summary=str(data.get("handoff_summary", "")),
            handoff_contract=(
                HandoffContract.from_dict_with_current_step(
                    dict(data["handoff_contract"]),
                    current_step=str(data.get("current_step", initial_step)),
                )
                if isinstance(data.get("handoff_contract"), dict)
                else None
            ),
            ownership_cursor=dict(raw_cursor) if raw_cursor is not None else None,
            step_visit_counts=visits,
            updated_at=str(data.get("updated_at", _now_iso())),
        )


class BlackboardStore:
    """Persist blackboard data in issue directory."""

    def __init__(self, issue_dir: Path) -> None:
        self.issue_dir = issue_dir
        self.file_path = issue_dir / BLACKBOARD_FILENAME
        self.next_step_path = issue_dir / NEXT_STEP_FILENAME

    def load_or_create(
        self,
        initial_step: str,
        playbook_id: str = "default",
        *,
        tolerate_invalid_baton: bool = False,
    ) -> BlackboardState:
        if self.file_path.exists():
            raw = json.loads(self.file_path.read_text(encoding="utf-8"))
            state = BlackboardState.from_dict(raw, initial_step=initial_step)
            if not raw.get("workflow_id"):
                state.workflow_id = str(uuid.uuid4())
                self.save(state)
            if not getattr(state, "playbook_id", None):
                state.playbook_id = playbook_id
                self.save(state)
            try:
                self.ensure_baton(state)
            except BatonRejected:
                if not tolerate_invalid_baton:
                    raise
            return state

        state = BlackboardState(current_step=initial_step, playbook_id=playbook_id)
        self.save(state)
        try:
            self.ensure_baton(state)
        except BatonRejected:
            if not tolerate_invalid_baton:
                raise
        return state

    def save(self, state: BlackboardState) -> None:
        self.issue_dir.mkdir(parents=True, exist_ok=True)
        state.updated_at = _now_iso()
        self.file_path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def ensure_baton(
        self,
        state: BlackboardState,
    ) -> Optional[HandoffContract]:
        """Ensure a persistent baton file exists for this issue."""
        if self.next_step_path.exists():
            try:
                contract = self.load_handoff_contract(
                    state,
                    allowed_steps=[],
                )
            except (BatonRejected, ValueError):
                # Keep the invalid baton on disk so the workflow runtime can
                # feed the exact schema error back to the responsible agent.
                # This also covers non-JSON/legacy-text batons, which are
                # schema errors now that legacy baton parsing is removed.
                state.handoff_contract = None
                self.save(state)
                return None
            state.handoff_contract = contract
            self.save(state)
            return contract

        contract = HandoffContract(
            version=HANDOFF_CONTRACT_VERSION,
            from_step=state.current_step,
            to_owner=(
                HandoffOwner.AGENT
                if state.current_step not in {"user", "done"}
                else HandoffOwner(state.current_step)
            ),
            to_step=state.current_step,
            intent=(
                HandoffIntent.AWAIT_AGENT
                if state.current_step not in {"user", "done"}
                else (
                    HandoffIntent.MANUAL_HANDOFF
                    if state.current_step == "user"
                    else HandoffIntent.WORKFLOW_COMPLETE
                )
            ),
            status_code="",
            created_at=_now_iso(),
            source="bootstrap",
        )
        self.write_handoff_contract(state, contract)
        return contract

    @staticmethod
    def _make_baton_rejected(payload: Dict[str, Any]) -> BatonRejected:
        """Inspect payload to identify the first invalid enum field and return BatonRejected."""
        to_owner_raw = str(payload.get("to_owner", ""))
        try:
            HandoffOwner(to_owner_raw)
        except ValueError:
            return BatonRejected(
                field="to_owner",
                invalid_value=to_owner_raw,
                valid_values=[e.value for e in HandoffOwner],
            )
        intent_raw = str(payload.get("intent", ""))
        try:
            HandoffIntent(intent_raw)
        except ValueError:
            return BatonRejected(
                field="intent",
                invalid_value=str(payload.get("intent", "")),
                valid_values=[e.value for e in HandoffIntent],
            )
        return BatonRejected(
            field="unknown",
            invalid_value="",
            valid_values=[],
        )

    def load_handoff_contract(
        self,
        state: BlackboardState,
        *,
        allowed_steps: List[str],
    ) -> HandoffContract:
        """Load and parse a structured baton contract from next_step.txt.

        Only the structured JSON baton contract is accepted. Plain-text step
        names, ``key=value`` text, and any other legacy shapes are rejected.
        """
        if not self.next_step_path.exists():
            raise ValueError(f"Baton file is missing: {self.next_step_path}")

        raw = self.next_step_path.read_text(encoding="utf-8").strip()
        if not raw:
            raise ValueError(f"Baton file is empty: {self.next_step_path}")

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid baton contract payload: {exc}") from exc

        if not isinstance(payload, dict):
            raise BatonRejected(
                field="payload",
                invalid_value=type(payload).__name__,
                valid_values=["JSON object"],
            )

        payload_has_from_step = "from_step" in payload
        payload_has_status_code = "status_code" in payload
        try:
            contract = HandoffContract.from_dict_with_current_step(
                payload,
                current_step=state.current_step,
            )
        except BatonRejected:
            raise
        except ValueError as exc:
            raise ValueError(f"Invalid baton contract payload: {exc}") from exc

        prior_contract = state.handoff_contract
        same_blackboard_handoff = (
            prior_contract is not None
            and contract.to_owner == prior_contract.to_owner
            and contract.to_step == prior_contract.to_step
            and contract.intent == prior_contract.intent
        )

        if prior_contract is not None and same_blackboard_handoff:
            if contract.source == "unknown":
                prior_source = str(prior_contract.source)
                if prior_source:
                    contract.source = prior_source
            if not payload_has_status_code and prior_contract.status_code:
                contract.status_code = prior_contract.status_code
            if not payload_has_from_step and prior_contract.from_step:
                contract.from_step = prior_contract.from_step

        if not contract.from_step:
            contract.from_step = state.current_step

        if allowed_steps:
            contract.validate(allowed_steps=allowed_steps)
        return contract

    def write_handoff_contract(self, state: BlackboardState, contract: HandoffContract) -> None:
        """Persist baton contract to next_step.txt and blackboard."""
        self.issue_dir.mkdir(parents=True, exist_ok=True)
        self.next_step_path.write_text(
            json.dumps(contract.to_next_step_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        state.handoff_contract = contract
        self.save(state)

    def update_handoff_contract(
        self,
        state: BlackboardState,
        *,
        from_step: str,
        to_owner: HandoffOwner,
        to_step: str,
        intent: HandoffIntent,
        status_code: str = "",
        source: str = "workflow",
    ) -> HandoffContract:
        contract = HandoffContract(
            version=HANDOFF_CONTRACT_VERSION,
            from_step=from_step,
            to_owner=to_owner,
            to_step=to_step,
            intent=intent,
            status_code=status_code,
            created_at=_now_iso(),
            source=source,
        )
        self.write_handoff_contract(state, contract)
        return contract

    def get_artifact(self, state: BlackboardState, name: str) -> Optional[ArtifactEntry]:
        return state.artifacts.get(name)

    def list_artifacts(self, state: BlackboardState) -> Dict[str, ArtifactEntry]:
        return dict(state.artifacts)

    def put_artifact(self, state: BlackboardState, entry: ArtifactEntry) -> None:
        state.artifacts[entry.name] = entry
        self.save(state)

    def read_operation_artifact(
        self, iteration_dir: Path
    ) -> Optional[LongRunningOperationArtifact]:
        """Read the fixed one-per-iteration operation artifact, if any.

        Raises ``ValueError``/``json.JSONDecodeError`` when the artifact
        exists but fails schema validation; callers must treat that as a
        schema error rather than silently defaulting to a state.
        """
        path = operation_artifact_path(iteration_dir)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return LongRunningOperationArtifact.from_dict(raw)

    def write_operation_artifact(
        self,
        state: BlackboardState,
        *,
        step: str,
        iteration_dir: Path,
        artifact: LongRunningOperationArtifact,
    ) -> LongRunningOperationArtifact:
        """Persist the operation artifact and publish it as blackboard metadata.

        Reuses existing metadata-artifact and event helpers; this does not
        introduce a new ``BlackboardState`` collection or job queue.
        """
        path = operation_artifact_path(iteration_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        artifact_name = f"{step}_operation"
        previous = state.artifacts.get(artifact_name)
        version = previous.version + 1 if previous else 1
        self.put_artifact(
            state,
            ArtifactEntry(
                name=artifact_name,
                kind=ArtifactKind.METADATA,
                version=version,
                updated_by=step,
                path=str(path),
                summary=(f"long_running_operation:{artifact.operation_id}:{artifact.state.value}"),
            ),
        )
        self.record_event(
            state,
            "long_running_operation",
            {
                "step": step,
                "state": artifact.state.value,
                "operation_id": artifact.operation_id,
                "reason": artifact.reason,
                "exit_code": artifact.exit_code,
                "path": str(path),
            },
        )
        return artifact

    def read_operation_receipt(self, iteration_dir: Path) -> Optional[LongRunningOperationArtifact]:
        path = operation_receipt_path(iteration_dir)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return LongRunningOperationArtifact.from_dict(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"{path.name} schema invalid: {exc}") from exc

    def write_operation_receipt(
        self,
        state: BlackboardState,
        *,
        step: str,
        iteration_dir: Path,
        operation_id: str,
        artifact: LongRunningOperationArtifact,
    ) -> LongRunningOperationArtifact:
        """Persist a controlled terminal receipt for an existing operation."""
        if artifact.state == LongRunningOperationState.RUNNING:
            raise ValueError("operation receipt must be terminal")
        if artifact.operation_id != operation_id:
            raise ValueError("operation receipt operation_id mismatch")

        path = operation_receipt_path(iteration_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        artifact_name = f"{step}_operation_receipt"
        previous = state.artifacts.get(artifact_name)
        version = previous.version + 1 if previous else 1
        self.put_artifact(
            state,
            ArtifactEntry(
                name=artifact_name,
                kind=ArtifactKind.METADATA,
                version=version,
                updated_by=step,
                path=str(path),
                summary=(
                    f"long_running_operation_receipt:{artifact.operation_id}:"
                    f"{artifact.state.value}"
                ),
            ),
        )
        self.record_event(
            state,
            "long_running_operation_receipt",
            {
                "step": step,
                "state": artifact.state.value,
                "operation_id": artifact.operation_id,
                "reason": artifact.reason,
                "exit_code": artifact.exit_code,
                "path": str(path),
            },
        )
        return artifact

    def append_capability_receipt(self, state: BlackboardState, receipt: Dict[str, Any]) -> None:
        """Append one structured host capability receipt and persist the blackboard."""
        state.capability_receipts.append(dict(receipt))
        self.save(state)

    def set_current_step(self, state: BlackboardState, step: str) -> None:
        state.current_step = step
        self.save(state)

    def set_handoff_summary(self, state: BlackboardState, summary: str) -> None:
        state.handoff_summary = summary
        self.save(state)

    def set_artifact(self, state: BlackboardState, key: str, path: str) -> None:
        previous = state.artifacts.get(key)
        version = previous.version + 1 if previous else 1
        kind = previous.kind if previous else ArtifactKind.DOCUMENT
        updated_by = state.current_step
        self.put_artifact(
            state,
            ArtifactEntry(
                name=key,
                kind=kind,
                version=version,
                updated_by=updated_by,
                path=path,
            ),
        )

    def log_event(
        self,
        state: BlackboardState,
        step: str,
        event_type: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        state.events.append(
            EventEntry(
                timestamp=_now_iso(),
                step=step,
                event_type=event_type,
                message=message,
                data=data or {},
            )
        )
        self.save(state)

    def record_event(
        self, state: BlackboardState, event_type: str, payload: Dict[str, Any]
    ) -> None:
        step = str(payload.get("step", state.current_step))
        self.log_event(state, step, event_type, json.dumps(payload, ensure_ascii=False), payload)

    def get_events_since(self, state: BlackboardState, timestamp: str) -> List[EventEntry]:
        return [entry for entry in state.events if entry.timestamp >= timestamp]

    def record_decision(
        self,
        state: BlackboardState,
        step_or_decision: str | Dict[str, Any],
        decision: Optional[str] = None,
        rationale: Optional[str] = None,
        made_by: Optional[str] = None,
    ) -> None:
        if isinstance(step_or_decision, dict):
            entry = DecisionEntry.from_dict(step_or_decision)
        else:
            entry = DecisionEntry(
                timestamp=_now_iso(),
                step=step_or_decision,
                decision=decision or "",
                rationale=rationale or "",
                made_by=made_by or step_or_decision,
            )
        state.decisions.append(entry)
        self.save(state)

    def generate_digest(
        self,
        state: BlackboardState,
        *,
        for_step: str,
        since: Optional[str] = None,
        max_events: int = 20,
    ) -> str:
        lines = [
            "## Blackboard",
            "",
            "### Artifacts",
            "| Name | Kind | Ver | Updated By | When |",
            "|------|------|-----|-----------|------|",
        ]
        for name, entry in sorted(state.artifacts.items()):
            when = (
                entry.updated_at.split("T", 1)[1][:5]
                if "T" in entry.updated_at
                else entry.updated_at
            )
            lines.append(
                f"| {name} | {entry.kind.value} | v{entry.version} | {entry.updated_by} | {when} |"
            )

        lines.extend(["", f"### Recent Events (since your last run, max {max_events})"])
        events = state.events if since is None else self.get_events_since(state, since)
        for entry in events[-max_events:]:
            when = (
                entry.timestamp.split("T", 1)[1][:5] if "T" in entry.timestamp else entry.timestamp
            )
            lines.append(f"- [{when}] {entry.step}: {entry.message}")

        lines.extend(["", "### Input Files"])
        for name, entry in sorted(state.artifacts.items()):
            if entry.kind == ArtifactKind.WORKSPACE and entry.base_sha and entry.head_sha:
                lines.append(f"- {name}: git diff {entry.base_sha}...{entry.head_sha}")
            else:
                lines.append(f"- {name}: {entry.path}")
        return "\n".join(lines)

    def rebuild_from_iterations(self, *, initial_step: str) -> BlackboardState:
        state = BlackboardState(current_step=initial_step)
        latest_step = initial_step
        latest_timestamp = ""

        for artifact_file in sorted(self.issue_dir.glob("*/iteration_*/artifact.json")):
            raw = json.loads(artifact_file.read_text(encoding="utf-8"))
            entry = ArtifactEntry.from_dict(raw)
            current = state.artifacts.get(entry.name)
            if current is None or entry.version >= current.version:
                state.artifacts[entry.name] = entry
            if entry.updated_at >= latest_timestamp:
                latest_timestamp = entry.updated_at
                latest_step = artifact_file.parent.parent.name

        state.current_step = latest_step
        state.events.append(
            EventEntry(
                timestamp=_now_iso(),
                step=latest_step,
                event_type="rebuild",
                message="Rebuilt blackboard state from iteration artifacts",
            )
        )
        self.save(state)
        return state
