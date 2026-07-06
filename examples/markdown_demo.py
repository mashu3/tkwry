"""Demo: Monaco markdown editor (left) + live preview (right) via PanedWindow.

Requires network access: Monaco Editor, marked, and highlight.js load from CDN.
"""

from __future__ import annotations

import json
import sys
import tkinter as tk
from collections.abc import Callable

from tkwry import WebView

SAMPLE_MARKDOWN = """\
# tkwry Markdown Editor

Edit **Markdown** on the left — the preview on the right updates as you type.

## How it works

1. **Left pane** — [Monaco Editor](https://microsoft.github.io/monaco-editor/)
   in a WebView
2. **Right pane** — rendered HTML (`marked` + `highlight.js`)
3. **Bridge** — `window.ipc.postMessage()` → Tkinter → `eval_js()` on the preview pane

Drag the **sash** between panes to resize.

## Requirements

**Network / CDN** — Monaco Editor, `marked`, and `highlight.js` load from CDN
on first launch. Offline, the WebViews open but the editor and preview stay blank.

## Code sample

```python
from tkwry import WebView

editor = WebView(left_frame, html=EDITOR_HTML, ipc_handler=on_ipc)
preview = WebView(right_frame, html=PREVIEW_HTML)
```

## List

- Syntax highlighting in the editor
- GitHub-flavoured markdown in the preview
- Two WebViews in one `PanedWindow`

> *Tip:* Try adding a table or a blockquote.
"""

MONACO_VERSION = "0.52.2"
PREVIEW_DEBOUNCE_MS = 80


class HtmlPages:
    """Embedded WebView HTML for the editor and preview panes."""

    @staticmethod
    def editor(initial_markdown: str) -> str:
        initial_json = json.dumps(initial_markdown)
        return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0;
      height: 100%;
      overflow: hidden;
      background: #1e1e1e;
    }}
    #editor {{
      width: 100%;
      height: 100%;
    }}
  </style>
</head>
<body>
  <div id="editor"></div>
  <script src="https://cdn.jsdelivr.net/npm/monaco-editor@{MONACO_VERSION}/min/vs/loader.js"></script>
  <script>
    const INITIAL = {initial_json};

    function sendMarkdown(text) {{
      if (!window.ipc) return;
      window.ipc.postMessage(JSON.stringify({{ type: "markdown", text }}));
    }}

    require.config({{
      paths: {{
        vs: "https://cdn.jsdelivr.net/npm/monaco-editor@{MONACO_VERSION}/min/vs",
      }},
    }});

    require(["vs/editor/editor.main"], function () {{
      const editor = monaco.editor.create(document.getElementById("editor"), {{
        value: INITIAL,
        language: "markdown",
        theme: "vs-dark",
        fontSize: 14,
        lineNumbers: "on",
        minimap: {{ enabled: false }},
        wordWrap: "on",
        scrollBeyondLastLine: false,
        automaticLayout: true,
        contextmenu: false,
      }});

      document.addEventListener("contextmenu", (event) => event.preventDefault());

      window.editorUndo = function () {{
        editor.focus();
        editor.trigger("keyboard", "undo", null);
      }};
      window.editorRedo = function () {{
        editor.focus();
        editor.trigger("keyboard", "redo", null);
      }};
      window.editorCut = function () {{
        editor.focus();
        editor.trigger("keyboard", "editor.action.clipboardCutAction", null);
      }};
      window.editorCopy = function () {{
        editor.focus();
        editor.trigger("keyboard", "editor.action.clipboardCopyAction", null);
      }};
      window.editorPasteText = function (text) {{
        if (text == null || text === "") return;
        editor.focus();
        const selection = editor.getSelection();
        if (!selection) return;
        editor.executeEdits("clipboard-paste", [
          {{
            range: selection,
            text: text,
            forceMoveMarkers: true,
          }},
        ]);
      }};
      window.editorSetMinimap = function (enabled) {{
        editor.updateOptions({{ minimap: {{ enabled: !!enabled }} }});
      }};

      editor.onDidChangeModelContent(() => sendMarkdown(editor.getValue()));
      sendMarkdown(editor.getValue());
      if (window.ipc) {{
        window.ipc.postMessage(JSON.stringify({{ type: "ready" }}));
      }}
    }});
  </script>
