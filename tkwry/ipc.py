"""IPC events and thin JSON-RPC over ``window.ipc`` / ``window.tkwry``.

Roles:

- **IPC** — fire-and-forget JS → Python via ``window.ipc.postMessage`` /
  :meth:`~tkwry.WebView.set_ipc_handler` (raw string).
- **RPC** — request/response JS → Python via ``window.tkwry.call`` /
  ``window.tkwry.invoke`` / :meth:`~tkwry.WebView.expose` (Promise + JSON
  result or structured error).
- **Emit** — fire-and-forget Python → JS via :meth:`~tkwry.WebView.emit` /
  ``window.tkwry.on``.

RPC envelopes use ``{"__tkwry": "rpc", ...}`` (optional ``kwargs`` object)
and settle Promises with ``eval_js``. ``window.tkwry.stream`` is additive
(``stream: true`` on protocol ``version: 1``) and does not change ``call``.
Low-level IPC traffic is unchanged. RPC is queued separately from IPC so
event floods cannot drop calls.
"""

from __future__ import annotations

import inspect
import json
import math
import re
import threading
import traceback
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias, Union, get_args, get_origin, get_type_hints

from tkwry.exceptions import RpcSerializationError

RpcHandler: TypeAlias = Callable[..., Any]
RpcRunIn: TypeAlias = Literal["main", "worker"]

RPC_MARKER = "rpc"
RPC_KEY = "__tkwry"
RPC_REJECT_KEY = "__tkwry_reject"
RPC_VERSION = 1

# Keep in sync with ``MAX_IPC_MESSAGE_BYTES`` / ``MAX_RPC_MESSAGE_BYTES`` in src/lib.rs.
MAX_RPC_MESSAGE_BYTES = 10 * 1024 * 1024
MAX_IPC_MESSAGE_BYTES = MAX_RPC_MESSAGE_BYTES
# Stream chunks reuse the envelope cap (eval_js outbound, not the native inbound queue).
MAX_RPC_STREAM_CHUNK_BYTES = MAX_RPC_MESSAGE_BYTES
MAX_RPC_ARGS = 256
MAX_RPC_KWARGS = 256
_RPC_ID_SCAN_CHARS = 8192
_RPC_ID_RE = re.compile(r'"id"\s*:\s*"((?:\\.|[^"\\])*)"')

_rpc_tls = threading.local()

# Sentinel: handler already emitted stream chunks; settle with JSON null.
RPC_STREAM_DONE = object()

