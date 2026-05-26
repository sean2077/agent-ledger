"""Secret / risk scanning over a candidate file set.

Built-in regex covers common cloud/SaaS tokens; high-entropy heuristic
catches long base64/hex blobs. Optional gitleaks integration: if the
binary is on PATH, callers can opt into a stricter pass.

Each finding is a Finding(path, line, kind, snippet). No raw secret
values are echoed beyond the matched line excerpt; callers that emit
findings to the ledger should ensure they keep the file mode 0600.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Finding:
    path: str
    line: int
    kind: str
    snippet: str


# kind name -> compiled regex
PATTERNS: dict[str, re.Pattern[str]] = {
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "aws_secret":     re.compile(r"aws_secret_access_key\s*=\s*[A-Za-z0-9/+=]{40}", re.IGNORECASE),
    "gh_token":       re.compile(r"gh[pousr]_[A-Za-z0-9]{36,255}"),
    "openai_key":     re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "anthropic_key":  re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "slack_token":    re.compile(r"xox[abprs]-[A-Za-z0-9\-]{10,}"),
    "stripe_live":    re.compile(r"sk_live_[A-Za-z0-9]{20,}"),
    "google_api":     re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "private_key":    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "jwt":            re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
}

# heuristic: long secret-shaped string, only triggers if the surrounding
# line looks like an assignment to a key/secret-ish name
ASSIGN_RE = re.compile(
    r"(?i)(?:password|secret|token|api[_\-]?key|bearer|credential)\s*[:=]\s*[\"']?([A-Za-z0-9/+_\-]{20,})"
)


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    from collections import Counter
    counts = Counter(s)
    total = len(s)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def scan_text(path: str, text: str) -> list[Finding]:
    out: list[Finding] = []
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        for kind, pat in PATTERNS.items():
            m = pat.search(line)
            if m:
                snippet = _excerpt(line, m.start(), m.end())
                out.append(Finding(path, i, kind, snippet))
        m = ASSIGN_RE.search(line)
        if m and _entropy(m.group(1)) >= 3.5:
            snippet = _excerpt(line, m.start(1), m.end(1))
            out.append(Finding(path, i, "high_entropy_assign", snippet))
    return out


def _excerpt(line: str, start: int, end: int, *, span: int = 16) -> str:
    a = max(0, start - span)
    b = min(len(line), end + span)
    prefix = "..." if a > 0 else ""
    suffix = "..." if b < len(line) else ""
    # mask the actual hit
    return f"{prefix}{line[a:start]}<REDACTED>{line[end:b]}{suffix}"


def scan_files(files: list[Path], *, max_bytes: int = 2 * 1024 * 1024) -> list[Finding]:
    out: list[Finding] = []
    for p in files:
        try:
            if p.stat().st_size > max_bytes:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        out.extend(scan_text(str(p), text))
    return out


def has_gitleaks() -> bool:
    return shutil.which("gitleaks") is not None


def gitleaks_scan(directory: Path) -> list[Finding]:
    """Run gitleaks if available; returns synthetic Findings.

    No fail if gitleaks is absent; caller should check has_gitleaks() first.
    """
    if not has_gitleaks():
        return []
    res = subprocess.run(
        ["gitleaks", "detect", "--no-banner", "--no-git", "--source", str(directory), "--report-format=json", "--report-path=/dev/stdout"],
        capture_output=True, text=True, check=False,
    )
    if res.returncode == 0 and not res.stdout.strip():
        return []
    import json
    out: list[Finding] = []
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        return out
    for item in data:
        out.append(Finding(
            path=item.get("File", "?"),
            line=int(item.get("StartLine", 0)),
            kind=f"gitleaks:{item.get('RuleID', 'unknown')}",
            snippet=item.get("Match", "")[:64],
        ))
    return out


def format_findings(findings: list[Finding]) -> str:
    if not findings:
        return "no findings\n"
    lines = [f"{len(findings)} finding(s):", ""]
    for f in findings:
        lines.append(f"  {f.path}:{f.line}  [{f.kind}]  {f.snippet}")
    return "\n".join(lines) + "\n"
