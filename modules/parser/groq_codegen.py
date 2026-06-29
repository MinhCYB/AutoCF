"""
Groq Code Generator — drop-in replacement cho g4f_codegen.
Dùng Groq API (free tier: 14,400 req/day) thay vì g4f free providers.

Model mặc định: deepseek-r1-distill-llama-70b (reasoning, tốt cho code)
Fallback:       llama-3.3-70b-versatile
"""

import asyncio
import json
import logging
import re
import time
from typing import Optional

from openai import AsyncOpenAI

from modules.parser.models import Problem, Subtask

logger = logging.getLogger("polygon-uploader.parser")

MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 30]
MIN_CALL_INTERVAL = 12.0  # 5 RPM free tier → 60/5 = 12s giữa các call

_last_call_time: float = 0.0

DEFAULT_MODEL  = "meta-llama/llama-4-scout-17b-16e-instruct"
FALLBACK_MODEL = "llama-3.1-8b-instant"

# ── Prompts (giữ nguyên từ g4f_codegen) ──────────────────────────────────────

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

Cấu trúc code BẮT BUỘC:
#include <bits/stdc++.h>
using namespace std;

const int MAXN = 1e6 + 7;  // hằng số khai báo NGOÀI main
// mảng, biến global khác nếu cần

int main() {{
    ios_base::sync_with_stdio(false);
    cin.tie(0);
    // code
    return 0;
}}

Yêu cầu bắt buộc:
- Hằng số (MAXN, MOD, ...) và mảng tĩnh lớn PHẢI khai báo ở global scope, NGOÀI hàm main
- KHÔNG khai báo hằng hay mảng lớn bên trong main
- KHÔNG dùng VLA như `int a[n]` — dùng `vector<int> a(n)` hoặc mảng global với MAXN
- Dùng `long long` khi giá trị có thể vượt 2^31
- Compile được với g++ -O2 -std=c++17

CHỈ trả về code C++ thuần túy, không có markdown, không có giải thích.
Bắt đầu bằng #include."""

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
- KHÔNG gọi println() không có argument — dùng cout << "\\n" thay thế
- println(x) cần ít nhất 1 argument; rnd.next(a, b) cho số ngẫu nhiên

QUAN TRỌNG: CHỈ trả về code C++ thuần túy. KHÔNG có markdown, KHÔNG có giải thích, KHÔNG có comment ngoài code, KHÔNG có text nào khác ngoài code C++.
Dòng đầu tiên phải là #include."""

GENERATOR_FIX_PROMPT = """Code generator C++ sau bị lỗi compile:

--- LỖI COMPILE ---
{compile_error}
--- HẾT LỖI ---

Subtask: {subtask_index} — {subtask_constraints}
Input format: {input_format}

Hãy sửa lại toàn bộ code để fix lỗi. KHÔNG dùng println() không có argument.
CHỈ trả về code C++ thuần túy đã sửa, không markdown, không giải thích."""

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

Nếu đề CÓ subtask rõ ràng (ví dụ "Subtask 1: 20 điểm, n ≤ 100"), hãy liệt kê đầy đủ.
Nếu đề KHÔNG có subtask rõ ràng nhưng có constraints (trong Notes, Statement, hoặc Input), hãy tạo 1 subtask duy nhất 100 điểm với constraints đó.
Chỉ trả về [] nếu đề hoàn toàn không có constraints nào.

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_cpp(code: str) -> str:
    """Extract only the C++ code, stripping any explanation text before/after."""
    # Nếu có #include thì lấy từ đó trở đi
    idx = code.find("#include")
    if idx > 0:
        code = code[idx:]
    # Tìm dấu } cuối cùng và cắt phần text sau đó
    last_brace = code.rfind("}")
    if last_brace != -1:
        code = code[:last_brace + 1]
    return code.strip()


def _strip_markdown(code: str) -> str:
    code = code.strip()
    # Strip <think>...</think> block (deepseek-r1 reasoning)
    code = re.sub(r"<think>.*?</think>", "", code, flags=re.DOTALL).strip()
    # Normalize line endings
    code = code.replace("\r\n", "\n").replace("\r", "\n")
    # Strip markdown code fences
    if code.startswith("```"):
        lines = code.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code = "\n".join(lines)
    # Strip any remaining ``` lines (giữa code)
    code = re.sub(r"^```.*$", "", code, flags=re.MULTILINE).strip()
    return code.strip()


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


async def _call_groq(api_key: str, prompt: str, label: str, model: str = DEFAULT_MODEL) -> str:
    global _last_call_time

    elapsed = time.monotonic() - _last_call_time
    if elapsed < MIN_CALL_INTERVAL:
        await asyncio.sleep(MIN_CALL_INTERVAL - elapsed)

    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1",
    )

    models_to_try = [model, FALLBACK_MODEL] if model != FALLBACK_MODEL else [model]

    for m in models_to_try:
        for attempt in range(MAX_RETRIES):
            try:
                logger.info("Groq codegen [%s] model=%s attempt %d/%d...", label, m, attempt + 1, MAX_RETRIES)
                _last_call_time = time.monotonic()
                response = await client.chat.completions.create(
                    model=m,
                    messages=[
                        {"role": "system", "content": "You are a competitive programming expert. Output ONLY raw C++ code. No explanations, no markdown, no comments outside the code, no text before or after the code. The very first character of your response must be '#'."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0,
                    timeout=120,
                )
                text = response.choices[0].message.content or ""
                logger.info("Groq codegen [%s] OK — %d chars", label, len(text))
                return _extract_cpp(_strip_markdown(text))
            except Exception as e:
                err = str(e)
                logger.warning("Groq codegen [%s] model=%s attempt %d: %s", label, m, attempt + 1, err)
                if "429" in err and attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                    logger.info("⏳ Rate limited — đợi %ds...", delay)
                    await asyncio.sleep(delay)
                elif attempt == MAX_RETRIES - 1:
                    logger.warning("Groq [%s] model=%s thất bại, thử model tiếp...", label, m)
                    break
                else:
                    raise

    raise ValueError(f"Groq codegen [{label}] tất cả model đều thất bại")


# ── Public API (drop-in thay g4f_codegen) ────────────────────────────────────

async def gen_solution(problem: Problem, api_key: str = "", model: str = "") -> str:
    prompt = _build_prompt(SOLUTION_PROMPT, problem)
    return await _call_groq(api_key, prompt, "solution", model or DEFAULT_MODEL)


async def gen_generator(problem: Problem, api_key: str = "", model: str = "") -> str:
    from modules.parser.g4f_codegen import GENERATOR_PROMPT, _build_prompt as _bp
    prompt = _bp(GENERATOR_PROMPT, problem)
    return await _call_groq(api_key, prompt, "generator", model or DEFAULT_MODEL)


async def gen_subtasks(problem: Problem, api_key: str = "", model: str = "") -> list[Subtask]:
    prompt = SUBTASK_PROMPT.format(
        title=problem.title,
        statement=problem.statement,
        input_format=problem.input_format,
        output_format=problem.output_format,
        time_limit=problem.time_limit,
        memory_limit=problem.memory_limit,
        notes=problem.notes or "(không có)",
    )

    raw = await _call_groq(api_key, prompt, "subtasks", model or DEFAULT_MODEL)

    raw = raw.strip()
    # Strip <think>...</think> block (qwen3/deepseek reasoning models)
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
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

    return await _call_groq(api_key, prompt, label, model or DEFAULT_MODEL)

async def suggest_subtask(problem: "Problem", api_key: str = "", model: str = "") -> str:
    """
    Khi không tìm được subtask từ đề, AI đề xuất 1 subtask 100đ
    dựa trên constraints trong đề. Trả về string dạng 'score | constraints | n_tests'.
    """
    prompt = f"""You are given a competitive programming problem. No explicit subtasks are defined.
Based on the constraints mentioned in the problem, propose ONE subtask worth 100 points.

Problem title: {problem.title}
Statement: {problem.statement}
Input format: {problem.input_format}

Reply with EXACTLY one line in this format (nothing else):
100 | <constraint expression, e.g. 1 ≤ n ≤ 100000> | 10

Use the actual constraints from the problem. If unclear, use reasonable defaults."""

    result = await _call_groq(api_key, prompt, "suggest-subtask", model or DEFAULT_MODEL)
    # Clean up — take first non-empty line
    for line in result.strip().splitlines():
        line = line.strip()
        if "|" in line:
            return line
    return "100 | 1 ≤ n ≤ 1000 | 10"