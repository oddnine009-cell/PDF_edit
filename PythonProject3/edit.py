import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from PySide6.QtCore import Qt, QRect, QSize, QByteArray, QBuffer, QIODevice, QMimeData
from PySide6.QtGui import QAction, QColor, QDrag, QFont, QImage, QImageReader, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGridLayout, QLabel, QMainWindow, QMessageBox,
    QProgressDialog, QScrollArea, QSizePolicy, QToolBar, QVBoxLayout, QWidget,
    QDialog, QDialogButtonBox, QFormLayout, QComboBox, QDoubleSpinBox, QSpinBox,
    QCheckBox, QGroupBox
)

PT_PER_MM = 72 / 25.4
INTERNAL_MIME = "application/x-smallpdf-local-card"
SUPPORTED_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

CARD_W, CARD_H = 220, 340
THUMB_W, THUMB_H = 180, 250
CARD_SPACING = 22


def mm_to_pt(mm: float) -> float:
    return mm * PT_PER_MM


def pt_to_mm(pt: float) -> float:
    return pt / PT_PER_MM


def event_pos(event):
    if hasattr(event, "position"):
        return event.position().toPoint()
    return event.pos()


@dataclass
class PageAsset:
    uid: int
    kind: str
    path: str
    title: str
    page_index: int | None = None
    source_w: float = 0
    source_h: float = 0
    rotation: int = 0


@dataclass
class ExportSettings:
    page_w_pt: float = mm_to_pt(210)
    page_h_pt: float = mm_to_pt(297)
    margin_pt: float = 0
    use_asset_page_size: bool = False
    compression_mode: str = "images"
    image_dpi: int = 150
    jpeg_quality: int = 75
    grayscale: bool = False
    optimize_save: bool = True


PAGE_PRESETS = [
    ("A4 竖版 210 × 297 mm", 210, 297),
    ("A4 横版 297 × 210 mm", 297, 210),
    ("A3 竖版 297 × 420 mm", 297, 420),
    ("A3 横版 420 × 297 mm", 420, 297),
    ("Letter 竖版 216 × 279 mm", 215.9, 279.4),
    ("Letter 横版 279 × 216 mm", 279.4, 215.9),
    ("16:9 横版 280 × 157.5 mm", 280, 157.5),
    ("1:1 方形 210 × 210 mm", 210, 210),
    ("自定义尺寸", -1, -1),
]


