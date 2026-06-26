"""
Polygon upload orchestrator — sequences all API calls needed
to fully create and configure a problem on Polygon.

Upload flow per problem:
  1. problem.create
  2. problem.updateInfo (time/memory limits)
  3. problem.saveStatement (LaTeX content)
  4. problem.saveTest (examples + optional test files)
  5. problem.setChecker
  6. problem.saveSolution (if exists)
  7. AI Codegen: gen solution / gen generator (optional, trước commit)
  8. problem.commitChanges
"""

from pathlib import Path
from typing import Awaitable, Callable, Optional

from modules.parser.models import Problem
from modules.polygon.client import PolygonClient
from modules.parser import groq_codegen as g4f_codegen  # groq backend, drop-in replacement
from modules.polygon.test_runner import compile_and_run, CompileError, RunError


def gen_dummy_solution(examples: list) -> str:
    """
    Generate a C++ dummy solution that hardcodes outputs for each example test case.
    Reads input line by line, matches against known inputs, prints hardcoded output.
    Fallback: print the first example output unconditionally.
    """
    if not examples:
        return (
            "#include <bits/stdc++.h>\n"
            "using namespace std;\n"
            "int main() {\n"
            "    // TODO: implement solution\n"
            "    return 0;\n"
            "}\n"
        )

    cases = []
    for ex in examples:
        inp = ex["input"] if isinstance(ex, dict) else ex.input
        out = ex["output"] if isinstance(ex, dict) else ex.output
        cases.append((inp.strip(), out.strip()))

    lines = [
        "#include <bits/stdc++.h>",
        "using namespace std;",
        "",
        "// AUTO-GENERATED dummy solution — hardcodes example outputs",
        "// Replace with actual solution before contest.",
        "",
        "int main() {",
        "    ios::sync_with_stdio(false);",
        "    cin.tie(nullptr);",
        "",
        "    // Read all input",
        "    string input_data, line;",
        "    while (getline(cin, line)) {",
        "        if (!input_data.empty()) input_data += '\\n';",
        "        input_data += line;",
        "    }",
        "",
    ]

    for i, (inp, out) in enumerate(cases):
        escaped_inp = inp.replace("\\", "\\\\").replace('"', '\\"'). \
            replace("\n", "\\n").replace("\r", "")
        escaped_out = out.replace("\\", "\\\\").replace('"', '\\"'). \
            replace("\n", "\\n").replace("\r", "")
        cond = "if" if i == 0 else "} else if"
        lines.append(f'    {cond} (input_data == "{escaped_inp}") {{')
        lines.append(f'        cout << "{escaped_out}" << endl;')

    lines += [
        "    } else {",
        f'        // Fallback: output first example',
        f'        cout << "{cases[0][1].replace(chr(10), "\\n").replace(chr(34), chr(92)+chr(34))}" << endl;',
        "    }",
        "",
        "    return 0;",
        "}",
        "",
    ]

    return "\n".join(lines)


async def _gen_and_upload_tests(
    client: PolygonClient,
    problem: Problem,
    problem_id: int,
    log,
    cached_inputs: dict | None = None,   # {subtask_index_str: [input_str, ...]} từ preview
) -> None:
    """
    Upload tests từ cache preview lên Polygon.
    Không gọi AI ở đây — AI gen đã xảy ra ở bước preview, user đã confirm.
    """
    if not cached_inputs:
        await log("⚠️ Không có test từ preview — bỏ qua upload test. Hãy Gen Test Preview trước.")
        return

    subtasks = problem.subtasks or []
    test_index = len(problem.examples) + 1

    for cached_key, inputs in cached_inputs.items():
        st_label = f"Subtask {cached_key}"
        # Tìm subtask tương ứng để lấy label
        for st in subtasks:
            if str(st.index) == cached_key:
                st_label = f"Subtask {st.index} ({st.constraints})"
                break

        await log(f"\n🔧 Upload {st_label}...")

        if not inputs:
            await log(f"   ⚠️ Không có test")
            continue

        # Deduplicate
        seen = set()
        unique_inputs = []
        for t in inputs:
            key = t.strip()
            if key not in seen:
                seen.add(key)
                unique_inputs.append(t)
        if len(unique_inputs) < len(inputs):
            await log(f"   ℹ️ Bỏ {len(inputs) - len(unique_inputs)} test trùng")
        inputs = unique_inputs

        # Upload từng test lên Polygon
        uploaded = 0
        for test_input in inputs:
            try:
                await client.call(
                    "problem.saveTest",
                    problemId=problem_id,
                    testset="tests",
                    testIndex=test_index,
                    testInput=test_input,
                )
                test_index += 1
                uploaded += 1
            except Exception as e:
                await log(f"   ⚠️ Upload test {test_index} thất bại: {e}")

        await log(f"   ✅ Subtask {st.index}: upload {uploaded}/{len(inputs)} test (index {test_index - uploaded}–{test_index - 1})")

    await log(f"\n✅ Gen test hoàn tất — tổng {test_index - len(problem.examples) - 1} test đã upload")


