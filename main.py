"""
Polygon Uploader — FastAPI entry point.

Routes:
  GET  /                      → Config page
  GET  /preview               → Preview & Edit page
  GET  /progress              → Progress & Log page

  GET  /api/browse            → File browser (directory listing)
  GET  /api/config            → Current config (keys masked)
  POST /api/save-config       → Save API keys
  POST /api/scan              → Scan problems directory
  GET  /api/problems          → List parsed problems
  GET  /api/problems/{idx}    → Single problem
  PUT  /api/problems/{idx}    → Update problem data
  POST /api/parse/{idx}       → Parse single problem with Gemini
  POST /api/parse-all         → Parse all scanned problems
  POST /api/upload            → Start upload (background)
  GET  /api/status            → SSE stream of upload progress
"""

import asyncio
import json
import logging
import os
import string
import traceback
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from modules.batch.scanner import scan_problems_dir
from modules.parser.file_loader import load_file
from modules.parser.gemini_parser import parse_problem
from modules.parser.models import Example, Problem
from modules.polygon.client import PolygonClient
from modules.polygon.uploader import upload_problem

# ─── App ──────────────────────────────────────────────

load_dotenv()

# ─── Logging ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("polygon-uploader")

app = FastAPI(title="Polygon Uploader", version="1.0.0")

FRONTEND_DIR = Path(__file__).parent / "frontend"


# ─── App State ────────────────────────────────────────

class AppState:
    """In-memory session state (single-user tool)."""

    def __init__(self):
        self.config: dict = {}
        self.scan_results: list[dict] = []
        self.problems: dict[int, dict] = {}   # idx -> {problem: Problem, status: str, scan: dict}
        self.log_queue: asyncio.Queue = asyncio.Queue()
        self.upload_running: bool = False

    def reset_for_upload(self):
        self.log_queue = asyncio.Queue()


state = AppState()


@app.on_event("startup")
async def startup():
    state.config = {
        "polygon_api_key": os.getenv("POLYGON_API_KEY", ""),
        "polygon_secret": os.getenv("POLYGON_SECRET", ""),
        "gemini_api_key": os.getenv("GEMINI_API_KEY", ""),
        "gemini_model": os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        "lang": "english",
        "level": "lv1",
        "contest_name": "",
        "start_index": 1,
    }


