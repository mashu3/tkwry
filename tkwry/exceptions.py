"""WebView lifecycle and RPC exceptions."""


class TkwrySecurityWarning(UserWarning):
    """Warning for explicit trust-boundary choices.

    Emitted when ``bridge_origins="*"`` (every page can call IPC/RPC) and when
    ``devtools=True`` is combined with that allowlist. Filter with
    ``PYTHONWARNINGS=ignore::tkwry.TkwrySecurityWarning`` if you accept the risk.
    """


class WebViewNotReadyError(RuntimeError):
    """Raised when a WebView API needs layout-ready state and ``ready`` is false.

    Native creation may already have succeeded; wait for ``<<WebViewReady>>``
    or :meth:`~tkwry.WebView.wait_until_ready` before calling ready-gated APIs.
    """


class WebViewCreationError(RuntimeError):
    """Raised when a gated API is used after native creation was abandoned.

    The constructor itself does not raise. Listen for
    ``<<WebViewCreateFailed>>`` / :meth:`~tkwry.WebView.when_failed` or check
    ``creation_failed``.
    """


class WebViewDestroyedError(RuntimeError):
    """Raised when a WebView API is called after :meth:`~tkwry.WebView.destroy`."""


class RpcTimeoutError(TimeoutError):
    """Structured RPC error when an exposed handler exceeds its timeout.

    Timeout rejects the JS Promise and signals cooperative cancellation
    (:func:`tkwry.rpc_cancelled`). Python threads cannot be preempted:
    running worker code continues until it returns or polls the flag.
    ``Future.cancel()`` only skips work that has not started.
    """


class RpcCancelledError(RuntimeError):
    """Structured RPC error when a call is cancelled from JS or destroy.

    ``window.tkwry.cancel(id)`` and :meth:`~tkwry.WebView.destroy` reject the
    Promise and set the cooperative cancel flag. Running worker Python is not
    preempted; poll :func:`tkwry.rpc_cancelled`. Destroy joins the pool for
    at most ~2 seconds; uncooperative handlers may outlive the WebView.
    """


class RpcSerializationError(ValueError):
    """Raised when an RPC result or ``emit`` payload is not JSON-serializable.

    ``datetime``, custom objects, ``NaN`` / ``Infinity``, and anything else
    outside standard JSON fail explicitly instead of being stringified.
    """
