"""Turn any file into text an LLM can use.

Built on mature open-source parsers:
  - MarkItDown (Microsoft, MIT)      PDF · Word · PowerPoint · Excel · HTML · EPUB ·
                                     Outlook .msg · CSV/JSON/XML · notebooks …
  - RapidOCR (Apache-2.0, ONNX)      text in images and scanned PDF pages
  - pypdfium2                        renders scanned PDF pages for OCR
  - charset-normalizer               plain text / code in any encoding
  - Pillow                           image metadata

Every parser is optional: a missing library just means a thinner description,
never a crash. ``parse(path)`` always returns a dict:
    {"kind", "text", "summary", "meta"}
where ``text`` is Markdown-ish and ``summary`` a one-line human description.
"""

from __future__ import annotations

import logging
import mimetypes
import tarfile
import zipfile
from pathlib import Path

log = logging.getLogger("opendot.fileparse")

MAX_TEXT = 2_000_000          # chars kept from any single file
OCR_MAX_PDF_PAGES = 30        # scanned PDFs: OCR at most this many pages
OCR_MAX_PIXELS = 2400         # downscale long edge before OCR

TEXT_EXT = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".ini", ".cfg", ".conf", ".toml", ".yaml",
    ".yml", ".env", ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".kt",
    ".swift", ".go", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".rb", ".php", ".pl",
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".sql", ".r", ".m", ".scala", ".lua",
    ".dart", ".vue", ".svelte", ".css", ".scss", ".less", ".tex", ".bib", ".srt", ".vtt",
    ".diff", ".patch", ".gitignore", ".dockerfile", ".makefile", ".proto", ".graphql",
    ".tsv", ".jsonl", ".ndjson",
}
DOC_EXT = {
    ".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".xls", ".csv", ".json", ".xml", ".html",
    ".htm", ".epub", ".msg", ".ipynb", ".rtf", ".odt",
}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".heic"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
ARCHIVE_EXT = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}


def kind_of(name: str, mime: str = "") -> str:
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXT or mime.startswith("image/"):
        return "image"
    if ext in {".pdf", ".docx", ".doc", ".odt", ".rtf", ".epub", ".md", ".txt", ".msg"}:
        return "document"
    if ext in {".xlsx", ".xls", ".csv", ".tsv"}:
        return "spreadsheet"
    if ext in {".pptx", ".ppt", ".key"}:
        return "slides"
    if ext in AUDIO_EXT or mime.startswith("audio/"):
        return "audio"
    if ext in VIDEO_EXT or mime.startswith("video/"):
        return "video"
    if ext in ARCHIVE_EXT:
        return "archive"
    if ext in TEXT_EXT or mime.startswith("text/"):
        return "code" if ext not in {".txt", ".md", ".log"} else "document"
    return "file"


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def _clip(s: str) -> str:
    return s if len(s) <= MAX_TEXT else s[:MAX_TEXT] + f"\n\n…[truncated, {len(s) - MAX_TEXT} more chars]"


# ---------------------------------------------------------------- plain text
def looks_binary(path: Path, probe: int = 8192) -> bool:
    with open(path, "rb") as f:
        chunk = f.read(probe)
    if not chunk:
        return False
    if b"\x00" in chunk:
        return True
    try:
        from charset_normalizer import from_bytes
        return from_bytes(chunk).best() is None
    except Exception:
        return False


def _cjk_ratio(s: str) -> float:
    wide = [c for c in s if ord(c) > 127]
    return sum("\u4e00" <= c <= "\u9fff" or "\u3000" <= c <= "\u303f" or "\uff00" <= c <= "\uffef"
               for c in wide) / len(wide) if wide else 0.0


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:  # Chinese GBK/GB18030 files are common and short ones fool generic detectors
        s = raw.decode("gb18030")
        if _cjk_ratio(s) > 0.6:
            return s
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(raw).best()
        if best is not None:
            return str(best)
    except Exception:
        pass
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- OCR
_ocr_engine = None


def _ocr():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    return _ocr_engine


def ocr_image(img) -> str:
    """OCR a PIL image; returns reading-order text ('' when nothing found)."""
    import numpy as np
    img = img.convert("RGB")
    if max(img.size) > OCR_MAX_PIXELS:
        img.thumbnail((OCR_MAX_PIXELS, OCR_MAX_PIXELS))
    result, _ = _ocr()(np.array(img))
    if not result:
        return ""
    # group boxes into lines by their vertical centre
    boxes = sorted(((b[0][0][1] + b[0][2][1]) / 2, b[0][0][0], b[1]) for b in result)
    lines: list[list] = []
    for y, x, t in boxes:
        if lines and abs(lines[-1][0] - y) < 12:
            lines[-1][1].append((x, t))
        else:
            lines.append([y, [(x, t)]])
    return "\n".join(" ".join(t for _, t in sorted(items)) for _, items in lines)


# ---------------------------------------------------------------- per-kind parsers
def _image(path: Path) -> dict:
    meta: dict = {}
    text = ""
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            meta = {"width": im.width, "height": im.height, "format": im.format}
            im = ImageOps.exif_transpose(im)
            try:
                import zxingcpp
                meta["codes"] = [r.text for r in zxingcpp.read_barcodes(im) if r.text][:5]
            except Exception as e:
                log.info("QR/barcode reading unavailable for %s: %s", path.name, e)
            try:
                text = ocr_image(im)
            except Exception as e:
                log.info("OCR unavailable for %s: %s", path.name, e)
    except Exception as e:
        log.info("image open failed for %s: %s", path.name, e)
    dims = f"{meta['width']}×{meta['height']} " if meta.get("width") else ""
    body = (f"Image {dims}{meta.get('format') or ''}".strip() + ".\n\n")
    if meta.get("codes"):
        body += "QR/barcodes in the image: " + " · ".join(meta["codes"]) + "\n(A QR code "
        body += "for a place or service can become its own agent — `create_agent`.)\n\n"
    body += ("Text found in the image (OCR):\n\n" + text) if text.strip() else \
        "(No readable text found in the image.)"
    return {"text": body, "summary": f"image {dims}".strip() +
            (f" · {len(text)} chars of text" if text else ""), "meta": meta}


