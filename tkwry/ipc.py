"""IPC events and thin JSON-RPC over ``window.ipc`` / ``window.tkwry``.

Roles:

- **IPC** — fire-and-forget JS → Python via ``window.ipc.postMessage`` /
  :meth:`~tkwry.WebView.set_ipc_handler` (raw string).
- **RPC** — request/response JS → Python via ``window.tkwry.call`` /
  :meth:`~tkwry.WebView.expose` (Promise + JSON result or structured error).
- **Emit** — fire-and-forget Python → JS via :meth:`~tkwry.WebView.emit` /
  ``window.tkwry.on``.

RPC envelopes use ``{"__tkwry": "rpc", ...}`` (optional ``kwargs`` object)
and settle Promises with ``eval_js``. Low-level IPC traffic is unchanged.
RPC is queued separately from IPC so event floods cannot drop calls.
"""

from __future__ import annotations

import json
import traceback
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

RpcHandler: TypeAlias = Callable[..., Any]
RpcRunIn: TypeAlias = Literal["main", "worker"]

RPC_MARKER = "rpc"
RPC_KEY = "__tkwry"

# Injected before user initialization_script / via eval_js after create.
RPC_BOOTSTRAP_JS = """\
(function () {
  if (window.tkwry && window.tkwry.call && window.tkwry.on) return;
  var seq = 0;
  var pending = Object.create(null);
  var listeners = Object.create(null);
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
      var id = "r" + String(++seq);
      return new Promise(function (resolve, reject) {
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
          id: id,
          method: String(method),
          params: params
        };
        if (options && options.kwargs && Object.keys(options.kwargs).length) {
          payload.kwargs = options.kwargs;
        }
        window.ipc.postMessage(JSON.stringify(payload));
      });
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
    _emit: function (event, payload) {
      var list = listeners[String(event)] || [];
      for (var i = 0; i < list.length; i++) {
        try { list[i](payload); } catch (e) {}
      }
    },
    _settle: function (id, ok, value) {
      var slot = pending[id];
      if (!slot) return;
      slot.finish(!!ok, value);
    }
  };
})();
"""


@dataclass(frozen=True, slots=True)
class RpcRequest:
    id: str
    method: str
    params: tuple[Any, ...]
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RpcRegistration:
    """Registered RPC method metadata."""

    handler: RpcHandler
    run_in: RpcRunIn = "main"
    timeout: float | None = None


def is_rpc_envelope(data: object) -> bool:
    return isinstance(data, Mapping) and data.get(RPC_KEY) == RPC_MARKER


def parse_rpc_request(message: str) -> RpcRequest | None:
    """Return an :class:`RpcRequest` if *message* is a tkwry RPC envelope."""
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return None
    if not is_rpc_envelope(data):
        return None
    req_id = data.get("id")
    method = data.get("method")
    if not isinstance(req_id, str) or not req_id:
        return None
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
    return RpcRequest(id=req_id, method=method, params=tuple(params), kwargs=kwargs)


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


def settle_script(req_id: str, *, ok: bool, value: Any) -> str:
    """Build ``eval_js`` source that resolves/rejects a pending Promise."""
    payload = json.dumps(value, ensure_ascii=False, default=str)
    return (
        "window.tkwry && window.tkwry._settle("
        f"{json.dumps(req_id)}, {json.dumps(ok)}, {payload});"
    )


def emit_script(event: str, data: Any = None) -> str:
    """Build ``eval_js`` source that delivers a Python→JS event."""
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"window.tkwry && window.tkwry._emit({json.dumps(event)}, {payload});"


def _normalize_registration(
    entry: RpcRegistration | RpcHandler,
) -> RpcRegistration:
    if isinstance(entry, RpcRegistration):
        return entry
    return RpcRegistration(handler=entry)


def dispatch_rpc(
    methods: Mapping[str, RpcRegistration | RpcHandler],
    request: RpcRequest,
    *,
    submit_worker: Callable[[Callable[[], Any]], Future[Any]] | None = None,
    include_traceback: bool = False,
) -> tuple[bool, Any] | Future[Any]:
    """Run *request* against *methods*.

    Returns either:

    - ``(ok, result_or_error_payload)`` for an immediate settle, or
    - a :class:`~concurrent.futures.Future` that the caller must settle later

    Handlers registered with ``run_in="worker"`` are submitted via
    *submit_worker* (required). Handlers that return a ``Future`` themselves
    are passed through. Default ``run_in="main"`` runs on the caller thread
    (Tk main thread in the WebView).
    """
    entry = methods.get(request.method)
    if entry is None:
        return False, rpc_error(
            "RpcMethodNotFound", f"unknown method: {request.method}"
        )
    reg = _normalize_registration(entry)

    def invoke() -> Any:
        return reg.handler(*request.params, **request.kwargs)

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
    try:
        json.dumps(result)
    except (TypeError, ValueError):
        return False, rpc_error(
            "RpcSerializationError",
            "rpc result is not JSON-serializable",
        )
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
