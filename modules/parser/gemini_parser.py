"""
Gemini Vision API parser — sends problem files (as images/text)
to Gemini and parses the structured JSON response into a Problem model.
"""

import asyncio
import json
import logging
import re
from typing import Optional

from google import genai
from google.genai import types

from modules.parser.file_loader import FileContent
from modules.parser.models import Example, Problem

logger = logging.getLogger("polygon-uploader.parser")

# Retry config for rate-limited API calls
MAX_RETRIES = 3
RETRY_DELAYS = [15, 45, 90]  # seconds — escalating backoff (free tier cần delay dài hơn)

# Per-call throttle — enforce minimum gap between Gemini requests regardless of caller
# Gemini free tier: 15 RPM → safe floor is ~10s between calls
MIN_CALL_INTERVAL = 10.0  # seconds
_last_call_time: float = 0.0

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


def _format_api_error(error: Exception) -> str:
    """Extract a short, human-readable error message from Gemini API errors."""
    err_str = str(error)

    # Rate limit
    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
        return "Rate limit — hết quota Gemini API. Đợi 1 phút rồi thử lại."

    # Auth errors
    if "401" in err_str or "UNAUTHENTICATED" in err_str:
        return "API key không hợp lệ. Kiểm tra lại Gemini API key."

    if "403" in err_str or "PERMISSION_DENIED" in err_str:
        return "Không có quyền truy cập API. Kiểm tra key hoặc bật Gemini API."

    # Content safety
    if "SAFETY" in err_str or "blocked" in err_str.lower():
        return "Nội dung bị chặn bởi bộ lọc an toàn của Gemini."

    # Generic — truncate to readable length
    if len(err_str) > 200:
        return err_str[:200] + "..."

    return err_str


def _is_retryable(error: Exception) -> bool:
    """Check if an error is a rate limit that can be retried."""
    err_str = str(error)
    return "429" in err_str or "RESOURCE_EXHAUSTED" in err_str


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

    Includes automatic retry with exponential backoff for rate limit errors (429).

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

    # Per-call throttle — enforce minimum gap between Gemini requests
    global _last_call_time
    import time as _time_module
    elapsed = _time_module.monotonic() - _last_call_time
    if elapsed < MIN_CALL_INTERVAL:
        wait = MIN_CALL_INTERVAL - elapsed
        logger.info("⏳ Throttle: đợi %.1fs trước khi gọi Gemini...", wait)
        await asyncio.sleep(wait)
    _last_call_time = _time_module.monotonic()

    # Call Gemini with retry for rate limits
    raw_text = None
    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            logger.info(
                "Gọi Gemini model=%s, parts=%d (images=%d, has_text=%s)%s",
                model, len(parts), len(content.images), bool(content.text),
                f" [retry {attempt}]" if attempt > 0 else "",
            )
            response = await client.aio.models.generate_content(
                model=model,
                contents=parts,
            )
            raw_text = response.text
            logger.info("Gemini response OK — length=%d chars", len(raw_text))
            logger.debug("Gemini raw response:\n%s", raw_text[:2000])
            break  # Success — exit retry loop

        except Exception as e:
            last_error = e
            short_err = _format_api_error(e)
            logger.warning("Gemini API error (attempt %d/%d): %s",
                           attempt + 1, MAX_RETRIES + 1, short_err)

            # Retry only for rate limits, and only if we have retries left
            if _is_retryable(e) and attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                logger.info("⏳ Rate limited — đợi %ds trước khi retry...", delay)
                await asyncio.sleep(delay)
                continue

            # Non-retryable or out of retries
            logger.error("Lỗi gọi Gemini API: %s", short_err, exc_info=True)
            raise ValueError(f"Lỗi gọi Gemini API: {short_err}")

    if raw_text is None:
        raise ValueError(f"Lỗi gọi Gemini API: {_format_api_error(last_error)}")

    # Parse JSON response
    try:
        data = _extract_json(raw_text)
        logger.info("JSON extracted OK — keys: %s", list(data.keys()))
    except ValueError:
        logger.warning("Không thể parse JSON từ Gemini response. Raw text (first 500):\n%s", raw_text[:500])
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