# Injected before user initialization_script / via eval_js after create.
RPC_BOOTSTRAP_JS = """\
(function () {
  if (window.tkwry && window.tkwry.call && window.tkwry.invoke
      && window.tkwry.stream && window.tkwry.on) return;
  var epoch = 0;
  var seq = 0;
  var pending = Object.create(null);
  var listeners = Object.create(null);
  function nextRpcId() {
    return String(epoch) + ":r" + String(++seq);
  }
  function rpcEpochOf(id) {
    id = String(id || "");
    var colon = id.indexOf(":");
    if (colon < 0) return null;
    return id.slice(0, colon);
  }
  function rejectAllPending(reason) {
    for (var key in pending) {
      if (!Object.prototype.hasOwnProperty.call(pending, key)) continue;
      var slot = pending[key];
      if (slot && slot.finish) slot.finish(false, reason);
    }
    pending = Object.create(null);
  }
  function isCallOptions(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    var keys = Object.keys(value);
    if (!keys.length) return false;
    var hasTimeout = false;
    var hasKwargs = false;
    for (var i = 0; i < keys.length; i++) {
      var key = keys[i];
      if (key === "timeout") {
        if (typeof value.timeout !== "number") return false;
        if (!Number.isFinite(value.timeout) || value.timeout <= 0) return false;
        hasTimeout = true;
        continue;
      }
      if (key === "kwargs") {
        if (!value.kwargs || typeof value.kwargs !== "object"
            || Array.isArray(value.kwargs)) {
          return false;
        }
        hasKwargs = true;
        continue;
      }
      return false;
    }
    return hasTimeout || hasKwargs;
  }
  function makeError(value) {
    if (value && typeof value === "object" && value.message != null) {
      var err = new Error(String(value.message));
      err.name = String(value.type || "RpcError");
      if (value.traceback) err.traceback = value.traceback;
      err.rpc = value;
      return err;
    }
    return new Error(String(value || "rpc error"));
  }
  window.tkwry = {
    call: function (method) {
      var params = Array.prototype.slice.call(arguments, 1);
      var options = null;
      if (params.length && isCallOptions(params[params.length - 1])) {
        options = params.pop();
      }
      var id = nextRpcId();
      var promise = new Promise(function (resolve, reject) {
        var settled = false;
        var timer = null;
        function finish(ok, value) {
          if (settled) return;
          settled = true;
          if (timer !== null) clearTimeout(timer);
          delete pending[id];
          if (ok) resolve(value);
          else reject(makeError(value));
        }
        pending[id] = { finish: finish };
        if (options && options.timeout > 0) {
          timer = setTimeout(function () {
            finish(false, {
              type: "RpcTimeoutError",
              message: "rpc timeout after " + options.timeout + "ms"
            });
          }, options.timeout);
        }
        if (!window.ipc || !window.ipc.postMessage) {
          finish(false, {
            type: "RpcTransportError",
            message: "window.ipc.postMessage unavailable"
          });
          return;
        }
        var payload = {
          __tkwry: "rpc",
          version: 1,
          id: id,
          method: String(method),
          params: params
        };
        if (options && options.kwargs && Object.keys(options.kwargs).length) {
          payload.kwargs = options.kwargs;
        }
        window.ipc.postMessage(JSON.stringify(payload));
      });
      promise.id = id;
      promise.cancel = function () { window.tkwry.cancel(id); };
      return promise;
    },
    invoke: function (method, data, options) {
      if (arguments.length <= 1) {
        return window.tkwry.call(method);
      }
      if (arguments.length === 2) {
        if (data === undefined || data === null) {
          return window.tkwry.call(method);
        }
        if (isCallOptions(data)) {
          return window.tkwry.call(method, data);
        }
        if (typeof data === "object" && !Array.isArray(data)) {
          return window.tkwry.call(method, { kwargs: data });
        }
        return window.tkwry.call(method, data);
      }
      var opts = (options && typeof options === "object" && !Array.isArray(options))
        ? Object.assign({}, options) : {};
      if (data !== undefined && data !== null) {
        if (typeof data === "object" && !Array.isArray(data)) {
          opts.kwargs = data;
          return window.tkwry.call(method, opts);
        }
        return window.tkwry.call(method, data, options);
      }
      if (options !== undefined) {
        return window.tkwry.call(method, options);
      }
      return window.tkwry.call(method);
    },
    stream: function (method) {
      var params = Array.prototype.slice.call(arguments, 1);
      var options = null;
      if (params.length && isCallOptions(params[params.length - 1])) {
        options = params.pop();
      }
      var id = nextRpcId();
      var queue = [];
      var waiting = null;
      var finished = false;
      var error = null;
      var timer = null;
      function finish(ok, value) {
        if (finished) return;
        finished = true;
        if (timer !== null) clearTimeout(timer);
        delete pending[id];
        if (!ok) {
          error = makeError(value);
          queue.length = 0;
        }
        if (waiting) {
          var w = waiting;
          waiting = null;
          if (!ok) w.reject(error);
          else w.resolve({ done: true, value: undefined });
        }
      }
      pending[id] = {
        finish: finish,
        chunk: function (value) {
          if (finished) return;
          if (waiting) {
            var w = waiting;
            waiting = null;
            w.resolve({ done: false, value: value });
          } else {
            queue.push(value);
          }
        }
      };
      if (options && options.timeout > 0) {
        timer = setTimeout(function () {
          finish(false, {
            type: "RpcTimeoutError",
            message: "rpc timeout after " + options.timeout + "ms"
          });
        }, options.timeout);
      }
      if (!window.ipc || !window.ipc.postMessage) {
        finish(false, {
          type: "RpcTransportError",
          message: "window.ipc.postMessage unavailable"
        });
      } else {
        var payload = {
          __tkwry: "rpc",
          version: 1,
          id: id,
          method: String(method),
          params: params,
          stream: true
        };
        if (options && options.kwargs && Object.keys(options.kwargs).length) {
          payload.kwargs = options.kwargs;
        }
        window.ipc.postMessage(JSON.stringify(payload));
      }
      var iter = {
        id: id,
        cancel: function () { window.tkwry.cancel(id); },
        next: function () {
          if (finished) {
            if (error) return Promise.reject(error);
            if (queue.length) {
              return Promise.resolve({ done: false, value: queue.shift() });
            }
            return Promise.resolve({ done: true, value: undefined });
          }
          if (queue.length) {
            return Promise.resolve({ done: false, value: queue.shift() });
          }
          return new Promise(function (resolve, reject) {
            waiting = { resolve: resolve, reject: reject };
          });
        },
        "return": function (value) {
          if (!finished) {
            finished = true;
            if (timer !== null) clearTimeout(timer);
            delete pending[id];
            if (waiting) {
              var w = waiting;
              waiting = null;
              w.resolve({ done: true, value: value });
            }
            if (window.ipc && window.ipc.postMessage) {
              window.ipc.postMessage(JSON.stringify({
                __tkwry: "rpc",
                version: 1,
                id: id,
                cancel: true
              }));
            }
          }
          return Promise.resolve({ done: true, value: value });
        }
      };
      if (typeof Symbol === "function" && Symbol.asyncIterator) {
        iter[Symbol.asyncIterator] = function () { return iter; };
      }
      return iter;
    },
    cancel: function (id) {
      id = String(id || "");
      if (!id) return;
      var idEpoch = rpcEpochOf(id);
      if (idEpoch !== null && String(epoch) !== idEpoch) return;
      var slot = pending[id];
      if (slot) {
        slot.finish(false, {
          type: "RpcCancelledError",
          message: "rpc cancelled"
        });
      }
      if (!window.ipc || !window.ipc.postMessage) return;
      window.ipc.postMessage(JSON.stringify({
        __tkwry: "rpc",
        version: 1,
        id: id,
        cancel: true
      }));
    },
    on: function (event, handler) {
      var key = String(event);
      if (!listeners[key]) listeners[key] = [];
      listeners[key].push(handler);
      return function () {
        window.tkwry.off(key, handler);
      };
    },
    off: function (event, handler) {
      var key = String(event);
      var list = listeners[key];
      if (!list) return;
      if (!handler) {
        delete listeners[key];
        return;
      }
      listeners[key] = list.filter(function (h) { return h !== handler; });
    },
    debug: true,
    _emit: function (event, payload) {
      var list = listeners[String(event)] || [];
      for (var i = 0; i < list.length; i++) {
        try {
          list[i](payload);
        } catch (e) {
          if (window.tkwry.debug !== false
              && typeof console !== "undefined" && console.error) {
            console.error("tkwry.emit listener error (" + event + "):", e);
          }
        }
      }
    },
    _chunk: function (id, value) {
      id = String(id || "");
      var idEpoch = rpcEpochOf(id);
      if (idEpoch !== null && String(epoch) !== idEpoch) return;
      var slot = pending[id];
      if (!slot || !slot.chunk) return;
      slot.chunk(value);
    },
    _settle: function (id, ok, value) {
      id = String(id || "");
      if (!id) return;
      var idEpoch = rpcEpochOf(id);
      if (idEpoch !== null && String(epoch) !== idEpoch) return;
      var slot = pending[id];
      if (!slot) return;
      slot.finish(!!ok, value);
    },
    _bumpEpoch: function (nextEpoch) {
      epoch = Number(nextEpoch) || 0;
      seq = 0;
      rejectAllPending({
        type: "RpcCancelledError",
        message: "rpc cancelled by navigation"
      });
    }
  };
})();
"""


