"""secret-get: Deliver a credential via temp file (NEVER on stdout).

Usage:
  secret-get <KEY_NAME> [--export-env] [--shell=auto|ps|posix]
  secret-get <KEY_NAME> [--json | --print-path]

Default:
  Writes value to ``<tempdir>/secret-paste-tmp/<name>.val`` (5-minute TTL).
  Stdout shows ONLY: ``OK: <name> available at <path> (5min TTL)``.

--export-env:
  Emits a shell snippet that, when dot-sourced / eval'd, sets ``$env:<NAME>``
  (PowerShell) or ``<NAME>`` (POSIX) from the temp file. The value is NEVER
  echoed to the terminal — it is read from the temp file inside the snippet.

--print-path:
  Print ONLY the absolute temp-file path and nothing else. Lets a caller do
  ``read_text(secret-get KEY --print-path)`` without parsing the human-readable
  ``OK:`` line with a regex.

--json:
  Print a machine-readable JSON object ``{"name", "path", "ttl_remaining"}`` to
  stdout, where ``ttl_remaining`` is the remaining lifetime of the temp file in
  seconds. The credential value itself is NEVER part of the JSON — only the path
  to the temp file is.

All output modes write the value to the same 5-minute-TTL temp file; they only
differ in what they print to stdout. The value is never on stdout.

Exit codes:
  0 = OK
  1 = Argument error
  2 = MISSING (not found locally)
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys

import secret_paste_core as cc


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="secret-get",
        description="Deliver credential via temp file (5min TTL).",
    )
    p.add_argument("name", help="Key name")
    p.add_argument(
        "--export-env",
        action="store_true",
        help=(
            "Emit a shell snippet to stdout that sets the env var from the "
            "temp file. Value is not in the output."
        ),
    )
    p.add_argument(
        "--shell",
        choices=("auto", "ps", "posix"),
        default="auto",
        help=(
            "Shell flavor for --export-env. 'auto' (default) picks PowerShell "
            "on Windows and POSIX otherwise."
        ),
    )
    p.add_argument(
        "--no-vault",
        action="store_true",
        help=(
            "(Reserved) Skip remote-backend fallback. No remote backend is "
            "configured in this release."
        ),
    )
    out = p.add_mutually_exclusive_group()
    out.add_argument(
        "--print-path",
        action="store_true",
        help=(
            "Print ONLY the absolute temp-file path (no human-readable text). "
            "Useful for piping straight into a file reader."
        ),
    )
    out.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help=(
            "Print a JSON object {name, path, ttl_remaining} to stdout. "
            "The value itself is never part of the JSON."
        ),
    )
    return p.parse_args(argv)


def _ps_quote(s: str) -> str:
    """Wrap ``s`` as a PowerShell single-quoted string (escapes embedded ')."""
    return "'" + s.replace("'", "''") + "'"


def _emit_export(env_name: str, tmp_path, shell: str) -> str:
    """Emit a shell snippet that loads the value from ``tmp_path`` into an env var.

    The value is never on the command line — the snippet reads it from the
    file at eval time. ``tmp_path`` is fully quoted so directories with
    spaces, single-quotes, or other special chars do not break the snippet.

    Note for callers: if shell tracing is active (``set -x`` / ``Set-PSDebug
    -Trace``), the value may be echoed by the shell itself. Run the snippet
    in a non-traced shell.
    """
    if shell == "auto":
        shell = "ps" if sys.platform == "win32" else "posix"

    path_str = str(tmp_path)
    if shell == "ps":
        path_q = _ps_quote(path_str)
        return (
            f"# File TTL: {cc.TMP_TTL_MINUTES} min. "
            "Run in a non-traced shell to avoid leaks.\n"
            f"$env:{env_name} = (Get-Content -Raw -Encoding UTF8 "
            f'{path_q}).TrimEnd("`r`n")\n'
            f"Write-Host 'OK: $env:{env_name} set from {path_q}'"
        )
    path_q = shlex.quote(path_str)
    # Read the WHOLE file, not just the first line: ``IFS= read -r`` stops at
    # the first newline and would truncate a multiline value (PEM key, JSON
    # service-account, multi-line env block) to its first line. ``$(cat …)``
    # reads the entire file; command substitution strips trailing newlines,
    # which matches the single-trailing-\n behavior on the paste side. The
    # value is still read from the temp file at eval time — never on the
    # command line.
    return (
        f"# File TTL: {cc.TMP_TTL_MINUTES} min. "
        "Run in a non-traced shell to avoid leaks.\n"
        f"{env_name}=\"$(cat {path_q})\"\n"
        f"export {env_name}\n"
        f'echo "OK: \\${env_name} set from {path_q}"'
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        safe = cc._safe_name(args.name)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.export_env and (args.print_path or args.as_json):
        print(
            "ERROR: --export-env cannot be combined with --print-path or --json.",
            file=sys.stderr,
        )
        return 1

    try:
        cc.default_backend()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    value, _meta = cc.read_credential(args.name)

    if value is None:
        print(
            f"MISSING: {args.name} not found locally. "
            f"Run 'secret-paste {args.name}' to add it.",
            file=sys.stderr,
        )
        return 2

    tmp_path = cc.write_tmp_value(args.name, value)
    backend = cc.backend_label()

    if args.print_path:
        # Only the path — no decoration, so callers can read the file directly.
        print(str(tmp_path))
    elif args.as_json:
        # ttl_remaining is the remaining lifetime of the temp file in seconds.
        # The value itself is intentionally NOT part of the JSON.
        payload = {
            "name": safe,
            "path": str(tmp_path),
            "ttl_remaining": cc.tmp_ttl_remaining(args.name),
        }
        print(json.dumps(payload))
    elif args.export_env:
        env_name = safe.upper().replace("-", "_").replace(".", "_")
        print(_emit_export(env_name, tmp_path, args.shell))
    else:
        print(
            f"OK: {args.name} available at {tmp_path} "
            f"({cc.TMP_TTL_MINUTES}min TTL, source={backend})"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
