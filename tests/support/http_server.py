"""Local HTTP server for browser-essentials integration tests."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class RequestRecord:
    path: str
    headers: dict[str, str]
    cookie: str | None


@dataclass
class LocalHttpServer:
    """Thread-backed ``ThreadingHTTPServer`` bound to ``127.0.0.1``."""

    host: str = "127.0.0.1"
    requests: list[RequestRecord] = field(default_factory=list)
    _httpd: ThreadingHTTPServer | None = field(default=None, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    @property
    def port(self) -> int:
        if self._httpd is None:
            raise RuntimeError("LocalHttpServer is not started")
        return int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        # Prefer the configured hostname (e.g. localhost) over the bound
        # address so cookie domains / page URLs stay consistent.
        if self._httpd is None:
            raise RuntimeError("LocalHttpServer is not started")
        return f"http://{self.host}:{self.port}"

    def url(self, path: str) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self.base_url}{path}"

    def clear_requests(self) -> None:
        with self._lock:
            self.requests.clear()

    def last_request(self, path: str | None = None) -> RequestRecord | None:
        with self._lock:
            matched = [r for r in self.requests if path is None or r.path == path]
        return matched[-1] if matched else None

    def start(self) -> LocalHttpServer:
        if self._httpd is not None:
            raise RuntimeError("LocalHttpServer already started")

        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

            def _record(self) -> None:
                headers = {str(k): str(v) for k, v in self.headers.items()}
                with server._lock:
                    server.requests.append(
                        RequestRecord(
                            path=urlparse(self.path).path,
                            headers=headers,
                            cookie=self.headers.get("Cookie"),
                        )
                    )

            def _send_html(
                self, body: str, *, extra_headers: list[tuple[str, str]] | None = None
            ) -> None:
                raw = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                for name, value in extra_headers or ():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                self._record()
                path = urlparse(self.path).path

                if path == "/headers":
                    payload = json.dumps(
                        {k: v for k, v in self.headers.items()},
                        ensure_ascii=True,
                    )
                    self._send_html(
                        "<!DOCTYPE html><html><body>"
                        f"<pre id='headers'>{payload}</pre>"
                        "<p id='t'>headers</p>"
                        "</body></html>"
                    )
                    return

                if path == "/set-cookie":
                    self._send_html(
                        "<!DOCTYPE html><html><body>"
                        "<p id='t'>set-cookie</p>"
                        "</body></html>",
                        extra_headers=[
                            ("Set-Cookie", "tkwry_sid=from-server; Path=/"),
                        ],
                    )
                    return

                if path == "/show":
                    req_cookie = self.headers.get("Cookie") or ""
                    self._send_html(
                        "<!DOCTYPE html><html><body>"
                        f"<pre id='req-cookie'>{req_cookie}</pre>"
                        "<pre id='doc-cookie'></pre>"
                        "<p id='t'>show</p>"
                        "<script>"
                        "document.getElementById('doc-cookie').textContent ="
                        " document.cookie;"
                        "</script>"
                        "</body></html>"
                    )
                    return

                if path == "/ok":
                    self._send_html(
                        "<!DOCTYPE html><html><body><p id='t'>ok</p></body></html>"
                    )
                    return

                self.send_error(404, "not found")

        httpd = ThreadingHTTPServer((self.host, 0), Handler)
        httpd.daemon_threads = True
        self._httpd = httpd
        thread = threading.Thread(
            target=httpd.serve_forever,
            name="tkwry-local-http",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        return self

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=5.0)

    def __enter__(self) -> LocalHttpServer:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
