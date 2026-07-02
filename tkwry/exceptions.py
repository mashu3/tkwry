"""WebView lifecycle exceptions."""


class WebViewNotReadyError(RuntimeError):
    """Raised when a WebView API is called before native initialization completes."""


class WebViewDestroyedError(RuntimeError):
    """Raised when a WebView API is called after :meth:`~tkwry.WebView.destroy`."""
