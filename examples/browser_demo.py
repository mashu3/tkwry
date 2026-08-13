"""Mini-browser: URL bar, tabs, and a shared ``WebSession``.

Link context menu is a Tk "Open in New Tab" menu (the native WebKit
"Open in New Window" item is suppressed). ``target=_blank``, Cmd/Ctrl-click,
middle-click, and ``window.open`` also open a tab. Creating another WebView
from ``on_new_window`` deadlocks WKWebView, so that hook only denies.

Downloads use ``on_download`` (save dialog) and ``on_download_complete``.
``session.emit_all`` toasts a notice on every live tab. Print opens the
system dialog. Native create failure is shown via ``on_creation_failed``.

IPC is only used to intercept links (no privileged Python APIs). Arbitrary
https pages need ``bridge_origins="*"`` (emits ``TkwrySecurityWarning``);
do not copy that into apps that ``expose()`` desktop capabilities.
"""

from __future__ import annotations

import json
import tempfile
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from tkwry import NewWindowResponse, PageLoadEvent, WebSession, WebView

HOME = "https://github.com/mashu3"

# Runs at document start on every navigation. Do not create a WebView from
# WebKit's new-window hook — intercept links here instead.
LINK_HELPER_JS = """
(function () {
  function absHref(href) {
    try { return new URL(href, location.href).href; } catch (e) { return href || ""; }
  }
  function openable(href) {
    try {
      var u = new URL(href, location.href);
      return (
        u.protocol === "http:" ||
        u.protocol === "https:" ||
        u.protocol === "tkwry:"
      );
    } catch (e) { return false; }
  }
  function post(payload) {
    if (window.ipc && window.ipc.postMessage) {
      window.ipc.postMessage(JSON.stringify(payload));
    }
  }
  document.addEventListener("contextmenu", function (e) {
    var a = e.target && e.target.closest && e.target.closest("a[href]");
    if (!a) return;
    var href = absHref(a.getAttribute("href") || a.href);
    if (!openable(href)) return;
    e.preventDefault();
    e.stopPropagation();
    post({ action: "link-menu", href: href, x: e.screenX, y: e.screenY });
  }, true);
  function maybeNewTab(e) {
    var a = e.target && e.target.closest && e.target.closest("a[href]");
    if (!a) return;
    var href = absHref(a.getAttribute("href") || a.href);
    if (!openable(href)) return;
    var blank = (a.target || "").toLowerCase() === "_blank";
    var modified = e.metaKey || e.ctrlKey || e.button === 1;
    if (!blank && !modified) return;
    e.preventDefault();
    e.stopPropagation();
    post({ action: "newtab", href: href });
  }
  document.addEventListener("click", maybeNewTab, true);
  document.addEventListener("auxclick", maybeNewTab, true);
  window.open = function (url) {
    if (url) {
      var href = absHref(String(url));
      if (openable(href)) post({ action: "newtab", href: href });
    }
    return null;
  };
  function showNotice(payload) {
    var text = "";
    if (payload && typeof payload === "object" && payload.text) {
      text = String(payload.text);
    } else if (payload != null) {
      text = String(payload);
    }
    if (!text) return;
    var el = document.createElement("div");
    el.textContent = text;
    el.setAttribute("data-tkwry-notice", "1");
    el.style.cssText = [
      "position:fixed",
      "z-index:2147483647",
      "right:12px",
      "bottom:12px",
      "max-width:min(360px,80vw)",
      "padding:10px 14px",
      "border-radius:8px",
      "background:#111827",
      "color:#f9fafb",
      "font:13px/1.4 system-ui,sans-serif",
      "box-shadow:0 8px 24px rgba(0,0,0,.35)",
    ].join(";");
    (document.body || document.documentElement).appendChild(el);
    setTimeout(function () { el.remove(); }, 3500);
  }
  function bootNotice() {
    if (!window.tkwry || !window.tkwry.on || window._tkwryNotice) return;
    window._tkwryNotice = true;
    window.tkwry.on("notice", showNotice);
  }
  bootNotice();
  document.addEventListener("DOMContentLoaded", bootNotice);
})();
"""