def rpc_cancelled() -> bool:
    """Return whether the current RPC call has been timed out or aborted.

    Worker handlers should poll this during long work. Timeout and
    :meth:`~tkwry.WebView.destroy` set the flag. Cancellation is
    **cooperative only**: Python cannot kill a running worker thread.
    Destroy waits up to ~2 seconds for pool threads to exit.
    """
    event = getattr(_rpc_tls, "cancel_event", None)
    return isinstance(event, threading.Event) and event.is_set()


def rpc_cancel_event() -> threading.Event | None:
    """Return the cancellation event for the current RPC, or ``None``.

    Capture this inside a handler if background work runs on another thread
    that should observe :func:`rpc_cancelled`.
    """
    event = getattr(_rpc_tls, "cancel_event", None)
    if isinstance(event, threading.Event):
        return event
    return None


def bind_rpc_cancel_event(event: threading.Event | None) -> None:
    """Associate *event* with this thread for :func:`rpc_cancelled` (internal)."""
    _rpc_tls.cancel_event = event


class RpcMessageTooLarge(ValueError):
    """JSON payload exceeds :data:`MAX_RPC_MESSAGE_BYTES` (stream chunks)."""


def dumps_rpc_json(value: Any) -> str:
    """Serialize *value* with strict JSON (no ``default=str``, no NaN/Inf)."""
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RpcSerializationError("value is not JSON-serializable") from exc