</body>
</html>
"""

    @staticmethod
    def preview() -> str:
        return """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <link
    rel="stylesheet"
    href="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.11.1/build/styles/github.min.css"
  />
  <style>
    * { box-sizing: border-box; }
    html, body {
      margin: 0;
      height: 100%;
      overflow: auto;
      background: #ffffff;
      color: #24292f;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial,
        sans-serif;
      font-size: 16px;
      line-height: 1.6;
    }
    #preview {
      max-width: 48rem;
      margin: 0 auto;
      padding: 24px 28px 48px;
    }
    #preview h1, #preview h2, #preview h3 {
      border-bottom: 1px solid #d8dee4;
      padding-bottom: 0.3em;
      margin-top: 1.5em;
      margin-bottom: 16px;
      line-height: 1.25;
    }
    #preview h1 { font-size: 2em; border-bottom-width: 1px; margin-top: 0; }
    #preview h2 { font-size: 1.5em; }
    #preview h3 { font-size: 1.25em; border-bottom: none; }
    #preview code {
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas,
        monospace;
      font-size: 0.9em;
      background: #f6f8fa;
      padding: 0.2em 0.4em;
      border-radius: 6px;
    }
    #preview pre {
      background: #f6f8fa;
      border-radius: 6px;
      padding: 16px;
      overflow: auto;
      line-height: 1.45;
    }
    #preview pre code {
      background: none;
      padding: 0;
      font-size: 0.85em;
    }
    #preview blockquote {
      margin: 0;
      padding: 0 1em;
      color: #57606a;
      border-left: 0.25em solid #d0d7de;
    }
    #preview ul, #preview ol { padding-left: 2em; }
    #preview a { color: #0969da; text-decoration: none; }
    #preview a:hover { text-decoration: underline; }
    #preview table {
      border-collapse: collapse;
      width: 100%;
      margin: 16px 0;
    }
    #preview th, #preview td {
      border: 1px solid #d0d7de;
      padding: 6px 13px;
    }
    #preview th { background: #f6f8fa; }
    .empty {
      color: #8b949e;
      font-style: italic;
      text-align: center;
      padding-top: 40vh;
    }
  </style>
</head>
<body>
  <article id="preview"><p class="empty">Waiting for markdown…</p></article>
  <script src="https://cdn.jsdelivr.net/npm/marked@15.0.7/marked.min.js"></script>
  <script src="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.11.1/build/highlight.min.js"></script>
  <script>
    marked.setOptions({
      gfm: true,
      breaks: false,
      highlight(code, lang) {
        if (lang && hljs.getLanguage(lang)) {
          return hljs.highlight(code, { language: lang }).value;
        }
        return hljs.highlightAuto(code).value;
      },
    });

    window.setMarkdown = function (text) {
      const el = document.getElementById("preview");
      if (!text || !text.trim()) {
        el.innerHTML = '<p class="empty">(empty)</p>';
        return;
      }
      el.innerHTML = marked.parse(text);
    };

    document.addEventListener("contextmenu", (event) => event.preventDefault());
  </script>
