"""
Gemini Vision API parser — sends problem files (as images/text)
to Gemini and parses the structured JSON response into a Problem model.
"""

import json
import re
from typing import Optional

from google import genai
from google.genai import types

from modules.parser.file_loader import FileContent
from modules.parser.models import Example, Problem

# System prompt for Gemini (from design doc)
PARSE_PROMPT = """Bạn là trợ lý phân tích đề bài lập trình thi đấu.
Hãy đọc đề bài trong ảnh/file và trả về JSON với cấu trúc sau.
CHỈ trả về JSON, không giải thích thêm.

{
  "title": "tên bài (string)",
  "time_limit": 1000,
  "memory_limit": 256,
  "statement": "...",
  "input_format": "...",
  "output_format": "...",
  "notes": "...",
  "examples": [
    { "input": "...", "output": "..." }
  ]
}

Lưu ý:
- Công thức toán học viết dạng LaTeX ($..$ hoặc $$..$$)
- Đề có thể bằng tiếng Việt, giữ nguyên ngôn ngữ
- Nếu không tìm thấy time/memory limit, dùng giá trị mặc định (1000ms, 256MB)
- statement chứa nội dung chính của đề bài
- input_format mô tả định dạng input
- output_format mô tả định dạng output
- notes chứa ghi chú thêm (nếu có), để "" nếu không có
- examples là danh sách các ví dụ input/output"""


def _extract_json(text: str) -> dict:
    """
    Extract JSON from Gemini response text.

    Handles:
      - Raw JSON
      - JSON wrapped in ```json ... ``` code blocks
      - JSON embedded in surrounding text
    """
    # Try direct parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from code block
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try finding JSON object boundaries
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    raise ValueError("Không thể parse JSON từ response của Gemini")


async def parse_problem(
    content: FileContent,
    api_key: str,
    model: str = "gemini-2.0-flash",
) -> Problem:
    """
    Send file content to Gemini Vision and parse the response.

    Args:
        content: FileContent from file_loader (images + optional text).
        api_key: Gemini API key.
        model: Gemini model name.

    Returns:
        Parsed Problem object.

    Raises:
        ValueError: If Gemini returns unparseable content.
    """
    client = genai.Client(api_key=api_key)

    # Build multimodal prompt parts
    parts: list = []

    # Add extracted text context (if any, e.g. from DOCX)
    if content.text:
        parts.append(
            f"Đây là text trích xuất từ file đề:\n\n{content.text}\n\n"
        )

    # Add images
    for img_bytes in content.images:
        # Detect mime type from PNG header
        mime = "image/png"
        if img_bytes[:2] == b"\xff\xd8":
            mime = "image/jpeg"
        parts.append(
            types.Part.from_bytes(data=img_bytes, mime_type=mime)
        )

    # Add instruction prompt last
    parts.append(PARSE_PROMPT)

    try:
        response = await client.aio.models.generate_content(
            model=model,
            contents=parts,
        )
        raw_text = response.text
    except Exception as e:
        raise ValueError(f"Lỗi gọi Gemini API: {e}")

    # Parse JSON response
    try:
        data = _extract_json(raw_text)
    except ValueError:
        # Fallback: return empty problem for user to fill manually
        return Problem(title=f"[Parse failed] {content.source_name}")

    # Build Problem from parsed data
    examples = [
        Example(input=ex.get("input", ""), output=ex.get("output", ""))
        for ex in data.get("examples", [])
    ]

    # Clamp time_limit to valid range and ensure divisible by 50
    time_limit = data.get("time_limit", 1000)
    time_limit = max(250, min(15000, time_limit))
    time_limit = round(time_limit / 50) * 50
    if time_limit < 250:
        time_limit = 250

    memory_limit = data.get("memory_limit", 256)
    memory_limit = max(4, min(1024, memory_limit))

    return Problem(
        title=data.get("title", ""),
        time_limit=time_limit,
        memory_limit=memory_limit,
        statement=data.get("statement", ""),
        input_format=data.get("input_format", ""),
        output_format=data.get("output_format", ""),
        notes=data.get("notes", ""),
        examples=examples,
    )
