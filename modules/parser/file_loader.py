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
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont

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
    """Render each PDF page to a PNG image at 200 DPI."""
    images = []
    doc = fitz.open(str(path))
    try:
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            images.append(pix.tobytes("png"))
    finally:
        doc.close()

    return FileContent(images=images, source_name=path.name)


def _load_image(path: Path) -> FileContent:
    """Read raw image bytes, converting to PNG if needed."""
    img_bytes = path.read_bytes()

    # Ensure it's valid and convert to PNG for consistency
    try:
        img = Image.open(io.BytesIO(img_bytes))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()
    except Exception:
        # If Pillow can't process it, send raw bytes
        png_bytes = img_bytes

    return FileContent(images=[png_bytes], source_name=path.name)


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
