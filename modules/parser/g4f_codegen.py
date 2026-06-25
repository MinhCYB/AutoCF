"""
G4F Code Generator — generates C++ solution and test generator
from problem statement using free AI providers via g4f.

Dùng DeepSeek (v3 / r1) qua các free providers, không cần API key.

Provider fallback order (text-only, no auth):
  1. PhindAi       — deepseek-v3
  2. WeWordle       — deepseek-v3
  3. PollinationsAI — deepseek-v3
  4. AnyProvider    — deepseek-r1 (last resort)
"""

import asyncio
import logging
import time
from typing import Optional

from modules.parser.models import Problem

logger = logging.getLogger("polygon-uploader.parser")

MAX_RETRIES = 3
RETRY_DELAY = 10.0   # giây giữa các lần retry trong cùng provider
MIN_CALL_INTERVAL = 3.0

_last_call_time: float = 0.0

# (provider_name, model) — thử theo thứ tự
PROVIDER_FALLBACKS = [
    ("PhindAi",        "deepseek-v3"),
    ("WeWordle",       "deepseek-v3"),
    ("PollinationsAI", "deepseek-v3"),
    ("AnyProvider",    "deepseek-r1"),
]

SOLUTION_PROMPT = """Bạn là chuyên gia lập trình thi đấu. Dưới đây là đề bài:

--- ĐỀ BÀI ---
Tên: {title}
Statement: {statement}
Input: {input_format}
Output: {output_format}
Giới hạn: {time_limit}ms / {memory_limit}MB
Examples:
{examples}
--- HẾT ĐỀ ---

Hãy viết solution C++ hoàn chỉnh, đúng, hiệu quả cho bài này.
CHỈ trả về code C++ thuần túy, không có markdown, không có giải thích.
Bắt đầu bằng #include."""

GENERATOR_PROMPT = """Bạn là chuyên gia lập trình thi đấu. Dưới đây là đề bài:

--- ĐỀ BÀI ---
Tên: {title}
Statement: {statement}
Input: {input_format}
Output: {output_format}
Giới hạn: {time_limit}ms / {memory_limit}MB
--- HẾT ĐỀ ---

Hãy viết test generator C++ dùng testlib.h để sinh test ngẫu nhiên cho bài này.
Generator nhận seed từ argv[1] (dùng registerGen(argc, argv, 1)).
Sinh các test nhỏ phù hợp để test solution.
CHỈ trả về code C++ thuần túy, không có markdown, không có giải thích.
Bắt đầu bằng #include."""


def _build_prompt(template: str, problem: Problem) -> str:
    examples_str = ""
    for i, ex in enumerate(problem.examples, 1):
        examples_str += f"Input {i}:\n{ex.input}\nOutput {i}:\n{ex.output}\n"
    return template.format(
        title=problem.title,
        statement=problem.statement,
        input_format=problem.input_format,
        output_format=problem.output_format,
        time_limit=problem.time_limit,
        memory_limit=problem.memory_limit,
        examples=examples_str or "(không có example)",
    )


def _strip_markdown(code: str) -> str:
    """Remove markdown code fences if present."""
    code = code.strip()
    if code.startswith("```"):
        lines = code.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code = "\n".join(lines)
    return code.strip()


async def _try_provider(provider_name: str, model: str, prompt: str) -> str:
    """Try a single g4f provider, return raw text or raise."""
    import g4f
    from g4f.client import AsyncClient

    provider_cls = getattr(g4f.Provider, provider_name, None)
    if provider_cls is None:
        raise ValueError(f"Provider '{provider_name}' không tồn tại trong g4f")

    logger.info("Codegen thử provider=%s model=%s...", provider_name, model)

    client = AsyncClient()
    response = await client.chat.completions.create(
        model=model,
        provider=provider_cls,
        messages=[{"role": "user", "content": prompt}],
        timeout=120,
    )
    text = response.choices[0].message.content
    if not text or not text.strip():
        raise ValueError(f"Provider {provider_name} trả về response rỗng")
    return text


async def _call_g4f(prompt: str, label: str) -> str:
    global _last_call_time

    # Throttle giữa các lần gọi
    elapsed = time.monotonic() - _last_call_time
    if elapsed < MIN_CALL_INTERVAL:
        await asyncio.sleep(MIN_CALL_INTERVAL - elapsed)

    last_error: Optional[Exception] = None

    for provider_name, model in PROVIDER_FALLBACKS:
        for attempt in range(MAX_RETRIES):
            try:
                _last_call_time = time.monotonic()
                text = await _try_provider(provider_name, model, prompt)
                logger.info("✅ Codegen [%s] OK via %s — %d chars", label, provider_name, len(text))
                return _strip_markdown(text)
            except Exception as e:
                last_error = e
                logger.warning(
                    "❌ Codegen [%s] provider=%s attempt=%d/%d: %s",
                    label, provider_name, attempt + 1, MAX_RETRIES, e,
                )
                if attempt < MAX_RETRIES - 1:
                    logger.info("⏳ Đợi %.0fs trước khi retry...", RETRY_DELAY)
                    await asyncio.sleep(RETRY_DELAY)

        logger.info("Codegen [%s] chuyển sang provider tiếp theo...", label)

    raise ValueError(f"Codegen [{label}] tất cả provider đều thất bại. Lỗi cuối: {last_error}")


async def gen_solution(problem: Problem, api_key: str = "", model: str = "") -> str:
    """Generate C++ solution for the problem via g4f (api_key/model ignored)."""
    prompt = _build_prompt(SOLUTION_PROMPT, problem)
    return await _call_g4f(prompt, "solution")


async def gen_generator(problem: Problem, api_key: str = "", model: str = "") -> str:
    """Generate C++ test generator via g4f (api_key/model ignored)."""
    prompt = _build_prompt(GENERATOR_PROMPT, problem)
    return await _call_g4f(prompt, "generator")
