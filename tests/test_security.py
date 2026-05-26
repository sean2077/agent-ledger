from awb import security


def test_aws_access_key_detected():
    f = security.scan_text("x.txt", "AWS_ACCESS_KEY_ID = AKIAIOSFODNN7EXAMPLE")
    assert any(it.kind == "aws_access_key" for it in f)


def test_github_token_detected():
    f = security.scan_text("x.txt", "token = ghp_" + "A" * 36)
    assert any(it.kind == "gh_token" for it in f)


def test_openai_key_detected():
    f = security.scan_text("x.txt", 'OPENAI_KEY="sk-' + "A" * 40 + '"')
    assert any(it.kind == "openai_key" for it in f)


def test_private_key_block_detected():
    f = security.scan_text("x.txt", "-----BEGIN OPENSSH PRIVATE KEY-----")
    assert any(it.kind == "private_key" for it in f)


def test_jwt_detected():
    jwt = "eyJ" + "A" * 20 + "." + "B" * 20 + "." + "C" * 20
    f = security.scan_text("x.txt", f"Bearer {jwt}")
    assert any(it.kind == "jwt" for it in f)


def test_high_entropy_assign_detected():
    val = "ka9SLfj32Lk8sdF03dGHj48dks02kS8d"  # high entropy
    f = security.scan_text("x.txt", f"password = {val}")
    assert any(it.kind == "high_entropy_assign" for it in f)


def test_low_entropy_assign_ignored():
    # value too uniform should not trigger high-entropy
    f = security.scan_text("x.txt", "password = aaaaaaaaaaaaaaaaaaaaaa")
    assert not any(it.kind == "high_entropy_assign" for it in f)


def test_snippet_is_redacted():
    f = security.scan_text("x.txt", "AWS_ACCESS_KEY_ID = AKIAIOSFODNN7EXAMPLE")
    assert "<REDACTED>" in f[0].snippet
    assert "AKIAIOSFODNN7EXAMPLE" not in f[0].snippet


def test_scan_files_handles_binary(tmp_path):
    p = tmp_path / "blob.bin"
    p.write_bytes(b"\x00\x01\x02\xffsome random bytes")
    # should not raise; either zero findings or skipped silently
    f = security.scan_files([p])
    assert isinstance(f, list)


def test_format_findings_no_findings():
    assert "no findings" in security.format_findings([])
