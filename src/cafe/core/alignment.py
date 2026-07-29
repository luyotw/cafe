"""Deterministic alignment policy and checkpoint payload models."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, Optional, Sequence

from cafe.core.strategic_context import StrategicContext, StrategicDocumentMetadata


class AlignmentDecisionLevel(str, Enum):
    """Policy result levels."""

    MUST_ALIGN = "must_align"
    ALIGNMENT_NOTE = "alignment_note"
    NO_ALIGNMENT = "no_alignment"


@dataclass(frozen=True)
class AlignmentPolicyConfig:
    """Threshold and document scope configuration for one step."""

    pause_threshold: int = 5
    note_threshold: int = 2
    affected_document_categories: tuple[str, ...] = ()
    reuse_approved: bool = True


@dataclass(frozen=True)
class AlignmentPolicyInput:
    """Inputs evaluated by the deterministic alignment policy."""

    step_name: str
    user_input: str = ""
    playbook_id: str = ""
    artifacts: Dict[str, str] = field(default_factory=dict)
    required_document_categories: tuple[str, ...] = ()
    proposed_scope: str = ""
    non_scope: str = ""


@dataclass(frozen=True)
class TriggeredRule:
    """One hard trigger or scored policy signal."""

    rule_id: str
    description: str
    risk_level: str
    score: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "description": self.description,
            "risk_level": self.risk_level,
            "score": self.score,
        }


@dataclass(frozen=True)
class StrategicDocumentUpdateRequirement:
    """A required strategic-document update before execution can continue."""

    category: str
    configured_path: Optional[str]
    current_status: str
    current_sha256: Optional[str]
    required_action: str
    unblock_choices: tuple[str, ...] = (
        "update_document",
        "narrow_scope",
        "defer_or_reject",
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "configured_path": self.configured_path,
            "current_status": self.current_status,
            "current_sha256": self.current_sha256,
            "required_action": self.required_action,
            "unblock_choices": list(self.unblock_choices),
        }


@dataclass(frozen=True)
class AlignmentCheckpointPayload:
    """User-facing alignment checkpoint payload."""

    interpreted_goal: str
    proposed_scope: str
    non_scope: str
    triggered_rules: tuple[TriggeredRule, ...]
    risk_level: str
    affected_documents: tuple[StrategicDocumentMetadata, ...]
    risks: tuple[str, ...]
    assumptions: tuple[str, ...]
    strategic_update_recommendation: str
    decision_requested: str
    recommended_resume_target: str
    fingerprint: str
    allowed_decisions: tuple[str, ...] = (
        "approve",
        "narrow_scope",
        "revise_spec",
        "revise_plan",
        "update_strategic_documents_first",
        "strategic_documents_updated",
        "manual_pause",
        "reject_or_defer",
    )
    strategic_document_update_requirements: tuple[StrategicDocumentUpdateRequirement, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "interpreted_goal": self.interpreted_goal,
            "proposed_scope": self.proposed_scope,
            "non_scope": self.non_scope,
            "triggered_rules": [rule.to_dict() for rule in self.triggered_rules],
            "risk_level": self.risk_level,
            "affected_documents": [doc.to_dict() for doc in self.affected_documents],
            "risks": list(self.risks),
            "assumptions": list(self.assumptions),
            "strategic_update_recommendation": self.strategic_update_recommendation,
            "decision_requested": self.decision_requested,
            "recommended_resume_target": self.recommended_resume_target,
            "fingerprint": self.fingerprint,
            "allowed_decisions": list(self.allowed_decisions),
            "strategic_document_update_requirements": [
                requirement.to_dict() for requirement in self.strategic_document_update_requirements
            ],
        }


@dataclass(frozen=True)
class AlignmentPolicyResult:
    """Policy result returned to hooks and tests."""

    level: AlignmentDecisionLevel
    triggered_rules: tuple[TriggeredRule, ...] = ()
    score: int = 0
    payload: Optional[AlignmentCheckpointPayload] = None


@dataclass(frozen=True)
class AgentAlignmentEvidence:
    """Optional agent-supplied details that can enrich but not downgrade policy."""

    suggested_level: Optional[AlignmentDecisionLevel] = None
    risks: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()


AXIS_KEYWORDS: Dict[str, tuple[str, ...]] = {
    "product_scope": (
        "product",
        "roadmap",
        "scope",
        "feature",
        "positioning",
        "governance",
        "principles",
        "product direction",
        "user trust",
        "business model",
        "產品",
        "產品方向",
        "路線圖",
        "範圍",
        "產品範圍",
        "定位",
        "治理",
        "原則",
        "使用者信任",
        "商業模式",
    ),
    "technical": ("architecture", "api", "runtime", "database", "schema", "dependency"),
    "quality": ("quality", "test", "reliability", "regression"),
}

DOCUMENT_KEYWORDS: Dict[str, tuple[str, ...]] = {
    "roadmap": ("roadmap", "路線圖"),
    "product_direction": ("product direction", "product strategy", "產品方向", "產品策略"),
    "principles": ("principles", "north star", "red line", "原則", "北極星", "紅線"),
    "positioning": ("positioning", "messaging", "market", "audience", "定位", "市場", "受眾"),
    "strategic_context": ("strategic_context", "strategic context", "mandate", "授權", "決策權限"),
}

TRUSTED_CAPABILITY_BOUNDARY_KEYWORDS: tuple[str, ...] = (
    "capability contract",
    "capability contracts",
    "capability-contract",
    "capability-contracts",
    "capability registry",
    "trusted capability",
    "trusted capabilities",
    "trusted host",
    "trusted host-side",
    "host-side capability",
    "host-side capabilities",
    "host-side execution",
    "host-side action",
    "host-side actions",
    "execution request",
    "trust boundary",
    "host 端",
    "主機端",
    "主機端執行",
    "可信能力",
    "受信任能力",
    "能力合約",
    "能力契約",
    "信任邊界",
)

_STRATEGIC_CHANGE_ACTION_RE = re.compile(
    r"""
    \b(?:
        chang(?:e|es|ed|ing)
        |expand(?:s|ed|ing)?
        |broaden(?:s|ed|ing)?
        |shift(?:s|ed|ing)?
        |redefin(?:e|es|ed|ing)
        |revis(?:e|es|ed|ing)
        |updat(?:e|es|ed|ing)
        |modif(?:y|ies|ied|ying)
        |alter(?:s|ed|ing)?
        |decid(?:e|es|ed|ing)
        |clarif(?:y|ies|ied|ying)
        |defin(?:e|es|ed|ing)
        |draft(?:s|ed|ing)?
        |introduc(?:e|es|ed|ing)
        |add(?:s|ed|ing)?
        |remov(?:e|es|ed|ing)
    )\b
    |改變|變更|擴大|擴張|拓展|重新定義|修訂|更新|修改|調整|決定|釐清|定義|草擬|新增|移除
    """,
    re.IGNORECASE | re.VERBOSE,
)

_STRATEGIC_TARGET_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\broadmap(?:\s+scope)?\b", re.IGNORECASE),
    re.compile(
        r"\bproduct(?:\s+[\w-]+){0,4}\s+(?:direction|strategy|scope)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:positioning|governance|principles?|business model|user trust|north star)\b",
        re.IGNORECASE,
    ),
    re.compile(r"路線圖|產品.{0,24}(?:方向|策略|範圍)|定位|治理|原則|商業模式|使用者信任|北極星"),
)

_NEGATED_ACTION_PREFIX_RE = re.compile(
    r"""
    (?:
        \b(?:do|does|did|will|would|should|must|can)\s+not\s+
        |\b(?:don't|doesn't|didn't|won't|wouldn't|shouldn't|mustn't|can't)\s+
        |\bwithout\s+
        |\bno\s+
        |\bnot\s+
        |不(?:會|再|要|應|得|需)?
        |無需
        |毋須
        |避免
    )$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_NON_SCOPE_LINE_MARKERS: tuple[str, ...] = (
    "out of scope",
    "non-goal",
    "non-goals",
    "範圍外",
    "非目標",
    "本次不做",
    "不包含",
)