# ─── Page Routes ──────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index_page():
    return (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/preview", response_class=HTMLResponse)
async def preview_page():
    return (FRONTEND_DIR / "preview.html").read_text(encoding="utf-8")


@app.get("/progress", response_class=HTMLResponse)
async def progress_page():
    return (FRONTEND_DIR / "progress.html").read_text(encoding="utf-8")


# Serve static assets (CSS, JS, images)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ─── API: Config ──────────────────────────────────────

@app.get("/api/config")
async def get_config():
    """Return current config with masked secrets + status flags."""
    c = dict(state.config)

    # Mask secrets for display, but tell frontend they exist
    for key in ("polygon_api_key", "polygon_secret", "gemini_api_key"):
        val = c.get(key, "")
        if len(val) > 8:
            c[key + "_display"] = val[:4] + "●" * (len(val) - 8) + val[-4:]
        elif val:
            c[key + "_display"] = "●" * len(val)
        else:
            c[key + "_display"] = ""
        c[key + "_set"] = bool(val)
        # Don't send raw key values to frontend
        c.pop(key, None)

    return c


class SaveConfigRequest(BaseModel):
    polygon_api_key: str = ""
    polygon_secret: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    lang: str = "vietnamese"
    level: str = "lv1"
    contest_name: str = ""
    start_index: int = 1


@app.post("/api/save-config")
async def save_config(req: SaveConfigRequest):
    """Update in-memory config and optionally write to .env."""
    if req.polygon_api_key:
        state.config["polygon_api_key"] = req.polygon_api_key
    if req.polygon_secret:
        state.config["polygon_secret"] = req.polygon_secret
    if req.gemini_api_key:
        state.config["gemini_api_key"] = req.gemini_api_key

    state.config["gemini_model"] = req.gemini_model
    state.config["lang"] = req.lang
    state.config["level"] = req.level
    state.config["contest_name"] = req.contest_name
    state.config["start_index"] = req.start_index

    # Persist to .env
    env_path = Path(__file__).parent / ".env"
    lines = [
        f'POLYGON_API_KEY={state.config["polygon_api_key"]}',
        f'POLYGON_SECRET={state.config["polygon_secret"]}',
        f'GEMINI_API_KEY={state.config["gemini_api_key"]}',
    ]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {"status": "ok"}


# ─── API: File Browser ───────────────────────────────

@app.get("/api/browse")
async def browse_directory(path: str = ""):
    """
    List contents of a directory for the file browser UI.
    Returns drives list on Windows when path is empty.
    """
    if not path:
        # Windows: list available drives
        if os.name == "nt":
            drives = []
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.exists(drive):
                    drives.append({
                        "name": f"{letter}:",
                        "path": f"{letter}:\\",
                        "is_dir": True,
                        "size": 0,
                    })
            return {"current": "", "parent": "", "items": drives}
        else:
            path = "/"

    p = Path(path)
    if not p.exists() or not p.is_dir():
        return JSONResponse(
            {"error": "Thư mục không tồn tại"},
            status_code=404,
        )

    items = []
    try:
        for item in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if item.name.startswith("."):
                continue
            try:
                is_dir = item.is_dir()
                size = item.stat().st_size if not is_dir else 0
                items.append({
                    "name": item.name,
                    "path": str(item),
                    "is_dir": is_dir,
                    "size": size,
                })
            except (PermissionError, OSError):
                continue
    except PermissionError:
        return JSONResponse({"error": "Không có quyền truy cập"}, status_code=403)

    parent = str(p.parent) if p.parent != p else ""
    return {"current": str(p), "parent": parent, "items": items}


# ─── API: Scan ────────────────────────────────────────

class ScanRequest(BaseModel):
    path: str
    level: str = "lv1"
    contest_name: str = ""
    start_index: int = 1


@app.post("/api/scan")
async def scan_directory(req: ScanRequest):
    """Scan a directory for problem subfolders."""
    try:
        results = scan_problems_dir(
            root=Path(req.path),
            level=req.level,
            contest_name=req.contest_name,
            start_index=req.start_index,
        )
        state.scan_results = results
        # Reset problems
        state.problems = {}
        return {"status": "ok", "results": results, "count": len(results)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ─── API: Parse ───────────────────────────────────────

@app.post("/api/parse/{idx}")
async def parse_single(idx: int):
    """Parse a single problem with Gemini Vision."""
    if idx >= len(state.scan_results):
        return JSONResponse({"error": "Index không hợp lệ"}, status_code=400)

    scan = state.scan_results[idx]
    file_path = scan.get("selected_file", "")

    if not file_path and scan["problem_files"]:
        file_path = scan["problem_files"][0]["path"]

    if not file_path:
        logger.warning("[parse %d] Không có file đề cho %s", idx, scan["folder"])
        return JSONResponse({"error": "Không có file đề"}, status_code=400)

    gemini_key = state.config.get("gemini_api_key", "")
    if not gemini_key:
        logger.error("[parse %d] Gemini API key chưa được cấu hình!", idx)
        return JSONResponse({"error": "Chưa cấu hình Gemini API key"}, status_code=400)

    logger.info("[parse %d] Bắt đầu parse: %s (file: %s)", idx, scan["folder"], file_path)

    try:
        # Step 1: Load file
        logger.info("[parse %d] Loading file...", idx)
        content = load_file(Path(file_path))
        logger.info(
            "[parse %d] File loaded OK — %d image(s), text: %d chars",
            idx, len(content.images), len(content.text),
        )

        # Step 2: Call Gemini
        gemini_model = state.config.get("gemini_model", "gemini-2.0-flash")
        logger.info("[parse %d] Gọi Gemini API (model: %s)...", idx, gemini_model)
        problem = await parse_problem(content, gemini_key, model=gemini_model)
        logger.info(
            "[parse %d] ✅ Parse thành công — title: %s, %d example(s)",
            idx, problem.title, len(problem.examples),
        )

        # Apply scan metadata
        problem.polygon_name = scan["polygon_name"]
        problem.solution_path = scan.get("solution_path", "")
        problem.tests_dir = scan.get("tests_dir", "")

        state.problems[idx] = {
            "problem": problem.model_dump(),
            "status": "parsed",
            "scan": scan,
        }

        return {"status": "ok", "problem": problem.model_dump()}

    except Exception as e:
        error_detail = traceback.format_exc()
        logger.error("[parse %d] ❌ Lỗi parse: %s\n%s", idx, e, error_detail)

        # Store empty problem on error
        empty = Problem(
            polygon_name=scan["polygon_name"],
            title=f"[Lỗi parse] {scan['folder']}",
            solution_path=scan.get("solution_path", ""),
            tests_dir=scan.get("tests_dir", ""),
        )
        state.problems[idx] = {
            "problem": empty.model_dump(),
            "status": "parse_error",
            "error": str(e),
            "scan": scan,
        }
        return JSONResponse(
            {"status": "error", "error": str(e), "problem": empty.model_dump()},
            status_code=200,
        )


# Delay giữa các lần gọi Gemini khi parse batch (giây)
# Flash Lite free tier bị throttle nặng — 5s đủ để tránh 429 liên tiếp
INTER_PARSE_DELAY = 5.0


@app.post("/api/parse-all")
async def parse_all():
    """Parse all scanned problems sequentially with delay between calls."""
    if not state.scan_results:
        return JSONResponse({"error": "Chưa scan thư mục"}, status_code=400)

    valid = [
        (i, s) for i, s in enumerate(state.scan_results) if s["status"] != "error"
    ]
    results = [
        {"index": i, "status": "skipped", "reason": s["warning"]}
        for i, s in enumerate(state.scan_results) if s["status"] == "error"
    ]

    for call_idx, (idx, scan) in enumerate(valid):
        # Delay before every call except the first one to avoid Gemini rate limits
        if call_idx > 0:
            logger.info(
                "[parse-all] Đợi %.1fs trước khi parse bài tiếp theo (%d/%d)...",
                INTER_PARSE_DELAY, call_idx + 1, len(valid),
            )
            await asyncio.sleep(INTER_PARSE_DELAY)

        resp = await parse_single(idx)
        if isinstance(resp, JSONResponse):
            body = json.loads(resp.body.decode())
            results.append({"index": idx, **body})
        else:
            results.append({"index": idx, **resp})

    results.sort(key=lambda r: r["index"])
    return {"status": "ok", "results": results}


# ─── API: Problems CRUD ──────────────────────────────

@app.get("/api/problems")
async def list_problems():
    """List all parsed problems."""
    return {
        "problems": [
            {"index": idx, **data}
            for idx, data in sorted(state.problems.items())
        ],
        "total": len(state.problems),
    }


@app.get("/api/problems/{idx}")
async def get_problem(idx: int):
    """Get a single problem by index."""
    if idx not in state.problems:
        return JSONResponse({"error": "Problem không tồn tại"}, status_code=404)
    return state.problems[idx]


class UpdateProblemRequest(BaseModel):
    polygon_name: Optional[str] = None
    title: Optional[str] = None
    time_limit: Optional[int] = None
    memory_limit: Optional[int] = None
    checker: Optional[str] = None
    statement: Optional[str] = None
    input_format: Optional[str] = None
    output_format: Optional[str] = None
    notes: Optional[str] = None
    examples: Optional[list[dict]] = None
    status: Optional[str] = None


@app.put("/api/problems/{idx}")
async def update_problem(idx: int, req: UpdateProblemRequest):
    """Update problem data after user edits in preview."""
    if idx not in state.problems:
        return JSONResponse({"error": "Problem không tồn tại"}, status_code=404)

    entry = state.problems[idx]
    prob = entry["problem"]

    # Update only provided fields
    updates = req.model_dump(exclude_none=True)
    status_update = updates.pop("status", None)

    if "examples" in updates:
        updates["examples"] = [
            {"input": ex.get("input", ""), "output": ex.get("output", "")}
            for ex in updates["examples"]
        ]

    prob.update(updates)

    if status_update:
        entry["status"] = status_update

    # Validate the updated problem
    try:
        Problem(**prob)
    except Exception as e:
        return JSONResponse({"error": f"Validation error: {e}"}, status_code=400)

    return {"status": "ok", "problem": prob}


# ─── API: Upload ──────────────────────────────────────

class UploadRequest(BaseModel):
    indices: Optional[list[int]] = None  # None = upload all confirmed


@app.post("/api/upload")
async def start_upload(req: UploadRequest):
    """Start uploading problems to Polygon (runs in background)."""
    if state.upload_running:
        return JSONResponse({"error": "Upload đang chạy"}, status_code=409)

    polygon_key = state.config.get("polygon_api_key", "")
    polygon_secret = state.config.get("polygon_secret", "")
    if not polygon_key or not polygon_secret:
        return JSONResponse(
            {"error": "Chưa cấu hình Polygon API keys"},
            status_code=400,
        )

    # Determine which problems to upload
    if req.indices is not None:
        indices = req.indices
    else:
        indices = [
            idx for idx, data in state.problems.items()
            if data["status"] in ("confirmed", "parsed")
        ]

    if not indices:
        return JSONResponse({"error": "Không có bài nào để upload"}, status_code=400)

    state.reset_for_upload()
    state.upload_running = True

    # Launch background task
    asyncio.create_task(_upload_task(indices, polygon_key, polygon_secret))

    return {
        "status": "ok",
        "message": f"Bắt đầu upload {len(indices)} bài",
        "count": len(indices),
    }


async def _upload_task(indices: list[int], api_key: str, secret: str):
    """Background task that uploads problems sequentially."""
    client = PolygonClient(api_key, secret)
    lang = state.config.get("lang", "vietnamese")
    total = len(indices)

    try:
        await state.log_queue.put({
            "type": "start",
            "total": total,
            "indices": indices,
        })

        for progress_idx, idx in enumerate(indices):
            entry = state.problems.get(idx)
            if not entry:
                continue

            prob_data = entry["problem"]
            problem = Problem(**prob_data)

            await state.log_queue.put({
                "type": "problem_start",
                "index": idx,
                "progress": progress_idx,
                "total": total,
                "name": problem.polygon_name,
            })

            try:
                async def on_log(msg: str):
                    await state.log_queue.put({
                        "type": "log",
                        "index": idx,
                        "message": msg,
                    })

                result = await upload_problem(client, problem, lang=lang, on_log=on_log)

                entry["status"] = "uploaded"
                await state.log_queue.put({
                    "type": "problem_done",
                    "index": idx,
                    "progress": progress_idx + 1,
                    "total": total,
                    "result": result,
                })

            except Exception as e:
                entry["status"] = "upload_error"
                await state.log_queue.put({
                    "type": "problem_error",
                    "index": idx,
                    "progress": progress_idx + 1,
                    "total": total,
                    "error": str(e),
                })

        await state.log_queue.put({"type": "done", "total": total})

    except Exception as e:
        await state.log_queue.put({"type": "fatal_error", "error": str(e)})
    finally:
        state.upload_running = False
        await client.close()


# ─── API: SSE Status Stream ──────────────────────────

@app.get("/api/status")
async def sse_status():
    """Server-Sent Events stream for real-time upload progress."""

    async def event_generator():
        while True:
            try:
                data = await asyncio.wait_for(
                    state.log_queue.get(), timeout=30.0
                )
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

                if data.get("type") in ("done", "fatal_error"):
                    break
            except asyncio.TimeoutError:
                # Send keepalive
                yield f": keepalive\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )