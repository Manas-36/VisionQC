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

MIN_AREA_FRACTION = 0.005      # part must cover >= 0.5 % of the frame
MIN_CONTRAST = 25              # min grey-level gap between part and background
MIN_FOCUS = 5.0                # variance of Laplacian below this = out of focus
DARK_P99 = 40                  # brightest 1 % of pixels below this = scene far too dark
SATURATED_FRACTION = 0.98      # >98 % of pixels blown out = nothing measurable
BORDER_MARGIN_PX = 2


@dataclass
class MeasurementResult:
    ok: bool
    width_px: Optional[float] = None
    height_px: Optional[float] = None
    error: Optional[str] = None
    annotated_jpeg: Optional[bytes] = None
    # "ok"         part measured
    # "no_part"    frame is empty (normal on a timer/trigger - NOT a failed part)
    # "bad_image"  camera problem: too dark/bright, blurry, undecodable, part cut off by frame edge
    code: str = "ok"


def _bad(msg, frame=None, code="bad_image"):
    return MeasurementResult(ok=False, error=msg, code=code, annotated_jpeg=_encode(frame) if frame is not None else None)


def measure_from_bytes(image_bytes: bytes, compare_box=None) -> MeasurementResult:
    """Measure the part in a photo: long side and short side in pixels.

    Pipeline: decode -> quality gates (exposure, focus) -> Gaussian blur ->
    Otsu threshold (adapts to lighting, no hand-tuned Canny numbers) ->
    auto-detect polarity (dark part on light background or the reverse,
    decided from the frame border) -> morphological clean-up -> external
    contours -> drop anything touching the frame edge -> largest remaining
    blob -> rotated bounding rectangle.
    """
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        return _bad("Could not decode image (corrupt or unsupported format).")

    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # (A backlit stage legitimately has a very bright background, so only reject
    # extreme cases rather than using a mean-brightness window.)
    if float(np.percentile(gray, 99)) < DARK_P99:
        return _bad("Image is too dark - check the light source.", frame)
    if float((gray >= 250).mean()) > SATURATED_FRACTION:
        return _bad("Image is over-exposed - reduce light or exposure.", frame)

    if float(gray.std()) < 8:                       # flat frame: nothing in view
        return _bad("No part in view.", frame, code="no_part")

    if cv2.Laplacian(gray, cv2.CV_64F).var() < MIN_FOCUS:
        return _bad("Image is out of focus - turn the lens ring / clean the lens.", frame)

    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    thr, binary = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Background = whatever dominates the frame border; make the part white.
    border = np.concatenate([binary[0, :], binary[-1, :], binary[:, 0], binary[:, -1]])
    if border.mean() > 127:
        binary = cv2.bitwise_not(binary)

    fg, bg = blur[binary > 0], blur[binary == 0]
    if fg.size == 0 or bg.size == 0 or abs(float(fg.mean()) - float(bg.mean())) < MIN_CONTRAST:
        return _bad("No part in view (contrast too low).", frame, code="no_part")

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    min_area = MIN_AREA_FRACTION * w * h
    contours = [c for c in contours if cv2.contourArea(c) >= min_area]
    if not contours:
        return _bad("No part in view.", frame, code="no_part")

    def touches_border(c):
        x, y, cw, ch = cv2.boundingRect(c)
        m = BORDER_MARGIN_PX
        return x <= m or y <= m or x + cw >= w - m or y + ch >= h - m

    inside = [c for c in contours if not touches_border(c)]
    if not inside:
        return _bad("Part touches the edge of the frame - it is cut off. Move the camera back or re-centre the part.", frame)

    largest = max(inside, key=cv2.contourArea)
    rect = cv2.minAreaRect(largest)
    w_px, h_px = max(rect[1]), min(rect[1])

    box = cv2.boxPoints(rect).astype(int)
    cv2.drawContours(frame, [box], 0, (0, 215, 255), 2)
    cv2.putText(frame, f"{w_px:.1f} x {h_px:.1f} px", (15, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 215, 255), 2)

    return MeasurementResult(ok=True, width_px=w_px, height_px=h_px, annotated_jpeg=_encode(frame))


def _encode(frame) -> bytes:
    success, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes() if success else b""
