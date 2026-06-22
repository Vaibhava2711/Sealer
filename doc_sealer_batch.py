#!/usr/bin/env python3
"""
DocSealer Batch — Desktop App
Each input file (JPG / PNG / HEIC / PDF / …) → one sealed TIFF per page.
Output TIFFs are named after the folio number found on that page.
If no folio found → falls back to originalname_pageN_sealed.tiff
Run: python3 doc_sealer_batch.py
"""

import sys, os, io, math, re, tempfile
from pathlib import Path

# ── PyQt6 ──────────────────────────────────────────────────────────────────
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QListWidget, QListWidgetItem,
    QProgressBar, QFrame, QSizePolicy, QMessageBox, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QPixmap, QDragEnterEvent, QDropEvent

# ── Processing libs ─────────────────────────────────────────────────────────
try:
    from PIL import Image, ImageEnhance, ImageChops, ImageFilter
    import img2pdf
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas as rl_canvas
    LIBS_OK = True
except ImportError as e:
    LIBS_OK = False
    LIBS_ERROR = str(e)

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIC_OK = True
except ImportError:
    HEIC_OK = False

try:
    from pdf2image import convert_from_path
    PDF2IMAGE_OK = True
except ImportError:
    PDF2IMAGE_OK = False

try:
    import pytesseract
    TESSERACT_OK = True
except ImportError:
    TESSERACT_OK = False

# ── Bundled paths (PyInstaller .exe) ─────────────────────────────────────────
if getattr(sys, 'frozen', False):
    # Poppler
    _poppler_path = os.path.join(sys._MEIPASS, 'poppler', 'bin')
    if not os.path.exists(_poppler_path):
        _poppler_path = None
    # Tesseract — set path so subprocess can find it
    _tess_path = os.path.join(sys._MEIPASS, 'tesseract', 'tesseract.exe')
    if os.path.exists(_tess_path):
        os.environ['PATH'] = os.path.join(sys._MEIPASS, 'tesseract') + os.pathsep + os.environ.get('PATH', '')
        os.environ['TESSDATA_PREFIX'] = os.path.join(sys._MEIPASS, 'tesseract', 'tessdata')
else:
    _poppler_path = None

# ── Constants ───────────────────────────────────────────────────────────────
MAX_TIFF_BYTES   = 5 * 1024 * 1024
RENDER_DPI_START = 150
RENDER_DPI_MIN   = 55
SEAL_MARGIN_PT   = 20
SEAL_OPACITY     = 1.0

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".pdf", ".bmp", ".webp",
                  ".tiff", ".tif", ".heic", ".heif"}

# ── Folio skip words ─────────────────────────────────────────────────────────
FOLIO_SKIP_WORDS = {
    "folio", "mandatory", "scheme", "name", "required", "change",
    "request", "specific", "applicable", "arn", "code", "euin",
    "sub", "distribution", "holder", "investor", "declaration",
    "old", "new", "limited", "bank", "securities", "mutual", "fund",
    "icici", "sbi", "hdfc", "axis", "kotak", "dsp", "canara",
    "nippon", "invesco", "union", "date", "place", "signature",
    "fields", "except", "filled", "only", "if", "all"
}

# ── AMC corrections ─────────────────────────────────────────────────────────
AMC_CORRECTIONS = {
    'ICICL':  'ICICI',
    'CICI':   'ICICI',
    'HDEC':   'HDFC',
}

# ── Known AMC list for fallback matching ─────────────────────────────────────
KNOWN_AMCS = [
    "HDFC", "SBI", "ICICI PRU", "ICICI", "AXIS", "KOTAK", "DSP",
    "CANARA", "NIPPON", "INVESCO", "UNION", "MOTILAL OSWAL",
    "PARAG PARIKH", "MIRAE", "FRANKLIN", "TATA", "UTI",
    "ADITYA BIRLA", "SUNDARAM", "EDELWEISS", "PGIM", "QUANTUM",
    "WHITEOAK", "NAVI", "BANDHAN", "BARODA", "JM", "LIC",
    "MAHINDRA", "SAMCO", "SHRIRAM", "TRUST", "ITI"
]

# ── Color Palette ────────────────────────────────────────────────────────────
C = {
    "bg":      "#0F1117",
    "surface": "#1A1D27",
    "card":    "#21253A",
    "accent":  "#4F7AFF",
    "accent2": "#7B5EA7",
    "success": "#22C55E",
    "warning": "#F59E0B",
    "danger":  "#EF4444",
    "text":    "#E8EAF0",
    "muted":   "#6B7280",
    "border":  "#2D3248",
}