def _pdf_ocr(path: Path, page_count_hint: int = 0) -> str:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(path))
    out = []
    n = min(len(pdf), OCR_MAX_PDF_PAGES)
    for i in range(n):
        img = pdf[i].render(scale=2).to_pil()
        t = ocr_image(img)
        if t.strip():
            out.append(f"## Page {i + 1}\n\n{t}")
    if len(pdf) > n:
        out.append(f"…(OCR stopped after {n} of {len(pdf)} pages)")
    return "\n\n".join(out)


def _pdf_pages(path: Path) -> int:
    try:
        import pypdfium2 as pdfium
        return len(pdfium.PdfDocument(str(path)))
    except Exception:
        return 0


def _markitdown(path: Path) -> str:
    from markitdown import MarkItDown
    return MarkItDown(enable_plugins=False).convert(str(path)).text_content or ""


def _archive(path: Path) -> dict:
    names: list[str] = []
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                infos = z.infolist()
                names = [f"{i.filename}  ({_fmt_size(i.file_size)})" for i in infos[:500]]
                total = len(infos)
        elif tarfile.is_tarfile(path):
            with tarfile.open(path) as t:
                members = t.getmembers()
                names = [f"{m.name}  ({_fmt_size(m.size)})" for m in members[:500]]
                total = len(members)
        else:
            total = 0
    except Exception as e:
        return {"text": f"Archive (could not list contents: {e}).", "summary": "archive",
                "meta": {}}
    listing = "\n".join(f"- {n}" for n in names) or "(unknown format — try `7z l` or `unrar l`)"
    more = f"\n…and {total - len(names)} more" if total > len(names) else ""
    return {"text": f"Archive with {total} entries (not extracted — unpack it in your "
                    f"terminal if you need the contents):\n\n{listing}{more}",
            "summary": f"archive · {total} entries", "meta": {"entries": total}}


def _media(path: Path, kind: str) -> dict:
    return {"text": f"{kind.title()} file. No transcript was made automatically; if you need "
                    f"one, use your terminal (e.g. ffmpeg / whisper if installed).",
            "summary": kind, "meta": {}}


# ---------------------------------------------------------------- entry point
def parse(path: Path, name: str | None = None) -> dict:
    name = name or path.name
    ext = Path(name).suffix.lower()
    mime = mimetypes.guess_type(name)[0] or ""
    kind = kind_of(name, mime)
    size = path.stat().st_size
    res: dict = {"text": "", "summary": "", "meta": {}}
    try:
        if kind == "image":
            res = _image(path)
        elif kind == "archive":
            res = _archive(path)
        elif kind in ("audio", "video"):
            res = _media(path, kind)
        elif ext == ".pdf":
            pages = _pdf_pages(path)
            text = ""
            try:
                text = _markitdown(path)
            except Exception as e:
                log.info("markitdown pdf failed for %s: %s", name, e)
            ocr_used = False
            # scanned PDF: almost no text layer → OCR the pages
            if len(text.strip()) < 40 * max(pages, 1):
                try:
                    o = _pdf_ocr(path)
                    if len(o.strip()) > len(text.strip()):
                        text, ocr_used = o, True
                except Exception as e:
                    log.info("pdf OCR failed for %s: %s", name, e)
            res = {"text": text, "summary": f"PDF · {pages} pages" + (" · OCR" if ocr_used else ""),
                   "meta": {"pages": pages, "ocr": ocr_used}}
        elif ext in DOC_EXT:
            res = {"text": _markitdown(path), "summary": "", "meta": {}}
        elif ext in TEXT_EXT or mime.startswith("text/") or not looks_binary(path):
            text = read_text(path)
            res = {"text": text, "summary": f"{text.count(chr(10)) + 1} lines", "meta": {}}
            if ext not in {".txt", ".md", ".markdown", ""}:
                lang = ext.lstrip(".")
                res["text"] = f"```{lang}\n{text}\n```"
        else:
            # last resort: let MarkItDown sniff it (it uses magika for type detection)
            try:
                res = {"text": _markitdown(path), "summary": "", "meta": {}}
            except Exception:
                res = {"text": f"Binary file ({mime or ext or 'unknown type'}). It is on your "
                               "computer — inspect it with the terminal (`file`, `xxd`, …).",
                       "summary": "binary", "meta": {}}
    except Exception as e:
        log.warning("parse failed for %s: %s", name, e)
        res = {"text": f"(Could not extract text automatically: {type(e).__name__}: {e}. The "
                       "original file is on your computer — try the terminal.)",
               "summary": "unparsed", "meta": {"error": str(e)}}
    text = _clip(res.get("text") or "")
    summary = res.get("summary") or (f"{len(text):,} chars" if text else "")
    return {"kind": kind, "mime": mime, "size": size, "text": text,
            "summary": " · ".join(x for x in (_fmt_size(size), summary) if x),
            "meta": res.get("meta") or {}}
