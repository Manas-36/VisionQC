"""Pretends to be a XIAO ESP32S3 Sense (same HTTP protocol as the firmware).
Use to test the hub/dashboard without hardware:
    python tools/simulate_xiao.py --api-key CAM-XXXXXX [--hub http://localhost:8000]
It polls the hub every 2 s; on 'capture' it uploads a preview photo; in auto mode it
sends a photo for inspection every N seconds (width slowly drifts so you see the trend)."""
import argparse, io, time, requests
import numpy as np, cv2

def part_image(w=220, h=140, noise=0):
    img = np.full((480, 640, 3), 235, np.uint8)
    x0, y0 = 320 - int(w) // 2, 240 - int(h) // 2
    cv2.rectangle(img, (x0, y0), (x0 + int(w), y0 + int(h)), (40, 40, 40), -1)
    if noise: img = cv2.add(img, np.random.randint(0, noise, img.shape, dtype=np.uint8))
    return cv2.imencode(".jpg", img)[1].tobytes()

ap = argparse.ArgumentParser()
ap.add_argument("--api-key", required=True); ap.add_argument("--hub", default="http://localhost:8000")
a = ap.parse_args()
last_auto, n, wasauto = 0, 0, False
while True:
    try:
        p = requests.get(f"{a.hub}/api/device/{a.api_key}/poll", timeout=5).json()
    except Exception as e:
        print("hub unreachable:", e); time.sleep(2); continue
    if p["command"] == "capture":
        r = requests.post(f"{a.hub}/api/device/{a.api_key}/photo", files={"image": ("p.jpg", part_image(), "image/jpeg")})
        print("preview sent", r.status_code)
    if p["auto_enabled"] and not wasauto: last_auto = 0
    wasauto = p["auto_enabled"]
    if p["auto_enabled"] and p["assigned"] and p["has_reference"] and time.time() - last_auto >= p["auto_interval_sec"]:
        last_auto = time.time(); n += 1
        w = 220 + n * 1.5                                  # slow drift
        r = requests.post(f"{a.hub}/api/inspect", data={"api_key": a.api_key}, files={"image": ("p.jpg", part_image(w), "image/jpeg")})
        print("auto photo", n, r.status_code, r.json().get("result"), r.json().get("error_pct"))
    time.sleep(2)
