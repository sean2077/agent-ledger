"""`awb pack` — bundle repo context + generate prompts for current round.

Produces under r<n>/context/:
  diff.patch
  manifest.json
  manifest.txt
  bundle.tar.gz
  bundle.tar.gz.sha256
  bundle.ready
  secret-scan.txt

And under r<n>/prompts/:
  <agent>.md  (one per required target without a reply yet)

File selection is `git ls-files`-only (no untracked unless --include-untracked),
filtered through builtin + .workbenchignore denylists. Secret scan runs over
the staged file set; bundle is refused on hit unless --allow-risk.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tarfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from awb import atomic, ledger, security

DEFAULT_BUNDLE_BYTES = 10 * 1024 * 1024  # 10 MB
DEFAULT_FILE_BYTES = 1 * 1024 * 1024  # 1 MB
DEFAULT_FILE_COUNT = 500

BUILTIN_DENYLIST = [
    ".env", ".env.*", "**/.env", "**/.env.*",
    "*.pem", "*.p12", "*.pfx", "*.key", "*.crt", "*.csr",
    "*.tfstate", "*.tfstate.*", "*.kubeconfig",
    ".npmrc", ".netrc", "id_rsa*", "id_dsa*", "id_ed25519*",
    "*.gpg", "*.asc", "credentials*", "authorized_keys",
    "*.sqlite", "*.sqlite3", "*.db", "*.dump", "*.bak",
    "node_modules/**", "vendor/**", ".git/**",
    "dist/**", "build/**", "target/**",
    ".venv/**", "venv/**", "__pycache__/**", ".pytest_cache/**",
]


@dataclass
class PackPolicy:
    max_bundle_bytes: int = DEFAULT_BUNDLE_BYTES
    max_file_bytes: int = DEFAULT_FILE_BYTES
    max_file_count: int = DEFAULT_FILE_COUNT
    include_untracked: bool = False
    allow_risk: bool = False
    extra_ignore: list[str] = field(default_factory=list)


@dataclass
class PackResult:
    bundle_path: Path
    manifest_path: Path
    diff_path: Path
    secret_scan_path: Path
    file_count: int
    total_bytes: int
    bundle_sha256: str
    excluded: list[tuple[str, str]]  # (path, reason)
    findings: list[security.Finding]


class PackError(Exception):
    pass


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=False,
    )
    if res.returncode != 0:
        raise PackError(f"git {' '.join(args)}: {res.stderr.strip()}")
    return res.stdout


def _resolve_repo(repo: Path) -> Path:
    return Path(_git(repo, "rev-parse", "--show-toplevel").strip())


def _tracked_files(repo: Path, include_untracked: bool) -> list[Path]:
    args = ["ls-files", "-z"]
    if include_untracked:
        args += ["--others", "--exclude-standard"]
    raw = _git(repo, *args)
    return [Path(p) for p in raw.split("\0") if p]


def _matches(path: Path, patterns: list[str]) -> str | None:
    s = str(path).replace("\\", "/")
    for pat in patterns:
        if Path(s).match(pat) or _glob_match(s, pat):
            return pat
    return None


def _glob_match(s: str, pat: str) -> bool:
    import fnmatch
    return fnmatch.fnmatch(s, pat)


def _read_workbenchignore(repo: Path) -> list[str]:
    wbi = repo / ".workbenchignore"
    if not wbi.exists():
        return []
    out: list[str] = []
    for ln in wbi.read_text().splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln)
    return out


def _bundle(files: list[tuple[Path, Path]], bundle_path: Path) -> int:
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for src, arcname in files:
            tf.add(str(src), arcname=str(arcname), recursive=False)
    data = buf.getvalue()
    atomic.atomic_write_bytes(bundle_path, data, mode=0o600)
    return len(data)


def pack(
    session: ledger.Session,
    round_n: int,
    repo: Path,
    policy: PackPolicy,
    *,
    prompt_template: str | None = None,
) -> PackResult:
    if session.path is None:
        raise PackError("session has no path")
    repo = _resolve_repo(Path(repo))
    rdir = session.path / f"r{round_n}"
    ctx = rdir / "context"
    prompts = rdir / "prompts"
    ctx.mkdir(parents=True, exist_ok=True)
    prompts.mkdir(parents=True, exist_ok=True)

    candidates = _tracked_files(repo, policy.include_untracked)
    ignore_patterns = BUILTIN_DENYLIST + _read_workbenchignore(repo) + policy.extra_ignore

    selected: list[tuple[Path, Path]] = []
    excluded: list[tuple[str, str]] = []
    total = 0
    for rel in candidates:
        src = repo / rel
        if src.is_symlink():
            link = src.resolve(strict=False)
            try:
                link.relative_to(repo.resolve())
            except ValueError:
                excluded.append((str(rel), "symlink escapes repo"))
                continue
        if not src.is_file():
            excluded.append((str(rel), "not a regular file"))
            continue
        hit = _matches(rel, ignore_patterns)
        if hit:
            excluded.append((str(rel), f"ignore: {hit}"))
            continue
        size = src.stat().st_size
        if size > policy.max_file_bytes:
            excluded.append((str(rel), f"size {size} > {policy.max_file_bytes}"))
            continue
        if total + size > policy.max_bundle_bytes:
            excluded.append((str(rel), "bundle size cap reached"))
            continue
        if len(selected) >= policy.max_file_count:
            excluded.append((str(rel), "file count cap reached"))
            continue
        selected.append((src, rel))
        total += size

    # secret scan
    findings = security.scan_files([src for src, _ in selected])
    secret_scan_path = ctx / "secret-scan.txt"
    atomic.atomic_write_text(secret_scan_path, security.format_findings(findings), mode=0o600)
    if findings and not policy.allow_risk:
        raise PackError(
            f"secret scan hit {len(findings)} item(s); re-run with --allow-risk after review. "
            f"See {secret_scan_path}"
        )

    # diff
    try:
        diff_text = _git(repo, "diff", "HEAD")
    except PackError:
        diff_text = ""
    diff_path = ctx / "diff.patch"
    atomic.atomic_write_text(diff_path, diff_text, mode=0o600)

    # manifest
    items = []
    for src, rel in selected:
        h = _sha256_of(src)
        items.append({
            "path": str(rel), "bytes": src.stat().st_size, "sha256": h,
        })
    manifest_path = ctx / "manifest.json"
    atomic.atomic_write_json(manifest_path, {
        "repo_root": str(repo),
        "file_count": len(items), "total_bytes": total,
        "files": items,
    }, mode=0o600)
    atomic.atomic_write_text(
        ctx / "manifest.txt",
        "\n".join(f"{it['sha256'][:12]}  {it['bytes']:>10d}  {it['path']}" for it in items) + "\n",
        mode=0o600,
    )

    # bundle
    bundle_path = ctx / "bundle.tar.gz"
    bundle_size = _bundle(selected, bundle_path)
    bundle_sha = _sha256_of(bundle_path)
    atomic.atomic_write_text(
        ctx / "bundle.tar.gz.sha256", f"{bundle_sha}  bundle.tar.gz\n", mode=0o644,
    )
    atomic.atomic_write_bytes(ctx / "bundle.ready", b"", mode=0o644)

    # prompts for non-terminal required targets
    rnd = session.round(round_n)
    template = prompt_template or DEFAULT_PROMPT_TEMPLATE
    for t in rnd.targets:
        if t.is_terminal():
            continue
        out = prompts / f"{t.agent}.md"
        if out.exists():
            continue
        text = template.format(
            agent=t.agent,
            project=session.project,
            session_id=session.session_id,
            round=round_n,
            remote_round_dir=_remote_round_dir(session, round_n),
            local_round_dir=str(rdir),
            title=session.title,
        )
        atomic.atomic_write_text(out, text, mode=0o600)

    return PackResult(
        bundle_path=bundle_path,
        manifest_path=manifest_path,
        diff_path=diff_path,
        secret_scan_path=secret_scan_path,
        file_count=len(selected),
        total_bytes=total,
        bundle_sha256=bundle_sha,
        excluded=excluded,
        findings=findings,
    )


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _remote_round_dir(session: ledger.Session, round_n: int) -> str:
    root = session.remote.remote_root or "<unset-remote-root>"
    return f"{root.rstrip('/')}/{session.project}/{session.session_id}/r{round_n}"


DEFAULT_PROMPT_TEMPLATE = """\
你是 {agent}。请评审本任务的 bundle 和 brief。

