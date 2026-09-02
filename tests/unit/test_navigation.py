"""Navigation policy and NavigationEvent (F18)."""

from __future__ import annotations

import tkinter as tk

from tkwry import NavigationEvent, NavigationType, WebView
from tkwry.navigation import call_navigation_handler


def test_navigation_event_fields() -> None:
    event = NavigationEvent(
        url="https://example.com/app",
        navigation_type=NavigationType.Link,
        is_redirect=True,
        is_main_frame=True,
    )
    assert event.url == "https://example.com/app"
    assert event.navigation_type is NavigationType.Link
    assert event.is_redirect is True
    assert event.is_main_frame is True


def test_navigation_event_defaults() -> None:
    event = NavigationEvent(url="https://example.com/")
    assert event.navigation_type is NavigationType.Unknown
    assert event.is_redirect is False
    assert event.is_main_frame is True


def test_call_navigation_handler_event_form() -> None:
    seen: list[NavigationEvent] = []

    def handler(event: NavigationEvent) -> bool:
        seen.append(event)
        return True

    event = NavigationEvent(url="https://example.com/x")
    assert call_navigation_handler(handler, event, url=event.url) is True
    assert seen == [event]


def test_call_navigation_handler_legacy_url_form() -> None:
    seen: list[str] = []

    def handler(url: str) -> bool:
        seen.append(url)
        return url.startswith("https://")

    event = NavigationEvent(url="https://example.com/x")
    assert call_navigation_handler(handler, event, url=event.url) is True
    assert seen == ["https://example.com/x"]


def test_call_navigation_handler_event_name_without_annotation() -> None:
    seen: list[NavigationEvent] = []

    def handler(event) -> bool:  # noqa: ANN001
        seen.append(event)
        return event.url.startswith("https://")

    event = NavigationEvent(url="https://example.com/x")
    assert call_navigation_handler(handler, event, url=event.url) is True
    assert seen == [event]


def test_set_navigation_policy_blocks_urls(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web.set_navigation_policy(
        lambda event: event.url.startswith("https://allowed.example/")
    )
    assert web._invoke_navigation_handler("https://allowed.example/page") is True
    assert web._invoke_navigation_handler("https://blocked.example/page") is False
    web.destroy()
    frame.destroy()


def test_on_navigation_receives_navigation_event(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    seen: list[NavigationEvent] = []

    def allow(event: NavigationEvent) -> bool:
        seen.append(event)
        return True

    web.set_on_navigation(allow)

    assert web._invoke_navigation_handler("https://example.com/") is True
    assert len(seen) == 1
    assert seen[0].url == "https://example.com/"

    web.destroy()
    frame.destroy()


def test_on_navigation_takes_precedence_over_navigation_policy(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")
    web.set_navigation_policy(lambda _event: False)

    def allow(_event: NavigationEvent) -> bool:
        return True

    web.set_on_navigation(allow)

    assert web._invoke_navigation_handler("https://example.com/") is True

    web.destroy()
    frame.destroy()


def test_navigation_policy_ctor_matches_setter(tk_root) -> None:
    frame_ctor = tk.Frame(tk_root)
    frame_setter = tk.Frame(tk_root)

    def policy(event: NavigationEvent) -> bool:
        return event.url.endswith("/ok")

    web_ctor = WebView(frame_ctor, html="<p>x</p>", navigation_policy=policy)
    web_setter = WebView(frame_setter, html="<p>y</p>")
    web_setter.set_navigation_policy(policy)

    url = "https://example.com/ok"
    assert web_ctor._invoke_navigation_handler(url) is True
    assert web_setter._invoke_navigation_handler(url) is True
    assert web_ctor._invoke_navigation_handler("https://example.com/no") is False
    assert web_setter._invoke_navigation_handler("https://example.com/no") is False

    web_ctor.destroy()
    web_setter.destroy()
    frame_ctor.destroy()
    frame_setter.destroy()


def test_on_navigation_unannotated_event_param(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")

    def allow(event) -> bool:  # noqa: ANN001
        return event.url.startswith("https://")

    web.set_on_navigation(allow)

    assert web._invoke_navigation_handler("https://example.com/") is True
    assert web._invoke_navigation_handler("http://example.com/") is False

    web.destroy()
    frame.destroy()


def test_legacy_on_navigation_url_handler(tk_root) -> None:
    frame = tk.Frame(tk_root)
    web = WebView(frame, html="<p>x</p>")

    def allow(url: str) -> bool:
        return url.startswith("https://")

    web.set_on_navigation(allow)

    assert web._invoke_navigation_handler("https://example.com/") is True
    assert web._invoke_navigation_handler("http://example.com/") is False

    web.destroy()
    frame.destroy()
