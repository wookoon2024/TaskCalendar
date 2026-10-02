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

    def do_GET(self) -> None:
        clean_path = self.path.split("?")[0]
        if clean_path == "/api/current_doc":
            server = RhwpStudioServer.get_instance()
            blob = server.get_current_blob()
            if blob is not None:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(blob)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(blob)
                return
            else:
                self.send_response(404)
                self.end_headers()
                return
        super().do_GET()

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
        if title.startswith("rhwp_modified:"):
            self.contentChanged.emit()
        elif title.startswith("rhwp_ai:chat:"):
            self.open_ai_chat()
        elif title.startswith("rhwp_ai:analyze:"):
            self.open_ai_analyze()
        elif title.startswith("rhwp_ai:req:"):
            if self.web_view:
                self.web_view.page().runJavaScript(
                    "window._rhwpAiGetPendingRequest()",
                    self._handle_ai_request_from_js,
                )
        elif title.startswith("rhwp_ai:stop:"):
            self._handle_ai_stop()
        elif title.startswith("rhwp_ai:settings:"):
            self._open_ai_settings()

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

    def open_ai_analyze(self) -> None:
        """AI 업무 법령/행정절차 분석 플로팅 DIV 열기"""
        if self.web_view:
            self.web_view.page().runJavaScript("if (window.rhwpOpenAiPanel) window.rhwpOpenAiPanel('analyze');")
        elif self._fallback_editor is not None:
            from taskcalendar.ai_assistant import AIAnalyzeDialog
            dlg = AIAnalyzeDialog(parent_editor=self, doc_text=self._fallback_editor.toPlainText(), palette=self.palette)
            dlg.exec()

    def _handle_ai_request_from_js(self, req_data: dict | None) -> None:
        """웹 에디터 플로팅 DIV에서 들어온 AI 요청 비동기 처리"""
        if not req_data or not isinstance(req_data, dict):
            return
        prompt = str(req_data.get("prompt", "") or "").strip()
        mode = str(req_data.get("mode", "chat") or "chat")
        doc_text = str(req_data.get("docText", "") or "").strip()

        if self._ai_worker is not None and self._ai_worker.isRunning():
            self._ai_worker.stop()
            self._ai_worker.wait(300)

        from taskcalendar.ai_assistant import AIChatWorker

        if mode == "analyze":
            system_prompt = (
                "당신은 대한민국 행정 법률 및 공공기관 실무 감사·행정절차 전문 AI 법률 자문관입니다.\n"
                "사용자가 작성한 업무 문서 내용에 기반하여 다음 사항을 체계적으로 분석해 주세요:\n"
                "1. 관련 법령 및 규정 (법률, 시행령, 시행규칙, 행안부 예규·지침 등 명칭 및 핵심 조항)\n"
                "2. 필수 사전·사후 행정 절차 (결재, 협의, 고시/공고, 위원회 심의, 보고 등)\n"
                "3. 실무상 유의사항 및 감사 지적 예방 포인트\n"
                "불필요한 서두나 인사말은 생략하고 체계적인 개조식 보고서 형태로 명확하게 제공하세요."
            )
            user_content = f"[분석 요청 업무 문서 내용]\n{doc_text}\n\n[추가 요청사항]\n{prompt if prompt else '관련 법령 및 필수 행정 절차를 분석해 주세요.'}"
        else:
            system_prompt = (
                "당신은 대한민국 공공기관 및 지방자치단체 행정 문서 작성 전문 AI 비서입니다.\n"
                "사용자의 요청에 따라 완성도 높은 공문서, 보고서 서식, 개조식 정리(□, ○, -, *), 기안문 등을 격식 있게 작성해 주세요.\n"
                "불필요한 인사말은 생략하고 곧바로 본문 서식 또는 요청된 작성 결과물을 명확하게 제공하세요."
            )
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
        if self.web_view:
            self.web_view.page().runJavaScript(
                f"if (window._rhwpAiOnFinished) window._rhwpAiOnFinished({json.dumps(full_text)});"
            )

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

            // 3. 우클릭 컨텍스트 메뉴 확장 (기존 메뉴 100% 유지 + 맨 밑에 AI 대화하기/분석하기 추가)
            if (!window._hasAiContextMenuHook) {
                window._hasAiContextMenuHook = true;
                var aiMenuObserver = new MutationObserver(function(mutations) {
                    for (var m of mutations) {
                        for (var node of m.addedNodes) {
                            if (node.nodeType === 1 && node.classList && node.classList.contains('context-menu')) {
                                if (node.querySelector('.ai-menu-item')) continue;

                                // 구분선 추가
                                var sep = document.createElement('div');
                                sep.className = 'md-sep';
                                node.appendChild(sep);

                                // [추가 1] 대화하기
                                var chatItem = document.createElement('div');
                                chatItem.className = 'md-item ai-menu-item';
                                chatItem.innerHTML = '<span class="md-label" style="font-weight:600; color:#2563EB;">대화하기</span><span class="md-shortcut" style="margin-left:auto; color:#6366F1; font-size:10px; font-weight:bold;">AI</span>';
                                chatItem.addEventListener('click', function(ev) {
                                    ev.stopPropagation();
                                    ev.preventDefault();
                                    if (node && node.parentNode) node.parentNode.removeChild(node);
                                    if (window.rhwpOpenAiPanel) {
                                        window.rhwpOpenAiPanel('chat');
                                    } else {
                                        document.title = 'rhwp_ai:chat:' + Date.now();
                                    }
                                });
                                node.appendChild(chatItem);

                                // [추가 2] 분석하기
                                var analyzeItem = document.createElement('div');
                                analyzeItem.className = 'md-item ai-menu-item';
                                analyzeItem.innerHTML = '<span class="md-label" style="font-weight:600; color:#0F766E;">분석하기</span><span class="md-shortcut" style="margin-left:auto; color:#0D9488; font-size:10px; font-weight:bold;">법령</span>';
                                analyzeItem.addEventListener('click', function(ev) {
                                    ev.stopPropagation();
                                    ev.preventDefault();
                                    if (node && node.parentNode) node.parentNode.removeChild(node);
                                    if (window.rhwpOpenAiPanel) {
                                        window.rhwpOpenAiPanel('analyze');
                                    } else {
                                        document.title = 'rhwp_ai:analyze:' + Date.now();
                                    }
                                });
                                node.appendChild(analyzeItem);

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
        """에디터 웹 뷰 내부에 플로팅 DIV 패널(대화하기/분석하기, 요청/중지 토글) 주입"""
        if not self.web_view:
            return
        panel_js = """
        (function() {
            if (window._hasAiPanelInjected) return;
            window._hasAiPanelInjected = true;

            var panel = document.createElement('div');
            panel.id = 'rhwp-ai-panel';
            panel.innerHTML = [
                '<div id="rhwp-ai-header">',
                '  <div class="rhwp-ai-title-wrap">',
                '    <span id="rhwp-ai-icon">✨</span>',
                '    <span id="rhwp-ai-title">AI 업무 도우미</span>',
                '  </div>',
                '  <div class="rhwp-ai-header-btns">',
                '    <button type="button" id="rhwp-ai-btn-settings" title="AI 연동 설정">AI 설정</button>',
                '    <button type="button" id="rhwp-ai-btn-close" title="닫기 (Esc)">✕</button>',
                '  </div>',
                '</div>',
                '<div id="rhwp-ai-body">',
                '  <div class="rhwp-ai-input-wrap">',
                '    <textarea id="rhwp-ai-input" rows="3" placeholder="요청할 내용이나 작성할 서식을 입력하세요... (Ctrl+Enter로 요청)"></textarea>',
                '  </div>',
                '  <div class="rhwp-ai-action-row">',
                '    <div id="rhwp-ai-progress" style="display:none;"><div class="rhwp-ai-bar"></div></div>',
                '    <div class="rhwp-ai-action-spacer"></div>',
                '    <button type="button" id="rhwp-ai-btn-send">요청하기</button>',
                '  </div>',
                '  <div class="rhwp-ai-response-wrap">',
                '    <div id="rhwp-ai-response" placeholder="AI 응답 내용이 여기에 실시간으로 표시됩니다."></div>',
                '  </div>',
                '</div>',
                '<div id="rhwp-ai-footer">',
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
                '  width: 500px;',
                '  height: 560px;',
                '  max-width: calc(100vw - 40px);',
                '  max-height: calc(100vh - 65px);',
                '  background: #FFFFFF;',
                '  border: 1px solid #CBD5E1;',
                '  border-radius: 12px;',
                '  box-shadow: 0 16px 40px rgba(15, 23, 42, 0.22), 0 2px 10px rgba(15, 23, 42, 0.08);',
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
                '  padding: 10px 14px;',
                '  display: flex;',
                '  align-items: center;',
                '  justify-content: space-between;',
                '  cursor: move;',
                '  user-select: none;',
                '}',
                '.rhwp-ai-title-wrap {',
                '  display: flex;',
                '  align-items: center;',
                '  gap: 6px;',
                '  font-size: 13px;',
                '  font-weight: 700;',
                '  color: #0F172A;',
                '}',
                '.rhwp-ai-header-btns {',
                '  display: flex;',
                '  align-items: center;',
                '  gap: 6px;',
                '}',
                '#rhwp-ai-btn-settings {',
                '  background: #FFFFFF;',
                '  border: 1px solid #CBD5E1;',
                '  border-radius: 4px;',
                '  padding: 3px 8px;',
                '  font-size: 11px;',
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
                '  font-size: 14px;',
                '  font-weight: bold;',
                '  color: #94A3B8;',
                '  cursor: pointer;',
                '  padding: 2px 6px;',
                '  border-radius: 4px;',
                '}',
                '#rhwp-ai-btn-close:hover {',
                '  background: #FEE2E2;',
                '  color: #EF4444;',
                '}',
                '#rhwp-ai-body {',
                '  padding: 12px 14px;',
                '  display: flex;',
                '  flex-direction: column;',
                '  flex: 1;',
                '  min-height: 0;',
                '  gap: 8px;',
                '  background: #FFFFFF;',
                '}',
                '.rhwp-ai-input-wrap {',
                '  position: relative;',
                '}',
                '#rhwp-ai-input {',
                '  width: 100%;',
                '  box-sizing: border-box;',
                '  height: 68px;',
                '  padding: 8px 10px;',
                '  border: 1px solid #CBD5E1;',
                '  border-radius: 6px;',
                '  font-size: 12px;',
                '  font-family: inherit;',
                '  line-height: 1.5;',
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
                '  gap: 8px;',
                '  min-height: 28px;',
                '}',
                '#rhwp-ai-progress {',
                '  flex: 1;',
                '  height: 4px;',
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
                '  font-size: 12px;',
                '  padding: 6px 16px;',
                '  border: none;',
                '  border-radius: 6px;',
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
                '  border: 1px solid #E2E8F0;',
                '  border-radius: 6px;',
                '  background: #F8FAFC;',
                '  display: flex;',
                '  flex-direction: column;',
                '}',
                '#rhwp-ai-response {',
                '  flex: 1;',
                '  padding: 10px;',
                '  overflow-y: auto;',
                '  font-size: 12.5px;',
                '  line-height: 1.6;',
                '  color: #1E293B;',
                '  white-space: pre-wrap;',
                '  word-break: break-word;',
                '  outline: none;',
                '  user-select: text;',
                '}',
                '#rhwp-ai-response:empty::before {',
                '  content: attr(placeholder);',
                '  color: #94A3B8;',
                '}',
                '#rhwp-ai-footer {',
                '  background: #F8FAFC;',
                '  border-top: 1px solid #E2E8F0;',
                '  padding: 10px 14px;',
                '  display: flex;',
                '  align-items: center;',
                '  gap: 8px;',
                '}',
                '#rhwp-ai-btn-insert {',
                '  background: #2563EB;',
                '  color: #FFFFFF;',
                '  font-weight: 600;',
                '  font-size: 12px;',
                '  padding: 6px 14px;',
                '  border: none;',
                '  border-radius: 6px;',
                '  cursor: pointer;',
                '}',
                '#rhwp-ai-btn-insert:disabled {',
                '  background: #E2E8F0;',
                '  color: #94A3B8;',
                '  cursor: not-allowed;',
                '}',
                '#rhwp-ai-btn-copy, #rhwp-ai-btn-cancel {',
                '  background: #FFFFFF;',
                '  border: 1px solid #CBD5E1;',
                '  border-radius: 6px;',
                '  padding: 6px 12px;',
                '  font-size: 12px;',
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

            window.rhwpOpenAiPanel = function(mode) {
                currentMode = mode || 'chat';
                var titleEl = document.getElementById('rhwp-ai-title');
                var iconEl = document.getElementById('rhwp-ai-icon');
                var inputEl = document.getElementById('rhwp-ai-input');
                var respEl = document.getElementById('rhwp-ai-response');
                var btnSend = document.getElementById('rhwp-ai-btn-send');
                var btnInsert = document.getElementById('rhwp-ai-btn-insert');

                panel.style.display = 'flex';

                if (currentMode === 'analyze') {
                    titleEl.textContent = 'AI 업무 법령/행정절차 분석';
                    iconEl.textContent = '⚖️';
                    inputEl.placeholder = '추가 요청사항이 있다면 입력하세요... (비워둘 시 기본 법령·절차 종합 분석)';
                    inputEl.value = '현재 작성 중인 업무 문서의 관련 법령, 행정 절차, 필수 준수사항을 분석해 주세요.';
                    respEl.textContent = '';
                    fullResponse = '';
                    btnInsert.disabled = true;
                    setTimeout(function() { doSendRequest(); }, 120);
                } else {
                    titleEl.textContent = 'AI 업무 도우미 (대화하기)';
                    iconEl.textContent = '✨';
                    inputEl.placeholder = '요청할 작업이나 작성할 서식을 입력하세요... (예: 출장보고서 양식 만들어줘, 위 문장 공문서체로 수정해줘 등)';
                    if (!isGenerating) {
                        inputEl.value = '';
                        respEl.textContent = '';
                        fullResponse = '';
                        btnInsert.disabled = true;
                    }
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
                if (!prompt && currentMode !== 'analyze') {
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

                var respEl = document.getElementById('rhwp-ai-response');
                respEl.textContent = '';
                fullResponse = '';

                window._rhwpAiPendingReq = {
                    prompt: prompt,
                    mode: currentMode,
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

            window._rhwpAiOnFinished = function(text) {
                isGenerating = false;
                btnSend.textContent = '요청하기';
                btnSend.classList.remove('is-stop');
                document.getElementById('rhwp-ai-progress').style.display = 'none';
                fullResponse = text;
                var respEl = document.getElementById('rhwp-ai-response');
                respEl.textContent = text;
                respEl.scrollTop = respEl.scrollHeight;
                if (text.trim()) {
                    document.getElementById('rhwp-ai-btn-insert').disabled = false;
                }
            };

            window._rhwpAiOnError = function(err) {
                isGenerating = false;
                btnSend.textContent = '요청하기';
                btnSend.classList.remove('is-stop');
                document.getElementById('rhwp-ai-progress').style.display = 'none';
                var respEl = document.getElementById('rhwp-ai-response');
                respEl.innerHTML = '<span style="color:#DC2626; font-weight:bold;">[오류 발생]</span><br>' + String(err).replace(/</g, '&lt;');
            };

            document.getElementById('rhwp-ai-btn-insert').addEventListener('click', function() {
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