async def upload_problem(
    client: PolygonClient,
    problem: Problem,
    lang: str = "english",
    on_log: Optional[Callable[[str], Awaitable[None]]] = None,
    gen_solution: bool = False,
    gen_tests: bool = False,
    gemini_api_key: str = "",
    gemini_model: str = "gemini-2.0-flash",
    testlib_path: str = "",
    cached_test_inputs: dict | None = None,
) -> dict:
    """
    Upload a single problem to Polygon.

    Args:
        client: Authenticated PolygonClient.
        problem: Problem data to upload.
        lang: Statement language code. Polygon chấp nhận: english, russian,
              chinese, kazakh, ukrainian, spanish, portuguese, french, german,
              persian. Không có "vietnamese" — nếu truyền vào sẽ fallback "english".
        on_log: Async callback for progress logging.

    Returns:
        Dict with problem_id, name, and status.

    Raises:
        PolygonAPIError: If any API call fails.
    """

    async def log(msg: str):
        if on_log:
            await on_log(msg)

    # ── 1. Create problem ──
    await log(f"Tạo problem '{problem.polygon_name}'...")
    try:
        result = await client.call("problem.create", name=problem.polygon_name)
        raw_result = result.get("result", {})
        if isinstance(raw_result, dict):
            problem_id = raw_result["id"]
        else:
            import re as _re
            m = _re.search(r'"id"\s*:\s*(\d+)', str(raw_result))
            if m:
                problem_id = int(m.group(1))
            else:
                raise ValueError("Cannot parse problem_id from create response")
    except Exception as create_err:
        err_str = str(create_err)
        if "already have such problem" in err_str or "already exists" in err_str.lower():
            # Problem đã tồn tại — tìm ID qua problems.list
            await log(f"⚠️ Problem '{problem.polygon_name}' đã tồn tại, tìm ID để update...")
            list_result = await client.call("problems.list")
            raw_list = list_result.get("result", [])
            # result có thể là list hoặc dict với key "problems"
            if isinstance(raw_list, list):
                problems_list = raw_list
            elif isinstance(raw_list, dict):
                problems_list = raw_list.get("problems", [])
            else:
                problems_list = []
            matched = [p for p in problems_list if isinstance(p, dict) and p.get("name") == problem.polygon_name]
            if not matched:
                raise ValueError(f"Không tìm thấy problem '{problem.polygon_name}' trong danh sách")
            problem_id = matched[0]["id"]
            await log(f"✅ Tìm thấy problem ID={problem_id}, tiếp tục update...")
        else:
            raise
    problem_name = problem.polygon_name
    await log(f"✅ Tạo thành công (ID: {problem_id})")

    # ── 2. Update info ──
    # Polygon problem-specific methods dùng problemId (integer) để identify problem
    await log(f"Cập nhật limits: {problem.time_limit}ms / {problem.memory_limit}MB...")
    await client.call(
        "problem.updateInfo",
        problemId=problem_id,
        timeLimit=problem.time_limit,
        memoryLimit=problem.memory_limit,
    )
    await log("✅ Limits đã cập nhật")

    # ── 3. Save statement ──
    # Bug fix: Polygon chỉ chấp nhận các lang code chuẩn như "english", "russian", ...
    # Không có "vietnamese" → fallback về "english" để tránh FAILED
    polygon_lang = lang if lang in (
        "english", "russian", "chinese", "kazakh", "ukrainian",
        "spanish", "portuguese", "french", "german", "persian",
    ) else "english"
    await log(f"Upload statement (lang={polygon_lang})...")
    stmt_params = {
        "problemId": problem_id,
        "lang": polygon_lang,
        "encoding": "UTF-8",
        "name": problem.title,
        "legend": problem.statement,
        "input": problem.input_format,
        "output": problem.output_format,
    }
    if problem.notes:
        stmt_params["notes"] = problem.notes

    await client.call("problem.saveStatement", **stmt_params)
    await log("✅ Statement uploaded")

    # ── 4. Save example tests ──
    # Bug fix: param đúng là testOutputForStatements (theo API docs), kết hợp với
    # testInputForStatements để hiển thị đúng example trong statement
    for i, ex in enumerate(problem.examples, start=1):
        await log(f"Upload example test {i}...")
        await client.call(
            "problem.saveTest",
            problemId=problem_id,
            testset="tests",
            testIndex=i,
            testInput=ex.input,
            testUseInStatements=True,
            testInputForStatements=ex.input,
            testOutputForStatements=ex.output,
        )
        await log(f"✅ Example test {i}")

    # ── 4b. Upload extra tests from tests_dir ──
    test_offset = len(problem.examples)
    if problem.tests_dir:
        tests_path = Path(problem.tests_dir)
        if tests_path.is_dir():
            # Find numbered test input files (1, 2, 3, ...)
            test_inputs = sorted(
                [
                    f
                    for f in tests_path.iterdir()
                    if f.is_file() and f.suffix == "" and f.name.isdigit()
                ],
                key=lambda f: int(f.name),
            )
            for tf in test_inputs:
                idx = test_offset + int(tf.name)
                test_input = tf.read_text(encoding="utf-8")
                # Check for corresponding .a answer file
                answer_path = tf.parent / f"{tf.name}.a"
                await log(f"Upload test {idx} (file)...")
                call_params: dict = {
                    "problemId": problem_id,
                    "testset": "tests",
                    "testIndex": idx,
                    "testInput": test_input,
                }
                if answer_path.is_file():
                    call_params["testOutputForStatements"] = answer_path.read_text(encoding="utf-8")
                await client.call("problem.saveTest", **call_params)
                await log(f"✅ Test {idx}")

    # ── 5. Set checker ──
    checker = problem.checker if problem.checker and problem.checker.strip() else ""
    if checker:
        await log(f"Set checker: {checker}...")
        try:
            await client.call(
                "problem.setChecker",
                problemId=problem_id,
                checker=checker,
            )
            await log(f"✅ Checker: {checker}")
        except Exception as e:
            await log(f"⚠️ Không set được checker '{checker}': {e} — bỏ qua")

    # ── 6. Upload solution ──
    # if problem.solution_path:
    #     sol_path = Path(problem.solution_path)
    #     if sol_path.is_file():
    #         sol_content = sol_path.read_text(encoding="utf-8")
    #         sol_name = sol_path.name
    #         await log(f"Upload solution: {sol_name}...")
    #     else:
    #         await log("Không có file solution, gen dummy solution C++...")
    #         sol_content = gen_dummy_solution(problem.examples)
    #         sol_name = "dummy_solution.cpp"
    # else:
    #     await log("Không có file solution, gen dummy solution C++...")
    #     sol_content = gen_dummy_solution(problem.examples)
    #     sol_name = "dummy_solution.cpp"

    # await client.call(
    #     "problem.saveSolution",
    #     problemId=problem_id,
    #     name=sol_name,
    #     file=sol_content,
    #     tag="MA",
    # )
    # await log(f"✅ Solution: {sol_name}")

    # ── 7. Save tags ──
    if problem.tags:
        await log(f"Lưu tags: {', '.join(problem.tags)}...")
        await client.call(
            "problem.saveTags",
            problemId=problem_id,
            tags=",".join(problem.tags),
        )
        await log(f"✅ Tags: {', '.join(problem.tags)}")

    # ── 7. AI Codegen (optional) — chạy TRƯỚC commit ──────────────────────────
    # Cả gen_solution lẫn gen_tests đều dùng g4f (DeepSeek), không cần gemini_api_key
    if gen_solution:
        await log("🤖 Gen solution C++ bằng Groq...")
        try:
            sol_code = await g4f_codegen.gen_solution(problem, gemini_api_key)
            if sol_code:
                await client.call(
                    "problem.saveSolution",
                    problemId=problem_id,
                    name="solution.cpp",
                    file=sol_code,
                    tag="MA",
                )
                await log("✅ Solution C++ đã upload")
        except Exception as e:
            await log(f"⚠️ Gen solution thất bại: {e}")

    if gen_tests:
        await _gen_and_upload_tests(
            client=client,
            problem=problem,
            problem_id=problem_id,
            log=log,
            cached_inputs=cached_test_inputs,
        )

    # ── 8. Commit — sau khi đã có đủ solution/generator ──
    await log("Commit changes...")
    await client.call(
        "problem.commitChanges",
        problemId=problem_id,
        minorChanges=False,
        message="Auto upload by polygon-uploader",
    )
    await log("✅ Commit thành công!")

    # ── 9. Build package ──
    await log("Tạo package (Standard)...")
    try:
        await client.call(
            "problem.buildPackage",
            problemId=problem_id,
            full=False,
            verify=True,
        )
    except Exception as e:
        await log(f"⚠️ buildPackage warning: {e}")
    import asyncio as _asyncio
    for _ in range(30):
        await _asyncio.sleep(3)
        try:
            pkg_list = await client.call("problem.getPackages", problemId=problem_id)
            packages = pkg_list.get("result", [])
            if isinstance(packages, list) and packages:
                latest = sorted(packages, key=lambda p: p.get("id", 0))[-1]
                state_str = latest.get("state", "")
                if state_str == "READY":
                    await log("✅ Package tạo thành công!")
                    break
                elif state_str == "FAILED":
                    await log("⚠️ Package build thất bại — kiểm tra Polygon manually")
                    break
        except Exception as e:
            await log(f"⚠️ getPackages warning: {e}")
            break
    else:
        await log("⚠️ Package build timeout — kiểm tra Polygon manually")

    return {
        "problem_id": problem_id,
        "name": problem.polygon_name,
        "status": "success",
    }