import diagnostics


def run(w, h, history=None, tol=3.0):
    return diagnostics.diagnose(width_px=w, height_px=h, ref_width_px=200, ref_height_px=100,
                                max_error_percent=tol, history_error_pct=history or [], vision_error=None)


def test_exact_match_passes():
    d = run(200, 100)
    assert d.result == "PASS" and d.error_pct == 0


def test_within_tolerance_passes():
    assert run(205, 100).result == "PASS"      # 2.5 %


def test_over_tolerance_fails():
    d = run(212, 100)                           # 6 %
    assert d.result == "FAIL" and d.error_pct > 3


def test_vision_error_reported():
    d = diagnostics.diagnose(width_px=None, height_px=None, ref_width_px=200, ref_height_px=100,
                             max_error_percent=3.0, history_error_pct=[], vision_error="no part found")
    assert d.result == "ERROR"


def test_sustained_same_direction_is_called_a_drift():
    d = run(212, 100, history=[1, 1.5, 2, 2.5, 3, 3.5, 4, 5])
    assert "drift" in d.diagnosis.lower() or "systematic" in d.diagnosis.lower()
