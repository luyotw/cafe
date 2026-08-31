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


class CatalogOperationLimitError(CatalogValidationError):
    """Raised when one catalog operation exceeds its durable entry budget."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"Catalog operation exceeds the {limit}-entry limit")


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

MAX_CATALOG_NODES = 10_000
MAX_CATALOG_BYTES = 64 * 1024 * 1024
MAX_CATALOG_DEPTH = 64
MAX_CATALOG_OPERATION_ENTRIES = 512


def bounded_directory_names(
    directory: Path | int,
    *,
    max_entries: int,
    limit_error: Callable[[], Exception],
) -> list[str]:
    """Collect sortable entry names without reading beyond the declared bound."""
    names: list[str] = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if len(names) >= max_entries:
                raise limit_error()
            names.append(entry.name)
    return sorted(names)


@contextmanager
def global_catalog_lock(global_root: Path, *, exclusive: bool = False) -> Iterator[None]:
    """Recover and coordinate catalog readers and publishers under one lock."""
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
    # Readers also enter exclusively so crash recovery completes before any
    # catalog path or content can be exposed.
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    try:
        from cafe.catalogs.transactions import recover_catalog_transactions

        recover_catalog_transactions(Path(global_root).resolve())
        held[key] = {"depth": 1, "exclusive": True, "descriptor": descriptor}
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


def discover_project_roots(start: Path, *, git_runner: GitRunner = _run_git) -> ProjectRoots:
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


def content_digest(
    path: Path,
    *,
    max_nodes: int = MAX_CATALOG_NODES,
    max_bytes: int = MAX_CATALOG_BYTES,
    max_depth: int = MAX_CATALOG_DEPTH,
    root_symlink_base: Optional[Path] = None,
) -> str:
    """Hash one confined catalog entry with deterministic resource bounds."""
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return "missing"
    if max_nodes < 1 or max_bytes < 0 or max_depth < 0:
        raise CatalogValidationError("Catalog digest bounds must be non-negative")

    if root_symlink_base is not None:
        authority_root = Path(root_symlink_base).resolve(strict=True)
    elif path.is_symlink() or not path.is_dir():
        authority_root = path.parent.resolve(strict=True)
    else:
        authority_root = path.resolve(strict=True)
    if not authority_root.is_dir():
        raise CatalogValidationError("Catalog digest authority must be a directory")

    materialize_nested_symlinks = path.is_symlink()
    digest_root = path
    if path.is_symlink():
        target_base = Path(root_symlink_base) if root_symlink_base is not None else path.parent
        target_path = Path(os.readlink(path))
        if not target_path.is_absolute():
            target_path = target_base / target_path
        try:
            prospective_target = target_path.resolve(strict=False)
            if not prospective_target.is_relative_to(authority_root):
                raise CatalogValidationError(
                    f"Catalog symlink target escapes entry authority: {path}"
                )
            digest_root = target_path.resolve(strict=True)
            if not digest_root.is_relative_to(authority_root):
                raise CatalogValidationError(
                    f"Catalog symlink target escapes entry authority: {path}"
                )
        except (OSError, RuntimeError) as exc:
            raise CatalogValidationError(f"Catalog symlink target is unavailable: {path}") from exc

    digest = hashlib.sha256()
    active_nodes: set[tuple[int, int, int]] = set()
    node_count = 0
    byte_count = 0

    def add_tree(node: Path, relative: str, depth: int) -> None:
        nonlocal byte_count, node_count
        if depth > max_depth:
            raise CatalogValidationError(f"Catalog entry exceeds digest depth limit: {path}")
        node_count += 1
        if node_count > max_nodes:
            raise CatalogValidationError(f"Catalog entry exceeds digest node limit: {path}")
        metadata = node.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if node.is_symlink():
            link_target = os.readlink(node)
            target_base = (
                Path(root_symlink_base)
                if relative == "." and root_symlink_base is not None
                else node.parent
            )
            target_path = Path(link_target)
            if not target_path.is_absolute():
                target_path = target_base / target_path
            try:
                prospective_target = target_path.resolve(strict=False)
                if not prospective_target.is_relative_to(authority_root):
                    raise CatalogValidationError(
                        f"Catalog symlink target escapes entry authority: {node}"
                    )
                target = target_path.resolve(strict=True)
                if not target.is_relative_to(authority_root):
                    raise CatalogValidationError(
                        f"Catalog symlink target escapes entry authority: {node}"
                    )
            except (OSError, RuntimeError):
                if materialize_nested_symlinks:
                    raise CatalogValidationError(
                        f"Catalog symlink target is unavailable: {node}"
                    )
                digest.update(f"L\0{relative}\0{mode:o}\0{link_target}\0".encode())
                digest.update(f"T\0{relative}\0missing\0".encode())
            else:
                if materialize_nested_symlinks:
                    target_metadata = target.lstat()
                    target_identity = (
                        target_metadata.st_dev,
                        target_metadata.st_ino,
                        stat.S_IFMT(target_metadata.st_mode),
                    )
                    if target_identity in active_nodes:
                        raise CatalogValidationError(
                            f"Catalog symlink cycle cannot be materialized: {node}"
                        )
                    add_tree(target, relative, depth)
                    return
                digest.update(f"L\0{relative}\0{mode:o}\0{link_target}\0".encode())
                digest.update(f"T\0{relative}\0".encode())
                add_tree(target, f"{relative}/<target>", depth + 1)
            return

        identity = (metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode))
        if identity in active_nodes:
            digest.update(f"C\0{relative}\0".encode())
            return
        active_nodes.add(identity)
        try:
            if stat.S_ISREG(metadata.st_mode):
                digest.update(f"F\0{relative}\0{mode:o}\0".encode())
                with node.open("rb") as handle:
                    while True:
                        remaining = max_bytes - byte_count
                        chunk = handle.read(min(65536, remaining + 1))
                        if not chunk:
                            break
                        byte_count += len(chunk)
                        if byte_count > max_bytes:
                            raise CatalogValidationError(
                                f"Catalog entry exceeds digest byte limit: {path}"
                            )
                        digest.update(chunk)
                digest.update(b"\0")
            elif stat.S_ISDIR(metadata.st_mode):
                digest.update(f"D\0{relative}\0{mode:o}\0".encode())
                names = bounded_directory_names(
                    node,
                    max_entries=max_nodes - node_count,
                    limit_error=lambda: CatalogValidationError(
                        f"Catalog entry exceeds digest node limit: {path}"
                    ),
                )
                for name in names:
                    add_tree(node / name, f"{relative}/{name}", depth + 1)
            else:
                raise CatalogValidationError(f"Unsupported catalog node: {node}")
        finally:
            active_nodes.remove(identity)

    add_tree(digest_root, ".", 0)
    return digest.hexdigest()


def read_valid_agent_definition(path: Path, key: str) -> str:
    """Read one agent definition and validate its required identity metadata."""
    if not path.is_file():
        raise CatalogValidationError(f"Invalid agent {key}: markdown file is required")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CatalogValidationError(f"Invalid agent {key}: unreadable UTF-8 content") from exc
    if not text.startswith("---\n"):
        raise CatalogValidationError(f"Invalid agent {key}: YAML frontmatter is required")
    end = text.find("\n---", 4)
    if end < 0:
        raise CatalogValidationError(f"Invalid agent {key}: unterminated frontmatter")
    try:
        metadata = yaml.safe_load(text[4:end])
    except yaml.YAMLError as exc:
        raise CatalogValidationError(f"Invalid agent {key}: malformed frontmatter") from exc
    expected_name = key.split("/", 1)[1]
    if not isinstance(metadata, dict) or metadata.get("name") != expected_name:
        raise CatalogValidationError(
            f"Invalid agent {key}: frontmatter name must match {expected_name!r}"
        )
    return text


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
            roots.append(("project", self.canonical_root / ".cafe" / directory, "canonical"))
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
        read_valid_agent_definition(path, key)

    def _resolve_unlocked(self, kind: CatalogKind, key: str) -> CatalogEntry:
        key = self._validate_key(kind, key)
        roots = [
            (source, root, project_layer, self._catalog_root_available(root))
            for source, root, project_layer in self.catalog_roots(kind)
        ]
        for source, root, project_layer, available in reversed(roots):
            if not available:
                continue
            path = self.candidate_path(kind, key, root)
            if not path.exists() and not path.is_symlink():
                continue
            entry_digest = content_digest(path)
            self._validate_entry(kind, key, path)
            return CatalogEntry(
                kind=kind,
                key=key,
                source=source,
                path=path,
                digest=entry_digest,
                project_layer=project_layer,
            )
        raise FileNotFoundError(f"{kind.value.title()} not found: {key}")

    def resolve(self, kind: CatalogKind, key: str) -> CatalogEntry:
        with global_catalog_lock(self.global_root):
            return self._resolve_unlocked(kind, key)

    def _keys_at_root(self, kind: CatalogKind, root: Path) -> Iterator[str]:
        if not self._catalog_root_available(root):
            return

        if kind is CatalogKind.PLAYBOOK:
            yield from (item.stem for item in root.glob("*.yaml"))
        elif kind is CatalogKind.PHASE:
            yield from (item.name for item in root.iterdir() if item.is_symlink() or item.is_dir())
        else:
            for role_dir in root.iterdir():
                if role_dir.is_symlink():
                    raise CatalogValidationError(
                        f"Agent role directory must not be a symlink: {role_dir}"
                    )
                if role_dir.is_dir():
                    yield from (f"{role_dir.name}/{item.stem}" for item in role_dir.glob("*.md"))

    @staticmethod
    def _catalog_root_available(root: Path) -> bool:
        if root.is_symlink():
            raise CatalogValidationError(f"Catalog root is not a real directory: {root}")
        if not root.exists():
            return False
        if not root.is_dir():
            raise CatalogValidationError(f"Catalog root is not a real directory: {root}")
        return True

    def _keys_unlocked(
        self,
        kind: CatalogKind,
        *,
        max_entries: Optional[int] = None,
        operation_limit: Optional[int] = None,
    ) -> list[str]:
        keys: set[str] = set()
        for _source, root, _layer in self.catalog_roots(kind):
            for key in self._keys_at_root(kind, root):
                if key in keys:
                    continue
                if max_entries is not None and len(keys) >= max_entries:
                    raise CatalogOperationLimitError(operation_limit or max_entries)
                keys.add(key)
        return sorted(keys)

    def keys(self, kind: CatalogKind) -> list[str]:
        with global_catalog_lock(self.global_root):
            return self._keys_unlocked(kind)

    def entries(
        self,
        kinds: Optional[Iterable[CatalogKind]] = None,
        *,
        max_entries: Optional[int] = None,
    ) -> list[CatalogEntry]:
        selected = list(kinds or CatalogKind)
        with global_catalog_lock(self.global_root):
            results: list[CatalogEntry] = []
            for kind in selected:
                remaining = None if max_entries is None else max_entries - len(results)
                keys = self._keys_unlocked(
                    kind,
                    max_entries=remaining,
                    operation_limit=max_entries,
                )
                results.extend(self._resolve_unlocked(kind, key) for key in keys)
            return results

    def project_entries(
        self,
        kinds: Optional[Iterable[CatalogKind]] = None,
        *,
        max_entries: Optional[int] = None,
    ) -> list[CatalogEntry]:
        """Return the effective canonical-plus-active project view only."""
        selected = list(kinds or CatalogKind)
        results: list[CatalogEntry] = []
        for kind in selected:
            project_roots = [item for item in self.catalog_roots(kind) if item[0] == "project"]
            keys: set[str] = set()
            for _source, root, _layer in project_roots:
                for key in self._keys_at_root(kind, root):
                    if key in keys:
                        continue
                    if max_entries is not None and len(results) + len(keys) >= max_entries:
                        raise CatalogOperationLimitError(max_entries)
                    keys.add(key)
            for key in sorted(keys):
                for _source, root, layer in reversed(project_roots):
                    path = self.candidate_path(kind, key, root)
                    if path.exists() or path.is_symlink():
                        entry_digest = content_digest(path)
                        self._validate_entry(kind, key, path)
                        results.append(
                            CatalogEntry(
                                kind=kind,
                                key=key,
                                source="project",
                                path=path,
                                digest=entry_digest,
                                project_layer=layer,
                            )
                        )
                        break
        return results
