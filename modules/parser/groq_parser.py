"""
Groq Vision Parser — parse problem files using Groq API (vision).
Drop-in replacement for g4f_parser.parse_problem().

Model: meta-llama/llama-4-scout-17b-16e-instruct (vision, free tier)
"""

import asyncio
import base64
import json
import logging
import re
import time
from typing import Optional

from openai import AsyncOpenAI

from modules.parser.file_loader import FileContent
from modules.parser.models import Example, Problem, Subtask

logger = logging.getLogger("polygon-uploader.parser")

VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
MIN_CALL_INTERVAL = 12.0
_last_call_time: float = 0.0

PARSE_PROMPT = """Bạn là trợ lý phân tích đề bài lập trình thi đấu.
Hãy đọc đề bài trong ảnh/file và trả về JSON với cấu trúc sau.
CHỈ trả về JSON thuần túy, không có markdown, không có giải thích.

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
  ],
  "subtasks": [
    { "index": 1, "score": 20, "constraints": "1 ≤ n ≤ 100", "n_tests": 5 }
  ]
}

Lưu ý:
- Công thức toán học viết dạng LaTeX ($..$ hoặc $$..$$)
- Đề có thể bằng tiếng Việt, giữ nguyên ngôn ngữ
- Nếu không tìm thấy time/memory limit, dùng giá trị mặc định (1000ms, 256MB)
- subtasks: nếu đề có subtask/scoring rõ ràng thì điền đầy đủ; nếu KHÔNG có thì để []
- n_tests trong subtask luôn là 5
- Dòng đầu tiên response PHẢI là dấu {"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    # Strip think tags
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
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
    prompt_text = PARSE_PROMPT
    if content.text:
        prompt_text = f"Đây là text trích xuất từ file đề:\n\n{content.text}\n\n{PARSE_PROMPT}"

    if not content.images:
        return [{"role": "user", "content": prompt_text}]

    message_content = []
    for img_bytes in content.images:
        # Detect mime type
        mime = "image/jpeg" if img_bytes[:2] == b"\xff\xd8" else "image/png"
        b64 = base64.b64encode(img_bytes).decode()
        message_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })
    message_content.append({"type": "text", "text": prompt_text})

    return [{"role": "user", "content": message_content}]


async def parse_problem(
    content: FileContent,
    api_key: str = "",
    model: str = "",
    **kwargs,
) -> Problem:
    global _last_call_time

    if not api_key:
        raise ValueError("Groq API key chưa được cấu hình")

    # Rate limiting
    elapsed = time.monotonic() - _last_call_time
    if elapsed < MIN_CALL_INTERVAL:
        await asyncio.sleep(MIN_CALL_INTERVAL - elapsed)

    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1",
    )

    messages = _build_messages(content)
    vision_model = model or VISION_MODEL

    logger.info("Groq vision parse — model=%s, images=%d", vision_model, len(content.images))
    _last_call_time = time.monotonic()

    try:
        resp = await client.chat.completions.create(
            model=vision_model,
            messages=messages,
            timeout=120,
            temperature=0,
        )
        raw_text = resp.choices[0].message.content or ""
    except Exception as e:
        raise ValueError(f"Groq API lỗi: {e}")

    logger.info("Groq vision OK — %d chars", len(raw_text))
    logger.info("Raw response (500 chars):\n%s", raw_text[:500])

    try:
        data = _extract_json(raw_text)
        logger.info("JSON extracted OK — keys: %s", list(data.keys()))
    except ValueError:
        logger.warning("Không parse được JSON. Raw:\n%s", raw_text[:500])
        return Problem(title=f"[Parse failed] {content.source_name}")

    examples = [
        Example(input=ex.get("input", ""), output=ex.get("output", ""))
        for ex in data.get("examples", [])
    ]

    time_limit = data.get("time_limit", 1000)
    time_limit = max(250, min(15000, time_limit))
    time_limit = round(time_limit / 50) * 50

    memory_limit = data.get("memory_limit", 256)
    memory_limit = max(4, min(1024, memory_limit))

    subtasks = []
    for i, st in enumerate(data.get("subtasks", []), 1):
        try:
            subtasks.append(Subtask(
                index=int(st.get("index", i)),
                score=int(st.get("score", 0)),
                constraints=str(st.get("constraints", "")),
                n_tests=int(st.get("n_tests", 5)),
            ))
        except Exception:
            pass

    return Problem(
        title=data.get("title", ""),
        time_limit=time_limit,
        memory_limit=memory_limit,
        statement=data.get("statement", ""),
        input_format=data.get("input_format", ""),
        output_format=data.get("output_format", ""),
        notes=data.get("notes", ""),
        examples=examples,
        subtasks=subtasks if subtasks else [],
    )