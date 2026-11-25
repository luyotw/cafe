"""Install a prepared integration with Git's checkout, index and ref locks."""

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def _git(root, *args, env=None):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, text=True,
        stdout=subprocess.PIPE, env=env,
    ).stdout.strip()


@contextmanager
def _index_reservation(index):
    lock = index.with_name(index.name + ".lock")
    with lock.open("xb") as handle:
        identity = os.fstat(handle.fileno())
    try:
        yield
    finally:
        if lock.exists() and os.path.samestat(lock.stat(), identity):
            lock.unlink()


def _exchange(transaction, command, expected):
    transaction.stdin.write(command + "\n")
    transaction.stdin.flush()
    if transaction.stdout.readline().strip() != expected:
        raise ValueError("Git did not acknowledge the integration ref transaction")


def _candidate(root, base, source, strategy, scratch):
    checkout = scratch / "merge"
    _git(root, "worktree", "add", "--detach", str(checkout), base)
    try:
        flag = {"ff-only": "--ff-only", "merge-commit": "--no-ff"}[strategy]
        _git(checkout, "merge", flag, "--no-edit", source)
        result, *parents = _git(checkout, "rev-list", "--parents", "-1", "HEAD").split()
        if (strategy == "ff-only" and result != source
                or strategy == "merge-commit" and parents != [base, source]):
            raise ValueError("candidate differs from the approved integration")
        return result
    finally:
        _git(root, "worktree", "remove", "--force", str(checkout))


def install(root, strategy, branch, base, source, source_branch, source_checkout):
    """Prepare off-checkout and publish only while both approved refs are locked."""
    from cafe.core.git import GitOperations

    index = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-path", "index"))
    with tempfile.TemporaryDirectory(prefix=".cafe-integration-", dir=index.parent) as temporary:
        scratch = Path(temporary)
        candidate = _candidate(root, base, source, strategy, scratch)
        with _index_reservation(index):
            # Git dereferences HEAD and locks its underlying branch in the same
            # transaction. Check the symbolic branch after those locks are held.
            with subprocess.Popen(
                ["git", "-C", str(root), "update-ref", "-m", "CAFE: approved integration", "--stdin"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            ) as transaction:
                try:
                    _exchange(transaction, "start", "start: ok")
                    source_ref = f"refs/heads/{source_branch}"
                    _exchange(
                        transaction,
                        f"verify {source_ref} {source}\nupdate HEAD {candidate} {base}\nprepare",
                        "prepare: ok",
                    )
                    if (_git(source_checkout, "symbolic-ref", "HEAD") != source_ref
                            or _git(source_checkout, "status", "--porcelain", "--untracked-files=all")):
                        raise ValueError("source checkout changed after approval")
                    if (_git(root, "symbolic-ref", "HEAD") != f"refs/heads/{branch}"
                            or GitOperations(str(root)).has_in_progress_operation()
                            or _git(root, "status", "--porcelain", "--untracked-files=all")):
                        raise ValueError("target checkout changed after approval")
                    _install_checkout(root, index, scratch, base, candidate, transaction)
                finally:
                    transaction.stdin.close()
                if transaction.wait():
                    raise ValueError("Git did not close the integration transaction")
    return candidate


def _install_checkout(root, index, scratch, base, candidate, transaction):
    original = scratch / "original-index"
    prepared = scratch / "prepared-index"
    shutil.copy2(index, original)
    shutil.copy2(original, prepared)
    env = {**os.environ, "GIT_INDEX_FILE": str(prepared)}
    _git(root, "read-tree", "-m", "-u", base, candidate, env=env)
    installed = False
    try:
        # Keep the index reservation separate until Git has acknowledged the ref
        # update. This also leaves the prepared index available for rollback.
        publish_index = scratch / "publish-index"
        shutil.copy2(prepared, publish_index)
        publish_index.replace(index)
        installed = True
        _exchange(transaction, "commit", "commit: ok")
    except (OSError, ValueError):
        if _git(root, "rev-parse", "HEAD") == base:
            _git(root, "read-tree", "-m", "-u", candidate, base, env=env)
            if installed:
                original.replace(index)
        raise


if __name__ == "__main__":
    try:
        print(install(Path(sys.argv[1]), *sys.argv[2:]))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
