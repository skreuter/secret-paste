# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Multiline paste support** in both GUI paths (CustomTkinter + ttk fallback).
  The masked single-line field stays the default; a new **"Multiline"** toggle
  swaps in an (unmasked) text box for pasting a whole `KEY=VALUE` env block,
  PEM key, or JSON service-account as **one** secret. Pasting a value that
  contains a newline auto-switches to multiline. `\r\n` is normalized to `\n`
  and at most one trailing newline is stripped on save.

### Fixed

- `secret-get <NAME> --export-env` (POSIX shell) no longer truncates a
  multiline value to its first line: the snippet now reads the whole temp file
  (`NAME="$(cat …)"`) instead of `IFS= read -r NAME < …`. The value is still
  read from the temp file at eval time, never placed on the command line.

## [1.1.0] — 2026-05-29

### Changed

- Modern paste dialog: uses **CustomTkinter** when available (dark, brand-styled,
  native HighDPI) with a DPI- and dark-contrast-fixed stdlib `ttk` fallback.
  Subtle fade-in on open. Fixes blurry text on HighDPI displays and the
  low-contrast input field in dark mode.

### Added

- Optional `gui` extra — `pipx install "secret-paste[gui]"` for the modern UI.
  Zero *required* dependencies preserved (the stdlib fallback always works).

## [1.0.0] — 2026-05-29

### Added

- `secret-get --print-path` — prints only the absolute temp-file path (no
  human-readable `OK:` line), so callers can read the file without parsing
  stdout with a regex.
- `secret-get --json` — prints a machine-readable
  `{"name", "path", "ttl_remaining"}` object. `ttl_remaining` is the temp
  file's remaining lifetime in seconds. The credential value is never part of
  the JSON — only the path to the 5-minute-TTL temp file.
- `secret_paste_core.tmp_ttl_remaining(name)` helper backing `--json`.
- Optional, **write-only** remote mirror (off by default):
  - `secret-paste --enable-remote` / `--disable-remote` / `--show-config`
    manage the feature flag (persisted in `config.json`).
  - `secret-paste --set-remote sops-age --recipient age1...` configures the
    remote backend.
  - File-based sops/age backend skeleton. Write-only by design — `secret-get`
    can never read back from a remote mirror (enforced at a single choke point).
  - Vault detection (`age`/`sops`/`bw`/`op` on PATH); the dialog's mirror
    checkbox renders only when a vault is detected and the feature is enabled,
    and a remote-write failure never corrupts the local store.
- `SETUP.md` — AI-assisted install: hand the prompt to your AI coding agent to
  install and configure secret-paste (incl. the optional, write-only vault
  mirror) tailored to your machine.

### Changed

- Paste dialog polish (pure stdlib, no new dependencies): a "Paste" button next
  to the input field, an accent-styled primary button, a larger header, and
  **OS dark-mode detection** with a matching light/dark palette.
- Product UI and strings are consistently English.

## [0.1.0] — 2026-05-14

First public release.

### Added

- `secret-paste <KEY>` — local GUI dialog (tkinter, per-OS ttk theme) for
  ad-hoc credential entry. Value never leaves the host.
- `secret-get <KEY>` — drops the value into a 5-minute-TTL temp file and
  prints only the path. `--export-env` emits a PowerShell or POSIX shell
  snippet that sets the env var from the file without echoing it.
- `secret-list` — show stored credential names, source, created/expires,
  never values.
- `secret-revoke <KEY>` — delete a credential locally.
- Cross-platform value backend:
  - Windows: per-user DPAPI-encrypted blob (`pywin32`).
  - macOS: Keychain via `keyring`.
  - Linux: libsecret / kwallet via `keyring`.
- `VaultBackend` plugin interface in `secret_paste_core.py` for future
  remote backends (Bitwarden, 1Password, sops/age).
- `secret-paste-install-skill` entry point that copies a Claude-Code skill
  (`secret-paste.md`) into `~/.claude/skills/`. Once installed, any
  Claude-Code session on the machine auto-uses secret-paste when it needs
  a credential, instead of asking the user to paste into chat.
- `install.ps1` runs the skill install step automatically (skip with
  `-SkipClaudeSkill`).
- pytest suite (42 tests) covering name sanitisation, TTL math, temp-file
  cleanup, DPAPI roundtrip (mocked), and the `VaultBackend` contract.
- GitHub Actions matrix CI: Windows / macOS / Linux × Python 3.10–3.12.
- `pyproject.toml` with entry points — `pipx install secret-paste` works
  on all three OSes.

### Known limitations

- macOS keyring backend is CI-tested only; not yet human-verified on a
  real Mac. Looking for testers — please open an issue with feedback.
- No remote backend ships in v0.1. The plugin interface is in place; first
  remote backend (likely sops/age) targets v0.2.
- The "Mirror to remote backend" checkbox in the paste dialog is visible
  but disabled.

[1.1.0]: https://github.com/MoritzV42/secret-paste/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/MoritzV42/secret-paste/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/MoritzV42/secret-paste/releases/tag/v0.1.0