def dumps_rpc_stream_chunk(value: Any) -> str:
    """Serialize one stream chunk and reject payloads over the chunk cap."""
    payload = dumps_rpc_json(value)
    size = len(payload.encode("utf-8"))
    if size > MAX_RPC_STREAM_CHUNK_BYTES:
        raise RpcMessageTooLarge(
            f"RPC stream chunk exceeds {MAX_RPC_STREAM_CHUNK_BYTES} byte limit "
            f"({size} bytes)"
        )
    return payload


def _extract_rpc_request_id(message: str) -> str | None:
    match = _RPC_ID_RE.search(message[:_RPC_ID_SCAN_CHARS])
    if match is None:
        return None
    try:
        loaded = json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        return None
    if isinstance(loaded, str) and loaded:
        return loaded
    return None


def _message_size(message: str) -> int | None:
    try:
        return len(message.encode("utf-8"))
    except UnicodeEncodeError:
        return None


@dataclass(frozen=True, slots=True)
class RpcRequest:
    id: str
    method: str
    params: tuple[Any, ...]
    kwargs: dict[str, Any] = field(default_factory=dict)
    reject: dict[str, str] | None = None
    cancel: bool = False
    stream: bool = False


@dataclass(frozen=True, slots=True)
class RpcRegistration:
    """Registered RPC method metadata."""

    handler: RpcHandler
    run_in: RpcRunIn = "main"
    timeout: float | None = None
    allow_any_origin: bool = False


def validate_rpc_timeout(timeout: float | None) -> None:
    """Raise :class:`ValueError` if *timeout* is set but not finite and positive."""
    if timeout is None:
        return
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("expose: timeout must be a finite positive number when set")
    if timeout <= 0 or not math.isfinite(timeout):
        raise ValueError("expose: timeout must be a finite positive number when set")


def is_rpc_envelope(data: object) -> bool:
    return isinstance(data, Mapping) and data.get(RPC_KEY) == RPC_MARKER


