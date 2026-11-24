"""
Firefox cookie export -> Netscape-format cookies.txt (for `yt-dlp --cookies`).

Firefox stores cookies unencrypted in <profile>/cookies.sqlite, so we can read
them directly with sqlite3. Chrome/Edge/Brave are NOT handled here on purpose:
their cookies are encrypted (app-bound encryption on recent Windows builds).
For those, use a browser extension that exports cookies.txt instead.

By default every cookie of the profile is written, so the same file works for
any site yt-dlp supports (YouTube, Instagram, X, ...). Pass `domains` to
restrict the export to specific sites and keep the file smaller. The file
holds the user's logged-in sessions: it is stored in the app's data folder
with owner-only permissions where the OS supports it and should never be shared.

This module only knows how to *find and read* Firefox's data. The cookies.txt
format itself lives in cookie/netscape.py, and the app-level "export into the
app's own cookies.txt and remember it" step is cookie.store.export_cookies_from_firefox.

Standalone (from the project root):
    python -m cookie cookies.txt
"""

import configparser
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from netscape import Cookie, has_login_cookie, write_cookie_file

logger = logging.getLogger(__name__)

# None = export every cookie. A tuple such as ("youtube.com", "google.com")
# limits the export to those domains (and their subdomains).
DEFAULT_DOMAINS = None


class CookieExportError(Exception):
    pass


@dataclass
class ExportResult:
    path: Path
    profile: str
    count: int
    logged_in: bool


# ---------------------------------------------------------------------------
# Profile discovery
# ---------------------------------------------------------------------------

def _firefox_root() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Firefox"
    return Path.home() / ".mozilla" / "firefox"


def _resolve(root: Path, path: str | Path, relative: bool) -> Path:
    if relative:
        real_path = root / Path(path)
    else:
        real_path = Path(path)
    return real_path.resolve()


def find_firefox_profiles() -> list:
    """Profile directories that contain a cookies.sqlite, default profile first."""
    root = _firefox_root()
    ini = root / "profiles.ini"
    if not ini.is_file():
        return []

    cp = configparser.ConfigParser(interpolation=None)
    cp.read(ini, encoding="utf-8")

    defaults, others = [], []
    for section in cp.sections():
        if section.startswith("Install") and cp.has_option(section, "Default"):
            defaults.append(_resolve(root, cp.get(section, "Default"), True))
        elif section.startswith("Profile") and cp.has_option(section, "Path"):
            relative = cp.get(section, "IsRelative", fallback="1") == "1"
            p = _resolve(root, cp.get(section, "Path"), relative)
            if cp.get(section, "Default", fallback="0") == "1":
                defaults.append(p)
            else:
                others.append(p)

    ordered = []
    for p in defaults + others:
        if p not in ordered and os.path.isfile(os.path.join(p, "cookies.sqlite")):
            ordered.append(p)
    return ordered


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _domain_ok(host: str, domains) -> bool:
    if not domains:  # None / empty: no filtering, keep everything
        return True
    h = host.lstrip(".").lower()
    return any(h == d or h.endswith("." + d) for d in domains)


def _read_cookies(profile: str | Path, domains) -> list:
    """Reads matching cookies (all of them if `domains` is None) from a *copy* of cookies.sqlite (Firefox keeps the
    original open/locked, and recent data may still sit in the -wal file)."""
    src = Path(profile) / "cookies.sqlite"
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "cookies.sqlite"
        try:
            shutil.copy2(src, dst)
            src_wal = src.with_suffix(src.suffix + "-wal")
            dst_wal = dst.with_suffix(dst.suffix + "-wal")
            if src_wal.is_file():
                shutil.copy2(src_wal, dst_wal)
        except OSError as e:
            raise CookieExportError(f"Could not copy Firefox cookie database: {e}") from e

        con = sqlite3.connect(dst)
        try:
            rows = con.execute(
                "SELECT host, path, isSecure, expiry, name, value, isHttpOnly FROM moz_cookies"
            ).fetchall()
        except sqlite3.Error as e:
            raise CookieExportError(f"Could not read Firefox cookie database: {e}") from e
        finally:
            con.close()

    cookies = [Cookie(*r) for r in rows]
    return [c for c in cookies if _domain_ok(c.host, domains)]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_firefox_cookies(out_path: str | Path, profile: str | None = None,
                           domains=DEFAULT_DOMAINS) -> ExportResult:
    """Writes a Netscape cookies.txt to out_path.

    If `profile` is None, every profile is checked and the one that looks
    signed in to Google/YouTube (or, failing that, has the most cookies) is
    used. `domains=None` exports all cookies.
    Raises CookieExportError if Firefox/profile/cookies can't be found.
    """
    out_path = Path(out_path)
    profiles = [profile] if profile else find_firefox_profiles()
    if not profiles:
        raise CookieExportError("No Firefox profile with a cookie database was found.")

    best = None  # (logged_in, count, profile, cookies)
    for p in profiles:
        try:
            cookies = _read_cookies(p, domains)
        except CookieExportError as e:
            logger.warning("Skipping profile %s: %s", p, e)
            continue
        logged_in = has_login_cookie(cookies)
        candidate = (logged_in, len(cookies), p, cookies)
        if best is None or candidate[:2] > best[:2]:
            best = candidate

    if best is None or not best[3]:
        raise CookieExportError("No cookies found in Firefox.")

    logged_in, count, chosen, cookies = best
    write_cookie_file(out_path, cookies)

    # Never log cookie values -- only counts/metadata.
    logger.info("Exported %d cookies from %s (signed in: %s)", count, chosen, logged_in)
    return ExportResult(path=out_path, profile=chosen, count=count, logged_in=logged_in)