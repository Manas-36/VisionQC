"""
VisionQC - central inspection hub.

Run with:  python -m uvicorn app:app --host 0.0.0.0 --port 8000

Design (matches database.py / vision.py / diagnostics.py):
  - Admin adds MACHINES (a line/station, holds the error tolerance %).
  - Admin adds CAMERAS and assigns each to a machine. Each camera gets its
    own api_key (a short token) -- that's what the camera/node script sends
    with every photo. A camera can be reassigned to a different machine,
    or unassigned and re-added, at any time from the dashboard.
  - Each CAMERA holds its own reference ("golden sample") photo, because
    two cameras -- even watching the same machine -- sit at different
    distances/angles and see different pixel sizes for an identical part.
  - Every inspection compares a new photo against that camera's reference
    and scores it as a % error, checked against its machine's tolerance.
"""

import asyncio
import base64
import hmac
import logging
import socket
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import os
import secrets
import json

import database as db
import vision
import diagnostics
from config import (IMAGE_DIR, CAMERA_OFFLINE_TIMEOUT_SEC, TREND_WINDOW_SIZE, DEFAULT_MAX_ERROR_PERCENT,
                    ADMIN_PASSWORD, MAX_UPLOAD_BYTES, RETENTION_DAYS_PASS, RETENTION_DAYS_FAIL)

log = logging.getLogger("visionqc")
db.init_db()

_online_state = {}          # camera_id -> was online at last check


async def check_offline(now=None):
    """Broadcast a 'camera_offline' event the moment a camera that was online stops reporting."""
    now = now or time.time()
    for c in db.list_cameras():
        online = bool(c["last_seen"]) and (now - c["last_seen"]) < CAMERA_OFFLINE_TIMEOUT_SEC
        was = _online_state.get(c["id"])
        _online_state[c["id"]] = online
        if was and not online:
            machine = db.get_machine(c["machine_id"]) if c["machine_id"] else None
            await manager.broadcast({"type": "camera_offline", "camera_id": c["id"], "camera_name": c["name"],
                                     "machine_name": machine["name"] if machine else None})
            await manager.broadcast({"type": "state_changed"})


async def _background_jobs():
    last_prune = 0.0
    while True:
        try:
            await check_offline()
            if time.time() - last_prune > 6 * 3600:
                last_prune = time.time()
                n = await run_in_threadpool(db.prune_images, RETENTION_DAYS_PASS, RETENTION_DAYS_FAIL, IMAGE_DIR)
                if n:
                    log.info("Retention: removed %d old image files", n)
        except Exception:
            log.exception("background job failed")
        await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(_app):
    if not ADMIN_PASSWORD:
        log.warning("VISIONQC_ADMIN_PASSWORD is not set: the dashboard and admin API are OPEN to anyone "
                    "on the network. Fine for a closed demo; set it before real use.")
    task = asyncio.create_task(_background_jobs())
    yield
    task.cancel()


app = FastAPI(title="VisionQC Hub", version="1.1.0", lifespan=lifespan)

# ---------------------------------------------------------------- security
# Cameras use their api_key (device endpoints + /api/inspect). Everything else
# (dashboard, admin API, stored images) needs the admin password when one is set.
_PUBLIC_PREFIXES = ("/api/device/", "/api/inspect", "/health")


def _basic_auth_ok(header: Optional[str]) -> bool:
    if not ADMIN_PASSWORD:
        return True
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        supplied = base64.b64decode(header[6:]).decode("utf-8", "replace").split(":", 1)[1]
    except Exception:
        return False
    return hmac.compare_digest(supplied.encode(), ADMIN_PASSWORD.encode())