@dataclass
class Tab:
    frame: tk.Frame
    web: WebView
    button: ttk.Button
    chip: ttk.Frame


def _normalize_url(raw: str) -> str:
    text = raw.strip()
    if not text:
        return HOME
    if "://" not in text:
        return "https://" + text
    return text


def _tab_label(title: str) -> str:
    label = (title or "Tab").strip() or "Tab"
    if len(label) > 24:
        return label[:23] + "…"
    return label


def main() -> None:
    profile = Path(tempfile.mkdtemp(prefix="tkwry-browser-"))
    # Shared profile for all tabs. If you also pass app=, every WebView on this
    # session must use the same app= root (Linux registers tkwry:// once per
    # WebContext; tkwry enforces this on all platforms).
    session = WebSession(data_directory=profile)

    root = tk.Tk()
    root.title("tkwry browser")
    root.minsize(720, 480)

    tab_bar = ttk.Frame(root)
    tab_bar.pack(fill="x", padx=8, pady=(8, 0))
    tab_buttons = ttk.Frame(tab_bar)
    tab_buttons.pack(side="left", fill="x", expand=True)

    toolbar = ttk.Frame(root)
    toolbar.pack(fill="x", padx=8, pady=(4, 0))
    url_var = tk.StringVar(value=HOME)
    url_entry = ttk.Entry(toolbar, textvariable=url_var)

    content = tk.Frame(root, bg="#1e1e1e")
    content.pack(fill="both", expand=True, padx=8, pady=(4, 4))
    status_var = tk.StringVar(value="Ready")
    ttk.Label(root, textvariable=status_var, foreground="#555").pack(
        fill="x", padx=8, pady=(0, 8)
    )

    tabs: dict[str, Tab] = {}
    selected_id: str | None = None

    def current_tab() -> Tab | None:
        return tabs.get(selected_id) if selected_id else None

    def select_tab(tab_id: str) -> None:
        nonlocal selected_id
        selected_id = tab_id
        for tid, tab in tabs.items():
            if tid == tab_id:
                tab.frame.pack(fill="both", expand=True)
                tab.button.configure(style="TButton")
                current = tab.web.url
                if current:
                    url_var.set(current)
                tab.web.sync_bounds()
            else:
                tab.frame.pack_forget()
                tab.button.configure(style="Toolbutton")

    def close_tab(tab_id: str) -> None:
        nonlocal selected_id
        tab = tabs.get(tab_id)
        if tab is None:
            return
        ids = list(tabs)
        idx = ids.index(tab_id)
        was_selected = selected_id == tab_id
        tab.web.destroy()
        tab.chip.destroy()
        tab.frame.destroy()
        del tabs[tab_id]
        if not tabs:
            add_tab(HOME)
            return
        if was_selected:
            nxt = ids[idx + 1] if idx + 1 < len(ids) else ids[idx - 1]
            select_tab(nxt)

    def copy_link(href: str) -> None:
        root.clipboard_clear()
        root.clipboard_append(href)

    def show_link_menu(href: str, x: int, y: int) -> None:
        menu = tk.Menu(root, tearoff=0)
        menu.add_command(label="Open in New Tab", command=lambda: add_tab(href))
        menu.add_command(label="Copy Link", command=lambda: copy_link(href))
        try:
            menu.tk_popup(int(x), int(y))
        finally:
            menu.grab_release()

    def on_ipc(message: str) -> None:
        try:
            payload = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        action = payload.get("action")
        href = str(payload.get("href") or "").strip()
        if not href:
            return
        if action == "newtab":
            add_tab(href)
        elif action == "link-menu":
            show_link_menu(href, payload.get("x") or 0, payload.get("y") or 0)

    def add_tab(url: str) -> Tab:
        frame = tk.Frame(content, bg="#1e1e1e")
        tab_id = str(frame)
        chip = ttk.Frame(tab_buttons)
        chip.pack(side="left", padx=(0, 2))
        button = ttk.Button(
            chip,
            text="Tab",
            style="Toolbutton",
            command=lambda i=tab_id: select_tab(i),
        )
        button.pack(side="left")
        close_btn = ttk.Button(
            chip,
            text="×",
            width=2,
            style="Toolbutton",
            command=lambda i=tab_id: close_tab(i),
        )
        close_btn.pack(side="left")
        chip.bind("<Button-2>", lambda _e, i=tab_id: close_tab(i))
        button.bind("<Button-2>", lambda _e, i=tab_id: close_tab(i))
        close_btn.bind("<Button-2>", lambda _e, i=tab_id: close_tab(i))

        def on_title(title: str) -> None:
            button.configure(text=_tab_label(title))

        def on_page_load(event: PageLoadEvent, page_url: str) -> None:
            if event is PageLoadEvent.Finished and page_url and selected_id == tab_id:
                url_var.set(page_url)

        def on_create_failed(exc: BaseException) -> None:
            status_var.set(f"Create failed: {exc}")
            messagebox.showerror("WebView failed", str(exc), parent=root)

        def on_download(url: str, suggested: str) -> str | bool:
            name = Path(suggested).name or "download"
            dest = filedialog.asksaveasfilename(
                parent=root,
                title="Save download",
                initialfile=name,
            )
            if not dest:
                status_var.set("Download cancelled")
                return False
            status_var.set(f"Downloading to {dest}")
            return dest

        def on_download_complete(url: str, dest: str | None, success: bool) -> None:
            if success:
                where = dest or url
                status_var.set(f"Saved {where}")
                session.emit_all("notice", {"text": f"Saved {Path(where).name}"})
            else:
                status_var.set("Download failed")

        web = WebView(
            frame,
            url=url,
            session=session,
            focused=False,
            bridge_origins="*",
            ipc_handler=on_ipc,
            initialization_script=LINK_HELPER_JS,
            on_title_changed=on_title,
            on_page_load=on_page_load,
            on_new_window=lambda _url: NewWindowResponse.Deny,
            on_creation_failed=on_create_failed,
            on_download=on_download,
            on_download_complete=on_download_complete,
        )
        tab = Tab(frame=frame, web=web, button=button, chip=chip)
        tabs[tab_id] = tab
        select_tab(tab_id)
        url_var.set(url)
        return tab

    def go() -> None:
        tab = current_tab()
        if tab is None:
            return
        target = _normalize_url(url_var.get())
        try:
            tab.web.load_url(target)
        except ValueError as exc:
            messagebox.showerror("Invalid URL", str(exc), parent=root)
            return
        url_var.set(target)

    def reload_current() -> None:
        tab = current_tab()
        if tab is not None:
            tab.web.reload()

    def go_back() -> None:
        tab = current_tab()
        if tab is not None and tab.web.ready and tab.web.can_go_back():
            tab.web.go_back()

    def go_forward() -> None:
        tab = current_tab()
        if tab is not None and tab.web.ready and tab.web.can_go_forward():
            tab.web.go_forward()

    def print_current() -> None:
        tab = current_tab()
        if tab is None or not tab.web.ready:
            return
        tab.web.print()
        status_var.set("Print dialog")
        session.emit_all("notice", {"text": "Print dialog opened"})

    ttk.Button(tab_bar, text="+", width=3, command=lambda: add_tab(HOME)).pack(
        side="left"
    )
    ttk.Button(toolbar, text="Back", command=go_back).pack(side="left")
    ttk.Button(toolbar, text="Forward", command=go_forward).pack(side="left")
    ttk.Button(toolbar, text="Reload", command=reload_current).pack(side="left")
    url_entry.pack(side="left", fill="x", expand=True, padx=8)
    ttk.Button(toolbar, text="Go", command=go).pack(side="left")
    ttk.Button(toolbar, text="Print", command=print_current).pack(
        side="left", padx=(8, 0)
    )

    def close_selected(_event: object = None) -> None:
        if selected_id:
            close_tab(selected_id)

    url_entry.bind("<Return>", lambda _e: go())
    url_entry.bind("<Button-1>", lambda _e: url_entry.icursor(tk.END), add="+")
    root.bind_all("<Command-w>", close_selected)
    root.bind_all("<Control-w>", close_selected)

    add_tab(HOME)
    root.focus_set()
    root.update_idletasks()
    root.geometry("960x640")
    root.mainloop()


if __name__ == "__main__":
    main()
