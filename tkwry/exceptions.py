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
    """Structured RPC error when an exposed handler exceeds its timeout."""
