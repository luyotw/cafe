"""Read-only resolution shared by playbook, phase-skill, and agent catalogs."""

from __future__ import annotations

import fcntl
import hashlib
import os
import stat
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

import yaml


class CatalogValidationError(ValueError):
    """Raised when the highest-precedence catalog entry is invalid."""


class CatalogKind(str, Enum):
    """Catalog types with stable public identifiers."""

    PLAYBOOK = "playbook"
    PHASE = "phase"
    AGENT = "agent"


@dataclass(frozen=True)
class ProjectRoots:
    """The active invocation root and canonical checkout root."""

    active: Path
    canonical: Path
    git_discovered: bool = field(default=False, compare=False, repr=False)


@dataclass(frozen=True)
class CatalogEntry:
    """One validated effective catalog entry."""

    kind: CatalogKind
    key: str
    source: str
    path: Path
    digest: str
    project_layer: Optional[str] = None

    @property
    def entry_id(self) -> str:
        return f"{self.kind.value}:{self.key}"


GitRunner = Callable[[tuple[str, ...], Path], str]


_catalog_lock_state = threading.local()


@contextmanager
def global_catalog_lock(global_root: Path, *, exclusive: bool = False) -> Iterator[None]:
    """Coordinate catalog readers and publishers without mutating the catalog."""
    lock_root = Path(global_root).resolve().parent
    if not lock_root.exists():
        if not exclusive:
            yield
            return
        lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = str(lock_root)
    held = getattr(_catalog_lock_state, "held", None)
    if held is None:
        held = {}
        _catalog_lock_state.held = held
    state = held.get(key)
    if state is not None:
        if exclusive and not state["exclusive"]:
            raise RuntimeError("Cannot upgrade a shared catalog lock")
        state["depth"] += 1
        try:
            yield
        finally:
            state["depth"] -= 1
        return

    descriptor = os.open(lock_root, os.O_RDONLY)
    fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
    held[key] = {"depth": 1, "exclusive": exclusive, "descriptor": descriptor}
    try:
        yield
    finally:
        held.pop(key, None)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _run_git(args: tuple[str, ...], cwd: Path) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "Git root discovery failed")
    return result.stdout.strip()


def _nearest_project_root(start: Path) -> Path:
    current = start.resolve()
    while current != current.parent:
        if (current / ".cafe").exists() or (current / ".git").exists():
            return current
        current = current.parent
    return start.resolve()


def discover_project_roots(
    start: Path, *, git_runner: GitRunner = _run_git
) -> ProjectRoots:
    """Discover the active checkout and canonical repository without writing either."""
    start = Path(start).resolve()
    try:
        active = Path(git_runner(("rev-parse", "--show-toplevel"), start)).resolve()
        common_git = Path(
            git_runner(
                ("rev-parse", "--path-format=absolute", "--git-common-dir"),
                start,
            )
        ).resolve()
        canonical = common_git.parent if common_git.name == ".git" else active
        return ProjectRoots(active=active, canonical=canonical, git_discovered=True)
    except (OSError, TypeError, ValueError):
        root = _nearest_project_root(start)
        return ProjectRoots(active=root, canonical=root)


def content_digest(path: Path) -> str:
    """Hash validated file/tree content, modes, and symlink targets deterministically."""
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return "missing"

    digest = hashlib.sha256()

    def add_node(node: Path, relative: str) -> None:
        metadata = node.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if node.is_symlink():
            digest.update(f"L\0{relative}\0{mode:o}\0{os.readlink(node)}\0".encode())
        elif node.is_file():
            digest.update(f"F\0{relative}\0{mode:o}\0".encode())
            with node.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
            digest.update(b"\0")
        elif node.is_dir():
            digest.update(f"D\0{relative}\0{mode:o}\0".encode())
        else:
            raise CatalogValidationError(f"Unsupported catalog node: {node}")

    add_node(path, ".")
    if path.is_dir() and not path.is_symlink():
        for child in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
            add_node(child, child.relative_to(path).as_posix())
    return digest.hexdigest()


