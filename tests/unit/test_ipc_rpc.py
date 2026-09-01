"""Unit tests for the thin ``tkwry.ipc`` RPC helpers."""

from __future__ import annotations

import json
from concurrent.futures import Future
from datetime import datetime, timezone

import pytest

from tkwry.exceptions import RpcSerializationError
from tkwry.ipc import (
    MAX_RPC_ARGS,
    MAX_RPC_KWARGS,
    MAX_RPC_MESSAGE_BYTES,
    MAX_RPC_STREAM_CHUNK_BYTES,
    RPC_BOOTSTRAP_JS,
    RPC_STREAM_DONE,
    RPC_VERSION,
    RpcMessageTooLarge,
    RpcRegistration,
    RpcRequest,
    bind_rpc_arguments,
    dispatch_rpc,
    emit_script,
    format_rpc_error,
    merge_initialization_script,
    parse_rpc_request,
    rpc_bump_epoch_script,
    rpc_cancel_event,
    rpc_cancelled,
    rpc_id_epoch,
    settle_script,
    stream_chunk_script,
)


def test_parse_rpc_request_ok() -> None:
    raw = json.dumps({"__tkwry": "rpc", "id": "r1", "method": "add", "params": [1, 2]})
    req = parse_rpc_request(raw)
    assert req is not None
    assert req.id == "r1"
    assert req.method == "add"
    assert req.params == (1, 2)
    assert req.kwargs == {}
    assert req.cancel is False
    assert req.stream is False


def test_parse_rpc_request_version_and_cancel() -> None:
    ok = parse_rpc_request(
        json.dumps(
            {
                "__tkwry": "rpc",
                "version": RPC_VERSION,
                "id": "r1",
                "method": "add",
                "params": [1],
            }
        )
    )
    assert ok is not None
    assert ok.method == "add"

    unknown = parse_rpc_request(
        json.dumps({"__tkwry": "rpc", "version": 99, "id": "r2", "method": "add"})
    )
    assert unknown is not None
    assert unknown.reject is not None
    assert unknown.reject["type"] == "RpcProtocolError"

    cancel = parse_rpc_request(
        json.dumps({"__tkwry": "rpc", "id": "r3", "cancel": True})
    )
    assert cancel is not None
    assert cancel.cancel is True
    ok, value = dispatch_rpc({}, cancel)
    assert ok is False
    assert value["type"] == "RpcCancelledError"


def test_parse_rpc_request_kwargs() -> None:
    raw = json.dumps(
        {
            "__tkwry": "rpc",
            "id": "r1",
            "method": "greet",
            "params": ["hi"],
            "kwargs": {"times": 3},
        }
    )
    req = parse_rpc_request(raw)
    assert req is not None
    assert req.params == ("hi",)
    assert req.kwargs == {"times": 3}


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
    assert (
        parse_rpc_request(
            json.dumps({"__tkwry": "rpc", "id": "1", "method": "x", "kwargs": ["bad"]})
        )
        is None
    )


def test_rpc_id_epoch_and_bump_script() -> None:
    assert rpc_id_epoch("0:r1") == 0
    assert rpc_id_epoch("12:r99") == 12
    assert rpc_id_epoch("r1") is None
    assert rpc_id_epoch("bad:r1") is None
    assert "_bumpEpoch(2)" in rpc_bump_epoch_script(2)


def test_rpc_bootstrap_uses_epoch_prefixed_ids() -> None:
    assert "nextRpcId" in RPC_BOOTSTRAP_JS
    assert "_bumpEpoch" in RPC_BOOTSTRAP_JS


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
    assert isinstance(value, dict)
    assert value["type"] == "RpcMethodNotFound"
    assert "unknown method" in value["message"]


def test_dispatch_rpc_future_passthrough() -> None:
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


def test_parse_rpc_request_rejects_oversize(monkeypatch: pytest.MonkeyPatch) -> None:
    import tkwry.ipc as ipc

    monkeypatch.setattr(ipc, "MAX_RPC_MESSAGE_BYTES", 64)
    raw = json.dumps(
        {"__tkwry": "rpc", "id": "r9", "method": "x", "params": ["y" * 80]}
    )
    req = ipc.parse_rpc_request(raw)
    assert req is not None
    assert req.id == "r9"
    assert req.reject is not None
    assert req.reject["type"] == "RpcMessageTooLarge"
    ok, value = dispatch_rpc({}, req)
    assert ok is False
    assert value["type"] == "RpcMessageTooLarge"


def test_parse_rpc_request_rejects_native_reject_envelope() -> None:
    raw = json.dumps(
        {
            "__tkwry": "rpc",
            "id": "r1",
            "__tkwry_reject": "RpcMessageTooLarge",
            "message": "RPC message exceeds limit",
        }
    )
    req = parse_rpc_request(raw)
    assert req is not None
    assert req.reject is not None
    assert req.reject["type"] == "RpcMessageTooLarge"
    assert "exceeds" in req.reject["message"]


