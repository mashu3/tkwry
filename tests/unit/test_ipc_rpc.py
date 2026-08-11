"""Unit tests for the thin ``tkwry.ipc`` RPC helpers."""

from __future__ import annotations

import json

from tkwry.ipc import (
    RPC_BOOTSTRAP_JS,
    dispatch_rpc,
    merge_initialization_script,
    parse_rpc_request,
    settle_script,
)


def test_parse_rpc_request_ok() -> None:
    raw = json.dumps(
        {"__tkwry": "rpc", "id": "r1", "method": "add", "params": [1, 2]}
    )
    req = parse_rpc_request(raw)
    assert req is not None
    assert req.id == "r1"
    assert req.method == "add"
    assert req.params == (1, 2)


def test_parse_rpc_request_rejects_plain_ipc() -> None:
    assert parse_rpc_request(json.dumps({"action": "increment"})) is None
    assert parse_rpc_request("not-json") is None
    empty_id = json.dumps({"__tkwry": "rpc", "id": "", "method": "x"})
    assert parse_rpc_request(empty_id) is None
    assert (
        parse_rpc_request(
            json.dumps({"__tkwry": "rpc", "id": "1", "method": "x", "params": "bad"})
        )
        is None
    )


def test_dispatch_rpc_success_and_unknown() -> None:
    def add(a: int, b: int) -> int:
        return a + b

    req = parse_rpc_request(
        json.dumps({"__tkwry": "rpc", "id": "1", "method": "add", "params": [2, 3]})
    )
    assert req is not None
    ok, value = dispatch_rpc({"add": add}, req)
    assert ok is True
    assert value == 5

    bad = parse_rpc_request(
        json.dumps({"__tkwry": "rpc", "id": "2", "method": "missing", "params": []})
    )
    assert bad is not None
    ok, value = dispatch_rpc({"add": add}, bad)
    assert ok is False
    assert "unknown method" in str(value)


def test_dispatch_rpc_future_passthrough() -> None:
    from concurrent.futures import Future

    fut: Future[int] = Future()
    fut.set_result(7)

    def returns_future() -> Future[int]:
        return fut

    req = parse_rpc_request(
        json.dumps({"__tkwry": "rpc", "id": "1", "method": "f", "params": []})
    )
    assert req is not None
    outcome = dispatch_rpc({"f": returns_future}, req)
    assert outcome is fut


def test_dispatch_rpc_exception_and_non_json() -> None:
    def boom() -> None:
        raise RuntimeError("nope")

    def bad_result() -> object:
        return object()

    boom_req = parse_rpc_request(
        json.dumps({"__tkwry": "rpc", "id": "1", "method": "boom", "params": []})
    )
    assert boom_req is not None
    ok, value = dispatch_rpc({"boom": boom}, boom_req)
    assert ok is False
    assert value == "nope"

    bad_req = parse_rpc_request(
        json.dumps({"__tkwry": "rpc", "id": "2", "method": "bad", "params": []})
    )
    assert bad_req is not None
    ok, value = dispatch_rpc({"bad": bad_result}, bad_req)
    assert ok is False
    assert "JSON-serializable" in str(value)


def test_settle_script_roundtrip() -> None:
    script = settle_script("r9", ok=True, value={"n": 1})
    assert '"r9"' in script
    assert "true" in script
    assert '{"n": 1}' in script or '{"n":1}' in script
    err = settle_script("r9", ok=False, value="fail")
    assert "false" in err
    assert "fail" in err


def test_merge_initialization_script() -> None:
    assert merge_initialization_script(None, rpc_enabled=False) is None
    assert merge_initialization_script("void 0", rpc_enabled=False) == "void 0"
    merged = merge_initialization_script("void 0;", rpc_enabled=True)
    assert merged is not None
    assert RPC_BOOTSTRAP_JS in merged
    assert merged.endswith("void 0;")
    only = merge_initialization_script(None, rpc_enabled=True)
    assert only == RPC_BOOTSTRAP_JS
