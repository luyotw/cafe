"""A failed release stage stays failed and leaves its own timing evidence."""

import json
import os
import shutil
import subprocess
from pathlib import Path


def test_failed_coverage_stage_preserves_exit_code_and_completed_run_report(tmp_path):
    source = Path(__file__).resolve().parents[2] / "scripts"
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("release-check.sh", "release-report.py"):
        shutil.copyfile(source / name, scripts / name)
    coverage = scripts / "test-coverage.sh"
    coverage.write_text("#!/bin/bash\nexit 7\n")
    coverage.chmod(0o755)
    environment = dict(os.environ)
    environment["CAFE_TEST_REPORT_DIR"] = str(tmp_path / "reports")
    result = subprocess.run(
        ["bash", str(scripts / "release-check.sh")],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
    )
    assert result.returncode == 7
    latest = json.loads((tmp_path / "reports/release-check-latest.json").read_text())
    assert latest["exit_code"] == 7 and latest["elapsed_seconds"] > 0
    assert len(latest["stages"]) == 1
    assert latest["stages"][0]["name"] == "coverage"
    assert latest["stages"][0]["exit_code"] == 7
    report = Path(latest["report_dir"]) / "timings.json"
    assert json.loads(report.read_text()) == latest

    # A retry retains its predecessor instead of overwriting the failed evidence.
    retry = subprocess.run(
        ["bash", str(scripts / "release-check.sh")],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
    )
    assert retry.returncode == 7
    retried = json.loads((tmp_path / "reports/release-check-latest.json").read_text())
    assert retried["report_dir"] != latest["report_dir"]
    assert json.loads(report.read_text()) == latest