class SettingsDialog(QDialog):
    def __init__(self, parent, settings: ExportSettings):
        super().__init__(parent)
        self.setWindowTitle("页面尺寸 / 压缩参数")
        self.setMinimumWidth(540)
        self.settings = settings

        root = QVBoxLayout(self)

        page_group = QGroupBox("输出图布 / 页面尺寸")
        page_form = QFormLayout(page_group)

        self.asset_size_check = QCheckBox("每页使用素材原始尺寸（PDF 保留原页面方向；图片按 DPI 换算）")
        self.asset_size_check.setChecked(settings.use_asset_page_size)
        self.asset_size_check.stateChanged.connect(self.update_page_controls)

        self.preset_combo = QComboBox()
        for name, _, _ in PAGE_PRESETS:
            self.preset_combo.addItem(name)
        self.preset_combo.currentIndexChanged.connect(self.on_preset_changed)

        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(10, 5000)
        self.width_spin.setDecimals(2)
        self.width_spin.setSuffix(" mm")
        self.width_spin.setValue(pt_to_mm(settings.page_w_pt))

        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(10, 5000)
        self.height_spin.setDecimals(2)
        self.height_spin.setSuffix(" mm")
        self.height_spin.setValue(pt_to_mm(settings.page_h_pt))

        self.margin_spin = QDoubleSpinBox()
        self.margin_spin.setRange(0, 500)
        self.margin_spin.setDecimals(2)
        self.margin_spin.setSuffix(" mm")
        self.margin_spin.setValue(pt_to_mm(settings.margin_pt))

        page_form.addRow("原始尺寸：", self.asset_size_check)
        page_form.addRow("固定尺寸预设：", self.preset_combo)
        page_form.addRow("固定宽度：", self.width_spin)
        page_form.addRow("固定高度：", self.height_spin)
        page_form.addRow("页面边距：", self.margin_spin)
        root.addWidget(page_group)

        comp_group = QGroupBox("PDF 压缩参数")
        comp_form = QFormLayout(comp_group)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("高清/保留矢量：PDF 页面不转图片，图片原图插入", "preserve")
        self.mode_combo.addItem("推荐压缩：PDF 保留矢量，图片按 DPI/JPEG 压缩", "images")
        self.mode_combo.addItem("强压缩：PDF 页面也转 JPEG，体积小但文字不可复制/搜索", "raster")
        idx = self.mode_combo.findData(settings.compression_mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)

        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(50, 600)
        self.dpi_spin.setSingleStep(10)
        self.dpi_spin.setValue(settings.image_dpi)
        self.dpi_spin.setSuffix(" DPI")

        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(10, 95)
        self.quality_spin.setSingleStep(5)
        self.quality_spin.setValue(settings.jpeg_quality)
        self.quality_spin.setSuffix(" JPEG 质量")

        self.gray_check = QCheckBox("转为灰度")
        self.gray_check.setChecked(settings.grayscale)

        self.optimize_check = QCheckBox("保存时清理和压缩 PDF 结构")
        self.optimize_check.setChecked(settings.optimize_save)

        comp_form.addRow("压缩模式：", self.mode_combo)
        comp_form.addRow("图片/栅格化 DPI：", self.dpi_spin)
        comp_form.addRow("JPEG 质量：", self.quality_spin)
        comp_form.addRow("", self.gray_check)
        comp_form.addRow("", self.optimize_check)
        root.addWidget(comp_group)

        tip = QLabel(
            "说明：\n"
            "1. 想保留 PDF 横竖版和原始尺寸，勾选“每页使用素材原始尺寸”。\n"
            "2. 推荐压缩会保留 PDF 文字层，只压缩图片。\n"
            "3. 强压缩会把 PDF 页面转成图片，体积更小，但文字不能复制/搜索。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#666;")
        root.addWidget(tip)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.guess_preset()
        self.update_page_controls()

    def guess_preset(self):
        w = round(pt_to_mm(self.settings.page_w_pt), 1)
        h = round(pt_to_mm(self.settings.page_h_pt), 1)
        for i, (_, pw, ph) in enumerate(PAGE_PRESETS):
            if pw == -1:
                continue
            if abs(w - pw) < 0.5 and abs(h - ph) < 0.5:
                self.preset_combo.setCurrentIndex(i)
                return
        self.preset_combo.setCurrentIndex(len(PAGE_PRESETS) - 1)

    def update_page_controls(self):
        fixed_enabled = not self.asset_size_check.isChecked()
        self.preset_combo.setEnabled(fixed_enabled)
        self.width_spin.setEnabled(fixed_enabled)
        self.height_spin.setEnabled(fixed_enabled)

    def on_preset_changed(self, index: int):
        _, w, h = PAGE_PRESETS[index]
        if w == -1 and h == -1:
            return
        self.width_spin.setValue(w)
        self.height_spin.setValue(h)

    def get_settings(self) -> ExportSettings:
        return ExportSettings(
            page_w_pt=mm_to_pt(self.width_spin.value()),
            page_h_pt=mm_to_pt(self.height_spin.value()),
            margin_pt=mm_to_pt(self.margin_spin.value()),
            use_asset_page_size=self.asset_size_check.isChecked(),
            compression_mode=self.mode_combo.currentData(),
            image_dpi=self.dpi_spin.value(),
            jpeg_quality=self.quality_spin.value(),
            grayscale=self.gray_check.isChecked(),
            optimize_save=self.optimize_check.isChecked(),
        )


