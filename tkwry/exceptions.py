"""WebView lifecycle and RPC exceptions."""


class WebViewNotReadyError(RuntimeError):
    """Raised when a WebView API needs layout-ready state and ``ready`` is false.

    Native creation may already have succeeded; wait for ``<<WebViewReady>>``
    or :meth:`~tkwry.WebView.wait_until_ready` before calling ready-gated APIs.
    """


class WebViewCreationError(RuntimeError):
    """Raised when the native WebView could not be created after all retries."""


class WebViewDestroyedError(RuntimeError):
    """Raised when a WebView API is called after :meth:`~tkwry.WebView.destroy`."""


class RpcTimeoutError(TimeoutError):
    """Structured RPC error when an exposed handler exceeds its timeout.

    Timeout rejects the JS Promise and signals cooperative cancellation
    (:func:`tkwry.rpc_cancelled`). It does **not** forcibly stop Python
    already running on a worker thread; ``Future.cancel()`` only skips
    work that has not started.
    """


class RpcCancelledError(RuntimeError):
    """Structured RPC error when a call is cancelled from JS or destroy.

    ``window.tkwry.cancel(id)`` and :meth:`~tkwry.WebView.destroy` reject the
    Promise and set the cooperative cancel flag. Running worker Python is not
    preempted; poll :func:`tkwry.rpc_cancelled`.
    """


class RpcSerializationError(ValueError):
    """Raised when an RPC result or ``emit`` payload is not JSON-serializable.

    ``datetime``, custom objects, ``NaN`` / ``Infinity``, and anything else
    outside standard JSON fail explicitly instead of being stringified.
    """
