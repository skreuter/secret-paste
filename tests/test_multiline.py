"""Multiline credential support (SP-1).

Covers:

* Blob round-trip: a multi-line ``KEY=VALUE`` block stored under ONE name comes
  back byte-for-byte through ``secret-get`` (temp-file bytes == input).
* ``secret-get --export-env`` (POSIX) emits the WHOLE value, not just line 1 —
  the previous ``IFS= read -r`` snippet truncated at the first newline.
* Single-line behavior is unchanged (regression guard).
* The store-side normalization helper (``\\r\\n`` → ``\\n`` + strip one trailing
  ``\\n``) used by both GUI paths.

The GUI mainloop itself can't run headless here; the normalization logic that
the dialogs apply to the active widget is exercised directly via the helper.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import secret_get_cli as get_cli
import secret_paste_cli as paste_cli
import secret_paste_core as core

MULTILINE_BLOCK = "DB_HOST=localhost\nDB_PORT=5432\nDB_PASS=p@ss w0rd\n# comment line"


@pytest.fixture
def get_ready(isolated_dirs, fake_backend, monkeypatch):
    """Patch default_backend so secret-get doesn't hard-fail off-Windows."""
    monkeypatch.setattr(get_cli.cc, "default_backend", lambda: core.LocalDPAPIBackend())


# ---------------------------------------------------------------------------
# Normalization helper (the logic the GUI applies before storing).
# ---------------------------------------------------------------------------


def test_normalize_crlf_to_lf():
    assert paste_cli._normalize_value("a\r\nb\r\nc") == "a\nb\nc"


def test_normalize_strips_one_trailing_newline_only():
    assert paste_cli._normalize_value("PEM\n") == "PEM"
    # A blank trailing line (two \n) keeps one.
    assert paste_cli._normalize_value("PEM\n\n") == "PEM\n"


def test_normalize_preserves_interior_newlines():
    val = "line1\nline2\nline3"
    assert paste_cli._normalize_value(val) == val


def test_normalize_single_line_is_identity():
    assert paste_cli._normalize_value("sk-secret-123") == "sk-secret-123"


# ---------------------------------------------------------------------------
# Blob round-trip through the real store + secret-get temp file.
# ---------------------------------------------------------------------------


def test_multiline_blob_roundtrip_via_secret_get(get_ready, capsys):
    """Store a multi-line block under one name → secret-get → temp file bytes
    equal the stored input exactly (interior newlines preserved)."""
    stored = paste_cli._normalize_value(MULTILINE_BLOCK + "\n")  # GUI would strip one \n
    core.write_credential("ENV_BLOCK", stored, ttl_hours=2, persist_to_vault=False)

    rc = get_cli.main(["ENV_BLOCK", "--print-path"])
    out = capsys.readouterr().out.strip()
    assert rc == 0
    path = Path(out)
    # Temp file holds the whole multi-line value, byte-for-byte.
    assert path.read_text(encoding="utf-8") == stored
    assert path.read_bytes() == stored.encode("utf-8")
    # And the value never leaked to stdout.
    assert "p@ss w0rd" not in out


# ---------------------------------------------------------------------------
# POSIX --export-env must carry the WHOLE multiline value (bug fix).
# ---------------------------------------------------------------------------


def test_export_env_posix_reads_whole_file_not_first_line(get_ready, capsys):
    """The POSIX snippet must read the entire temp file, so a multiline value
    isn't truncated to its first line. We assert on the snippet shape AND on
    the actual eval'd result in a real /bin/sh."""
    stored = "DB_HOST=localhost\nDB_PORT=5432\nDB_PASS=secret"
    core.write_credential("ENV_BLOCK", stored, ttl_hours=2, persist_to_vault=False)

    rc = get_cli.main(["ENV_BLOCK", "--export-env", "--shell=posix"])
    snippet = capsys.readouterr().out
    assert rc == 0
    # The old truncating form must be gone; the whole-file read must be present.
    assert "IFS= read -r" not in snippet
    assert "$(cat " in snippet
    # The value itself is never inlined in the snippet (only the temp path is).
    assert "secret" not in snippet.replace("# ", "")  # comment-safe check
    assert stored not in snippet

    # Actually run the snippet and confirm the env var holds the whole value.
    import subprocess

    proc = subprocess.run(
        ["/bin/sh", "-c", snippet + "\nprintf '%s' \"$ENV_BLOCK\""],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    # printf of the var == the full multiline value (the eval'd snippet read it
    # from the temp file). Command substitution strips trailing newlines; our
    # stored value has none, so it round-trips exactly.
    assert proc.stdout.endswith(stored)


def test_export_env_posix_single_line_regression(get_ready, capsys):
    """A single-line value still exports correctly through the new snippet."""
    core.write_credential("BREVO_KEY", "sk-secret-123", ttl_hours=2, persist_to_vault=False)
    rc = get_cli.main(["BREVO_KEY", "--export-env", "--shell=posix"])
    snippet = capsys.readouterr().out
    assert rc == 0

    import subprocess

    proc = subprocess.run(
        ["/bin/sh", "-c", snippet + "\nprintf '%s' \"$BREVO_KEY\""],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert proc.stdout.endswith("sk-secret-123")


def test_export_env_ps_branch_still_whole_file(get_ready, capsys):
    """The PowerShell branch already read the whole file (Get-Content -Raw); make
    sure it's unchanged and references the temp path, not the value."""
    core.write_credential("ENV_BLOCK", "a\nb\nc", ttl_hours=2, persist_to_vault=False)
    rc = get_cli.main(["ENV_BLOCK", "--export-env", "--shell=ps"])
    snippet = capsys.readouterr().out
    assert rc == 0
    assert "Get-Content -Raw" in snippet
    assert "a\nb\nc" not in snippet
