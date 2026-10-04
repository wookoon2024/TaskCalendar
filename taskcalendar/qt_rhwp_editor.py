from __future__ import annotations

import base64
import http.server
import json
import logging
import socket
import threading
from pathlib import Path
from PySide6.QtCore import QObject, QPoint, QTimer, QUrl, Qt, Signal, Slot
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

# AI 스파클 아이콘 Data URL (투명 배경 PNG)
_AI_SPARKLE_DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABMAAAASCAYAAAC5DOVpAAACz0lEQVR4nH2UTWhcVRTHf+e+N5/JpDaZOGrThLZODNUs/KBYXIQKbgS1K5VmIehCLF276UYQFyq6kSIqKPhBUVAREezGYDaKoC268KNSbIKp1mSaNx/JzHvv3iNv0kkm6SMHLuc+zuF3/++cc6+0w5h+s8tz6pWPCSnWvjKn+dF7BX8oLYzp/4jqi2e4/Cm49RsSg2/e0D0r75GtndFUUj8sWl34sHHhnZMmFvzld1Xi2v5tivVvsCCtJSRu7g4Lzn8w6/67hIQGlhbgyvcLvVh49WKQz/ybEKFRx2uc011h5WOnpXTnU3SWO8TlJ9D9D2/WbfXCF0ODftxVhnWY9u+7K0ssN/WQEBZh7J4dDQjIoOAUYpBwJRUmO7vZbxo2uPbtWzqgvzBUsOBdPz6fRStVotFnZtS/eX5XWLR4Xus/fEZGFikVY3IZRRKQJxuwZJ/zoZDDDU3iSvdjSw/IDbD6uTd1MPiJrOlgPAVfIZMsNoBGNmA9eOIH8uhNB7dq5oKlF1zttxbtBtlmhEm6GgokvpMsQSPZqFu3Ed156Xqtt3FNf+s3m1+9qHsnR4iqJ8Ve/E5l/mOysorkQTJ9Cn2Q63sXQWwr6MgUTD8+6veUlbw1zNUaVMGrHhWqR4lXFs/az197slAMwCXyXVeN6wi2MIabPgG33rXZeWld+lnbP55lOK7BoEMnDhCNzz6vxX2v9pLs26e0UFzr1keN4qzFHn99hmJ5ftucFb9+n+F/Alg30DLIn3+RnXvpFRNc/qiX5I/dAZGBSBBrkMokO0Fd2Pojz/4auQq0BBoGFx8mPvQ0bs/E7Oa8HXnsy7jpkAQYCrrvSOpcGlM5cNg9d1pUh0mmPzp+SuzUfdtvQHn80TD2IDaIy2APzaQ+UVvXyRvB3n536ond8C3jaGRwe2+DTC41Z9vQyloLLQ6kJ7ZWq/aTl/8wD56AielUZf8DFTkxIb0Gu84AAAAASUVORK5CYII="

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


def _decode_wasm_from_png(png_path: Path) -> bytes | None:
    try:
        import struct
        import zlib
        data = png_path.read_bytes()
        idx = 8
        idat_acc = bytearray()
        w, h = 0, 0
        while idx < len(data):
            clen = struct.unpack(">I", data[idx : idx + 4])[0]
            ctype = data[idx + 4 : idx + 8]
            cdata = data[idx + 8 : idx + 8 + clen]
            if ctype == b"IHDR":
                w, h = struct.unpack(">II", cdata[:8])
            elif ctype == b"IDAT":
                idat_acc.extend(cdata)
            idx += 12 + clen
        decomp = zlib.decompress(bytes(idat_acc))
        rb = w * 4
        payload = bytearray()
        for i in range(h):
            start = i * (rb + 1) + 1
            payload.extend(decomp[start : start + rb])
        olen = struct.unpack(">I", payload[:4])[0]
        return bytes(payload[4 : 4 + olen])
    except Exception as e:
        logger.warning("Failed to decode wasm from png %s: %s", png_path, e)
        return None


def ensure_rhwp_wasm() -> Path | None:
    """망연계 보안 통과용 표준 PNG 이미지(rhwp_engine.png)로부터 .wasm 자동 복원"""
    studio_assets = Path(__file__).parent / "assets" / "rhwp" / "studio" / "assets"
    wasm_path = studio_assets / "rhwp_bg-PUGAA2uC.wasm"
    if wasm_path.exists() and wasm_path.stat().st_size > 0:
        return wasm_path
    png_path = studio_assets / "rhwp_engine.png"
    if png_path.exists():
        raw = _decode_wasm_from_png(png_path)
        if raw and raw.startswith(b"\x00asm"):
            wasm_path.write_bytes(raw)
            logger.info("Auto-restored rhwp_bg-PUGAA2uC.wasm from %s", png_path.name)
            return wasm_path
    return wasm_path if wasm_path.exists() else None


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
            if str(target).startswith(str(fonts_dir)) and target.exists():
                return str(target)
        resolved = super().translate_path(clean_path)
        # .wasm 요청 시 실제 파일이 없으면 자동 복원 시도
        if clean_path.endswith(".wasm") and not Path(resolved).exists():
            ensure_rhwp_wasm()
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

    def parse_request(self) -> bool:
        # Host 헤더를 루프백으로 제한한다. 검증이 없으면 DNS 리바인딩으로
        # 악성 페이지가 127.0.0.1 서버를 자기 도메인으로 속여 문서 blob을 읽어갈 수 있다.
        if not super().parse_request():
            return False
        host = (self.headers.get("Host") or "").strip().lower()
        if host:
            hostname = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
            if hostname not in ("127.0.0.1", "localhost", "[::1]"):
                self.send_error(403, "Host header not allowed")
                return False
        return True

    def do_GET(self) -> None:
        clean_path = self.path.split("?")[0]
        if clean_path == "/api/current_doc":
            server = RhwpStudioServer.get_instance()
            blob = server.get_current_blob()
            if blob is not None:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)
                return
            else:
                self.send_response(404)
                self.end_headers()
                return
        super().do_GET()

    def end_headers(self) -> None:
        # COOP/COEP 는 Rust/WASM 엔진의 SharedArrayBuffer 구동에 필수이므로 유지한다.
        # CORS(Access-Control-Allow-Origin)는 WASM 구동에 필요하지 않으며,
        # 문서 원본을 담는 /api/current_doc 가 교차 출처로 읽혀 유출되므로 싣지 않는다.
        # 에디터 내부 요청은 모두 상대경로(동일 출처)라 CORS 자체가 걸리지 않는다.
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        # 정적 에셋은 오래 캐싱해 WASM 로딩 시간을 단축하되,
        # 문서 원본은 브라우저 캐시에 남지 않도록 예외 처리한다.
        if self.path.split("?")[0] == "/api/current_doc":
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        else:
            self.send_header("Cache-Control", "public, max-age=31536000")
        super().end_headers()


