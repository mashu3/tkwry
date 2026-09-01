"""Named browser profiles (``profile=`` / ``user_data_dir=`` on WebView)."""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

import pytest

from tkwry import WebSession, WebView
from tkwry.profile import (
    close_profile,
    get_profile_session,
    profile_directory,
    reset_profile_registry_for_tests,
    set_profiles_base,
    validate_profile_name,
)


@pytest.fixture(autouse=True)
def _isolated_profiles(tmp_path: Path) -> None:
    reset_profile_registry_for_tests()
    set_profiles_base(tmp_path / "profiles")
    yield
    reset_profile_registry_for_tests()


def test_validate_profile_name_rejects_unsafe() -> None:
    validate_profile_name("account_a")
    with pytest.raises(ValueError, match="profile name"):
        validate_profile_name("../escape")
    with pytest.raises(ValueError, match="profile name"):
        validate_profile_name("")


def test_profile_directory_creates_under_base(tmp_path: Path) -> None:
    set_profiles_base(tmp_path / "root")
    path = profile_directory("default")
    assert path == (tmp_path / "root" / "default").resolve()


def test_get_profile_session_is_shared_and_persistent() -> None:
    first = get_profile_session("default")
    second = get_profile_session("default")
    assert first is second
    assert first.data_directory == profile_directory("default")
    assert first.data_directory.is_dir()
    assert first.ephemeral is False


def test_close_profile_drops_registry_entry() -> None:
    session = get_profile_session("work")
    close_profile("work")
    assert session.closed
    again = get_profile_session("work")
    assert again is not session
    assert not again.closed


def test_profile_names_stay_distinct_on_unix() -> None:
    if sys.platform == "win32":
        pytest.skip("Unix-only distinction")
    first = get_profile_session("Foo")
    second = get_profile_session("foo")
    assert first is not second
    assert profile_directory("Foo") != profile_directory("foo")


def test_profile_names_case_insensitive_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tkwry.profile.sys.platform", "win32")
    monkeypatch.setattr(
        "tkwry.profile.os.path.normcase",
        lambda name: name.casefold(),
    )
    first = get_profile_session("Foo")
    second = get_profile_session("foo")
    assert first is second
    assert profile_directory("Foo") == profile_directory("foo")
    close_profile("Foo")
    assert first.closed
    again = get_profile_session("foo")
    assert again is not first
    assert not again.closed


def test_webview_profile_shares_session(tk_root) -> None:
    frame_a = tk.Frame(tk_root)
    frame_b = tk.Frame(tk_root)
    left = WebView(frame_a, html="<p>a</p>", profile="account_a")
    right = WebView(frame_b, html="<p>b</p>", profile="account_a")
    try:
        assert left.profile == "account_a"
        assert right.profile == "account_a"
        assert left._session is right._session
        assert left._owned_session is None
        assert right._owned_session is None
    finally:
        left.destroy()
        right.destroy()


def test_webview_user_data_dir_owns_session(tk_root, tmp_path: Path) -> None:
    data = tmp_path / "browser_data"
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>", user_data_dir=data)
    try:
        assert web.profile is None
        assert web._owned_session is not None
        assert web._session.data_directory == data.resolve()
    finally:
        web.destroy()


def test_webview_rejects_profile_with_session(tk_root, tmp_path: Path) -> None:
    frame = tk.Frame(tk_root)
    session = WebSession(data_directory=tmp_path / "p")
    with pytest.raises(ValueError, match="only one of"):
        WebView(frame, html="<p>x</p>", session=session, profile="a")


def test_webview_rejects_profile_with_user_data_dir(tk_root) -> None:
    frame = tk.Frame(tk_root)
    with pytest.raises(ValueError, match="only one of"):
        WebView(
            frame,
            html="<p>x</p>",
            profile="a",
            user_data_dir="/tmp/data",
        )


def test_webview_rejects_untrusted_with_profile(tk_root) -> None:
    frame = tk.Frame(tk_root)
    with pytest.raises(ValueError, match="profile="):
        WebView(frame, html="<p>x</p>", untrusted=True, profile="a")
