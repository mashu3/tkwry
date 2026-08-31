"""Named browser profiles — shared :class:`~tkwry.WebSession` by profile name."""

from __future__ import annotations

import os
import re
from pathlib import Path

from tkwry.session import WebSession

_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

_profiles_base: Path | None = None
_profile_sessions: dict[str, WebSession] = {}


def profiles_base() -> Path:
    """Root directory for named profiles (``profile=`` on :class:`~tkwry.WebView`)."""
    if _profiles_base is not None:
        return _profiles_base
    env = os.environ.get("TKWRY_PROFILES_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".tkwry" / "profiles"


def set_profiles_base(path: str | Path) -> None:
    """Set the root directory for ``profile=`` before the first profile is opened.

    Does not relocate or close sessions that were already opened under a
    previous base.
    """
    global _profiles_base
    _profiles_base = Path(path).expanduser().resolve()


def validate_profile_name(name: str) -> None:
    """Raise :class:`ValueError` if *name* is not a safe profile identifier."""
    if not isinstance(name, str) or not name:
        raise ValueError("profile name must be a non-empty string")
    if not _PROFILE_NAME_RE.fullmatch(name):
        raise ValueError(
            f"profile name must match [A-Za-z0-9][A-Za-z0-9._-]{{0,63}} (got {name!r})"
        )


def profile_directory(name: str) -> Path:
    """Persistent data directory for a named profile."""
    validate_profile_name(name)
    return profiles_base() / name


def get_profile_session(name: str) -> WebSession:
    """Return a shared persistent :class:`~tkwry.WebSession` for *name*.

    Creates the on-disk directory and session on first use. The session stays
    open for the process lifetime unless :func:`close_profile` is called.
    """
    validate_profile_name(name)
    existing = _profile_sessions.get(name)
    if existing is not None:
        if existing.closed:
            del _profile_sessions[name]
        else:
            return existing
    session = WebSession(data_directory=profile_directory(name))
    _profile_sessions[name] = session
    return session


def close_profile(name: str) -> None:
    """Close a named profile session and drop it from the in-process registry."""
    validate_profile_name(name)
    session = _profile_sessions.pop(name, None)
    if session is not None and not session.closed:
        session.close()


def reset_profile_registry_for_tests() -> None:
    """Clear the in-process profile registry (tests only)."""
    _profile_sessions.clear()
