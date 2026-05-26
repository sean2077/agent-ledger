import json
import os
import stat
import tempfile
from pathlib import Path

import pytest

from awb import cli, doctor


def _mk_root(td: str) -> Path:
    root = Path(td) / "ledger"
    root.mkdir(mode=0o700)
    return root


def test_all_fs_probes_pass_on_tmpfs():
    with tempfile.TemporaryDirectory() as td:
        root = _mk_root(td)
        results = [doctor._run(name, fn, root) for name, fn in doctor._FS_PROBES]
        statuses = {r.name: (r.status, r.detail) for r in results}
        assert all(r.status == "pass" for r in results), statuses


def test_posix_mode_pass_at_target():
    with tempfile.TemporaryDirectory() as td:
        root = _mk_root(td)
        status, _ = doctor._posix_mode(root, target_mode=0o700)
        assert status == "pass"


def test_posix_mode_warn_when_group_accessible():
    with tempfile.TemporaryDirectory() as td:
        root = _mk_root(td)
        os.chmod(root, 0o750)
        status, detail = doctor._posix_mode(root, target_mode=0o700)
        assert status == "warn", (status, detail)


def test_posix_mode_fail_when_world_writable():
    with tempfile.TemporaryDirectory() as td:
        root = _mk_root(td)
        os.chmod(root, 0o777)
        status, _ = doctor._posix_mode(root, target_mode=0o700)
        assert status == "fail"


def test_run_returns_zero_on_clean_tmpfs():
    with tempfile.TemporaryDirectory() as td:
        root = _mk_root(td)
        rc = doctor.run(root)
        assert rc == 0


def test_run_returns_two_when_path_missing(capsys):
    rc = doctor.run(Path("/nonexistent/path/does/not/exist"))
    assert rc == 2
    assert "does not exist" in capsys.readouterr().err


def test_run_returns_two_when_not_a_dir(capsys):
    with tempfile.NamedTemporaryFile() as f:
        rc = doctor.run(Path(f.name))
        assert rc == 2
        assert "not a directory" in capsys.readouterr().err


def test_json_output_parses(capsys):
    with tempfile.TemporaryDirectory() as td:
        root = _mk_root(td)
        rc = doctor.run(root, as_json=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["exit_code"] == rc
        assert {r["name"] for r in data["results"]} == {
            "tmp_rename",
            "mtime_monotonic",
            "symlink_ops",
            "sha256_stable",
            "fsync_barrier",
            "posix_mode",
        }


def test_cli_doctor_invokes_run(tmp_path, capsys):
    (tmp_path / "ledger").mkdir(mode=0o700)
    rc = cli.main(["doctor", str(tmp_path / "ledger"), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["posix_target"] == "0o700"


def test_cli_rejects_non_octal_posix_target(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["doctor", str(tmp_path), "--posix-target", "rwx"])
