"""secret-paste: GUI dialog for entering a credential.

Usage:
  secret-paste <KEY_NAME> [--ttl=24] [--persist] [--desc="Brevo API key"]
  secret-paste --enable-remote | --disable-remote | --show-config

Stores the value via the platform backend:

* Windows: DPAPI-encrypted blob under ``%LOCALAPPDATA%\\secret-paste\\``.
* macOS / Linux: via ``keyring`` (Keychain / libsecret / kwallet).

A "Mirror to remote backend" checkbox is shown only when the user has enabled
remote mirroring (``secret-paste --enable-remote``) AND a supported vault CLI
is detected on PATH. Remote backends plug in via the ``VaultBackend`` interface
(sops/age skeleton shipped; Bitwarden / 1Password planned) — see ROADMAP.md.
"""

from __future__ import annotations

import argparse
import sys

import secret_paste_core as cc


def _enable_dpi_awareness() -> None:
    """Make the process DPI-aware on Windows so tkinter renders crisply.

    Without this, tkinter windows are bitmap-scaled by Windows on HighDPI
    displays and look blurry. ``SetProcessDpiAwareness(1)`` opts into
    system-DPI awareness. Fully defensive: any failure (non-Windows, missing
    API on old Windows, already-set) is swallowed — DPI awareness is a
    nice-to-have, never a hard requirement.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        # PROCESS_SYSTEM_DPI_AWARE = 1. Available since Windows 8.1.
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        # Fall back to the older Vista+ API if shcore is unavailable.
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="secret-paste",
        description=(
            "Open a GUI dialog to paste a credential and store it locally "
            "via the platform's secure backend (Windows DPAPI / macOS "
            "Keychain / Linux Secret Service)."
        ),
    )
    p.add_argument(
        "name",
        nargs="?",
        help="Key name (e.g. BREVO_KEY). Optional when using a --*-remote / --show-config flag.",
    )
    remote = p.add_mutually_exclusive_group()
    remote.add_argument(
        "--enable-remote",
        action="store_true",
        help="Enable remote mirroring (sets remote_enabled=true in config) and exit.",
    )
    remote.add_argument(
        "--disable-remote",
        action="store_true",
        help="Disable remote mirroring (sets remote_enabled=false in config) and exit.",
    )
    p.add_argument(
        "--show-config",
        action="store_true",
        help="Print the current config (remote_enabled, remote_backend) and exit.",
    )
    p.add_argument(
        "--set-remote",
        metavar="TYPE",
        help=(
            "Configure the remote backend type (e.g. 'sops-age') and exit. "
            "Pass an empty string to clear it. Use --recipient for sops-age."
        ),
    )
    p.add_argument(
        "--recipient",
        help="age recipient (public key, age1...) used with --set-remote sops-age.",
    )
    p.add_argument(
        "--ttl",
        type=int,
        default=24,
        help="TTL in hours (default 24). Ignored with --persist.",
    )
    p.add_argument(
        "--persist",
        action="store_true",
        help="Store permanently (no TTL).",
    )
    p.add_argument(
        "--desc",
        "--description",
        dest="desc",
        default="",
        help="Optional description shown in the dialog.",
    )
    return p.parse_args(argv)


# Font stack: pick first available per-OS sans-serif.
_FONT_BY_OS = {
    "win32": "Segoe UI",
    "darwin": "SF Pro Text",  # falls back via tk
    "linux": "DejaVu Sans",
}


def _font(weight: str = "normal", size: int = 10) -> tuple:
    family = _FONT_BY_OS.get(sys.platform, "TkDefaultFont")
    return (family, size, weight)


def _normalize_value(text: str) -> str:
    """Normalize a pasted/typed credential for storage.

    * ``\\r\\n`` and bare ``\\r`` → ``\\n`` (LF-clean across platforms).
    * Strip at most ONE trailing newline (matches env-file habits where the
      file ends in a newline; PEM keys / JSON service-accounts keep their
      interior structure untouched).

    Interior newlines are preserved — that is the whole point of multiline
    support. This is the single place store-side normalization happens, so the
    ttk and CTk paths stay consistent.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.endswith("\n"):
        text = text[:-1]
    return text


def _has_newline(text: str) -> bool:
    """True if ``text`` contains a line break (after \\r\\n normalization)."""
    return "\n" in text.replace("\r\n", "\n").replace("\r", "\n")


