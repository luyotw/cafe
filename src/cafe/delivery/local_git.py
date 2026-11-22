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
        yield lock
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


def install(root, strategy, branch, base, source):
    """Prepare the candidate off-checkout, then install only against the pinned HEAD."""
    from cafe.core.git import GitOperations

    index = Path(_git(root, "rev-parse", "--path-format=absolute", "--git-path", "index"))
    with tempfile.TemporaryDirectory(prefix=".cafe-integration-", dir=index.parent) as temporary:
        scratch = Path(temporary)
        candidate = _candidate(root, base, source, strategy, scratch)
        with _index_reservation(index) as lock:
            # Git dereferences HEAD and locks its underlying branch in the same
            # transaction. Check the symbolic branch after those locks are held.
            with subprocess.Popen(
                ["git", "-C", str(root), "update-ref", "-m", "CAFE: approved integration", "--stdin"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            ) as transaction:
                try:
                    _exchange(transaction, "start", "start: ok")
                    _exchange(transaction, f"update HEAD {candidate} {base}\nprepare", "prepare: ok")
                    if (_git(root, "symbolic-ref", "HEAD") != f"refs/heads/{branch}"
                            or GitOperations(str(root)).has_in_progress_operation()
                            or _git(root, "status", "--porcelain", "--untracked-files=all")):
                        raise ValueError("target checkout changed after approval")
                    prepared_index = scratch / "index"
                    shutil.copyfile(index, prepared_index)
                    env = {**os.environ, "GIT_INDEX_FILE": str(prepared_index)}
                    _git(root, "read-tree", "-m", "-u", base, candidate, env=env)
                    shutil.copyfile(prepared_index, lock)
                    _exchange(transaction, "commit", "commit: ok")
                    lock.replace(index)
                finally:
                    transaction.stdin.close()
                if transaction.wait():
                    raise ValueError("Git did not close the integration transaction")
    return candidate


if __name__ == "__main__":
    try:
        print(install(Path(sys.argv[1]), *sys.argv[2:]))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