def evaluate_alignment_policy(
    input_data: AlignmentPolicyInput,
    *,
    strategic_context: StrategicContext,
    config: Optional[AlignmentPolicyConfig] = None,
) -> AlignmentPolicyResult:
    """Evaluate deterministic rules and return an alignment result."""
    cfg = config or AlignmentPolicyConfig()
    text = _policy_text(input_data)
    triggered: list[TriggeredRule] = []
    risks: list[str] = []
    assumptions: list[str] = []
    required_categories = list(dict.fromkeys(input_data.required_document_categories))
    affected_categories = _detect_document_categories(text)

    if _explicit_alignment_requested(text):
        triggered.append(
            TriggeredRule(
                "explicit_alignment_request",
                "User explicitly requested alignment before execution.",
                "high",
            )
        )
        risks.append("The user requested an explicit direction check before continuing.")

    for axis_name, axis in strategic_context.axes.items():
        if axis.level != "escalate":
            continue
        if _matches_axis_escalation(text, axis_name):
            triggered.append(
                TriggeredRule(
                    f"mandate_escalation:{axis_name}",
                    f"Task touches escalated mandate axis '{axis_name}'.",
                    "high",
                )
            )
            risks.append(f"Work may affect escalated mandate axis '{axis_name}'.")
            for category in axis.grounds:
                if category not in affected_categories:
                    affected_categories.append(category)

    matched_out_of_mandate = [
        item for item in strategic_context.out_of_mandate if item and item.lower() in text
    ]
    if matched_out_of_mandate:
        triggered.append(
            TriggeredRule(
                "out_of_mandate",
                "Task overlaps declared out-of-mandate items: " + ", ".join(matched_out_of_mandate),
                "high",
            )
        )
        risks.append("The task overlaps declared out-of-mandate work.")

    for category in _detect_required_update_categories(text, required_categories):
        if category not in required_categories:
            required_categories.append(category)
        if category not in affected_categories:
            affected_categories.append(category)
    if required_categories:
        triggered.append(
            TriggeredRule(
                "strategic_document_update_required",
                "A relevant strategic document must be updated before execution.",
                "high",
            )
        )
        risks.append("Execution may proceed against stale or missing strategic guidance.")

    if _should_include_configured_documents(
        text=text,
        triggered_rules=triggered,
        required_categories=required_categories,
    ):
        for category in cfg.affected_document_categories:
            if category not in affected_categories:
                affected_categories.append(category)
    score_rules = _score_signals(text, affected_categories, strategic_context)
    triggered.extend(score_rules)

    affected_documents = tuple(
        strategic_context.document(category) for category in affected_categories
    )
    requirements = tuple(
        _document_requirement(strategic_context.document(category))
        for category in required_categories
    )

    hard_required = any(rule.score == 0 and rule.risk_level == "high" for rule in triggered)
    score = sum(rule.score for rule in triggered)
    if hard_required or score >= cfg.pause_threshold:
        level = AlignmentDecisionLevel.MUST_ALIGN
    elif score >= cfg.note_threshold:
        level = AlignmentDecisionLevel.ALIGNMENT_NOTE
    else:
        return AlignmentPolicyResult(level=AlignmentDecisionLevel.NO_ALIGNMENT)

    payload = _build_payload(
        input_data=input_data,
        strategic_context=strategic_context,
        triggered_rules=tuple(triggered),
        risk_level="high" if level == AlignmentDecisionLevel.MUST_ALIGN else "medium",
        affected_documents=affected_documents,
        requirements=requirements,
        risks=tuple(dict.fromkeys(risks or _default_risks(level))),
        assumptions=tuple(
            dict.fromkeys(
                assumptions or ("Policy signals were derived from workflow input and artifacts.",)
            )
        ),
    )
    return AlignmentPolicyResult(
        level=level,
        triggered_rules=tuple(triggered),
        score=score,
        payload=payload,
    )


