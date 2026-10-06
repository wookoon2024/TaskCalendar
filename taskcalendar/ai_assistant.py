from __future__ import annotations

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

import json
import logging
import os
import ssl
from pathlib import Path
from typing import Any, Callable

import requests
from requests.adapters import HTTPAdapter

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

from PySide6.QtCore import QPoint, Qt, QThread, Signal
from PySide6.QtGui import QFont, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from taskcalendar.paths import runtime_root

logger = logging.getLogger(__name__)

DEFAULT_AI_ENDPOINT = "https://api.commandcode.ai/provider/v1/chat/completions"
DEFAULT_AI_KEY = ""
DEFAULT_AI_MODEL = "gpt-5.6-sol"


def extract_svg_from_text(text: str) -> str | None:
    """텍스트/AI 응답 중에서 순수 SVG 마크업 코드를 정규식으로 추출"""
    if not text:
        return None
    import re

    # 1. 텍스트 내에서 <svg ... </svg> 완전문 추출 (혹은 <?xml ...?> 뒤에 오는 경우 포함)
    m_tag = re.search(r"(<svg[\s\S]*?</svg>)", text, re.IGNORECASE)
    if m_tag:
        svg_candidate = m_tag.group(1).strip()
        return svg_candidate

    # 2. 코드 블록(```xml, ```svg 등) 또는 본문 내에서 <svg 태그로 시작하는 영역 추출
    # AI가 출력 도중 중단되었거나 </svg> 닫는 태그가 누락된 경우도 복원
    m_code = re.search(r"<svg[\s\S]*?(?:</svg>|(?=```)|$)", text, re.IGNORECASE)
    if m_code:
        svg_candidate = m_code.group(0).strip()
        # 끝에 불완전하게 잘린 미완성 태그(예: <path d="... ) 제거
        if not re.search(r"</svg>\s*$", svg_candidate, re.IGNORECASE):
            svg_candidate = re.sub(r"<[^>]*$", "", svg_candidate).strip()
            svg_candidate += "\n</svg>"
        return svg_candidate

    return None



def render_svg_to_png(svg_str: str, max_width: int = 500, max_height: int = 500) -> str | None:
    """순수 SVG 문자열을 PySide6 QSvgRenderer로 고화질 투명 배경 PNG 파일로 변환하여 임시 경로 반환"""
    if not svg_str or "<svg" not in svg_str.lower():
        return None
    try:
        import tempfile
        import re
        from PySide6.QtCore import QByteArray, QSize
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer

        # xmlns 속성이 누락된 경우 자동 보정
        if "xmlns=" not in svg_str:
            svg_str = re.sub(r"(<svg\b[^>]*)>", r'\1 xmlns="http://www.w3.org/2000/svg">', svg_str, count=1, flags=re.IGNORECASE)

        # SVG 유효성 검사 및 렌더러 준비
        data_bytes = svg_str.encode("utf-8")
        renderer = QSvgRenderer(QByteArray(data_bytes))
        if not renderer.isValid():
            return None

        def_sz = renderer.defaultSize()
        w = def_sz.width() if def_sz.width() > 0 else 300
        h = def_sz.height() if def_sz.height() > 0 else 300

        # 종횡비 유지하면서 max 크기 내로 스케일링
        scale = min(max_width / max(w, 1), max_height / max(h, 1), 2.0)
        out_w = max(32, int(w * scale))
        out_h = max(32, int(h * scale))

        img = QImage(out_w, out_h, QImage.Format_ARGB32_Premultiplied)
        img.fill(0)  # 투명 배경

        painter = QPainter(img)
        renderer.render(painter)
        painter.end()

        temp_dir = Path(tempfile.gettempdir())
        out_path = temp_dir / f"ai_gen_{int(os.getpid())}_{int(id(svg_str))}.png"
        if img.save(str(out_path), "PNG"):
            return str(out_path)
    except Exception as e:
        logger.warning("Failed to render SVG to PNG: %s", e)
    return None



class TruststoreAdapter(HTTPAdapter):
    """Adapter that injects Windows OS certificate store into urllib3 poolmanager."""

    def __init__(self, ssl_context: Any = None, *args: Any, **kwargs: Any) -> None:
        self.ssl_context = ssl_context
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> Any:
        if self.ssl_context:
            kwargs["ssl_context"] = self.ssl_context
        return super().init_poolmanager(*args, **kwargs)


class AIConfigManager:
    """AI API 설정 관리자 (암호화 DB 영구 저장 및 DPAPI 보안 관리)"""

    _config_cache: dict[str, Any] | None = None

    @classmethod
    def _get_repository(cls) -> Any:
        try:
            from taskcalendar.storage import EncryptedRepository
            repo = EncryptedRepository.get_global_instance()
            if repo is not None:
                return repo
            from taskcalendar.paths import runtime_root
            db_path = runtime_root() / "db" / "taskcalendar.db.enc"
            if db_path.exists():
                return EncryptedRepository(db_path)
        except Exception as e:
            logger.warning("Failed to access EncryptedRepository for AI config: %s", e)
        return None

    @classmethod
    def load(cls, force_reload: bool = False) -> dict[str, Any]:
        if not force_reload and cls._config_cache is not None:
            return dict(cls._config_cache)

        default_cfg: dict[str, Any] = {
            "provider": "commandcode",
            "endpoint": DEFAULT_AI_ENDPOINT,
            "api_key": DEFAULT_AI_KEY,
            "model": DEFAULT_AI_MODEL,
            "ssl_verify": True,
            "timeout_sec": 35,
            "stream": True,
        }

        # 암호화 DB(settings 테이블)에서 로드
        repo = cls._get_repository()
        if repo is not None:
            try:
                db_cfg = repo.get_ai_config()
                for k, v in db_cfg.items():
                    if v is not None and v != "":
                        default_cfg[k] = v
                    elif k in ("ssl_verify", "stream"):
                        default_cfg[k] = v
            except Exception as e:
                logger.warning("Failed to load AI config from encrypted DB: %s", e)

        cls._config_cache = default_cfg
        return dict(cls._config_cache)

    @classmethod
    def save(cls, new_cfg: dict[str, Any]) -> None:
        cfg = cls.load(force_reload=True)
        cfg.update(new_cfg)
        cls._config_cache = cfg

        # 암호화 DB(settings 테이블)에 Windows DPAPI 암호화하여 저장
        repo = cls._get_repository()
        if repo is not None:
            try:
                repo.save_ai_config(cfg)
            except Exception as e:
                logger.error("Failed to save AI config to encrypted DB: %s", e)


