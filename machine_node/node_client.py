"""
Machine Node client -- runs on (or next to) each camera.

This script only needs an api_key (get it from Admin -> Cameras on the
dashboard after adding a camera there, or note it down when you first
create the camera -- it's shown once). Which machine it belongs to, what
tolerance applies, and what the reference photo looks like all live on
the hub and can be changed from the dashboard without touching this
script.

Two things it can do:
  1. Normal inspection capture -> POST /api/inspect
  2. One-off reference capture ("this is what a good part looks like") ->
     POST /api/cameras/{id}/reference  (use --set-reference)

Offline queueing: if the hub is unreachable, frames are saved to disk and
retried automatically -- no inspection data is lost from a network blip.

Run with:
  python node_client.py --api-key CAM-A1B2C3 --hub http://192.168.1.50:8000
  python node_client.py --api-key CAM-A1B2C3 --hub http://192.168.1.50:8000 --set-reference --camera-id 2
"""

import argparse
import io
import os
import time
import glob
from datetime import datetime

import cv2
import requests

QUEUE_DIR = "queued_frames"
os.makedirs(QUEUE_DIR, exist_ok=True)


def capture_frame(cam_index: int):
    cap = cv2.VideoCapture(cam_index)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera index {cam_index}")
    for _ in range(5):   # let auto-exposure settle before the real grab
        cap.read()
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError("Failed to capture frame")
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise RuntimeError("Failed to encode frame")
    return buf.tobytes()


def send_inspection(hub_url: str, api_key: str, jpeg_bytes: bytes, timeout=6) -> bool:
    try:
        resp = requests.post(
            f"{hub_url.rstrip('/')}/api/inspect",
            data={"api_key": api_key},
            files={"image": ("frame.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
            timeout=timeout,
        )
        if resp.status_code == 200:
            r = resp.json()
            err = r.get('error_pct')
            err_str = f"{err:.2f}%" if err is not None else "  --  "
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {err_str} -> {r['result']:5s}  {r.get('diagnosis', '')}")
            return True
        print(f"Hub rejected frame: {resp.status_code} {resp.text}")
        return False
    except requests.RequestException as e:
        print(f"Could not reach hub ({e}) -- queueing frame locally.")
        return False


def send_reference(hub_url: str, camera_id: str, jpeg_bytes: bytes) -> bool:
    resp = requests.post(
        f"{hub_url.rstrip('/')}/api/cameras/{camera_id}/reference",
        files={"image": ("reference.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
        timeout=8,
    )
    if resp.status_code == 200:
        r = resp.json()
        print(f"Reference set for camera {camera_id}: {r['width_px']:.0f} x {r['height_px']:.0f} px")
        return True
    print(f"Hub rejected reference photo: {resp.status_code} {resp.text}")
    return False


def queue_frame(api_key: str, jpeg_bytes: bytes):
    fname = os.path.join(QUEUE_DIR, f"{api_key}_{int(time.time()*1000)}.jpg")
    with open(fname, "wb") as f:
        f.write(jpeg_bytes)


def flush_queue(hub_url: str, api_key: str):
    pending = sorted(glob.glob(os.path.join(QUEUE_DIR, f"{api_key}_*.jpg")))
    if not pending:
        return
    print(f"Flushing {len(pending)} queued frame(s)...")
    for path in pending:
        with open(path, "rb") as f:
            data = f.read()
        if send_inspection(hub_url, api_key, data):
            os.remove(path)
        else:
            break


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", required=True, help="Get this from Admin -> Cameras on the dashboard")
    parser.add_argument("--hub", required=True, help="Hub base URL, e.g. http://192.168.1.50:8000")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--mode", choices=["manual", "interval"], default="manual",
                         help="'manual' = press Enter to inspect a part; 'interval' = auto-capture every --interval seconds")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--set-reference", action="store_true",
                         help="One-off: capture a single frame right now and upload it as this camera's "
                              "reference (master part) photo, then exit. Requires --camera-id.")
    parser.add_argument("--camera-id", help="Numeric camera ID (shown in Admin -> Cameras), needed with --set-reference")
    args = parser.parse_args()

    if args.set_reference:
        if not args.camera_id:
            print("--set-reference needs --camera-id (the numeric ID shown in Admin -> Cameras).")
            return
        print("Place the known-good reference part in view, then press Enter to capture it...")
        input()
        jpeg = capture_frame(args.camera_index)
        send_reference(args.hub, args.camera_id, jpeg)
        return

    print(f"Camera key '{args.api_key}' -> Hub {args.hub}")
    print(f"Mode: {args.mode}" + (f" (every {args.interval}s)" if args.mode == "interval" else " (press Enter to inspect, Ctrl+C to quit)"))

    while True:
        try:
            flush_queue(args.hub, args.api_key)
            if args.mode == "manual":
                input("\nPlace part in view, then press Enter to inspect...")
            else:
                time.sleep(args.interval)

            jpeg = capture_frame(args.camera_index)
            if not send_inspection(args.hub, args.api_key, jpeg):
                queue_frame(args.api_key, jpeg)

        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(2)


if __name__ == "__main__":
    main()