@app.middleware("http")
async def require_admin(request: Request, call_next):
    if request.url.path.startswith(_PUBLIC_PREFIXES) or _basic_auth_ok(request.headers.get("authorization")):
        return await call_next(request)
    return Response("Admin password required", status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="VisionQC"'})


@app.get("/health")
def health():
    return {"ok": True}


def _hub_lan_ip() -> str:
    """The laptop/server's IP on the interface that reaches the network (no packet is sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


@app.get("/api/hub-info")
def hub_info(request: Request):
    port = request.url.port or 8000
    return {"lan_ip": _hub_lan_ip(), "port": port, "camera_hub_url": f"http://{_hub_lan_ip()}:{port}",
            "auth_enabled": ADMIN_PASSWORD is not None}


async def _read_upload(image: UploadFile) -> bytes:
    data = await image.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Image larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    return data


class ConnectionManager:
    def __init__(self):
        self.active = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, payload: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    if not _basic_auth_ok(websocket.headers.get("authorization")):
        await websocket.close(code=1008)
        return
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


def _save_image(prefix: str, jpeg_bytes: bytes) -> str:
    filename = f"{prefix}_{int(time.time() * 1000)}.jpg"
    with open(os.path.join(IMAGE_DIR, filename), "wb") as f:
        f.write(jpeg_bytes)
    return filename


def _camera_public(c: dict, now: float) -> dict:
    c = dict(c)
    c["online"] = (now - c["last_seen"]) < CAMERA_OFFLINE_TIMEOUT_SEC if c["last_seen"] else False
    c["has_reference"] = c.get("reference_image_path") is not None
    c["has_preview"] = c.get("preview_image_path") is not None
    c["auto_enabled"] = bool(c.get("auto_enabled"))
    c["latest"] = db.latest_inspection(c["id"])
    c["mm_per_px"] = (c["nominal_width_mm"] / c["reference_width_px"]
                      if c.get("nominal_width_mm") and c.get("reference_width_px") else None)
    c["capability"] = _capability(c, 30)
    c["stats"] = db.camera_stats(c["id"])
    return c


# ==========================================================================
# MACHINES
# ==========================================================================
@app.post("/api/machines")
async def create_machine(name: str = Form(...), location: str = Form(""),
                          max_error_percent: float = Form(DEFAULT_MAX_ERROR_PERCENT)):
    machine_id = db.create_machine(name, location, max_error_percent)
    await manager.broadcast({"type": "state_changed"})
    return {"id": machine_id}


@app.patch("/api/machines/{machine_id}")
async def edit_machine(machine_id: int, name: Optional[str] = Form(None), location: Optional[str] = Form(None),
                        max_error_percent: Optional[float] = Form(None)):
    machine = db.get_machine(machine_id)
    if machine is None:
        raise HTTPException(404, "Machine not found")
    db.update_machine(
        machine_id,
        name if name is not None else machine["name"],
        location if location is not None else machine["location"],
        max_error_percent if max_error_percent is not None else machine["max_error_percent"],
    )
    await manager.broadcast({"type": "state_changed"})
    return {"ok": True}


@app.delete("/api/machines/{machine_id}")
async def remove_machine(machine_id: int):
    if db.get_machine(machine_id) is None:
        raise HTTPException(404, "Machine not found")
    db.delete_machine(machine_id)
    await manager.broadcast({"type": "state_changed"})
    return {"ok": True}


def _worst_error(r):
    we, he = r["width_error_pct"], r["height_error_pct"]
    if we is None and he is None:
        return None
    return we if abs(we or 0) >= abs(he or 0) else he


def _capability(camera: dict, n: int) -> dict:
    """Spread / Cpk of the camera's last n readings vs its machine tolerance."""
    machine = db.get_machine(camera["machine_id"]) if camera.get("machine_id") else None
    tol = machine["max_error_percent"] if machine else DEFAULT_MAX_ERROR_PERCENT
    rows = [r for r in db.recent_inspections(camera["id"], n) if r["result"] in ("PASS", "FAIL")]
    out = {"tolerance_pct": tol}
    for key, col in (("width", "width_error_pct"), ("height", "height_error_pct")):
        out[key] = diagnostics.process_capability([r[col] for r in rows if r[col] is not None], tol)
    cpks = [v["cpk"] for v in (out["width"], out["height"]) if v["cpk"] is not None]
    out["cpk"] = min(cpks) if cpks else None
    return out


@app.get("/api/machines")
def api_machines():
    now = time.time()
    out = []
    for m in db.list_machines():
        cameras = [_camera_public(c, now) for c in db.list_cameras(m["id"])]
        out.append({**m, "cameras": cameras})
    return out


# ==========================================================================
# CAMERAS
# ==========================================================================
@app.post("/api/cameras")
async def create_camera(name: str = Form(...), machine_id: Optional[int] = Form(None)):
    api_key = "CAM-" + secrets.token_hex(6).upper()     # 48 bits: not guessable on a LAN
    camera_id = db.create_camera(name, machine_id, api_key)
    await manager.broadcast({"type": "state_changed"})
    return {"id": camera_id, "api_key": api_key}


@app.patch("/api/cameras/{camera_id}")
async def edit_camera(camera_id: int, name: Optional[str] = Form(None), machine_id: Optional[str] = Form(None)):
    camera = db.get_camera(camera_id)
    if camera is None:
        raise HTTPException(404, "Camera not found")
    new_machine_id = camera["machine_id"]
    if machine_id is not None:
        new_machine_id = None if machine_id in ("", "null", "none") else int(machine_id)
    db.update_camera(camera_id, name if name is not None else camera["name"], new_machine_id)
    await manager.broadcast({"type": "state_changed"})
    return {"ok": True}


@app.delete("/api/cameras/{camera_id}")
async def remove_camera(camera_id: int):
    if db.get_camera(camera_id) is None:
        raise HTTPException(404, "Camera not found")
    db.delete_camera(camera_id)
    await manager.broadcast({"type": "state_changed"})
    return {"ok": True}


@app.post("/api/cameras/{camera_id}/reference")
async def set_reference(camera_id: int, image: UploadFile = File(...),
                        width_mm: Optional[float] = Form(None), height_mm: Optional[float] = Form(None)):
    camera = db.get_camera(camera_id)
    if camera is None:
        raise HTTPException(404, "Camera not found")

    image_bytes = await _read_upload(image)
    result = await run_in_threadpool(vision.measure_from_bytes, image_bytes)
    if not result.ok:
        raise HTTPException(422, f"Could not measure the reference part: {result.error}")

    filename = _save_image(f"REF_C{camera_id}", result.annotated_jpeg or image_bytes)
    db.set_camera_reference(camera_id, filename, result.width_px, result.height_px)
    db.set_camera_nominal(camera_id, width_mm, height_mm)
    await manager.broadcast({"type": "state_changed"})
    return {"ok": True, "width_px": result.width_px, "height_px": result.height_px,
            "image_url": f"/api/images/{filename}"}


@app.delete("/api/cameras/{camera_id}/reference")
async def clear_reference(camera_id: int):
    if db.get_camera(camera_id) is None:
        raise HTTPException(404, "Camera not found")
    db.clear_camera_reference(camera_id)
    await manager.broadcast({"type": "state_changed"})
    return {"ok": True}


@app.get("/api/cameras")
def api_cameras():
    now = time.time()
    return [_camera_public(c, now) for c in db.list_cameras()]


@app.get("/api/cameras/{camera_id}/history")
def api_camera_history(camera_id: int, limit: int = 25):
    if db.get_camera(camera_id) is None:
        raise HTTPException(404, "Unknown camera")
    return db.recent_inspections(camera_id, limit)


# ==========================================================================
# CORE INSPECTION ENDPOINT -- called by node_client.py / ESP32-CAM firmware
# ==========================================================================
async def _run_inspection(camera: dict, image_bytes: bytes) -> dict:
    """Measure one photo against the camera's reference, store it, broadcast it."""
    machine = db.get_machine(camera["machine_id"])
    result = await run_in_threadpool(vision.measure_from_bytes, image_bytes)
    if result.code == "no_part":
        # Empty frame (timer fired between parts, sensor false-trigger...). That is not a
        # failed part, so it is neither stored nor alerted.
        return {"type": "no_part", "result": "NO_PART", "camera_id": camera["id"],
                "message": result.error}

    raw_history = db.recent_inspections(camera["id"], TREND_WINDOW_SIZE)
    history = []
    for r in raw_history:
        we, he = r["width_error_pct"], r["height_error_pct"]
        if we is None and he is None:
            continue
        history.append(we if abs(we or 0) >= abs(he or 0) else he)

    diag = diagnostics.diagnose(
        width_px=result.width_px, height_px=result.height_px,
        ref_width_px=camera["reference_width_px"], ref_height_px=camera["reference_height_px"],
        max_error_percent=machine["max_error_percent"],
        history_error_pct=history,
        vision_error=result.error if not result.ok else None,
    )

    filename = _save_image(f"C{camera['id']}", result.annotated_jpeg or image_bytes)

    mm_per_px = (camera["nominal_width_mm"] / camera["reference_width_px"]
                 if camera.get("nominal_width_mm") and camera.get("reference_width_px") else None)
    width_mm = round(result.width_px * mm_per_px, 3) if (mm_per_px and result.width_px) else None
    height_mm = (round(result.height_px * (camera["nominal_height_mm"] / camera["reference_height_px"]), 3)
                 if camera.get("nominal_height_mm") and camera.get("reference_height_px") and result.height_px else None)

    row_id = db.insert_inspection(
        width_mm=width_mm, height_mm=height_mm,
        camera_id=camera["id"], machine_id=machine["id"],
        width_px=result.width_px, height_px=result.height_px,
        width_error_pct=diag.width_error_pct, height_error_pct=diag.height_error_pct,
        error_pct=diag.error_pct, result=diag.result, diagnosis=diag.diagnosis,
        recommendation=diag.recommendation, severity=diag.severity, image_path=filename,
    )

    payload = {
        "type": "inspection", "id": row_id,
        "machine_id": machine["id"], "machine_name": machine["name"],
        "camera_id": camera["id"], "camera_name": camera["name"],
        "ts": time.time(),
        "width_px": result.width_px, "height_px": result.height_px,
        "width_mm": width_mm, "height_mm": height_mm,
        "width_error_pct": diag.width_error_pct, "height_error_pct": diag.height_error_pct,
        "error_pct": diag.error_pct, "max_error_percent": machine["max_error_percent"],
        "result": diag.result, "diagnosis": diag.diagnosis,
        "recommendation": diag.recommendation, "severity": diag.severity,
        "image_url": f"/api/images/{filename}",
    }
    await manager.broadcast(payload)
    return payload


@app.post("/api/inspect")
async def inspect(api_key: str = Form(...), image: UploadFile = File(...)):
    camera = db.get_camera_by_api_key(api_key)
    if camera is None:
        raise HTTPException(404, "Unknown camera api_key. Register a camera first from the dashboard's Admin panel.")
    db.touch_camera(camera["id"])

    if camera["machine_id"] is None:
        raise HTTPException(409, f"Camera '{camera['name']}' is not assigned to any machine yet. "
                                  f"Assign it from the dashboard's Admin panel.")
    return JSONResponse(await _run_inspection(camera, await _read_upload(image)))


# ==========================================================================
# DEVICE CONTROL -- used by the XIAO ESP32S3 Sense (and any other wireless camera)
#
# The camera sits on the factory WiFi and the hub can't "call" into it, so the
# camera calls the hub: every ~2 s it asks  GET /api/device/<key>/poll  ("any
# orders for me?"). The dashboard's buttons only leave an order on the hub;
# the camera picks it up on its next poll.
# ==========================================================================
@app.get("/api/device/{api_key}/poll")
async def device_poll(api_key: str):
    camera = db.get_camera_by_api_key(api_key)
    if camera is None:
        raise HTTPException(404, "Unknown api_key")
    was_offline = not camera["last_seen"] or (time.time() - camera["last_seen"]) >= CAMERA_OFFLINE_TIMEOUT_SEC
    db.touch_camera(camera["id"])
    command = camera.get("pending_command") or "none"
    if command != "none":
        db.set_pending_command(camera["id"], None)       # order is consumed
    if was_offline:
        await manager.broadcast({"type": "state_changed"})   # flip card to ONLINE
    return {
        "command": command,                               # "capture" | "none"
        "name": camera["name"],
        "assigned": camera["machine_id"] is not None,
        "has_reference": camera.get("reference_image_path") is not None,
        "auto_enabled": bool(camera.get("auto_enabled")),
        "auto_interval_sec": camera.get("auto_interval_sec") or 10,
        "mode": camera.get("trigger_mode") or "timer",   # "timer" | "sensor" (photo-eye triggered)
    }


@app.post("/api/device/{api_key}/photo")
async def device_photo(api_key: str, image: UploadFile = File(...)):
    """Photo taken because the admin pressed 'Capture photo'. It is NOT scored;
    it becomes a preview the admin can accept as the reference."""
    camera = db.get_camera_by_api_key(api_key)
    if camera is None:
        raise HTTPException(404, "Unknown api_key")
    db.touch_camera(camera["id"])
    data = await _read_upload(image)
    if len(data) < 500:
        raise HTTPException(400, "Empty image")
    filename = _save_image(f"PREV_C{camera['id']}", data)
    db.set_preview(camera["id"], filename)
    await manager.broadcast({"type": "preview", "camera_id": camera["id"], "camera_name": camera["name"],
                             "image_url": f"/api/images/{filename}"})
    await manager.broadcast({"type": "state_changed"})
    return {"ok": True}


@app.post("/api/cameras/{camera_id}/capture")
async def request_capture(camera_id: int):
    """Dashboard 'Capture photo' button: leave an order for the camera."""
    camera = db.get_camera(camera_id)
    if camera is None:
        raise HTTPException(404, "Camera not found")
    db.set_pending_command(camera_id, "capture")
    return {"ok": True}


@app.post("/api/cameras/{camera_id}/preview/use-as-reference")
async def preview_as_reference(camera_id: int, width_mm: Optional[float] = Form(None),
                               height_mm: Optional[float] = Form(None)):
    camera = db.get_camera(camera_id)
    if camera is None or not camera.get("preview_image_path"):
        raise HTTPException(404, "No preview photo for this camera yet")
    with open(os.path.join(IMAGE_DIR, camera["preview_image_path"]), "rb") as f:
        data = f.read()
    result = await run_in_threadpool(vision.measure_from_bytes, data)
    if not result.ok:
        raise HTTPException(422, f"Could not measure the part in this photo: {result.error}")
    filename = _save_image(f"REF_C{camera_id}", result.annotated_jpeg or data)
    db.set_camera_reference(camera_id, filename, result.width_px, result.height_px)
    db.set_camera_nominal(camera_id, width_mm, height_mm)
    db.clear_preview(camera_id)
    await manager.broadcast({"type": "state_changed"})
    return {"ok": True, "width_px": result.width_px, "height_px": result.height_px,
            "image_url": f"/api/images/{filename}"}


@app.post("/api/cameras/{camera_id}/preview/test")
async def preview_test(camera_id: int):
    """Score the preview photo against the reference (a one-off test)."""
    camera = db.get_camera(camera_id)
    if camera is None or not camera.get("preview_image_path"):
        raise HTTPException(404, "No preview photo for this camera yet")
    if camera["machine_id"] is None:
        raise HTTPException(409, "Assign this camera to a machine first")
    if camera["reference_width_px"] is None:
        raise HTTPException(409, "Set a reference first")
    with open(os.path.join(IMAGE_DIR, camera["preview_image_path"]), "rb") as f:
        data = f.read()
    payload = await _run_inspection(camera, data)
    db.clear_preview(camera_id)
    await manager.broadcast({"type": "state_changed"})
    return payload


@app.delete("/api/cameras/{camera_id}/preview")
async def discard_preview(camera_id: int):
    if db.get_camera(camera_id) is None:
        raise HTTPException(404, "Camera not found")
    db.clear_preview(camera_id)
    await manager.broadcast({"type": "state_changed"})
    return {"ok": True}


@app.post("/api/cameras/{camera_id}/auto")
async def set_auto_test(camera_id: int, enabled: bool = Form(...), interval_sec: int = Form(10),
                        mode: str = Form("timer")):
    """Dashboard timer: camera takes + sends a test photo every N seconds."""
    if db.get_camera(camera_id) is None:
        raise HTTPException(404, "Camera not found")
    interval_sec = max(2, min(interval_sec, 3600))
    if mode not in ("timer", "sensor"):
        raise HTTPException(422, "mode must be 'timer' or 'sensor'")
    db.set_auto(camera_id, enabled, interval_sec, mode)
    await manager.broadcast({"type": "state_changed"})
    return {"ok": True, "enabled": enabled, "interval_sec": interval_sec, "mode": mode}


@app.get("/api/cameras/{camera_id}/capability")
def api_capability(camera_id: int, n: int = 30):
    """Spread and Cpk of the last n readings. With the SAME part left untouched under the camera
    this is a repeatability test of the gauge: if 6 sigma uses more than ~30 % of the tolerance band,
    the camera set-up is not good enough to enforce that tolerance."""
    camera = db.get_camera(camera_id)
    if camera is None:
        raise HTTPException(404, "Camera not found")
    return _capability(camera, max(5, min(n, 500)))


@app.get("/api/feed")
def api_feed(limit: int = 50):
    return db.all_recent_inspections(limit)


@app.get("/api/images/{filename}")
def api_image(filename: str):
    if filename != os.path.basename(filename) or "\\" in filename or ".." in filename:
        raise HTTPException(404, "Image not found")           # no path traversal (esp. on Windows)
    path = os.path.join(IMAGE_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(404, "Image not found")
    return FileResponse(path, media_type="image/jpeg")


STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "dashboard.html"))


@app.get("/capture")
def capture_page():
    """Phone-friendly page: use a phone's own camera as an inspection camera,
    no app install needed -- just open this URL in the phone's browser while
    on the same WiFi network as the hub."""
    return FileResponse(os.path.join(STATIC_DIR, "capture.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
