"""
G4F Code Generator — generates C++ solution and test generator
from problem statement using free AI providers via g4f.

Dùng DeepSeek (v3 / r1) qua các free providers, không cần API key.

Provider fallback order (text-only, no auth):
  1. WeWordle       — deepseek-v3
  2. PollinationsAI — deepseek-v3
  3. AnyProvider    — deepseek-r1 (last resort)
"""

import asyncio
import logging
import time
from typing import Optional

from modules.parser.models import Problem, Subtask

logger = logging.getLogger("polygon-uploader.parser")

MAX_RETRIES = 3
RETRY_DELAY = 10.0   # giây giữa các lần retry trong cùng provider
MIN_CALL_INTERVAL = 3.0

_last_call_time: float = 0.0

# (provider_name, model) — thử theo thứ tự
PROVIDER_FALLBACKS = [
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


# ── Subtask extraction ──────────────────────────────────────────────────────

SUBTASK_PROMPT = """Bạn là chuyên gia lập trình thi đấu. Dưới đây là đề bài:

--- ĐỀ BÀI ---
Tên: {title}
Statement: {statement}
Input: {input_format}
Output: {output_format}
Giới hạn: {time_limit}ms / {memory_limit}MB
Notes: {notes}
--- HẾT ĐỀ ---

Hãy xác định các subtask của bài này dựa trên constraints trong đề.
Nếu đề KHÔNG có subtask rõ ràng, trả về mảng rỗng [].

Trả về ĐÚNG JSON sau, không giải thích thêm:
[
  {{
    "index": 1,
    "score": 20,
    "constraints": "1 ≤ n ≤ 100",
    "n_tests": 5
  }},
  ...
]

Lưu ý:
- index bắt đầu từ 1
- constraints là chuỗi mô tả ràng buộc cho subtask đó (ngắn gọn, rõ ràng)
- score là điểm của subtask (nếu không có trong đề thì chia đều, tổng 100)
- n_tests luôn là 5"""

GENERATOR_FOR_SUBTASK_PROMPT = """Bạn là chuyên gia lập trình thi đấu. Dưới đây là đề bài:

--- ĐỀ BÀI ---
Tên: {title}
Statement: {statement}
Input: {input_format}
Output: {output_format}
Giới hạn: {time_limit}ms / {memory_limit}MB
--- HẾT ĐỀ ---

Hãy viết test generator C++ dùng testlib.h để sinh test ngẫu nhiên CHO SUBTASK SAU:
  Subtask {subtask_index}: {subtask_constraints}

Generator phải:
- Dùng registerGen(argc, argv, 1) để nhận seed từ argv[1]
- Sinh test thỏa mãn ĐÚNG constraint của subtask này (không sinh test vượt quá giới hạn subtask)
- Sinh test đa dạng, cover edge case trong phạm vi subtask
- KHÔNG gọi println() không có argument — dùng cout << "\n" thay thế
- println(x) cần ít nhất 1 argument; rnd.next(a, b) cho số ngẫu nhiên

CHỈ trả về code C++ thuần túy, không có markdown, không có giải thích.
Bắt đầu bằng #include."""

GENERATOR_FIX_PROMPT = """Code generator C++ sau bị lỗi compile:

--- LỖI COMPILE ---
{compile_error}
--- HẾT LỖI ---

Subtask: {subtask_index} — {subtask_constraints}
Input format: {input_format}

Hãy sửa lại toàn bộ code để fix lỗi. KHÔNG dùng println() không có argument.
CHỈ trả về code C++ thuần túy đã sửa, không markdown, không giải thích."""


async def gen_subtasks(problem: Problem, api_key: str = "", model: str = "") -> list[Subtask]:
    """
    Ask AI to extract subtasks from the problem statement.
    Returns empty list if the problem has no subtasks.
    """
    import json, re

    prompt = SUBTASK_PROMPT.format(
        title=problem.title,
        statement=problem.statement,
        input_format=problem.input_format,
        output_format=problem.output_format,
        time_limit=problem.time_limit,
        memory_limit=problem.memory_limit,
        notes=problem.notes or "(không có)",
    )

    raw = await _call_g4f(prompt, "subtasks")

    # Strip markdown fences if present
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try to find JSON array in the response
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
        else:
            logger.warning("gen_subtasks: không parse được JSON, trả về []")
            return []

    if not isinstance(data, list):
        return []

    subtasks = []
    for item in data:
        try:
            subtasks.append(Subtask(
                index=int(item.get("index", len(subtasks) + 1)),
                score=int(item.get("score", 0)),
                constraints=str(item.get("constraints", "")),
                n_tests=int(item.get("n_tests", 5)),
            ))
        except Exception as e:
            logger.warning("gen_subtasks: bỏ qua item lỗi %s — %s", item, e)

    return subtasks


async def gen_generator_for_subtask(
    problem: Problem,
    subtask: Subtask,
    api_key: str = "",
    model: str = "",
    compile_error_hint: str | None = None,
) -> str:
    """Generate a C++ test generator for one subtask.
    If compile_error_hint provided, AI fixes the error instead of gen from scratch.
    """
    if compile_error_hint:
        prompt = GENERATOR_FIX_PROMPT.format(
            compile_error=compile_error_hint[:600],
            subtask_index=subtask.index,
            subtask_constraints=subtask.constraints,
            input_format=problem.input_format,
        )
        label = f"generator-subtask{subtask.index}-fix"
    else:
        prompt = GENERATOR_FOR_SUBTASK_PROMPT.format(
            title=problem.title,
            statement=problem.statement,
            input_format=problem.input_format,
            output_format=problem.output_format,
            time_limit=problem.time_limit,
            memory_limit=problem.memory_limit,
            subtask_index=subtask.index,
            subtask_constraints=subtask.constraints,
        )
        label = f"generator-subtask{subtask.index}"
    return await _call_g4f(prompt, label)