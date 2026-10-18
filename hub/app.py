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

import time
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import os
import secrets
import json

import database as db
import vision
import diagnostics
from config import IMAGE_DIR, CAMERA_OFFLINE_TIMEOUT_SEC, TREND_WINDOW_SIZE, DEFAULT_MAX_ERROR_PERCENT

app = FastAPI(title="VisionQC Hub", version="1.0.0")
db.init_db()


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
    api_key = "CAM-" + secrets.token_hex(3).upper()
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
async def set_reference(camera_id: int, image: UploadFile = File(...)):
    camera = db.get_camera(camera_id)
    if camera is None:
        raise HTTPException(404, "Camera not found")

    image_bytes = await image.read()
    result = vision.measure_from_bytes(image_bytes)
    if not result.ok:
        raise HTTPException(422, f"Could not measure the reference part: {result.error}")

    filename = _save_image(f"REF_C{camera_id}", result.annotated_jpeg or image_bytes)
    db.set_camera_reference(camera_id, filename, result.width_px, result.height_px)
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
    result = vision.measure_from_bytes(image_bytes)

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

    row_id = db.insert_inspection(
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
    return JSONResponse(await _run_inspection(camera, await image.read()))


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
    }


@app.post("/api/device/{api_key}/photo")
async def device_photo(api_key: str, image: UploadFile = File(...)):
    """Photo taken because the admin pressed 'Capture photo'. It is NOT scored;
    it becomes a preview the admin can accept as the reference."""
    camera = db.get_camera_by_api_key(api_key)
    if camera is None:
        raise HTTPException(404, "Unknown api_key")
    db.touch_camera(camera["id"])
    data = await image.read()
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
async def preview_as_reference(camera_id: int):
    camera = db.get_camera(camera_id)
    if camera is None or not camera.get("preview_image_path"):
        raise HTTPException(404, "No preview photo for this camera yet")
    with open(os.path.join(IMAGE_DIR, camera["preview_image_path"]), "rb") as f:
        data = f.read()
    result = vision.measure_from_bytes(data)
    if not result.ok:
        raise HTTPException(422, f"Could not measure the part in this photo: {result.error}")
    filename = _save_image(f"REF_C{camera_id}", result.annotated_jpeg or data)
    db.set_camera_reference(camera_id, filename, result.width_px, result.height_px)
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
async def set_auto_test(camera_id: int, enabled: bool = Form(...), interval_sec: int = Form(10)):
    """Dashboard timer: camera takes + sends a test photo every N seconds."""
    if db.get_camera(camera_id) is None:
        raise HTTPException(404, "Camera not found")
    interval_sec = max(2, min(interval_sec, 3600))
    db.set_auto(camera_id, enabled, interval_sec)
    await manager.broadcast({"type": "state_changed"})
    return {"ok": True, "enabled": enabled, "interval_sec": interval_sec}


@app.get("/api/feed")
def api_feed(limit: int = 50):
    return db.all_recent_inspections(limit)


@app.get("/api/images/{filename}")
def api_image(filename: str):
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