def test_parse_rpc_request_rejects_too_many_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tkwry.ipc as ipc

    monkeypatch.setattr(ipc, "MAX_RPC_ARGS", 2)
    raw = json.dumps({"__tkwry": "rpc", "id": "r1", "method": "x", "params": [1, 2, 3]})
    req = ipc.parse_rpc_request(raw)
    assert req is not None
    assert req.reject is not None
    assert req.reject["type"] == "RpcArgumentLimitError"


def test_parse_rpc_request_rejects_too_many_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tkwry.ipc as ipc

    monkeypatch.setattr(ipc, "MAX_RPC_KWARGS", 1)
    raw = json.dumps(
        {
            "__tkwry": "rpc",
            "id": "r1",
            "method": "x",
            "params": [],
            "kwargs": {"a": 1, "b": 2},
        }
    )
    req = ipc.parse_rpc_request(raw)
    assert req is not None
    assert req.reject is not None
    assert req.reject["type"] == "RpcArgumentLimitError"


def test_rpc_limits_match_documented_defaults() -> None:
    assert MAX_RPC_MESSAGE_BYTES == 10 * 1024 * 1024
    assert MAX_RPC_STREAM_CHUNK_BYTES == MAX_RPC_MESSAGE_BYTES
    assert MAX_RPC_ARGS == 256
    assert MAX_RPC_KWARGS == 256


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
    assert value == {"type": "RuntimeError", "message": "nope"}

    ok, value = dispatch_rpc(
        {"boom": boom},
        boom_req,
        include_traceback=True,
    )
    assert ok is False
    assert isinstance(value, dict)
    assert "traceback" in value
    assert "RuntimeError" in value["traceback"]

    bad_req = parse_rpc_request(
        json.dumps({"__tkwry": "rpc", "id": "2", "method": "bad", "params": []})
    )
    assert bad_req is not None
    ok, value = dispatch_rpc({"bad": bad_result}, bad_req)
    assert ok is False
    assert value["type"] == "RpcSerializationError"


def test_dispatch_rpc_rejects_datetime_and_nan() -> None:
    def now() -> datetime:
        return datetime.now(timezone.utc)

    def inf() -> float:
        return float("inf")

    req = parse_rpc_request(
        json.dumps({"__tkwry": "rpc", "id": "1", "method": "now", "params": []})
    )
    assert req is not None
    ok, value = dispatch_rpc({"now": now}, req)
    assert ok is False
    assert value["type"] == "RpcSerializationError"

    inf_req = parse_rpc_request(
        json.dumps({"__tkwry": "rpc", "id": "2", "method": "inf", "params": []})
    )
    assert inf_req is not None
    ok, value = dispatch_rpc({"inf": inf}, inf_req)
    assert ok is False
    assert value["type"] == "RpcSerializationError"


def test_settle_and_emit_reject_non_json() -> None:
    with pytest.raises(RpcSerializationError):
        settle_script("r1", ok=True, value=object())
    with pytest.raises(RpcSerializationError):
        emit_script("evt", datetime.now(timezone.utc))
    with pytest.raises(RpcSerializationError):
        emit_script("evt", float("nan"))


def test_rpc_cancelled_false_outside_handler() -> None:
    assert rpc_cancelled() is False
    assert rpc_cancel_event() is None


def test_dispatch_rpc_reject_passthrough() -> None:
    req = RpcRequest(
        id="r1",
        method="",
        params=(),
        reject={"type": "RpcMessageTooLarge", "message": "too big"},
    )
    ok, value = dispatch_rpc({}, req)
    assert ok is False
    assert value["type"] == "RpcMessageTooLarge"


def test_dispatch_rpc_worker_submit() -> None:
    submitted: list[object] = []

    def submit(fn):  # noqa: ANN001
        fut: Future[object] = Future()
        submitted.append(fn)
        fut.set_result(fn())
        return fut

    def heavy() -> str:
        return "ok"

    req = parse_rpc_request(
        json.dumps({"__tkwry": "rpc", "id": "1", "method": "heavy", "params": []})
    )
    assert req is not None
    outcome = dispatch_rpc(
        {"heavy": RpcRegistration(handler=heavy, run_in="worker")},
        req,
        submit_worker=submit,
    )
    assert isinstance(outcome, Future)
    assert outcome.result() == "ok"
    assert submitted