def parse_rpc_request(message: str) -> RpcRequest | None:
    """Return an :class:`RpcRequest` if *message* is a tkwry RPC envelope."""
    size = _message_size(message)
    if size is None:
        return None
    if size > MAX_RPC_MESSAGE_BYTES:
        req_id = _extract_rpc_request_id(message)
        if not req_id:
            return None
        return RpcRequest(
            id=req_id,
            method="",
            params=(),
            reject=rpc_error(
                "RpcMessageTooLarge",
                f"RPC message exceeds {MAX_RPC_MESSAGE_BYTES} byte limit "
                f"({size} bytes)",
            ),
        )
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return None
    if not is_rpc_envelope(data):
        return None
    req_id = data.get("id")
    if not isinstance(req_id, str) or not req_id:
        return None
    version = data.get("version", RPC_VERSION)
    if version is None:
        version = RPC_VERSION
    if not isinstance(version, int) or isinstance(version, bool):
        return RpcRequest(
            id=req_id,
            method="",
            params=(),
            reject=rpc_error(
                "RpcProtocolError",
                f"invalid RPC version: {version!r}",
            ),
        )
    if version != RPC_VERSION:
        return RpcRequest(
            id=req_id,
            method="",
            params=(),
            reject=rpc_error(
                "RpcProtocolError",
                f"unsupported RPC version: {version} (expected {RPC_VERSION})",
            ),
        )
    if data.get("cancel") is True:
        return RpcRequest(id=req_id, method="", params=(), cancel=True)
    reject_type = data.get(RPC_REJECT_KEY)
    if isinstance(reject_type, str) and reject_type:
        message_text = data.get("message")
        if not isinstance(message_text, str) or not message_text:
            message_text = reject_type
        return RpcRequest(
            id=req_id,
            method="",
            params=(),
            reject=rpc_error(reject_type, message_text),
        )
    method = data.get("method")
    if not isinstance(method, str) or not method:
        return None
    params = data.get("params", [])
    if params is None:
        params = []
    if not isinstance(params, Sequence) or isinstance(params, (str, bytes, bytearray)):
        return None
    kwargs_raw = data.get("kwargs")
    if kwargs_raw is None:
        kwargs: dict[str, Any] = {}
    elif isinstance(kwargs_raw, Mapping) and not isinstance(
        kwargs_raw, (str, bytes, bytearray)
    ):
        if not all(isinstance(key, str) for key in kwargs_raw):
            return None
        kwargs = dict(kwargs_raw)
    else:
        return None
    if len(params) > MAX_RPC_ARGS:
        return RpcRequest(
            id=req_id,
            method=method,
            params=(),
            reject=rpc_error(
                "RpcArgumentLimitError",
                f"too many positional arguments ({len(params)} > {MAX_RPC_ARGS})",
            ),
        )
    if len(kwargs) > MAX_RPC_KWARGS:
        return RpcRequest(
            id=req_id,
            method=method,
            params=(),
            reject=rpc_error(
                "RpcArgumentLimitError",
                f"too many keyword arguments ({len(kwargs)} > {MAX_RPC_KWARGS})",
            ),
        )
    return RpcRequest(
        id=req_id,
        method=method,
        params=tuple(params),
        kwargs=kwargs,
        stream=data.get("stream") is True,
    )


def format_rpc_error(
    exc: BaseException,
    *,
    include_traceback: bool = False,
) -> dict[str, str]:
    """Build a structured RPC error payload for Promise rejection."""
    payload: dict[str, str] = {
        "type": type(exc).__name__,
        "message": str(exc) or type(exc).__name__,
    }
    if include_traceback:
        payload["traceback"] = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
    return payload


def rpc_error(type_name: str, message: str) -> dict[str, str]:
    """Build a structured error without a live exception."""
    return {"type": type_name, "message": message}


def rpc_id_epoch(req_id: str) -> int | None:
    """Return the navigation epoch prefix of an RPC id, or ``None`` if absent."""
    if ":" not in req_id:
        return None
    prefix, _, rest = req_id.partition(":")
    if not rest:
        return None
    try:
        return int(prefix)
    except ValueError:
        return None


def rpc_bump_epoch_script(epoch: int) -> str:
    """Build ``eval_js`` source that advances the in-page RPC epoch."""
    return (
        "window.tkwry && window.tkwry._bumpEpoch && "
        f"window.tkwry._bumpEpoch({int(epoch)});"
    )


def settle_script(req_id: str, *, ok: bool, value: Any) -> str:
    """Build ``eval_js`` source that resolves/rejects a pending Promise.

    Raises :class:`~tkwry.RpcSerializationError` if *value* is not JSON.
    """
    payload = dumps_rpc_json(value)
    return (
        "window.tkwry && window.tkwry._settle("
        f"{json.dumps(req_id)}, {json.dumps(ok)}, {payload});"
    )


