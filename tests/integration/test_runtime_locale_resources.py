"""Runtime catalogs remain usable from built distributions outside the checkout."""

import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path


def test_distribution_runtime_copy_loads_without_checkout_or_working_directory(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    dist = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--out-dir", str(dist)],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(dist.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        for locale in ("en-US", "zh-TW"):
            assert f"cafe/data/locales/{locale}.yaml" in archive.namelist()
    with tarfile.open(next(dist.glob("*.tar.gz"))) as archive:
        for locale in ("en-US", "zh-TW"):
            assert any(
                name.endswith(f"/src/cafe/data/locales/{locale}.yaml")
                for name in archive.getnames()
            )
    installed = tmp_path / "installed"
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "--target",
            str(installed),
            "--no-deps",
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    script = """
import sys
sys.path.insert(0, sys.argv[1])
import cafe
from cafe.core.runtime_locales import load_catalogs
from cafe.core.workspace_artifact import workspace_correction_prompt
assert cafe.__file__.startswith(sys.argv[1])
assert set(load_catalogs()) == {"en-US", "zh-TW"}
for locale in ("en-US", "zh-TW"):
    prompt = workspace_correction_prompt(
        "owned/{reason}.txt", consumed=1, remaining=2, locale=locale
    )
    assert "owned/{reason}.txt" in prompt
    assert "1" in prompt and "2" in prompt
"""
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    subprocess.run(
        [sys.executable, "-I", "-c", script, str(installed)],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
