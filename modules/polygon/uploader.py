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
  7. problem.commitChanges
"""

from pathlib import Path
from typing import Awaitable, Callable, Optional

from modules.parser.models import Problem
from modules.polygon.client import PolygonClient


async def upload_problem(
    client: PolygonClient,
    problem: Problem,
    lang: str = "vietnamese",
    on_log: Optional[Callable[[str], Awaitable[None]]] = None,
) -> dict:
    """
    Upload a single problem to Polygon.

    Args:
        client: Authenticated PolygonClient.
        problem: Problem data to upload.
        lang: Statement language code (default "vietnamese").
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
    result = await client.call("problem.create", name=problem.polygon_name)
    problem_id = result["result"]["id"]
    await log(f"✅ Tạo thành công (ID: {problem_id})")

    # ── 2. Update info ──
    await log(f"Cập nhật limits: {problem.time_limit}ms / {problem.memory_limit}MB...")
    await client.call(
        "problem.updateInfo",
        problemId=problem_id,
        timeLimit=problem.time_limit,
        memoryLimit=problem.memory_limit,
    )
    await log("✅ Limits đã cập nhật")

    # ── 3. Save statement ──
    await log("Upload statement...")
    stmt_params = {
        "problemId": problem_id,
        "lang": lang,
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
    for i, ex in enumerate(problem.examples, start=1):
        await log(f"Upload example test {i}...")
        await client.call(
            "problem.saveTest",
            problemId=problem_id,
            testset="tests",
            testIndex=i,
            testInput=ex.input,
            testUseInStatements=True,
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
                await log(f"Upload test {idx} (file)...")
                await client.call(
                    "problem.saveTest",
                    problemId=problem_id,
                    testset="tests",
                    testIndex=idx,
                    testInput=test_input,
                )
                await log(f"✅ Test {idx}")

    # ── 5. Set checker ──
    await log(f"Set checker: {problem.checker}...")
    await client.call(
        "problem.setChecker",
        problemId=problem_id,
        checker=problem.checker,
    )
    await log(f"✅ Checker: {problem.checker}")

    # ── 6. Upload solution (optional) ──
    if problem.solution_path:
        sol_path = Path(problem.solution_path)
        if sol_path.is_file():
            sol_content = sol_path.read_text(encoding="utf-8")
            await log(f"Upload solution: {sol_path.name}...")
            await client.call(
                "problem.saveSolution",
                problemId=problem_id,
                name=sol_path.name,
                file=sol_content,
                tag="MA",
            )
            await log(f"✅ Solution: {sol_path.name}")

    # ── 7. Commit ──
    await log("Commit changes...")
    await client.call(
        "problem.commitChanges",
        problemId=problem_id,
        minorChanges=False,
        message="Auto upload by polygon-uploader",
    )
    await log("✅ Commit thành công!")

    return {
        "problem_id": problem_id,
        "name": problem.polygon_name,
        "status": "success",
    }
