from __future__ import annotations

import base64
import http.server
import json
import logging
import socket
import threading
from pathlib import Path
from PySide6.QtCore import QObject, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# 기본 글꼴(Pretendard 12pt) 적용 스니펫:
# - 문서의 루트 바탕글(Style 0) 모양 자체를 Pretendard 12pt로 설정하여
#   새 문서 작성 및 입력 시 함초롬바탕과의 깜빡임/충돌 없이 즉시 Pretendard로 동작하게 한다.
_PRETENDARD_DEFAULTS_JS = """
(function() {
    try {
        var deps = window.rhwpStudio && window.rhwpStudio.plugins && window.rhwpStudio.plugins.deps;
        if (!deps || !deps.wasm || !deps.wasm.doc) return;
        var wasm = deps.wasm;
        var fid = wasm.findOrCreateFontId ? wasm.findOrCreateFontId('Pretendard') : -1;
        if (fid !== undefined && fid !== null && fid >= 0) {
            var charMods = {
                fontId: fid,
                fontSize: 1200,
                fontFamilies: ['Pretendard', 'Pretendard', 'Pretendard', 'Pretendard', 'Pretendard', 'Pretendard', 'Pretendard']
            };
            if (wasm.updateStyleShapes) {
                try {
                    wasm.updateStyleShapes(0, JSON.stringify(charMods), '{}');
                } catch(e) {}
            }
            if (deps.eventBus && wasm.getCharPropertiesAt) {
                try {
                    deps.eventBus.emit('cursor-format-changed', wasm.getCharPropertiesAt(0, 0, 0));
                } catch(e) {}
            }
        }
        var fontSelect = document.getElementById('font-name');
        if (fontSelect) {
            var opt = fontSelect.querySelector('option[value="Pretendard"]');
            if (!opt) {
                opt = document.createElement('option');
                opt.value = 'Pretendard';
                opt.textContent = 'Pretendard';
                fontSelect.insertBefore(opt, fontSelect.firstChild);
            }
            fontSelect.value = 'Pretendard';
        }
        var sizeInput = document.getElementById('font-size');
        if (sizeInput) { sizeInput.value = '12.0'; }
    } catch (e) {
        // 엔진 준비 중 조기 호출 시 조용히 무시
    }
})();
"""

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
        if clean_path.startswith("/fonts/"):
            fonts_dir = (Path(__file__).parent / "assets" / "fonts").resolve()
            subpath = clean_path[len("/fonts/"):].split("?")[0].split("#")[0]
            target = (fonts_dir / subpath).resolve()
        resolved = super().translate_path(clean_path)
        # .wasm 요청 시 실제 파일이 없으면 .dat 또는 .bin 대체 파일 탐색
        if clean_path.endswith(".wasm") and not Path(resolved).exists():
            for alt_ext in (".dat", ".bin"):
                cand = Path(resolved).with_suffix(alt_ext)
                if cand.exists():
                    return str(cand)
        return resolved

    def guess_type(self, path: str) -> str:
        p = str(path).lower()
        if p.endswith(".wasm") or "rhwp_bg" in p:
            return "application/wasm"
        return super().guess_type(path)

    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".wasm": "application/wasm",
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".json": "application/json",
        ".css": "text/css",
        ".woff2": "font/woff2",
        ".ttf": "font/ttf",
    }

    def end_headers(self) -> None:
        # WASM 및 ES 모듈 스트리밍 컴파일 및 보안 헤더, CORS 허용
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        # 브라우저 캐싱으로 WASM 및 정적 에셋 로딩 시간 단축
        self.send_header("Cache-Control", "public, max-age=31536000")
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

        preferred_port = 28419
        try:
            self._server = http.server.ThreadingHTTPServer(("127.0.0.1", preferred_port), handler_factory)
            self.port = preferred_port
        except Exception:
            try:
                self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_factory)
                self.port = self._server.server_address[1]
            except Exception as e:
                logger.exception("Failed to start local rhwp-studio server: %e", e)
                return

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info("Local rhwp-studio server running on port %d", self.port)

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
        self._is_engine_ready = False
        self._check_timer: QTimer | None = None
        self._check_count = 0
        self._pending_html: str | None = None
        self._pending_load: tuple[str, str, bytes | None] | None = None

        panel = self.palette.get("panel", "#FFFFFF")
        text = self.palette.get("text", "#1F2328")
        text_muted = self.palette.get("text_muted", "#64748B")
        accent = self.palette.get("accent", "#2563EB")

        self.stack_layout = QStackedLayout(self)
        self.stack_layout.setContentsMargins(0, 0, 0, 0)

        # 1. 엔진 로딩 화면 위젯
        self.loading_widget = QWidget(self)
        self.loading_widget.setStyleSheet(f"background-color: {panel};")
        loading_layout = QVBoxLayout(self.loading_widget)
        loading_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_layout.setSpacing(12)

        loading_title = QLabel("한글(HWPX) 문서 편집기를 불러오는 중입니다...")
        loading_title.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {text}; border: none;")
        loading_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_layout.addWidget(loading_title)

        self.progress_bar = QProgressBar(self.loading_widget)
        self.progress_bar.setRange(0, 0)  # 무한 반복 인디케이터 (Marquee)
        self.progress_bar.setFixedWidth(280)
        self.progress_bar.setFixedHeight(5)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: #E2E8F0;
                border: none;
                border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background-color: {accent};
                border-radius: 2px;
            }}
        """)
        loading_layout.addWidget(self.progress_bar, alignment=Qt.AlignmentFlag.AlignCenter)

        loading_subtitle = QLabel("WebAssembly 문서 엔진을 초기화하고 있습니다.")
        loading_subtitle.setStyleSheet(f"font-size: 11px; color: {text_muted}; border: none;")
        loading_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_layout.addWidget(loading_subtitle)

        self.stack_layout.addWidget(self.loading_widget)  # Index 0: 로딩 화면

        if _HAS_WEBENGINE:
            self.server = RhwpStudioServer.get_instance()
            self.web_view = QWebEngineView(self)
            self.web_view.loadFinished.connect(self._on_load_finished)
            self.web_view.page().titleChanged.connect(self._on_web_title_changed)
            self.web_view.titleChanged.connect(self._on_web_title_changed)

            studio_url = self.server.get_url()
            self.web_view.setUrl(QUrl(studio_url))
            self.stack_layout.addWidget(self.web_view)  # Index 1: 에디터 뷰
            self._fallback_editor = None
        else:
            from taskcalendar.rich_text_edit import RichTextEdit
            self._fallback_editor = RichTextEdit(self)
            self._fallback_editor.setFont(QFont("Pretendard", 12))
            self._fallback_editor.textChanged.connect(self.contentChanged.emit)
            self.stack_layout.addWidget(self._fallback_editor)
            self.stack_layout.setCurrentWidget(self._fallback_editor)
            self.web_view = None

    def _on_web_title_changed(self, title: str) -> None:
        if title.startswith("rhwp_modified:"):
            self.contentChanged.emit()

    def _inject_change_hook(self) -> None:
        if not self.web_view:
            return
        js = """
        (function() {
            function notifyMod() {
                document.title = 'rhwp_modified:' + Date.now();
            }
            window._rhwpNotifyMod = notifyMod;

            // 1. rhwp-studio eventBus 구독 (에디터 내 문서 변형 감지)
            try {
                var deps = window.rhwpStudio && window.rhwpStudio.plugins && window.rhwpStudio.plugins.deps;
                var eb = deps && (deps.eventBus || (deps.getInputHandler && deps.getInputHandler()?.eventBus));
                if (eb && !window._hasEventBusHook) {
                    window._hasEventBusHook = true;
                    eb.on('document-changed', notifyMod);
                    eb.on('document-dirty-changed', notifyMod);
                    eb.on('document-mutated', notifyMod);
                }
            } catch(e) {}

            // 2. DOM 전역 이벤트 캡처 (한글 IME composition, input, keydown, paste, cut, 툴바 버튼 클릭)
            if (!window._hasDomHook) {
                window._hasDomHook = true;
                window.addEventListener('input', notifyMod, true);
                window.addEventListener('beforeinput', notifyMod, true);
                window.addEventListener('compositionend', notifyMod, true);
                window.addEventListener('compositionupdate', notifyMod, true);
                window.addEventListener('keydown', function(e) {
                    if (!['Control', 'Alt', 'Shift', 'Meta', 'CapsLock', 'Escape', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) {
                        notifyMod();
                    }
                }, true);
                window.addEventListener('paste', notifyMod, true);
                window.addEventListener('cut', notifyMod, true);
                document.addEventListener('click', function(e) {
                    var t = e.target;
                    if (t && (t.closest('button') || t.closest('.tb-btn') || t.closest('.sb-btn') || t.closest('.stb-item') || t.closest('select') || t.closest('input'))) {
                        notifyMod();
                    }
                }, true);
            }
        })();
        """
        self.web_view.page().runJavaScript(js)

    def _apply_default_font_and_size(self) -> None:
        """한글 에디터 기본 글꼴을 Pretendard 12pt로 설정 (문서가 있으면 본문 전체에 적용)"""
        if not self.web_view:
            return
        self.web_view.page().runJavaScript(_PRETENDARD_DEFAULTS_JS)

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            logger.error("Failed to load rhwp-studio web view")
            return
        # WebEngine의 HTML 다운로드가 끝난 후에도 WebAssembly 컴파일 및 내부 문서 인스턴스화가 비동기로 진행됨
        # WASM doc 엔진이 완전히 준비될 때까지 안전하게 대기 후 표시
        self._check_count = 0
        if self._check_timer is not None:
            self._check_timer.stop()
        self._check_timer = QTimer(self)
        self._check_timer.setInterval(30)
        self._check_timer.timeout.connect(self._check_studio_engine_ready)
        self._check_timer.start()
        # 첫 번째 검사를 딜레이 없이 즉시 실행
        self._check_studio_engine_ready()

    def _check_studio_engine_ready(self) -> None:
        self._check_count += 1
        if self._check_count > 300:  # 최대 약 9초 대기
            if self._check_timer:
                self._check_timer.stop()
            self._mark_engine_ready()
            return

        check_js = """
        (function() {
            try {
                var deps = window.rhwpStudio && window.rhwpStudio.plugins && window.rhwpStudio.plugins.deps;
                if (!deps || !deps.loadDocument || !deps.wasm) return false;
                var wasm = deps.wasm;
                if (!wasm.doc && !wasm.createEmptyDocument) return false;
                if (typeof wasm.findOrCreateFontId !== 'function') return false;
                return true;
            } catch(e) {
                return false;
            }
        })();
        """
        if self.web_view:
            self.web_view.page().runJavaScript(check_js, self._on_engine_check_result)

    def _on_engine_check_result(self, is_ready: bool) -> None:
        if is_ready:
            if self._check_timer:
                self._check_timer.stop()
            self._mark_engine_ready()

    def _mark_engine_ready(self) -> None:
        self._is_loaded = True
        self._is_engine_ready = True
        if self.web_view:
            self.stack_layout.setCurrentWidget(self.web_view)
            self._inject_change_hook()
            self._apply_default_font_and_size()

        if self._pending_load is not None:
            title, text, hwpx_bytes = self._pending_load
            self._pending_load = None
            self.load_document(title, text, hwpx_bytes)
        elif self._pending_html is not None:
            self.set_html(self._pending_html)
            self._pending_html = None

    def load_document(self, title: str, text: str = "", hwpx_bytes: bytes | None = None) -> None:
        """HWPX 바이너리 또는 텍스트를 rhwp-studio에 로드"""
        if self._fallback_editor is not None:
            self._fallback_editor.setFont(QFont("Pretendard", 12))
            self._fallback_editor.setPlainText(text)
            return

        if not self._is_loaded or not self._is_engine_ready or not self.web_view:
            self._pending_load = (title, text, hwpx_bytes)
            return

        safe_title = title.replace('"', '\\"').replace("'", "\\'") or "문서"
        ext = ".hwp" if (title.lower().endswith(".hwp") and not title.lower().endswith(".hwpx")) else ".hwpx"
        doc_filename = safe_title if safe_title.lower().endswith(('.hwp', '.hwpx')) else f"{safe_title}{ext}"

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
                    await deps.loadDocument(bytes, "{doc_filename}");
                }}
            }})();
            """
        else:
            json_text = json.dumps(text)
            js = f"""
            (async function() {{
                var deps = window.rhwpStudio && window.rhwpStudio.plugins && window.rhwpStudio.plugins.deps;
                if (!deps) return;
                if (deps.createBlankDocument) {{
                    try {{
                        await deps.createBlankDocument();
                    }} catch(e) {{}}
                }}
                var wasm = deps.wasm;
                var doc = wasm && wasm.doc;
                if (wasm && wasm.updateStyleShapes) {{
                    try {{
                        var fid = wasm.findOrCreateFontId ? wasm.findOrCreateFontId('Pretendard') : -1;
                        if (fid !== undefined && fid !== null && fid >= 0) {{
                            var charMods = {{
                                fontId: fid,
                                fontSize: 1200,
                                fontFamilies: ['Pretendard', 'Pretendard', 'Pretendard', 'Pretendard', 'Pretendard', 'Pretendard', 'Pretendard']
                            }};
                            wasm.updateStyleShapes(0, JSON.stringify(charMods), '{{}}');
                        }}
                    }} catch(e) {{}}
                }}
                var rawText = {json_text};
                if (doc && rawText) {{
                    try {{
                        var lines = rawText.split(/\\r?\\n/);
                        var currentPara = 0;
                        for (var i = 0; i < lines.length; i++) {{
                            var line = lines[i];
                            if (line.length > 0) {{
                                doc.insertText(0, currentPara, 0, line);
                            }}
                            if (i < lines.length - 1) {{
                                var resStr = doc.splitParagraph(0, currentPara, line.length, null);
                                try {{
                                    var res = JSON.parse(resStr);
                                    if (res && res.ok && res.paraIdx !== undefined) {{
                                        currentPara = res.paraIdx;
                                    }} else {{
                                        currentPara += 1;
                                    }}
                                }} catch(e) {{
                                    currentPara += 1;
                                }}
                            }}
                        }}
                    }} catch (e) {{
                        console.error('Error inserting lines:', e);
                    }}
                }}
                if (doc && doc.exportHwpx) {{
                    try {{
                        var bytes = doc.exportHwpx();
                        await deps.loadDocument(bytes, "{safe_title}.hwpx");
                    }} catch (e) {{}}
                }}
                var fontSelect = document.getElementById('font-name');
                if (fontSelect) fontSelect.value = 'Pretendard';
                var sizeInput = document.getElementById('font-size');
                if (sizeInput) sizeInput.value = '12.0';
            }})();
            """
        self.web_view.page().runJavaScript(js)
        QTimer.singleShot(500, self._inject_change_hook)

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

    def setFocus(self, reason=Qt.FocusReason.OtherFocusReason) -> None:
        super().setFocus(reason)
        if self.web_view:
            self.web_view.setFocus(reason)
            js = """
            (function() {
                try {
                    var deps = window.rhwpStudio && window.rhwpStudio.plugins && window.rhwpStudio.plugins.deps;
                    if (deps && deps.getInputHandler) {
                        var h = deps.getInputHandler();
                        if (h && typeof h.focus === 'function') {
                            h.focus();
                            return;
                        }
                    }
                    var canvas = document.querySelector('canvas') || document.querySelector('#scroll-content');
                    if (canvas) canvas.focus();
                } catch(e) {}
            })();
            """
            self.web_view.page().runJavaScript(js)
        elif self._fallback_editor:
            self._fallback_editor.setFocus(reason)
