"""Thin JSON-RPC over ``window.ipc.postMessage`` / ``window.tkwry.call``.

Keeps the low-level ``ipc_handler`` path intact. RPC envelopes are detected by
``{"__tkwry": "rpc", ...}`` and settled back into JS Promises via ``eval_js``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeAlias

RpcHandler: TypeAlias = Callable[..., Any]

RPC_MARKER = "rpc"
RPC_KEY = "__tkwry"

# Injected before user initialization_script / via eval_js after create.
RPC_BOOTSTRAP_JS = """\
(function () {
  if (window.tkwry && window.tkwry.call) return;
  var seq = 0;
  var pending = Object.create(null);
  window.tkwry = {
    call: function (method) {
      var params = Array.prototype.slice.call(arguments, 1);
      var id = "r" + String(++seq);
      return new Promise(function (resolve, reject) {
        pending[id] = { resolve: resolve, reject: reject };
        if (!window.ipc || !window.ipc.postMessage) {
          delete pending[id];
          reject(new Error("window.ipc.postMessage unavailable"));
          return;
        }
        window.ipc.postMessage(JSON.stringify({
          __tkwry: "rpc",
          id: id,
          method: String(method),
          params: params
        }));
      });
    },
    _settle: function (id, ok, value) {
      var slot = pending[id];
      if (!slot) return;
      delete pending[id];
      if (ok) slot.resolve(value);
      else slot.reject(new Error(String(value || "rpc error")));
    }
  };
})();
"""


@dataclass(frozen=True, slots=True)
class RpcRequest:
    id: str
    method: str
    params: tuple[Any, ...]


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
    return RpcRequest(id=req_id, method=method, params=tuple(params))


def settle_script(req_id: str, *, ok: bool, value: Any) -> str:
    """Build ``eval_js`` source that resolves/rejects a pending Promise."""
    payload = json.dumps(value, ensure_ascii=False, default=str)
    return (
        "window.tkwry && window.tkwry._settle("
        f"{json.dumps(req_id)}, {json.dumps(ok)}, {payload});"
    )


def dispatch_rpc(
    methods: Mapping[str, RpcHandler],
    request: RpcRequest,
) -> tuple[bool, Any] | Any:
    """Run *request* against *methods*.

    Returns either:

    - ``(ok, result_or_error_message)`` for an immediate settle, or
    - a :class:`~concurrent.futures.Future` that the caller must settle later
    """
    from concurrent.futures import Future

    handler = methods.get(request.method)
    if handler is None:
        return False, f"unknown method: {request.method}"
    try:
        result = handler(*request.params)
    except Exception as exc:
        return False, str(exc) or type(exc).__name__
    if isinstance(result, Future):
        return result
    try:
        json.dumps(result)
    except (TypeError, ValueError):
        return False, "rpc result is not JSON-serializable"
    return True, result


def merge_initialization_script(
    user_script: str | None, *, rpc_enabled: bool
) -> str | None:
    """Prepend the RPC bootstrap when RPC is enabled."""
    if not rpc_enabled:
        return user_script
    if user_script:
        return f"{RPC_BOOTSTRAP_JS}\n{user_script}"
    return RPC_BOOTSTRAP_JS