任务上下文：
- 项目：{project}
- 会话：{session_id}
- 轮次：r{round}
- 标题：{title}
- 远端任务目录：{remote_round_dir}

请读取：
- brief.md（会话根）
- r{round}/context/manifest.txt
- r{round}/context/diff.patch
- r{round}/context/bundle.tar.gz （需要更多上下文时解包到临时目录）

正式写入路径：{remote_round_dir}/replies/{agent}.md

写入协议（重要）：
1. 不要只在终端回答。
2. 使用 Write 工具写正文到 replies/{agent}.md.tmp，然后用 Bash mv 到 replies/{agent}.md，最后 touch replies/{agent}.submitted。
3. 不要写 .sha256 或 .ready —— awb 自动算/touch。
4. 如果无法完成，也必须写 replies/{agent}.md，Verdict 设为 blocked。

输出格式（第一行必须是 Verdict）：

Verdict: approve | needs-change | blocked

(正文：你的实质评审)
"""


# --- CLI -----------------------------------------------------------------


def cmd_pack(args) -> int:
    from awb.session import _resolve_session
    try:
        session = _resolve_session(args.ledger, args.project, args.session)
    except ledger.LedgerError as exc:
        print(f"awb pack: {exc}", file=sys.stderr)
        return 2

    round_n = args.round or session.current_round
    policy = PackPolicy(
        max_bundle_bytes=args.max_bundle_bytes,
        max_file_bytes=args.max_file_bytes,
        max_file_count=args.max_file_count,
        include_untracked=args.include_untracked,
        allow_risk=args.allow_risk,
    )
    try:
        res = pack(session, round_n, Path(args.repo), policy)
    except PackError as exc:
        print(f"awb pack: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(_dry_run_summary(res))
        # roll back what pack wrote? for MVP we keep artifacts in place.
        return 0

    ledger.append_event(
        session,
        {
            "actor": "awb", "event": "context.packed", "round": round_n,
            "file_count": res.file_count, "total_bytes": res.total_bytes,
            "bundle_sha256": res.bundle_sha256, "excluded": len(res.excluded),
        },
        command="awb pack",
    )
    print(f"packed r{round_n}:")
    print(f"  files:   {res.file_count}")
    print(f"  bytes:   {res.total_bytes}")
    print(f"  bundle:  {res.bundle_path}")
    print(f"  sha256:  {res.bundle_sha256}")
    print(f"  excluded: {len(res.excluded)}")
    return 0


def _dry_run_summary(res: PackResult) -> str:
    lines = [
        "pack DRY RUN summary:",
        f"  selected files:   {res.file_count}",
        f"  total bytes:      {res.total_bytes}",
        f"  bundle sha256:    {res.bundle_sha256}",
        f"  bundle path:      {res.bundle_path}",
        f"  excluded:         {len(res.excluded)}",
    ]
    if res.excluded:
        lines.append("  excluded sample (up to 20):")
        for path, reason in res.excluded[:20]:
            lines.append(f"    {path}  ({reason})")
    if res.findings:
        lines.append(f"  SECRET FINDINGS: {len(res.findings)}")
        for f in res.findings[:10]:
            lines.append(f"    {f.path}:{f.line}  {f.kind}: {f.snippet}")
    return "\n".join(lines)
