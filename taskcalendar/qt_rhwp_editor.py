from __future__ import annotations

import base64
import http.server
import logging
import socket
import threading
from pathlib import Path
from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    _HAS_WEBENGINE = True
except ImportError:
    _HAS_WEBENGINE = False


class _QuietStudioHandler(http.server.SimpleHTTPRequestHandler):
    """rhwp-studio 정적 에셋 로컬 서빙 핸들러"""

    def __init__(self, *args, directory: str | None = None, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def log_message(self, format: str, *args) -> None:
        pass

    def translate_path(self, path: str) -> str:
        # /assets/assets/ -> /assets/ 중복 경로 자동 정규화
        clean_path = path.replace("/assets/assets/", "/assets/")
        # /rhwp/ 접두사 요청을 루트 기준으로도 매핑
        if clean_path.startswith("/rhwp/"):
            clean_path = clean_path[len("/rhwp"):]
        return super().translate_path(clean_path)

    def end_headers(self) -> None:
        # WASM 및 ES 모듈 로딩을 위한 필수 보안 헤더 및 CORS 허용
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        super().end_headers()


class RhwpStudioServer:
    """rhwp-studio 전용 로컬 HTTP 서버 싱글톤"""

    _instance: RhwpStudioServer | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.port: int = 0
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._start_server()

    @classmethod
    def get_instance(cls) -> RhwpStudioServer:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _start_server(self) -> None:
        studio_dir = Path(__file__).parent / "assets" / "rhwp" / "studio"
        if not studio_dir.exists():
            logger.error("rhwp studio directory not found: %s", studio_dir)
            return

        def handler_factory(*args, **kwargs):
            return _QuietStudioHandler(*args, directory=str(studio_dir.resolve()), **kwargs)

        try:
            self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_factory)
            self.port = self._server.server_address[1]
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
            logger.info("Local rhwp-studio server running on port %d", self.port)
        except Exception as e:
            logger.exception("Failed to start local rhwp-studio server: %e", e)

    def get_url(self) -> str:
        if self.port > 0:
            return f"http://127.0.0.1:{self.port}/index.html"
        return "https://edwardkim.github.io/rhwp/"