def merge_agent_alignment_evidence(
    policy_result: AlignmentPolicyResult,
    evidence: Optional[AgentAlignmentEvidence],
) -> AlignmentPolicyResult:
    """Merge agent evidence without allowing it to downgrade policy severity."""
    if evidence is None or policy_result.payload is None:
        return policy_result

    level = policy_result.level
    if evidence.suggested_level == AlignmentDecisionLevel.MUST_ALIGN:
        level = AlignmentDecisionLevel.MUST_ALIGN
    elif (
        evidence.suggested_level == AlignmentDecisionLevel.ALIGNMENT_NOTE
        and level == AlignmentDecisionLevel.NO_ALIGNMENT
    ):
        level = AlignmentDecisionLevel.ALIGNMENT_NOTE

    payload = AlignmentCheckpointPayload(
        interpreted_goal=policy_result.payload.interpreted_goal,
        proposed_scope=policy_result.payload.proposed_scope,
        non_scope=policy_result.payload.non_scope,
        triggered_rules=policy_result.payload.triggered_rules,
        risk_level=policy_result.payload.risk_level,
        affected_documents=policy_result.payload.affected_documents,
        risks=tuple(dict.fromkeys((*policy_result.payload.risks, *evidence.risks))),
        assumptions=tuple(
            dict.fromkeys((*policy_result.payload.assumptions, *evidence.assumptions))
        ),
        strategic_update_recommendation=policy_result.payload.strategic_update_recommendation,
        decision_requested=policy_result.payload.decision_requested,
        recommended_resume_target=policy_result.payload.recommended_resume_target,
        fingerprint=policy_result.payload.fingerprint,
        allowed_decisions=policy_result.payload.allowed_decisions,
        strategic_document_update_requirements=policy_result.payload.strategic_document_update_requirements,
    )
    return AlignmentPolicyResult(
        level=level,
        triggered_rules=policy_result.triggered_rules,
        score=policy_result.score,
        payload=payload,
    )