</body>
</html>
"""


class MacOsEditMenu:
    """Hide macOS-injected Edit menu items (Auto Fill, Dictation, etc.)."""

    _REMOVED_TITLES = frozenset(
        {
            "autofill",
            "auto fill",
            "smart dictation",
            "start dictation",
            "start dictation…",
            "start dictation...",
        }
    )

    @classmethod
    def disable_extras(cls) -> None:
        if sys.platform != "darwin":
            return
        try:
            from Foundation import NSUserDefaults
        except ImportError:
            return
        defaults = NSUserDefaults.standardUserDefaults()
        defaults.setBool_forKey_(True, "NSDisabledDictationMenuItem")
        defaults.setBool_forKey_(True, "NSDisabledCharacterPaletteMenuItem")
        defaults.setBool_forKey_(False, "NSAutoFillHeuristicControllerEnabled")

    @classmethod
    def strip_injected_items(cls) -> None:
        cls._strip_submenu("Edit", cls._REMOVED_TITLES, ("start dictation",))

    @staticmethod
    def _strip_submenu(
        menu_title: str,
        removed_titles: frozenset[str],
        removed_prefixes: tuple[str, ...] = (),
    ) -> None:
        if sys.platform != "darwin":
            return
        try:
            from AppKit import NSApp
        except ImportError:
            return
        main_menu = NSApp.mainMenu()
        if main_menu is None:
            return
        menu_item = main_menu.itemWithTitle_(menu_title)
        if menu_item is None:
            return
        submenu = menu_item.submenu()
        if submenu is None:
            return
        for index in range(submenu.numberOfItems() - 1, -1, -1):
            item = submenu.itemAtIndex_(index)
            if item is None:
                continue
            title = str(item.title()).strip().casefold()
            if title in removed_titles or any(
                title.startswith(prefix) for prefix in removed_prefixes
            ):
                submenu.removeItemAtIndex_(index)


class MacOsWindowTabs:
    """Disable macOS window tabbing and hide related menu items."""

    _REMOVED_TITLES = frozenset(
        {
            "show tab bar",
            "hide tab bar",
            "show all tabs",
            "merge all windows",
            "move tab to new window",
        }
    )

    @classmethod
    def disable(cls) -> None:
        if sys.platform != "darwin":
            return
        try:
            from AppKit import NSWindow
        except ImportError:
            return
        NSWindow.setAllowsAutomaticWindowTabbing_(False)

    @classmethod
    def apply(cls) -> None:
        cls._disallow_on_all_windows()
        cls.strip_menu_items()

    @classmethod
    def _disallow_on_all_windows(cls) -> None:
        if sys.platform != "darwin":
            return
        try:
            from AppKit import NSApp, NSWindowTabbingModeDisallowed
        except ImportError:
            return
        for window in NSApp.windows() or []:
            window.setTabbingMode_(NSWindowTabbingModeDisallowed)

    @classmethod
    def strip_menu_items(cls) -> None:
        MacOsEditMenu._strip_submenu("View", cls._REMOVED_TITLES, ("show all tab",))
        MacOsEditMenu._strip_submenu(
            "Window", cls._REMOVED_TITLES, ("merge all", "move tab")
        )


class MenuAccelerators:
    """Platform-appropriate menu accelerator labels."""

    @staticmethod
    def edit(key: str) -> str:
        if sys.platform == "darwin":
            return f"Command-{key}"
        return f"Ctrl+{key}"

    @classmethod
    def undo(cls) -> str:
        return cls.edit("Z")

    @classmethod
    def redo(cls) -> str:
        if sys.platform == "darwin":
            return "Command-Shift-Z"
        return "Ctrl+Y"


class EditorShortcutBindings:
    """Bind editor shortcuts before tkwry's macOS web key guard."""

    TAG = "MarkdownEditorShortcuts"
    UNDO = (
        "<Command-z>",
        "<Command-Z>",
        "<Control-z>",
        "<Control-Z>",
        "<<Undo>>",
    )
    REDO = (
        "<Command-Shift-z>",
        "<Command-Shift-Z>",
        "<Control-y>",
        "<Control-Y>",
        "<<Redo>>",
    )
    CUT = (
        "<Command-x>",
        "<Command-X>",
        "<Control-x>",
        "<Control-X>",
        "<<Cut>>",
    )
    COPY = (
        "<Command-c>",
        "<Command-C>",
        "<Control-c>",
        "<Control-C>",
        "<<Copy>>",
    )
    PASTE = (
        "<Command-v>",
        "<Command-V>",
        "<Control-v>",
        "<Control-V>",
        "<<Paste>>",
    )

    @classmethod
    def install(
        cls,
        root: tk.Misc,
        bindings: list[tuple[tuple[str, ...], Callable[[tk.Event], str]]],
    ) -> None:
        for sequences, handler in bindings:
            for sequence in sequences:
                root.bind_class(cls.TAG, sequence, handler)
        cls._prepend_tag_tree(root, cls.TAG)

    @staticmethod
    def wrap(action: Callable[[], None]) -> Callable[[tk.Event], str]:
        def handler(_event: tk.Event) -> str:
            action()
            return "break"

        return handler

    @staticmethod
    def _prepend_tag_tree(widget: tk.Misc, tag: str) -> None:
        tags = widget.bindtags()
        if not tags or tags[0] != tag:
            widget.bindtags((tag, *tuple(t for t in tags if t != tag)))
        for child in widget.winfo_children():
            EditorShortcutBindings._prepend_tag_tree(child, tag)


