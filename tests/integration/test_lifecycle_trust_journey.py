from pathlib import Path
from types import SimpleNamespace

import pytest

from cafe.core.execution_boundary import ExecutionClass, TrustSource
from cafe.core.lifecycle_trust import LifecycleTrustStore, declare_lifecycle_trust, run_lifecycle


def test_user_trusts_runs_invalidates_and_revokes_lifecycle_script(tmp_path: Path) -> None:
    script = tmp_path / "prepare.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    store = LifecycleTrustStore(tmp_path / "user-home" / ".cafe" / "lifecycle-trust.yaml")
    declaration = declare_lifecycle_trust(store, script=script, stage="prepare", cwd=tmp_path, writable_roots=(tmp_path,))
    requests = []
    executor = SimpleNamespace(run=lambda request: requests.append(request) or SimpleNamespace(returncode=0))

    run_lifecycle(store, declaration.id, stage="prepare", executor=executor)
    assert requests[0].execution_class is ExecutionClass.LIFECYCLE
    assert requests[0].trust_source is TrustSource.USER_DECLARATION
    assert requests[0].boundary.network_destinations == ()

    script.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    with pytest.raises(ValueError):
        run_lifecycle(store, declaration.id, stage="prepare", executor=executor)
    assert store.revoke(declaration.id) is True
    assert store.list() == ()