def _prefers_dark_mode() -> bool:
    """Best-effort OS dark-mode detection. Pure stdlib, never raises.

    * Windows: ``HKCU\\...\\Themes\\Personalize\\AppsUseLightTheme`` (0 = dark).
    * macOS: ``defaults read -g AppleInterfaceStyle`` returns ``Dark`` only when
      dark mode is on (errors out otherwise).
    * Linux: ``gsettings`` color-scheme / GTK theme name heuristic.

    Returns ``False`` if detection is unavailable or ambiguous (light is the
    safe default — it matches the legacy look).
    """
    try:
        if sys.platform == "win32":
            import winreg  # type: ignore

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            try:
                val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            finally:
                winreg.CloseKey(key)
            return val == 0

        if sys.platform == "darwin":
            import subprocess

            out = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            return out.returncode == 0 and "dark" in out.stdout.strip().lower()

        # Linux / other POSIX: best-effort via gsettings.
        import subprocess

        for schema, key in (
            ("org.gnome.desktop.interface", "color-scheme"),
            ("org.gnome.desktop.interface", "gtk-theme"),
        ):
            try:
                out = subprocess.run(
                    ["gsettings", "get", schema, key],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
            except (FileNotFoundError, OSError):
                return False
            if out.returncode == 0 and "dark" in out.stdout.strip().lower():
                return True
        return False
    except Exception:  # noqa: BLE001
        return False


# Brand palette (matches the moritzvoigt landing page). Dark-first, cyan→violet
# accent. Used by the CustomTkinter dialog directly and by the ttk fallback for
# the dark palette below.
BRAND = {
    "bg": "#0a0c16",
    "surface": "#141a2e",
    "surface_alt": "#1a2138",
    "line": "#232b45",
    "text": "#eef2fb",
    "muted": "#aab4d0",
    "cyan": "#22d3ee",
    "cyan_light": "#6ee7ff",
    "violet": "#8b5cf6",
    "violet_light": "#a78bfa",
}

# Color palettes for the ttk fallback dialog. ttk themes don't expose a portable
# dark mode, so we drive label / accent colors ourselves and tint the window
# background.
_LIGHT_COLORS = {
    "bg": None,  # None = leave the native window background untouched
    "header": "#1a1a1a",
    "hint": "#666666",
    "muted": "#999999",
    "backend": "#3a7bd5",
    "accent_bg": "#3a7bd5",
    "accent_active": "#2f66b3",
    "accent_disabled": "#9bb8e0",
    # Entry colors: leave native (light field, dark text) for the light theme.
    "entry_bg": "#ffffff",
    "entry_fg": "#1a1a1a",
}
# Dark fallback uses the brand palette. Crucially ``entry_bg``/``entry_fg`` give
# the input field a dark background with light text — fixing the legacy bug
# where the field stayed white with hard-to-read light-grey text in dark mode.
_DARK_COLORS = {
    "bg": BRAND["bg"],
    "header": BRAND["text"],
    "hint": BRAND["muted"],
    "muted": "#7e89a8",
    "backend": BRAND["cyan_light"],
    "accent_bg": BRAND["violet"],
    "accent_active": BRAND["violet_light"],
    "accent_disabled": BRAND["line"],
    "entry_bg": BRAND["surface_alt"],
    "entry_fg": BRAND["text"],
}


def _apply_theme(root, dark: bool | None = None) -> dict:
    """Pick a per-OS ttk theme and apply a light/dark color palette.

    ``dark`` forces the palette; ``None`` auto-detects from the OS. Pure
    stdlib, no extra deps. Returns the active color dict so callers can tint
    non-ttk widgets (e.g. an overrideredirect toast) consistently.
    """
    from tkinter import ttk

    if dark is None:
        dark = _prefers_dark_mode()
    colors = _DARK_COLORS if dark else _LIGHT_COLORS

    style = ttk.Style(root)
    available = set(style.theme_names())
    preferred = []
    if sys.platform == "darwin":
        # 'aqua' renders its own dark mode natively; for our manual dark palette
        # 'clam' honors background overrides far better, so prefer it when dark.
        preferred = ["clam", "aqua"] if dark else ["aqua", "clam"]
    elif sys.platform == "win32":
        # The native Windows themes ignore background overrides, so for dark we
        # fall back to 'clam' which actually paints our colors.
        preferred = ["clam"] if dark else ["vista", "winnative", "xpnative"]
    else:
        preferred = ["clam"]
    for t in preferred:
        if t in available:
            try:
                style.theme_use(t)
                break
            except Exception:  # noqa: BLE001
                continue

    # Tint the window + ttk surfaces when a dark background is requested.
    if colors["bg"]:
        try:
            root.configure(bg=colors["bg"])
            style.configure(".", background=colors["bg"], foreground=colors["header"])
            style.configure("TFrame", background=colors["bg"])
            style.configure("TLabel", background=colors["bg"])
            style.configure("TCheckbutton", background=colors["bg"], foreground=colors["hint"])
            style.map(
                "TCheckbutton",
                background=[("active", colors["bg"])],
            )
            style.configure("TSeparator", background=colors["line"])
            # Dark-mode contrast fix for the input field: a dark field with
            # light text (the legacy look left the field white + light-grey
            # text, which was nearly unreadable). ``fieldbackground`` is the
            # option ttk actually honors for the entry's interior.
            style.configure(
                "TEntry",
                fieldbackground=colors["entry_bg"],
                foreground=colors["entry_fg"],
                insertcolor=colors["entry_fg"],
                bordercolor=colors["line"],
                lightcolor=colors["line"],
                darkcolor=colors["line"],
            )
            style.map(
                "TEntry",
                fieldbackground=[("focus", colors["entry_bg"])],
                foreground=[("focus", colors["entry_fg"])],
            )
        except Exception:  # noqa: BLE001
            pass

    style.configure("Header.TLabel", font=_font("bold", 15), foreground=colors["header"])
    style.configure("Hint.TLabel", foreground=colors["hint"], font=_font("normal", 9))
    style.configure("Muted.TLabel", foreground=colors["muted"], font=_font("normal", 8))
    style.configure("Backend.TLabel", foreground=colors["backend"], font=_font("normal", 9))
    # Accent button for the primary action. Pure ttk, no extra deps — not every
    # theme honors every option, so this is configured defensively.
    try:
        style.configure(
            "Accent.TButton",
            font=_font("bold", 10),
            foreground="#ffffff",
            background=colors["accent_bg"],
            padding=(14, 6),
        )
        style.map(
            "Accent.TButton",
            background=[
                ("active", colors["accent_active"]),
                ("disabled", colors["accent_disabled"]),
            ],
            foreground=[("disabled", "#eeeeee")],
        )
    except Exception:  # noqa: BLE001
        pass

    return colors


def _fade_in(window, *, duration_ms: int = 150, steps: int = 12) -> None:
    """Animate the window opacity from 0 → 1 over ``duration_ms``.

    Tasteful, subtle. Fully defensive — platforms / WMs that ignore the
    ``-alpha`` attribute simply show the window immediately. Works for both
    tkinter and CustomTkinter windows (both expose ``attributes``/``after``).
    """
    try:
        window.attributes("-alpha", 0.0)
    except Exception:  # noqa: BLE001
        return  # alpha unsupported — window is already fully visible
    delay = max(1, duration_ms // steps)

    def step(i: int) -> None:
        try:
            window.attributes("-alpha", min(1.0, i / steps))
        except Exception:  # noqa: BLE001
            return
        if i < steps:
            window.after(delay, step, i + 1)

    window.after(delay, step, 1)


def _safe_destroy(window) -> None:
    """Tear a Tk/CustomTkinter window down without the noisy
    ``invalid command name "..._check_dpi_scaling"`` Tcl errors.

    CustomTkinter schedules internal ``after`` callbacks (DPI scaling polling,
    button hover/click animations, geometry updates). Calling ``destroy()``
    directly while those are still queued makes them fire against an
    already-destroyed widget → Tcl raises ``invalid command name`` to stderr.
    We first cancel every pending ``after`` callback, then ``quit()`` the
    mainloop, then ``destroy()`` — each step fully defensive.
    """
    try:
        pending = window.tk.call("after", "info")
        ids = window.tk.splitlist(pending) if pending else ()
        for after_id in ids:
            try:
                window.after_cancel(after_id)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    try:
        window.quit()
    except Exception:  # noqa: BLE001
        pass
    try:
        window.destroy()
    except Exception:  # noqa: BLE001
        pass


def show_dialog(
    key_name: str,
    description: str,
    default_persist: bool,
    backend_label: str,
) -> tuple[bool, str, bool, bool]:
    """Modal dialog. Returns ``(ok, value, vault_checkbox, persist_checkbox)``.

    Prefers the modern CustomTkinter UI when the optional ``customtkinter``
    dependency is installed; otherwise falls back to the pure-stdlib ttk
    dialog (DPI-aware + dark-mode contrast fixed). Both honor the exact same
    return contract, so ``main()`` never needs to know which path ran.
    """
    _enable_dpi_awareness()
    try:
        import customtkinter  # noqa: F401
    except Exception:  # noqa: BLE001
        return _show_dialog_ttk(key_name, description, default_persist, backend_label)
    try:
        return _show_dialog_ctk(key_name, description, default_persist, backend_label)
    except Exception:  # noqa: BLE001
        # If the modern UI fails for any reason, never block the user — fall
        # back to the always-available stdlib dialog.
        return _show_dialog_ttk(key_name, description, default_persist, backend_label)


def _show_dialog_ttk(
    key_name: str,
    description: str,
    default_persist: bool,
    backend_label: str,
) -> tuple[bool, str, bool, bool]:
    """Pure-stdlib ttk fallback dialog (DPI-aware + dark-contrast fixed)."""
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(f"secret-paste: {key_name}")
    _apply_theme(root)
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()

    try:
        root.geometry("560x340")
        root.minsize(480, 280)
    except Exception:  # noqa: BLE001
        pass

    outer = ttk.Frame(root, padding=18)
    outer.pack(fill="both", expand=True)

    ttk.Label(outer, text=f"Enter credential: {key_name}", style="Header.TLabel").pack(anchor="w")

    ttk.Label(
        outer,
        text=description
        or "Paste the value (Ctrl+V on Win/Linux, ⌘V on macOS). " "Stored locally on this machine.",
        style="Hint.TLabel",
        wraplength=520,
    ).pack(anchor="w", pady=(4, 10))

    value_var = tk.StringVar()
    show_var = tk.BooleanVar(value=False)
    multiline_var = tk.BooleanVar(value=False)
    persist_var = tk.BooleanVar(value=default_persist)
    vault_var = tk.BooleanVar(value=False)

    colors = _DARK_COLORS if _prefers_dark_mode() else _LIGHT_COLORS

    # Input row: field + "Paste" button side by side. The button pulls the
    # value from the clipboard into the field — convenient, and the value still
    # never touches the chat. A masked single-line Entry is the default; a
    # "Multiline" toggle swaps in an unmasked tk.Text for whole KEY=VALUE
    # blocks (a Text widget can't honor ``show="*"`` so it is always unmasked).
    entry_row = ttk.Frame(outer)
    entry_row.pack(fill="x", pady=(4, 4))
    entry = ttk.Entry(entry_row, textvariable=value_var, show="*")
    entry.pack(side="left", fill="x", expand=True)
    entry.focus_set()

    # Multiline widget — created up front but only packed when active. Styled
    # manually with the active palette so it matches the themed Entry (the
    # ``TEntry`` ttk style does not reach a raw tk.Text).
    text_box = tk.Text(
        entry_row,
        height=6,
        wrap="word",
        undo=True,
        font=_font("normal", 10),
        bg=colors["entry_bg"],
        fg=colors["entry_fg"],
        insertbackground=colors["entry_fg"],
        relief="flat",
        borderwidth=1,
        highlightthickness=1,
        highlightbackground=colors["bg"] or "#888888",
        highlightcolor=colors["backend"],
    )

    def _active_value() -> str:
        if multiline_var.get():
            return text_box.get("1.0", "end-1c")
        return value_var.get()

    def _set_value(text: str) -> None:
        if multiline_var.get():
            text_box.delete("1.0", "end")
            text_box.insert("1.0", text)
        else:
            value_var.set(text)

    def switch_to_multiline(keep_text: str | None = None):
        """Show the Text widget, hide the Entry, carry the current value over."""
        if not multiline_var.get():
            multiline_var.set(True)
        carry = keep_text if keep_text is not None else value_var.get()
        entry.pack_forget()
        text_box.pack(side="left", fill="both", expand=True)
        text_box.delete("1.0", "end")
        text_box.insert("1.0", carry)
        toggle_show()  # disables "Show value" + sets hint
        text_box.focus_set()

    def switch_to_single():
        carry = text_box.get("1.0", "end-1c")
        multiline_var.set(False)
        text_box.pack_forget()
        entry.pack(side="left", fill="x", expand=True)
        # Drop interior newlines when collapsing back to single-line.
        value_var.set(carry.replace("\r\n", "\n").replace("\r", "").replace("\n", " ").strip())
        toggle_show()
        entry.focus_set()

    def toggle_multiline():
        if multiline_var.get():
            switch_to_single()
        else:
            switch_to_multiline()

    def paste_clipboard():
        try:
            clip = root.clipboard_get()
        except Exception:  # noqa: BLE001  — empty / non-text clipboard
            clip = ""
        if clip:
            clip = clip.replace("\r\n", "\n").replace("\r", "\n")
            # Auto-switch to multiline if the clipboard spans lines.
            if "\n" in clip and not multiline_var.get():
                switch_to_multiline(keep_text=clip)
            else:
                _set_value(clip)
                if not multiline_var.get():
                    entry.icursor("end")
        (text_box if multiline_var.get() else entry).focus_set()

    ttk.Button(entry_row, text="Paste", width=8, command=paste_clipboard).pack(
        side="left", padx=(8, 0)
    )

    err_lbl = ttk.Label(outer, text="", foreground="#d23", style="Hint.TLabel")
    err_lbl.pack(anchor="w")

    toggle_row = ttk.Frame(outer)
    toggle_row.pack(anchor="w", fill="x")

    show_chk = ttk.Checkbutton(
        toggle_row, text="Show value", variable=show_var, command=lambda: toggle_show()
    )
    show_chk.pack(side="left")
    ttk.Checkbutton(
        toggle_row, text="Multiline", variable=multiline_var, command=toggle_multiline
    ).pack(side="left", padx=(14, 0))

    multi_hint = ttk.Label(toggle_row, text="", style="Muted.TLabel")
    multi_hint.pack(side="left", padx=(10, 0))

    def toggle_show():
        if multiline_var.get():
            # A tk.Text can't mask its content, so "Show value" is meaningless
            # in multiline mode — disable it and show a hint.
            try:
                show_chk.state(["disabled"])
            except Exception:  # noqa: BLE001
                pass
            multi_hint.configure(text="(multiline shown unmasked)")
        else:
            try:
                show_chk.state(["!disabled"])
            except Exception:  # noqa: BLE001
                pass
            multi_hint.configure(text="")
            entry.configure(show="" if show_var.get() else "*")

    def clear_error(*_):
        if err_lbl.cget("text"):
            err_lbl.configure(text="")

    value_var.trace_add("write", clear_error)
    text_box.bind("<KeyRelease>", clear_error)

    # Mirror-to-remote is only offered when the user has opted in
    # (remote_enabled) AND at least one supported vault CLI is detected on
    # PATH. Otherwise the checkbox is not rendered at all (rather than shown
    # disabled), so the dialog stays clean for the common local-only case.
    cfg = cc.load_config()
    detected_vaults = cc.detect_vaults()
    show_mirror = bool(cfg.get("remote_enabled")) and bool(detected_vaults)

    if show_mirror:
        ttk.Separator(outer).pack(fill="x", pady=10)
        ttk.Checkbutton(
            outer,
            text="Also mirror to remote backend",
            variable=vault_var,
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text="Detected: " + ", ".join(detected_vaults) + ". See ROADMAP.md.",
            style="Muted.TLabel",
            wraplength=520,
        ).pack(anchor="w", padx=(22, 0))
    else:
        ttk.Separator(outer).pack(fill="x", pady=10)

    ttk.Checkbutton(
        outer,
        text="Store permanently (no local TTL)",
        variable=persist_var,
    ).pack(anchor="w", pady=(6, 4))

    ttk.Label(outer, text=f"Backend: {backend_label}", style="Backend.TLabel").pack(
        anchor="w", pady=(12, 0)
    )

    result: dict = {"ok": False}

    def on_ok(event=None):
        raw = _active_value()
        if not raw:
            err_lbl.configure(text="Please enter a value.")
            (text_box if multiline_var.get() else entry).focus_set()
            return
        result["ok"] = True
        result["value"] = _normalize_value(raw)
        result["vault"] = vault_var.get()
        result["persist"] = persist_var.get()
        _safe_destroy(root)

    def on_cancel(event=None):
        result["ok"] = False
        _safe_destroy(root)

    btn_frame = ttk.Frame(outer)
    btn_frame.pack(fill="x", pady=(14, 0))
    ttk.Button(btn_frame, text="Cancel", command=on_cancel).pack(side="right")
    ok_btn = ttk.Button(btn_frame, text="Save", command=on_ok)
    # Apply the accent style only if the theme supports it — fall back otherwise.
    try:
        ok_btn.configure(style="Accent.TButton")
    except Exception:  # noqa: BLE001
        pass
    ok_btn.pack(side="right", padx=(0, 8))

    def on_return(event=None):
        # In multiline mode Enter must insert a newline in the Text widget, not
        # submit. The Text's own class binding does the insert; we just don't
        # submit (and Ctrl+Enter / the Save button remain the submit paths).
        if multiline_var.get() and event is not None and event.widget is text_box:
            return None
        return on_ok(event)

    root.bind("<Return>", on_return)
    # Ctrl+Enter always submits, including from inside the multiline Text.
    root.bind("<Control-Return>", on_ok)
    root.bind("<Escape>", on_cancel)
    root.protocol("WM_DELETE_WINDOW", on_cancel)

    _fade_in(root)
    root.mainloop()

    if not result.get("ok"):
        return False, "", False, False
    return (
        True,
        result["value"],
        bool(result.get("vault")),
        bool(result.get("persist")),
    )


def _show_dialog_ctk(
    key_name: str,
    description: str,
    default_persist: bool,
    backend_label: str,
) -> tuple[bool, str, bool, bool]:
    """Modern CustomTkinter dialog. Same return contract as the ttk path.

    Uses the brand palette (dark-first, cyan→violet accent) and respects the
    OS light/dark preference via ``appearance_mode="system"``. CustomTkinter
    handles HighDPI scaling natively, so no blur on HighDPI displays.
    """
    import customtkinter as ctk

    ctk.set_appearance_mode("system")

    root = ctk.CTk()
    root.title(f"secret-paste: {key_name}")

    # Brand-tinted window background. When the OS is in light mode, CTk widgets
    # adapt their own colors; we still paint the frame surfaces consistently.
    root.configure(fg_color=(BRAND["surface"], BRAND["bg"]))
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    try:
        root.geometry("580x420")
        root.minsize(520, 360)
    except Exception:  # noqa: BLE001
        pass

    font_family = _FONT_BY_OS.get(sys.platform, "")
    f_header = ctk.CTkFont(family=font_family, size=20, weight="bold")
    f_body = ctk.CTkFont(family=font_family, size=13)
    f_small = ctk.CTkFont(family=font_family, size=12)
    f_btn = ctk.CTkFont(family=font_family, size=13, weight="bold")

    outer = ctk.CTkFrame(root, fg_color="transparent")
    outer.pack(fill="both", expand=True, padx=26, pady=22)

    ctk.CTkLabel(
        outer,
        text=f"Enter credential: {key_name}",
        font=f_header,
        text_color=BRAND["text"],
        anchor="w",
    ).pack(anchor="w", fill="x")

    ctk.CTkLabel(
        outer,
        text=(
            description
            or "Paste the value (Ctrl+V on Win/Linux, ⌘V on macOS). "
            "Stored locally on this machine."
        ),
        font=f_small,
        text_color=BRAND["muted"],
        anchor="w",
        justify="left",
        wraplength=520,
    ).pack(anchor="w", fill="x", pady=(4, 14))

    value_var = ctk.StringVar()
    show_var = ctk.BooleanVar(value=False)
    multiline_var = ctk.BooleanVar(value=False)
    persist_var = ctk.BooleanVar(value=default_persist)
    vault_var = ctk.BooleanVar(value=False)

    # Input row: masked field + "Show value" toggle would crowd the row, so the
    # toggle lives below. Field + "Paste" button sit side by side. A masked
    # single-line CTkEntry is the default; a "Multiline" toggle swaps in an
    # (always-unmasked) CTkTextbox for whole KEY=VALUE blocks — a Textbox can't
    # honor ``show="*"``.
    entry_row = ctk.CTkFrame(outer, fg_color="transparent")
    entry_row.pack(fill="x")
    entry = ctk.CTkEntry(
        entry_row,
        textvariable=value_var,
        show="*",
        font=f_body,
        height=40,
        fg_color=BRAND["surface_alt"],
        text_color=BRAND["text"],
        border_color=BRAND["line"],
        placeholder_text="",
    )
    entry.pack(side="left", fill="x", expand=True)
    entry.focus_set()

    # Multiline widget — built up front, only packed when active.
    text_box = ctk.CTkTextbox(
        entry_row,
        height=120,
        font=f_body,
        fg_color=BRAND["surface_alt"],
        text_color=BRAND["text"],
        border_color=BRAND["line"],
        border_width=1,
        wrap="word",
    )

    def _active_value() -> str:
        if multiline_var.get():
            return text_box.get("1.0", "end-1c")
        return value_var.get()

    def switch_to_multiline(keep_text: str | None = None):
        if not multiline_var.get():
            multiline_var.set(True)
        carry = keep_text if keep_text is not None else value_var.get()
        entry.pack_forget()
        text_box.pack(side="left", fill="both", expand=True)
        text_box.delete("1.0", "end")
        text_box.insert("1.0", carry)
        toggle_show()
        text_box.focus_set()

    def switch_to_single():
        carry = text_box.get("1.0", "end-1c")
        multiline_var.set(False)
        text_box.pack_forget()
        entry.pack(side="left", fill="x", expand=True)
        value_var.set(carry.replace("\r\n", "\n").replace("\r", "").replace("\n", " ").strip())
        toggle_show()
        entry.focus_set()

    def toggle_multiline():
        if multiline_var.get():
            switch_to_single()
        else:
            switch_to_multiline()

    def paste_clipboard():
        try:
            clip = root.clipboard_get()
        except Exception:  # noqa: BLE001  — empty / non-text clipboard
            clip = ""
        if clip:
            clip = clip.replace("\r\n", "\n").replace("\r", "\n")
            if "\n" in clip and not multiline_var.get():
                switch_to_multiline(keep_text=clip)
            elif multiline_var.get():
                text_box.delete("1.0", "end")
                text_box.insert("1.0", clip)
            else:
                value_var.set(clip)
                entry.icursor("end")
        (text_box if multiline_var.get() else entry).focus_set()

    ctk.CTkButton(
        entry_row,
        text="Paste",
        width=84,
        height=40,
        command=paste_clipboard,
        font=f_small,
        fg_color=BRAND["surface_alt"],
        hover_color=BRAND["line"],
        text_color=BRAND["text"],
        border_width=1,
        border_color=BRAND["line"],
    ).pack(side="left", padx=(10, 0))

    err_lbl = ctk.CTkLabel(outer, text="", font=f_small, text_color="#f87171", anchor="w")
    err_lbl.pack(anchor="w", fill="x", pady=(4, 0))

    toggle_row = ctk.CTkFrame(outer, fg_color="transparent")
    toggle_row.pack(anchor="w", fill="x", pady=(6, 0))

    show_chk = ctk.CTkCheckBox(
        toggle_row,
        text="Show value",
        variable=show_var,
        command=lambda: toggle_show(),
        font=f_small,
        text_color=BRAND["muted"],
        fg_color=BRAND["violet"],
        hover_color=BRAND["violet_light"],
        border_color=BRAND["line"],
    )
    show_chk.pack(side="left")

    ctk.CTkCheckBox(
        toggle_row,
        text="Multiline",
        variable=multiline_var,
        command=toggle_multiline,
        font=f_small,
        text_color=BRAND["muted"],
        fg_color=BRAND["violet"],
        hover_color=BRAND["violet_light"],
        border_color=BRAND["line"],
    ).pack(side="left", padx=(16, 0))

    multi_hint = ctk.CTkLabel(toggle_row, text="", font=f_small, text_color=BRAND["muted"])
    multi_hint.pack(side="left", padx=(10, 0))

    def toggle_show():
        if multiline_var.get():
            # A Textbox can't mask its content — disable "Show value" + hint.
            try:
                show_chk.configure(state="disabled")
            except Exception:  # noqa: BLE001
                pass
            multi_hint.configure(text="(multiline shown unmasked)")
        else:
            try:
                show_chk.configure(state="normal")
            except Exception:  # noqa: BLE001
                pass
            multi_hint.configure(text="")
            entry.configure(show="" if show_var.get() else "*")

    def clear_error(*_):
        if err_lbl.cget("text"):
            err_lbl.configure(text="")

    value_var.trace_add("write", clear_error)
    text_box.bind("<KeyRelease>", clear_error)

    # Mirror-to-remote is only offered when the user has opted in
    # (remote_enabled) AND at least one supported vault CLI is detected on PATH.
    cfg = cc.load_config()
    detected_vaults = cc.detect_vaults()
    show_mirror = bool(cfg.get("remote_enabled")) and bool(detected_vaults)

    if show_mirror:
        ctk.CTkFrame(outer, height=1, fg_color=BRAND["line"]).pack(fill="x", pady=12)
        ctk.CTkCheckBox(
            outer,
            text="Also mirror to remote backend",
            variable=vault_var,
            font=f_small,
            text_color=BRAND["text"],
            fg_color=BRAND["violet"],
            hover_color=BRAND["violet_light"],
            border_color=BRAND["line"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            outer,
            text="Detected: " + ", ".join(detected_vaults) + ". See ROADMAP.md.",
            font=f_small,
            text_color=BRAND["muted"],
            anchor="w",
            justify="left",
            wraplength=520,
        ).pack(anchor="w", fill="x", padx=(28, 0))
    else:
        ctk.CTkFrame(outer, height=1, fg_color=BRAND["line"]).pack(fill="x", pady=12)

    ctk.CTkCheckBox(
        outer,
        text="Store permanently (no local TTL)",
        variable=persist_var,
        font=f_small,
        text_color=BRAND["text"],
        fg_color=BRAND["violet"],
        hover_color=BRAND["violet_light"],
        border_color=BRAND["line"],
    ).pack(anchor="w", pady=(6, 4))

    ctk.CTkLabel(
        outer,
        text=f"Backend: {backend_label}",
        font=f_small,
        text_color=BRAND["cyan_light"],
        anchor="w",
    ).pack(anchor="w", fill="x", pady=(12, 0))

    result: dict = {"ok": False}

    def on_ok(event=None):
        raw = _active_value()
        if not raw:
            err_lbl.configure(text="Please enter a value.")
            (text_box if multiline_var.get() else entry).focus_set()
            return
        result["ok"] = True
        result["value"] = _normalize_value(raw)
        result["vault"] = vault_var.get()
        result["persist"] = persist_var.get()
        _safe_destroy(root)

    def on_cancel(event=None):
        result["ok"] = False
        _safe_destroy(root)

    btn_frame = ctk.CTkFrame(outer, fg_color="transparent")
    btn_frame.pack(fill="x", side="bottom", pady=(16, 0))

    ctk.CTkButton(
        btn_frame,
        text="Cancel",
        width=110,
        height=42,
        command=on_cancel,
        font=f_btn,
        fg_color="transparent",
        hover_color=BRAND["surface_alt"],
        text_color=BRAND["muted"],
        border_width=1,
        border_color=BRAND["line"],
    ).pack(side="right")

    # Primary "Save" action in the cyan→violet brand accent. CTk buttons don't
    # render gradients, so we use the violet end as a solid fill with a cyan
    # hover — reading as the same accent family as the landing page.
    ctk.CTkButton(
        btn_frame,
        text="Save",
        width=140,
        height=42,
        command=on_ok,
        font=f_btn,
        fg_color=BRAND["violet"],
        hover_color=BRAND["cyan"],
        text_color="#ffffff",
    ).pack(side="right", padx=(0, 10))

    def on_return(event=None):
        # In multiline mode Enter inserts a newline in the Textbox; the Save
        # button or Ctrl+Enter submit. Single-line Enter still submits.
        if multiline_var.get():
            return None
        return on_ok(event)

    root.bind("<Return>", on_return)
    root.bind("<Control-Return>", on_ok)
    root.bind("<Escape>", on_cancel)
    root.protocol("WM_DELETE_WINDOW", on_cancel)

    _fade_in(root)
    root.mainloop()

    if not result.get("ok"):
        return False, "", False, False
    return (
        True,
        result["value"],
        bool(result.get("vault")),
        bool(result.get("persist")),
    )


def show_toast(key_name: str, ttl_text: str, backend_label: str) -> None:
    """Brief auto-closing confirmation popup after a successful paste."""
    _enable_dpi_awareness()
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        return
    try:
        root = tk.Tk()
    except Exception:  # noqa: BLE001
        return
    _apply_theme(root)
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="#2a2a2a")

    frame = ttk.Frame(root, padding=14)
    frame.pack()
    ttk.Label(
        frame,
        text=f"[OK] Stored: {key_name}",
        style="Header.TLabel",
    ).pack(anchor="w")
    ttk.Label(
        frame,
        text=f"{ttl_text} · {backend_label}",
        style="Hint.TLabel",
    ).pack(anchor="w", pady=(2, 0))

    root.update_idletasks()
    w = root.winfo_width()
    sw = root.winfo_screenwidth()
    root.geometry(f"+{sw - w - 24}+{24}")
    root.after(1800, lambda: _safe_destroy(root))
    try:
        root.mainloop()
    except Exception:  # noqa: BLE001
        pass


def _print_config() -> None:
    cfg = cc.load_config()
    detected = cc.detect_vaults()
    print(f"remote_enabled: {cfg.get('remote_enabled')}")
    print(f"remote_backend: {cfg.get('remote_backend')}")
    print(f"detected vault CLIs: {', '.join(detected) if detected else '(none)'}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    # Config-management commands run first and exit early — they do not need a
    # key name and do not open the dialog.
    if args.enable_remote or args.disable_remote:
        cfg = cc.set_remote_enabled(bool(args.enable_remote))
        state = "enabled" if cfg["remote_enabled"] else "disabled"
        print(f"OK: remote mirroring {state}.")
        if args.enable_remote and not cc.detect_vaults():
            print(
                "NOTE: no supported vault CLI (age/sops/bw/op) detected on PATH yet; "
                "the mirror option stays hidden until one is installed.",
                file=sys.stderr,
            )
        return 0
    if args.set_remote is not None:
        backend_type = args.set_remote or None  # empty string clears it
        try:
            cfg = cc.set_remote_backend(backend_type, recipient=args.recipient)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"OK: remote backend set to {cfg['remote_backend']}.")
        if backend_type == "sops-age" and not args.recipient:
            print(
                "NOTE: no --recipient given; sops-age needs an age recipient "
                "(age1...) to encrypt to before mirroring will work.",
                file=sys.stderr,
            )
        return 0
    if args.show_config:
        _print_config()
        return 0

    if not args.name:
        print(
            "ERROR: a key name is required (or use --enable-remote / "
            "--disable-remote / --show-config).",
            file=sys.stderr,
        )
        return 1

    try:
        cc._safe_name(args.name)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        cc.default_backend()  # surface backend errors early
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    backend_label = cc.backend_label()

    ttl_cli = args.ttl
    if args.persist and ttl_cli != 24:
        # 24 is the default — only warn when user passed a real value.
        print(
            f"WARN: --ttl={ttl_cli} ignored because --persist is set.",
            file=sys.stderr,
        )

    ok, value, vault, persist_flag = show_dialog(
        args.name,
        args.desc,
        default_persist=args.persist,
        backend_label=backend_label,
    )
    if not ok:
        print("Cancelled.", file=sys.stderr)
        return 1

    if persist_flag and not args.persist and ttl_cli != 24:
        print(
            f"WARN: --ttl={ttl_cli} ignored because 'Store permanently' was "
            "checked in the dialog.",
            file=sys.stderr,
        )

    ttl_hours = None if (args.persist or persist_flag or vault) else int(args.ttl)
    cc.write_credential(args.name, value, ttl_hours=ttl_hours, persist_to_vault=vault)
    ttl_text = "permanent" if ttl_hours is None else f"TTL {ttl_hours}h"
    print(f"OK: {args.name} stored locally ({ttl_text}, {backend_label}).")

    if vault:
        print(
            "NOTE: Remote-mirror requested but no remote backend is " "configured. See ROADMAP.md.",
            file=sys.stderr,
        )

    show_toast(args.name, ttl_text, backend_label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
