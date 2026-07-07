"""Demo: Monaco markdown editor (left) + live preview (right) via PanedWindow.

The editor pane uses a single Monaco instance with VS Code-style document tabs
(``createModel`` / ``setModel``). The preview pane follows the active tab.

Requires network access: Monaco Editor, marked, and highlight.js load from CDN.
"""

from __future__ import annotations

import ctypes
import json
import sys
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox

from tkwry import PageLoadEvent, WebView

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
- VS Code-style document tabs in a single Monaco editor
- Dark / light appearance for editor and preview (independently)
- Two WebViews in one `PanedWindow`

> *Tip:* Try adding a table or a blockquote.
"""

MONACO_VERSION = "0.52.2"
PREVIEW_DEBOUNCE_MS = 80


def _parse_eval_json_object(result: str) -> dict | None:
    """Parse ``eval_js_with_callback`` results that may be JSON-encoded twice."""
    if not result or result == "null":
        return None
    try:
        parsed = json.loads(result)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, str):
        try:
            again = json.loads(parsed)
        except json.JSONDecodeError:
            return None
        return again if isinstance(again, dict) else None
    return None


class HtmlPages:
    """Embedded WebView HTML for the editor and preview panes."""

    @staticmethod
    def editor(initial_tabs: list[dict[str, str]]) -> str:
        initial_tabs_json = json.dumps(initial_tabs)
        return f"""\
<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8" />
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0;
      height: 100%;
      overflow: hidden;
      background: #1e1e1e;
      color: #cccccc;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    #workbench {{
      display: flex;
      flex-direction: column;
      height: 100%;
    }}
    #tab-bar {{
      display: flex;
      align-items: stretch;
      height: 35px;
      background: #252526;
      border-bottom: 1px solid #1e1e1e;
      flex-shrink: 0;
    }}
    #tabs {{
      display: flex;
      flex: 1;
      min-width: 0;
      overflow-x: auto;
      overflow-y: hidden;
      scrollbar-width: thin;
    }}
    #tabs::-webkit-scrollbar {{
      height: 4px;
    }}
    #tabs::-webkit-scrollbar-thumb {{
      background: #424242;
      border-radius: 2px;
    }}
    .tab {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      max-width: 200px;
      padding: 0 8px 0 10px;
      background: #2d2d2d;
      border-right: 1px solid #1e1e1e;
      cursor: pointer;
      user-select: none;
      flex-shrink: 0;
    }}
    .tab:hover {{
      background: #1e1e1e;
    }}
    .tab.active {{
      background: #1e1e1e;
      border-top: 1px solid #007acc;
      padding-top: 0;
    }}
    .tab-label {{
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 13px;
      line-height: 35px;
    }}
    .tab-dirty {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #cccccc;
      flex-shrink: 0;
      transition: opacity 0.1s ease;
    }}
    .tab:hover .tab-dirty,
    .tab.active .tab-dirty {{
      opacity: 0;
      width: 0;
      margin: 0;
    }}
    .tab-actions {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 22px;
      height: 22px;
      flex-shrink: 0;
      margin-right: -4px;
    }}
    .tab-close {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 22px;
      height: 22px;
      border: none;
      border-radius: 4px;
      background: transparent;
      color: #cccccc;
      padding: 0;
      cursor: pointer;
      flex-shrink: 0;
      opacity: 0;
      transition: opacity 0.1s ease, background 0.1s ease;
    }}
    .tab:hover .tab-close,
    .tab.active .tab-close {{
      opacity: 1;
    }}
    .tab-close:hover {{
      background: rgba(255, 255, 255, 0.1);
      color: #ffffff;
    }}
    .tab-close:disabled {{
      opacity: 0.35;
      cursor: default;
    }}
    .tab-close:disabled:hover {{
      background: transparent;
      color: #cccccc;
    }}
    .tab-close svg {{
      display: block;
      width: 16px;
      height: 16px;
      fill: currentColor;
    }}
    #new-tab {{
      width: 32px;
      border: none;
      border-left: 1px solid #1e1e1e;
      background: #252526;
      color: #cccccc;
      font-size: 18px;
      line-height: 1;
      cursor: pointer;
      flex-shrink: 0;
    }}
    #new-tab:hover {{
      background: #2a2d2e;
    }}
    #editor {{
      flex: 1;
      min-height: 0;
      width: 100%;
    }}
    html[data-theme="light"] body {{
      background: #f3f3f3;
      color: #333333;
    }}
    html[data-theme="light"] #tab-bar {{
      background: #ececec;
      border-bottom-color: #d4d4d4;
    }}
    html[data-theme="light"] #tabs::-webkit-scrollbar-thumb {{
      background: #c8c8c8;
    }}
    html[data-theme="light"] .tab {{
      background: #e8e8e8;
      border-right-color: #d4d4d4;
    }}
    html[data-theme="light"] .tab:hover {{
      background: #f3f3f3;
    }}
    html[data-theme="light"] .tab.active {{
      background: #ffffff;
      border-top-color: #0078d4;
    }}
    html[data-theme="light"] .tab-dirty {{
      background: #424242;
    }}
    html[data-theme="light"] .tab-close {{
      color: #424242;
    }}
    html[data-theme="light"] .tab-close:hover {{
      background: rgba(0, 0, 0, 0.08);
      color: #000000;
    }}
    html[data-theme="light"] #new-tab {{
      background: #ececec;
      border-left-color: #d4d4d4;
      color: #424242;
    }}
    html[data-theme="light"] #new-tab:hover {{
      background: #e0e0e0;
    }}
  </style>