class RhwpEditorWidget(QWidget):
    """
    rhwp-studio 오픈소스 웹에디터 자체를 로컬에서 구동하는 위젯
    - Rust + WebAssembly 한글(HWP/HWPX) 완벽 호환 엔진
    - 한글과컴퓨터 스타일의 리본 메뉴, 눈금자, 캔버스 편집기 자체 렌더링
    """

    contentChanged = Signal()

    def __init__(self, parent: QWidget | None = None, palette: dict[str, str] | None = None) -> None:
        super().__init__(parent)
        self.palette = palette or {}
        self._html_content = ""
        self._is_loaded = False
        self._pending_html: str | None = None
        self._pending_load: tuple[str, str, bytes | None] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if _HAS_WEBENGINE:
            self.server = RhwpStudioServer.get_instance()
            self.web_view = QWebEngineView(self)
            self.web_view.loadFinished.connect(self._on_load_finished)

            studio_url = self.server.get_url()
            self.web_view.setUrl(QUrl(studio_url))
            layout.addWidget(self.web_view, 1)
            self._fallback_editor = None
        else:
            from taskcalendar.rich_text_edit import RichTextEdit
            self._fallback_editor = RichTextEdit(self)
            self._fallback_editor.textChanged.connect(self.contentChanged.emit)
            layout.addWidget(self._fallback_editor, 1)
            self.web_view = None

    def _on_load_finished(self, ok: bool) -> None:
        self._is_loaded = ok
        if ok and self._pending_load is not None:
            title, text, hwpx_bytes = self._pending_load
            self._pending_load = None
            self.load_document(title, text, hwpx_bytes)
        elif ok and self._pending_html is not None:
            self.set_html(self._pending_html)
            self._pending_html = None

    def load_document(self, title: str, text: str = "", hwpx_bytes: bytes | None = None) -> None:
        """HWPX 바이너리 또는 텍스트를 rhwp-studio에 로드"""
        if self._fallback_editor is not None:
            self._fallback_editor.setPlainText(text)
            return

        if not self._is_loaded or not self.web_view:
            self._pending_load = (title, text, hwpx_bytes)
            return

        safe_title = title.replace('"', '\\"').replace("'", "\\'") or "문서"
        if hwpx_bytes:
            b64_str = base64.b64encode(hwpx_bytes).decode("ascii")
            js = f"""
            (async function() {{
                var b64 = "{b64_str}";
                var bin = atob(b64);
                var len = bin.length;
                var bytes = new Uint8Array(len);
                for (var i = 0; i < len; i++) {{
                    bytes[i] = bin.charCodeAt(i);
                }}
                var deps = window.rhwpStudio?.plugins?.deps;
                if (deps && deps.loadDocument) {{
                    await deps.loadDocument(bytes, "{safe_title}.hwpx");
                }}
            }})();
            """
        else:
            escaped_text = text.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
            js = f"""
            (async function() {{
                var deps = window.rhwpStudio?.plugins?.deps;
                if (!deps) return;
                deps.createBlankDocument?.();
                var doc = deps.wasm?.doc;
                if (doc && doc.insertText) {{
                    try {{
                        doc.insertText(0, 0, 0, `{escaped_text}`);
                        var bytes = doc.exportHwpx();
                        await deps.loadDocument(bytes, "{safe_title}.hwpx");
                    }} catch(e) {{
                        console.error(e);
                    }}
                }}
            }})();
            """
        self.web_view.page().runJavaScript(js)

    def export_document_data(self, callback) -> None:
        """현재 편집 중인 문서의 순수 텍스트 및 HWPX 바이너리 추출 callback(text: str, hwpx_bytes: bytes | None)"""
        import json as _json

        if self._fallback_editor is not None:
            callback(self._fallback_editor.toPlainText(), None)
            return

        if not self.web_view:
            callback(self._html_content, None)
            return

        js = """
        (function() {
            try {
                var doc = window.rhwpStudio?.plugins?.deps?.wasm?.doc;
                if (!doc) return JSON.stringify({ text: "", hwpxB64: null });
                var rawText = doc.getTextFileText ? doc.getTextFileText() : "";
                var parsedText = rawText;
                try {
                    parsedText = JSON.parse(rawText);
                } catch(e) {}
                var hwpxB64 = null;
                if (doc.exportHwpx) {
                    var bytes = doc.exportHwpx();
                    if (bytes && bytes.byteLength > 0) {
                        var bin = '';
                        var len = bytes.byteLength;
                        for (var i = 0; i < len; i++) {
                            bin += String.fromCharCode(bytes[i]);
                        }
                        hwpxB64 = window.btoa(bin);
                    }
                }
                return JSON.stringify({ text: parsedText, hwpxB64: hwpxB64 });
            } catch(err) {
                return JSON.stringify({ text: "", hwpxB64: null, err: String(err) });
            }
        })();
        """

        def _on_js_result(res):
            if not res or not isinstance(res, str):
                callback("", None)
                return
            try:
                data = _json.loads(res)
                text = data.get("text", "")
                hwpx_b64 = data.get("hwpxB64")
                hwpx_bytes = base64.b64decode(hwpx_b64) if hwpx_b64 else None
                callback(text, hwpx_bytes)
            except Exception as e:
                logger.exception("Failed to parse export_document_data JSON: %s", e)
                callback("", None)

        self.web_view.page().runJavaScript(js, _on_js_result)

    def fit_page(self) -> None:
        """웹에디터 화면을 '쪽맞춤' (Fit Page) 비율로 자동 전환"""
        if not self.web_view:
            return
        js = """
        (function() {
            var btn = document.getElementById('sb-zoom-fit');
            if (btn) {
                btn.click();
            }
        })();
        """
        self.web_view.page().runJavaScript(js)

    def set_html(self, html: str) -> None:
        self._html_content = html
        if self._fallback_editor is not None:
            self._fallback_editor.setHtml(html)
            return

        if not self._is_loaded or not self.web_view:
            self._pending_html = html
            return

        # rhwp-studio 내부 문서에 텍스트 또는 HTML 주입 시도 및 쪽맞춤 유지
        escaped = html.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
        js = f"""
        (function() {{
            if (window.rhwpStudio && window.rhwpStudio.loadDocument) {{
                window.rhwpStudio.loadDocument(`{escaped}`);
            }}
            setTimeout(function() {{
                var btn = document.getElementById('sb-zoom-fit');
                if (btn) {{
                    btn.click();
                }}
            }}, 200);
        }})();
        """
        self.web_view.page().runJavaScript(js)

    def get_html(self, callback) -> None:
        if self._fallback_editor is not None:
            callback(self._fallback_editor.toHtml())
            return

        if self.web_view:
            self.web_view.page().runJavaScript(
                "document.title",
                lambda t: callback(self._html_content),
            )
        else:
            callback(self._html_content)

    def get_cached_html(self) -> str:
        if self._fallback_editor is not None:
            return self._fallback_editor.toHtml()
        return self._html_content