class AIChatWorker(QThread):
    """비동기 AI 호출 백그라운드 워커 (실시간 토큰 스트리밍 지원)"""

    chunk_received = Signal(str)
    finished = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        messages: list[dict[str, str]],
        config: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.messages = messages
        self.config = config or AIConfigManager.load(force_reload=True)
        self._is_stopped = False

    def stop(self) -> None:
        self._is_stopped = True

    def run(self) -> None:
        endpoint = str(self.config.get("endpoint", DEFAULT_AI_ENDPOINT)).strip()
        api_key = str(self.config.get("api_key", DEFAULT_AI_KEY)).strip()
        model = str(self.config.get("model", DEFAULT_AI_MODEL)).strip()
        ssl_verify = bool(self.config.get("ssl_verify", True))
        timeout = int(self.config.get("timeout_sec", 35))
        stream_enabled = bool(self.config.get("stream", True))

        if not (endpoint.startswith("http://") or endpoint.startswith("https://")):
            self.error.emit("올바른 HTTP/HTTPS API 엔드포인트 URL을 입력해 주세요.")
            return

        if not api_key:
            self.error.emit("AI API 키가 설정되어 있지 않습니다.\n[AI 설정] 메뉴에서 API 키를 입력하고 저장해 주세요.")
            return

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

        payload = {
            "model": model,
            "messages": self.messages,
            "stream": stream_enabled,
            "temperature": 0.4,
            "max_tokens": 4096,
        }

        session = requests.Session()
        if ssl_verify:
            try:
                import truststore
                ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                session.mount("https://", TruststoreAdapter(ssl_context=ctx))
            except Exception:
                pass
        else:
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass

        accumulated_text = ""
        try:
            with session.post(
                endpoint,
                json=payload,
                headers=headers,
                stream=stream_enabled,
                timeout=timeout,
                verify=ssl_verify,
            ) as resp:
                resp.raise_for_status()
                resp.encoding = "utf-8"
                if stream_enabled:
                    for raw_line in resp.iter_lines(decode_unicode=False):
                        if self._is_stopped:
                            break
                        if not raw_line:
                            continue
                        line_str = raw_line.decode("utf-8", errors="replace").strip()
                        if line_str.startswith("data: "):
                            data_str = line_str[6:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk_json = json.loads(data_str)
                                choices = chunk_json.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    content = delta.get("content", "")
                                    if content:
                                        accumulated_text += content
                                        self.chunk_received.emit(content)
                            except Exception:
                                continue
                    self.finished.emit(accumulated_text)
                else:
                    try:
                        res_data = resp.json()
                        choices = res_data.get("choices", [])
                        if choices:
                            content = choices[0].get("message", {}).get("content", "")
                            self.finished.emit(content)
                        else:
                            self.finished.emit(resp.content.decode("utf-8", errors="replace"))
                    except Exception:
                        self.finished.emit(resp.content.decode("utf-8", errors="replace"))
        except requests.exceptions.HTTPError as e:
            err_body = ""
            if e.response is not None:
                try:
                    err_body = e.response.content.decode("utf-8", errors="replace")[:300]
                except Exception:
                    err_body = e.response.text[:300]
            self.error.emit(f"AI 서버 응답 오류 (HTTP {e.response.status_code if e.response else 'N/A'}):\n{err_body}")
        except requests.exceptions.SSLError as e:
            self.error.emit(f"SSL 보안 인증서 검증 실패:\n{e}\n\n(기관 전용 사설 인증서 또는 행정망 환경인 경우 AI 설정에서 '표준 SSL 보안 인증서 검증 활성화' 체크를 해제해 주세요.)")
        except requests.exceptions.Timeout:
            self.error.emit(f"요청 시간 초과 ({timeout}초 경과). 서버 상태를 확인해 주세요.")
        except requests.exceptions.RequestException as e:
            self.error.emit(f"네트워크 통신 오류: {e}")
        except Exception as e:
            self.error.emit(f"AI 통신 오류 발생: {e}")
        finally:
            try:
                session.close()
            except Exception:
                pass


class AIConnectionTestWorker(QThread):
    """AI 연결 테스트 워커 — 입력값(저장 전)으로 실제 API 호출 후 결과 반환"""

    finished_ok = Signal(str, str)   # (응답 텍스트, 사용 모델)
    failed = Signal(str)             # (오류 메시지)

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        model: str,
        ssl_verify: bool = True,
        timeout_sec: int = 35,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model
        self.ssl_verify = ssl_verify
        self.timeout_sec = timeout_sec

    def run(self) -> None:
        endpoint = self.endpoint.strip()
        if not (endpoint.startswith("http://") or endpoint.startswith("https://")):
            self.failed.emit("올바른 HTTP/HTTPS API 엔드포인트 URL을 입력해 주세요.")
            return

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key.strip()}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        payload = {
            "model": self.model.strip(),
            "messages": [{"role": "user", "content": "연결 확인"}],
            "stream": False,
            "temperature": 0.1,
        }

        session = requests.Session()
        try:
            if self.ssl_verify:
                try:
                    import truststore
                    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    session.mount("https://", TruststoreAdapter(ssl_context=ctx))
                except Exception:
                    pass
            else:
                try:
                    import urllib3
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                except Exception:
                    pass

            with session.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=self.timeout_sec,
                verify=self.ssl_verify,
            ) as resp:
                if resp.status_code != 200:
                    detail = ""
                    try:
                        detail = resp.text[:300]
                    except Exception:
                        detail = ""
                    msg = f"서버 오류 (HTTP {resp.status_code})"
                    if detail:
                        msg += f"\n\n{detail}"
                    if resp.status_code in (401, 403):
                        msg += "\n\nAPI Key가 유효하지 않거나 권한이 없습니다."
                    self.failed.emit(msg)
                    return

                try:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        content = choices[0].get("message", {}).get("content", "")
                        if isinstance(content, str) and content.strip():
                            self.finished_ok.emit(content.strip(), self.model.strip())
                            return
                    # content가 비어 있으면 원문으로 확인
                    raw = resp.text[:200]
                    self.finished_ok.emit(f"(응답 수신, 본문: {raw})", self.model.strip())
                except Exception:
                    self.finished_ok.emit("(응답 수신, JSON 파싱 불가)", self.model.strip())

        except requests.exceptions.SSLError as e:
            self.failed.emit(
                f"SSL 보안 인증서 검증 실패:\n{e}\n\n"
                "(기관 전용 사설 인증서 또는 행정망 환경인 경우 "
                "'표준 SSL 보안 인증서 검증 활성화' 체크를 해제해 주세요.)"
            )
        except requests.exceptions.Timeout:
            self.failed.emit(f"요청 시간 초과 ({self.timeout_sec}초 경과). 서버 상태를 확인해 주세요.")
        except requests.exceptions.HTTPError as e:
            self.failed.emit(f"HTTP 오류 발생: {e}")
        except requests.exceptions.RequestException as e:
            self.failed.emit(f"네트워크 통신 오류:\n{e}\n\n(엔드포인트 URL과 네트워크 연결을 확인해 주세요.)")
        except Exception as e:
            self.failed.emit(f"연결 테스트 중 오류 발생: {type(e).__name__}: {e}")
        finally:
            try:
                session.close()
            except Exception:
                pass