def test_dispatch_rpc_worker_awaits_handler_future() -> None:
    inner: Future[int] = Future()

    def submit(fn):  # noqa: ANN001
        fut: Future[object] = Future()
        fut.set_result(fn())
        return fut

    def returns_future() -> Future[int]:
        inner.set_result(99)
        return inner

    req = parse_rpc_request(
        json.dumps({"__tkwry": "rpc", "id": "1", "method": "f", "params": []})
    )
    assert req is not None
    outcome = dispatch_rpc(
        {"f": RpcRegistration(handler=returns_future, run_in="worker")},
        req,
        submit_worker=submit,
    )
    assert isinstance(outcome, Future)
    assert outcome.result() == 99


def test_dispatch_rpc_typeerror_on_arity_and_types() -> None:
    def add(a: int, b: int) -> int:
        return a + b

    missing = parse_rpc_request(
        json.dumps({"__tkwry": "rpc", "id": "1", "method": "add", "params": [1]})
    )
    assert missing is not None
    ok, value = dispatch_rpc({"add": add}, missing)
    assert ok is False
    assert value["type"] == "TypeError"
    assert "b" in value["message"] or "argument" in value["message"].lower()

    wrong = parse_rpc_request(
        json.dumps({"__tkwry": "rpc", "id": "2", "method": "add", "params": ["x", 2]})
    )
    assert wrong is not None
    ok, value = dispatch_rpc({"add": add}, wrong)
    assert ok is False
    assert value["type"] == "TypeError"

    flag = parse_rpc_request(
        json.dumps({"__tkwry": "rpc", "id": "3", "method": "add", "params": [True, 2]})
    )
    assert flag is not None
    ok, value = dispatch_rpc({"add": add}, flag)
    assert ok is False
    assert value["type"] == "TypeError"


def test_bind_rpc_arguments_coerces_integral_float() -> None:
    def add(a: int, b: int) -> int:
        return a + b

    args, kwargs = bind_rpc_arguments(add, (2.0, 3), {})
    assert args == (2, 3)
    assert kwargs == {}


def test_dispatch_rpc_kwargs() -> None:
    def greet(message: str, times: int = 1) -> str:
        return message * int(times)

    req = parse_rpc_request(
        json.dumps(
            {
                "__tkwry": "rpc",
                "id": "1",
                "method": "greet",
                "params": ["hi"],
                "kwargs": {"times": 3},
            }
        )
    )
    assert req is not None
    ok, value = dispatch_rpc({"greet": greet}, req)
    assert ok is True
    assert value == "hihihi"


def test_format_rpc_error() -> None:
    try:
        raise ValueError("x")
    except ValueError as exc:
        payload = format_rpc_error(exc, include_traceback=True)
    assert payload["type"] == "ValueError"
    assert payload["message"] == "x"
    assert "ValueError" in payload["traceback"]


def test_settle_and_emit_script() -> None:
    script = settle_script("r9", ok=True, value={"n": 1})
    assert '"r9"' in script
    assert "true" in script
    assert '{"n": 1}' in script or '{"n":1}' in script
    err = settle_script(
        "r9",
        ok=False,
        value={"type": "RuntimeError", "message": "fail"},
    )
    assert "false" in err
    assert "RuntimeError" in err
    emitted = emit_script("data_updated", {"n": 2})
    assert "_emit" in emitted
    assert "data_updated" in emitted


def test_bootstrap_includes_on_and_timeout() -> None:
    assert "window.tkwry.on" in RPC_BOOTSTRAP_JS
    assert "window.tkwry.invoke" in RPC_BOOTSTRAP_JS
    assert "timeout" in RPC_BOOTSTRAP_JS
    assert "kwargs" in RPC_BOOTSTRAP_JS
    assert "_emit" in RPC_BOOTSTRAP_JS
    assert "console.error" in RPC_BOOTSTRAP_JS
    assert "tkwry.debug" in RPC_BOOTSTRAP_JS
    assert "window.tkwry.cancel" in RPC_BOOTSTRAP_JS
    assert "window.tkwry.stream" in RPC_BOOTSTRAP_JS
    assert "_chunk" in RPC_BOOTSTRAP_JS
    assert "stream: true" in RPC_BOOTSTRAP_JS
    assert "version: 1" in RPC_BOOTSTRAP_JS
    assert "cancel: true" in RPC_BOOTSTRAP_JS
    assert "iter[Symbol.asyncIterator]" in RPC_BOOTSTRAP_JS
    assert '"return": function' in RPC_BOOTSTRAP_JS


def test_merge_initialization_script() -> None:
    assert merge_initialization_script(None, rpc_enabled=False) is None
    assert merge_initialization_script("void 0", rpc_enabled=False) == "void 0"
    merged = merge_initialization_script("void 0;", rpc_enabled=True)
    assert merged is not None
    assert RPC_BOOTSTRAP_JS in merged
    assert merged.endswith("void 0;")
    only = merge_initialization_script(None, rpc_enabled=True)
    assert only == RPC_BOOTSTRAP_JS


