"""Strategic context loading for policy-based workflow decisions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml


DEFAULT_DOCUMENT_CATEGORIES = (
    "roadmap",
    "product_direction",
    "principles",
    "positioning",
    "strategic_context",
)


@dataclass(frozen=True)
class StrategicDocumentMetadata:
    """Metadata for one strategic document category."""

    category: str
    path: Optional[str] = None
    status: str = "missing"
    sha256: Optional[str] = None
    exists: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "path": self.path,
            "status": self.status,
            "sha256": self.sha256,
            "exists": self.exists,
        }


@dataclass(frozen=True)
class AxisRule:
    """Effective authority level for one mandate axis."""

    name: str
    level: str
    grounds: tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "level": self.level, "grounds": list(self.grounds)}


@dataclass(frozen=True)
class StrategicContext:
    """Resolved repo and issue-level strategic context."""

    version: int
    project_root: Path
    issue_name: Optional[str]
    playbook_id: Optional[str]
    documents: Dict[str, StrategicDocumentMetadata]
    axes: Dict[str, AxisRule]
    out_of_mandate: tuple[str, ...]
    notes: str = ""

    def document(self, category: str) -> StrategicDocumentMetadata:
        key = str(category).strip()
        return self.documents.get(key) or StrategicDocumentMetadata(category=key)

    def document_hashes(self, categories: Optional[Iterable[str]] = None) -> Dict[str, Optional[str]]:
        selected = categories if categories is not None else self.documents.keys()
        return {category: self.document(category).sha256 for category in selected}


def load_strategic_context(project_root: Path | str = Path.cwd(), issue_name: Optional[str] = None) -> StrategicContext:
    """Load `.cafe/strategic_context.yaml` and resolve issue overrides."""
    root = Path(project_root).resolve()
    config_path = root / ".cafe" / "strategic_context.yaml"
    if not config_path.exists():
        return StrategicContext(
            version=1,
            project_root=root,
            issue_name=issue_name,
            playbook_id=None,
            documents=_default_documents(root, config_path, config_exists=False),
            axes={},
            out_of_mandate=(),
        )

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        # An unreadable or unparseable strategic context must not crash the
        # workflow; degrade to the missing-file shape so the alignment policy
        # surfaces it as a document update requirement instead.
        return StrategicContext(
            version=1,
            project_root=root,
            issue_name=issue_name,
            playbook_id=None,
            documents=_default_documents(root, config_path, config_exists=False),
            axes={},
            out_of_mandate=(),
        )
    if not isinstance(raw, dict):
        raw = {}
    mandate = _as_dict(raw.get("mandate"))
    issue_overrides = _as_dict(_as_dict(raw.get("issues")).get(str(issue_name))) if issue_name else {}

    documents_raw = _deep_merge(_as_dict(raw.get("documents")), _as_dict(issue_overrides.get("documents")))
    documents = _resolve_documents(
        root=root,
        config_path=config_path,
        documents_raw=documents_raw,
    )

    axes = _resolve_axes(
        _deep_merge(
            _as_dict(mandate.get("axes")),
            _as_dict(issue_overrides.get("axes")),
        )
    )
    out_of_mandate = _resolve_out_of_mandate(mandate, issue_overrides)
    notes = "\n\n".join(
        item.strip()
        for item in (str(mandate.get("notes", "")), str(issue_overrides.get("notes", "")))
        if item.strip()
    )

    return StrategicContext(
        version=int(raw.get("version", 1) or 1),
        project_root=root,
        issue_name=issue_name,
        # Legacy playbook fields in strategic_context.yaml are intentionally
        # non-authoritative. Playbook selection belongs to the issue contract.
        playbook_id=None,
        documents=documents,
        axes=axes,
        out_of_mandate=out_of_mandate,
        notes=notes,
    )


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, tuple):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return ()


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _default_documents(root: Path, config_path: Path, *, config_exists: bool) -> Dict[str, StrategicDocumentMetadata]:
    documents = {
        category: StrategicDocumentMetadata(category=category)
        for category in DEFAULT_DOCUMENT_CATEGORIES
    }
    documents["strategic_context"] = StrategicDocumentMetadata(
        category="strategic_context",
        path=str(config_path.relative_to(root)),
        status="exists" if config_exists else "missing",
        sha256=_sha256(config_path) if config_exists else None,
        exists=config_exists,
    )
    return documents


def _resolve_documents(
    *,
    root: Path,
    config_path: Path,
    documents_raw: Dict[str, Any],
) -> Dict[str, StrategicDocumentMetadata]:
    documents = _default_documents(root, config_path, config_exists=True)
    for category, raw_value in documents_raw.items():
        raw_doc = _as_dict(raw_value)
        rel_path = raw_doc.get("path")
        path = str(rel_path).strip() if rel_path else None
        status = str(raw_doc.get("status") or ("exists" if path else "missing")).strip() or "missing"
        absolute = (root / path).resolve() if path else None
        exists = bool(absolute and absolute.exists())
        documents[str(category)] = StrategicDocumentMetadata(
            category=str(category),
            path=path,
            status=status,
            sha256=_sha256(absolute) if exists and absolute is not None else None,
            exists=exists,
        )
    return documents


def _resolve_axes(axes_raw: Dict[str, Any]) -> Dict[str, AxisRule]:
    axes: Dict[str, AxisRule] = {}
    for name, raw_value in axes_raw.items():
        raw_axis = _as_dict(raw_value)
        axes[str(name)] = AxisRule(
            name=str(name),
            level=str(raw_axis.get("level", "agent")),
            grounds=_as_string_tuple(raw_axis.get("grounds")),
        )
    return axes


def _resolve_out_of_mandate(mandate: Dict[str, Any], issue_overrides: Dict[str, Any]) -> tuple[str, ...]:
    if "out_of_mandate" in issue_overrides:
        return _as_string_tuple(issue_overrides.get("out_of_mandate"))
    return _as_string_tuple(mandate.get("out_of_mandate"))


def _sha256(path: Path) -> Optional[str]:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None
