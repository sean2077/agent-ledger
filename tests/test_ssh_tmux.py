import pytest

from awb import ssh_tmux
from awb.ssh_tmux import ProcResult, SSHConfig, TmuxConfig


def _fake(returncode=0, stdout="", stderr=""):
    def runner(argv):
        runner.called.append(list(argv))
        return ProcResult(returncode, stdout, stderr)
    runner.called = []
    return runner


def test_sshconfig_builds_safe_argv():
    cfg = SSHConfig(host="public-box")
    argv = cfg.argv(["test", "-e", "/path with space"])
    assert argv[0] == "ssh"
    assert "public-box" in argv
    assert argv[-3:] == ["test", "-e", "/path with space"]


def test_remote_exists_uses_test_minus_e():
    r = _fake(returncode=0)
    cfg = SSHConfig(host="h")
    assert ssh_tmux.remote_exists(cfg, "/x", runner=r) is True
    assert r.called[0][-2:] == ["test", "-e"][1:] + ["/x"]  # ["-e", "/x"]


def test_remote_exists_false_on_nonzero():
    r = _fake(returncode=1)
    assert ssh_tmux.remote_exists(SSHConfig("h"), "/x", runner=r) is False


def test_remote_sha256_parses():
    r = _fake(returncode=0, stdout="abc123  /x\n")
    assert ssh_tmux.remote_sha256(SSHConfig("h"), "/x", runner=r) == "abc123"


def test_remote_sha256_none_on_fail():
    r = _fake(returncode=1)
    assert ssh_tmux.remote_sha256(SSHConfig("h"), "/x", runner=r) is None


def test_local_sha256(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"hello")
    import hashlib
    assert ssh_tmux.local_sha256(str(p)) == hashlib.sha256(b"hello").hexdigest()


def test_tmux_has_session_argv():
    r = _fake(returncode=0)
    tmux = TmuxConfig(ssh=SSHConfig("h"), session_name="awb-c")
    assert ssh_tmux.tmux_has_session(tmux, runner=r) is True
    flat = " ".join(r.called[0])
    assert "tmux" in flat and "has-session" in flat and "awb-c" in flat


def test_tmux_send_keys_two_calls_with_enter():
    r = _fake(returncode=0)
    tmux = TmuxConfig(ssh=SSHConfig("h"), session_name="s")
    ssh_tmux.tmux_send_keys(tmux, "hello world", runner=r)
    assert len(r.called) == 2
    assert "hello world" in r.called[0]
    assert "Enter" in r.called[1]


def test_tmux_send_keys_rejects_newline():
    tmux = TmuxConfig(ssh=SSHConfig("h"), session_name="s")
    with pytest.raises(ValueError):
        ssh_tmux.tmux_send_keys(tmux, "a\nb", runner=_fake())


def test_tmux_socket_propagated():
    r = _fake()
    tmux = TmuxConfig(ssh=SSHConfig("h"), session_name="s", socket_path="/tmp/x.sock")
    ssh_tmux.tmux_has_session(tmux, runner=r)
    # find the -S /tmp/x.sock in argv
    argv = r.called[0]
    i = argv.index("tmux")
    assert argv[i + 1 : i + 3] == ["-S", "/tmp/x.sock"]
