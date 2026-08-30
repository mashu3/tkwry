"""Origin allowlists and navigation policy helpers."""

from __future__ import annotations

import pytest

from tkwry._origin import (
    APP_ORIGINS,
    INLINE_ORIGINS,
    app_navigation_allowed,
    is_external_http_url,
    normalize_navigation_allow,
    open_in_browser,
    origin_allowed,
    origin_of,
    path_prefix_matches,
    resolve_bridge_origins,
    untrusted_navigation_allowed,
)


def test_origin_of_common_forms() -> None:
    assert origin_of(None) == "null"
    assert origin_of("") == "null"
    assert origin_of("about:blank") == "about:blank"
    assert origin_of("https://Example.com:443/path") == "https://example.com"
    assert origin_of("http://localhost:8080/x") == "http://localhost:8080"
    assert origin_of("tkwry://localhost/index.html") == "tkwry://localhost"
    assert origin_of("https://tkwry.localhost/app.js") == "https://tkwry.localhost"
    assert origin_of("http://tkwry.localhost/app.js") == "http://tkwry.localhost"
    assert origin_of("file:///tmp/index.html") == "file://"
    assert origin_of("data:text/html,hi") == "null"


def test_resolve_bridge_origins_infers_from_content() -> None:
    assert resolve_bridge_origins(None, url=None, html="<p>x</p>", app=False) == (
        INLINE_ORIGINS
    )
    assert resolve_bridge_origins(None, url=None, html=None, app=True) == APP_ORIGINS
    assert resolve_bridge_origins(
        None, url="https://example.com/app", html=None, app=False
    ) == frozenset({"https://example.com"})
    assert (
        resolve_bridge_origins("*", url="https://example.com/", html=None, app=False)
        == "*"
    )
    assert resolve_bridge_origins(
        ["https://trusted.example/path"],
        url=None,
        html=None,
        app=False,
    ) == frozenset({"https://trusted.example/path"})


def test_resolve_bridge_origins_rejects_bare_string() -> None:
    with pytest.raises(TypeError, match="sequence"):
        resolve_bridge_origins("https://example.com", url=None, html=None, app=False)


def test_origin_allowed_star_and_blank() -> None:
    assert origin_allowed("https://evil.example/", "*")
    assert origin_allowed("about:blank", INLINE_ORIGINS)
    assert origin_allowed("", INLINE_ORIGINS)
    assert not origin_allowed("https://evil.example/", INLINE_ORIGINS)
    assert not origin_allowed("https://evil.example/", APP_ORIGINS)


def test_origin_allowed_path_prefix() -> None:
    allow = frozenset({"https://trusted.example/app"})
    assert origin_allowed("https://trusted.example/app", allow)
    assert origin_allowed("https://trusted.example/app/", allow)
    assert origin_allowed("https://trusted.example/app/page", allow)
    assert not origin_allowed("https://trusted.example/application", allow)
    assert not origin_allowed("https://trusted.example/other", allow)
    assert not origin_allowed("https://other.example/app", allow)
    assert origin_allowed(
        "https://trusted.example/any", frozenset({"https://trusted.example"})
    )


def test_path_prefix_matches_boundary() -> None:
    assert path_prefix_matches("/app", "/app")
    assert path_prefix_matches("/app/x", "/app")
    assert path_prefix_matches("/app/x", "/app/")
    assert not path_prefix_matches("/application", "/app")
    assert path_prefix_matches("/anything", "/")


def test_app_and_untrusted_navigation_policy() -> None:
    assert app_navigation_allowed("tkwry://localhost/index.html")
    assert app_navigation_allowed("https://tkwry.localhost/x")
    assert app_navigation_allowed("http://tkwry.localhost/x")
    assert app_navigation_allowed("about:blank")
    assert not app_navigation_allowed("https://example.com/")
    assert not app_navigation_allowed("file:///tmp/x")
    assert not app_navigation_allowed("data:text/html,<p>x</p>")
    assert not app_navigation_allowed("blob:https://example.com/uuid")

    assert untrusted_navigation_allowed("https://example.com/")
    assert untrusted_navigation_allowed("http://localhost:8080/")
    assert untrusted_navigation_allowed("about:blank")
    assert not untrusted_navigation_allowed("tkwry://localhost/")
    assert not untrusted_navigation_allowed("https://tkwry.localhost/")
    assert not untrusted_navigation_allowed("http://tkwry.localhost/")
    assert not untrusted_navigation_allowed("file:///tmp/x")
    assert not untrusted_navigation_allowed("javascript:alert(1)")


def test_is_external_http_url() -> None:
    assert is_external_http_url("https://example.com/x")
    assert is_external_http_url("http://localhost:8080/")
    assert not is_external_http_url("file:///tmp/x")
    assert not is_external_http_url("javascript:alert(1)")
    assert not is_external_http_url("https://tkwry.localhost/index.html")
    assert not is_external_http_url("http://tkwry.localhost/index.html")
    assert not is_external_http_url("tkwry://localhost/index.html")


def test_normalize_navigation_allow() -> None:
    assert normalize_navigation_allow(["https://Example.com/app"]) == frozenset(
        {"https://example.com/app"}
    )
    with pytest.raises(TypeError, match="sequence"):
        normalize_navigation_allow("https://example.com")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="empty"):
        normalize_navigation_allow([])
    with pytest.raises(ValueError, match="concrete"):
        normalize_navigation_allow(["*"])


def test_open_in_browser_http_only(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []

    def fake_open(url: str, new: int = 2) -> bool:
        opened.append(url)
        assert new == 2
        return True

    monkeypatch.setattr("tkwry._origin.webbrowser.open", fake_open)
    assert open_in_browser("https://example.com/x") is True
    assert opened == ["https://example.com/x"]
    assert open_in_browser("file:///tmp/x") is False
    assert open_in_browser("https://tkwry.localhost/x") is False
    assert opened == ["https://example.com/x"]