STYLE = f"""
QMainWindow, QWidget {{
    background: {C['bg']};
    color: {C['text']};
    font-family: 'Segoe UI', 'SF Pro Display', Helvetica, Arial, sans-serif;
}}
QLabel {{ color: {C['text']}; background: transparent; }}
QPushButton {{
    background: {C['card']};
    color: {C['text']};
    border: 1px solid {C['border']};
    border-radius: 8px;
    padding: 8px 18px;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton:hover {{ background: {C['accent']}; border-color: {C['accent']}; }}
QPushButton:pressed {{ background: #3B5FCC; }}
QPushButton:disabled {{ background: {C['surface']}; color: {C['muted']}; border-color: {C['border']}; }}
QListWidget {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 10px;
    color: {C['text']};
    font-size: 13px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    padding: 8px 12px;
    border-radius: 6px;
    margin: 2px 4px;
}}
QListWidget::item:selected {{ background: {C['accent']}; color: white; }}
QListWidget::item:hover:!selected {{ background: {C['card']}; }}
QProgressBar {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 6px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {C['accent']}, stop:1 {C['accent2']});
    border-radius: 6px;
}}
QScrollBar:vertical {{
    background: {C['surface']};
    width: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {C['border']};
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  Drop Zone
# ═══════════════════════════════════════════════════════════════════════════════
class DropZone(QFrame):
    files_dropped = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(130)
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(8)

        self.icon_lbl = QLabel("📂")
        self.icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_lbl.setStyleSheet("font-size: 34px; background: transparent;")

        self.title_lbl = QLabel("Drop files here  or  click to browse")
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_lbl.setStyleSheet(
            f"font-size: 14px; font-weight: 600; color: {C['text']}; background: transparent;")

        self.sub_lbl = QLabel("JPG · PNG · HEIC · PDF · BMP · WEBP · TIFF")
        self.sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub_lbl.setStyleSheet(
            f"font-size: 11px; color: {C['muted']}; background: transparent;")

        lay.addWidget(self.icon_lbl)
        lay.addWidget(self.title_lbl)
        lay.addWidget(self.sub_lbl)
        self._update_style()

    def _update_style(self):
        border_col = C['accent'] if self._hover else C['border']
        bg_col     = "#1F2640" if self._hover else C['surface']
        self.setStyleSheet(f"""
            QFrame {{
                background: {bg_col};
                border: 2px dashed {border_col};
                border-radius: 14px;
            }}
        """)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._hover = True
            self._update_style()
            self.icon_lbl.setText("📥")

    def dragLeaveEvent(self, e):
        self._hover = False
        self._update_style()
        self.icon_lbl.setText("📂")

    def dropEvent(self, e: QDropEvent):
        self._hover = False
        self._update_style()
        self.icon_lbl.setText("📂")
        paths = [u.toLocalFile() for u in e.mimeData().urls()
                 if Path(u.toLocalFile()).suffix.lower() in SUPPORTED_EXTS]
        if paths:
            self.files_dropped.emit(paths)

    def mousePressEvent(self, e):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select Files", "",
            "Documents (*.jpg *.jpeg *.png *.heic *.heif *.pdf *.bmp *.webp *.tiff *.tif)"
        )
        if paths:
            self.files_dropped.emit(paths)


# ═══════════════════════════════════════════════════════════════════════════════
#  Seal Preview
# ═══════════════════════════════════════════════════════════════════════════════
class SealPreview(QLabel):
    seal_loaded = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.seal_path = None
        self.setMinimumSize(100, 100)
        self.setMaximumSize(100, 100)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(f"""
            QLabel {{
                background: {C['surface']};
                border: 2px dashed {C['border']};
                border-radius: 50px;
                color: {C['muted']};
                font-size: 11px;
            }}
        """)
        self.setText("No seal\nloaded")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to load your seal image")

    def mousePressEvent(self, e):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Seal Image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if path:
            self.load_seal(path)

    def load_seal(self, path: str):
        self.seal_path = path
        pix = QPixmap(path).scaled(
            88, 88,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.setPixmap(pix)
        self.setStyleSheet(f"""
            QLabel {{
                background: {C['surface']};
                border: 2px solid {C['accent']};
                border-radius: 50px;
            }}
        """)
        self.seal_loaded.emit(path)


# ═══════════════════════════════════════════════════════════════════════════════
#  Batch Worker
# ═══════════════════════════════════════════════════════════════════════════════
class BatchWorker(QThread):
    batch_progress = pyqtSignal(int, str)
    page_started   = pyqtSignal(int, int)
    page_done      = pyqtSignal(int, int, float, str)
    page_failed    = pyqtSignal(int, int, str)
    all_done       = pyqtSignal(int, int)

    def __init__(self, input_files: list, seal_path: str, output_folder: str):
        super().__init__()
        self.input_files   = input_files
        self.seal_path     = seal_path
        self.output_folder = Path(output_folder)

    # ── Step 1: Convert any file to PDF bytes ─────────────────────────────────
    def _file_to_pdf_bytes(self, fp: Path) -> bytes:
        if fp.suffix.lower() == ".pdf":
            return fp.read_bytes()
        img = Image.open(fp)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            img.save(tmp_path, format="PNG")
            return img2pdf.convert(tmp_path)
        finally:
            try: os.unlink(tmp_path)
            except OSError: pass

    # ── Step 2: Apply seal overlay ────────────────────────────────────────────
    def _make_seal_overlay(self, w_pt: float, h_pt: float,
                           seal_img: Image.Image) -> bytes:
        buf = io.BytesIO()
        c   = rl_canvas.Canvas(buf, pagesize=(w_pt, h_pt))

        seal_sz = max(60, min(160, int(min(w_pt, h_pt) * 0.21)))
        x = w_pt - SEAL_MARGIN_PT - seal_sz
        y = SEAL_MARGIN_PT

        rgba = seal_img.convert("RGBA")
        _w, _h = rgba.size
        _px    = rgba.load()
        _corners = [_px[0, 0][:3], _px[_w-1, 0][:3],
                    _px[0, _h-1][:3], _px[_w-1, _h-1][:3]]
        _bg = tuple(sum(ch[i] for ch in _corners) // 4 for i in range(3))
        _bg_img = Image.new("RGB", (_w, _h), _bg)
        _diff   = ImageChops.difference(rgba.convert("RGB"), _bg_img)
        _bbox   = _diff.point(lambda v: 255 if v > 20 else 0).convert("L").getbbox()
        if _bbox:
            pad   = 4
            _bbox = (max(0, _bbox[0]-pad), max(0, _bbox[1]-pad),
                     min(_w, _bbox[2]+pad), min(_h, _bbox[3]+pad))
            rgba  = rgba.crop(_bbox)

        if SEAL_OPACITY < 1.0:
            r, g, b, a = rgba.split()
            a = ImageEnhance.Brightness(a).enhance(SEAL_OPACITY)
            rgba.putalpha(a)

        tmp_seal = io.BytesIO()
        rgba.save(tmp_seal, format="PNG")
        tmp_seal.seek(0)

        c.saveState()
        c.drawImage(rl_canvas.ImageReader(tmp_seal),
                    x, y, seal_sz, seal_sz, mask='auto')
        c.restoreState()
        c.save()
        buf.seek(0)
        return buf.getvalue()

    # ── Step 3: Folio extraction ──────────────────────────────────────────────
    @staticmethod
    def _ocr(img: Image.Image, psm: int) -> str:
        """Run Tesseract via subprocess with 15s timeout."""
        import subprocess as _sp
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            img.save(tmp_path)
            result = _sp.run(
                ["tesseract", tmp_path, "stdout",
                 "-l", "eng", "--psm", str(psm), "--oem", "3"],
                capture_output=True, timeout=15)
            return (result.stdout or b"").decode("utf-8", errors="ignore")
        except _sp.TimeoutExpired:
            return ""
        except Exception:
            return ""
        finally:
            try: os.unlink(tmp_path)
            except OSError: pass

    @staticmethod
    def _is_folio_line(line: str) -> bool:
        """Return True if this OCR line looks like a folio number."""
        line = line.strip()
        # Strip leading noise chars (table borders read as | : [ etc.)
        line = re.sub(r'^[^\d]+', '', line).strip()
        if not line:
            return False
        # Must start with digit
        if not re.match(r'^\d', line):
            return False
        # Must contain 6+ consecutive digits
        if not re.search(r'\d{6,}', line):
            return False
        # Must not contain skip words
        words = re.findall(r'[a-zA-Z]{3,}', line.lower())
        return not any(w in FOLIO_SKIP_WORDS for w in words)

    @staticmethod
    def _clean_folio(line: str) -> str:
        """Extract just the folio number from a line.
        '| 11105259 / 37'     -> '11105259/37'
        '501762276477 Sundar' -> '501762276477'
        '34867057'            -> '34867057'
        """
        line = line.strip()
        line = re.sub(r'^[^\d]+', '', line).strip()
        m = re.match(r'^(\d[\d\s]*/\s*\d+|\d+)', line)
        if m:
            val = m.group(1)
            val = re.sub(r'(?<=\d)\s+(?=\d)', '', val)
            val = re.sub(r'\s*/\s*', '/', val)
            return val.strip()
        return ""

    @staticmethod
    def _extract_folios(text: str) -> list:
        """Filter OCR text line by line. Returns list with duplicates preserved."""
        if not text:
            return []
        folios = []
        for line in text.splitlines():
            if BatchWorker._is_folio_line(line):
                val = BatchWorker._clean_folio(line)
                if val:
                    folios.append(val)
        return folios

    @staticmethod
    def _get_crop(img: Image.Image, x_pct: float) -> Image.Image:
        """Crop and preprocess the folio column area for OCR."""
        w, h = img.size
        # Fixed y: 5%-38% = folio table area
        crop = img.crop((0, int(h * 0.05), int(w * x_pct), int(h * 0.38)))
        cw, ch = crop.size
        # Skip first 8% of crop = shaded "Folio No (Mandatory)" header row
        data = crop.crop((0, int(ch * 0.08), cw, ch))
        # Upscale 2x
        data_up = data.resize((data.width * 2, data.height * 2), Image.LANCZOS)
        # Preprocess: grayscale + contrast + sharpen
        proc = data_up.convert("L")
        proc = ImageEnhance.Contrast(proc).enhance(2.0)
        proc = proc.filter(ImageFilter.SHARPEN)
        return proc

    TITLE_WORDS = {"request", "change", "distribution", "distributor", "mfd"}

    @staticmethod
    def _parse_amc(text: str) -> str:
        """Parse AMC name from OCR text of mutual fund line."""
        for line in text.splitlines():
            line = line.strip()
            if 'mutual' in line.lower() and 'fund' in line.lower():
                if any(w in line.lower() for w in BatchWorker.TITLE_WORDS):
                    continue
                line = re.sub(r'^[_\W]+', '', line)
                m = re.match(r'^([\w\-]+(?:\s+[\w\-]+)*?)\s*[_.:]*\s*Mutual',
                             line, re.IGNORECASE)
                if m:
                    amc = re.sub(r'[_\s\.]+$', '', m.group(1)).strip()
                    if not amc or len(amc) < 2:
                        continue
                    amc = amc.upper()
                    amc = re.sub(r'l$', 'I', amc)
                    return AMC_CORRECTIONS.get(amc, amc)
        return ""

    @staticmethod
    def _find_known_amc(text: str) -> str:
        """Fallback: scan OCR text for known AMC names."""
        text_upper = text.upper()
        for amc in KNOWN_AMCS:
            pattern = r'\b' + re.escape(amc) + r'\b'
            if re.search(pattern, text_upper):
                return amc.replace(' ', '_')
        return ""

    @staticmethod
    def _get_amc(img: Image.Image) -> str:
        """Extract AMC name from page top area."""
        w, h = img.size
        all_text = ""
        for y1_pct, y2_pct in [(0.08, 0.15), (0.06, 0.16), (0.05, 0.18)]:
            crop = img.crop((0, int(h*y1_pct), int(w*0.70), int(h*y2_pct)))
            crop_up = crop.resize((crop.width*2, crop.height*2), Image.LANCZOS)
            proc = crop_up.convert("L")
            proc = ImageEnhance.Contrast(proc).enhance(2.0)
            proc = proc.filter(ImageFilter.SHARPEN)
            for psm in (4, 6, 11):
                text = BatchWorker._ocr(proc, psm)
                all_text += text
                amc = BatchWorker._parse_amc(text)
                if amc:
                    return amc
        return BatchWorker._find_known_amc(all_text)

    def _get_folios_and_amc(self, pdf_bytes: bytes, page_idx: int) -> tuple:
        """
        Extract folio numbers and AMC name from one page.
        Returns (folios_list, amc_string)
        """
        if not PDF2IMAGE_OK:
            return [], ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(pdf_bytes)
                tmp_path = tmp.name
            try:
                pages = convert_from_path(
                    tmp_path, dpi=200,
                    first_page=page_idx + 1,
                    last_page=page_idx + 1,
                    poppler_path=_poppler_path)
            finally:
                try: os.unlink(tmp_path)
                except OSError: pass

            img = pages[0]

            # Extract AMC name
            amc = self._get_amc(img)

            # Build both crops upfront
            crops = {
                0.38: self._get_crop(img, 0.38),
                0.22: self._get_crop(img, 0.22),
            }

            # Try PSM × width combinations, stop at first success
            folios   = []
            all_text = ""
            for psm in (6, 4, 11):
                for x_pct in (0.38, 0.22):
                    text  = self._ocr(crops[x_pct], psm)
                    all_text += text
                    found = self._extract_folios(text)
                    if len(found) > len(folios):
                        folios = found
                    if folios:
                        break
                if folios:
                    break

            # If AMC not found from dedicated strip, try from folio OCR text
            if not amc and all_text:
                amc = self._parse_amc(all_text)
                if not amc:
                    amc = self._find_known_amc(all_text)

            return folios, amc

        except Exception:
            return [], ""

    def _get_folios(self, pdf_bytes: bytes, page_idx: int) -> list:
        """Kept for backward compatibility — use _get_folios_and_amc instead."""
        folios, _ = self._get_folios_and_amc(pdf_bytes, page_idx)
        return folios

    # ── Step 5: Build output filename ────────────────────────────────────────
    @staticmethod
    def _build_filename(fp: Path, page_idx: int, folios: list, amc: str = "") -> str:
        """
        Build output TIFF filename from folio list.
        Single folio:    11105259-37.tiff
        Multiple folios: 26951654-67(8).tiff
        Same folio 3x:   34867057(3).tiff
        No folio found:  originalname_page1_sealed.tiff
        """
        if not folios:
            return f"{fp.stem}_page{page_idx + 1}_sealed.tiff"
        first = folios[0]
        first = re.sub(r'(?<=\d)\s+(?=\d)', '', first)
        first = re.sub(r'\s*/\s*', '-', first)
        first = re.sub(r'[^\w\-]', '', first).strip('-_')
        count = len(folios)
        folio_part = f"{first}({count})" if count > 1 else first
        if amc:
            amc_clean = re.sub(r'\s+', '_', amc)
            return f"{amc_clean}_{folio_part}.tiff"
        return f"{folio_part}.tiff"

    # ── Step 4+6: Render sealed TIFF and save ────────────────────────────────
    def _render_and_save(self, fp: Path, page_idx: int,
                         sealed_pdf: Path, folios: list, amc: str = "") -> tuple:
        """
        Render one page of sealed PDF to TIFF under 5MB.
        Saves to output_folder.
        Returns (out_path, size_mb, display_name).
        """
        out_name = self._build_filename(fp, page_idx, folios, amc)
        display  = out_name if folios else ""

        # Collision-safe naming
        out_path = self.output_folder / out_name
        counter  = 1
        base     = out_name.replace(".tiff", "")
        while out_path.exists():
            out_path = self.output_folder / f"{base}_{counter}.tiff"
            counter += 1

        # Render with DPI reduction to stay under 5MB
        dpi = RENDER_DPI_START
        buf = None
        while dpi >= RENDER_DPI_MIN:
            pages_img = convert_from_path(
                str(sealed_pdf), dpi=dpi,
                first_page=page_idx + 1, last_page=page_idx + 1,
                poppler_path=_poppler_path)
            rgb = pages_img[0].convert("RGB")
            buf = io.BytesIO()
            rgb.save(buf, format="TIFF", compression="jpeg", quality=82)
            size = buf.tell()
            if size <= MAX_TIFF_BYTES:
                buf.seek(0)
                out_path.write_bytes(buf.getvalue())
                return out_path, size / 1024 / 1024, display
            ratio   = MAX_TIFF_BYTES / size
            new_dpi = max(int(dpi * math.sqrt(ratio) * 0.88), RENDER_DPI_MIN)
            if new_dpi >= dpi:
                break
            dpi = new_dpi

        # Last resort
        size = buf.tell()
        buf.seek(0)
        out_path.write_bytes(buf.getvalue())
        return out_path, size / 1024 / 1024, display

    # ── Main run ──────────────────────────────────────────────────────────────
    def run(self):
        successes = 0
        failures  = 0

        self.batch_progress.emit(0, "Loading seal image…")
        seal_img = Image.open(self.seal_path).convert("RGBA")

        # Count total pages for progress bar
        total_pages      = 0
        file_page_counts = []
        for fp in self.input_files:
            try:
                pdf_bytes = self._file_to_pdf_bytes(fp)
                count = len(PdfReader(io.BytesIO(pdf_bytes)).pages)
            except Exception:
                count = 1
            file_page_counts.append(count)
            total_pages += count

        pages_done = 0
        n_files    = len(self.input_files)

        for file_idx, fp in enumerate(self.input_files):
            self.batch_progress.emit(
                int(100 * pages_done / max(total_pages, 1)),
                f"Processing {fp.name}  ({file_idx + 1}/{n_files})…")
            try:
                # Step 1: Convert to PDF
                pdf_bytes = self._file_to_pdf_bytes(fp)
                rdr       = PdfReader(io.BytesIO(pdf_bytes))
                n_pages   = len(rdr.pages)

                # Step 2: Extract folios for ALL pages first (fast OCR pass)
                self.batch_progress.emit(
                    int(100 * pages_done / max(total_pages, 1)),
                    f"{fp.name} — extracting folios…")
                all_folios = []
                all_amcs   = []
                for page_idx in range(n_pages):
                    try:
                        folios, amc = self._get_folios_and_amc(pdf_bytes, page_idx)
                    except Exception:
                        folios, amc = [], ""
                    all_folios.append(folios)
                    all_amcs.append(amc)

                # Step 3: Seal all pages into one sealed PDF
                with tempfile.TemporaryDirectory() as tmp_dir:
                    tmp = Path(tmp_dir)
                    writer = PdfWriter()
                    for page in rdr.pages:
                        pw = float(page.mediabox.width)
                        ph = float(page.mediabox.height)
                        overlay = PdfReader(
                            io.BytesIO(self._make_seal_overlay(pw, ph, seal_img))
                        ).pages[0]
                        page.merge_page(overlay)
                        writer.add_page(page)

                    sealed_pdf = tmp / "sealed.pdf"
                    with open(str(sealed_pdf), "wb") as fh:
                        writer.write(fh)

                    # Step 4: Render each page to TIFF using known filenames
                    for page_idx in range(n_pages):
                        self.batch_progress.emit(
                            int(100 * pages_done / max(total_pages, 1)),
                            f"{fp.name} — page {page_idx + 1}/{n_pages}…")
                        self.page_started.emit(file_idx, page_idx)
                        try:
                            _, size_mb, display = self._render_and_save(
                                fp, page_idx, sealed_pdf,
                                all_folios[page_idx], all_amcs[page_idx])
                            successes += 1
                            self.page_done.emit(
                                file_idx, page_idx, size_mb, display)
                        except Exception:
                            import traceback
                            failures += 1
                            self.page_failed.emit(
                                file_idx, page_idx, traceback.format_exc())
                        pages_done += 1

            except Exception:
                import traceback
                err = traceback.format_exc()
                for page_idx in range(file_page_counts[file_idx]):
                    self.page_started.emit(file_idx, page_idx)
                    self.page_failed.emit(file_idx, page_idx, err)
                    failures  += 1
                    pages_done += 1

        self.batch_progress.emit(100, "Batch complete.")
        self.all_done.emit(successes, failures)


# ═══════════════════════════════════════════════════════════════════════════════
#  Badge
# ═══════════════════════════════════════════════════════════════════════════════
class Badge(QLabel):
    def __init__(self, text, color):
        super().__init__(text)
        self.setStyleSheet(f"""
            QLabel {{
                background: {color}22;
                color: {color};
                border: 1px solid {color}55;
                border-radius: 10px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 600;
            }}
        """)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Window
# ═══════════════════════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DocSealer Batch")
        self.setMinimumSize(700, 820)
        self.resize(740, 880)
        self.setStyleSheet(STYLE)
        self._worker        = None
        self._output_folder = None
        self._build_ui()
        self._check_deps()

    def _check_deps(self):
        issues = []
        if not LIBS_OK:
            issues.append(f"Missing library: {LIBS_ERROR}")
        if not PDF2IMAGE_OK:
            issues.append("Missing: pdf2image  →  pip install pdf2image")
        if not HEIC_OK:
            self.heic_badge.setText("HEIC: off")
            self.heic_badge.setStyleSheet(self.heic_badge.styleSheet().replace(
                C['success'], C['warning']))
        if not TESSERACT_OK:
            self.tess_badge.setText("OCR: off")
            self.tess_badge.setStyleSheet(self.tess_badge.styleSheet().replace(
                C['success'], C['warning']))
        if issues:
            QMessageBox.warning(self, "Missing Dependencies",
                                "\n".join(issues) +
                                "\n\nInstall then restart the app.")

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        lay  = QVBoxLayout(root)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(16)

        # Header
        hdr   = QHBoxLayout()
        title = QLabel("DocSealer Batch")
        title.setStyleSheet(
            f"font-size: 24px; font-weight: 800; color: {C['text']}; letter-spacing: -0.5px;")
        sub = QLabel("One sealed TIFF per page · Named by folio number")
        sub.setStyleSheet(f"font-size: 12px; color: {C['muted']};")
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addWidget(title)
        title_col.addWidget(sub)

        self.heic_badge = Badge("HEIC: on" if HEIC_OK else "HEIC: off",
                                C['success'] if HEIC_OK else C['warning'])
        self.tess_badge = Badge("OCR: on" if TESSERACT_OK else "OCR: off",
                                C['success'] if TESSERACT_OK else C['warning'])
        size_badge = Badge("< 5 MB per file", C['accent'])

        hdr.addLayout(title_col)
        hdr.addStretch()
        hdr.addWidget(self.heic_badge)
        hdr.addWidget(self.tess_badge)
        hdr.addWidget(size_badge)
        lay.addLayout(hdr)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C['border']};")
        lay.addWidget(sep)

        # Seal row
        seal_row = QHBoxLayout()
        seal_row.setSpacing(16)
        self.seal_preview = SealPreview()
        self.seal_preview.seal_loaded.connect(self._on_seal_loaded)

        seal_col = QVBoxLayout()
        seal_col.setSpacing(5)
        seal_lbl = QLabel("Your Seal")
        seal_lbl.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {C['text']};")
        seal_hint = QLabel("Stamped on every output TIFF.\nPNG with transparency recommended.")
        seal_hint.setStyleSheet(f"font-size: 11px; color: {C['muted']};")
        self.seal_status = QLabel("⚠  No seal loaded")
        self.seal_status.setStyleSheet(f"font-size: 11px; color: {C['warning']};")
        browse_seal_btn = QPushButton("Browse seal…")
        browse_seal_btn.setFixedWidth(120)
        browse_seal_btn.clicked.connect(self._browse_seal)
        seal_col.addWidget(seal_lbl)
        seal_col.addWidget(seal_hint)
        seal_col.addWidget(self.seal_status)
        seal_col.addWidget(browse_seal_btn)
        seal_col.addStretch()

        seal_row.addWidget(self.seal_preview)
        seal_row.addLayout(seal_col)
        seal_row.addStretch()
        lay.addLayout(seal_row)

        # Drop zone
        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self._add_files)
        lay.addWidget(self.drop_zone)

        # Input file list
        list_hdr = QHBoxLayout()
        self.file_count_lbl = QLabel("Input files (0)")
        self.file_count_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {C['text']};")
        remove_btn = QPushButton("Remove selected")
        remove_btn.setFixedWidth(140)
        remove_btn.clicked.connect(self._remove_selected)
        clear_btn = QPushButton("Clear all")
        clear_btn.setFixedWidth(90)
        clear_btn.clicked.connect(self._clear_files)
        list_hdr.addWidget(self.file_count_lbl)
        list_hdr.addStretch()
        list_hdr.addWidget(remove_btn)
        list_hdr.addWidget(clear_btn)
        lay.addLayout(list_hdr)

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setFixedHeight(130)
        lay.addWidget(self.file_list)

        # Output folder
        out_row = QHBoxLayout()
        out_lbl = QLabel("Output folder:")
        out_lbl.setStyleSheet(f"font-size: 12px; color: {C['muted']};")
        self.out_folder_lbl = QLabel("Not set — files will be saved next to input")
        self.out_folder_lbl.setStyleSheet(
            f"font-size: 12px; color: {C['muted']};"
            f"background: {C['surface']}; border-radius: 6px; padding: 6px 10px;")
        self.out_folder_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        browse_out_btn = QPushButton("Browse…")
        browse_out_btn.setFixedWidth(90)
        browse_out_btn.clicked.connect(self._browse_output_folder)
        out_row.addWidget(out_lbl)
        out_row.addWidget(self.out_folder_lbl)
        out_row.addWidget(browse_out_btn)
        lay.addLayout(out_row)

        # Run button
        self.run_btn = QPushButton("▶  Start Batch")
        self.run_btn.setFixedHeight(44)
        self.run_btn.setStyleSheet(
            self.run_btn.styleSheet() +
            f"QPushButton {{ background: {C['accent']}; font-size: 15px; font-weight: 700; }}")
        self.run_btn.clicked.connect(self._run)
        lay.addWidget(self.run_btn)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        lay.addWidget(self.progress_bar)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(f"font-size: 12px; color: {C['muted']}; padding: 2px;")
        lay.addWidget(self.status_lbl)

        # Summary banner
        self.summary_frame = QFrame()
        self.summary_frame.setVisible(False)
        sf_lay = QHBoxLayout(self.summary_frame)
        sf_lay.setContentsMargins(16, 10, 16, 10)
        self.summary_lbl = QLabel("")
        sf_lay.addWidget(self.summary_lbl)
        sf_lay.addStretch()
        open_folder_btn = QPushButton("Open output folder")
        open_folder_btn.setFixedWidth(150)
        open_folder_btn.clicked.connect(self._open_folder)
        sf_lay.addWidget(open_folder_btn)
        lay.addWidget(self.summary_frame)

        # Results list
        self.results_header_lbl = QLabel("Results")
        self.results_header_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {C['text']};")
        self.results_header_lbl.setVisible(False)
        lay.addWidget(self.results_header_lbl)

        self.results_list = QListWidget()
        self.results_list.setVisible(False)
        self.results_list.itemClicked.connect(self._on_result_item_clicked)
        lay.addWidget(self.results_list)

    def _on_seal_loaded(self, path: str):
        self.seal_status.setText(f"✅  {Path(path).name}")
        self.seal_status.setStyleSheet(f"font-size: 11px; color: {C['success']};")

    def _browse_seal(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Seal Image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path:
            self.seal_preview.load_seal(path)

    def _add_files(self, paths: list):
        existing = {self.file_list.item(i).data(Qt.ItemDataRole.UserRole)
                    for i in range(self.file_list.count())}
        for p in paths:
            if p in existing or p == self.seal_preview.seal_path:
                continue
            fp   = Path(p)
            item = QListWidgetItem()
            item.setText(
                f"  {fp.name}   ({fp.suffix.upper().lstrip('.')}  ·  {fp.stat().st_size // 1024} KB)")
            item.setData(Qt.ItemDataRole.UserRole, p)
            self.file_list.addItem(item)
        self._update_file_count()

    def _remove_selected(self):
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))
        self._update_file_count()

    def _clear_files(self):
        self.file_list.clear()
        self._update_file_count()

    def _update_file_count(self):
        self.file_count_lbl.setText(f"Input files ({self.file_list.count()})")

    def _browse_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if folder:
            self._output_folder = folder
            self.out_folder_lbl.setText(folder)
            self.out_folder_lbl.setStyleSheet(
                f"font-size: 12px; color: {C['text']};"
                f"background: {C['surface']}; border-radius: 6px; padding: 6px 10px;")

    def _validate(self) -> bool:
        if self.file_list.count() == 0:
            QMessageBox.warning(self, "No Files", "Please add at least one input file.")
            return False
        if not self.seal_preview.seal_path:
            QMessageBox.warning(self, "No Seal", "Please load your seal image first.")
            return False
        if not self._output_folder:
            QMessageBox.warning(self, "No Output Folder", "Please choose an output folder.")
            return False
        return True

    def _run(self):
        if not self._validate():
            return
        files = [Path(self.file_list.item(i).data(Qt.ItemDataRole.UserRole))
                 for i in range(self.file_list.count())]

        self.run_btn.setEnabled(False)
        self.summary_frame.setVisible(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.status_lbl.setText("Starting batch…")
        self.status_lbl.setStyleSheet(
            f"font-size: 12px; color: {C['muted']}; padding: 2px;")

        self.results_list.clear()
        self._page_items   = {}
        self._file_headers = {}

        for fi, fp in enumerate(files):
            header = QListWidgetItem(f"  📄  {fp.name}")
            header.setForeground(QColor(C['text']))
            header.setFlags(header.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.results_list.addItem(header)
            self._file_headers[fi] = header

        self.results_header_lbl.setVisible(True)
        self.results_list.setVisible(True)

        self._worker = BatchWorker(
            files, self.seal_preview.seal_path, self._output_folder)
        self._worker.batch_progress.connect(self._on_batch_progress)
        self._worker.page_started.connect(self._on_page_started)
        self._worker.page_done.connect(self._on_page_done)
        self._worker.page_failed.connect(self._on_page_failed)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.start()

    def _on_batch_progress(self, pct: int, msg: str):
        self.progress_bar.setValue(pct)
        self.status_lbl.setText(msg)

    def _get_or_create_page_item(self, file_idx: int, page_idx: int) -> QListWidgetItem:
        key = (file_idx, page_idx)
        if key in self._page_items:
            return self._page_items[key]
        fp   = Path(self.file_list.item(file_idx).data(Qt.ItemDataRole.UserRole))
        item = QListWidgetItem(
            f"      ⏳  {fp.stem}_page{page_idx+1}_sealed.tiff  →  pending…")
        item.setForeground(QColor(C['muted']))
        header_row = self.results_list.row(self._file_headers[file_idx])
        self.results_list.insertItem(header_row + 1 + page_idx, item)
        self._page_items[key] = item
        return item

    def _on_page_started(self, file_idx: int, page_idx: int):
        fp   = Path(self.file_list.item(file_idx).data(Qt.ItemDataRole.UserRole))
        item = self._get_or_create_page_item(file_idx, page_idx)
        item.setText(f"      ⚙️  {fp.stem}_page{page_idx+1}  →  processing…")
        item.setForeground(QColor(C['accent']))
        self.results_list.scrollToItem(item)

    def _on_page_done(self, file_idx: int, page_idx: int,
                      size_mb: float, display: str):
        fp    = Path(self.file_list.item(file_idx).data(Qt.ItemDataRole.UserRole))
        item  = self._get_or_create_page_item(file_idx, page_idx)
        under = size_mb <= 5.0
        icon  = "✅" if under else "⚠️"
        color = C['success'] if under else C['warning']
        name  = display if display else f"{fp.stem}_page{page_idx+1}_sealed.tiff"
        item.setText(f"      {icon}  {name}  ({size_mb:.2f} MB)")
        item.setForeground(QColor(color))
        self.results_list.scrollToItem(item)

    def _on_page_failed(self, file_idx: int, page_idx: int, err: str):
        fp   = Path(self.file_list.item(file_idx).data(Qt.ItemDataRole.UserRole))
        item = self._get_or_create_page_item(file_idx, page_idx)
        item.setText(
            f"      ❌  {fp.stem}_page{page_idx+1}  →  failed (click to see error)")
        item.setForeground(QColor(C['danger']))
        item.setData(Qt.ItemDataRole.UserRole, err)
        self.results_list.scrollToItem(item)

    def _on_result_item_clicked(self, item: QListWidgetItem):
        err = item.data(Qt.ItemDataRole.UserRole)
        if err and "❌" in item.text():
            QMessageBox.critical(self, "Error Details", err)

    def _on_all_done(self, successes: int, failures: int):
        self.run_btn.setEnabled(True)
        total = successes + failures
        if failures == 0:
            color  = C['success']
            msg    = f"✅  All {successes} page(s) sealed successfully."
            border = C['success']
        elif successes == 0:
            color  = C['danger']
            msg    = f"❌  All {failures} page(s) failed."
            border = C['danger']
        else:
            color  = C['warning']
            msg    = f"⚠️  {successes} succeeded, {failures} failed."
            border = C['warning']

        self.summary_lbl.setText(msg)
        self.summary_lbl.setStyleSheet(
            f"font-size: 13px; color: {color}; font-weight: 600;")
        self.summary_frame.setStyleSheet(f"""
            QFrame {{
                background: {border}18;
                border: 1px solid {border}44;
                border-radius: 10px;
            }}""")
        self.summary_frame.setVisible(True)
        self.status_lbl.setText(
            f"Batch complete — {successes}/{total} pages processed.")

    def _open_folder(self):
        if self._output_folder:
            import subprocess
            if sys.platform == "win32":
                os.startfile(self._output_folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", self._output_folder])


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry Point
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("DocSealer Batch")
    app.setOrganizationName("DocSealer")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