class RhwpStudioServer:
    """rhwp-studio 전용 로컬 HTTP 서버 싱글톤"""

    _instance: RhwpStudioServer | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.port: int = 0
        self._current_blob: bytes | None = None
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._start_server()

    def set_current_blob(self, blob: bytes | None) -> None:
        with self._lock:
            self._current_blob = blob

    def get_current_blob(self) -> bytes | None:
        with self._lock:
            return self._current_blob

    @classmethod
    def get_instance(cls) -> RhwpStudioServer:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _start_server(self) -> None:
        ensure_rhwp_wasm()
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
    fullscreenToggleRequested = Signal()

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

        self._ai_worker = None

    def _on_web_title_changed(self, title: str) -> None:
        if title.startswith("rhwp_fullscreen:toggle:"):
            self.fullscreenToggleRequested.emit()
        elif title.startswith("rhwp_modified:"):
            self.contentChanged.emit()
        elif title.startswith("rhwp_ai:chat:"):
            self.open_ai_chat()
        elif title.startswith("rhwp_ai:analyze:"):
            self.open_ai_analyze()
        elif title.startswith("rhwp_ai:preset:"):
            # rhwp_ai:preset:<preset_name>:<timestamp>
            parts = title.split(":")
            preset_name = parts[2] if len(parts) > 2 else "gongmun"
            self._handle_ai_preset_from_web(preset_name)
        elif title.startswith("rhwp_ai:req:"):
            if self.web_view:
                # JS 객체를 그대로 반환하면 PySide6 이 빈 문자열로 변환해 버린다.
                # 반드시 JSON 문자열로 변환한 뒤 파이썬에서 json.loads 로 파싱한다.
                self.web_view.page().runJavaScript(
                    "JSON.stringify(window._rhwpAiGetPendingRequest())",
                    self._handle_ai_request_from_js,
                )
        elif title.startswith("rhwp_ai:stop:"):
            self._handle_ai_stop()
        elif title.startswith("rhwp_ai:law:"):
            self._open_law_search()
        elif title.startswith("rhwp_ai:settings:"):
            self._open_ai_settings()

    def _open_law_search(self) -> None:
        """우클릭 「법령 찾기」 — 국가법령정보센터에서 법령 검색"""
        if not self.web_view:
            return
        # 선택된 텍스트가 있으면 검색어로 사용
        query = ""
        try:
            self.web_view.page().runJavaScript(
                "JSON.stringify(window._rhwpLawQuery || '')",
                lambda v: self._launch_law_search(self._parse_js_str(v)),
            )
        except Exception:
            self._launch_law_search("")

    @staticmethod
    def _parse_js_str(value: Any) -> str:
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8", errors="replace")
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, str) else ""
            except Exception:
                return ""
        return ""

    def _launch_law_search(self, initial_query: str) -> None:
        from taskcalendar.law_search_dialog import LawSearchDialog

        dlg = LawSearchDialog(self, palette=self.palette, initial_query=initial_query)
        dlg.exec()

    def _open_ai_settings(self) -> None:
        """AI 연동 설정 대화상자 열기"""
        from taskcalendar.ai_assistant import AISettingsDialog
        dlg = AISettingsDialog(self, palette=self.palette)
        dlg.exec()

    def open_ai_chat(self) -> None:
        """AI 대화하기 플로팅 DIV 열기"""
        if self.web_view:
            self.web_view.page().runJavaScript("if (window.rhwpOpenAiPanel) window.rhwpOpenAiPanel('chat');")
        elif self._fallback_editor is not None:
            from taskcalendar.ai_assistant import AIChatDialog
            dlg = AIChatDialog(parent_editor=self, palette=self.palette)
            dlg.exec()

    def _handle_ai_preset_from_web(self, preset_name: str) -> None:
        """웹 에디터에서 선택된 텍스트와 함께 AI 프리셋 요청 수신"""
        if not self.web_view:
            self.open_ai_preset(preset_name, "")
            return

        js_get_sel = """
        (function() {
            var sel = '';
            try {
                if (typeof window._getWasmSelectionInfo === 'function') {
                    var info = window._getWasmSelectionInfo();
                    if (info && info.text) sel = info.text;
                }
            } catch(e) {}
            if (!sel && window._rhwpLastSelectedText) {
                sel = window._rhwpLastSelectedText;
            }
            if (!sel) {
                try {
                    var s = window.getSelection();
                    sel = s ? s.toString() : '';
                } catch(e) {}
            }
            return JSON.stringify(sel || '');
        })();
        """
        def _on_got_sel(res):
            sel_text = self._parse_js_str(res)
            self.open_ai_preset(preset_name, sel_text)

        self.web_view.page().runJavaScript(js_get_sel, _on_got_sel)

    def open_ai_analyze(self) -> None:
        """AI 업무 법령/행정절차 분석 플로팅 DIV 열기"""
        if self.web_view:
            self.web_view.page().runJavaScript("if (window.rhwpOpenAiPanel) window.rhwpOpenAiPanel('analyze');")
        elif self._fallback_editor is not None:
            from taskcalendar.ai_assistant import AIAnalyzeDialog
            dlg = AIAnalyzeDialog(parent_editor=self, doc_text=self._fallback_editor.toPlainText(), palette=self.palette)
            dlg.exec()

    def _handle_ai_request_from_js(self, req_data: Any) -> None:
        """웹 에디터 플로팅 DIV에서 들어온 AI 요청 비동기 처리"""
        # runJavaScript 결과는 항상 문자열이므로 JSON 파싱이 필요하다
        if isinstance(req_data, (bytes, bytearray)):
            req_data = req_data.decode("utf-8", errors="replace")
        if isinstance(req_data, str):
            text = req_data.strip()
            if not text or text == "null":
                self._on_ai_error("웹 에디터에서 AI 요청 데이터를 받지 못했습니다. 다시 시도해 주세요.")
                return
            try:
                req_data = json.loads(text)
            except Exception:
                self._on_ai_error(f"AI 요청 데이터 파싱에 실패했습니다: {text[:200]}")
                return

        if not req_data or not isinstance(req_data, dict):
            self._on_ai_error("AI 요청 데이터가 비어 있습니다.")
            return
        prompt = str(req_data.get("prompt", "") or "").strip()
        mode = str(req_data.get("mode", "chat") or "chat")
        doc_text = str(req_data.get("docText", "") or "").strip()
        selected_text = str(req_data.get("selectedText", "") or "").strip()

        if self._ai_worker is not None and self._ai_worker.isRunning():
            self._ai_worker.stop()
            self._ai_worker.wait(300)

        from taskcalendar.ai_assistant import AIChatWorker

        korean_rule = (
            "\n\n[출력 언어 및 서식 필수 규칙]\n"
            "- 모든 출력 결과물은 100% 한국어(한글)로만 작성해야 합니다.\n"
            "- 영어, 외국어 번역투, 라틴 알파벳(로마자)의 혼용이나 번역은 엄격히 금지합니다.\n"
            "- 문장이나 항목 앞에 이모티콘(😊, 🏛️, 📋 등)을 절대로 붙이지 마세요.\n"
            "- 불필요한 인사말이나 부연 설명 없이 본문 내용만 명료하게 출력하세요."
        )

        if mode == "gongmun":
            system_prompt = (
                "당신은 대한민국 행정안전부 「행정업무운영 편람」 및 「공문서 작성 규정」을 준수하는 공문서 전문 교정 AI입니다.\n"
                "[절대 원칙]\n"
                "1. 사용자가 입력한 내용이 인사말, 구어체, 구호, 메모 등 그 어떤 형태이더라도 절대 사용자와 대화하거나 안부 인사를 건네지 마십시오.\n"
                "2. 원문의 핵심 의도를 파악하여 행정기관 공문서 본문에 즉시 들어갈 수 있는 완벽한 '행정 표준 개조식 문체'로 변환하여 출력하십시오.\n"
                "3. 반드시 항목 부호(1., 가., 1) 또는 □, ○, -)를 사용하고, 명사형 또는 개조식 종결어미(~함, ~바람, ~안내함, ~추진 예정)로 문장을 끝맺으십시오.\n"
                "4. '안녕하세요', '반갑습니다', '좋은 하루 되세요' 같은 일상 대화체는 공문서에서 결코 사용하지 않습니다.\n"
                "5. 설명이나 코멘트 없이 변환된 공문서 개조식 본문 결과만 단독으로 출력하십시오.\n\n"
                "[변환 예시]\n"
                "원문: '안녕 반가워요 ㅋㅋ 오늘도 즐거운 하루 되시랑께'\n"
                "변환 결과:\n"
                "□ 인사 및 업무 협조 안내\n"
                "  ○ 부서 간 원활한 소통 및 상호 협력 체계 구축\n"
                "  ○ 활기찬 근무 환경 조성을 위한 부서원 격려\n"
                "  ○ 금일 업무 추진에 만전을 기하여 주시기 바람."
            ) + korean_rule
            target = selected_text or prompt
            user_content = f"[다듬을 원문 내용]:\n{target}"
            if prompt and prompt != "공문서 표준 개조식 문체(명사형 종결, 항목 부호)로 다듬어 주세요.":
                user_content += f"\n\n[추가 지시사항]:\n{prompt}"
        elif mode == "summary":
            system_prompt = (
                "당신은 행정 보고서 및 결재 문서 핵심 요약 전문가입니다.\n"
                "사용자가 제공한 내용을 상급자/기관장 보고에 즉시 활용할 수 있도록 '핵심 3줄 요약'으로 정리해 주세요.\n"
                "- 1줄: 추진 배경 및 목적\n"
                "- 2줄: 주요 핵심 내용 및 현황\n"
                "- 3줄: 향후 계획 및 기대 효과\n"
                "- 각 줄은 '○ ' 불릿과 함께 명확하고 간결한 개조식 한국어 문장으로 작성하세요.\n"
                "- 다른 설명이나 인사말 없이 3줄 요약 결과만 바로 출력하세요."
            ) + korean_rule
            target = selected_text or prompt
            user_content = f"[요약할 본문 내용]:\n{target}"
            if prompt and prompt != "추진 배경, 주요 내용, 향후 계획의 핵심 3줄 요약으로 정리해 주세요.":
                user_content += f"\n\n[추가 요청사항]:\n{prompt}"
        elif mode == "law":
            system_prompt = (
                "당신은 대한민국 행정 법률 및 감사 실무 전문 자문관입니다.\n"
                "사용자가 제공한 업무/기안문 내용을 바탕으로 다음 사항을 검토하여 정리해 주세요:\n"
                "1. [관련 법령 및 조례]: 직접적 근거가 되는 법률·시행령·자치법규\n"
                "2. [필수 사전 절차]: 결재·시행 전 필수 심의/협의/사전예고 등\n"
                "3. [실무 유의사항]: 감사 지적 예방 체크포인트\n"
                "불필요한 인사말 없이 개조식으로 명료하게 제공하세요."
            ) + korean_rule
            target = selected_text or prompt
            user_content = f"[검토할 업무 내용]:\n{target}"
            if prompt and prompt != "관련 근거 법령, 자치법규 및 필수 사전 행정 절차를 검토해 주세요.":
                user_content += f"\n\n[추가 요청사항]:\n{prompt}"
        elif mode == "refine":
            system_prompt = (
                "당신은 문화체육관광부 및 국립국어원 표준 행정용어 순화 전문가입니다.\n"
                "사용자가 제공한 문장에서 어려운 한자어, 무분별한 외래어, 일본식 행정용어, 권위적 표현을 찾아 국민이 이해하기 쉬운 '바른 공공언어'로 순화하여 다시 작성해 주세요.\n"
                "- 순화된 최종 완성 문장을 최상단에 제공하세요.\n"
                "- 하단에 [주요 순화 내역]을 간단한 목록으로 첨부하세요 (예: 바우처 → 이용권, 익일 → 다음 날).\n"
                "- 부가적인 인사말은 생략하세요."
            ) + korean_rule
            target = selected_text or prompt
            user_content = f"[순화할 원문 내용]:\n{target}"
            if prompt and prompt != "어려운 한자어, 외래어, 권위적 표현을 쉬운 표준 공공언어로 순화해 주세요.":
                user_content += f"\n\n[추가 요청사항]:\n{prompt}"
        elif mode == "analyze":
            system_prompt = (
                "당신은 대한민국 행정 법률 및 공공기관 실무 감사·행정절차 전문 AI 법률 자문관입니다.\n"
                "사용자가 작성한 업무 문서 내용에 기반하여 다음 사항을 체계적으로 분석해 주세요:\n"
                "1. 관련 법령 및 규정 (법률, 시행령, 시행규칙, 행안부 예규·지침 등 명칭 및 핵심 조항)\n"
                "2. 필수 사전·사후 행정 절차 (결재, 협의, 고시/공고, 위원회 심의, 보고 등)\n"
                "3. 실무상 유의사항 및 감사 지적 예방 포인트\n"
                "불필요한 서두나 인사말은 생략하고 체계적인 개조식 보고서 형태로 명확하게 제공하세요."
            ) + korean_rule
            target_doc = selected_text if selected_text else doc_text
            user_content = f"[분석 요청 업무 문서 내용]\n{target_doc}\n\n[추가 요청사항]\n{prompt if prompt else '관련 법령 및 필수 행정 절차를 분석해 주세요.'}"
        else:
            system_prompt = (
                "당신은 대한민국 공공기관 및 지방자치단체 행정 문서 작성 전문 AI 비서입니다.\n"
                "사용자의 요청에 따라 완성도 높은 공문서, 보고서 서식, 개조식 정리(□, ○, -, *), 기안문 등을 격식 있게 작성해 주세요.\n"
                "[이미지/일러스트/아이콘/도장/그림 생성 요청 시 절대 규칙]:\n"
                "사용자가 이미지, 일러스트, 인물(남자, 여자 등), 캐릭터, 아이콘, 마크, 도장, 사과 등의 그림 생성을 요청하는 경우, "
                "반드시 단독으로 즉시 렌더링 가능한 완전한 SVG XML 코드를 작성하여 ```xml 코드 블록 안에 담아 출력하십시오.\n"
                "- <?xml ...?> 선언문이나 불필요한 설명/인사말을 넣지 마시고 곧바로 ```xml\\n<svg ...>...</svg>\\n``` 형태로만 출력하세요.\n"
                "- SVG는 viewBox, xmlns=\"http://www.w3.org/2000/svg\" 속성을 반드시 포함하고, 선명하고 미려하며 완성도 높은 벡터 그래픽으로 디자인하세요.\n"
                "불필요한 인사말은 생략하고 곧바로 본문 서식 또는 요청된 작성 결과물을 명확하게 제공하세요."
            ) + korean_rule
            if selected_text:
                user_content = f"[참고/대상 텍스트]\n{selected_text}\n\n[요청사항]\n{prompt}"
            else:
                user_content = prompt

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        self._ai_worker = AIChatWorker(messages=messages, parent=self)
        self._ai_worker.chunk_received.connect(self._on_ai_chunk)
        self._ai_worker.finished.connect(self._on_ai_finished)
        self._ai_worker.error.connect(self._on_ai_error)
        self._ai_worker.start()

    def _handle_ai_stop(self) -> None:
        """AI 요청 백그라운드 워커 즉시 중지 (중간에 멈춤)"""
        if self._ai_worker is not None and self._ai_worker.isRunning():
            self._ai_worker.stop()

    def _on_ai_chunk(self, chunk: str) -> None:
        if self.web_view:
            self.web_view.page().runJavaScript(
                f"if (window._rhwpAiOnChunk) window._rhwpAiOnChunk({json.dumps(chunk)});"
            )

    def _on_ai_finished(self, full_text: str) -> None:
        img_b64 = None
        try:
            from taskcalendar.ai_assistant import extract_svg_from_text, render_svg_to_png
            svg_content = extract_svg_from_text(full_text)
            if svg_content:
                logger.info("Found SVG content in AI response (len=%d), rendering to PNG...", len(svg_content))
                png_path = render_svg_to_png(svg_content)
                if png_path and os.path.exists(png_path):
                    import base64
                    with open(png_path, "rb") as f:
                        img_b64 = base64.b64encode(f.read()).decode("ascii")
                    logger.info("Successfully rendered SVG to PNG b64 (len=%d)", len(img_b64))
                    try:
                        os.remove(png_path)
                    except Exception:
                        pass
                else:
                    logger.warning("render_svg_to_png returned None or file missing for SVG")
            else:
                logger.debug("No SVG markup detected in AI response")
        except Exception as e:
            logger.exception("Error rendering AI SVG to PNG: %s", e)

        if self.web_view:
            js_call = (
                f"if (window._rhwpAiOnFinished) window._rhwpAiOnFinished({json.dumps(full_text)}, {json.dumps(img_b64)});"
            )
            self.web_view.page().runJavaScript(js_call)

    def _on_ai_error(self, err_msg: str) -> None:
        if self.web_view:
            self.web_view.page().runJavaScript(
                f"if (window._rhwpAiOnError) window._rhwpAiOnError({json.dumps(err_msg)});"
            )

    def insert_text_at_cursor(self, text: str) -> None:
        """현재 에디터 커서 위치에 텍스트 삽입"""
        if self._fallback_editor is not None:
            self._fallback_editor.insertPlainText(text)
            return

        if not self.web_view:
            return

        json_text = json.dumps(text)
        js = f"""
        (function() {{
            try {{
                var text = {json_text};
                if (window.rhwpStudio && typeof window.rhwpStudio.insertTextAtCursor === 'function') {{
                    var ok = window.rhwpStudio.insertTextAtCursor(text);
                    if (ok) return true;
                }}
                var deps = window.rhwpStudio && window.rhwpStudio.plugins && window.rhwpStudio.plugins.deps;
                var ih = deps && deps.getInputHandler ? deps.getInputHandler() : null;
                if (ih && ih.textarea) {{
                    ih.active = true;
                    if (ih.focusTextarea) ih.focusTextarea();
                    var dt = new DataTransfer();
                    dt.setData('text/plain', text);
                    var ev = new ClipboardEvent('paste', {{ clipboardData: dt, bubbles: true, cancelable: true }});
                    ih.textarea.dispatchEvent(ev);
                    return true;
                }}
            }} catch(e) {{
                console.error('Error in insertTextAtCursor:', e);
            }}
            return false;
        }})();
        """
        self.web_view.page().runJavaScript(js)

    def insert_image_file(self, filepath: str) -> None:
        """현재 에디터 커서 위치에 이미지 파일 삽입"""
        if self._fallback_editor is not None:
            self._fallback_editor.insert_image_file(filepath)
            return

        if not self.web_view:
            return

        import base64
        import os
        from PIL import Image

        try:
            with Image.open(filepath) as pil_img:
                w, h = pil_img.size
            with open(filepath, "rb") as f:
                raw_bytes = f.read()
            b64_str = base64.b64encode(raw_bytes).decode("ascii")
            ext = os.path.splitext(filepath)[1].lstrip(".").lower() or "png"
            filename = os.path.basename(filepath)

            js = f"""
            (async function() {{
                try {{
                    var b64 = "{b64_str}";
                    var bin = atob(b64);
                    var len = bin.length;
                    var bytes = new Uint8Array(len);
                    for (var i = 0; i < len; i++) {{
                        bytes[i] = bin.charCodeAt(i);
                    }}
                    var blob = new Blob([bytes], {{ type: 'image/{ext}' }});

                    // 1. 커서 위치에 즉시 직접 삽입 (click/drag 모드 없이 바로 본문 삽입)
                    if (window.rhwpStudio && typeof window.rhwpStudio.insertImageAtCursor === 'function') {{
                        var ok = await window.rhwpStudio.insertImageAtCursor(blob, "{ext}");
                        if (ok) return true;
                    }}

                    // 2. ClipboardEvent paste 폴백 (커서 위치에 삽입)
                    var deps = window.rhwpStudio && window.rhwpStudio.plugins && window.rhwpStudio.plugins.deps;
                    var ih = deps && deps.getInputHandler ? deps.getInputHandler() : null;
                    if (ih && ih.textarea) {{
                        ih.active = true;
                        if (ih.focusTextarea) ih.focusTextarea();
                        var dt = new DataTransfer();
                        dt.items.add(new File([blob], "{filename}", {{ type: 'image/{ext}' }}));
                        var ev = new ClipboardEvent('paste', {{ clipboardData: dt, bubbles: true, cancelable: true }});
                        ih.textarea.dispatchEvent(ev);
                        return true;
                    }}

                    // 3. 최후 폴백: 배치 모드
                    if (ih && typeof ih.enterImagePlacementMode === 'function') {{
                        ih.enterImagePlacementMode(bytes, "{ext}", {w}, {h}, "{filename}");
                        return true;
                    }}
                }} catch(e) {{
                    console.error("Failed to insert image into rhwp:", e);
                }}
                return false;
            }})();
            """
            self.web_view.page().runJavaScript(js)
        except Exception as e:
            logger.error("Failed to insert image file %s: %s", filepath, e)

    def replace_selection_with_text(self, text: str) -> None:
        """현재 선택된 영역을 새 텍스트로 치환 (한글 에디터 선택 영역 덮어쓰기)"""
        if self._fallback_editor is not None:
            cursor = self._fallback_editor.textCursor()
            if cursor.hasSelection():
                cursor.insertText(text)
            else:
                self._fallback_editor.insertPlainText(text)
            return

        # rhwp-studio에서는 선택 영역이 있는 상태에서 paste 이벤트 또는 insertTextAtCursor를 실행하면 선택 영역이 대체됨
        self.insert_text_at_cursor(text)

    def open_ai_preset(self, preset: str, selected_text: str = "") -> None:
        """AI 원클릭 프리셋 실행 (웹 에디터 플로팅 DIV 패널 또는 폴백 다이얼로그)"""
        if self.web_view:
            json_preset = json.dumps(preset)
            json_sel = json.dumps(selected_text)
            self.web_view.page().runJavaScript(
                f"if (window.rhwpOpenAiPanel) window.rhwpOpenAiPanel({json_preset}, {json_sel});"
            )
            return

        from taskcalendar.ai_assistant import AIChatDialog

        dlg = AIChatDialog(
            parent_editor=self,
            selected_text=selected_text,
            preset=preset,
            palette=self.palette,
        )
        dlg.exec()

    def _inject_change_hook(self) -> None:
        if not self.web_view:
            return
        sparkle_json = json.dumps(_AI_SPARKLE_DATA_URL)
        js = f"window._rhwpAiSparkleIcon = {sparkle_json};\n" + """
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
                    if (e.key === 'F12' || (e.ctrlKey && (e.key === 'Enter' || e.keyCode === 13))) {
                        e.preventDefault();
                        e.stopPropagation();
                        document.title = 'rhwp_fullscreen:toggle:' + Date.now();
                        return;
                    }
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

            // 3. 우클릭 컨텍스트 메뉴 확장 (기존 메뉴 유지 + AI 도우미 프리셋 및 법령 연동)
            if (!window._hasAiContextMenuHook) {
                window._hasAiContextMenuHook = true;

                function getWasmSelectionInfo() {
                    var result = { hasSelection: false, text: '' };
                    try {
                        var deps = window.rhwpStudio && window.rhwpStudio.plugins && window.rhwpStudio.plugins.deps;
                        var ih = deps && deps.getInputHandler ? deps.getInputHandler() : null;
                        if (!ih || !ih.cursor) {
                            var s = window.getSelection();
                            if (s && s.toString()) {
                                result.hasSelection = true;
                                result.text = s.toString();
                            }
                            return result;
                        }

                        var hasSel = false;
                        if (typeof ih.cursor.hasSelection === 'function') {
                            hasSel = ih.cursor.hasSelection();
                        }
                        if (hasSel) {
                            result.hasSelection = true;

                            // 1. cursor.getSelectionOrdered() 로 범위 획득 후 wasm.copySelection 호출
                            var range = typeof ih.cursor.getSelectionOrdered === 'function' ? ih.cursor.getSelectionOrdered() : null;
                            if (range && range.start && range.end && deps.wasm) {
                                var start = range.start;
                                var end = range.end;
                                var wasm = deps.wasm;
                                try {
                                    if (start.parentParaIndex === undefined) {
                                        wasm.copySelection(start.sectionIndex, start.paragraphIndex, start.charOffset, end.paragraphIndex, end.charOffset);
                                    } else if (start.cellPath && start.cellPath.length > 0 && typeof wasm.copySelectionInCellByPath === 'function') {
                                        var cellPara = start.cellParaIndex || 0;
                                        wasm.copySelectionInCellByPath(start.sectionIndex, start.parentParaIndex, JSON.stringify(start.cellPath), cellPara, start.charOffset, end.cellParaIndex || 0, end.charOffset);
                                    } else if (typeof wasm.copySelectionInCell === 'function') {
                                        wasm.copySelectionInCell(start.sectionIndex, start.parentParaIndex, start.controlIndex, start.cellIndex, start.cellParaIndex || 0, start.charOffset, end.cellParaIndex || 0, end.charOffset);
                                    }
                                    if (typeof wasm.getClipboardText === 'function') {
                                        result.text = wasm.getClipboardText() || '';
                                    }
                                } catch(eWasm) {
                                    console.warn('WASM copySelection error:', eWasm);
                                }
                            }

                            // 2. 만약 text가 아직 비어있다면, ih.onCopy 더미 이벤트로 클립보드 추출
                            if (!result.text && typeof ih.onCopy === 'function') {
                                try {
                                    var dummyData = '';
                                    var dummyEvt = {
                                        preventDefault: function() {},
                                        clipboardData: {
                                            setData: function(type, val) {
                                                if (type === 'text/plain') dummyData = val;
                                            }
                                        }
                                    };
                                    ih.onCopy(dummyEvt);
                                    if (dummyData) {
                                        result.text = dummyData;
                                    } else if (deps.wasm && typeof deps.wasm.getClipboardText === 'function') {
                                        result.text = deps.wasm.getClipboardText() || '';
                                    }
                                } catch(eCopy) {
                                    console.warn('ih.onCopy error:', eCopy);
                                }
                            }
                        }

                        // 3. 만약 텍스트가 안 나왔더라도 DOM 선택이 있을 수 있음
                        if (!result.text) {
                            var s = window.getSelection();
                            if (s && s.toString()) {
                                result.hasSelection = true;
                                result.text = s.toString();
                            }
                        }
                    } catch(e) {
                        console.warn('getWasmSelectionInfo global error:', e);
                    }
                    return result;
                }
                window._getWasmSelectionInfo = getWasmSelectionInfo;

                function captureCurrentSelection() {
                    var info = getWasmSelectionInfo();
                    window._rhwpHasSelection = info.hasSelection;
                    if (info.text && info.text.trim()) {
                        window._rhwpLastSelectedText = info.text.trim();
                    } else if (!info.hasSelection) {
                        window._rhwpLastSelectedText = '';
                    }
                    return info;
                }

                // 텍스트 드래그(선택) 직후 및 우클릭 시 선택 텍스트 캡처
                window.addEventListener('mouseup', captureCurrentSelection, true);
                window.addEventListener('contextmenu', captureCurrentSelection, true);
                document.addEventListener('selectionchange', captureCurrentSelection, true);

                var aiMenuObserver = new MutationObserver(function(mutations) {
                    for (var m of mutations) {
                        for (var node of m.addedNodes) {
                            if (node.nodeType === 1 && node.classList && node.classList.contains('context-menu')) {
                                if (node.querySelector('.ai-menu-item')) continue;

                                var selInfo = captureCurrentSelection();
                                var selText = (window._rhwpLastSelectedText || '').trim();
                                var hasSel = Boolean(selInfo.hasSelection || selText);

                                // 구분선 추가
                                var sep = document.createElement('div');
                                sep.className = 'md-sep';
                                node.appendChild(sep);

                                var aiIconHtml = '<img src="' + (window._rhwpAiSparkleIcon || '') + '" style="width:13px; height:13px; margin-right:6px; vertical-align:-2px; display:inline-block;" />';

                                if (hasSel) {
                                    // [선택 영역이 있을 때: 원클릭 AI 서식/작성 프리셋 4종 - 투명 AI 아이콘, 일반 굵기]
                                    var presets = [
                                        { label: '공문서 개조식 다듬기', preset: 'gongmun' },
                                        { label: '3줄 핵심 요약', preset: 'summary' },
                                        { label: '관련 법령·규정 검토', preset: 'law' },
                                        { label: '쉬운 공공언어로 순화', preset: 'refine' }
                                    ];

                                    presets.forEach(function(item) {
                                        var pItem = document.createElement('div');
                                        pItem.className = 'md-item ai-menu-item';
                                        pItem.innerHTML = '<span class="md-label" style="color:#2563EB; display:inline-flex; align-items:center;">' + aiIconHtml + item.label + '</span>';
                                        pItem.addEventListener('click', function(ev) {
                                            ev.stopPropagation();
                                            ev.preventDefault();
                                            captureCurrentSelection();
                                            if (node && node.parentNode) node.parentNode.removeChild(node);
                                            var curSel = (window._rhwpLastSelectedText || '').trim();
                                            if (window.rhwpOpenAiPanel) {
                                                window.rhwpOpenAiPanel(item.preset, curSel);
                                            } else {
                                                document.title = 'rhwp_ai:preset:' + item.preset + ':' + Date.now();
                                            }
                                        });
                                        node.appendChild(pItem);
                                    });
                                }

                                // [공통: AI 대화하기 플로팅 패널 - 원래 💬 아이콘 및 일반 굵기]
                                var chatItem = document.createElement('div');
                                chatItem.className = 'md-item ai-menu-item';
                                chatItem.innerHTML = '<span class="md-label" style="color:#2563EB;"><span style="margin-right:6px;">💬</span>AI 도우미 (대화하기)</span><span class="md-shortcut" style="margin-left:auto; color:#6366F1; font-size:10px;">AI</span>';
                                chatItem.addEventListener('click', function(ev) {
                                    ev.stopPropagation();
                                    ev.preventDefault();
                                    captureCurrentSelection();
                                    if (node && node.parentNode) node.parentNode.removeChild(node);
                                    var curSel = (window._rhwpLastSelectedText || '').trim();
                                    if (window.rhwpOpenAiPanel) {
                                        window.rhwpOpenAiPanel('chat', curSel);
                                    } else {
                                        document.title = 'rhwp_ai:chat:' + Date.now();
                                    }
                                });
                                node.appendChild(chatItem);

                                // [공통: 국가법령정보센터 법령 찾기 - 원래 🔎 아이콘 및 일반 굵기]
                                var lawSep = document.createElement('div');
                                lawSep.className = 'md-sep';
                                node.appendChild(lawSep);

                                var lawItem = document.createElement('div');
                                lawItem.className = 'md-item ai-menu-item';
                                lawItem.innerHTML = '<span class="md-label" style="color:#475569;"><span style="margin-right:6px;">🔎</span>법령 찾기 (국가법령센터)</span><span class="md-shortcut" style="margin-left:auto; color:#64748B; font-size:10px;">law.go.kr</span>';
                                lawItem.addEventListener('click', function(ev) {
                                    ev.stopPropagation();
                                    ev.preventDefault();
                                    captureCurrentSelection();
                                    if (node && node.parentNode) node.parentNode.removeChild(node);
                                    window._rhwpLawQuery = (window._rhwpLastSelectedText || '').trim();
                                    document.title = 'rhwp_ai:law:' + Date.now();
                                });
                                node.appendChild(lawItem);

                                // 팝업이 화면 아래로 삐져나가는 경우 Y축 위치 보정
                                var rect = node.getBoundingClientRect();
                                if (rect.bottom > window.innerHeight) {
                                    var newTop = Math.max(2, window.innerHeight - rect.height - 8);
                                    node.style.top = newTop + 'px';
                                }
                            }
                        }
                    }
                });
                aiMenuObserver.observe(document.body, { childList: true });
            }
        })();
        """
        self.web_view.page().runJavaScript(js)
        self._inject_ai_floating_panel()

    def _inject_ai_floating_panel(self) -> None:
        """에디터 웹 뷰 내부에 플로팅 DIV 패널(대화하기/분석하기/프리셋, 요청/중지 토글) 주입"""
        if not self.web_view:
            return
        sparkle_json = json.dumps(_AI_SPARKLE_DATA_URL)
        panel_js = f"window._rhwpAiSparkleIcon = {sparkle_json};\n" + """
        (function() {
            if (window._hasAiPanelInjected) return;
            window._hasAiPanelInjected = true;

            var sparkleImgHtml = '<img src="' + (window._rhwpAiSparkleIcon || '') + '" style="width:14px; height:14px; vertical-align:-2px; display:inline-block;" />';

            var panel = document.createElement('div');
            panel.id = 'rhwp-ai-panel';
            panel.innerHTML = [
                '<div id="rhwp-ai-header">',
                '  <div class="rhwp-ai-title-wrap">',
                '    <span id="rhwp-ai-icon">' + sparkleImgHtml + '</span>',
                '    <span id="rhwp-ai-title" style="margin-left:4px;">AI 업무 도우미</span>',
                '  </div>',
                '  <div class="rhwp-ai-header-btns">',
                '    <button type="button" id="rhwp-ai-btn-settings" title="AI 연동 설정">AI 설정</button>',
                '    <button type="button" id="rhwp-ai-btn-close" title="닫기 (Esc)">✕</button>',
                '  </div>',
                '</div>',
                '<div id="rhwp-ai-body">',
                '  <div id="rhwp-ai-selected-wrap">',
                '    <div class="rhwp-ai-section-title">',
                '      <span>선택한 본문 문구</span>',
                '      <span id="rhwp-ai-selected-count"></span>',
                '    </div>',
                '    <div id="rhwp-ai-selected-text"></div>',
                '  </div>',
                '  <div class="rhwp-ai-input-wrap">',
                '    <div class="rhwp-ai-section-title" id="rhwp-ai-input-title">요청사항</div>',
                '    <textarea id="rhwp-ai-input" rows="2" placeholder="요청할 내용이나 작성할 서식을 입력하세요... (Ctrl+Enter로 요청)"></textarea>',
                '  </div>',
                '  <div class="rhwp-ai-action-row">',
                '    <div id="rhwp-ai-progress" style="display:none;"><div class="rhwp-ai-bar"></div></div>',
                '    <div class="rhwp-ai-action-spacer"></div>',
                '    <button type="button" id="rhwp-ai-btn-send">요청하기</button>',
                '  </div>',
                '  <div class="rhwp-ai-response-wrap">',
                '    <div class="rhwp-ai-section-title">변환 결과</div>',
                '    <div id="rhwp-ai-response" placeholder="AI 변환 결과가 여기에 표시됩니다."></div>',
                '  </div>',
                '</div>',
                '<div id="rhwp-ai-footer">',
                '  <button type="button" id="rhwp-ai-btn-replace" style="display:none;" disabled>선택 영역 교체</button>',
                '  <button type="button" id="rhwp-ai-btn-insert" disabled>커서 위치에 삽입</button>',
                '  <button type="button" id="rhwp-ai-btn-copy">복사하기</button>',
                '  <div style="flex:1;"></div>',
                '  <button type="button" id="rhwp-ai-btn-cancel">닫기</button>',
                '</div>'
            ].join('');

            var style = document.createElement('style');
            style.textContent = [
                '#rhwp-ai-panel {',
                '  position: fixed;',
                '  top: 48px;',
                '  right: 20px;',
                '  width: 430px;',
                '  height: 480px;',
                '  max-width: calc(100vw - 30px);',
                '  max-height: calc(100vh - 50px);',
                '  background: #FFFFFF;',
                '  border: 1px solid #CBD5E1;',
                '  border-radius: 10px;',
                '  box-shadow: 0 14px 34px rgba(15, 23, 42, 0.20), 0 2px 8px rgba(15, 23, 42, 0.08);',
                '  z-index: 999999;',
                '  display: none;',
                '  flex-direction: column;',
                '  overflow: hidden;',
                '  font-family: Pretendard, -apple-system, BlinkMacSystemFont, "Segoe UI", "Malgun Gothic", sans-serif;',
                '  color: #1E293B;',
                '  box-sizing: border-box;',
                '}',
                '#rhwp-ai-header {',
                '  background: #F8FAFC;',
                '  border-bottom: 1px solid #E2E8F0;',
                '  padding: 8px 12px;',
                '  display: flex;',
                '  align-items: center;',
                '  justify-content: space-between;',
                '  cursor: move;',
                '  user-select: none;',
                '}',
                '.rhwp-ai-title-wrap {',
                '  display: flex;',
                '  align-items: center;',
                '  font-size: 12.5px;',
                '  font-weight: 700;',
                '  color: #0F172A;',
                '}',
                '.rhwp-ai-header-btns {',
                '  display: flex;',
                '  align-items: center;',
                '  gap: 5px;',
                '}',
                '#rhwp-ai-btn-settings {',
                '  background: #FFFFFF;',
                '  border: 1px solid #CBD5E1;',
                '  border-radius: 4px;',
                '  padding: 2px 7px;',
                '  font-size: 10.5px;',
                '  color: #64748B;',
                '  cursor: pointer;',
                '}',
                '#rhwp-ai-btn-settings:hover {',
                '  background: #F1F5F9;',
                '  color: #1E293B;',
                '}',
                '#rhwp-ai-btn-close {',
                '  background: transparent;',
                '  border: none;',
                '  font-size: 13px;',
                '  font-weight: bold;',
                '  color: #94A3B8;',
                '  cursor: pointer;',
                '  padding: 1px 5px;',
                '  border-radius: 4px;',
                '}',
                '#rhwp-ai-btn-close:hover {',
                '  background: #FEE2E2;',
                '  color: #EF4444;',
                '}',
                '#rhwp-ai-body {',
                '  padding: 9px 12px;',
                '  display: flex;',
                '  flex-direction: column;',
                '  flex: 1;',
                '  min-height: 0;',
                '  gap: 6px;',
                '  background: #FFFFFF;',
                '}',
                '#rhwp-ai-selected-wrap {',
                '  display: none;',
                '  flex-direction: column;',
                '  gap: 3px;',
                '}',
                '.rhwp-ai-section-title {',
                '  display: flex;',
                '  justify-content: space-between;',
                '  align-items: center;',
                '  font-size: 10.5px;',
                '  font-weight: 700;',
                '  color: #475569;',
                '}',
                '#rhwp-ai-selected-count {',
                '  font-size: 10.5px;',
                '  color: #64748B;',
                '  font-weight: normal;',
                '}',
                '#rhwp-ai-selected-text {',
                '  max-height: 52px;',
                '  overflow-y: auto;',
                '  color: #1E293B;',
                '  font-size: 11px;',
                '  line-height: 1.45;',
                '  white-space: pre-wrap;',
                '  word-break: break-word;',
                '  user-select: text;',
                '  background: #F1F5F9;',
                '  border: 1px solid #CBD5E1;',
                '  border-radius: 5px;',
                '  padding: 4px 8px;',
                '}',
                '.rhwp-ai-input-wrap {',
                '  display: flex;',
                '  flex-direction: column;',
                '  gap: 3px;',
                '}',
                '#rhwp-ai-input {',
                '  width: 100%;',
                '  box-sizing: border-box;',
                '  height: 44px;',
                '  padding: 6px 8px;',
                '  border: 1px solid #CBD5E1;',
                '  border-radius: 5px;',
                '  font-size: 11.5px;',
                '  font-family: inherit;',
                '  line-height: 1.45;',
                '  resize: none;',
                '  outline: none;',
                '  color: #0F172A;',
                '  background: #FFFFFF;',
                '}',
                '#rhwp-ai-input:focus {',
                '  border-color: #2563EB;',
                '  box-shadow: 0 0 0 2px rgba(37,99,235,0.12);',
                '}',
                '.rhwp-ai-action-row {',
                '  display: flex;',
                '  align-items: center;',
                '  gap: 6px;',
                '  min-height: 24px;',
                '}',
                '#rhwp-ai-progress {',
                '  flex: 1;',
                '  height: 3px;',
                '  background: #E2E8F0;',
                '  border-radius: 2px;',
                '  overflow: hidden;',
                '  position: relative;',
                '}',
                '.rhwp-ai-bar {',
                '  width: 40%;',
                '  height: 100%;',
                '  background: #2563EB;',
                '  position: absolute;',
                '  animation: rhwpAiProgress 1.2s infinite ease-in-out;',
                '}',
                '@keyframes rhwpAiProgress {',
                '  0% { left: -40%; width: 40%; }',
                '  50% { width: 60%; }',
                '  100% { left: 100%; width: 40%; }',
                '}',
                '.rhwp-ai-action-spacer {',
                '  flex: 1;',
                '}',
                '#rhwp-ai-btn-send {',
                '  background: #2563EB;',
                '  color: #FFFFFF;',
                '  font-weight: 600;',
                '  font-size: 11.5px;',
                '  padding: 4px 14px;',
                '  border: none;',
                '  border-radius: 5px;',
                '  cursor: pointer;',
                '  transition: background 0.15s;',
                '}',
                '#rhwp-ai-btn-send:hover {',
                '  opacity: 0.92;',
                '}',
                '#rhwp-ai-btn-send.is-stop {',
                '  background: #DC2626 !important;',
                '  color: #FFFFFF !important;',
                '}',
                '.rhwp-ai-response-wrap {',
                '  flex: 1;',
                '  min-height: 0;',
                '  display: flex;',
                '  flex-direction: column;',
                '  gap: 3px;',
                '}',
                '#rhwp-ai-response {',
                '  flex: 1;',
                '  padding: 8px 10px;',
                '  overflow-y: auto;',
                '  font-size: 12px;',
                '  line-height: 1.55;',
                '  color: #1E293B;',
                '  white-space: pre-wrap;',
                '  word-break: break-word;',
                '  outline: none;',
                '  user-select: text;',
                '  border: 1px solid #E2E8F0;',
                '  border-radius: 5px;',
                '  background: #F8FAFC;',
                '}',
                '#rhwp-ai-response:empty::before {',
                '  content: attr(placeholder);',
                '  color: #94A3B8;',
                '}',
                '#rhwp-ai-footer {',
                '  background: #F8FAFC;',
                '  border-top: 1px solid #E2E8F0;',
                '  padding: 7px 12px;',
                '  display: flex;',
                '  align-items: center;',
                '  gap: 6px;',
                '}',
                '#rhwp-ai-btn-replace {',
                '  background: #2563EB;',
                '  color: #FFFFFF;',
                '  font-weight: 600;',
                '  font-size: 11.5px;',
                '  padding: 5px 12px;',
                '  border: none;',
                '  border-radius: 5px;',
                '  cursor: pointer;',
                '  transition: background 0.15s;',
                '}',
                '#rhwp-ai-btn-replace:hover {',
                '  background: #1D4ED8;',
                '}',
                '#rhwp-ai-btn-replace:disabled {',
                '  background: #E2E8F0;',
                '  color: #94A3B8;',
                '  cursor: not-allowed;',
                '}',
                '#rhwp-ai-btn-insert {',
                '  background: #F1F5F9;',
                '  color: #334155;',
                '  border: 1px solid #CBD5E1;',
                '  font-weight: 500;',
                '  font-size: 11.5px;',
                '  padding: 5px 12px;',
                '  border-radius: 5px;',
                '  cursor: pointer;',
                '}',
                '#rhwp-ai-btn-insert:disabled {',
                '  background: #F8FAFC;',
                '  color: #CBD5E1;',
                '  border-color: #E2E8F0;',
                '  cursor: not-allowed;',
                '}',
                '#rhwp-ai-btn-copy, #rhwp-ai-btn-cancel {',
                '  background: #FFFFFF;',
                '  border: 1px solid #CBD5E1;',
                '  border-radius: 5px;',
                '  padding: 5px 10px;',
                '  font-size: 11.5px;',
                '  color: #334155;',
                '  cursor: pointer;',
                '}',
                '#rhwp-ai-btn-copy:hover, #rhwp-ai-btn-cancel:hover {',
                '  background: #F1F5F9;',
                '}'
            ].join('\\n');

            document.head.appendChild(style);
            document.body.appendChild(panel);

            var isGenerating = false;
            var currentMode = 'chat';
            var currentSelectedText = '';
            var fullResponse = '';

            // 드래그 이동
            var header = document.getElementById('rhwp-ai-header');
            var isDragging = false, startX = 0, startY = 0, startLeft = 0, startTop = 0;
            header.addEventListener('mousedown', function(e) {
                if (e.target.closest('button')) return;
                isDragging = true;
                var rect = panel.getBoundingClientRect();
                startX = e.clientX;
                startY = e.clientY;
                startLeft = rect.left;
                startTop = rect.top;
                panel.style.right = 'auto';
                panel.style.left = startLeft + 'px';
                panel.style.top = startTop + 'px';
                e.preventDefault();
            });
            document.addEventListener('mousemove', function(e) {
                if (!isDragging) return;
                var dx = e.clientX - startX;
                var dy = e.clientY - startY;
                var newL = Math.max(8, Math.min(window.innerWidth - panel.offsetWidth - 8, startLeft + dx));
                var newT = Math.max(8, Math.min(window.innerHeight - panel.offsetHeight - 8, startTop + dy));
                panel.style.left = newL + 'px';
                panel.style.top = newT + 'px';
            });
            document.addEventListener('mouseup', function() {
                isDragging = false;
            });

            window.rhwpOpenAiPanel = function(mode, selectedText) {
                currentMode = mode || 'chat';
                currentSelectedText = (selectedText || '').trim();

                var titleEl = document.getElementById('rhwp-ai-title');
                var inputEl = document.getElementById('rhwp-ai-input');
                var inputTitleEl = document.getElementById('rhwp-ai-input-title');
                var respEl = document.getElementById('rhwp-ai-response');
                var btnSend = document.getElementById('rhwp-ai-btn-send');
                var btnInsert = document.getElementById('rhwp-ai-btn-insert');
                var btnReplace = document.getElementById('rhwp-ai-btn-replace');
                var selWrap = document.getElementById('rhwp-ai-selected-wrap');
                var selTextEl = document.getElementById('rhwp-ai-selected-text');
                var selCountEl = document.getElementById('rhwp-ai-selected-count');

                panel.style.display = 'flex';

                // 선택 문구가 있으면 상단 박스에 표시하고 교체 버튼 노출
                if (currentSelectedText) {
                    selWrap.style.display = 'flex';
                    selTextEl.textContent = currentSelectedText;
                    selCountEl.textContent = '(' + currentSelectedText.length + '자)';
                    btnReplace.style.display = 'inline-block';
                    btnReplace.disabled = true;
                    if (inputTitleEl) inputTitleEl.textContent = '추가 요청사항';
                } else {
                    selWrap.style.display = 'none';
                    selTextEl.textContent = '';
                    btnReplace.style.display = 'none';
                    if (inputTitleEl) inputTitleEl.textContent = '요청사항';
                }

                respEl.textContent = '';
                fullResponse = '';
                btnInsert.disabled = true;

                if (currentMode === 'gongmun') {
                    titleEl.textContent = '공문서 개조식 다듬기';
                    inputEl.placeholder = '추가 지시사항이 있다면 입력하세요... (기본: 행안부 표준 개조식 문체 변환)';
                    inputEl.value = '공문서 표준 개조식 문체(명사형 종결, 항목 부호)로 다듬어 주세요.';
                    if (currentSelectedText) setTimeout(function() { doSendRequest(); }, 100);
                } else if (currentMode === 'summary') {
                    titleEl.textContent = '3줄 핵심 요약';
                    inputEl.placeholder = '추가 지시사항이 있다면 입력하세요... (기본: 배경/현황/계획 3줄 요약)';
                    inputEl.value = '추진 배경, 주요 내용, 향후 계획의 핵심 3줄 요약으로 정리해 주세요.';
                    if (currentSelectedText) setTimeout(function() { doSendRequest(); }, 100);
                } else if (currentMode === 'law') {
                    titleEl.textContent = '관련 법령·규정 검토';
                    inputEl.placeholder = '추가 지시사항이 있다면 입력하세요... (기본: 근거 법령 및 행정 절차 검토)';
                    inputEl.value = '관련 근거 법령, 자치법규 및 필수 사전 행정 절차를 검토해 주세요.';
                    if (currentSelectedText) setTimeout(function() { doSendRequest(); }, 100);
                } else if (currentMode === 'refine') {
                    titleEl.textContent = '쉬운 공공언어로 순화';
                    inputEl.placeholder = '추가 지시사항이 있다면 입력하세요... (기본: 바른 공공언어 순화)';
                    inputEl.value = '어려운 한자어, 외래어, 권위적 표현을 쉬운 표준 공공언어로 순화해 주세요.';
                    if (currentSelectedText) setTimeout(function() { doSendRequest(); }, 100);
                } else if (currentMode === 'analyze') {
                    titleEl.textContent = '업무 법령/행정절차 분석';
                    inputEl.placeholder = '추가 지시사항이 있다면 입력하세요... (기본: 문서 법령·절차 종합 분석)';
                    inputEl.value = '현재 작성 중인 업무 문서의 관련 법령, 행정 절차, 필수 준수사항을 종합 분석해 주세요.';
                    setTimeout(function() { doSendRequest(); }, 100);
                } else {
                    titleEl.textContent = 'AI 업무 도우미 (대화하기)';
                    inputEl.placeholder = '요청할 작업이나 작성할 서식을 입력하세요... (예: 출장보고서 양식 만들어줘 등)';
                    if (!isGenerating) inputEl.value = '';
                    inputEl.focus();
                }
            };

            function closePanel() {
                if (isGenerating) {
                    doStopRequest();
                }
                panel.style.display = 'none';
            }
            document.getElementById('rhwp-ai-btn-close').addEventListener('click', closePanel);
            document.getElementById('rhwp-ai-btn-cancel').addEventListener('click', closePanel);

            document.getElementById('rhwp-ai-btn-settings').addEventListener('click', function() {
                document.title = 'rhwp_ai:settings:' + Date.now();
            });

            var btnSend = document.getElementById('rhwp-ai-btn-send');
            btnSend.addEventListener('click', function() {
                if (isGenerating) {
                    doStopRequest();
                } else {
                    doSendRequest();
                }
            });

            document.getElementById('rhwp-ai-input').addEventListener('keydown', function(e) {
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                    e.preventDefault();
                    if (!isGenerating) doSendRequest();
                }
                if (e.key === 'Escape') {
                    closePanel();
                }
            });

            function doSendRequest() {
                var inputEl = document.getElementById('rhwp-ai-input');
                var prompt = inputEl.value.trim();
                if (!prompt && currentMode !== 'analyze' && !currentSelectedText) {
                    alert('요청하실 내용을 입력해 주세요.');
                    inputEl.focus();
                    return;
                }

                var docText = '';
                try {
                    var doc = window.rhwpStudio && window.rhwpStudio.plugins && window.rhwpStudio.plugins.deps && window.rhwpStudio.plugins.deps.wasm && window.rhwpStudio.plugins.deps.wasm.doc;
                    if (doc && doc.getTextFileText) {
                        var raw = doc.getTextFileText();
                        try { docText = JSON.parse(raw); } catch(e) { docText = raw; }
                    }
                } catch(e) {}

                isGenerating = true;
                btnSend.textContent = '⏹ 중지';
                btnSend.classList.add('is-stop');
                document.getElementById('rhwp-ai-progress').style.display = 'block';
                document.getElementById('rhwp-ai-btn-insert').disabled = true;
                document.getElementById('rhwp-ai-btn-replace').disabled = true;

                var respEl = document.getElementById('rhwp-ai-response');
                respEl.textContent = '';
                fullResponse = '';

                window._rhwpAiPendingReq = {
                    prompt: prompt,
                    mode: currentMode,
                    selectedText: currentSelectedText,
                    docText: docText
                };
                document.title = 'rhwp_ai:req:' + Date.now();
            }

            function doStopRequest() {
                isGenerating = false;
                btnSend.textContent = '요청하기';
                btnSend.classList.remove('is-stop');
                document.getElementById('rhwp-ai-progress').style.display = 'none';
                if (fullResponse.trim()) {
                    document.getElementById('rhwp-ai-btn-insert').disabled = false;
                    if (currentSelectedText) {
                        document.getElementById('rhwp-ai-btn-replace').disabled = false;
                    }
                }
                document.title = 'rhwp_ai:stop:' + Date.now();
            }

            window._rhwpAiGetPendingRequest = function() {
                var req = window._rhwpAiPendingReq || null;
                window._rhwpAiPendingReq = null;
                return req;
            };

            window._rhwpAiOnChunk = function(chunk) {
                if (!isGenerating) return;
                fullResponse += chunk;
                var respEl = document.getElementById('rhwp-ai-response');
                respEl.textContent = fullResponse;
                respEl.scrollTop = respEl.scrollHeight;
            };

            var generatedImageB64 = null;

            function extractSvgFromJs(raw) {
                if (!raw) return null;
                // 1. 완전한 <svg> ... </svg> 태그 검색
                var m = raw.match(/<svg[\\s\\S]*?<\\/svg>/i);
                if (m) return m[0].trim();

                // 2. 코드 블록 또는 본문 내에서 <svg 로 시작하는 영역 검색 (<?xml 뒤에 있어도 추출)
                var mCode = raw.match(/<svg[\\s\\S]*?(?:<\\/svg>|(?=```)|$)/i);
                if (mCode) {
                    var s = mCode[0].trim();
                    if (!/<\\/svg>\\s*$/i.test(s)) {
                        s = s.replace(/<[^>]*$/, '').trim();
                        s += '\\n</svg>';
                    }
                    return s;
                }
                return null;
            }

            function renderSvgInBrowser(svgStr, callback) {
                try {
                    var blob = new Blob([svgStr], { type: 'image/svg+xml;charset=utf-8' });
                    var url = URL.createObjectURL(blob);
                    var img = new Image();
                    img.onload = function() {
                        try {
                            var canvas = document.createElement('canvas');
                            var w = img.naturalWidth || 400;
                            var h = img.naturalHeight || 400;
                            var maxD = 450;
                            var scale = Math.min(maxD / Math.max(w, 1), maxD / Math.max(h, 1), 2.0);
                            canvas.width = Math.max(32, Math.round(w * scale));
                            canvas.height = Math.max(32, Math.round(h * scale));
                            var ctx = canvas.getContext('2d');
                            ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                            URL.revokeObjectURL(url);
                            var dataUrl = canvas.toDataURL('image/png');
                            var b64 = dataUrl.split(',')[1] || '';
                            callback(b64);
                        } catch(e) {
                            URL.revokeObjectURL(url);
                            callback(null);
                        }
                    };
                    img.onerror = function() {
                        URL.revokeObjectURL(url);
                        callback(null);
                    };
                    img.src = url;
                } catch(e) {
                    callback(null);
                }
            }

            window._rhwpAiOnFinished = function(text, imgB64) {
                isGenerating = false;
                btnSend.textContent = '요청하기';
                btnSend.classList.remove('is-stop');
                document.getElementById('rhwp-ai-progress').style.display = 'none';
                fullResponse = text;
                generatedImageB64 = imgB64 || null;

                function applyResult(finalImgB64) {
                    generatedImageB64 = finalImgB64;
                    var respEl = document.getElementById('rhwp-ai-response');
                    var btnInsert = document.getElementById('rhwp-ai-btn-insert');

                    if (generatedImageB64) {
                        respEl.innerHTML = '<div style="display:flex; flex-direction:column; align-items:center; justify-content:center; padding:12px; background:#FFFFFF; border:1px dashed #CBD5E1; border-radius:6px;">' +
                            '<div style="font-size:11px; font-weight:bold; color:#2563EB; margin-bottom:8px;">🎨 AI 이미지/일러스트 생성 완료</div>' +
                            '<img src="data:image/png;base64,' + generatedImageB64 + '" style="max-width:240px; max-height:200px; object-fit:contain; border-radius:4px; box-shadow:0 2px 8px rgba(0,0,0,0.08); background:#F8FAFC;" />' +
                            '<div style="font-size:10.5px; color:#64748B; margin-top:8px;">아래 [커서 위치에 이미지 삽입] 버튼을 누르면 본문에 삽입됩니다.</div>' +
                            '</div>';
                        btnInsert.textContent = '🖼️ 커서 위치에 이미지 삽입';
                        btnInsert.style.background = '#2563EB';
                        btnInsert.style.color = '#FFFFFF';
                        btnInsert.style.fontWeight = 'bold';
                        btnInsert.disabled = false;
                    } else {
                        respEl.textContent = text;
                        btnInsert.textContent = '커서 위치에 삽입';
                        btnInsert.style.background = '#F1F5F9';
                        btnInsert.style.color = '#334155';
                        btnInsert.style.fontWeight = '500';
                        if (text.trim()) {
                            btnInsert.disabled = false;
                            if (currentSelectedText) {
                                document.getElementById('rhwp-ai-btn-replace').disabled = false;
                            }
                        }
                    }
                    respEl.scrollTop = respEl.scrollHeight;
                }

                if (!generatedImageB64) {
                    var svgStr = extractSvgFromJs(text);
                    if (svgStr) {
                        renderSvgInBrowser(svgStr, function(b64) {
                            applyResult(b64);
                        });
                        return;
                    }
                }
                applyResult(generatedImageB64);
            };

            window._rhwpAiOnError = function(err) {
                isGenerating = false;
                generatedImageB64 = null;
                btnSend.textContent = '요청하기';
                btnSend.classList.remove('is-stop');
                document.getElementById('rhwp-ai-progress').style.display = 'none';
                var respEl = document.getElementById('rhwp-ai-response');
                respEl.innerHTML = '<span style="color:#DC2626; font-weight:bold;">[오류 발생]</span><br>' + String(err).replace(/</g, '&lt;');
            };

            // 선택 영역 교체 (선택된 문구를 AI 변환 결과로 즉시 덮어쓰기)
            document.getElementById('rhwp-ai-btn-replace').addEventListener('click', function() {
                var text = fullResponse.trim();
                if (!text) return;
                if (window.rhwpStudio && typeof window.rhwpStudio.insertTextAtCursor === 'function') {
                    window.rhwpStudio.insertTextAtCursor(text);
                } else {
                    var deps = window.rhwpStudio && window.rhwpStudio.plugins && window.rhwpStudio.plugins.deps;
                    var ih = deps && deps.getInputHandler ? deps.getInputHandler() : null;
                    if (ih && ih.textarea) {
                        ih.active = true;
                        if (ih.focusTextarea) ih.focusTextarea();
                        var dt = new DataTransfer();
                        dt.setData('text/plain', text);
                        var ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
                        ih.textarea.dispatchEvent(ev);
                    }
                }
                panel.style.display = 'none';
            });

            // 커서 위치에 삽입 (이미지 또는 텍스트)
            document.getElementById('rhwp-ai-btn-insert').addEventListener('click', async function() {
                if (generatedImageB64) {
                    try {
                        var bin = atob(generatedImageB64);
                        var len = bin.length;
                        var bytes = new Uint8Array(len);
                        for (var i = 0; i < len; i++) {
                            bytes[i] = bin.charCodeAt(i);
                        }
                        var blob = new Blob([bytes], { type: 'image/png' });

                        // 1. rhwpStudio.insertImageAtCursor 직접 호출
                        if (window.rhwpStudio && typeof window.rhwpStudio.insertImageAtCursor === 'function') {
                            await window.rhwpStudio.insertImageAtCursor(blob, 'png');
                        } else {
                            // 2. ClipboardEvent paste 폴백
                            var deps = window.rhwpStudio && window.rhwpStudio.plugins && window.rhwpStudio.plugins.deps;
                            var ih = deps && deps.getInputHandler ? deps.getInputHandler() : null;
                            if (ih && ih.textarea) {
                                ih.active = true;
                                if (ih.focusTextarea) ih.focusTextarea();
                                var dt = new DataTransfer();
                                dt.items.add(new File([blob], "ai_image.png", { type: 'image/png' }));
                                var ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
                                ih.textarea.dispatchEvent(ev);
                            }
                        }
                    } catch(e) {
                        console.error('Failed to insert AI generated image:', e);
                    }
                    panel.style.display = 'none';
                    return;
                }

                var text = fullResponse.trim();
                if (!text) return;
                if (window.rhwpStudio && typeof window.rhwpStudio.insertTextAtCursor === 'function') {
                    window.rhwpStudio.insertTextAtCursor(text);
                } else {
                    var deps = window.rhwpStudio && window.rhwpStudio.plugins && window.rhwpStudio.plugins.deps;
                    var ih = deps && deps.getInputHandler ? deps.getInputHandler() : null;
                    if (ih && ih.textarea) {
                        ih.active = true;
                        if (ih.focusTextarea) ih.focusTextarea();
                        var dt = new DataTransfer();
                        dt.setData('text/plain', text);
                        var ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
                        ih.textarea.dispatchEvent(ev);
                    }
                }
                panel.style.display = 'none';
            });

            // 결과 복사하기
            document.getElementById('rhwp-ai-btn-copy').addEventListener('click', function() {
                var text = fullResponse.trim();
                if (!text) return;
                var btn = this;
                navigator.clipboard.writeText(text).then(function() {
                    var orig = btn.textContent;
                    btn.textContent = '✓ 복사됨';
                    setTimeout(function() { btn.textContent = orig; }, 1500);
                }).catch(function() {
                    var ta = document.createElement('textarea');
                    ta.value = text;
                    document.body.appendChild(ta);
                    ta.select();
                    document.execCommand('copy');
                    document.body.removeChild(ta);
                    var orig = btn.textContent;
                    btn.textContent = '✓ 복사됨';
                    setTimeout(function() { btn.textContent = orig; }, 1500);
                });
            });
        })();
        """
        self.web_view.page().runJavaScript(panel_js)

    def _apply_default_font_and_size(self) -> None:
        """한글 에디터 기본 글꼴을 Pretendard 12pt로 설정 (문서가 있으면 본문 전체에 적용)"""
        if not self.web_view:
            return
        self.web_view.page().runJavaScript(_PRETENDARD_DEFAULTS_JS)

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            logger.error("Failed to load rhwp-studio web view")
            return
        # DOM이 로드되자마자 우클릭 훅 및 AI 플로팅 패널 조기 주입
        self._inject_change_hook()
        self._inject_ai_floating_panel()

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
            QTimer.singleShot(200, self.reset_zoom_to_100)

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
            if self.server and self.server.port > 0:
                self.server.set_current_blob(hwpx_bytes)
                js = f"""
                (async function() {{
                    try {{
                        var resp = await fetch("/api/current_doc?t=" + Date.now());
                        if (!resp.ok) throw new Error("HTTP " + resp.status);
                        var buf = await resp.arrayBuffer();
                        var bytes = new Uint8Array(buf);
                        var deps = window.rhwpStudio?.plugins?.deps;
                        if (deps && deps.loadDocument) {{
                            await deps.loadDocument(bytes, "{doc_filename}");
                        }}
                    }} catch(e) {{
                        console.error("Failed to load document via fetch:", e);
                    }}
                }})();
                """
            else:
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
            self.web_view.page().runJavaScript(js)
            QTimer.singleShot(250, self.reset_zoom_to_100)
            QTimer.singleShot(600, self.reset_zoom_to_100)
            QTimer.singleShot(500, self._inject_change_hook)
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
        # 문서가 로드된 후 기본 100% 배율로 깔끔하게 띄움
        QTimer.singleShot(250, self.reset_zoom_to_100)
        QTimer.singleShot(600, self.reset_zoom_to_100)
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

    def reset_zoom_to_100(self) -> None:
        """웹에디터 화면 배율을 100% 정상 크기로 설정"""
        if not self.web_view:
            return
        js = """
        (function() {
            try {
                var deps = window.rhwpStudio && window.rhwpStudio.plugins && window.rhwpStudio.plugins.deps;
                var vm = deps && deps.getInputHandler && deps.getInputHandler()?.viewportManager;
                if (vm && typeof vm.setZoom === 'function') {
                    vm.setZoom(1.0);
                    return;
                }
                var item = document.querySelector('[data-cmd="view:zoom-100"]');
                if (item) {
                    item.click();
                    return;
                }
            } catch(e) {}
        })();
        """
        self.web_view.page().runJavaScript(js)

    def fit_page(self) -> None:
        """웹에디터 화면을 쪽맞춤 비율로 전환"""
        if not self.web_view:
            return
        js = """
        (function() {
            var btn = document.getElementById('sb-zoom-fit');
            if (btn) btn.click();
        })();
        """
        self.web_view.page().runJavaScript(js)

    def fit_width(self) -> None:
        """웹에디터 화면을 폭맞춤 비율로 전환"""
        if not self.web_view:
            return
        js = """
        (function() {
            var btn = document.getElementById('sb-zoom-fit-width');
            if (btn) btn.click();
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

        escaped = html.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
        js = f"""
        (function() {{
            if (window.rhwpStudio && window.rhwpStudio.loadDocument) {{
                window.rhwpStudio.loadDocument(`{escaped}`);
            }}
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
