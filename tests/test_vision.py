from conftest import make_part
import vision


def test_measures_rectangle_size():
    r = vision.measure_from_bytes(make_part(220, 140))
    assert r.ok
    long_side, short_side = sorted([r.width_px, r.height_px], reverse=True)
    assert abs(long_side - 220) < 8 and abs(short_side - 140) < 8


def test_blank_image_is_an_error():
    import cv2, numpy as np
    blank = cv2.imencode(".jpg", np.full((480, 640, 3), 200, np.uint8))[1].tobytes()
    assert not vision.measure_from_bytes(blank).ok


def test_garbage_bytes_do_not_crash():
    assert not vision.measure_from_bytes(b"not an image").ok