</head>
<body>
  <div id="workbench">
    <div id="tab-bar">
      <div id="tabs"></div>
      <button id="new-tab" type="button" title="New tab">+</button>
    </div>
    <div id="editor"></div>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/monaco-editor@{MONACO_VERSION}/min/vs/loader.js"></script>
  <script>
    const INITIAL_TABS = {initial_tabs_json};

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
      const tabs = [];
      let activeTabId = null;
      let nextTabId = 1;
      let untitledSerial = 0;

      const editor = monaco.editor.create(document.getElementById("editor"), {{
        language: "markdown",
        theme: "vs-dark",
        fontSize: 14,
        lineNumbers: "on",
        minimap: {{ enabled: false }},
        wordWrap: "on",
        scrollBeyondLastLine: false,
        automaticLayout: true,
        contextmenu: false,
        fixedOverflowWidgets: true,
        find: {{
          addExtraSpaceOnTop: false,
        }},
      }});

      document.addEventListener("contextmenu", (event) => event.preventDefault());

      function nextUntitledTitle() {{
        untitledSerial += 1;
        return `Untitled ${{untitledSerial}}`;
      }}

      function tabById(id) {{
        return tabs.find((tab) => tab.id === id) || null;
      }}

      function activeTab() {{
        return activeTabId ? tabById(activeTabId) : null;
      }}

      function isDirty(tab) {{
        return tab.model.getValue() !== tab.baseline;
      }}

      function renderTabBar() {{
        const container = document.getElementById("tabs");
        const closePath =
          "M8 8.707l3.646 3.647.708-.707L8.707 8l3.647-3.646-.707-.708" +
          "L8 7.293 4.354 3.646l-.707.708L7.293 8l-3.646 3.646.707.708L8 8.707z";
        const closeIcon =
          '<svg viewBox="0 0 16 16" aria-hidden="true">' +
          '<path d="' + closePath + '"/>' +
          "</svg>";
        container.replaceChildren();
        for (const tab of tabs) {{
          const el = document.createElement("div");
          el.className = "tab" + (tab.id === activeTabId ? " active" : "");
          el.dataset.id = tab.id;

          if (isDirty(tab)) {{
            const dot = document.createElement("span");
            dot.className = "tab-dirty";
            dot.title = "Modified";
            el.appendChild(dot);
          }}

          const label = document.createElement("span");
          label.className = "tab-label";
          label.textContent = tab.title;
          el.appendChild(label);

          const actions = document.createElement("span");
          actions.className = "tab-actions";

          const close = document.createElement("button");
          close.className = "tab-close";
          close.type = "button";
          close.title = "Close";
          close.innerHTML = closeIcon;
          if (tabs.length <= 1) {{
            close.disabled = true;
          }}
          close.addEventListener("click", (event) => {{
            event.stopPropagation();
            if (tabs.length > 1) closeTab(tab.id);
          }});
          actions.appendChild(close);
          el.appendChild(actions);

          el.addEventListener("click", () => switchTab(tab.id));
          el.addEventListener("mousedown", (event) => {{
            if (event.button === 1 && tabs.length > 1) {{
              event.preventDefault();
              closeTab(tab.id);
            }}
          }});

          container.appendChild(el);
        }}
      }}

      function switchTab(id) {{
        const tab = tabById(id);
        if (!tab || activeTabId === id) return;
        activeTabId = id;
        editor.setModel(tab.model);
        renderTabBar();
        sendMarkdown(tab.model.getValue());
      }}

      function openTab(title, content, path) {{
        const id = String(nextTabId++);
        const uri = monaco.Uri.parse(`inmemory://${{id}}/${{title}}`);
        const model = monaco.editor.createModel(content, "markdown", uri);
        const tab = {{
          id,
          title,
          model,
          baseline: content,
          path: path || null,
        }};
        tabs.push(tab);
        activeTabId = id;
        editor.setModel(model);
        renderTabBar();
        sendMarkdown(model.getValue());
        return id;
      }}

      function closeTab(id) {{
        if (tabs.length <= 1) return;
        const index = tabs.findIndex((tab) => tab.id === id);
        if (index < 0) return;
        const closing = tabs[index];
        const wasActive = activeTabId === id;
        tabs.splice(index, 1);
        closing.model.dispose();
        if (wasActive) {{
          const next = tabs[Math.min(index, tabs.length - 1)];
          activeTabId = next.id;
          editor.setModel(next.model);
          sendMarkdown(next.model.getValue());
        }}
        renderTabBar();
      }}

      window.editorNewTab = function (title, content) {{
        const tabTitle = title || nextUntitledTitle();
        const tabContent = content == null ? "" : content;
        openTab(tabTitle, tabContent);
      }};

      window.editorCloseActiveTab = function () {{
        if (activeTabId) closeTab(activeTabId);
      }};

      window.editorGetActiveTabInfo = function () {{
        const tab = activeTab();
        if (!tab) return null;
        return {{
          id: tab.id,
          title: tab.title,
          content: tab.model.getValue(),
          path: tab.path,
        }};
      }};

      window.editorGetActiveTabMeta = function () {{
        const tab = activeTab();
        if (!tab) return null;
        return {{
          title: tab.title,
          path: tab.path,
        }};
      }};

      window.editorMarkSaved = function (path, title) {{
        const tab = activeTab();
        if (!tab) return;
        tab.path = path;
        if (title) tab.title = title;
        tab.baseline = tab.model.getValue();
        renderTabBar();
      }};

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
      function runEditorAction(candidates) {{
        editor.focus();
        requestAnimationFrame(() => {{
          for (const id of candidates) {{
            const action = editor.getAction(id);
            if (action) {{
              action.run();
              return;
            }}
          }}
          if (candidates.length > 0) {{
            editor.trigger("editor", candidates[0], null);
          }}
        }});
      }}

      window.editorFind = function () {{
        runEditorAction(["actions.find", "editor.action.startFindAction"]);
      }};
      window.editorReplace = function () {{
        runEditorAction([
          "editor.action.startFindReplaceAction",
          "actions.findWithReplace",
        ]);
      }};
      window.editorSetMinimap = function (enabled) {{
        editor.updateOptions({{ minimap: {{ enabled: !!enabled }} }});
      }};
      window.editorSetTheme = function (mode) {{
        const dark = mode !== "light";
        document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
        monaco.editor.setTheme(dark ? "vs-dark" : "vs");
      }};

      const IS_MAC = /Mac|iPhone|iPod|iPad/.test(navigator.userAgent);
      editor.addCommand(
        monaco.KeyMod.CtrlCmd | monaco.KeyMod.Alt | monaco.KeyCode.KeyF,
        () => window.editorReplace(),
      );
      if (!IS_MAC) {{
        editor.addCommand(
          monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyH,
          () => window.editorReplace(),
        );
      }}
      document.addEventListener(
        "keydown",
        (event) => {{
          const key = event.key.toLowerCase();
          if (
            IS_MAC &&
            event.metaKey &&
            event.altKey &&
            !event.shiftKey &&
            !event.ctrlKey &&
            key === "f"
          ) {{
            event.preventDefault();
            event.stopPropagation();
            window.editorReplace();
            return;
          }}
          if (
            !IS_MAC &&
            event.ctrlKey &&
            !event.metaKey &&
            !event.altKey &&
            !event.shiftKey &&
            key === "h"
          ) {{
            event.preventDefault();
            event.stopPropagation();
            window.editorReplace();
          }}
        }},
        true,
      );

      editor.onDidChangeModelContent(() => {{
        const tab = activeTab();
        if (!tab) return;
        renderTabBar();
        sendMarkdown(tab.model.getValue());
      }});

      document.getElementById("new-tab").addEventListener("click", () => {{
        window.editorNewTab();
      }});

      if (INITIAL_TABS.length === 0) {{
        openTab("Untitled 1", "");
      }} else {{
        for (const spec of INITIAL_TABS) {{
          openTab(spec.title, spec.content || "", spec.path || null);
        }}
        switchTab(tabs[0].id);
      }}

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
    id="hljs-theme"
    rel="stylesheet"
    href="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.11.1/build/styles/github.min.css"
  />
  <style>
    * { box-sizing: border-box; }
    html, body {
      margin: 0;
      height: 100%;
      overflow: auto;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial,
        sans-serif;
      font-size: 16px;
      line-height: 1.6;
    }
    body.theme-light {
      background: #ffffff;
      color: #24292f;
    }
    body.theme-dark {
      background: #0d1117;
      color: #e6edf3;
    }
    #preview {
      max-width: 48rem;
      margin: 0 auto;
      padding: 24px 28px 48px;
    }
    body.theme-light #preview h1,
    body.theme-light #preview h2,
    body.theme-light #preview h3 {
      border-bottom: 1px solid #d8dee4;
      padding-bottom: 0.3em;
      margin-top: 1.5em;
      margin-bottom: 16px;
      line-height: 1.25;
    }
    body.theme-dark #preview h1,
    body.theme-dark #preview h2,
    body.theme-dark #preview h3 {
      border-bottom: 1px solid #30363d;
      padding-bottom: 0.3em;
      margin-top: 1.5em;
      margin-bottom: 16px;
      line-height: 1.25;
    }
    #preview h1 { font-size: 2em; border-bottom-width: 1px; margin-top: 0; }
    #preview h2 { font-size: 1.5em; }
    #preview h3 { font-size: 1.25em; border-bottom: none; }
    body.theme-light #preview code {
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas,
        monospace;
      font-size: 0.9em;
      background: #f6f8fa;
      padding: 0.2em 0.4em;
      border-radius: 6px;
    }
    body.theme-dark #preview code {
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas,
        monospace;
      font-size: 0.9em;
      background: #161b22;
      padding: 0.2em 0.4em;
      border-radius: 6px;
    }
    body.theme-light #preview pre {
      background: #f6f8fa;
      border-radius: 6px;
      padding: 16px;
      overflow: auto;
      line-height: 1.45;
    }
    body.theme-dark #preview pre {
      background: #161b22;
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
    body.theme-light #preview blockquote {
      margin: 0;
      padding: 0 1em;
      color: #57606a;
      border-left: 0.25em solid #d0d7de;
    }
    body.theme-dark #preview blockquote {
      margin: 0;
      padding: 0 1em;
      color: #8b949e;
      border-left: 0.25em solid #30363d;
    }
    #preview ul, #preview ol { padding-left: 2em; }
    body.theme-light #preview a { color: #0969da; text-decoration: none; }
    body.theme-dark #preview a { color: #4493f8; text-decoration: none; }
    #preview a:hover { text-decoration: underline; }
    #preview table {
      border-collapse: collapse;
      width: 100%;
      margin: 16px 0;
    }
    body.theme-light #preview th,
    body.theme-light #preview td {
      border: 1px solid #d0d7de;
      padding: 6px 13px;
    }
    body.theme-dark #preview th,
    body.theme-dark #preview td {
      border: 1px solid #30363d;
      padding: 6px 13px;
    }
    body.theme-light #preview th { background: #f6f8fa; }
    body.theme-dark #preview th { background: #161b22; }
    body.theme-light .empty { color: #8b949e; }
    body.theme-dark .empty { color: #7d8590; }
    .empty {
      font-style: italic;
      text-align: center;
      padding-top: 40vh;
    }
  </style>
</head>
<body class="theme-light">
  <article id="preview"><p class="empty">Waiting for markdown…</p></article>
  <script src="https://cdn.jsdelivr.net/npm/marked@15.0.7/marked.min.js"></script>
  <script src="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.11.1/build/highlight.min.js"></script>
  <script>
    const HLJS_LIGHT =
      "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.11.1/build/styles/github.min.css";
    const HLJS_DARK =
      "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.11.1/build/styles/github-dark.min.css";

    let lastMarkdown = "";

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
      lastMarkdown = text == null ? "" : text;
      const el = document.getElementById("preview");
      if (!lastMarkdown || !lastMarkdown.trim()) {
        el.innerHTML = '<p class="empty">(empty)</p>';
        return;
      }
      el.innerHTML = marked.parse(lastMarkdown);
    };

    window.setAppearance = function (mode) {
      const dark = mode === "dark";
      document.body.classList.toggle("theme-dark", dark);
      document.body.classList.toggle("theme-light", !dark);
      const themeLink = document.getElementById("hljs-theme");
      themeLink.href = dark ? HLJS_DARK : HLJS_LIGHT;
      window.setMarkdown(lastMarkdown);
    };

    document.addEventListener("contextmenu", (event) => event.preventDefault());
  </script>
</body>
</html>
"""


class SaveDialog:
    """Platform-native save panel (NSSavePanel on macOS, Tk elsewhere)."""

    @staticmethod
    def ask_path(
        *,
        parent: tk.Misc,
        title: str,
        path: str | None,
        suggested_name: str,
    ) -> str | None:
        if sys.platform == "darwin":
            return SaveDialog._ask_path_macos(
                title=title,
                path=path,
                suggested_name=suggested_name,
                parent=parent,
            )
        return SaveDialog._ask_path_tk(
            parent=parent,
            title=title,
            path=path,
            suggested_name=suggested_name,
        )

    @staticmethod
    def _ask_path_macos(
        *,
        title: str,
        path: str | None,
        suggested_name: str,
        parent: tk.Misc,
    ) -> str | None:
        try:
            from AppKit import NSOKButton, NSSavePanel
            from Foundation import NSURL
        except ImportError:
            return SaveDialog._ask_path_tk(
                parent=parent,
                title=title,
                path=path,
                suggested_name=suggested_name,
            )

        panel = NSSavePanel.savePanel()
        panel.setTitle_(title)
        panel.setCanCreateDirectories_(True)
        panel.setAllowsOtherFileTypes_(True)
        panel.setAllowedFileTypes_(["md", "markdown", "txt"])
        panel.setExtensionHidden_(False)

        if path:
            file_path = Path(path)
            panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(file_path.parent)))
            panel.setNameFieldStringValue_(file_path.name)
        else:
            panel.setNameFieldStringValue_(suggested_name)

        if panel.runModal() != NSOKButton:
            return None
        url = panel.URL()
        if url is None:
            return None
        return str(url.path())

    @staticmethod
    def _ask_path_tk(
        *,
        parent: tk.Misc,
        title: str,
        path: str | None,
        suggested_name: str,
    ) -> str | None:
        options: dict[str, object] = {
            "parent": parent,
            "title": title,
            "defaultextension": ".md",
            "filetypes": [
                ("Markdown", "*.md"),
                ("Text", "*.txt"),
                ("All files", "*.*"),
            ],
        }
        if path:
            file_path = Path(path)
            options["initialdir"] = str(file_path.parent)
            options["initialfile"] = file_path.name
        else:
            options["initialfile"] = suggested_name
        chosen = filedialog.asksaveasfilename(**options)
        return chosen or None


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


class _Win32Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _Win32UahMenu(ctypes.Structure):
    _fields_ = [
        ("hmenu", ctypes.c_void_p),
        ("hdc", ctypes.c_void_p),
        ("dwFlags", ctypes.c_uint32),
    ]


class _Win32UahMenuItem(ctypes.Structure):
    _fields_ = [
        ("iPosition", ctypes.c_int),
        ("_padding", ctypes.c_int),
        ("umim", ctypes.c_uint32 * 4),
        ("umpm", ctypes.c_uint32 * 5),
    ]


class _Win32DrawItemStruct(ctypes.Structure):
    _fields_ = [
        ("CtlType", ctypes.c_uint32),
        ("CtlID", ctypes.c_uint32),
        ("itemID", ctypes.c_uint32),
        ("itemAction", ctypes.c_uint32),
        ("itemState", ctypes.c_uint32),
        ("_padding", ctypes.c_uint32),
        ("hwndItem", ctypes.c_void_p),
        ("hDC", ctypes.c_void_p),
        ("rcItem", _Win32Rect),
        ("itemData", ctypes.c_size_t),
    ]


class _Win32UahDrawMenuItem(ctypes.Structure):
    _fields_ = [
        ("dis", _Win32DrawItemStruct),
        ("um", _Win32UahMenu),
        ("umi", _Win32UahMenuItem),
    ]


class _Win32MenuBarInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("rcBar", _Win32Rect),
        ("hMenu", ctypes.c_void_p),
        ("hwndMenu", ctypes.c_void_p),
        ("fBarHovered", ctypes.c_int),
        ("fFocused", ctypes.c_int),
    ]


class _Win32MenuItemInfoW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("fMask", ctypes.c_uint32),
        ("fType", ctypes.c_uint32),
        ("fState", ctypes.c_uint32),
        ("wID", ctypes.c_uint32),
        ("hSubMenu", ctypes.c_void_p),
        ("hbmpChecked", ctypes.c_void_p),
        ("hbmpUnchecked", ctypes.c_void_p),
        ("dwItemData", ctypes.c_size_t),
        ("dwTypeData", ctypes.c_wchar_p),
        ("cch", ctypes.c_uint32),
        ("hbmpItem", ctypes.c_void_p),
    ]


class Win32MenuBarTheme:
    """Paint the native Win32 menu bar via undocumented UAH messages."""

    WM_UAHDRAWMENU = 0x0091
    WM_UAHDRAWMENUITEM = 0x0092
    WM_UAHMEASUREMENUITEM = 0x0094
    WM_NCPAINT = 0x0085
    WM_NCACTIVATE = 0x0086
    GWLP_WNDPROC = -4
    OBJID_MENU = -3
    MIIM_STRING = 0x00000040
    ODS_INACTIVE = 0x0080
    ODS_DEFAULT = 0x0020
    ODS_HOTLIGHT = 0x0040
    ODS_SELECTED = 0x0001
    ODS_GRAYED = 0x0002
    ODS_DISABLED = 0x0004
    ODS_NOACCEL = 0x0100
    DT_CENTER = 0x00000001
    DT_SINGLELINE = 0x00000020
    DT_VCENTER = 0x00000004
    DT_HIDEPREFIX = 0x00100000
    TRANSPARENT = 1

    _dark = True
    _hooked_hwnds: set[int] = set()
    _old_wndprocs: dict[int, int] = {}
    _wndproc_ref = None
    _brush_bar = 0
    _brush_item = 0
    _brush_item_hot = 0
    _brush_item_selected = 0
    _text_color = 0x00F0F0F0
    _text_disabled = 0x00888888
    _api_configured = False

    @classmethod
    def _configure_api(cls) -> None:
        if cls._api_configured:
            return
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        user32.FillRect.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_Win32Rect),
            ctypes.c_void_p,
        ]
        user32.FillRect.restype = ctypes.c_int
        user32.DrawTextW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_int,
            ctypes.POINTER(_Win32Rect),
            ctypes.c_uint,
        ]
        user32.DrawTextW.restype = ctypes.c_int
        user32.GetMenuBarInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.c_long,
            ctypes.c_long,
            ctypes.POINTER(_Win32MenuBarInfo),
        ]
        user32.GetMenuBarInfo.restype = ctypes.c_int
        user32.GetMenuItemInfoW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_bool,
            ctypes.POINTER(_Win32MenuItemInfoW),
        ]
        user32.GetMenuItemInfoW.restype = ctypes.c_int
        gdi32.SetBkMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
        gdi32.SetBkMode.restype = ctypes.c_int
        gdi32.SetTextColor.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        gdi32.SetTextColor.restype = ctypes.c_uint32
        cls._api_configured = True

    @classmethod
    def install(cls, window: tk.Misc) -> None:
        if sys.platform != "win32":
            return
        cls._configure_api()
        window.update_idletasks()
        hwnd = cls._window_hwnd(window)
        if not hwnd or hwnd in cls._hooked_hwnds:
            return

        if cls._wndproc_ref is None:
            cls._wndproc_ref = cls._wndproc_type()(cls._wndproc)

        set_long = cls._set_window_long_ptr()
        old_proc = set_long(hwnd, cls.GWLP_WNDPROC, cls._wndproc_ref)
        if not old_proc:
            return
        cls._old_wndprocs[hwnd] = old_proc
        cls._hooked_hwnds.add(hwnd)

    @classmethod
    def apply(cls, window: tk.Misc, *, dark: bool) -> None:
        if sys.platform != "win32":
            return
        cls._configure_api()
        cls._dark = dark
        cls._ensure_brushes(dark)
        cls.install(window)
        hwnd = cls._window_hwnd(window)
        if hwnd:
            import ctypes as ct

            ct.windll.user32.RedrawWindow(hwnd, None, None, 0x0401)

    @classmethod
    def _ensure_brushes(cls, dark: bool) -> None:
        import ctypes as ct

        gdi32 = ct.windll.gdi32
        for name in (
            "_brush_bar",
            "_brush_item",
            "_brush_item_hot",
            "_brush_item_selected",
        ):
            brush = getattr(cls, name)
            if brush:
                gdi32.DeleteObject(brush)
                setattr(cls, name, 0)
        if dark:
            palette = {
                "_brush_bar": "#2b2b2b",
                "_brush_item": "#2b2b2b",
                "_brush_item_hot": "#094771",
                "_brush_item_selected": "#0e639c",
            }
            cls._text_color = cls._hex_to_colorref("#f0f0f0")
            cls._text_disabled = cls._hex_to_colorref("#888888")
        else:
            palette = {
                "_brush_bar": "#f0f0f0",
                "_brush_item": "#f0f0f0",
                "_brush_item_hot": "#d9d9d9",
                "_brush_item_selected": "#cce4f7",
            }
            cls._text_color = cls._hex_to_colorref("#000000")
            cls._text_disabled = cls._hex_to_colorref("#888888")
        for name, hex_color in palette.items():
            brush = gdi32.CreateSolidBrush(cls._hex_to_colorref(hex_color))
            setattr(cls, name, brush)

    @classmethod
    def _wndproc_type(cls):
        import ctypes as ct

        result_type = ct.c_longlong if ct.sizeof(ct.c_void_p) == 8 else ct.c_long
        return ct.WINFUNCTYPE(
            result_type,
            ct.c_void_p,
            ct.c_uint,
            ct.c_size_t,
            ct.c_longlong,
        )

    @classmethod
    def _wndproc(cls, hwnd, msg, wparam, lparam):
        if msg == cls.WM_UAHDRAWMENU:
            if cls._draw_menu_bar(hwnd, lparam):
                return 1
        elif msg == cls.WM_UAHDRAWMENUITEM:
            if cls._draw_menu_item(lparam):
                return 1
        elif msg == cls.WM_UAHMEASUREMENUITEM:
            old_proc = cls._old_wndprocs.get(int(hwnd))
            if old_proc:
                return cls._call_window_proc(old_proc, hwnd, msg, wparam, lparam)
        old_proc = cls._old_wndprocs.get(int(hwnd))
        if not old_proc:
            return 0
        return cls._call_window_proc(old_proc, hwnd, msg, wparam, lparam)

    @classmethod
    def _draw_menu_bar(cls, hwnd, lparam: int) -> bool:
        import ctypes as ct

        uah_menu = ct.cast(lparam, ct.POINTER(_Win32UahMenu)).contents
        rect = cls._menubar_rect(hwnd)
        if rect is None or not uah_menu.hdc:
            return False
        ct.windll.user32.FillRect(
            ct.c_void_p(uah_menu.hdc),
            ct.byref(rect),
            ct.c_void_p(cls._brush_bar),
        )
        return True

    @classmethod
    def _draw_menu_item(cls, lparam: int) -> bool:
        import ctypes as ct

        item = ct.cast(lparam, ct.POINTER(_Win32UahDrawMenuItem)).contents
        state = item.dis.itemState
        if state & (cls.ODS_GRAYED | cls.ODS_DISABLED):
            brush = cls._brush_item
            text_color = cls._text_disabled
        elif state & cls.ODS_SELECTED:
            brush = cls._brush_item_selected
            text_color = cls._text_color
        elif state & cls.ODS_HOTLIGHT:
            brush = cls._brush_item_hot
            text_color = cls._text_color
        else:
            brush = cls._brush_item
            text_color = cls._text_color

        hdc = ct.c_void_p(item.um.hdc)
        if not hdc:
            return False
        user32 = ct.windll.user32
        gdi32 = ct.windll.gdi32
        user32.FillRect(hdc, ct.byref(item.dis.rcItem), ct.c_void_p(brush))
        label = cls._menu_item_text(int(item.um.hmenu), item.umi.iPosition)
        if not label:
            return True
        flags = cls.DT_CENTER | cls.DT_SINGLELINE | cls.DT_VCENTER
        if state & cls.ODS_NOACCEL:
            flags |= cls.DT_HIDEPREFIX
        gdi32.SetBkMode(hdc, cls.TRANSPARENT)
        gdi32.SetTextColor(hdc, text_color)
        buffer = ct.create_unicode_buffer(label)
        user32.DrawTextW(
            hdc,
            buffer,
            -1,
            ct.byref(item.dis.rcItem),
            flags,
        )
        return True

    @classmethod
    def _menu_item_text(cls, hmenu: int, position: int) -> str:
        import ctypes as ct

        buffer = ct.create_unicode_buffer(256)
        item_info = _Win32MenuItemInfoW()
        item_info.cbSize = ct.sizeof(_Win32MenuItemInfoW)
        item_info.fMask = cls.MIIM_STRING
        item_info.dwTypeData = ct.cast(buffer, ct.c_wchar_p)
        item_info.cch = len(buffer) - 1
        if not ct.windll.user32.GetMenuItemInfoW(
            hmenu, position, True, ct.byref(item_info)
        ):
            return ""
        return buffer.value

    @classmethod
    def _menubar_rect(cls, hwnd: int):
        import ctypes as ct

        info = _Win32MenuBarInfo()
        info.cbSize = ct.sizeof(_Win32MenuBarInfo)
        if not ct.windll.user32.GetMenuBarInfo(hwnd, cls.OBJID_MENU, 0, ct.byref(info)):
            return None
        window_rect = _Win32Rect()
        ct.windll.user32.GetWindowRect(hwnd, ct.byref(window_rect))
        rect = info.rcBar
        rect.left -= window_rect.left
        rect.right -= window_rect.left
        rect.top -= window_rect.top
        rect.bottom -= window_rect.top
        return rect

    @classmethod
    def _set_window_long_ptr(cls):
        import ctypes as ct

        if ct.sizeof(ct.c_void_p) == 8:
            setter = ct.windll.user32.SetWindowLongPtrW
            setter.argtypes = [ct.c_void_p, ct.c_int, ct.c_void_p]
            setter.restype = ct.c_longlong

            def set_long(hwnd: int, index: int, value) -> int:
                return int(setter(hwnd, index, value))

            return set_long
        setter = ct.windll.user32.SetWindowLongW
        setter.argtypes = [ct.c_void_p, ct.c_int, ct.c_void_p]
        setter.restype = ct.c_long

        def set_long(hwnd: int, index: int, value) -> int:
            return int(setter(hwnd, index, value))

        return set_long

    @classmethod
    def _call_window_proc(cls, old_proc: int, hwnd, msg, wparam, lparam):
        import ctypes as ct

        caller = ct.WINFUNCTYPE(
            ct.c_longlong if ct.sizeof(ct.c_void_p) == 8 else ct.c_long,
            ct.c_void_p,
            ct.c_uint,
            ct.c_size_t,
            ct.c_longlong,
            use_last_error=True,
        )(old_proc)
        return caller(hwnd, msg, wparam, lparam)

    @staticmethod
    def _window_hwnd(window: tk.Misc) -> int:
        import ctypes as ct

        try:
            window.update_idletasks()
            widget_id = window.winfo_id()
            hwnd = ct.windll.user32.GetParent(widget_id)
            if not hwnd:
                hwnd = widget_id
            return int(hwnd)
        except (AttributeError, OSError, tk.TclError, TypeError, ValueError):
            return 0

    @staticmethod
    def _hex_to_colorref(hex_color: str) -> int:
        red = int(hex_color[1:3], 16)
        green = int(hex_color[3:5], 16)
        blue = int(hex_color[5:7], 16)
        return (blue << 16) | (green << 8) | red


class WindowTitleBar:
    """Sync native window chrome and menus with editor dark/light mode."""

    _MENU_DARK = {
        "background": "#2b2b2b",
        "foreground": "#f0f0f0",
        "activebackground": "#094771",
        "activeforeground": "#ffffff",
        "selectcolor": "#f0f0f0",
    }
    _MENU_LIGHT = {
        "background": "#f0f0f0",
        "foreground": "#000000",
        "activebackground": "#d9d9d9",
        "activeforeground": "#000000",
        "selectcolor": "#000000",
    }
    _WIN_MENU_BAR_DARK = "#2b2b2b"
    _WIN_MENU_BAR_LIGHT = "#f0f0f0"
    _WIN_CHROME_DARK = "#1e1e1e"
    _WIN_CHROME_LIGHT = "#f3f3f3"
    _WIN_SASH_DARK = "#404040"
    _WIN_SASH_LIGHT = "#c0c0c0"
    _DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
    _DWMWA_BORDER_COLOR = 34
    _DWMWA_CAPTION_COLOR = 35
    _DWMWA_TEXT_COLOR = 36
    _DWM_COLOR_DEFAULT = 0xFFFFFFFF
    _MN_GETHMENU = 0x01E1
    _MIM_BACKGROUND = 0x00000002
    _MIM_APPLYTOSUBMENUS = 0x80000000

    _menu_dark = True
    _win_menu_brush = 0
    _win_popup_menus: set[int] = set()
    _windows_menu_hooks_installed = False

    @staticmethod
    def prepare_startup(*, dark: bool) -> None:
        if sys.platform == "win32":
            WindowTitleBar._set_preferred_app_mode(dark)

    @staticmethod
    def install(window: tk.Misc, menubar: tk.Menu | None) -> None:
        if sys.platform != "win32" or menubar is None:
            return
        if WindowTitleBar._windows_menu_hooks_installed:
            return
        WindowTitleBar._windows_menu_hooks_installed = True

        def on_menu_select(_event: tk.Event) -> None:
            WindowTitleBar._style_windows_popup_menus()

        window.bind("<<MenuSelect>>", on_menu_select, add="+")
        menubar.bind("<<MenuSelect>>", on_menu_select, add="+")
        Win32MenuBarTheme.install(window)

    @staticmethod
    def apply(
        window: tk.Misc,
        *,
        dark: bool,
        menubar: tk.Menu | None = None,
        paned: tk.PanedWindow | None = None,
    ) -> None:
        WindowTitleBar._menu_dark = dark
        WindowTitleBar._win_popup_menus.clear()
        if sys.platform == "win32":
            WindowTitleBar._apply_windows(window, dark=dark, paned=paned)
        elif sys.platform == "darwin":
            WindowTitleBar._apply_macos(window, dark=dark)
        if menubar is not None:
            WindowTitleBar._apply_menus(window, menubar, dark=dark)

    @staticmethod
    def _apply_windows(
        window: tk.Misc,
        *,
        dark: bool,
        paned: tk.PanedWindow | None = None,
    ) -> None:
        try:
            window.update_idletasks()
            if not window.winfo_exists():
                return
            hwnd = WindowTitleBar._window_hwnd(window)
            if not hwnd:
                return
            WindowTitleBar._set_preferred_app_mode(dark)
            WindowTitleBar._allow_dark_mode_for_window(hwnd, dark=dark)
            WindowTitleBar._apply_windows_dwm(hwnd, dark=dark)
            WindowTitleBar._flush_menu_themes()
            Win32MenuBarTheme.apply(window, dark=dark)
            chrome_bg = (
                WindowTitleBar._WIN_CHROME_DARK
                if dark
                else WindowTitleBar._WIN_CHROME_LIGHT
            )
            window.configure(bg=chrome_bg)
            if paned is not None:
                paned.configure(
                    bg=(
                        WindowTitleBar._WIN_SASH_DARK
                        if dark
                        else WindowTitleBar._WIN_SASH_LIGHT
                    )
                )
            import ctypes as ct

            ct.windll.user32.RedrawWindow(hwnd, None, None, 0x0401)
        except (AttributeError, OSError, tk.TclError):
            return

    @staticmethod
    def _apply_macos(window: tk.Misc, *, dark: bool) -> None:
        try:
            from AppKit import (
                NSAppearance,
                NSAppearanceNameAqua,
                NSAppearanceNameDarkAqua,
                NSApplication,
            )

            name = NSAppearanceNameDarkAqua if dark else NSAppearanceNameAqua
            appearance = NSAppearance.appearanceNamed_(name)
            if appearance is not None:
                app = NSApplication.sharedApplication()
                app.setAppearance_(appearance)
                for ns_window in list(app.windows()) or []:
                    ns_window.setAppearance_(appearance)
        except ImportError:
            pass

        appearance_attr = "darkaqua" if dark else "aqua"
        try:
            window.wm_attributes("-appearance", appearance_attr)
        except tk.TclError:
            if not dark:
                return
            try:
                window.wm_attributes("-appearance", "dark")
            except tk.TclError:
                return

    @staticmethod
    def _apply_menus(window: tk.Misc, menubar: tk.Menu, *, dark: bool) -> None:
        if sys.platform == "darwin":
            return
        options = WindowTitleBar._MENU_DARK if dark else WindowTitleBar._MENU_LIGHT
        if sys.platform == "win32":
            WindowTitleBar._apply_windows_menus(window, dark=dark)
            WindowTitleBar._configure_menu_tree(menubar, options)
            return
        WindowTitleBar._configure_menu_tree(menubar, options)

    @staticmethod
    def _apply_windows_menus(window: tk.Misc, *, dark: bool) -> None:
        WindowTitleBar._set_preferred_app_mode(dark)
        hwnd = WindowTitleBar._window_hwnd(window)
        if not hwnd:
            return
        color = (
            WindowTitleBar._WIN_MENU_BAR_DARK
            if dark
            else WindowTitleBar._WIN_MENU_BAR_LIGHT
        )
        import ctypes as ct

        hmenu = ct.windll.user32.GetMenu(hwnd)
        if hmenu:
            WindowTitleBar._set_menu_background(hmenu, color)
            ct.windll.user32.DrawMenuBar(hwnd)

    @staticmethod
    def _apply_windows_dwm(hwnd: int, *, dark: bool) -> None:
        import ctypes as ct

        dwm = ct.windll.dwmapi.DwmSetWindowAttribute
        size = ct.sizeof(ct.c_int)
        dark_value = ct.c_int(1 if dark else 0)
        if (
            dwm(
                hwnd,
                WindowTitleBar._DWMWA_USE_IMMERSIVE_DARK_MODE,
                ct.byref(dark_value),
                size,
            )
            != 0
        ):
            dwm(
                hwnd,
                WindowTitleBar._DWMWA_USE_IMMERSIVE_DARK_MODE_OLD,
                ct.byref(dark_value),
                size,
            )
        if dark:
            border = ct.c_int(
                WindowTitleBar._hex_to_colorref(WindowTitleBar._WIN_CHROME_DARK)
            )
            caption = ct.c_int(
                WindowTitleBar._hex_to_colorref(WindowTitleBar._WIN_CHROME_DARK)
            )
            text = ct.c_int(WindowTitleBar._hex_to_colorref("#ffffff"))
        else:
            border = caption = text = ct.c_int(WindowTitleBar._DWM_COLOR_DEFAULT)
        for attribute, value in (
            (WindowTitleBar._DWMWA_BORDER_COLOR, border),
            (WindowTitleBar._DWMWA_CAPTION_COLOR, caption),
            (WindowTitleBar._DWMWA_TEXT_COLOR, text),
        ):
            dwm(hwnd, attribute, ct.byref(value), size)

    @staticmethod
    def _allow_dark_mode_for_window(hwnd: int, *, dark: bool) -> None:
        import ctypes as ct

        try:
            allow_dark = ct.WINFUNCTYPE(ct.c_bool, ct.c_void_p, ct.c_bool)(
                (133, ct.windll.uxtheme)
            )
            allow_dark(hwnd, dark)
        except (AttributeError, OSError):
            return

    @staticmethod
    def _flush_menu_themes() -> None:
        import ctypes as ct

        try:
            flush = ct.WINFUNCTYPE(None)((136, ct.windll.uxtheme))
            flush()
        except (AttributeError, OSError):
            return

    @staticmethod
    def _style_windows_popup_menus() -> None:
        if sys.platform != "win32":
            return
        import ctypes as ct

        color = (
            WindowTitleBar._WIN_MENU_BAR_DARK
            if WindowTitleBar._menu_dark
            else WindowTitleBar._WIN_MENU_BAR_LIGHT
        )
        hwnd = ct.windll.user32.FindWindowExW(None, None, "#32768", None)
        while hwnd:
            hmenu = ct.windll.user32.SendMessageW(
                hwnd, WindowTitleBar._MN_GETHMENU, 0, 0
            )
            if hmenu:
                WindowTitleBar._allow_dark_mode_for_window(
                    hwnd, dark=WindowTitleBar._menu_dark
                )
                WindowTitleBar._set_menu_background(hmenu, color)
                WindowTitleBar._win_popup_menus.add(hmenu)
            hwnd = ct.windll.user32.FindWindowExW(None, hwnd, "#32768", None)

    @staticmethod
    def _set_preferred_app_mode(dark: bool) -> None:
        import ctypes as ct

        try:
            set_mode = ct.WINFUNCTYPE(ct.c_int, ct.c_int)((135, ct.windll.uxtheme))
            set_mode(2 if dark else 3)
        except (AttributeError, OSError):
            return

    @staticmethod
    def _window_hwnd(window: tk.Misc) -> int:
        import ctypes as ct

        try:
            window.update_idletasks()
            widget_id = window.winfo_id()
            hwnd = ct.windll.user32.GetParent(widget_id)
            if not hwnd:
                hwnd = widget_id
            return int(hwnd)
        except (AttributeError, OSError, tk.TclError, TypeError, ValueError):
            return 0

    @staticmethod
    def _hex_to_colorref(hex_color: str) -> int:
        red = int(hex_color[1:3], 16)
        green = int(hex_color[3:5], 16)
        blue = int(hex_color[5:7], 16)
        return (blue << 16) | (green << 8) | red

    @staticmethod
    def _set_menu_background(hmenu: int, hex_color: str) -> None:
        import ctypes as ct

        class MENUINFO(ct.Structure):
            _fields_ = [
                ("cbSize", ct.c_uint32),
                ("fMask", ct.c_uint32),
                ("dwStyle", ct.c_uint32),
                ("cyMax", ct.c_uint32),
                ("hbrBack", ct.c_void_p),
                ("dwContextHelpID", ct.c_uint32),
                ("dwMenuData", ct.c_size_t),
            ]

        gdi32 = ct.windll.gdi32
        if WindowTitleBar._win_menu_brush:
            gdi32.DeleteObject(WindowTitleBar._win_menu_brush)
            WindowTitleBar._win_menu_brush = 0
        brush = gdi32.CreateSolidBrush(WindowTitleBar._hex_to_colorref(hex_color))
        if not brush:
            return
        WindowTitleBar._win_menu_brush = brush
        menu_info = MENUINFO()
        menu_info.cbSize = ct.sizeof(MENUINFO)
        menu_info.fMask = (
            WindowTitleBar._MIM_BACKGROUND | WindowTitleBar._MIM_APPLYTOSUBMENUS
        )
        menu_info.hbrBack = brush
        ct.windll.user32.SetMenuInfo(hmenu, ct.byref(menu_info))

    @staticmethod
    def _configure_menu_tree(menu: tk.Menu, options: dict[str, str]) -> None:
        selectcolor = options.get("selectcolor")
        try:
            menu.configure(**options)
        except tk.TclError:
            return
        try:
            end = menu.index("end")
        except tk.TclError:
            return
        if end is None:
            return
        for index in range(end + 1):
            try:
                entry_type = menu.type(index)
            except tk.TclError:
                continue
            if entry_type in {"checkbutton", "radiobutton"} and selectcolor:
                try:
                    menu.entryconfigure(index, selectcolor=selectcolor)
                except tk.TclError:
                    pass
                continue
            if entry_type != "cascade":
                continue
            try:
                submenu_name = menu.entrycget(index, "menu")
                submenu = menu.nametowidget(submenu_name)
            except tk.TclError:
                continue
            if isinstance(submenu, tk.Menu):
                WindowTitleBar._configure_menu_tree(submenu, options)


class MenuAccelerators:
    """Platform-appropriate menu accelerator labels."""

    @staticmethod
    def command_kwargs(
        *,
        label: str,
        command: Callable[[], None],
        accelerator: str | None = None,
        **extra: object,
    ) -> dict[str, object]:
        kwargs: dict[str, object] = {"label": label, "command": command, **extra}
        if accelerator is not None:
            kwargs["accelerator"] = accelerator
        return kwargs

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

    @classmethod
    def find(cls) -> str:
        return cls.edit("F")

    @classmethod
    def replace(cls) -> str:
        if sys.platform == "darwin":
            return "Command-Option-F"
        return "Ctrl+H"

    @classmethod
    def new_tab(cls) -> str:
        return cls.edit("T")

    @classmethod
    def save(cls) -> str:
        return cls.edit("S")

    @classmethod
    def save_as(cls) -> str:
        if sys.platform == "darwin":
            return "Command-Shift-S"
        return "Ctrl+Shift+S"

    @classmethod
    def close_tab(cls) -> str:
        return cls.edit("W")


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
    FIND = (
        "<Command-f>",
        "<Command-F>",
        "<Control-f>",
        "<Control-F>",
    )
    REPLACE = (
        "<Command-Option-f>",
        "<Command-Option-F>",
        "<Command-Alt-f>",
        "<Command-Alt-F>",
        "<Control-h>",
        "<Control-H>",
    )
    NEW_TAB = (
        "<Command-t>",
        "<Command-T>",
        "<Control-t>",
        "<Control-T>",
    )
    SAVE = (
        "<Command-s>",
        "<Command-S>",
        "<Control-s>",
        "<Control-S>",
    )
    SAVE_AS = (
        "<Command-Shift-s>",
        "<Command-Shift-S>",
        "<Control-Shift-s>",
        "<Control-Shift-S>",
    )
    CLOSE_TAB = (
        "<Command-w>",
        "<Command-W>",
        "<Control-w>",
        "<Control-W>",
    )

    @classmethod
    def install(
        cls,
        root: tk.Misc,
        bindings: list[tuple[tuple[str, ...], Callable[[tk.Event], str]]],
        *,
        global_bindings: list[tuple[tuple[str, ...], Callable[[tk.Event], str]]]
        | None = None,
    ) -> None:
        for sequences, handler in bindings:
            for sequence in sequences:
                root.bind_class(cls.TAG, sequence, handler)
        cls._prepend_tag_tree(root, cls.TAG)
        if global_bindings:
            for sequences, handler in global_bindings:
                for sequence in sequences:
                    root.bind_all(sequence, handler, add="+")

    @classmethod
    def register_virtual_events(cls, root: tk.Misc) -> None:
        if sys.platform != "darwin":
            return
        for sequence in cls.REPLACE:
            root.event_add("<<Replace>>", sequence)

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
        self._paned: tk.PanedWindow | None = None
        self._preview_frame: tk.Frame | None = None

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("tkwry Markdown editor demo")
        self.root.geometry("1200x760")
        self.root.minsize(800, 520)

        self._preview_var = tk.BooleanVar(self.root, value=True)
        self._minimap_var = tk.BooleanVar(self.root, value=False)
        self._editor_dark_var = tk.BooleanVar(self.root, value=True)
        self._preview_dark_var = tk.BooleanVar(self.root, value=False)
        WindowTitleBar.prepare_startup(dark=self._editor_dark_var.get())
        self._save_dialog_active = False
        self._editor_frame: tk.Frame | None = None
        self._menubar: tk.Menu | None = None

        self._build_menubar()
        WindowTitleBar.install(self.root, self._menubar)
        self._build_panes()
        self._install_shortcuts()
        self._preview_var.trace_add("write", lambda *_: self._toggle_preview())
        self._minimap_var.trace_add("write", lambda *_: self._toggle_minimap())
        self._editor_dark_var.trace_add(
            "write", lambda *_: self._apply_editor_appearance()
        )
        self._preview_dark_var.trace_add(
            "write", lambda *_: self._apply_preview_appearance()
        )
        self.root.update_idletasks()
        self._apply_editor_appearance()
        self._apply_preview_appearance()
        self.root.deiconify()

    def run(self) -> None:
        self.root.mainloop()

    def _build_menubar(self) -> None:
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(
            **MenuAccelerators.command_kwargs(
                label="New Tab",
                command=self._new_tab,
                accelerator=MenuAccelerators.new_tab(),
            )
        )
        file_menu.add_command(
            **MenuAccelerators.command_kwargs(
                label="Close Tab",
                command=self._close_tab,
                accelerator=MenuAccelerators.close_tab(),
            )
        )
        file_menu.add_separator()
        file_menu.add_command(
            **MenuAccelerators.command_kwargs(
                label="Save",
                command=self._save,
                accelerator=MenuAccelerators.save(),
            )
        )
        file_menu.add_command(
            **MenuAccelerators.command_kwargs(
                label="Save As",
                command=self._save_as,
                accelerator=MenuAccelerators.save_as(),
            )
        )

        edit_menu = tk.Menu(menubar, tearoff=0)
        if sys.platform == "darwin":
            edit_menu.configure(postcommand=MacOsEditMenu.strip_injected_items)

        edit_menu.add_command(
            **MenuAccelerators.command_kwargs(
                label="Undo",
                command=self._undo,
                accelerator=MenuAccelerators.undo(),
            )
        )
        edit_menu.add_command(
            **MenuAccelerators.command_kwargs(
                label="Redo",
                command=self._redo,
                accelerator=MenuAccelerators.redo(),
            )
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            **MenuAccelerators.command_kwargs(
                label="Cut",
                command=self._cut,
                accelerator=MenuAccelerators.edit("X"),
            )
        )
        edit_menu.add_command(
            **MenuAccelerators.command_kwargs(
                label="Copy",
                command=self._copy,
                accelerator=MenuAccelerators.edit("C"),
            )
        )
        edit_menu.add_command(
            **MenuAccelerators.command_kwargs(
                label="Paste",
                command=self._paste,
                accelerator=MenuAccelerators.edit("V"),
            )
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            **MenuAccelerators.command_kwargs(
                label="Find",
                command=self._find,
                accelerator=MenuAccelerators.find(),
            )
        )
        replace_accel = None if sys.platform == "darwin" else MenuAccelerators.replace()
        edit_menu.add_command(
            **MenuAccelerators.command_kwargs(
                label="Replace",
                command=self._replace,
                accelerator=replace_accel,
            )
        )

        view_menu = tk.Menu(menubar, tearoff=0)
        if sys.platform == "darwin":
            view_menu.configure(postcommand=MacOsWindowTabs.strip_menu_items)
        view_menu.add_checkbutton(
            label="Preview",
            variable=self._preview_var,
        )
        view_menu.add_checkbutton(
            label="Minimap",
            variable=self._minimap_var,
        )
        view_menu.add_checkbutton(
            label="Editor Dark Mode",
            variable=self._editor_dark_var,
        )
        view_menu.add_checkbutton(
            label="Preview Dark Mode",
            variable=self._preview_dark_var,
        )

        self._menubar = menubar
        if sys.platform == "darwin":
            menubar.add_cascade(menu=tk.Menu(menubar, name="apple"))
        menubar.add_cascade(menu=file_menu, label="File")
        menubar.add_cascade(menu=edit_menu, label="Edit")
        menubar.add_cascade(menu=view_menu, label="View")
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

        self._paned = paned
        self._editor_frame = editor_frame
        self._preview_frame = preview_frame

        self._preview_web = WebView(
            preview_frame,
            html=HtmlPages.preview(),
            on_page_load=self._on_preview_page_load,
        )

        initial_tabs = [{"title": "readme.md", "content": SAMPLE_MARKDOWN}]
        self._editor_web = WebView(
            editor_frame,
            html=HtmlPages.editor(initial_tabs),
            ipc_handler=self._on_editor_ipc,
        )

    def _new_tab(self) -> None:
        self._eval_editor("window.editorNewTab && window.editorNewTab();")

    def _close_tab(self) -> None:
        self._eval_editor(
            "window.editorCloseActiveTab && window.editorCloseActiveTab();"
        )

    def _save(self) -> None:
        self._save_active_tab(save_as=False)

    def _save_as(self) -> None:
        self._save_active_tab(save_as=True)

    def _ask_save_path(
        self,
        *,
        title: str,
        path: str | None,
        suggested_name: str,
    ) -> str | None:
        if self._editor_web is not None:
            self._editor_web.focus_parent()
        self.root.update_idletasks()
        return SaveDialog.ask_path(
            parent=self.root,
            title=title,
            path=path,
            suggested_name=suggested_name,
        )

    def _write_saved_tab(self, path: str, content: str) -> None:
        file_path = Path(path)
        try:
            file_path.write_text(content, encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(
                "Save",
                f"Could not save file:\n{exc}",
                parent=self.root,
            )
            return

        if self._editor_web is not None:
            self._editor_web.eval_js(
                "window.editorMarkSaved("
                f"{json.dumps(str(file_path))}, {json.dumps(file_path.name)}"
                ");"
            )

    def _finish_save_tab(self, info: dict[str, object], *, save_as: bool) -> None:
        content = str(info.get("content", ""))
        title = str(info.get("title", "Untitled"))
        path = info.get("path")
        path_str = str(path) if path else None
        suggested = title if title.lower().endswith(".md") else f"{title}.md"

        if save_as or not path_str:
            dialog_title = "Save As" if save_as else "Save"
            chosen = self._ask_save_path(
                title=dialog_title,
                path=path_str if save_as else None,
                suggested_name=suggested,
            )
            if chosen is None:
                return
            path_str = chosen

        self._write_saved_tab(path_str, content)

    def _save_active_tab(self, *, save_as: bool) -> None:
        if self._save_dialog_active:
            return
        if not self._editor_ready or self._editor_web is None:
            return
        self._save_dialog_active = True

        def on_tab_meta(result: str) -> None:
            try:
                meta = _parse_eval_json_object(result)
                if not meta:
                    return
                info = {
                    "title": meta.get("title", "Untitled"),
                    "path": meta.get("path"),
                    "content": self._last_markdown,
                }
                self._finish_save_tab(info, save_as=save_as)
            finally:
                self._save_dialog_active = False

        def on_tab_meta_error(_exc: BaseException) -> None:
            self._save_dialog_active = False

        self._editor_web.eval_js_with_callback(
            "window.editorGetActiveTabMeta && window.editorGetActiveTabMeta()",
            on_tab_meta,
            on_error=on_tab_meta_error,
        )

    def _install_shortcuts(self) -> None:
        wrap = EditorShortcutBindings.wrap
        replace_handler = wrap(self._replace)
        save_handler = wrap(self._save)
        save_as_handler = wrap(self._save_as)
        EditorShortcutBindings.register_virtual_events(self.root)
        self.root.bind("<<Replace>>", replace_handler, add="+")
        EditorShortcutBindings.install(
            self.root,
            [
                (EditorShortcutBindings.UNDO, wrap(self._undo)),
                (EditorShortcutBindings.REDO, wrap(self._redo)),
                (EditorShortcutBindings.CUT, wrap(self._cut)),
                (EditorShortcutBindings.COPY, wrap(self._copy)),
                (EditorShortcutBindings.PASTE, wrap(self._paste)),
                (EditorShortcutBindings.FIND, wrap(self._find)),
                (EditorShortcutBindings.REPLACE, replace_handler),
                (EditorShortcutBindings.NEW_TAB, wrap(self._new_tab)),
                (EditorShortcutBindings.SAVE, save_handler),
                (EditorShortcutBindings.SAVE_AS, save_as_handler),
                (EditorShortcutBindings.CLOSE_TAB, wrap(self._close_tab)),
            ],
            global_bindings=[
                (EditorShortcutBindings.REPLACE, replace_handler),
                (EditorShortcutBindings.SAVE, save_handler),
                (EditorShortcutBindings.SAVE_AS, save_as_handler),
            ],
        )

    def _eval_editor(self, script: str) -> None:
        if not self._editor_ready or self._editor_web is None:
            return
        self._editor_web.focus()

        def run() -> None:
            if self._editor_web is not None:
                self._editor_web.eval_js(script)

        self.root.after_idle(run)

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

    def _find(self) -> None:
        self._eval_editor("window.editorFind && window.editorFind();")

    def _replace(self) -> None:
        self._eval_editor("window.editorReplace && window.editorReplace();")

    def _toggle_minimap(self) -> None:
        if not self._editor_ready or self._editor_web is None:
            return
        enabled = self._minimap_var.get()
        self._editor_web.focus()
        self._editor_web.eval_js(f"window.editorSetMinimap({json.dumps(enabled)});")

    def _editor_appearance_mode(self) -> str:
        return "dark" if self._editor_dark_var.get() else "light"

    def _preview_appearance_mode(self) -> str:
        return "dark" if self._preview_dark_var.get() else "light"

    def _apply_editor_appearance(self) -> None:
        mode = self._editor_appearance_mode()
        editor_bg = "#1e1e1e" if mode == "dark" else "#f3f3f3"
        if self._editor_frame is not None:
            self._editor_frame.configure(bg=editor_bg)
        if self._editor_ready and self._editor_web is not None:
            self._editor_web.eval_js(
                f"window.editorSetTheme && window.editorSetTheme({json.dumps(mode)});"
            )
        WindowTitleBar.apply(
            self.root,
            dark=(mode == "dark"),
            menubar=self._menubar,
            paned=self._paned,
        )
        self._sync_pane_bounds()

    def _apply_preview_appearance(self) -> None:
        mode = self._preview_appearance_mode()
        preview_bg = "#0d1117" if mode == "dark" else "#ffffff"
        if self._preview_frame is not None:
            self._preview_frame.configure(bg=preview_bg)
        if self._preview_ready and self._preview_web is not None:
            self._preview_web.eval_js(
                f"window.setAppearance && window.setAppearance({json.dumps(mode)});"
            )
        self._sync_pane_bounds()

    def _toggle_preview(self) -> None:
        if self._paned is None or self._preview_frame is None:
            return
        hide = not self._preview_var.get()
        self._paned.paneconfigure(self._preview_frame, hide=hide)
        self.root.update_idletasks()
        self._sync_pane_bounds()
        if not hide:
            self._push_preview_now(self._last_markdown)

    def _sync_pane_bounds(self) -> None:
        if self._editor_web is not None:
            self._editor_web.sync_bounds()
        if self._preview_web is not None:
            self._preview_web.sync_bounds()

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

    def _on_preview_page_load(self, event: PageLoadEvent, _url: str) -> None:
        if event != PageLoadEvent.Finished:
            return
        self._preview_ready = True
        self._apply_preview_appearance()
        self._push_preview_now(self._last_markdown)

    def _on_editor_ipc(self, message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return
        if data.get("type") == "ready":
            self._editor_ready = True
            self._toggle_minimap()
            self._apply_editor_appearance()
        elif data.get("type") == "markdown":
            self._schedule_preview(data.get("text", ""))


def main() -> None:
    MarkdownEditorDemo().run()


if __name__ == "__main__":
    main()