def test_parse_rpc_request_stream_flag() -> None:
    req = parse_rpc_request(
        json.dumps(
            {
                "__tkwry": "rpc",
                "version": 1,
                "id": "s1",
                "method": "ticks",
                "params": [3],
                "stream": True,
            }
        )
    )
    assert req is not None
    assert req.stream is True
    ignored = parse_rpc_request(
        json.dumps(
            {
                "__tkwry": "rpc",
                "id": "s2",
                "method": "ticks",
                "params": [],
                "stream": "yes",
            }
        )
    )
    assert ignored is not None
    assert ignored.stream is False


def test_stream_chunk_script() -> None:
    src = stream_chunk_script("r1", {"n": 2})
    assert "_chunk" in src
    assert "r1" in src
    assert "2" in src


def test_dispatch_rpc_stream_generator() -> None:
    def ticks() -> object:
        yield 1
        yield 2

    chunks: list[object] = []
    req = parse_rpc_request(
        json.dumps(
            {
                "__tkwry": "rpc",
                "id": "1",
                "method": "ticks",
                "params": [],
                "stream": True,
            }
        )
    )
    assert req is not None
    ok, value = dispatch_rpc(
        {"ticks": ticks},
        req,
        on_stream_chunk=chunks.append,
    )
    assert ok is True
    assert value is RPC_STREAM_DONE
    assert chunks == [1, 2]


def test_dispatch_rpc_generator_requires_stream() -> None:
    def ticks() -> object:
        yield 1

    req = parse_rpc_request(
        json.dumps({"__tkwry": "rpc", "id": "1", "method": "ticks", "params": []})
    )
    assert req is not None
    ok, value = dispatch_rpc({"ticks": ticks}, req)
    assert ok is False
    assert value["type"] == "TypeError"
    assert "stream" in value["message"]


def test_dispatch_rpc_stream_single_value() -> None:
    def ping() -> str:
        return "pong"

    chunks: list[object] = []
    req = parse_rpc_request(
        json.dumps(
            {
                "__tkwry": "rpc",
                "id": "1",
                "method": "ping",
                "params": [],
                "stream": True,
            }
        )
    )
    assert req is not None
    ok, value = dispatch_rpc(
        {"ping": ping},
        req,
        on_stream_chunk=chunks.append,
    )
    assert ok is True
    assert value is RPC_STREAM_DONE
    assert chunks == ["pong"]


def test_dispatch_rpc_rejects_async_generator() -> None:
    async def agen() -> object:
        yield 1

    def handler() -> object:
        return agen()

    req = parse_rpc_request(
        json.dumps(
            {
                "__tkwry": "rpc",
                "id": "1",
                "method": "agen",
                "params": [],
                "stream": True,
            }
        )
    )
    assert req is not None
    ok, value = dispatch_rpc(
        {"agen": handler},
        req,
        on_stream_chunk=lambda _item: None,
    )
    assert ok is False
    assert value["type"] == "TypeError"
    assert "async" in value["message"]


def test_dispatch_rpc_stream_chunk_too_large(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tkwry.ipc as ipc

    monkeypatch.setattr(ipc, "MAX_RPC_STREAM_CHUNK_BYTES", 16)

    def ticks() -> object:
        yield "x" * 80

    chunks: list[object] = []
    req = parse_rpc_request(
        json.dumps(
            {
                "__tkwry": "rpc",
                "id": "1",
                "method": "ticks",
                "params": [],
                "stream": True,
            }
        )
    )
    assert req is not None
    ok, value = dispatch_rpc(
        {"ticks": ticks},
        req,
        on_stream_chunk=chunks.append,
    )
    assert ok is False
    assert value["type"] == "RpcMessageTooLarge"
    assert chunks == []


def test_stream_chunk_script_rejects_oversized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tkwry.ipc as ipc

    monkeypatch.setattr(ipc, "MAX_RPC_STREAM_CHUNK_BYTES", 8)
    with pytest.raises(RpcMessageTooLarge, match="stream chunk"):
        stream_chunk_script("r1", "x" * 80)


def test_dispatch_rpc_stream_handler_error_rejects() -> None:
    """Chunks already sent stay sent; the iterator/Promise then rejects."""

    def ticks() -> object:
        yield 1
        raise RuntimeError("boom")

    chunks: list[object] = []
    req = parse_rpc_request(
        json.dumps(
            {
                "__tkwry": "rpc",
                "id": "1",
                "method": "ticks",
                "params": [],
                "stream": True,
            }
        )
    )
    assert req is not None
    ok, value = dispatch_rpc(
        {"ticks": ticks},
        req,
        on_stream_chunk=chunks.append,
    )
    assert ok is False
    assert value["type"] == "RuntimeError"
    assert value["message"] == "boom"
    assert chunks == [1]