def stream_chunk_script(req_id: str, value: Any) -> str:
    """Build ``eval_js`` source that delivers one streaming RPC chunk.

    Raises :class:`~tkwry.RpcSerializationError` if *value* is not JSON, or
    :class:`RpcMessageTooLarge` if the JSON exceeds
    :data:`MAX_RPC_STREAM_CHUNK_BYTES`.
    """
    payload = dumps_rpc_stream_chunk(value)
    return f"window.tkwry && window.tkwry._chunk({json.dumps(req_id)}, {payload});"


def finalize_rpc_result(
    result: Any,
    *,
    stream: bool,
    on_stream_chunk: Callable[[Any], None] | None = None,
) -> Any:
    """Consume a handler return value, emitting stream chunks when needed.

    Sync generators require ``stream=True`` (``window.tkwry.stream``).
    Async generators are rejected. Each chunk JSON is capped at
    :data:`MAX_RPC_STREAM_CHUNK_BYTES` (same 10 MiB as the RPC envelope).
    Returns :data:`RPC_STREAM_DONE` after a stream has been fully emitted so
    the caller can settle with ``null``. Handler exceptions propagate so the
    Promise / iterator rejects — there is no second error channel.
    """
    if inspect.isasyncgen(result):
        raise TypeError("async generators are not supported; use a sync generator")
    if inspect.isgenerator(result):
        if not stream:
            result.close()
            raise TypeError("generator requires window.tkwry.stream()")
        if on_stream_chunk is None:
            result.close()
            raise TypeError("stream RPC requires on_stream_chunk")
        try:
            for item in result:
                if rpc_cancelled():
                    break
                dumps_rpc_stream_chunk(item)
                on_stream_chunk(item)
        finally:
            result.close()
        return RPC_STREAM_DONE
    if stream:
        if on_stream_chunk is None:
            raise TypeError("stream RPC requires on_stream_chunk")
        dumps_rpc_stream_chunk(result)
        on_stream_chunk(result)
        return RPC_STREAM_DONE
    dumps_rpc_json(result)
    return result


def emit_script(event: str, data: Any = None) -> str:
    """Build ``eval_js`` source that delivers a Python→JS event.

    Raises :class:`~tkwry.RpcSerializationError` if *data* is not JSON.
    """
    payload = dumps_rpc_json(data)
    return f"window.tkwry && window.tkwry._emit({json.dumps(event)}, {payload});"


def _normalize_registration(
    entry: RpcRegistration | RpcHandler,
) -> RpcRegistration:
    if isinstance(entry, RpcRegistration):
        return entry
    return RpcRegistration(handler=entry)


def _annotation_origin(hint: Any) -> Any:
    origin = get_origin(hint)
    return origin if origin is not None else hint


def _coerce_rpc_value(value: Any, hint: Any) -> Any:
    """Coerce a JSON value to a simple annotation; pass through unknown hints."""
    if hint is Any or hint is inspect.Parameter.empty:
        return value
    origin = get_origin(hint)
    args = get_args(hint)
    if origin is Union or str(origin) == "typing.Union":
        non_none = [item for item in args if item is not type(None)]
        if type(None) in args and value is None:
            return None
        errors: list[str] = []
        for item in non_none:
            try:
                return _coerce_rpc_value(value, item)
            except TypeError as exc:
                errors.append(str(exc))
        raise TypeError(errors[-1] if errors else f"expected {hint!r}")
    target = _annotation_origin(hint)
    if target is bool:
        if isinstance(value, bool):
            return value
        raise TypeError(f"expected bool, got {type(value).__name__}")
    if target is int:
        if isinstance(value, bool):
            raise TypeError("expected int, got bool")
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        raise TypeError(f"expected int, got {type(value).__name__}")
    if target is float:
        if isinstance(value, bool):
            raise TypeError("expected float, got bool")
        if isinstance(value, (int, float)):
            return float(value)
        raise TypeError(f"expected float, got {type(value).__name__}")
    if target is str:
        if isinstance(value, str):
            return value
        raise TypeError(f"expected str, got {type(value).__name__}")
    if target is dict:
        if isinstance(value, dict):
            return value
        raise TypeError(f"expected dict, got {type(value).__name__}")
    if target is list:
        if isinstance(value, list):
            return value
        raise TypeError(f"expected list, got {type(value).__name__}")
    return value


