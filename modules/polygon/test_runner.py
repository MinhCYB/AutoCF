"""
Local test generator runner.

Flow:
  1. Copy gen.cpp + testlib.h vào tmpdir
  2. Compile bằng g++
  3. Chạy ./gen <seed> N lần → collect stdin strings
  4. Trả về list[str] để upload lên Polygon qua problem.saveTest
"""

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Awaitable, Optional

logger = logging.getLogger("polygon-uploader.test_runner")

COMPILE_TIMEOUT = 30   # seconds
RUN_TIMEOUT     = 10   # seconds per test


class CompileError(Exception):
    """Raised when g++ fails to compile gen.cpp."""


class RunError(Exception):
    """Raised when the generator crashes or times out."""


def _find_gpp() -> str:
    """Return g++ binary path, raise if not found."""
    gpp = shutil.which("g++")
    if not gpp:
        raise EnvironmentError(
            "Không tìm thấy g++ trên PATH. "
            "Cài MinGW (Windows) hoặc build-essential (Linux) rồi thử lại."
        )
    return gpp


def _compile(src_path: str, out_path: str, include_dirs: list[str]) -> None:
    """Compile src_path → out_path. Raises CompileError on failure."""
    import shutil, platform

    gpp = _find_gpp()

    # Trên Windows, thử tìm g++ không có spaces trong path (tránh MinGW linker bug)
    # MinGW 16.x có bug với path chứa "Program Files"
    if platform.system() == "Windows":
        # Ưu tiên g++ từ path không có spaces: C:\mingw64, C:\mingw, C:\msys64, ...
        for candidate in ["C:\\mingw64\\bin\\g++.exe", "C:\\mingw\\bin\\g++.exe",
                          "C:\\msys64\\mingw64\\bin\\g++.exe", "C:\\w64devkit\\bin\\g++.exe"]:
            if os.path.isfile(candidate):
                gpp = candidate
                logger.info("Dùng g++ không có spaces: %s", gpp)
                break

    include_flags = []
    for d in include_dirs:
        include_flags += ["-I", d]

    cmd = [gpp, "-O2", "-std=c++17"] + include_flags + [src_path, "-o", out_path]
    logger.info("Compile: %s", " ".join(cmd))

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=COMPILE_TIMEOUT,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise CompileError(
            f"Compile thất bại:\n{stderr}"
        )
    logger.info("Compile OK → %s", out_path)


def _run_once(binary: str, seed: int) -> str:
    """Run binary with seed, return stdout. Raises RunError on failure."""
    result = subprocess.run(
        [binary, str(seed)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=RUN_TIMEOUT,
    )
    if result.returncode != 0:
        raise RunError(
            f"Generator crash (seed={seed}):\n{(result.stderr or '').strip()}"
        )
    return result.stdout


async def compile_and_run(
    gen_code: str,
    n_tests: int,
    testlib_path: str,
    seed_offset: int = 0,
    on_log: Optional[Callable[[str], Awaitable[None]]] = None,
) -> list[str]:
    """
    Compile gen_code and run it n_tests times.

    Args:
        gen_code:     C++ source of the generator (uses testlib.h).
        n_tests:      How many test inputs to generate.
        testlib_path: Absolute path to testlib.h on the local machine.
        seed_offset:  Starting seed value (useful when running multiple subtasks
                      so seeds don't overlap: subtask1 uses 1-5, subtask2 uses 6-10).
        on_log:       Async callback for progress messages.

    Returns:
        List of test input strings (length == n_tests).

    Raises:
        CompileError, RunError, EnvironmentError
    """

    async def log(msg: str):
        if on_log:
            await on_log(msg)

    # Validate testlib.h
    testlib_path = str(testlib_path).strip()
    if not os.path.isfile(testlib_path):
        raise FileNotFoundError(
            f"testlib.h không tìm thấy tại: {testlib_path}\n"
            "Hãy kiểm tra lại đường dẫn testlib_path."
        )
    testlib_dir = str(Path(testlib_path).parent)

    with tempfile.TemporaryDirectory() as tmpdir:
        gen_src  = os.path.join(tmpdir, "gen.cpp")
        gen_bin  = os.path.join(tmpdir, "gen")
        if os.name == "nt":
            gen_bin += ".exe"

        # Write gen.cpp
        with open(gen_src, "w", encoding="utf-8") as f:
            f.write(gen_code)

        # Compile (blocking, run in thread to not block event loop)
        await log("⚙️  Đang compile gen.cpp...")
        try:
            await asyncio.to_thread(_compile, gen_src, gen_bin, [testlib_dir])
        except CompileError as e:
            await log(f"❌ {e}")
            raise
        await log("✅ Compile thành công")

        # Run N times
        inputs: list[str] = []
        for i in range(n_tests):
            seed = seed_offset + i + 1
            await log(f"   Chạy gen seed={seed} ({i+1}/{n_tests})...")
            try:
                test_input = await asyncio.to_thread(_run_once, gen_bin, seed)
                inputs.append(test_input)
            except RunError as e:
                await log(f"⚠️  {e} — bỏ qua test này")
                # Don't crash entire subtask; just skip this seed

        await log(f"✅ Sinh được {len(inputs)}/{n_tests} test")
        return inputs