class AISettingsDialog(QDialog):
    """AI API 연동 설정 대화상자 (인터넷망/공공기관 행정망 프리셋 지원)"""

    def __init__(self, parent: QWidget | None = None, palette: dict[str, str] | None = None) -> None:
        super().__init__(parent)
        self.palette = palette or {}
        self.setWindowTitle("AI 설정")
        self.resize(500, 340)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self.cfg = AIConfigManager.load(force_reload=True)
        self._test_worker: AIConnectionTestWorker | None = None
        self.init_ui()

    def init_ui(self) -> None:
        bg = self.palette.get("bg", "#F8FAFC")
        panel = self.palette.get("panel", "#FFFFFF")
        text = self.palette.get("text", "#1E293B")
        muted = self.palette.get("muted", "#64748B")
        line = self.palette.get("line", "#CBD5E1")
        accent = self.palette.get("accent", "#2563EB")
        button_text = self.palette.get("button_text", "#FFFFFF")
        self._muted = muted

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
                color: {text};
                font-family: 'Pretendard', 'Malgun Gothic', 'Segoe UI', sans-serif;
            }}
            QLabel {{
                color: {text};
                font-size: 12px;
            }}
            QGroupBox {{
                font-weight: bold;
                border: 1px solid {line};
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 12px;
                background-color: {panel};
                color: {text};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }}
            QLineEdit, QComboBox {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                border-radius: 4px;
                padding: 5px 8px;
                font-size: 12px;
            }}
            QLineEdit:focus, QComboBox:focus {{
                border-color: {accent};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        group = QGroupBox("AI 모델 및 API 엔드포인트 설정", self)
        form = QFormLayout(group)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.combo_preset = QComboBox(self)
        self.combo_preset.addItem("인터넷망 기본 (Command Code AI - GPT-5.6 / Claude)", "commandcode")
        self.combo_preset.addItem("공공기관 행정망 (CLOVA Studio GOV)", "clova_gov")
        self.combo_preset.addItem("공공기관 범정부 AI (dev.ai.go.kr)", "gov")
        self.combo_preset.addItem("사내 자체 구축 LLM / Ollama (직접 입력)", "custom")
        form.addRow("망 환경 프리셋:", self.combo_preset)

        self.edit_endpoint = QLineEdit(self)
        self.edit_endpoint.setText(self.cfg.get("endpoint", DEFAULT_AI_ENDPOINT))
        form.addRow("API 엔드포인트:", self.edit_endpoint)

        key_layout = QHBoxLayout()
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(6)
        self.edit_key = QLineEdit(self)
        self.edit_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_key.setText(self.cfg.get("api_key", DEFAULT_AI_KEY))
        self.btn_toggle_key = QPushButton("보기", self)
        self.btn_toggle_key.setFixedWidth(52)
        self.btn_toggle_key.setStyleSheet(f"background-color: {panel}; color: {text}; border: 1px solid {line}; padding: 4px 8px; border-radius: 4px; font-size: 11px;")
        self.btn_toggle_key.clicked.connect(self._toggle_key_visibility)
        key_layout.addWidget(self.edit_key, 1)
        key_layout.addWidget(self.btn_toggle_key)
        form.addRow("API Key:", key_layout)

        self.edit_model = QLineEdit(self)
        self.edit_model.setText(self.cfg.get("model", DEFAULT_AI_MODEL))
        form.addRow("모델명 (Model):", self.edit_model)

        self.chk_ssl = QCheckBox("표준 SSL 보안 인증서 검증 활성화 (해제 시 기관 사설인증서 허용)", self)
        self.chk_ssl.setChecked(self.cfg.get("ssl_verify", True))
        form.addRow("보안 검증:", self.chk_ssl)

        layout.addWidget(group)

        lbl_hint = QLabel(
            "업무망/행정망 환경에서 SSL 보안 오류가 발생할 경우 '보안 검증' 체크를 해제하시면 기관 내부망에서도 정상 연결됩니다.",
            self,
        )
        lbl_hint.setWordWrap(True)
        lbl_hint.setStyleSheet(f"color: {muted}; font-size: 11px;")
        layout.addWidget(lbl_hint)

        # ---- 연결 테스트 영역 ----
        test_group = QGroupBox("연결 테스트", self)
        test_layout = QVBoxLayout(test_group)
        test_layout.setSpacing(8)

        test_btn_row = QHBoxLayout()
        test_btn_row.setSpacing(8)
        self.btn_test = QPushButton("연결 테스트", self)
        self.btn_test.setStyleSheet(
            f"background-color: {panel}; color: {text}; border: 1px solid {line}; "
            "padding: 6px 14px; border-radius: 4px; font-size: 12px;"
        )
        self.btn_test.clicked.connect(self._run_connection_test)
        test_btn_row.addWidget(self.btn_test)
        test_btn_row.addStretch(1)
        test_layout.addLayout(test_btn_row)

        self.lbl_test_result = QLabel("아직 테스트하지 않았습니다.", self)
        self.lbl_test_result.setWordWrap(True)
        self.lbl_test_result.setStyleSheet(f"color: {muted}; font-size: 11px;")
        test_layout.addWidget(self.lbl_test_result)

        layout.addWidget(test_group)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)

        btn_save = QPushButton("설정 저장", self)
        btn_save.setStyleSheet(f"background-color: {accent}; color: {button_text}; font-weight: bold; padding: 6px 16px; border-radius: 4px; border: none;")
        btn_save.clicked.connect(self._save_settings)

        btn_cancel = QPushButton("닫기", self)
        btn_cancel.setStyleSheet(f"background-color: {panel}; color: {text}; border: 1px solid {line}; padding: 6px 14px; border-radius: 4px;")
        btn_cancel.clicked.connect(self.reject)

        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

        # Set preset index without triggering _on_preset_changed
        curr_provider = self.cfg.get("provider", "commandcode")
        idx = self.combo_preset.findData(curr_provider)
        if idx >= 0:
            self.combo_preset.setCurrentIndex(idx)
        # Connect only AFTER initial population
        self.combo_preset.currentIndexChanged.connect(self._on_preset_changed)

    def _toggle_key_visibility(self) -> None:
        if self.edit_key.echoMode() == QLineEdit.EchoMode.Password:
            self.edit_key.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_toggle_key.setText("숨김")
        else:
            self.edit_key.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_toggle_key.setText("보기")

    def _on_preset_changed(self, index: int) -> None:
        data = self.combo_preset.currentData()
        if data == "commandcode":
            self.edit_endpoint.setText("https://api.commandcode.ai/provider/v1/chat/completions")
            self.edit_model.setText("gpt-5.6-sol")
            self.edit_key.setPlaceholderText("API Key를 입력하세요")
        elif data == "clova_gov":
            self.edit_endpoint.setText("https://api.clovastudio.go.kr/api/v1/chat/completions")
            self.edit_model.setText("HCX-GOV-THINK-V1-32B")
            self.edit_key.setPlaceholderText("기관에서 발급받은 CLOVA GOV API 키를 입력하세요")
        elif data == "gov":
            self.edit_endpoint.setText("https://dev.ai.go.kr/api/v1/chat/completions")
            self.edit_model.setText("HCX-003")
            self.edit_key.setPlaceholderText("범정부 AI API 키를 입력하세요")

    def _run_connection_test(self) -> None:
        """입력된 현재 설정값으로 실제 API를 호출해 연결 여부 확인"""
        if self._test_worker is not None and self._test_worker.isRunning():
            return

        endpoint = self.edit_endpoint.text().strip()
        api_key = self.edit_key.text().strip()
        model = self.edit_model.text().strip()
        ssl_verify = self.chk_ssl.isChecked()

        if not endpoint:
            self.lbl_test_result.setText("✗ API 엔드포인트를 입력해 주세요.")
            return
        if not api_key:
            self.lbl_test_result.setText("✗ API Key를 입력해 주세요.")
            return
        if not model:
            self.lbl_test_result.setText("✗ 모델명을 입력해 주세요.")
            return

        self.btn_test.setEnabled(False)
        self.btn_test.setText("테스트 중...")
        self.lbl_test_result.setText("⏳ 연결 여부를 확인하고 있습니다... (최대 35초)")
        self.lbl_test_result.setStyleSheet(f"color: {self._muted}; font-size: 11px;")

        self._test_worker = AIConnectionTestWorker(
            endpoint=endpoint,
            api_key=api_key,
            model=model,
            ssl_verify=ssl_verify,
            timeout_sec=int(self.cfg.get("timeout_sec", 35) or 35),
            parent=self,
        )
        self._test_worker.finished_ok.connect(self._on_test_success)
        self._test_worker.failed.connect(self._on_test_failed)
        self._test_worker.start()

    def _on_test_success(self, text: str, model: str) -> None:
        self.btn_test.setEnabled(True)
        self.btn_test.setText("연결 테스트")
        preview = text[:200] + ("..." if len(text) > 200 else "")
        self.lbl_test_result.setText(f"✓ 연결 성공 (모델: {model})\n응답: {preview}")
        self.lbl_test_result.setStyleSheet("color: #16A34A; font-size: 11px;")

    def _on_test_failed(self, err: str) -> None:
        self.btn_test.setEnabled(True)
        self.btn_test.setText("연결 테스트")
        self.lbl_test_result.setText(f"✗ 연결 실패\n{err}")
        self.lbl_test_result.setStyleSheet("color: #DC2626; font-size: 11px;")

    def _save_settings(self) -> None:
        new_data = {
            "provider": self.combo_preset.currentData(),
            "endpoint": self.edit_endpoint.text().strip(),
            "api_key": self.edit_key.text().strip(),
            "model": self.edit_model.text().strip(),
            "ssl_verify": self.chk_ssl.isChecked(),
        }
        AIConfigManager.save(new_data)
        self.cfg = AIConfigManager.load(force_reload=True)
        QMessageBox.information(self, "설정 완료", "AI 연동 설정이 성공적으로 저장되었습니다.")
        self.accept()


class AIChatDialog(QDialog):
    """
    [우클릭 ➔ AI 도우미 / 원클릭 프리셋] 플로팅 팝업
    - 깔끔한 스킨 디자인 연동
    - 원클릭 프리셋 (공문서체 다듬기, 3줄 요약, 법령 검토, 공공언어 순화 등)
    - 요청 진행 중 '바꾸기', '삽입' 비활성화, 완료 시 활성화
    """

    def __init__(
        self,
        parent_editor: Any = None,
        initial_prompt: str = "",
        selected_text: str = "",
        preset: str = "",
        palette: dict[str, str] | None = None,
    ) -> None:
        super().__init__(parent_editor)
        self.editor = parent_editor
        self.palette = palette or {}
        self.worker: AIChatWorker | None = None
        self.full_response_text = ""
        self.selected_text = (selected_text or "").strip()
        self.preset = preset or ""

        title = "AI 업무 도우미"
        if preset == "gongmun":
            title = "✨ 공문서 개조식 다듬기"
        elif preset == "summary":
            title = "📋 3줄 핵심 요약"
        elif preset == "law":
            title = "⚖️ 관련 법령·규정 검토"
        elif preset == "refine":
            title = "✍️ 쉬운 공공언어로 순화"

        self.setWindowTitle(title)
        self.resize(600, 600)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.init_ui(initial_prompt)

        # 프리셋이 지정되어 있고 선택 텍스트가 있으면 즉시 자동 실행
        if self.preset and (self.selected_text or initial_prompt):
            self._send_request()

    def init_ui(self, initial_prompt: str) -> None:
        bg = self.palette.get("bg", "#F8FAFC")
        panel = self.palette.get("panel", "#FFFFFF")
        text = self.palette.get("text", "#1E293B")
        muted = self.palette.get("muted", "#64748B")
        line = self.palette.get("line", "#CBD5E1")
        accent = self.palette.get("accent", "#2563EB")
        button_text = self.palette.get("button_text", "#FFFFFF")

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
                color: {text};
                font-family: 'Pretendard', 'Malgun Gothic', 'Segoe UI', sans-serif;
            }}
            QLabel {{
                color: {text};
                font-size: 12px;
            }}
            QTextEdit {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 8px;
                color: {text};
                font-size: 13px;
                line-height: 1.5;
            }}
            QTextEdit:focus {{
                border-color: {accent};
            }}
            QPushButton {{
                font-size: 12px;
                border-radius: 4px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(14, 12, 14, 12)

        # Header bar: Right-aligned AI Settings button only
        header_row = QHBoxLayout()
        header_row.addStretch(1)

        btn_settings = QPushButton("AI 설정", self)
        btn_settings.setStyleSheet(f"background-color: {panel}; border: 1px solid {line}; border-radius: 4px; padding: 4px 10px; font-size: 11px; color: {muted};")
        btn_settings.clicked.connect(self._open_settings)
        header_row.addWidget(btn_settings)
        layout.addLayout(header_row)

        # Prompt input area
        self.input_edit = QTextEdit(self)
        self.input_edit.setFixedHeight(75)
        self.input_edit.setPlaceholderText("요청할 작업이나 작성할 서식을 입력하세요 (예: 출장보고서 양식 만들어줘, 위 문장 공문서체로 수정해줘 등)")
        if initial_prompt:
            self.input_edit.setPlainText(initial_prompt)
        layout.addWidget(self.input_edit)

        # Action row (Submit & Progress bar)
        action_row = QHBoxLayout()
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(f"QProgressBar {{ background-color: {line}; border: none; }} QProgressBar::chunk {{ background-color: {accent}; }}")
        self.progress_bar.hide()
        action_row.addWidget(self.progress_bar, 1)

        self.btn_send = QPushButton("요청하기", self)
        self.btn_send.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_send.setStyleSheet(f"""
            QPushButton {{
                background-color: {accent};
                color: {button_text};
                font-weight: bold;
                padding: 6px 18px;
                border: none;
            }}
            QPushButton:hover {{
                opacity: 0.9;
            }}
            QPushButton:disabled {{
                background-color: {line};
                color: {muted};
            }}
        """)
        self.btn_send.clicked.connect(self._send_request)
        action_row.addWidget(self.btn_send)
        layout.addLayout(action_row)

        # Response viewer
        self.viewer_edit = QTextEdit(self)
        self.viewer_edit.setReadOnly(True)
        self.viewer_edit.setPlaceholderText("AI 응답 내용이 여기에 실시간으로 표시됩니다.")
        layout.addWidget(self.viewer_edit, 1)

        # Bottom action buttons
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)

        # [바꾸기] 버튼: 선택 영역이 있을 때만 표시/활성화
        if self.selected_text:
            self.btn_replace = QPushButton("🔄 선택 영역 바꾸기", self)
            self.btn_replace.setEnabled(False)
            self.btn_replace.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_replace.setStyleSheet(f"""
                QPushButton {{
                    background-color: {accent};
                    color: {button_text};
                    font-weight: bold;
                    padding: 7px 14px;
                    border: none;
                }}
                QPushButton:disabled {{
                    background-color: {line};
                    color: {muted};
                }}
            """)
            self.btn_replace.clicked.connect(self._replace_in_editor)
            bottom_row.addWidget(self.btn_replace)

        # 커서 위치에 삽입 버튼 (초기 비활성화, 완료 시 활성화)
        self.btn_insert = QPushButton("➕ 본문에 삽입" if self.selected_text else "커서 위치에 삽입", self)
        self.btn_insert.setEnabled(False)
        self.btn_insert.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_insert.setStyleSheet(f"""
            QPushButton {{
                background-color: {panel if self.selected_text else accent};
                color: {text if self.selected_text else button_text};
                border: 1px solid {line if self.selected_text else accent};
                font-weight: bold;
                padding: 7px 14px;
            }}
            QPushButton:disabled {{
                background-color: {line};
                color: {muted};
                border: none;
            }}
        """)
        self.btn_insert.clicked.connect(self._insert_to_editor)
        bottom_row.addWidget(self.btn_insert)

        self.btn_copy = QPushButton("복사하기", self)
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.setStyleSheet(f"""
            QPushButton {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                padding: 7px 12px;
            }}
            QPushButton:hover {{
                background-color: {bg};
            }}
        """)
        self.btn_copy.clicked.connect(self._copy_response)
        bottom_row.addWidget(self.btn_copy)

        bottom_row.addStretch(1)

        btn_close = QPushButton("닫기", self)
        btn_close.clicked.connect(self.close)
        btn_close.setStyleSheet(f"background-color: {panel}; border: 1px solid {line}; padding: 6px 12px; color: {muted};")
        bottom_row.addWidget(btn_close)

        layout.addLayout(bottom_row)

    def _open_settings(self) -> None:
        dlg = AISettingsDialog(self, palette=self.palette)
        dlg.exec()

    def _send_request(self) -> None:
        prompt = self.input_edit.toPlainText().strip()
        if not prompt and not self.selected_text:
            QMessageBox.warning(self, "입력 필요", "요청하실 내용을 입력해 주세요.")
            return

        self.viewer_edit.clear()
        self.full_response_text = ""
        self.btn_send.setEnabled(False)
        if hasattr(self, "btn_replace"):
            self.btn_replace.setEnabled(False)
        self.btn_insert.setEnabled(False)
        self.progress_bar.show()

        # 프리셋별 특화 시스템 프롬프트 및 사용자 내용 구성
        if self.preset == "gongmun":
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
            )
            target_content = self.selected_text or prompt
            user_content = f"[다듬을 원문 내용]:\n{target_content}"
        elif self.preset == "summary":
            system_prompt = (
                "당신은 행정 보고서 및 결재 문서 핵심 요약 전문가입니다.\n"
                "사용자가 제공한 내용을 상급자/기관장 보고에 즉시 활용할 수 있도록 '핵심 3줄 요약'으로 정리해 주세요.\n"
                "- 1줄: 추진 배경 및 목적\n"
                "- 2줄: 주요 핵심 내용 및 현황\n"
                "- 3줄: 향후 계획 및 기대 효과\n"
                "- 각 줄은 '○ ' 불릿과 함께 명확하고 간결한 개조식 문장으로 작성하세요.\n"
                "- 다른 설명이나 인사말 없이 3줄 요약 결과만 바로 출력하세요."
            )
            target_content = self.selected_text or prompt
            user_content = f"[요약할 본문 내용]:\n{target_content}"
        elif self.preset == "law":
            system_prompt = (
                "당신은 대한민국 행정 법률 및 감사 실무 전문 자문관입니다.\n"
                "사용자가 작성한 업무/기안문 내용을 바탕으로 다음 사항을 검토하여 정리해 주세요:\n"
                "1. [관련 법령 및 조례]: 직접적 근거가 되는 법률·시행령·자치법규\n"
                "2. [필수 사전 절차]: 결재·시행 전 필수 심의/협의/사전예고 등\n"
                "3. [실무 유의사항]: 감사 지적 예방 체크포인트\n"
                "불필요한 인사말 없이 개조식으로 명료하게 제공하세요."
            )
            target_content = self.selected_text or prompt
            user_content = f"[검토할 업무 내용]:\n{target_content}"
        elif self.preset == "refine":
            system_prompt = (
                "당신은 문화체육관광부 및 국립국어원 표준 행정용어 순화 전문가입니다.\n"
                "사용자가 제공한 문장에서 어려운 한자어, 무분별한 외래어, 일본식 행정용어, 권위적 표현을 찾아 국민이 이해하기 쉬운 '바른 공공언어'로 순화하여 다시 작성해 주세요.\n"
                "- 순화된 최종 완성 문장을 최상단에 제공하세요.\n"
                "- 하단에 [주요 순화 내역]을 간단한 표나 목록으로 첨부하세요 (예: 바우처 → 이용권, 익일 → 다음 날).\n"
                "- 부가적인 인사말은 생략하세요."
            )
            target_content = self.selected_text or prompt
            user_content = f"[순화할 원문 내용]:\n{target_content}"
        else:
            system_prompt = (
                "당신은 대한민국 공공기관 및 지방자치단체 행정 문서 작성 전문 AI 비서입니다.\n"
                "사용자의 요청에 따라 완성도 높은 공문서, 보고서 서식, 개조식 정리(□, ○, -, *), 기안문 등을 격식 있게 작성해 주세요.\n"
                "[이미지/일러스트/아이콘/도장 생성 요청 시 절대 규칙]:\n"
                "사용자가 이미지, 일러스트, 아이콘, 마크, 도장, 사과, 캐릭터 등의 그림 생성을 요청하는 경우, "
                "반드시 단독으로 즉시 렌더링 가능한 완전한 SVG XML 코드를 작성하여 ```xml 코드 블록 안에 담아 출력하십시오.\n"
                "- SVG는 viewBox, xmlns를 포함하고 화려하고 선명하며 완성도 높은 벡터 그래픽으로 디자인하세요.\n"
                "- 불필요한 설명이나 잡담은 일절 배제하고 곧바로 결과물(SVG 코드 블록 또는 서식 본문)만 출력하세요.\n"
                "불필요한 인사말은 생략하고 곧바로 본문 서식 또는 요청된 작성 결과물을 명확하게 제공하세요."
            )
            user_content = prompt or self.selected_text

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        self.generated_image_path = None
        self.worker = AIChatWorker(messages=messages, parent=self)
        self.worker.chunk_received.connect(self._on_chunk)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_chunk(self, chunk: str) -> None:
        self.full_response_text += chunk
        self.viewer_edit.setPlainText(self.full_response_text)
        cursor = self.viewer_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.viewer_edit.setTextCursor(cursor)

    def _on_finished(self, text: str) -> None:
        self.full_response_text = text
        self.btn_send.setEnabled(True)
        has_text = bool(text.strip())
        self.progress_bar.hide()

        # SVG 이미지 생성 여부 확인 및 렌더링
        svg_code = extract_svg_from_text(text)
        if svg_code:
            png_path = render_svg_to_png(svg_code)
            if png_path and os.path.exists(png_path):
                self.generated_image_path = png_path
                self.viewer_edit.setHtml(
                    f'<div style="text-align:center; padding:15px;">'
                    f'<h3 style="color:#2563EB; margin-bottom:10px;">🎨 AI 이미지/일러스트 생성 완료</h3>'
                    f'<img src="{png_path}" style="max-width:260px; max-height:220px; border-radius:6px; box-shadow:0 2px 8px rgba(0,0,0,0.1);" /><br>'
                    f'<p style="color:#64748B; font-size:11px; margin-top:10px;">하단 <b>[커서 위치에 이미지 삽입]</b> 버튼을 누르면 에디터에 삽입됩니다.</p>'
                    f'</div>'
                )
                self.btn_insert.setText("🖼️ 커서 위치에 이미지 삽입")
                self.btn_insert.setEnabled(True)
                if hasattr(self, "btn_replace"):
                    self.btn_replace.setEnabled(False)
                return

        self.viewer_edit.setPlainText(text)
        self.btn_insert.setText("➕ 본문에 삽입" if self.selected_text else "커서 위치에 삽입")
        if hasattr(self, "btn_replace"):
            self.btn_replace.setEnabled(has_text)
        self.btn_insert.setEnabled(has_text)

    def _on_error(self, err: str) -> None:
        self.btn_send.setEnabled(True)
        if hasattr(self, "btn_replace"):
            self.btn_replace.setEnabled(False)
        self.btn_insert.setEnabled(False)
        self.progress_bar.hide()
        QMessageBox.critical(self, "AI 오류", err)

    def _replace_in_editor(self) -> None:
        text = self.full_response_text.strip()
        if not text:
            return

        if self.editor and hasattr(self.editor, "replace_selection_with_text"):
            self.editor.replace_selection_with_text(text)
            self.close()
        elif self.editor and hasattr(self.editor, "insert_text_at_cursor"):
            self.editor.insert_text_at_cursor(text)
            self.close()
        else:
            QGuiApplication.clipboard().setText(text)
            QMessageBox.information(self, "안내", "클립보드에 복사되었습니다.")

    def _insert_to_editor(self) -> None:
        if getattr(self, "generated_image_path", None) and os.path.exists(self.generated_image_path):
            if self.editor and hasattr(self.editor, "insert_image_file"):
                self.editor.insert_image_file(self.generated_image_path)
                self.close()
                return

        text = self.full_response_text.strip()
        if not text:
            return

        if self.editor and hasattr(self.editor, "insert_text_at_cursor"):
            self.editor.insert_text_at_cursor(text)
            self.close()
        else:
            QGuiApplication.clipboard().setText(text)
            QMessageBox.information(self, "안내", "클립보드에 복사되었습니다.")

    def _copy_response(self) -> None:
        text = self.full_response_text.strip()
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        QMessageBox.information(self, "복사 완료", "클립보드에 복사되었습니다.")


