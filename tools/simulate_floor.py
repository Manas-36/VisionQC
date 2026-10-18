"""
Floor simulator -- for demos, testing, and your viva.

Exercises the FULL admin workflow through the real API:
  1. Creates a couple of machines (as if an admin just added them)
  2. Creates a camera for each and assigns it to that machine
  3. Uploads a synthetic "reference part" photo for each camera
  4. Then continuously sends synthetic "current part" photos, comparing
     against that reference -- with a slow size drift injected on one
     camera and occasional random defects on another, so you can watch
     the diagnostics engine catch a "systematic drift" vs. an "isolated
     defect" live on the dashboard.

Run with:  python simulate_floor.py --hub http://localhost:8000
"""

import argparse
import io
import random
import time

import cv2
import numpy as np
import requests

REFERENCE_SIZE_UNITS = 20.0
PIXELS_PER_UNIT = 38.0

SETUPS = {
    "Shaft Line":   {"location": "Bay 1", "max_error_percent": 1.5, "drift_after": 15, "drift_rate_pct": 0.15},
    "Bracket Line": {"location": "Bay 2", "max_error_percent": 2.5, "defect_chance": 0.12},
    "Housing Line": {"location": "Bay 3", "max_error_percent": 1.0, "defect_chance": 0.03},
}

counters = {name: 0 for name in SETUPS}


def make_part_image(size_units, aspect=0.6, pad_factor=1.8, jitter=True):
    w_px = int(size_units * PIXELS_PER_UNIT)
    h_px = int(w_px * aspect)
    canvas = (int(w_px * pad_factor), int(h_px * pad_factor) + 40)
    img = np.full((canvas[1], canvas[0], 3), 235, dtype=np.uint8)
    cx, cy = canvas[0] // 2, canvas[1] // 2
    x0, y0 = cx - w_px // 2, cy - h_px // 2
    x1, y1 = x0 + w_px, y0 + h_px
    cv2.rectangle(img, (x0, y0), (x1, y1), (40, 40, 40), thickness=-1)
    if jitter:
        noise = np.random.randint(-4, 4, img.shape, dtype=np.int16)
        img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return buf.tobytes()


def next_size_units(name):
    cfg = SETUPS[name]
    counters[name] += 1
    n = counters[name]
    base = REFERENCE_SIZE_UNITS
    noise = random.gauss(0, cfg["max_error_percent"] * 0.15) / 100 * base

    if "drift_after" in cfg and n > cfg["drift_after"]:
        base += base * (cfg["drift_rate_pct"] / 100) * (n - cfg["drift_after"])

    if "defect_chance" in cfg and random.random() < cfg["defect_chance"]:
        base += random.choice([-1, 1]) * base * (cfg["max_error_percent"] / 100) * random.uniform(3, 5)

    return base + noise


def setup_floor(hub_url):
    """Creates machines + cameras + reference photos through the real API, as an admin would."""
    api_keys = {}
    for name, cfg in SETUPS.items():
        r = requests.post(f"{hub_url}/api/machines", data={
            "name": name, "location": cfg["location"], "max_error_percent": cfg["max_error_percent"],
        })
        r.raise_for_status()
        machine_id = r.json()["id"]

        r = requests.post(f"{hub_url}/api/cameras", data={"name": f"{name} camera", "machine_id": machine_id})
        r.raise_for_status()
        cam = r.json()
        camera_id, api_key = cam["id"], cam["api_key"]
        api_keys[name] = api_key

        ref_jpeg = make_part_image(REFERENCE_SIZE_UNITS, jitter=False)
        r = requests.post(f"{hub_url}/api/cameras/{camera_id}/reference",
                           files={"image": ("reference.jpg", io.BytesIO(ref_jpeg), "image/jpeg")})
        r.raise_for_status()
        print(f"{name}: machine #{machine_id}, camera #{camera_id} ({api_key}), reference set.")

    return api_keys


def post_inspection(hub_url, api_key, jpeg_bytes):
    r = requests.post(f"{hub_url}/api/inspect", data={"api_key": api_key},
                       files={"image": ("frame.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")}, timeout=8)
    r.raise_for_status()
    return r.json()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub", default="http://localhost:8000")
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()

    print("Setting up a demo floor (machines, cameras, reference parts) through the real API...")
    api_keys = setup_floor(args.hub.rstrip('/'))

    print("\nSimulating production. Ctrl+C to stop.")
    print("Watch the dashboard: Shaft Line will start drifting after ~15 parts, "
          "Bracket Line throws occasional isolated defects.\n")

    while True:
        for name in SETUPS:
            size = next_size_units(name)
            jpeg = make_part_image(size)
            try:
                r = post_inspection(args.hub.rstrip('/'), api_keys[name], jpeg)
                err = r.get('error_pct')
                err_str = f"{err:.2f}%" if err is not None else "  --  "
                print(f"{name}: {err_str} -> {r['result']:5s}  {r['diagnosis']}")
            except requests.RequestException as e:
                print(f"{name}: could not reach hub ({e})")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
