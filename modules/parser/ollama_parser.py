"""
Ollama Vision parser — sends problem files (as images) to a local Ollama
instance and parses the structured JSON response into a Problem model.

Drop-in replacement for gemini_parser.parse_problem().
Default model: llava  (or qwen2-vl, moondream2, etc.)
"""

import asyncio
import base64
import json
import logging
import re
from typing import Optional

import httpx

from modules.parser.file_loader import FileContent
from modules.parser.models import Example, Problem

logger = logging.getLogger("polygon-uploader.parser")

# Ollama default endpoint (chạy local)
OLLAMA_BASE_URL = "http://localhost:11434"

# Retry config
MAX_RETRIES = 2
RETRY_DELAYS = [5, 15]

# System prompt — giống gemini_parser
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
    """Extract JSON from model response (handles code blocks, raw JSON, embedded JSON)."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    raise ValueError("Không thể parse JSON từ response của Ollama")


async def parse_problem(
    content: FileContent,
    api_key: str = "",          # unused — kept for interface compatibility
    model: str = "llava",
    base_url: str = OLLAMA_BASE_URL,
) -> Problem:
    """
    Send file content to local Ollama Vision and parse the response.

    Args:
        content: FileContent from file_loader (images + optional text).
        api_key: Ignored — kept for drop-in compatibility with gemini_parser.
        model: Ollama model name (e.g. "llava", "qwen2-vl", "moondream").
        base_url: Ollama API base URL (default: http://localhost:11434).

    Returns:
        Parsed Problem object.
    """
    # Build prompt text — prepend extracted text if available
    prompt = PARSE_PROMPT
    if content.text:
        prompt = f"Đây là text trích xuất từ file đề:\n\n{content.text}\n\n{PARSE_PROMPT}"

    # Encode images as base64
    images_b64 = []
    for img_bytes in content.images:
        images_b64.append(base64.b64encode(img_bytes).decode("utf-8"))

    # Build Ollama /api/generate payload
    payload = {
        "model": model,
        "prompt": prompt,
        "images": images_b64,
        "stream": False,
        "options": {
            "temperature": 0.1,   # low temp for structured output
        },
    }

    raw_text = None
    last_error = None

    async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as client:
        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.info(
                    "Gọi Ollama model=%s, images=%d, has_text=%s%s",
                    model, len(images_b64), bool(content.text),
                    f" [retry {attempt}]" if attempt > 0 else "",
                )
                resp = await client.post("/api/generate", json=payload)
                resp.raise_for_status()
                data = resp.json()
                raw_text = data.get("response", "")
                logger.info("Ollama response OK — length=%d chars", len(raw_text))
                logger.debug("Ollama raw response:\n%s", raw_text[:2000])
                break

            except httpx.ConnectError:
                msg = "Không kết nối được Ollama. Chắc chắn Ollama đang chạy tại " + base_url
                logger.error(msg)
                raise ValueError(msg)

            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(
                    "Ollama HTTP error (attempt %d/%d): %s",
                    attempt + 1, MAX_RETRIES + 1, e,
                )
                if attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.info("⏳ Đợi %ds trước khi retry...", delay)
                    await asyncio.sleep(delay)
                else:
                    raise ValueError(f"Ollama HTTP error: {e}")

            except Exception as e:
                last_error = e
                logger.error("Lỗi gọi Ollama: %s", e, exc_info=True)
                raise ValueError(f"Lỗi gọi Ollama: {e}")

    if raw_text is None:
        raise ValueError(f"Ollama không trả về response: {last_error}")

    # Parse JSON response
    try:
        data = _extract_json(raw_text)
        logger.info("JSON extracted OK — keys: %s", list(data.keys()))
    except ValueError:
        logger.warning(
            "Không thể parse JSON từ Ollama response. Raw text (first 500):\n%s",
            raw_text[:500],
        )
        return Problem(title=f"[Parse failed] {content.source_name}")

    # Build Problem
    examples = [
        Example(input=ex.get("input", ""), output=ex.get("output", ""))
        for ex in data.get("examples", [])
    ]

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