def bind_rpc_arguments(
    handler: RpcHandler,
    params: Sequence[Any],
    kwargs: Mapping[str, Any],
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Bind RPC args to *handler* and coerce simple annotations.

    Raises ``TypeError`` (stable ``type`` name for JS) on arity / type mismatch.
    """
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return tuple(params), dict(kwargs)
    try:
        bound = signature.bind(*params, **kwargs)
        bound.apply_defaults()
    except TypeError as exc:
        raise TypeError(str(exc) or "invalid RPC arguments") from exc
    try:
        hints = get_type_hints(handler)
    except Exception:
        hints = {}
    for name, parameter in signature.parameters.items():
        if name not in bound.arguments:
            continue
        hint = hints.get(name, parameter.annotation)
        try:
            bound.arguments[name] = _coerce_rpc_value(bound.arguments[name], hint)
        except TypeError as exc:
            raise TypeError(f"{name}: {exc}") from exc
    return bound.args, dict(bound.kwargs)


def _resolve_handler_future(result: Any) -> Any:
    """Block on a handler-returned ``Future`` (worker-thread path only)."""
    if isinstance(result, Future):
        return result.result()
    return result


def dispatch_rpc(
    methods: Mapping[str, RpcRegistration | RpcHandler],
    request: RpcRequest,
    *,
    submit_worker: Callable[[Callable[[], Any]], Future[Any]] | None = None,
    include_traceback: bool = False,
    on_stream_chunk: Callable[[Any], None] | None = None,
) -> tuple[bool, Any] | Future[Any]:
    """Run *request* against *methods*.

    Returns either:

    - ``(ok, result_or_error_payload)`` for an immediate settle, or
    - a :class:`~concurrent.futures.Future` that the caller must settle later

    Handlers registered with ``run_in="worker"`` are submitted via
    *submit_worker* (required). If such a handler returns a ``Future``, it is
    awaited on the worker thread before the RPC is settled. On the main thread,
    a handler ``Future`` is passed through for the caller to track. Default
    ``run_in="main"`` runs on the caller thread (Tk main thread in the WebView).
    """
    if request.reject is not None:
        return False, request.reject
    if request.cancel:
        return False, rpc_error("RpcCancelledError", "rpc cancelled")
    entry = methods.get(request.method)
    if entry is None:
        return False, rpc_error(
            "RpcMethodNotFound", f"unknown method: {request.method}"
        )
    reg = _normalize_registration(entry)

    def invoke() -> Any:
        try:
            args, kwargs = bind_rpc_arguments(
                reg.handler, request.params, request.kwargs
            )
        except TypeError as exc:
            raise TypeError(str(exc) or "invalid RPC arguments") from exc
        result = reg.handler(*args, **kwargs)
        if reg.run_in == "worker":
            result = _resolve_handler_future(result)
            return finalize_rpc_result(
                result,
                stream=request.stream,
                on_stream_chunk=on_stream_chunk,
            )
        if isinstance(result, Future):
            return result
        return finalize_rpc_result(
            result,
            stream=request.stream,
            on_stream_chunk=on_stream_chunk,
        )

    if reg.run_in == "worker":
        if submit_worker is None:
            return False, rpc_error(
                "RpcConfigError",
                f"method {request.method!r} requires a worker executor",
            )
        try:
            return submit_worker(invoke)
        except Exception as exc:
            return False, format_rpc_error(exc, include_traceback=include_traceback)

    try:
        result = invoke()
    except Exception as exc:
        return False, format_rpc_error(exc, include_traceback=include_traceback)
    if isinstance(result, Future):
        return result
    return True, result


def merge_initialization_script(
    user_script: str | None, *, rpc_enabled: bool
) -> str | None:
    """Prepend the tkwry bridge bootstrap when RPC/emit is enabled."""
    if not rpc_enabled:
        return user_script
    if user_script:
        return f"{RPC_BOOTSTRAP_JS}\n{user_script}"
    return RPC_BOOTSTRAP_JS
