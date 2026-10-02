from __future__ import annotations

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

import requests

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

from taskcalendar.paths import data_path

logger = logging.getLogger(__name__)

DEFAULT_AI_ENDPOINT = "https://api.commandcode.ai/provider/v1/chat/completions"
DEFAULT_AI_KEY = "user_2PKt7EHf5NygyxWLz62XfKn1ZT6aKx2P6Q2kEpRP2uiUUjTh66Eepmnjcyvwph6peG8U6dCCrZ4mjSvmnYowyomN"
DEFAULT_AI_MODEL = "gpt-5.6-sol"


class AIConfigManager:
    """AI API 설정 관리자 (로컬 영구 저장)"""

    _config_cache: dict[str, Any] | None = None

    @classmethod
    def get_config_file(cls) -> Path:
        p = data_path("ai_config.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @classmethod
    def load(cls) -> dict[str, Any]:
        if cls._config_cache is not None:
            return cls._config_cache

        cfg_file = cls.get_config_file()
        default_cfg: dict[str, Any] = {
            "provider": "commandcode",
            "endpoint": DEFAULT_AI_ENDPOINT,
            "api_key": DEFAULT_AI_KEY,
            "model": DEFAULT_AI_MODEL,
            "ssl_verify": True,
            "timeout_sec": 35,
            "stream": True,
        }

        if cfg_file.exists():
            try:
                data = json.loads(cfg_file.read_text(encoding="utf-8"))
                default_cfg.update(data)
            except Exception as e:
                logger.warning("Failed to load ai_config.json: %s", e)

        cls._config_cache = default_cfg
        return cls._config_cache

    @classmethod
    def save(cls, new_cfg: dict[str, Any]) -> None:
        cfg = cls.load()
        cfg.update(new_cfg)
        cls._config_cache = cfg
        cfg_file = cls.get_config_file()
        try:
            cfg_file.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.error("Failed to save ai_config.json: %s", e)


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
        self.config = config or AIConfigManager.load()
        self._is_stopped = False

    def stop(self) -> None:
        self._is_stopped = True

    def run(self) -> None:
        endpoint = str(self.config.get("endpoint", DEFAULT_AI_ENDPOINT)).strip()
        api_key = str(self.config.get("api_key", DEFAULT_AI_KEY)).strip()
        model = str(self.config.get("model", DEFAULT_AI_MODEL)).strip()
        timeout = int(self.config.get("timeout_sec", 35))
        stream_enabled = bool(self.config.get("stream", True))

        if not (endpoint.startswith("http://") or endpoint.startswith("https://")):
            self.error.emit("올바른 HTTP/HTTPS API 엔드포인트 URL을 입력해 주세요.")
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
        }

        accumulated_text = ""
        try:
            with requests.post(
                endpoint,
                json=payload,
                headers=headers,
                stream=stream_enabled,
                timeout=timeout,
                verify=True,
            ) as resp:
                resp.raise_for_status()
                if stream_enabled:
                    for line in resp.iter_lines(decode_unicode=True):
                        if self._is_stopped:
                            break
                        if not line:
                            continue
                        line_str = line.strip()
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
                    res_data = resp.json()
                    choices = res_data.get("choices", [])
                    if choices:
                        content = choices[0].get("message", {}).get("content", "")
                        self.finished.emit(content)
                    else:
                        self.finished.emit(resp.text)
        except requests.exceptions.HTTPError as e:
            err_body = ""
            if e.response is not None:
                err_body = e.response.text[:300]
            self.error.emit(f"AI 서버 응답 오류 (HTTP {e.response.status_code if e.response else 'N/A'}):\n{err_body}")
        except requests.exceptions.SSLError as e:
            self.error.emit(f"SSL 보안 인증서 검증 실패:\n{e}\n\n(기관 전용 사설 인증서인 경우 Windows 인증서 저장소에 등록되어 있는지 확인해 주세요.)")
        except requests.exceptions.Timeout:
            self.error.emit(f"요청 시간 초과 ({timeout}초 경과). 서버 상태를 확인해 주세요.")
        except requests.exceptions.RequestException as e:
            self.error.emit(f"네트워크 통신 오류: {e}")
        except Exception as e:
            self.error.emit(f"AI 통신 오류 발생: {e}")


