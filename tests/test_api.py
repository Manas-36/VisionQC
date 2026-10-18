from conftest import make_part


def setup_camera(client, assign=True):
    m = client.post("/api/machines", data={"name": "Press 1", "max_error_percent": 3}).json()["id"]
    cam = client.post("/api/cameras", data={"name": "Cam A", **({"machine_id": m} if assign else {})}).json()
    return m, cam["id"], cam["api_key"]


def photo(w=220, h=140):
    return {"image": ("p.jpg", make_part(w, h), "image/jpeg")}


def test_dashboard_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "VISIONQC" in r.text


def test_unknown_api_key_rejected(client):
    assert client.post("/api/inspect", data={"api_key": "CAM-NOPE"}, files=photo()).status_code == 404


def test_unassigned_camera_rejected(client):
    _, _, key = setup_camera(client, assign=False)
    assert client.post("/api/inspect", data={"api_key": key}, files=photo()).status_code == 409


def test_reference_then_inspection(client):
    _, cam_id, key = setup_camera(client)
    assert client.post(f"/api/cameras/{cam_id}/reference", files=photo(220, 140)).status_code == 200
    ok = client.post("/api/inspect", data={"api_key": key}, files=photo(222, 141)).json()
    assert ok["result"] == "PASS"
    bad = client.post("/api/inspect", data={"api_key": key}, files=photo(260, 140)).json()
    assert bad["result"] == "FAIL" and bad["error_pct"] > 3
    assert len(client.get(f"/api/cameras/{cam_id}/history").json()) == 2


def test_capture_preview_use_as_reference_flow(client):
    """Admin presses Capture -> device polls -> uploads preview -> admin accepts it as reference."""
    _, cam_id, key = setup_camera(client)
    assert client.get(f"/api/device/{key}/poll").json()["command"] == "none"
    client.post(f"/api/cameras/{cam_id}/capture")
    assert client.get(f"/api/device/{key}/poll").json()["command"] == "capture"
    assert client.get(f"/api/device/{key}/poll").json()["command"] == "none"      # order consumed once
    assert client.post(f"/api/device/{key}/photo", files=photo()).status_code == 200
    assert client.post(f"/api/cameras/{cam_id}/preview/use-as-reference").json()["ok"]
    assert client.get(f"/api/device/{key}/poll").json()["has_reference"] is True


def test_auto_test_settings_reach_device(client):
    _, cam_id, key = setup_camera(client)
    client.post(f"/api/cameras/{cam_id}/auto", data={"enabled": "true", "interval_sec": 7})
    p = client.get(f"/api/device/{key}/poll").json()
    assert p["auto_enabled"] is True and p["auto_interval_sec"] == 7
