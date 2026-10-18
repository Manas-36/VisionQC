import base64
import os
import time

import diagnostics
from conftest import make_part, empty_frame
from test_api import setup_camera, photo


# ------------------------------------------------------------------ inspection logic
def test_empty_frame_is_not_a_failed_part(client):
    _, cam_id, key = setup_camera(client)
    client.post(f"/api/cameras/{cam_id}/reference", files=photo())
    r = client.post("/api/inspect", data={"api_key": key}, files={"image": ("e.jpg", empty_frame(), "image/jpeg")})
    assert r.status_code == 200 and r.json()["result"] == "NO_PART"
    assert client.get(f"/api/cameras/{cam_id}/history").json() == []       # nothing stored, no alert


def test_millimetres_from_nominal_size(client):
    _, cam_id, key = setup_camera(client)
    client.post(f"/api/cameras/{cam_id}/reference", files=photo(220, 140), data={"width_mm": 110, "height_mm": 70})
    r = client.post("/api/inspect", data={"api_key": key}, files=photo(224, 140)).json()
    assert abs(r["width_mm"] - 112) < 1.0 and abs(r["height_mm"] - 70) < 1.0


def test_oversized_upload_rejected(client, monkeypatch):
    _, _, key = setup_camera(client)
    monkeypatch.setattr(client.hub, "MAX_UPLOAD_BYTES", 1000)
    r = client.post("/api/inspect", data={"api_key": key}, files=photo())
    assert r.status_code == 413


def test_sensor_mode_reaches_device(client):
    _, cam_id, key = setup_camera(client)
    client.post(f"/api/cameras/{cam_id}/auto", data={"enabled": "true", "interval_sec": 5, "mode": "sensor"})
    assert client.get(f"/api/device/{key}/poll").json()["mode"] == "sensor"
    assert client.post(f"/api/cameras/{cam_id}/auto", data={"enabled": "true", "mode": "bogus"}).status_code == 422


def test_api_keys_are_long_enough_to_resist_guessing(client):
    _, _, key = setup_camera(client)
    assert len(key.replace("CAM-", "")) >= 12


# ------------------------------------------------------------------ diagnostics
def _d(w, h, hist):
    return diagnostics.diagnose(width_px=w, height_px=h, ref_width_px=200, ref_height_px=100,
                                max_error_percent=3.0, history_error_pct=hist, vision_error=None)


def test_noise_is_not_reported_as_drift():
    d = _d(200.05, 100, [0.02, 0.03, 0.01, 0.04, 0.02, 0.03, 0.01, 0.02])
    assert d.severity == "info" and "trending" not in d.diagnosis


def test_real_drift_is_still_reported():
    d = _d(204.5, 100, [0.9, 1.2, 1.5, 1.8, 2.1, 2.4, 2.7, 2.9])
    assert d.severity == "warning" and "trending" in d.diagnosis


def test_both_dimensions_scaling_points_at_camera():
    d = _d(212, 106, [])                      # +6 % and +6 %
    assert d.result == "FAIL" and "check the camera" in d.recommendation.lower()


def test_single_dimension_fault_does_not_blame_camera():
    d = _d(212, 100, [])
    assert "check the camera" not in d.recommendation.lower()


def test_process_capability_math():
    cap = diagnostics.process_capability([0.1, -0.1] * 10, tol_pct=3.0)
    assert cap["n"] == 20 and cap["cpk"] > 5 and abs(cap["mean"]) < 1e-9
    assert diagnostics.process_capability([1, 2], 3.0)["cpk"] is None     # too few readings


def test_capability_endpoint(client):
    _, cam_id, key = setup_camera(client)
    client.post(f"/api/cameras/{cam_id}/reference", files=photo())
    for w in (220, 221, 220, 219, 220, 221, 220, 219, 220, 221, 220, 220):
        client.post("/api/inspect", data={"api_key": key}, files=photo(w))
    cap = client.get(f"/api/cameras/{cam_id}/capability").json()
    assert cap["width"]["n"] == 12 and cap["cpk"] is not None


# ------------------------------------------------------------------ security
def test_admin_password_protects_dashboard_and_api(secure_client):
    c = secure_client
    assert c.get("/").status_code == 401
    assert c.get("/api/machines").status_code == 401
    ok = {"Authorization": "Basic " + base64.b64encode(b"admin:s3cret").decode()}
    bad = {"Authorization": "Basic " + base64.b64encode(b"admin:nope").decode()}
    assert c.get("/api/machines", headers=ok).status_code == 200
    assert c.get("/api/machines", headers=bad).status_code == 401


def test_cameras_still_work_without_admin_password(secure_client):
    c = secure_client
    ok = {"Authorization": "Basic " + base64.b64encode(b":s3cret").decode()}
    m = c.post("/api/machines", data={"name": "M"}, headers=ok).json()["id"]
    cam = c.post("/api/cameras", data={"name": "C", "machine_id": m}, headers=ok).json()
    assert c.get(f"/api/device/{cam['api_key']}/poll").status_code == 200      # no auth header
    assert c.post("/api/inspect", data={"api_key": cam["api_key"]}, files=photo()).status_code == 200


def test_image_path_traversal_blocked(client):
    for evil in ("..%5C..%5Cetc%5Cpasswd", "..%2F..%2Fetc%2Fpasswd", "%2E%2E"):
        assert client.get(f"/api/images/{evil}").status_code == 404


def test_websocket_requires_admin_password(secure_client):
    import pytest
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with secure_client.websocket_connect("/ws"):
            pass
    hdr = {"Authorization": "Basic " + base64.b64encode(b":s3cret").decode()}
    with secure_client.websocket_connect("/ws", headers=hdr):
        pass


# ------------------------------------------------------------------ housekeeping
def test_retention_deletes_old_pass_images_but_keeps_rows(client):
    _, cam_id, key = setup_camera(client)
    client.post(f"/api/cameras/{cam_id}/reference", files=photo())
    r = client.post("/api/inspect", data={"api_key": key}, files=photo(220)).json()
    fname = r["image_url"].split("/")[-1]
    path = os.path.join(client.hub.IMAGE_DIR, fname)
    assert os.path.exists(path)
    import database as db
    with db.get_conn() as conn:
        conn.execute("UPDATE inspections SET ts = ?", (time.time() - 40 * 86400,))
    assert db.prune_images(30, 180, client.hub.IMAGE_DIR) == 1
    assert not os.path.exists(path)
    assert len(client.get(f"/api/cameras/{cam_id}/history").json()) == 1


def test_offline_event_fires_once(client):
    import asyncio
    _, cam_id, key = setup_camera(client)
    client.get(f"/api/device/{key}/poll")                     # camera comes online
    seen = []

    async def fake_broadcast(msg): seen.append(msg["type"])
    client.hub.manager.broadcast = fake_broadcast
    asyncio.run(client.hub.check_offline(time.time()))        # records "online"
    asyncio.run(client.hub.check_offline(time.time() + 120))  # silent for 2 min
    asyncio.run(client.hub.check_offline(time.time() + 240))
    assert seen.count("camera_offline") == 1


def test_hub_info_reports_an_address(client):
    assert client.get("/api/hub-info").json()["camera_hub_url"].startswith("http://")