class AISettingsDialog(QDialog):
    """AI API 연동 설정 대화상자 (인터넷망/공공기관 행정망 프리셋 지원)"""

    def __init__(self, parent: QWidget | None = None, palette: dict[str, str] | None = None) -> None:
        super().__init__(parent)
        self.palette = palette or {}
        self.setWindowTitle("AI 설정")
        self.resize(500, 340)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self.cfg = AIConfigManager.load()
        self.init_ui()

    def init_ui(self) -> None:
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
        self.combo_preset.currentIndexChanged.connect(self._on_preset_changed)
        form.addRow("망 환경 프리셋:", self.combo_preset)

        self.edit_endpoint = QLineEdit(self)
        self.edit_endpoint.setText(self.cfg.get("endpoint", DEFAULT_AI_ENDPOINT))
        form.addRow("API 엔드포인트:", self.edit_endpoint)

        self.edit_key = QLineEdit(self)
        self.edit_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_key.setText(self.cfg.get("api_key", DEFAULT_AI_KEY))
        form.addRow("API Key:", self.edit_key)

        self.edit_model = QLineEdit(self)
        self.edit_model.setText(self.cfg.get("model", DEFAULT_AI_MODEL))
        form.addRow("모델명 (Model):", self.edit_model)

        self.chk_ssl = QCheckBox("표준 SSL 보안 인증서 검증 활성화 (권장)", self)
        self.chk_ssl.setChecked(self.cfg.get("ssl_verify", True))
        form.addRow("보안 검증:", self.chk_ssl)

        layout.addWidget(group)

        lbl_hint = QLabel(
            "업무망/행정망 환경에서는 기관에서 발급받은 내부 엔드포인트 및 인증키를 입력하여 사용할 수 있습니다.",
            self,
        )
        lbl_hint.setWordWrap(True)
        lbl_hint.setStyleSheet(f"color: {muted}; font-size: 11px;")
        layout.addWidget(lbl_hint)

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

        curr_provider = self.cfg.get("provider", "commandcode")
        idx = self.combo_preset.findData(curr_provider)
        if idx >= 0:
            self.combo_preset.setCurrentIndex(idx)

    def _on_preset_changed(self, index: int) -> None:
        data = self.combo_preset.currentData()
        if data == "commandcode":
            self.edit_endpoint.setText("https://api.commandcode.ai/provider/v1/chat/completions")
            self.edit_model.setText("gpt-5.6-sol")
            if not self.edit_key.text() or self.edit_key.text().startswith("HCX"):
                self.edit_key.setText(DEFAULT_AI_KEY)
        elif data == "clova_gov":
            self.edit_endpoint.setText("https://api.clovastudio.go.kr/api/v1/chat/completions")
            self.edit_model.setText("HCX-GOV-THINK-V1-32B")
            self.edit_key.clear()
            self.edit_key.setPlaceholderText("기관에서 발급받은 CLOVA GOV API 키를 입력하세요")
        elif data == "gov":
            self.edit_endpoint.setText("https://dev.ai.go.kr/api/v1/chat/completions")
            self.edit_model.setText("HCX-003")
            self.edit_key.clear()

    def _save_settings(self) -> None:
        new_data = {
            "provider": self.combo_preset.currentData(),
            "endpoint": self.edit_endpoint.text().strip(),
            "api_key": self.edit_key.text().strip(),
            "model": self.edit_model.text().strip(),
            "ssl_verify": self.chk_ssl.isChecked(),
        }
        AIConfigManager.save(new_data)
        QMessageBox.information(self, "설정 완료", "AI 연동 설정이 성공적으로 저장되었습니다.")
        self.accept()


class AIChatDialog(QDialog):
    """
    [우클릭 ➔ 대화하기] 플로팅 팝업
    - 깔끔한 스킨 디자인 연동
    - 버튼/제목 이모티콘 제거
    - 요청 진행 중 '커서 위치에 삽입' 비활성화, 완료 시 활성화
    """

    def __init__(self, parent_editor: Any = None, initial_prompt: str = "", palette: dict[str, str] | None = None) -> None:
        super().__init__(parent_editor)
        self.editor = parent_editor
        self.palette = palette or {}
        self.worker: AIChatWorker | None = None
        self.full_response_text = ""

        self.setWindowTitle("AI 업무 도우미")
        self.resize(580, 580)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.init_ui(initial_prompt)

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

        # 커서 위치에 삽입 버튼 (초기 비활성화, 완료 시 활성화)
        self.btn_insert = QPushButton("커서 위치에 삽입", self)
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
        if not prompt:
            QMessageBox.warning(self, "입력 필요", "요청하실 내용을 입력해 주세요.")
            return

        self.viewer_edit.clear()
        self.full_response_text = ""
        self.btn_send.setEnabled(False)
        self.btn_insert.setEnabled(False)  # 진행 중 비활성화
        self.progress_bar.show()

        system_prompt = (
            "당신은 대한민국 공공기관 및 지방자치단체 행정 문서 작성 전문 AI 비서입니다.\n"
            "사용자의 요청에 따라 완성도 높은 공문서, 보고서 서식, 개조식 정리(□, ○, -, *), 기안문 등을 격식 있게 작성해 주세요.\n"
            "불필요한 인사말은 생략하고 곧바로 본문 서식 또는 요청된 작성 결과물을 명확하게 제공하세요."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

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
        self.viewer_edit.setPlainText(text)
        self.btn_send.setEnabled(True)
        self.btn_insert.setEnabled(bool(text.strip()))  # 결과 완료 시 활성화
        self.progress_bar.hide()

    def _on_error(self, err: str) -> None:
        self.btn_send.setEnabled(True)
        self.btn_insert.setEnabled(False)
        self.progress_bar.hide()
        QMessageBox.critical(self, "AI 오류", err)

    def _insert_to_editor(self) -> None:
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
