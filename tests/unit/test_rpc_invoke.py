"""RPC invoke ergonomics — ``WebView.rpc`` and ``window.tkwry.invoke``."""

from __future__ import annotations

import json

import pytest
from support.linux import noop_linux_runtime

from tkwry import WebView
from tkwry.ipc import RPC_BOOTSTRAP_JS, dispatch_rpc, parse_rpc_request


@pytest.fixture(autouse=True)
def _noop_linux_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    noop_linux_runtime(monkeypatch)


def _make_frame(tk_root):
    import tkinter as tk

    return tk.Frame(tk_root)


def test_rpc_named_decorator_registers_method(tk_root) -> None:
    frame = _make_frame(tk_root)
    web = WebView(frame, html="<p>x</p>")

    @web.rpc("get_data")
    def handler(id: int) -> dict[str, int]:
        return {"id": id}

    assert "get_data" in web._rpc_methods
    assert web._rpc_methods["get_data"].handler is handler

    web.destroy()
    frame.destroy()


def test_rpc_bare_decorator_uses_function_name(tk_root) -> None:
    frame = _make_frame(tk_root)
    web = WebView(frame, html="<p>x</p>")

    @web.rpc
    def ping() -> str:
        return "pong"

    assert "ping" in web._rpc_methods

    web.destroy()
    frame.destroy()


def test_rpc_direct_call_matches_expose(tk_root) -> None:
    frame = _make_frame(tk_root)
    web = WebView(frame, html="<p>x</p>")

    def add(a: int, b: int) -> int:
        return a + b

    web.rpc(add)
    assert "add" in web._rpc_methods

    web.destroy()
    frame.destroy()


def test_rpc_rejects_duplicate_names(tk_root) -> None:
    frame = _make_frame(tk_root)
    web = WebView(frame, html="<p>x</p>")

    @web.rpc("same")
    def first() -> int:
        return 1

    with pytest.raises(ValueError, match="already exposed"):

        @web.rpc("same")
        def second() -> int:
            return 2

    assert web._rpc_methods["same"].handler is first

    web.destroy()
    frame.destroy()


def test_rpc_untrusted_rejects(tk_root) -> None:
    frame = _make_frame(tk_root)
    web = WebView(frame, html="<p>x</p>", untrusted=True)

    with pytest.raises(ValueError, match="untrusted"):

        @web.rpc("ping")
        def ping() -> int:
            return 1

    web.destroy()
    frame.destroy()


def test_bootstrap_includes_invoke() -> None:
    assert "window.tkwry.invoke" in RPC_BOOTSTRAP_JS
    assert "kwargs: data" in RPC_BOOTSTRAP_JS


def test_invoke_payload_binds_kwargs() -> None:
    req = parse_rpc_request(
        json.dumps(
            {
                "__tkwry": "rpc",
                "version": 1,
                "id": "r1",
                "method": "get_data",
                "params": [],
                "kwargs": {"id": 123},
            }
        )
    )
    assert req is not None

    def get_data(*, id: int) -> dict[str, int]:
        return {"id": id}

    ok, value = dispatch_rpc({"get_data": get_data}, req)
    assert ok is True
    assert value == {"id": 123}
