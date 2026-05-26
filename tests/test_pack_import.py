import subprocess
import tarfile
from pathlib import Path

import pytest

from awb import importer, ledger, pack, wait


def _git_init(path: Path, files: dict[str, str]) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    for rel, content in files.items():
        p = path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True
    )


def _make_session(tmp_path: Path) -> ledger.Session:
    return ledger.create_session(
        ledger_root=tmp_path / "ledger", project="p", slug="x", title="t",
        target_agents=["claude", "gpt55"],
        remote=ledger.Remote(
            ssh_host="h", tmux_session="s", remote_root="/r/ledger",
        ),
    )


def test_pack_basic(tmp_path):
    repo = tmp_path / "repo"
    _git_init(repo, {"src/a.py": "print('hi')\n", "README.md": "yo\n"})
    s = _make_session(tmp_path)
    res = pack.pack(s, 1, repo, pack.PackPolicy())
    assert res.file_count == 2
    assert res.bundle_path.exists()
    assert (res.bundle_path.parent / "bundle.ready").exists()
    assert (res.bundle_path.parent / "bundle.tar.gz.sha256").exists()
    assert res.manifest_path.exists()
    # prompts generated for each non-terminal target
    rdir = s.path / "r1"
    assert (rdir / "prompts" / "claude.md").exists()
    assert (rdir / "prompts" / "gpt55.md").exists()


def test_pack_excludes_denylisted(tmp_path):
    repo = tmp_path / "repo"
    _git_init(repo, {".env": "SECRET=x", "src/a.py": "print(1)\n"})
    s = _make_session(tmp_path)
    res = pack.pack(s, 1, repo, pack.PackPolicy())
    paths = [p for p, _ in res.excluded]
    assert ".env" in paths
    files_in_bundle: list[str] = []
    with tarfile.open(res.bundle_path) as tf:
        files_in_bundle = tf.getnames()
    assert ".env" not in files_in_bundle


def test_pack_blocks_on_secret_without_allow_risk(tmp_path):
    repo = tmp_path / "repo"
    _git_init(repo, {"src/config.py": "AWS = 'AKIAIOSFODNN7EXAMPLE'\n"})
    s = _make_session(tmp_path)
    with pytest.raises(pack.PackError, match="secret scan hit"):
        pack.pack(s, 1, repo, pack.PackPolicy())


def test_pack_allow_risk_proceeds(tmp_path):
    repo = tmp_path / "repo"
    _git_init(repo, {"src/config.py": "AWS = 'AKIAIOSFODNN7EXAMPLE'\n"})
    s = _make_session(tmp_path)
    res = pack.pack(s, 1, repo, pack.PackPolicy(allow_risk=True))
    assert res.findings
    # secret-scan.txt written
    assert (res.bundle_path.parent / "secret-scan.txt").exists()


def test_pack_respects_workbenchignore(tmp_path):
    repo = tmp_path / "repo"
    _git_init(repo, {
        "src/a.py": "x=1\n", "src/b.py": "y=1\n",
        ".workbenchignore": "src/b.py\n",
    })
    s = _make_session(tmp_path)
    res = pack.pack(s, 1, repo, pack.PackPolicy())
    excluded_paths = [p for p, _ in res.excluded]
    assert "src/b.py" in excluded_paths


def test_pack_size_cap(tmp_path):
    repo = tmp_path / "repo"
    _git_init(repo, {"big.txt": "x" * 4096})
    s = _make_session(tmp_path)
    res = pack.pack(s, 1, repo, pack.PackPolicy(max_file_bytes=1024))
    assert any("size" in reason for _, reason in res.excluded)


def test_import_publishes_into_ledger(tmp_path):
    s = _make_session(tmp_path)
    src = tmp_path / "external.md"
    src.write_text("Verdict: needs-change\n\nbody\n")
    paths = importer.import_reply(s, 1, "gpt55", src)
    assert paths["ready"].exists()
    assert paths["md"].read_text().startswith("Verdict: needs-change")


def test_import_refuses_double_without_replace(tmp_path):
    s = _make_session(tmp_path)
    src = tmp_path / "external.md"
    src.write_text("Verdict: approve\n")
    importer.import_reply(s, 1, "gpt55", src)
    with pytest.raises(importer.ImportError_, match="already present"):
        importer.import_reply(s, 1, "gpt55", src)


def test_import_with_replace_archives(tmp_path):
    s = _make_session(tmp_path)
    src = tmp_path / "external.md"
    src.write_text("Verdict: approve\nv1\n")
    importer.import_reply(s, 1, "gpt55", src)
    src.write_text("Verdict: approve\nv2\n")
    importer.import_reply(s, 1, "gpt55", src, replace=True)
    arch = list((s.path / "archive").glob("r1-gpt55-superseded-*.md"))
    assert len(arch) == 1
    assert "v1" in arch[0].read_text()
