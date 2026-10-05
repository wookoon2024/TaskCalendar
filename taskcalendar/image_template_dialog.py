"""
공문서 및 행정업무용 이미지 템플릿 갤러리 및 AI 생성 다이얼로그
- 직인/관인, 결재선, 고무인, 상태 도장, 절차도, 통계 차트
- AI 맞춤형 직인/도장/서식 이미지 즉석 생성
- 본문 즉시 삽입 (에디터 연동) 및 파일로 저장 / 클립보드 복사
"""
import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon, QPixmap, QImage, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QWidget,
    QScrollArea,
    QGridLayout,
    QFrame,
    QLineEdit,
    QComboBox,
    QMessageBox,
    QFileDialog,
)

from taskcalendar.image_templates import (
    generate_default_templates,
    get_visual_templates,
    create_official_seal,
    create_approval_stamp,
    create_badge_stamp,
    create_end_mark,
    create_process_flow_chart,
)
from taskcalendar.paths import runtime_root
from taskcalendar.qt_styles import get_input_text

class ImageTemplateDialog(QDialog):
    """문서 꾸미기 이미지 템플릿 라이브러리 (폴더 기반 관리, 추가/삭제 지원)"""

    def __init__(self, parent_editor: Any = None, palette: dict[str, str] | None = None) -> None:
        super().__init__(parent_editor)
        self.editor = parent_editor
        self.palette = palette or {}
        self.setWindowTitle("문서 꾸미기 이미지 템플릿 라이브러리")
        self.resize(960, 680)
        self.setMinimumSize(840, 540)

        self.storage_dir = runtime_root() / "이미지 템플릿"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.categories: list[str] = []
        self.templates: list[dict] = []
        self._current_filtered_items: list[dict] = []
        self._rendered_count: int = 0
        self._batch_size: int = 40

        self._init_ui()
        self._reload_templates()

    def _init_ui(self) -> None:
        bg = self.palette.get("bg", "#F8FAFC")
        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F1F5F9")
        text = self.palette.get("text", "#0F172A")
        muted = self.palette.get("muted", "#64748B")
        line = self.palette.get("line", "#CBD5E1")
        accent = self.palette.get("accent", "#2563EB")
        btn_text = self.palette.get("button_text", "#FFFFFF")

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
                color: {text};
                font-family: 'Pretendard', 'Malgun Gothic', 'Segoe UI', sans-serif;
            }}
            QLabel {{
                color: {text};
            }}
            QListWidget {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 8px;
                padding: 4px;
                outline: none;
            }}
            QListWidget::item {{
                padding: 6px 10px;
                margin: 1px 0px;
                border-radius: 6px;
                font-size: 12px;
                font-weight: 500;
                color: {text};
                border: none;
                outline: none;
            }}
            QListWidget::item:focus {{
                border: none;
                outline: none;
            }}
            QListWidget::item:selected {{
                background-color: {accent};
                color: {btn_text};
                font-weight: bold;
                border: none;
                outline: none;
            }}
            QListWidget::item:hover:!selected {{
                background-color: {panel_alt};
            }}
            QLineEdit, QComboBox {{
                background-color: {panel};
                border: 1px solid {line};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
                color: {text};
            }}
            QLineEdit:focus, QComboBox:focus {{
                border-color: {accent};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # 좌/우 분할 레이아웃 (좌측: 카테고리 탭 및 폴더 관리, 우측: 갤러리 그리드)
        body_layout = QHBoxLayout()
        body_layout.setSpacing(12)

        # 좌측 패널: 카테고리 목록 + 폴더 관리 버튼
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        left_panel.setFixedWidth(180)

        lbl_cat_title = QLabel("이미지 분류")
        lbl_cat_title.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {muted}; padding-left: 2px;")
        left_layout.addWidget(lbl_cat_title)

        self.cat_list = QListWidget()
        self.cat_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cat_list.currentRowChanged.connect(self._on_category_changed)
        left_layout.addWidget(self.cat_list, 1)

        # 좌측 하단: 카테고리/이미지 추가 버튼들
        btn_add_img = QPushButton("➕ 이미지 추가", self)
        btn_add_img.setStyleSheet(f"background-color: {panel}; color: {text}; border: 1px solid {line}; border-radius: 6px; padding: 6px; font-size: 11px; font-weight: 500;")
        btn_add_img.setToolTip("선택한 분류 폴더에 외부 이미지(PNG, JPG, SVG 등)를 추가합니다.")
        btn_add_img.clicked.connect(self._on_add_image_clicked)
        left_layout.addWidget(btn_add_img)

        btn_add_folder = QPushButton("📁 새 분류 폴더", self)
        btn_add_folder.setStyleSheet(f"background-color: {panel_alt}; color: {text}; border: 1px solid {line}; border-radius: 6px; padding: 6px; font-size: 11px;")
        btn_add_folder.setToolTip("새로운 이미지 템플릿 분류 폴더를 생성합니다.")
        btn_add_folder.clicked.connect(self._on_add_folder_clicked)
        left_layout.addWidget(btn_add_folder)

        body_layout.addWidget(left_panel)

        # 우측 스택 위젯 (템플릿 갤러리 뷰)
        self.stack = QStackedWidget()

        self.gallery_area = QScrollArea()
        self.gallery_area.setWidgetResizable(True)
        self.gallery_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.gallery_area.setStyleSheet(f"background-color: {panel}; border: 1px solid {line}; border-radius: 8px;")
        self.gallery_content = QWidget()
        self.gallery_grid = QGridLayout(self.gallery_content)
        self.gallery_grid.setContentsMargins(12, 12, 12, 12)
        self.gallery_grid.setSpacing(10)
        self.gallery_area.setWidget(self.gallery_content)

        # 스크롤 하단 도달 시 추가 배치 로드
        self.gallery_area.verticalScrollBar().valueChanged.connect(self._on_gallery_scroll)
        self.stack.addWidget(self.gallery_area)

        body_layout.addWidget(self.stack, 1)
        layout.addLayout(body_layout, 1)

        # 하단 버튼 행
        bottom_row = QHBoxLayout()
        btn_open_folder = QPushButton("📂 템플릿 폴더 열기", self)
        btn_open_folder.setStyleSheet(f"background-color: {panel_alt}; color: {text}; border: 1px solid {line}; border-radius: 6px; padding: 6px 14px; font-size: 12px; font-weight: 500;")
        btn_open_folder.setToolTip("윈도우 파일 탐색기에서 이미지 템플릿 폴더를 직접 열어 파일을 추가/삭제합니다.")
        btn_open_folder.clicked.connect(self._on_open_storage_folder)
        bottom_row.addWidget(btn_open_folder)

        btn_refresh = QPushButton("🔄 새로고침", self)
        btn_refresh.setStyleSheet(f"background-color: {panel}; color: {text}; border: 1px solid {line}; border-radius: 6px; padding: 6px 12px; font-size: 12px;")
        btn_refresh.setToolTip("폴더의 변경 사항(파일 추가/삭제)을 다시 스캔하여 목록을 갱신합니다.")
        btn_refresh.clicked.connect(self._reload_templates)
        bottom_row.addWidget(btn_refresh)

        bottom_row.addStretch(1)

        btn_close = QPushButton("닫기", self)
        btn_close.setStyleSheet(f"background-color: {panel}; color: {text}; border: 1px solid {line}; border-radius: 6px; padding: 6px 18px; font-size: 12px;")
        btn_close.clicked.connect(self.close)
        bottom_row.addWidget(btn_close)

        layout.addLayout(bottom_row)

    def _reload_templates(self) -> None:
        """템플릿 폴더를 스캔하여 카테고리 탭과 갤러리 갱신"""
        self.templates = get_visual_templates()

        # 카테고리 목록 추출
        known_order = ["일러스트", "장식", "이미지·카툰"]
        cat_counts: dict[str, int] = {}
        for it in self.templates:
            c = it["category"]
            cat_counts[c] = cat_counts.get(c, 0) + 1

        # 폴더는 존재하지만 이미지가 없는 빈 폴더도 탭에 노출
        if self.storage_dir.exists():
            for p in self.storage_dir.iterdir():
                if p.is_dir() and p.name not in cat_counts:
                    cat_counts[p.name] = 0

        # 정렬: 일러스트, 장식, 이미지·카툰 우선, 이후 사용자 생성 폴더
        sorted_cats = sorted(
            cat_counts.keys(),
            key=lambda c: (0, known_order.index(c)) if c in known_order else (1, c)
        )
        self.categories = sorted_cats

        # 카테고리 리스트위젯 재구성
        curr_row = max(0, self.cat_list.currentRow())
        self.cat_list.blockSignals(True)
        self.cat_list.clear()

        # 전체 보기
        total_count = len(self.templates)
        self.cat_list.addItem(f"전체 보기 ({total_count})")
        for c in self.categories:
            cnt = cat_counts.get(c, 0)
            self.cat_list.addItem(f"{c} ({cnt})")

        self.cat_list.blockSignals(False)

        # 기존 선택 유지 또는 0번 선택
        if curr_row >= self.cat_list.count():
            curr_row = 0
        self.cat_list.setCurrentRow(curr_row)
        self._on_category_changed(curr_row)

    def _on_category_changed(self, row: int) -> None:
        if row <= 0:
            target_cat = "all"
        else:
            cat_idx = row - 1
            if cat_idx < len(self.categories):
                target_cat = self.categories[cat_idx]
            else:
                target_cat = "all"

        self.stack.setCurrentIndex(0)
        self._render_gallery(target_cat)

    def _get_current_target_category(self) -> str:
        """현재 선택된 카테고리명 반환 (전체 보기일 경우 첫 번째 카테고리 또는 '일러스트')"""
        row = self.cat_list.currentRow()
        if row > 0 and (row - 1) < len(self.categories):
            return self.categories[row - 1]
        if self.categories:
            return self.categories[0]
        return "일러스트"

    def _render_gallery(self, filter_cat: str) -> None:
        # 기존 그리드 아이템 정리
        while self.gallery_grid.count():
            item = self.gallery_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        self._current_filtered_items = [
            it for it in self.templates
            if filter_cat == "all" or it["category"] == filter_cat
        ]
        self._rendered_count = 0

        # 초기 1회분 렌더링
        self._render_next_batch()

    def _render_next_batch(self) -> None:
        """한 번에 _batch_size 개씩만 비동기적으로 추가 렌더링"""
        total = len(self._current_filtered_items)
        if self._rendered_count >= total:
            return

        end_idx = min(self._rendered_count + self._batch_size, total)
        items_to_render = self._current_filtered_items[self._rendered_count:end_idx]

        cols = 4
        panel = self.palette.get("panel", "#FFFFFF")
        panel_alt = self.palette.get("panel_alt", "#F8FAFC")
        line = self.palette.get("line", "#E2E8F0")
        text = self.palette.get("text", "#1E293B")
        muted = self.palette.get("muted", "#64748B")
        accent = self.palette.get("accent", "#2563EB")
        btn_text = self.palette.get("button_text", "#FFFFFF")

        start_idx = self._rendered_count
        for i, item_data in enumerate(items_to_render):
            idx = start_idx + i
            card = QFrame()
            card.setStyleSheet(f"""
                QFrame {{
                    background-color: {panel};
                    border: 1px solid {line};
                    border-radius: 8px;
                    padding: 4px;
                }}
                QFrame:hover {{
                    border-color: {accent};
                }}
            """)
            c_layout = QVBoxLayout(card)
            c_layout.setContentsMargins(6, 6, 6, 6)
            c_layout.setSpacing(4)

            # 이미지 미리보기
            lbl_preview = QLabel()
            lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_preview.setFixedHeight(95)
            lbl_preview.setStyleSheet("background-color: transparent; border: none; padding: 2px;")
            pix = QPixmap(item_data["path"])
            if not pix.isNull():
                lbl_preview.setPixmap(pix.scaled(130, 85, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            c_layout.addWidget(lbl_preview)

            # 제목
            lbl_title = QLabel(item_data["title"])
            lbl_title.setStyleSheet(f"border: none; background: transparent; font-size: 11px; font-weight: 500; color: {text};")
            lbl_title.setWordWrap(True)
            lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            c_layout.addWidget(lbl_title)

            # 동작 버튼 행 (삽입 / 복사 / 삭제)
            btn_row = QHBoxLayout()
            btn_row.setSpacing(4)

            btn_insert = QPushButton("본문 삽입")
            btn_insert.setStyleSheet(f"background-color: {accent}; color: {btn_text}; font-size: 10px; font-weight: 500; padding: 4px 6px; border-radius: 4px; border: none;")
            btn_insert.clicked.connect(lambda _, p=item_data["path"]: self._insert_to_editor(p))
            btn_row.addWidget(btn_insert, 1)

            btn_copy = QPushButton("복사")
            btn_copy.setStyleSheet(f"background-color: {panel_alt}; color: {text}; border: 1px solid {line}; font-size: 10px; padding: 4px 6px; border-radius: 4px;")
            btn_copy.clicked.connect(lambda _, p=item_data["path"]: self._copy_image(p))
            btn_row.addWidget(btn_copy)

            btn_del = QPushButton("🗑️")
            btn_del.setToolTip("이 템플릿 이미지 삭제")
            btn_del.setStyleSheet(f"background-color: {panel_alt}; color: #EF4444; border: 1px solid {line}; font-size: 10px; padding: 4px 6px; border-radius: 4px;")
            btn_del.clicked.connect(lambda _, p=item_data["path"], t=item_data["title"]: self._delete_image(p, t))
            btn_row.addWidget(btn_del)

            c_layout.addLayout(btn_row)

            row = idx // cols
            col = idx % cols
            self.gallery_grid.addWidget(card, row, col)

        self._rendered_count = end_idx
        self.gallery_grid.setRowStretch((self._rendered_count + cols - 1) // cols, 1)

    def _on_gallery_scroll(self, value: int) -> None:
        """스크롤이 85% 이상 내려가면 다음 배치 자동 로드"""
        sb = self.gallery_area.verticalScrollBar()
        if sb.maximum() > 0 and value >= sb.maximum() * 0.85:
            self._render_next_batch()

    def _insert_to_editor(self, filepath: str) -> None:
        """현재 에디터에 이미지 삽입"""
        if not os.path.exists(filepath):
            QMessageBox.warning(self, "오류", "이미지 파일을 찾을 수 없습니다.")
            return

        if self.editor and hasattr(self.editor, "insert_image_file"):
            self.editor.insert_image_file(filepath)
            self.close()
        else:
            self._copy_image(filepath)
            QMessageBox.information(self, "클립보드 복사", "이미지가 클립보드에 복사되었습니다.\n에디터에서 Ctrl+V로 붙여넣으세요.")
            self.close()

    def _copy_image(self, filepath: str) -> None:
        """이미지를 클립보드로 복사"""
        img = QImage(filepath)
        if not img.isNull():
            QGuiApplication.clipboard().setImage(img)
            QMessageBox.information(self, "복사 완료", "이미지가 클립보드에 복사되었습니다.")

    def _delete_image(self, filepath: str, title: str) -> None:
        """사용자가 템플릿 이미지 삭제"""
        p = Path(filepath)
        reply = QMessageBox.question(
            self,
            "템플릿 삭제 확인",
            f"'{title}' 이미지를 템플릿에서 완전히 삭제하시겠습니까?\n({p.name})",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                if p.exists():
                    p.unlink()
                self._reload_templates()
            except Exception as e:
                QMessageBox.warning(self, "삭제 실패", f"파일을 삭제할 수 없습니다: {e}")

    def _on_add_image_clicked(self) -> None:
        """사용자가 새 이미지 파일을 현재 선택된 분류 폴더로 추가"""
        target_cat = self._get_current_target_category()
        target_dir = self.storage_dir / target_cat
        target_dir.mkdir(parents=True, exist_ok=True)

        files, _ = QFileDialog.getOpenFileNames(
            self,
            f"'{target_cat}' 분류에 추가할 이미지 선택",
            "",
            "이미지 파일 (*.png *.jpg *.jpeg *.svg *.webp *.gif);;모든 파일 (*.*)"
        )
        if not files:
            return

        import shutil
        added_count = 0
        for src in files:
            src_path = Path(src)
            dst_path = target_dir / src_path.name
            counter = 1
            while dst_path.exists():
                dst_path = target_dir / f"{src_path.stem}_{counter}{src_path.suffix}"
                counter += 1
            try:
                shutil.copy2(src_path, dst_path)
                added_count += 1
            except Exception as e:
                QMessageBox.warning(self, "복사 오류", f"파일 복사 실패 ({src_path.name}): {e}")

        if added_count > 0:
            QMessageBox.information(self, "추가 완료", f"'{target_cat}' 분류에 {added_count}개의 이미지가 추가되었습니다.")
            self._reload_templates()

    def _on_add_folder_clicked(self) -> None:
        """새 이미지 분류 폴더 생성"""
        from PySide6.QtWidgets import QInputDialog
        folder_name, ok = get_input_text(
            self,
            "새 분류 폴더 추가",
            "새로운 이미지 분류(카테고리) 폴더 이름을 입력하세요:"
        )
        if ok and folder_name and folder_name.strip():
            clean_name = folder_name.strip().replace("/", "_").replace("\\", "_")
            new_dir = self.storage_dir / clean_name
            if new_dir.exists():
                QMessageBox.information(self, "안내", "이미 존재하는 폴더 이름입니다.")
                return
            new_dir.mkdir(parents=True, exist_ok=True)
            self._reload_templates()
            # 새로 생성된 폴더로 선택 이동
            for i in range(1, self.cat_list.count()):
                if self.cat_list.item(i).text().startswith(clean_name):
                    self.cat_list.setCurrentRow(i)
                    break

    def _on_open_storage_folder(self) -> None:
        """현재 선택된 카테고리 폴더 또는 전체 이미지 템플릿 폴더 열기"""
        row = self.cat_list.currentRow()
        if row > 0 and (row - 1) < len(self.categories):
            cat_name = self.categories[row - 1]
            cat_dir = self.storage_dir / cat_name
            target = cat_dir if cat_dir.exists() else self.storage_dir
        else:
            target = self.storage_dir

        os.startfile(str(target))