class AIAnalyzeDialog(QDialog):
    """
    [우클릭 ➔ 분석하기] 플로팅 팝업
    - 에디터 본문 내용(업무)을 스캔하여 관련 법령, 시행령, 훈령·지침, 필수 행정 절차 안내
    """

    def __init__(self, parent_editor: Any = None, doc_text: str = "", palette: dict[str, str] | None = None) -> None:
        super().__init__(parent_editor)
        self.editor = parent_editor
        self.palette = palette or {}
        self.doc_text = doc_text.strip()
        self.worker: AIChatWorker | None = None
        self.analysis_result = ""

        self.setWindowTitle("AI 업무 분석기")
        self.resize(620, 640)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.init_ui()

        if self.doc_text:
            self._start_analysis()
        else:
            self.viewer.setPlainText("분석할 업무 문서 내용이 비어 있습니다.\n에디터에 업무 내용이나 기안문을 작성한 후 다시 실행해 주세요.")

    def init_ui(self) -> None:
        bg = self.palette.get("bg", "#F8FAFC")
        panel = self.palette.get("panel", "#FFFFFF")
        text = self.palette.get("text", "#1E293B")
        muted = self.palette.get("muted", "#64748B")
        line = self.palette.get("line", "#CBD5E1")
        accent = self.palette.get("accent", "#0F766E")
        button_text = self.palette.get("button_text", "#FFFFFF")

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
                color: {text};
                font-family: 'Pretendard', 'Malgun Gothic', 'Segoe UI', sans-serif;
            }}
            QLabel {{
                color: {text};
                font-size: 12px;
            }}
            QTextEdit {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 10px;
                color: {text};
                font-size: 13px;
                line-height: 1.6;
            }}
            QPushButton {{
                font-size: 12px;
                border-radius: 4px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(14, 12, 14, 12)

        header_row = QHBoxLayout()
        title_lbl = QLabel("업무 관련 법령 및 필수 행정절차 분석")
        title_lbl.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {text};")
        header_row.addWidget(title_lbl)
        header_row.addStretch(1)

        btn_settings = QPushButton("AI 설정", self)
        btn_settings.setStyleSheet(f"background-color: {panel}; border: 1px solid {line}; border-radius: 4px; padding: 4px 10px; font-size: 11px; color: {muted};")
        btn_settings.clicked.connect(self._open_settings)
        header_row.addWidget(btn_settings)
        layout.addLayout(header_row)

        desc_lbl = QLabel("작성 중인 업무 내용을 분석하여 근거 법령, 선결 행정 절차, 감사 지적 예방 체크리스트를 도출합니다.")
        desc_lbl.setStyleSheet(f"font-size: 11px; color: {muted};")
        layout.addWidget(desc_lbl)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(f"QProgressBar {{ background-color: {line}; border: none; }} QProgressBar::chunk {{ background-color: {accent}; }}")
        layout.addWidget(self.progress_bar)

        self.viewer = QTextEdit(self)
        self.viewer.setReadOnly(True)
        layout.addWidget(self.viewer, 1)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)

        self.btn_insert = QPushButton("분석 결과 삽입", self)
        self.btn_insert.setEnabled(False)
        self.btn_insert.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_insert.setStyleSheet(f"""
            QPushButton {{
                background-color: {accent};
                color: {button_text};
                font-weight: bold;
                padding: 7px 14px;
                border: none;
            }}
            QPushButton:disabled {{
                background-color: {line};
                color: {muted};
            }}
        """)
        self.btn_insert.clicked.connect(self._insert_to_editor)
        bottom_row.addWidget(self.btn_insert)

        self.btn_copy = QPushButton("복사하기", self)
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.setStyleSheet(f"""
            QPushButton {{
                background-color: {panel};
                color: {text};
                border: 1px solid {line};
                padding: 7px 12px;
            }}
            QPushButton:hover {{
                background-color: {bg};
            }}
        """)
        self.btn_copy.clicked.connect(self._copy_result)
        bottom_row.addWidget(self.btn_copy)

        self.btn_reanalyze = QPushButton("다시 분석", self)
        self.btn_reanalyze.setStyleSheet(f"background-color: {panel}; border: 1px solid {line}; padding: 7px 12px; color: {text};")
        self.btn_reanalyze.clicked.connect(self._start_analysis)
        bottom_row.addWidget(self.btn_reanalyze)

        bottom_row.addStretch(1)

        btn_close = QPushButton("닫기", self)
        btn_close.clicked.connect(self.close)
        btn_close.setStyleSheet(f"background-color: {panel}; border: 1px solid {line}; padding: 6px 12px; color: {muted};")
        bottom_row.addWidget(btn_close)

        layout.addLayout(bottom_row)

    def _open_settings(self) -> None:
        dlg = AISettingsDialog(self, palette=self.palette)
        dlg.exec()

    def _start_analysis(self) -> None:
        if not self.doc_text:
            return

        self.viewer.clear()
        self.analysis_result = ""
        self.progress_bar.show()
        self.btn_reanalyze.setEnabled(False)
        self.btn_insert.setEnabled(False)

        system_prompt = (
            "당신은 대한민국 행정안전부 및 지방자치단체 감사실 출신의 최고 행정 법률 자문관입니다.\n"
            "제공되는 업무 문서(기안문, 계획서, 메모 등)의 내용을 면밀히 분석하여 아래 3개 항목으로 일목요연하게 정리해 주세요:\n\n"
            "1. [관련 근거 법령 및 행정규칙]\n"
            "  - 해당 업무를 수행하는 직접적 근거가 되는 법률, 시행령, 시행규칙 조항\n"
            "  - 관련 행정안전부/소관 부처 훈령, 예규, 고시 및 자치법규(조례, 규칙)\n\n"
            "2. [필수 행정 절차 및 사전 선결 요건]\n"
            "  - 결재 또는 시행 전 반드시 거쳐야 할 행정 절차 (예: 일상감사, 계약심사, 사전예고, 의견수렴, 위원회 심의, 유관부서 협의 등)\n"
            "  - 계약/지출 방식에 따른 요건 (수의계약 사유, 경쟁입찰 기준액, 지정정보처리장치 공고 등)\n\n"
            "3. [감사 지적 사례 예방 & 실무 체크리스트]\n"
            "  - 사후 감사나 지도점검에서 자주 지적되는 위험 요소\n"
            "  - 실무 담당자가 누락하기 쉬운 필수 첨부 서류 및 사전 징구 서류\n\n"
            "가독성이 좋도록 개조식 기호(□, ○, -)와 굵은 글씨를 활용하여 공무원 실무자가 즉시 참고할 수 있게 작성하세요."
        )

        user_content = f"[분석할 업무 내용]:\n{self.doc_text}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        self.worker = AIChatWorker(messages=messages, parent=self)
        self.worker.chunk_received.connect(self._on_chunk)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_chunk(self, chunk: str) -> None:
        self.analysis_result += chunk
        self.viewer.setPlainText(self.analysis_result)
        cursor = self.viewer.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.viewer.setTextCursor(cursor)

    def _on_finished(self, text: str) -> None:
        self.analysis_result = text
        self.viewer.setPlainText(text)
        self.progress_bar.hide()
        self.btn_reanalyze.setEnabled(True)
        self.btn_insert.setEnabled(bool(text.strip()))

    def _on_error(self, err: str) -> None:
        self.progress_bar.hide()
        self.btn_reanalyze.setEnabled(True)
        self.btn_insert.setEnabled(False)
        QMessageBox.critical(self, "분석 오류", err)

    def _insert_to_editor(self) -> None:
        text = self.analysis_result.strip()
        if not text:
            return

        insert_text = f"\n\n[업무 관련 법령 및 행정절차 분석]\n{text}\n"
        if self.editor and hasattr(self.editor, "insert_text_at_cursor"):
            self.editor.insert_text_at_cursor(insert_text)
            self.close()
        else:
            QGuiApplication.clipboard().setText(insert_text)
            QMessageBox.information(self, "안내", "클립보드에 복사되었습니다.")

    def _copy_result(self) -> None:
        text = self.analysis_result.strip()
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        QMessageBox.information(self, "복사 완료", "분석 결과가 클립보드에 복사되었습니다.")
