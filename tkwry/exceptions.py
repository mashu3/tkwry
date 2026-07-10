"""WebView lifecycle exceptions."""


class WebViewNotReadyError(RuntimeError):
    """Raised when a WebView API is called before native initialization completes."""


class WebViewCreationError(RuntimeError):
    """Raised when the native WebView could not be created after all retries."""


class WebViewDestroyedError(RuntimeError):
    """Raised when a WebView API is called after :meth:`~tkwry.WebView.destroy`."""