def _policy_text(input_data: AlignmentPolicyInput) -> str:
    parts = [
        input_data.step_name,
        input_data.user_input,
        input_data.proposed_scope,
        input_data.non_scope,
    ]
    parts.extend(input_data.artifacts.values())
    return "\n".join(part for part in parts if part).lower()


def _explicit_alignment_requested(text: str) -> bool:
    return bool(
        re.search(
            r"\b(align first|alignment checkpoint|pre[- ]?align|align before|alignment first)\b",
            text,
        )
        or any(
            token in text
            for token in (
                "先對標",
                "先校準",
                "先確認方向",
                "對標一下",
                "校準一下",
                "確認方向",
                "對標",
                "校準",
            )
        )
    )


def _matches_keywords(text: str, keywords: Sequence[str]) -> bool:
    return any(keyword.lower() in text for keyword in keywords)


def _matches_axis_escalation(text: str, axis_name: str) -> bool:
    """Return whether the request has actionable impact on an escalated axis.

    Product artifacts routinely mention headings such as "scope" and
    "principles", including explicit statements that positioning is unchanged.
    Those mentions are context, not a product-direction decision. Require a
    non-negated change/decision action near a strategic product target before
    forcing a product-scope checkpoint.
    """
    if axis_name == "product_scope":
        return _matches_strategic_change_intent(text)
    return _matches_keywords(text, AXIS_KEYWORDS.get(axis_name, (axis_name,)))


def _matches_strategic_change_intent(text: str) -> bool:
    for action in _STRATEGIC_CHANGE_ACTION_RE.finditer(text):
        line_start = text.rfind("\n", 0, action.start()) + 1
        line_end = text.find("\n", action.end())
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end]
        line_prefix = text[line_start : action.start()].rstrip()
        nearby_prefix = line_prefix[-32:]
        if any(marker in line.lower() for marker in _NON_SCOPE_LINE_MARKERS):
            continue
        if _NEGATED_ACTION_PREFIX_RE.search(nearby_prefix):
            continue

        window_start = max(line_start, action.start() - 120)
        window_end = min(line_end, action.end() + 120)
        window = text[window_start:window_end]
        if any(pattern.search(window) for pattern in _STRATEGIC_TARGET_RES):
            return True
    return False


def _detect_document_categories(text: str) -> list[str]:
    categories: list[str] = []
    for category, keywords in DOCUMENT_KEYWORDS.items():
        if _matches_keywords(text, keywords):
            categories.append(category)
    return categories


def _detect_required_update_categories(text: str, explicit: Sequence[str]) -> list[str]:
    categories = list(dict.fromkeys(explicit))
    for category, keywords in DOCUMENT_KEYWORDS.items():
        if not _mentions_document_update(text, keywords):
            continue
        if category not in categories:
            categories.append(category)
    return categories


def _mentions_document_update(text: str, keywords: Sequence[str]) -> bool:
    english_verbs = r"(?:update|revise|change|modify|create|add)"
    english_nouns = (
        r"(?:update|updates|revision|revisions|change|changes|modification|modifications)"
    )
    chinese_verbs = "(?:更新|修訂|修改|調整|建立|新增)"

    for keyword in keywords:
        escaped = re.escape(keyword.lower())
        if re.search(rf"\b{english_verbs}\b[^\n]{{0,48}}\b{escaped}\b", text):
            return True
        if re.search(rf"\b{escaped}\b[^\n]{{0,48}}\b{english_nouns}\b", text):
            return True
        if re.search(rf"{chinese_verbs}.{{0,24}}{escaped}", text):
            return True
        if re.search(rf"{escaped}.{{0,24}}{chinese_verbs}", text):
            return True
    return False