class CatalogResolver:
    """Resolve all supported catalogs through one project-first contract."""

    _DIRECTORIES = {
        CatalogKind.PLAYBOOK: "playbooks",
        CatalogKind.PHASE: "skills",
        CatalogKind.AGENT: "agents",
    }

    def __init__(
        self,
        *,
        project_root: Optional[Path] = None,
        canonical_root: Optional[Path] = None,
        global_root: Optional[Path] = None,
        builtin_root: Optional[Path] = None,
        git_runner: GitRunner = _run_git,
    ) -> None:
        requested_root = Path(project_root).resolve() if project_root else None
        roots = discover_project_roots(requested_root or Path.cwd(), git_runner=git_runner)
        self.project_root = (
            roots.active if roots.git_discovered or requested_root is None else requested_root
        )
        if canonical_root:
            self.canonical_root = Path(canonical_root).resolve()
        elif roots.git_discovered or requested_root is None:
            self.canonical_root = roots.canonical
        else:
            self.canonical_root = requested_root
        if global_root is None:
            from cafe.utils import config

            global_root = config.get_global_cafe_dir()
        self.global_root = Path(global_root).resolve()
        self.builtin_root = Path(
            builtin_root or (Path(__file__).resolve().parent.parent / "data")
        ).resolve()

    @staticmethod
    def _validate_key(kind: CatalogKind, key: str) -> str:
        normalized = key.removesuffix(".yaml") if kind is CatalogKind.PLAYBOOK else key
        parts = normalized.split("/")
        expected = 2 if kind is CatalogKind.AGENT else 1
        if (
            len(parts) != expected
            or any(not part or part in {".", ".."} for part in parts)
            or any("\\" in part for part in parts)
        ):
            raise CatalogValidationError(f"Invalid {kind.value} key: {key}")
        return normalized

    def catalog_roots(self, kind: CatalogKind) -> list[tuple[str, Path, Optional[str]]]:
        """Return roots from lowest to highest precedence for adapter discovery."""
        directory = self._DIRECTORIES[kind]
        roots: list[tuple[str, Path, Optional[str]]] = [
            ("builtin", self.builtin_root / directory, None),
            ("global", self.global_root / directory, None),
        ]
        if self.canonical_root != self.project_root:
            roots.append(
                ("project", self.canonical_root / ".cafe" / directory, "canonical")
            )
        roots.append(("project", self.project_root / ".cafe" / directory, "active"))
        return roots

    @staticmethod
    def _relative_entry_path(kind: CatalogKind, key: str) -> Path:
        if kind is CatalogKind.PLAYBOOK:
            return Path(f"{key}.yaml")
        if kind is CatalogKind.PHASE:
            return Path(key)
        role, name = key.split("/", 1)
        return Path(role) / f"{name}.md"

    def candidate_path(self, kind: CatalogKind, key: str, root: Path) -> Path:
        key = self._validate_key(kind, key)
        return root / self._relative_entry_path(kind, key)

    def _validate_entry(self, kind: CatalogKind, key: str, path: Path) -> None:
        if kind is CatalogKind.PLAYBOOK:
            if not path.is_file():
                raise CatalogValidationError(f"Playbook is not a file: {path}")
            try:
                document = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, yaml.YAMLError) as exc:
                raise CatalogValidationError(f"Invalid playbook {key}: {exc}") from exc
            if not isinstance(document, dict):
                raise CatalogValidationError(f"Invalid playbook {key}: expected a mapping")
            return
        if kind is CatalogKind.PHASE:
            marker = path / "SKILL.md"
            if not path.is_dir() or not marker.is_file():
                raise CatalogValidationError(f"Invalid phase skill {key}: SKILL.md is required")
            return
        if not path.is_file():
            raise CatalogValidationError(f"Invalid agent {key}: markdown file is required")

    def _resolve_unlocked(self, kind: CatalogKind, key: str) -> CatalogEntry:
        key = self._validate_key(kind, key)
        for source, root, project_layer in reversed(self.catalog_roots(kind)):
            path = self.candidate_path(kind, key, root)
            if not path.exists() and not path.is_symlink():
                continue
            self._validate_entry(kind, key, path)
            return CatalogEntry(
                kind=kind,
                key=key,
                source=source,
                path=path,
                digest=content_digest(path),
                project_layer=project_layer,
            )
        raise FileNotFoundError(f"{kind.value.title()} not found: {key}")

    def resolve(self, kind: CatalogKind, key: str) -> CatalogEntry:
        with global_catalog_lock(self.global_root):
            return self._resolve_unlocked(kind, key)

    def _keys_at_root(self, kind: CatalogKind, root: Path) -> Iterator[str]:
        if not root.exists():
            return
        if kind is CatalogKind.PLAYBOOK:
            yield from (item.stem for item in root.glob("*.yaml"))
        elif kind is CatalogKind.PHASE:
            yield from (item.name for item in root.iterdir() if item.is_dir())
        else:
            for role_dir in root.iterdir():
                if role_dir.is_dir():
                    yield from (
                        f"{role_dir.name}/{item.stem}" for item in role_dir.glob("*.md")
                    )

    def _keys_unlocked(self, kind: CatalogKind) -> list[str]:
        keys: set[str] = set()
        for _source, root, _layer in self.catalog_roots(kind):
            keys.update(self._keys_at_root(kind, root))
        return sorted(keys)

    def keys(self, kind: CatalogKind) -> list[str]:
        with global_catalog_lock(self.global_root):
            return self._keys_unlocked(kind)

    def entries(self, kinds: Optional[Iterable[CatalogKind]] = None) -> list[CatalogEntry]:
        selected = list(kinds or CatalogKind)
        with global_catalog_lock(self.global_root):
            return [
                self._resolve_unlocked(kind, key)
                for kind in selected
                for key in self._keys_unlocked(kind)
            ]

    def project_entries(
        self, kinds: Optional[Iterable[CatalogKind]] = None
    ) -> list[CatalogEntry]:
        """Return the effective canonical-plus-active project view only."""
        selected = list(kinds or CatalogKind)
        results: list[CatalogEntry] = []
        for kind in selected:
            project_roots = [item for item in self.catalog_roots(kind) if item[0] == "project"]
            keys: set[str] = set()
            for _source, root, _layer in project_roots:
                keys.update(self._keys_at_root(kind, root))
            for key in sorted(keys):
                for _source, root, layer in reversed(project_roots):
                    path = self.candidate_path(kind, key, root)
                    if path.exists() or path.is_symlink():
                        self._validate_entry(kind, key, path)
                        results.append(
                            CatalogEntry(
                                kind=kind,
                                key=key,
                                source="project",
                                path=path,
                                digest=content_digest(path),
                                project_layer=layer,
                            )
                        )
                        break
        return results