class PageCard(QFrame):
    def __init__(self, main_window, asset: PageAsset, index: int):
        super().__init__()
        self.main_window = main_window
        self.asset = asset
        self.drag_start_pos = None

        self.setFixedSize(CARD_W, CARD_H)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAcceptDrops(False)

        self.preview_label = QLabel()
        self.preview_label.setFixedSize(THUMB_W, THUMB_H)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setPixmap(self.main_window.make_preview_pixmap(asset))

        self.title_label = QLabel(f"{index + 1:03d}  {asset.title}")
        self.title_label.setWordWrap(True)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setFont(QFont("Microsoft YaHei", 9))

        kind_text = "PDF 页面" if asset.kind == "pdf" else "图片页面"
        if asset.rotation:
            kind_text += f" / 旋转 {asset.rotation}°"
        self.type_label = QLabel(kind_text)
        self.type_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.type_label.setFont(QFont("Microsoft YaHei", 8))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)
        layout.addWidget(self.preview_label, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)
        layout.addWidget(self.type_label)
        self.update_style()

    def update_style(self):
        selected = self.main_window.selected_uid == self.asset.uid
        border = "3px solid #3478f6" if selected else "1px solid #cfcfcf"
        self.setStyleSheet(f"""
            QFrame {{ background:white; border:{border}; border-radius:12px; }}
            QLabel {{ border:none; color:#222; }}
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_pos = event.position().toPoint()
            self.main_window.select_asset(self.asset.uid)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if self.drag_start_pos is None:
            return
        current_pos = event.position().toPoint()
        if (current_pos - self.drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return

        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(INTERNAL_MIME, str(self.asset.uid).encode("utf-8"))
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(current_pos)
        drag.exec(Qt.DropAction.MoveAction)

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)


class CardGrid(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.card_widgets: list[PageCard] = []
        self.drop_index: int | None = None
        self.current_columns = 0

        self.setAcceptDrops(True)
        self.setMinimumWidth(700)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)

        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(24, 24, 24, 24)
        self.grid.setHorizontalSpacing(CARD_SPACING)
        self.grid.setVerticalSpacing(CARD_SPACING)
        self.setStyleSheet("QWidget{background:#f0f0f0;}")

    def calculate_columns(self) -> int:
        return max(1, max(1, self.width() - 48) // (CARD_W + CARD_SPACING))

    def rebuild(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
        self.card_widgets.clear()

        columns = self.calculate_columns()
        self.current_columns = columns

        if not self.main_window.assets:
            empty = QLabel("把 PDF、图片或文件夹直接拖到这里\n导入后可以像 Smallpdf 一样拖动卡片排序")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color:#888; font-size:18px; padding:120px;")
            self.grid.addWidget(empty, 0, 0)
            self.updateGeometry()
            self.update()
            return

        for i, asset in enumerate(self.main_window.assets):
            card = PageCard(self.main_window, asset, i)
            self.card_widgets.append(card)
            self.grid.addWidget(card, i // columns, i % columns)

        self.grid.setRowStretch((len(self.main_window.assets) // columns) + 1, 1)
        self.updateGeometry()
        self.update()

    def update_selection_styles(self):
        for card in self.card_widgets:
            card.update_style()

    def resizeEvent(self, event):
        new_columns = self.calculate_columns()
        if new_columns != self.current_columns:
            self.rebuild()
        super().resizeEvent(event)

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if mime.hasFormat(INTERNAL_MIME):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            return
        if mime.hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        mime = event.mimeData()
        if mime.hasFormat(INTERNAL_MIME):
            self.drop_index = self.index_at_position(event_pos(event))
            self.update()
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            return
        if mime.hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self.drop_index = None
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        mime = event.mimeData()
        if mime.hasFormat(INTERNAL_MIME):
            try:
                uid = int(bytes(mime.data(INTERNAL_MIME)).decode("utf-8"))
            except ValueError:
                return
            target_index = self.index_at_position(event_pos(event))
            self.drop_index = None
            self.main_window.move_asset(uid, target_index)
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            self.update()
            return

        if mime.hasUrls():
            paths = [url.toLocalFile() for url in mime.urls() if url.toLocalFile()]
            if paths:
                self.main_window.import_paths(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def index_at_position(self, pos) -> int:
        if not self.card_widgets:
            return 0

        rows: dict[int, list[tuple[int, PageCard]]] = {}
        for i, card in enumerate(self.card_widgets):
            rows.setdefault(card.geometry().top(), []).append((i, card))

        for _, row_cards in sorted(rows.items(), key=lambda x: x[0]):
            row_cards = sorted(row_cards, key=lambda x: x[1].geometry().left())
            top = min(c.geometry().top() for _, c in row_cards) - CARD_SPACING // 2
            bottom = max(c.geometry().bottom() for _, c in row_cards) + CARD_SPACING // 2
            if top <= pos.y() <= bottom:
                for i, card in row_cards:
                    if pos.x() < card.geometry().center().x():
                        return i
                return row_cards[-1][0] + 1

        if pos.y() < self.card_widgets[0].geometry().top():
            return 0
        return len(self.card_widgets)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.drop_index is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#3478f6"), 4))

        if not self.card_widgets:
            x, y1, y2 = 28, 28, CARD_H + 28
        elif self.drop_index < len(self.card_widgets):
            g = self.card_widgets[self.drop_index].geometry()
            x, y1, y2 = g.left() - CARD_SPACING // 2, g.top(), g.bottom()
        else:
            g = self.card_widgets[-1].geometry()
            x, y1, y2 = g.right() + CARD_SPACING // 2, g.top(), g.bottom()
        painter.drawLine(x, y1, x, y2)
        painter.end()


class SmallPdfLikeEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Smallpdf-like PDF / 图片排序合并压缩工具")
        self.resize(1320, 880)

        self.assets: list[PageAsset] = []
        self.next_uid = 1
        self.selected_uid: int | None = None
        self.pdf_cache: dict[str, fitz.Document] = {}
        self.preview_cache: dict[tuple[int, int], QPixmap] = {}
        self.settings = ExportSettings()

        self.card_grid = CardGrid(self)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.card_grid)
        self.scroll_area.setAcceptDrops(True)
        self.setCentralWidget(self.scroll_area)
        self.setAcceptDrops(True)

        self.build_toolbar()
        self.card_grid.rebuild()
        self.update_status()

    def build_toolbar(self):
        toolbar = QToolBar("工具")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        actions = [
            ("导入文件", self.choose_files),
            ("页面/压缩参数", self.open_settings_dialog),
            ("导出 PDF", self.export_pdf),
            (None, None),
            ("选中右转 90°", self.rotate_selected),
            ("删除选中", self.delete_selected),
            ("清空全部", self.clear_all),
        ]
        for text, func in actions:
            if text is None:
                toolbar.addSeparator()
                continue
            act = QAction(text, self)
            act.triggered.connect(func)
            toolbar.addAction(act)

    def update_status(self, message: str | None = None):
        if self.settings.use_asset_page_size:
            size_text = "页面：每页使用素材原始尺寸"
        else:
            size_text = f"页面：{pt_to_mm(self.settings.page_w_pt):.1f} × {pt_to_mm(self.settings.page_h_pt):.1f} mm"
        mode_name = {"preserve": "高清/保留矢量", "images": "推荐压缩", "raster": "强压缩"}.get(self.settings.compression_mode)
        comp_text = f"压缩：{mode_name}，{self.settings.image_dpi} DPI，JPEG {self.settings.jpeg_quality}"
        prefix = f"{message} | " if message else ""
        self.statusBar().showMessage(prefix + f"{size_text} | 边距：{pt_to_mm(self.settings.margin_pt):.1f} mm | {comp_text}")

    def open_settings_dialog(self):
        dialog = SettingsDialog(self, self.settings)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings = dialog.get_settings()
            self.update_status("已更新页面尺寸和压缩参数")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls() if url.toLocalFile()]
            if paths:
                self.import_paths(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def select_asset(self, uid: int):
        self.selected_uid = uid
        self.card_grid.update_selection_styles()

    def move_asset(self, uid: int, target_index: int):
        old_index = next((i for i, a in enumerate(self.assets) if a.uid == uid), None)
        if old_index is None:
            return
        target_index = max(0, min(target_index, len(self.assets)))
        if target_index > old_index:
            target_index -= 1
        if target_index == old_index:
            self.card_grid.rebuild()
            return
        asset = self.assets.pop(old_index)
        self.assets.insert(target_index, asset)
        self.selected_uid = uid
        self.card_grid.rebuild()
        self.update_status(f"已移动：{asset.title}")

    def rotate_selected(self):
        if self.selected_uid is None:
            return
        for asset in self.assets:
            if asset.uid == self.selected_uid:
                asset.rotation = (asset.rotation + 90) % 360
                self.clear_preview_cache_for(asset.uid)
                break
        self.card_grid.rebuild()
        self.update_status("已旋转选中页面")

    def delete_selected(self):
        if self.selected_uid is None:
            return
        before = len(self.assets)
        self.assets = [a for a in self.assets if a.uid != self.selected_uid]
        if len(self.assets) != before:
            self.selected_uid = None
            self.card_grid.rebuild()
            self.update_status("已删除选中页面")

    def clear_all(self):
        if not self.assets:
            return
        reply = QMessageBox.question(self, "确认清空", "确定要清空当前全部页面吗？",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.assets.clear()
            self.selected_uid = None
            self.preview_cache.clear()
            self.card_grid.rebuild()
            self.update_status("已清空全部页面")

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择 PDF 或图片", "",
            "PDF / 图片 (*.pdf *.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff);;所有文件 (*.*)",
        )
        if paths:
            self.import_paths(paths)

    def import_paths(self, paths: list[str]):
        files = [p for p in self.expand_paths(paths) if Path(p).suffix.lower() in SUPPORTED_EXTS]
        if not files:
            QMessageBox.warning(self, "没有可导入文件", "请拖入 PDF、PNG、JPG、JPEG、WEBP、BMP 或 TIFF 文件。")
            return

        added = 0
        progress = QProgressDialog("正在导入文件...", "取消", 0, len(files), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(400)

        for i, path in enumerate(files):
            if progress.wasCanceled():
                break
            progress.setValue(i)
            progress.setLabelText(f"正在导入：{Path(path).name}")
            QApplication.processEvents()
            try:
                if Path(path).suffix.lower() == ".pdf":
                    added += self.import_pdf(path)
                else:
                    added += self.import_image(path)
            except Exception as e:
                QMessageBox.critical(self, "导入失败", f"文件导入失败：\n{path}\n\n错误：{e}")
        progress.setValue(len(files))
        self.card_grid.rebuild()
        self.update_status(f"已导入 {added} 个页面，拖动卡片即可排序")

    def expand_paths(self, paths: list[str]) -> list[str]:
        result = []
        for p in paths:
            path = Path(p)
            if path.is_file():
                result.append(str(path))
            elif path.is_dir():
                children = [c for c in path.rglob("*") if c.is_file() and c.suffix.lower() in SUPPORTED_EXTS]
                result.extend(str(c) for c in sorted(children, key=lambda x: str(x).lower()))
        return result

    def import_pdf(self, path: str) -> int:
        doc = self.open_pdf(path)
        file_name = Path(path).name
        for i in range(len(doc)):
            rect = doc[i].rect
            self.assets.append(PageAsset(
                uid=self.next_uid, kind="pdf", path=path, title=f"{file_name} - 第 {i + 1} 页",
                page_index=i, source_w=float(rect.width), source_h=float(rect.height), rotation=0
            ))
            self.next_uid += 1
            if i % 10 == 0:
                QApplication.processEvents()
        return len(doc)

    def import_image(self, path: str) -> int:
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if not size.isValid():
            raise RuntimeError("无法读取图片尺寸。")
        self.assets.append(PageAsset(
            uid=self.next_uid, kind="image", path=path, title=Path(path).name,
            source_w=float(size.width()), source_h=float(size.height()), rotation=0
        ))
        self.next_uid += 1
        return 1

    def open_pdf(self, path: str) -> fitz.Document:
        if path not in self.pdf_cache:
            self.pdf_cache[path] = fitz.open(path)
        return self.pdf_cache[path]

    def clear_preview_cache_for(self, uid: int):
        for key in [k for k in self.preview_cache if k[0] == uid]:
            self.preview_cache.pop(key, None)

    def make_preview_pixmap(self, asset: PageAsset) -> QPixmap:
        key = (asset.uid, asset.rotation)
        if key in self.preview_cache:
            return self.preview_cache[key]

        canvas = QPixmap(THUMB_W, THUMB_H)
        canvas.fill(QColor(245, 245, 245))
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setPen(QPen(QColor(190, 190, 190), 1))
        painter.setBrush(QColor(255, 255, 255))
        painter.drawRect(0, 0, THUMB_W - 1, THUMB_H - 1)

        try:
            raw = self.render_asset_thumbnail(asset)
        except Exception:
            raw = QPixmap(THUMB_W, THUMB_H)
            raw.fill(QColor(230, 230, 230))

        if asset.rotation:
            raw = raw.transformed(QTransform().rotate(asset.rotation), Qt.TransformationMode.SmoothTransformation)
        target = self.fit_pixmap_rect(raw.width(), raw.height(), QRect(8, 8, THUMB_W - 16, THUMB_H - 16))
        painter.drawPixmap(target, raw)
        painter.end()
        self.preview_cache[key] = canvas
        return canvas

    def render_asset_thumbnail(self, asset: PageAsset) -> QPixmap:
        if asset.kind == "pdf":
            doc = self.open_pdf(asset.path)
            page = doc[asset.page_index]
            rect = page.rect
            zoom = min(THUMB_W / rect.width, THUMB_H / rect.height)
            zoom = max(0.05, min(zoom * 2.2, 1.5))
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
            return QPixmap.fromImage(image)

        reader = QImageReader(asset.path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid():
            scale = min(THUMB_W * 2 / size.width(), THUMB_H * 2 / size.height(), 1.0)
            reader.setScaledSize(QSize(max(1, int(size.width() * scale)), max(1, int(size.height() * scale))))
        image = reader.read()
        if image.isNull():
            raise RuntimeError("图片缩略图读取失败。")
        return QPixmap.fromImage(image)

    def fit_pixmap_rect(self, src_w: int, src_h: int, box: QRect) -> QRect:
        if src_w <= 0 or src_h <= 0:
            return box
        scale = min(box.width() / src_w, box.height() / src_h)
        w, h = int(src_w * scale), int(src_h * scale)
        return QRect(box.x() + (box.width() - w) // 2, box.y() + (box.height() - h) // 2, w, h)

    def export_pdf(self):
        if not self.assets:
            QMessageBox.warning(self, "没有内容", "请先导入 PDF 或图片。")
            return

        save_path, _ = QFileDialog.getSaveFileName(self, "导出 PDF", "sorted_output.pdf", "PDF 文件 (*.pdf)")
        if not save_path:
            return
        if not save_path.lower().endswith(".pdf"):
            save_path += ".pdf"

        out_doc = fitz.open()
        warnings = []
        progress = QProgressDialog("正在导出 PDF...", "取消", 0, len(self.assets), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(400)

        try:
            for i, asset in enumerate(self.assets):
                if progress.wasCanceled():
                    break
                progress.setValue(i)
                progress.setLabelText(f"正在导出第 {i + 1} / {len(self.assets)} 页")
                QApplication.processEvents()

                page_w, page_h = self.output_page_size_for(asset)
                out_page = out_doc.new_page(width=page_w, height=page_h)
                target = self.compute_target_rect(asset, page_w, page_h)
                try:
                    self.draw_asset_to_page(out_page, target, asset)
                except Exception as e:
                    warnings.append(f"第 {i + 1} 页导出失败：{asset.title}，错误：{e}")
            progress.setValue(len(self.assets))

            if self.settings.optimize_save:
                out_doc.save(save_path, deflate=True, garbage=4, clean=True)
            else:
                out_doc.save(save_path)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出 PDF 失败：\n{e}")
            return
        finally:
            out_doc.close()

        if warnings:
            QMessageBox.warning(self, "导出完成，但有警告", f"PDF 已导出：\n{save_path}\n\n" + "\n".join(warnings[:10]))
        else:
            QMessageBox.information(self, "导出完成", f"PDF 已导出：\n{save_path}")
        self.update_status("导出完成")

    def output_page_size_for(self, asset: PageAsset) -> tuple[float, float]:
        if not self.settings.use_asset_page_size:
            return self.settings.page_w_pt, self.settings.page_h_pt

        if asset.kind == "pdf":
            w, h = asset.source_w, asset.source_h
        else:
            w = asset.source_w / self.settings.image_dpi * 72
            h = asset.source_h / self.settings.image_dpi * 72

        if asset.rotation in [90, 270]:
            w, h = h, w
        return max(10, w), max(10, h)

    def compute_target_rect(self, asset: PageAsset, page_w: float, page_h: float) -> fitz.Rect:
        margin = min(self.settings.margin_pt, page_w / 2 - 1, page_h / 2 - 1)
        content_w = max(1, page_w - margin * 2)
        content_h = max(1, page_h - margin * 2)

        src_w, src_h = asset.source_w, asset.source_h
        if asset.rotation in [90, 270]:
            src_w, src_h = src_h, src_w
        if src_w <= 0 or src_h <= 0:
            return fitz.Rect(margin, margin, page_w - margin, page_h - margin)

        scale = min(content_w / src_w, content_h / src_h)
        target_w, target_h = src_w * scale, src_h * scale
        x0, y0 = (page_w - target_w) / 2, (page_h - target_h) / 2
        return fitz.Rect(x0, y0, x0 + target_w, y0 + target_h)

    def draw_asset_to_page(self, out_page, target_rect: fitz.Rect, asset: PageAsset):
        mode = self.settings.compression_mode

        if mode == "raster":
            if asset.kind == "pdf":
                data = self.pdf_page_to_jpeg_bytes(asset)
            else:
                data = self.image_to_jpeg_bytes_for_export(asset, target_rect)
            out_page.insert_image(target_rect, stream=data, keep_proportion=True, overlay=True, rotate=asset.rotation)
            return

        if asset.kind == "pdf":
            src_doc = self.open_pdf(asset.path)
            out_page.show_pdf_page(target_rect, src_doc, asset.page_index, keep_proportion=True, overlay=True, rotate=asset.rotation)
            return

        if mode == "preserve":
            try:
                out_page.insert_image(target_rect, filename=asset.path, keep_proportion=True, overlay=True, rotate=asset.rotation)
                return
            except Exception:
                pass

        data = self.image_to_jpeg_bytes_for_export(asset, target_rect)
        out_page.insert_image(target_rect, stream=data, keep_proportion=True, overlay=True, rotate=asset.rotation)

    def pdf_page_to_jpeg_bytes(self, asset: PageAsset) -> bytes:
        page = self.open_pdf(asset.path)[asset.page_index]
        zoom = self.settings.image_dpi / 72
        max_side = 6500
        predicted_w, predicted_h = page.rect.width * zoom, page.rect.height * zoom
        if max(predicted_w, predicted_h) > max_side:
            zoom *= max_side / max(predicted_w, predicted_h)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        return self.qimage_to_jpeg_bytes(img, self.settings.jpeg_quality, self.settings.grayscale)

    def image_to_jpeg_bytes_for_export(self, asset: PageAsset, target_rect: fitz.Rect) -> bytes:
        reader = QImageReader(asset.path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid():
            box_w_px = max(1, int(target_rect.width / 72 * self.settings.image_dpi))
            box_h_px = max(1, int(target_rect.height / 72 * self.settings.image_dpi))
            if asset.rotation in [90, 270]:
                box_w_px, box_h_px = box_h_px, box_w_px
            scale = min(box_w_px / max(1, size.width()), box_h_px / max(1, size.height()), 1.0)
            reader.setScaledSize(QSize(max(1, int(size.width() * scale)), max(1, int(size.height() * scale))))
        image = reader.read()
        if image.isNull():
            raise RuntimeError("图片读取失败，无法压缩导出。")
        return self.qimage_to_jpeg_bytes(image, self.settings.jpeg_quality, self.settings.grayscale)

    def qimage_to_jpeg_bytes(self, image: QImage, quality: int, grayscale: bool) -> bytes:
        if image.hasAlphaChannel():
            white = QImage(image.width(), image.height(), QImage.Format.Format_RGB888)
            white.fill(QColor(255, 255, 255))
            p = QPainter(white)
            p.drawImage(0, 0, image)
            p.end()
            image = white
        else:
            image = image.convertToFormat(QImage.Format.Format_RGB888)

        if grayscale:
            image = image.convertToFormat(QImage.Format.Format_Grayscale8)

        arr = QByteArray()
        buffer = QBuffer(arr)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "JPEG", quality)
        buffer.close()
        return bytes(arr)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete:
            self.delete_selected()
            return
        if event.key() == Qt.Key.Key_R:
            self.rotate_selected()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        for doc in self.pdf_cache.values():
            try:
                doc.close()
            except Exception:
                pass
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = SmallPdfLikeEditor()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()