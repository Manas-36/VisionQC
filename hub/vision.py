"""
Image -> pixel dimension extraction.

Note there is deliberately no pixel-to-mm conversion in this version. The
system now works by comparing every photo against a "golden sample"
reference photo taken by the SAME camera — so as long as that camera's
position never changes, comparing raw pixel widths/heights between the
reference and each new part is a valid, calibration-free way to catch a
percentage size difference. (If you later want true millimetre readings
too, you can still calibrate a camera separately and store a pixels-per-mm
value per camera — the measurement step below would be unaffected.)
"""

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

MIN_CONTOUR_AREA_PX = 800


@dataclass
class MeasurementResult:
    ok: bool
    width_px: Optional[float] = None
    height_px: Optional[float] = None
    error: Optional[str] = None
    annotated_jpeg: Optional[bytes] = None


def measure_from_bytes(image_bytes: bytes, compare_box=None) -> MeasurementResult:
    """compare_box, if given, is (ref_w_px, ref_h_px) and is only used to
    draw a translucent reference outline on the annotated image for a
    quick visual sanity check — it does not affect the measurement."""
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        return MeasurementResult(ok=False, error="Could not decode image (corrupt or unsupported format).")

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)
    edges = cv2.dilate(edges, None, iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return MeasurementResult(ok=False, error="No part edges detected — check lighting/focus/placement.",
                                  annotated_jpeg=_encode(frame))

    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < MIN_CONTOUR_AREA_PX:
        return MeasurementResult(ok=False, error="Detected object too small — no part present, or contrast too low.",
                                  annotated_jpeg=_encode(frame))

    rect = cv2.minAreaRect(largest)
    (w_px, h_px) = rect[1]
    w_px, h_px = max(w_px, h_px), min(w_px, h_px)

    box = cv2.boxPoints(rect).astype(int)
    cv2.drawContours(frame, [box], 0, (0, 215, 255), 2)
    cv2.putText(frame, f"{w_px:.0f} x {h_px:.0f} px", (15, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 215, 255), 2)

    return MeasurementResult(ok=True, width_px=w_px, height_px=h_px, annotated_jpeg=_encode(frame))


def _encode(frame) -> bytes:
    success, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes() if success else b""
