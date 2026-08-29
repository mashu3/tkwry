"""Guard public Python / stub / Rust constructor signatures."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from tkwry import WebSession, WebView

ROOT = Path(__file__).resolve().parents[2]
CORE_STUB = ROOT / "tkwry" / "_core.pyi"
LIB_RS = ROOT / "src" / "lib.rs"
SESSION_RS = ROOT / "src" / "session.rs"

WEBVIEW_INIT_KWONLY = (
    "width",
    "height",
    "url",
    "html",
    "app",
    "session",
    "data_directory",
    "ephemeral",
    "untrusted",
    "bridge_origins",
    "bridge_allow",
    "navigation_allow",
    "open_external",
    "download_allow",
    "ipc_handler",
    "spa_fallback",
    "app_dev",
    "csp",
    "coop",
    "corp",
    "rpc_traceback",
    "devtools",
    "clipboard",
    "background_color",
    "user_agent",
    "initialization_script",
    "focused",
    "on_navigation",
    "on_page_load",
    "on_title_changed",
    "on_new_window",
    "permission_handler",
    "drag_drop_handler",
    "on_download",
    "on_download_complete",
    "on_creation_failed",
)

NATIVE_WEBVIEW_KWONLY = (
    "owner_thread",
    "width",
    "height",
    "url",
    "html",
    "app_root",
    "spa_fallback",
    "app_cache_control",
    "app_csp",
    "app_coop",
    "app_corp",
    "visible",
    "devtools",
    "clipboard",
    "focused",
    "background_color",
    "user_agent",
    "initialization_script",
    "on_navigation",
    "on_new_window",
    "on_permission",
    "page_load_listening",
    "ipc_listening",
    "title_listening",
    "drag_drop_listening",
    "on_download_started",
    "download_complete_listening",
    "with_ipc",
    "session",
)


def _stub_new_kwonly(class_name: str) -> tuple[str, ...]:
    tree = ast.parse(CORE_STUB.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "__new__":
                    return tuple(arg.arg for arg in item.args.kwonlyargs)
    raise AssertionError(f"__new__ not found for {class_name} in _core.pyi")


def _rust_signature_kwonly(source: str, marker: str) -> tuple[str, ...]:
    start = source.index(marker)
    open_paren = start + len(marker) - 1
    if source[open_paren] != "(":
        raise AssertionError(f"marker must end with '(': {marker!r}")
    depth = 0
    end = None
    for index, char in enumerate(source[open_paren:], start=open_paren):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end is None:
        raise AssertionError(f"unbalanced signature after {marker!r}")
    body = source[open_paren + 1 : end]
    names: list[str] = []
    seen_star = False
    for raw in body.split(","):
        token = raw.strip()
        if not token or token.startswith("#"):
            continue
        token = token.split("=")[0].strip()
        if token == "*":
            seen_star = True
            continue
        if not seen_star:
            continue
        names.append(token)
    return tuple(names)


def _kwonly(fn) -> tuple[str, ...]:  # noqa: ANN001
    signature = inspect.signature(fn)
    return tuple(
        name
        for name, param in signature.parameters.items()
        if param.kind is inspect.Parameter.KEYWORD_ONLY
    )


def test_webview_init_kwonly_matches_frozen_list() -> None:
    assert _kwonly(WebView.__init__) == WEBVIEW_INIT_KWONLY


def test_native_webview_stub_matches_rust_signature() -> None:
    stub = _stub_new_kwonly("WebView")
    rust = _rust_signature_kwonly(
        LIB_RS.read_text(encoding="utf-8"), "#[pyo3(signature = ("
    )
    assert stub == NATIVE_WEBVIEW_KWONLY
    assert rust == NATIVE_WEBVIEW_KWONLY


def test_native_session_stub_matches_rust_signature() -> None:
    stub = _stub_new_kwonly("WebSession")
    rust = _rust_signature_kwonly(
        SESSION_RS.read_text(encoding="utf-8"), "#[pyo3(signature = ("
    )
    assert stub == rust == ("data_directory", "ephemeral")


def test_python_session_init_kwonly() -> None:
    assert _kwonly(WebSession.__init__) == ("ephemeral",)