class MarkdownEditorDemo:
    """Tkinter shell: Monaco editor + live Markdown preview."""

    def __init__(self) -> None:
        MacOsEditMenu.disable_extras()
        MacOsWindowTabs.disable()

        self._editor_ready = False
        self._preview_ready = False
        self._last_markdown = SAMPLE_MARKDOWN
        self._editor_web: WebView | None = None
        self._preview_web: WebView | None = None
        self._preview_after_id: str | None = None

        self.root = tk.Tk()
        self.root.title("tkwry Markdown editor demo")
        self.root.geometry("1200x760")
        self.root.minsize(800, 520)

        self._minimap_var = tk.BooleanVar(self.root, value=False)

        self._build_menubar()
        self._build_panes()
        self._install_shortcuts()

    def run(self) -> None:
        self.root.mainloop()

    def _build_menubar(self) -> None:
        menubar = tk.Menu(self.root)
        if sys.platform == "darwin":
            menubar.add_cascade(menu=tk.Menu(menubar, name="apple"))

        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(menu=edit_menu, label="Edit")
        if sys.platform == "darwin":
            edit_menu.configure(postcommand=MacOsEditMenu.strip_injected_items)

        edit_menu.add_command(
            label="Undo",
            command=self._undo,
            accelerator=MenuAccelerators.undo(),
        )
        edit_menu.add_command(
            label="Redo",
            command=self._redo,
            accelerator=MenuAccelerators.redo(),
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            label="Cut",
            command=self._cut,
            accelerator=MenuAccelerators.edit("X"),
        )
        edit_menu.add_command(
            label="Copy",
            command=self._copy,
            accelerator=MenuAccelerators.edit("C"),
        )
        edit_menu.add_command(
            label="Paste",
            command=self._paste,
            accelerator=MenuAccelerators.edit("V"),
        )

        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(menu=view_menu, label="View")
        if sys.platform == "darwin":
            view_menu.configure(postcommand=MacOsWindowTabs.strip_menu_items)
        view_menu.add_checkbutton(
            label="Minimap",
            variable=self._minimap_var,
            command=self._toggle_minimap,
        )

        self.root.config(menu=menubar)
        if sys.platform == "darwin":
            self.root.after_idle(MacOsWindowTabs.apply)
            self.root.bind("<Map>", lambda _event: MacOsWindowTabs.apply(), add="+")

    def _build_panes(self) -> None:
        paned = tk.PanedWindow(
            self.root,
            orient=tk.HORIZONTAL,
            sashwidth=6,
            sashrelief=tk.RAISED,
            bg="#404040",
            showhandle=False,
        )
        paned.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        editor_frame = tk.Frame(paned, bg="#1e1e1e")
        preview_frame = tk.Frame(paned, bg="#ffffff")
        paned.add(editor_frame, minsize=280, stretch="always")
        paned.add(preview_frame, minsize=280, stretch="always")

        self._preview_web = WebView(preview_frame, html=HtmlPages.preview())
        self._preview_web.when_ready(self._on_preview_ready)

        self._editor_web = WebView(
            editor_frame,
            html=HtmlPages.editor(SAMPLE_MARKDOWN),
            ipc_handler=self._on_editor_ipc,
        )

    def _install_shortcuts(self) -> None:
        wrap = EditorShortcutBindings.wrap
        EditorShortcutBindings.install(
            self.root,
            [
                (EditorShortcutBindings.UNDO, wrap(self._undo)),
                (EditorShortcutBindings.REDO, wrap(self._redo)),
                (EditorShortcutBindings.CUT, wrap(self._cut)),
                (EditorShortcutBindings.COPY, wrap(self._copy)),
                (EditorShortcutBindings.PASTE, wrap(self._paste)),
            ],
        )

    def _eval_editor(self, script: str) -> None:
        if not self._editor_ready or self._editor_web is None:
            return
        self._editor_web.focus()
        self._editor_web.eval_js(script)

    def _undo(self) -> None:
        self._eval_editor("window.editorUndo && window.editorUndo();")

    def _redo(self) -> None:
        self._eval_editor("window.editorRedo && window.editorRedo();")

    def _cut(self) -> None:
        self._eval_editor("window.editorCut && window.editorCut();")

    def _copy(self) -> None:
        self._eval_editor("window.editorCopy && window.editorCopy();")

    def _paste(self) -> None:
        if not self._editor_ready or self._editor_web is None:
            return
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            return
        self._editor_web.focus()
        self._editor_web.eval_js(f"window.editorPasteText({json.dumps(text)});")

    def _toggle_minimap(self) -> None:
        enabled = self._minimap_var.get()
        self._eval_editor(f"window.editorSetMinimap({json.dumps(enabled)});")

    def _push_preview_now(self, markdown: str) -> None:
        self._last_markdown = markdown
        if self._preview_ready and self._preview_web is not None:
            self._preview_web.eval_js(f"window.setMarkdown({json.dumps(markdown)});")

    def _schedule_preview(self, markdown: str) -> None:
        self._last_markdown = markdown
        if self._preview_after_id is not None:
            self.root.after_cancel(self._preview_after_id)
        self._preview_after_id = self.root.after(
            PREVIEW_DEBOUNCE_MS, self._flush_preview
        )

    def _flush_preview(self) -> None:
        self._preview_after_id = None
        self._push_preview_now(self._last_markdown)

    def _on_preview_ready(self) -> None:
        self._preview_ready = True
        self._push_preview_now(self._last_markdown)

    def _on_editor_ipc(self, message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return
        if data.get("type") == "ready":
            self._editor_ready = True
            self._toggle_minimap()
        elif data.get("type") == "markdown":
            self._schedule_preview(data.get("text", ""))


def main() -> None:
    MarkdownEditorDemo().run()


if __name__ == "__main__":
    main()
