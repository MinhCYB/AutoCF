"""
Gemini Code Generator — generates C++ solution and test generator
from problem statement text using Gemini API (text-only, no image).

Much lighter than vision calls — just sends text, no image encoding.
"""

import asyncio
import logging
import time
from typing import Optional

from google import genai

from modules.parser.models import Problem

logger = logging.getLogger("polygon-uploader.parser")

MAX_RETRIES = 3
RETRY_DELAYS = [10, 30, 60]

_last_call_time: float = 0.0
MIN_CALL_INTERVAL = 5.0  # lighter than vision calls


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
        # Remove first line (```cpp or ```) and last ``` 
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code = "\n".join(lines)
    return code.strip()


async def _call_gemini(api_key: str, model: str, prompt: str, label: str) -> str:
    global _last_call_time

    elapsed = time.monotonic() - _last_call_time
    if elapsed < MIN_CALL_INTERVAL:
        wait = MIN_CALL_INTERVAL - elapsed
        logger.info("⏳ Codegen throttle: đợi %.1fs...", wait)
        await asyncio.sleep(wait)

    client = genai.Client(api_key=api_key)

    for attempt in range(MAX_RETRIES + 1):
        try:
            logger.info("Gemini codegen [%s] attempt %d/%d...", label, attempt + 1, MAX_RETRIES + 1)
            _last_call_time = time.monotonic()
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model,
                contents=prompt,
            )
            text = response.text or ""
            logger.info("Gemini codegen [%s] OK — %d chars", label, len(text))
            return _strip_markdown(text)

        except Exception as e:
            err = str(e)
            logger.warning("Gemini codegen [%s] error attempt %d: %s", label, attempt + 1, err)
            if attempt < MAX_RETRIES:
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                logger.info("⏳ Đợi %ds trước khi retry...", delay)
                await asyncio.sleep(delay)
            else:
                raise ValueError(f"Gemini codegen [{label}] thất bại: {err}")


async def gen_solution(problem: Problem, api_key: str, model: str = "gemini-2.0-flash") -> str:
    """Generate C++ solution for the problem."""
    prompt = _build_prompt(SOLUTION_PROMPT, problem)
    return await _call_gemini(api_key, model, prompt, "solution")


async def gen_generator(problem: Problem, api_key: str, model: str = "gemini-2.0-flash") -> str:
    """Generate C++ test generator for the problem."""
    prompt = _build_prompt(GENERATOR_PROMPT, problem)
    return await _call_gemini(api_key, model, prompt, "generator")
