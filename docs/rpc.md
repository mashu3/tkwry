# IPC / RPC / emit

Use **IPC** for fire-and-forget events and **RPC** for request/response.
Landing snippets live in [Usage](usage.md); this page is the
execution model, cancel contract, streaming, and limits.

| Direction | Role | Python | JavaScript |
|-----------|------|--------|------------|
| JS → Python | IPC (event) | `set_ipc_handler` / `ipc_handler=` | `window.ipc.postMessage(str)` |
| JS → Python | RPC (call) | `@web.expose` | `await window.tkwry.call(name, ...)` |
| JS → Python | RPC (stream) | sync generator `@web.expose` | `for await (const x of window.tkwry.stream(name, ...))` |
| Python → JS | Emit (event) | `web.emit(event, data)` | `window.tkwry.on(event, handler)` |

These APIs run with **desktop-app privileges**. By default only the initial
page origin may use them — foreign RPC rejects with `RpcOriginError`;
foreign IPC is dropped. See [Trust boundaries](trust.md).

## IPC (`window.ipc.postMessage`)

Keep raw `ipc_handler` + `window.ipc.postMessage` for free-form events:

```python
def on_message(msg: str) -> None:
    print("from JS:", msg)

web = WebView(
    frame,
    html='<button onclick="window.ipc.postMessage(\'hi\')">send</button>',
    ipc_handler=on_message,
)
```

## RPC (`expose` / `window.tkwry.call`)

Expose callables and await them from JS:

```python
web = WebView(frame, html=HTML)

@web.expose
def greet(name: str) -> str:
    return f"hello {name}"

# Heavy I/O / CPU — run off the Tk thread so the UI stays responsive
@web.expose(thread=True, timeout=30.0)
def heavy_task(data: dict) -> dict:
    from tkwry import rpc_cancelled

    ...
    if rpc_cancelled():
        return {"status": "cancelled"}
    return result
```

```js
const text = await window.tkwry.call("greet", "Ada");
// optional JS-side timeout (ms) and Python kwargs:
await window.tkwry.call("heavy_task", payload, {
  timeout: 5000,
  kwargs: { verbose: true },
});
const pending = window.tkwry.call("heavy_task", payload);
// pending.id / pending.cancel() / window.tkwry.cancel(pending.id)
pending.cancel();
```

### Execution model

Default handlers run on the **Tk main thread** (safe for Tk APIs; long work
blocks the UI). Pass `thread=True` / `run_in="worker"` to use a background
pool. Handlers may also return a `concurrent.futures.Future`. Return values
and `emit` payloads must be strict JSON (no `datetime`, custom objects,
`NaN` / `Infinity`) — otherwise the Promise rejects / `emit` raises
`RpcSerializationError`. Errors reject the Promise with a structured payload
(`error.name` / `error.message`; set `rpc_traceback=True` or
`TKWRY_RPC_TRACEBACK=1` for tracebacks). Duplicate method names raise unless
`replace=True`. Destroy rejects in-flight RPCs. Keyword args go in
`{ kwargs: { … } }` (a trailing `{ timeout: ms }` is still call options, not
a positional dict).

### Timeout and cancel

Optional `timeout` on `expose` applies to worker handlers and returned
`Future`s (ignored for a synchronous main-thread handler). It rejects the JS
Promise and sets a cooperative cancel flag. This is **specified as
cooperative only**: Python cannot preempt a running worker thread
(`Future.cancel()` only skips work that has not started). Long handlers
should poll `rpc_cancelled()` (or capture `rpc_cancel_event()` for other
threads). `destroy()` joins the pool for at most ~2 seconds; uncooperative
handlers may briefly outlive the WebView. JS `call(..., { timeout: ms })` is
independent and only settles the Promise on the JS side.
`window.tkwry.cancel(id)` (or `promise.cancel()`) cancels from JS and
rejects with `RpcCancelledError`. Argument mismatches reject with a stable
`TypeError` payload (arity + simple annotation checks: `int` / `float` /
`str` / `bool` / `list` / `dict` / `Optional`). Envelopes include
`version: 1`; unknown versions reject with `RpcProtocolError` (omitted
version is treated as 1).

### Streaming (`window.tkwry.stream`)

A **sync generator** handler is consumed as a chunked stream. Protocol stays
`version: 1` with an additive `"stream": true` flag — `call` is unchanged.

```python
@web.expose(thread=True)
def ticks(count: int = 5):
    from tkwry import rpc_cancelled

    for i in range(count):
        if rpc_cancelled():
            return
        yield i + 1
```

```js
const parts = [];
const stream = window.tkwry.stream("ticks", 5);
// stream.id / stream.cancel() — same cancel envelope as call
for await (const n of stream) {
  parts.push(n);
}
```

Each `yield` is one JSON chunk (`window.tkwry._chunk`), capped at the
same **10 MiB** as RPC envelopes (`RpcMessageTooLarge` if over). The
iterator then completes. `call()` on a generator rejects with `TypeError`
(do not collect into an array). A non-generator `stream()` yields the
return value as a single chunk. Async generators and full-duplex RPC are
not supported.
Prefer `thread=True` so `cancel` / timeout can stop between yields;
a main-thread generator blocks Tk until it finishes. Breaking a
`for await` loop calls the iterator `return()` and sends cancel.
`stream.cancel()` / `window.tkwry.cancel(id)` uses the same cancel
envelope as `call` and rejects with `RpcCancelledError`. `destroy()`
sets the cooperative cancel flag and drops open streams without
settling (the native view is going away).
A handler exception after some chunks already arrived **rejects** the
iterator / Promise with the structured error payload — there is no
second error channel.

### Limits

IPC/RPC messages cap at **10 MiB**; RPC allows at most **256** positional
args and **256** kwargs. Stream chunks reuse that **10 MiB** JSON cap.
Oversized RPC / chunks reject with `RpcMessageTooLarge`; too many args
with `RpcArgumentLimitError`. RPC has its own 2048-deep queue so IPC
overflow cannot drop `tkwry.call`. Worker→Tk **stream** chunks also cap
at 2048 pending; further chunks are dropped (``rpc_stream``).

Prefer `take_queue_drop_stats()` → `QueueDropCounts` (includes
`download_complete` and `rpc_stream`). Legacy `take_queue_drop_counts()`
still returns `(ipc, page_load, title, drag_drop, eval, rpc)`.

## Python to JS events (emit)

```python
web.emit("data_updated", {"n": 1})

# Broadcast to every emit-eligible WebView sharing a WebSession
session.emit_all("data_updated", {"n": 1})
```

```js
window.tkwry.on("data_updated", (payload) => { ... });
// listener errors are logged with console.error (set window.tkwry.debug = false to silence)
```

``emit_all`` skips destroyed / not-ready / ``untrusted`` views and pages
outside each view's ``bridge_origins``. A sibling that fails ``emit`` is
skipped (traceback to stderr); others still receive the event. Returns
the number of views that successfully received it.

See [`examples/ipc_demo.py`](../examples/ipc_demo.py).

## Related

- [Usage](usage.md) — `app=`, eval, layout, navigation
- [Trust boundaries](trust.md) — who may call the bridge
- [Usage — Navigation / lifecycle callbacks](usage.md#navigation--lifecycle-callbacks)
  — Tk-thread vs WebKit-blocking hooks
