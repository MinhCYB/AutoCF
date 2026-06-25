"""
GPT4Free Vision parser — parse problem files using free AI providers
via the g4f library (no API key required).

Drop-in replacement for gemini_parser.parse_problem().

Provider fallback order (vision, no auth):
  1. PollinationsAI  — openai model, wraps GPT-4o vision
  2. GeminiPro       — wraps Gemini web
  3. Copilot         — wraps Microsoft Copilot

Install: pip install g4f
"""

import asyncio
import base64
import json
import logging
import re
from typing import Optional

from modules.parser.file_loader import FileContent
from modules.parser.models import Example, Problem

logger = logging.getLogger("polygon-uploader.parser")

# Provider fallback list — tried in order until one succeeds
# Each entry: (provider_name, model_name)
PROVIDER_FALLBACKS = [
    ("PollinationsAI", "openai"),
    # GeminiPro dùng Gemini API key từ env — bỏ để tránh quota conflict
    # ("GeminiPro",      "gemini-2.5-flash"),
    ("Copilot",        "Copilot"),
]

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

    raise ValueError("Không thể parse JSON từ response")


def _build_messages(content: FileContent) -> list:
    """Build g4f messages with image attachments."""
    try:
        from g4f.client import Image as G4FImage
        has_image_class = True
    except ImportError:
        has_image_class = False

    prompt_text = PARSE_PROMPT
    if content.text:
        prompt_text = f"Đây là text trích xuất từ file đề:\n\n{content.text}\n\n{PARSE_PROMPT}"

    if not content.images:
        return [{"role": "user", "content": prompt_text}]

    # g4f accepts images as list of dicts with base64 data URIs
    message_content = []

    for img_bytes in content.images:
        mime = "image/jpeg" if img_bytes[:2] == b"\xff\xd8" else "image/png"
        b64 = base64.b64encode(img_bytes).decode()
        data_uri = f"data:{mime};base64,{b64}"
        message_content.append({
            "type": "image_url",
            "image_url": {"url": data_uri},
        })

    message_content.append({"type": "text", "text": prompt_text})

    return [{"role": "user", "content": message_content}]


async def _try_provider(provider_name: str, model: str, messages: list) -> str:
    """Try a single g4f provider, return raw text or raise."""
    import g4f
    from g4f.client import AsyncClient

    provider_cls = getattr(g4f.Provider, provider_name, None)
    if provider_cls is None:
        raise ValueError(f"Provider '{provider_name}' không tồn tại trong g4f")

    logger.info("Thử provider=%s model=%s...", provider_name, model)

    client = AsyncClient()
    response = await client.chat.completions.create(
        model=model,
        provider=provider_cls,
        messages=messages,
        timeout=60,
    )
    text = response.choices[0].message.content
    if not text or not text.strip():
        raise ValueError(f"Provider {provider_name} trả về response rỗng")
    return text


async def parse_problem(
    content: FileContent,
    api_key: str = "",      # unused — kept for interface compatibility
    model: str = "",        # unused — provider handles model selection
    providers: Optional[list] = None,
    retry_delay: float = 15.0,
) -> Problem:
    """
    Parse problem using g4f free providers with automatic fallback.

    Args:
        content: FileContent from file_loader.
        api_key: Ignored.
        model: Ignored — each provider uses its own model.
        providers: Override provider list [(name, model), ...].

    Returns:
        Parsed Problem object.
    """
    provider_list = providers or PROVIDER_FALLBACKS
    messages = _build_messages(content)

    last_error = None
    for provider_name, provider_model in provider_list:
        try:
            raw_text = await _try_provider(provider_name, provider_model, messages)
            logger.info("✅ Provider %s OK — %d chars", provider_name, len(raw_text))
            logger.debug("Raw response:\n%s", raw_text[:2000])
            break
        except Exception as e:
            logger.warning("❌ Provider %s thất bại: %s", provider_name, e)
            last_error = e
            logger.info("⏳ Đợi %.0fs trước khi thử provider tiếp theo...", retry_delay)
            await asyncio.sleep(retry_delay)
            continue
    else:
        raise ValueError(f"Tất cả provider đều thất bại. Lỗi cuối: {last_error}")

    # Parse JSON
    try:
        data = _extract_json(raw_text)
        logger.info("JSON extracted OK — keys: %s", list(data.keys()))
    except ValueError:
        logger.warning("Không parse được JSON. Raw (500 chars):\n%s", raw_text[:500])
        return Problem(title=f"[Parse failed] {content.source_name}")

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