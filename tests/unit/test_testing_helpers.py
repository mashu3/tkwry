"""Coverage for :mod:`tkwry.testing` helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tkwry import testing


def test_pump_drives_update_and_after(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(tk_root, "update_idletasks", lambda: calls.append("idle"))
    monkeypatch.setattr(tk_root, "update", lambda: calls.append("update"))
    monkeypatch.setattr(tk_root, "after", lambda delay: calls.append(f"after:{delay}"))
    monkeypatch.setattr(testing.sys, "platform", "darwin")

    testing.pump(tk_root, steps=2, delay_ms=7)

    assert calls.count("idle") == 2
    assert calls.count("update") == 4  # once before after, once after
    assert calls.count("after:7") == 2


def test_pump_on_linux_calls_gtk_helpers(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    gtk_calls: list[str] = []

    def ensure() -> None:
        gtk_calls.append("ensure")

    def pump_events() -> None:
        gtk_calls.append("pump")

    monkeypatch.setattr(testing.sys, "platform", "linux")
    monkeypatch.setattr("tkwry._core.ensure_gtk_init", ensure, raising=False)
    monkeypatch.setattr("tkwry._core.pump_events", pump_events, raising=False)
    monkeypatch.setattr(tk_root, "update_idletasks", lambda: None)
    monkeypatch.setattr(tk_root, "update", lambda: None)
    monkeypatch.setattr(tk_root, "after", lambda _delay: None)

    testing.pump(tk_root, steps=1, delay_ms=1)

    assert gtk_calls == ["ensure", "pump"]


def test_wait_until_true_and_false(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(testing, "pump", lambda *_a, **_k: None)
    assert testing.wait_until(tk_root, lambda: True, steps=3) is True

    counter = {"n": 0}

    def later() -> bool:
        counter["n"] += 1
        return counter["n"] >= 3

    assert testing.wait_until(tk_root, later, steps=5) is True
    assert testing.wait_until(tk_root, lambda: False, steps=2) is False


def test_wait_ready_asserts_and_pumps(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    web = MagicMock()
    web.wait_until_ready.return_value = True
    pumped: list[tuple] = []
    monkeypatch.setattr(
        testing, "pump", lambda root, **kwargs: pumped.append((root, kwargs))
    )

    testing.wait_ready(tk_root, web, timeout=1.5, pump_steps=11)

    web.wait_until_ready.assert_called_once_with(timeout=1.5)
    assert pumped == [(tk_root, {"steps": 11})]

    web.wait_until_ready.return_value = False
    with pytest.raises(AssertionError, match="did not become ready"):
        testing.wait_ready(tk_root, web)


def test_wait_eval_returns_result_or_none(
    tk_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    web = MagicMock()

    def fake_eval(script: str, callback) -> None:
        callback(f"result:{script}")

    web.eval_js_with_callback.side_effect = fake_eval
    monkeypatch.setattr(testing, "wait_until", lambda *_a, **_k: True)

    assert testing.wait_eval(tk_root, web, "1+1") == "result:1+1"

    monkeypatch.setattr(testing, "wait_until", lambda *_a, **_k: False)
    assert testing.wait_eval(tk_root, web, "missing") is None


def test_wait_title_polls_until_match(tk_root, monkeypatch: pytest.MonkeyPatch) -> None:
    web = MagicMock()
    titles = iter(["other", "Hello World"])

    def fake_eval(_script: str, callback) -> None:
        callback(next(titles))

    web.eval_js_with_callback.side_effect = fake_eval

    # Drive wait_until with real pump stub so predicates advance.
    monkeypatch.setattr(testing, "pump", lambda *_a, **_k: None)
    assert testing.wait_title(tk_root, web, "Hello", steps=5) is True

    web.eval_js_with_callback.side_effect = lambda _s, cb: cb("nope")
    assert testing.wait_title(tk_root, web, "Hello", steps=2) is False
