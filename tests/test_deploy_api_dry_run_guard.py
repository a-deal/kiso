"""The DRY_RUN=1 guard in deploy-api.sh must short-circuit before any side effect.

Source: 2026-04-12 accidental-deploy incident. A prior test invoked
deploy-api.sh against the real Mac Mini because the script had no
DRY_RUN guard and treated the env var as noise. This test pins the
contract that DRY_RUN=1 is an early exit with no git/ssh/curl calls,
so future tests (the full 10-step dry-run harness in Task B) can run
safely against the real script.

This is HARD RULE #9 in ~/.claude/CLAUDE.md: side-effecting scripts
must be no-op by default. The guard is commit #1 before any tests
that exercise deploy logic.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "deploy-api.sh"


@pytest.fixture
def sandboxed_env(tmp_path):
    """PATH points at shims that explode if ssh/git/curl are invoked.

    If the DRY_RUN guard works, none of these shims should ever run.
    If the guard is missing or broken, the shim exits non-zero with
    a loud message, which fails the test — safely, without touching
    the real remote.
    """
    shim_dir = tmp_path / "shims"
    shim_dir.mkdir()
    for cmd in ("ssh", "git", "curl"):
        shim = shim_dir / cmd
        shim.write_text(
            f'#!/usr/bin/env bash\n'
            f'echo "FATAL: {cmd} invoked while DRY_RUN=1 — guard failed" >&2\n'
            f'exit 87\n'
        )
        shim.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{shim_dir}:{env['PATH']}"
    env["DRY_RUN"] = "1"
    env["DEPLOY_DRY_RUN_LOG"] = str(tmp_path / "deploy-dry-run.log")
    return env, tmp_path


def test_deploy_script_with_dry_run_does_not_invoke_ssh_git_or_curl(sandboxed_env):
    env, tmp_path = sandboxed_env
    result = subprocess.run(
        ["bash", str(SCRIPT), "--skip-tests"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    # If the guard works, exit is 0 and the shims never ran (no exit 87).
    assert result.returncode == 0, (
        f"deploy-api.sh with DRY_RUN=1 must exit 0 without side effects.\n"
        f"returncode={result.returncode}\n"
        f"stdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )
    # The shims write "FATAL:" to stderr if they're ever invoked.
    assert "FATAL:" not in result.stderr, (
        f"a sandboxed shim was invoked during DRY_RUN — guard is not early "
        f"enough. stderr={result.stderr}"
    )


def test_deploy_script_with_dry_run_writes_marker_to_log(sandboxed_env):
    """The guard should leave a trace so test harnesses can tell it fired."""
    env, tmp_path = sandboxed_env
    log_path = Path(env["DEPLOY_DRY_RUN_LOG"])
    subprocess.run(
        ["bash", str(SCRIPT), "--skip-tests"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert log_path.exists(), (
        f"DRY_RUN guard must write a marker to $DEPLOY_DRY_RUN_LOG "
        f"({log_path}) so harnesses can assert the guard fired."
    )
    contents = log_path.read_text()
    assert "DRY_RUN" in contents, (
        f"dry-run log should mention DRY_RUN, got: {contents!r}"
    )