def _score_signals(
    text: str,
    affected_categories: Sequence[str],
    strategic_context: StrategicContext,
) -> list[TriggeredRule]:
    rules: list[TriggeredRule] = []
    trusted_boundary = _matches_keywords(text, TRUSTED_CAPABILITY_BOUNDARY_KEYWORDS)
    strategic_change = _matches_strategic_change_intent(text)
    if trusted_boundary:
        rules.append(
            TriggeredRule(
                "trusted_capability_boundary",
                "Trusted host capability or trust boundary change detected.",
                "high",
                5,
            )
        )
    if strategic_change:
        rules.append(
            TriggeredRule(
                "product_or_governance_impact",
                "Product or governance impact detected.",
                "medium",
                3,
            )
        )
    if _matches_keywords(
        text, ("external mutation", "external api", "publish", "deploy", "host-side")
    ):
        rules.append(
            TriggeredRule("external_mutation_risk", "External mutation risk detected.", "medium", 2)
        )
    if _matches_keywords(text, ("large", "ambiguous", "unclear", "broad")):
        rules.append(
            TriggeredRule(
                "large_ambiguous_issue", "Large or ambiguous issue signal detected.", "low", 1
            )
        )
    if _matches_keywords(
        text, ("architecture outside", "outside obvious scope", "cross-module architecture")
    ):
        rules.append(
            TriggeredRule(
                "architecture_scope_risk", "Architecture scope risk detected.", "medium", 2
            )
        )
    if strategic_change or trusted_boundary or _explicit_alignment_requested(text):
        for category in affected_categories:
            doc = strategic_context.document(category)
            if doc.status in {"missing", "draft"}:
                rules.append(
                    TriggeredRule(
                        f"strategic_document_{doc.status}:{category}",
                        f"Relevant strategic document '{category}' is {doc.status}.",
                        "medium",
                        3,
                    )
                )
    return rules


def _should_include_configured_documents(
    *,
    text: str,
    triggered_rules: Sequence[TriggeredRule],
    required_categories: Sequence[str],
) -> bool:
    if required_categories:
        return True
    if any(rule.risk_level == "high" for rule in triggered_rules):
        return True
    if _matches_keywords(text, TRUSTED_CAPABILITY_BOUNDARY_KEYWORDS):
        return True
    return _matches_keywords(
        text,
        (
            "product",
            "roadmap",
            "governance",
            "positioning",
            "principles",
            "product direction",
            "strategic context",
            "mandate",
            "business model",
            "產品",
            "產品方向",
            "路線圖",
            "治理",
            "定位",
            "原則",
            "商業模式",
        ),
    )


def _document_requirement(doc: StrategicDocumentMetadata) -> StrategicDocumentUpdateRequirement:
    return StrategicDocumentUpdateRequirement(
        category=doc.category,
        configured_path=doc.path,
        current_status=doc.status,
        current_sha256=doc.sha256,
        required_action="update_or_create_document",
    )


def _build_payload(
    *,
    input_data: AlignmentPolicyInput,
    strategic_context: StrategicContext,
    triggered_rules: tuple[TriggeredRule, ...],
    risk_level: str,
    affected_documents: tuple[StrategicDocumentMetadata, ...],
    requirements: tuple[StrategicDocumentUpdateRequirement, ...],
    risks: tuple[str, ...],
    assumptions: tuple[str, ...],
) -> AlignmentCheckpointPayload:
    if requirements:
        recommendation = (
            "Update affected strategic documents first, narrow scope, or explicitly defer/reject."
        )
    elif any(doc.status in {"missing", "draft"} for doc in affected_documents):
        recommendation = "Review missing or draft strategic documents before relying on them."
    else:
        recommendation = "No strategic document update is required by policy."

    payload_without_fingerprint = {
        "step_name": input_data.step_name,
        "playbook_id": input_data.playbook_id,
        "user_input": input_data.user_input,
        "artifacts": input_data.artifacts,
        "triggered_rules": [rule.to_dict() for rule in triggered_rules],
        "affected_documents": [doc.to_dict() for doc in affected_documents],
        "requirements": [req.to_dict() for req in requirements],
        "strategic_context_hashes": strategic_context.document_hashes(
            [doc.category for doc in affected_documents] + [req.category for req in requirements]
        ),
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload_without_fingerprint, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    return AlignmentCheckpointPayload(
        interpreted_goal=input_data.user_input.strip()
        or f"Continue workflow step '{input_data.step_name}'.",
        proposed_scope=input_data.proposed_scope.strip()
        or "Proceed with the current workflow step scope.",
        non_scope=input_data.non_scope.strip()
        or "Do not treat alignment as host capability approval.",
        triggered_rules=triggered_rules,
        risk_level=risk_level,
        affected_documents=affected_documents,
        risks=risks,
        assumptions=assumptions,
        strategic_update_recommendation=recommendation,
        decision_requested="Choose how the workflow should proceed from this alignment checkpoint.",
        recommended_resume_target=input_data.step_name,
        fingerprint=fingerprint,
        strategic_document_update_requirements=requirements,
    )


def _default_risks(level: AlignmentDecisionLevel) -> Iterable[str]:
    if level == AlignmentDecisionLevel.MUST_ALIGN:
        return ("Policy requires a user decision before execution can continue.",)
    return ("Policy recorded a medium-risk alignment note without pausing.",)
