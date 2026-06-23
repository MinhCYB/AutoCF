"""
File loader — converts problem files (PDF, DOCX, images) into content
that can be sent directly to Gemini Vision API.

Strategy (per user request):
  - All file types are converted to images and sent to Gemini
  - PDF  → render each page to PNG via PyMuPDF
  - Image → pass through as-is
  - DOCX → extract text + embedded images; render text to image as fallback
"""

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("polygon-uploader.loader")

# Max image width for Gemini (pixels) — larger images waste tokens
MAX_IMAGE_WIDTH = 1200
JPEG_QUALITY = 80  # Compression quality (0-100)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


@dataclass
class FileContent:
    """Content extracted from a problem file, ready for Gemini."""
    images: list[bytes] = field(default_factory=list)    # PNG image bytes
    text: str = ""                                        # Extracted text (DOCX fallback)
    source_name: str = ""


def load_file(path: Path) -> FileContent:
    """
    Load a problem file and convert to Gemini-compatible content.

    Args:
        path: Path to the problem file.

    Returns:
        FileContent with images and/or text for Gemini.

    Raises:
        ValueError: If the file type is not supported.
    """
    if not path.exists():
        raise ValueError(f"File không tồn tại: {path}")

    ext = path.suffix.lower()

    if ext == ".pdf":
        return _load_pdf(path)
    elif ext in IMAGE_EXTENSIONS:
        return _load_image(path)
    elif ext in {".docx", ".doc"}:
        return _load_docx(path)
    else:
        raise ValueError(f"Định dạng file không được hỗ trợ: {ext}")


def _load_pdf(path: Path) -> FileContent:
    """Render each PDF page to a compressed JPEG at 150 DPI."""
    images = []
    doc = fitz.open(str(path))
    try:
        for page_num, page in enumerate(doc):
            pix = page.get_pixmap(dpi=150)
            raw_png = pix.tobytes("png")
            compressed = _compress_image(raw_png)
            logger.info(
                "PDF page %d: %d KB → %d KB (compressed)",
                page_num + 1,
                len(raw_png) // 1024,
                len(compressed) // 1024,
            )
            images.append(compressed)
    finally:
        doc.close()

    return FileContent(images=images, source_name=path.name)


def _load_image(path: Path) -> FileContent:
    """Read image bytes, compress and resize for Gemini."""
    img_bytes = path.read_bytes()
    compressed = _compress_image(img_bytes)
    logger.info(
        "Image %s: %d KB → %d KB",
        path.name,
        len(img_bytes) // 1024,
        len(compressed) // 1024,
    )
    return FileContent(images=[compressed], source_name=path.name)


def _load_docx(path: Path) -> FileContent:
    """
    Extract content from DOCX files.

    Primary: extract embedded images.
    Fallback: extract text and render to an image so Gemini can read it.
    """
    try:
        from docx import Document
    except ImportError:
        raise ValueError(
            "python-docx chưa được cài đặt. "
            "Chạy: pip install python-docx"
        )

    doc = Document(str(path))
    images = []

    # Extract embedded images
    for rel in doc.part.rels.values():
        if "image" in rel.reltype:
            try:
                blob = rel.target_part.blob
                images.append(blob)
            except Exception:
                continue

    # Extract text from paragraphs
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    text = "\n".join(paragraphs)

    # If no images were found, render text to a PNG
    if not images and text:
        images.append(_text_to_image(text))

    return FileContent(images=images, text=text, source_name=path.name)


def _text_to_image(text: str, width: int = 1200, font_size: int = 18) -> bytes:
    """Render plain text onto a white PNG image."""
    lines = text.split("\n")
    line_height = font_size + 8
    height = max(200, len(lines) * line_height + 80)

    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()

    y = 30
    for line in lines:
        draw.text((30, y), line, fill="black", font=font)
        y += line_height

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _compress_image(img_bytes: bytes) -> bytes:
    """
    Compress and resize an image to reduce Gemini token usage.

    - Resize to max MAX_IMAGE_WIDTH pixels wide (keep aspect ratio)
    - Convert to JPEG with quality JPEG_QUALITY
    """
    try:
        img = Image.open(io.BytesIO(img_bytes))

        # Convert RGBA → RGB (JPEG doesn't support alpha)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # Resize if too wide
        if img.width > MAX_IMAGE_WIDTH:
            ratio = MAX_IMAGE_WIDTH / img.width
            new_size = (MAX_IMAGE_WIDTH, int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return buf.getvalue()

    except Exception:
        # If compression fails, return original bytes
        return img_bytes
