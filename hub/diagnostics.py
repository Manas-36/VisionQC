"""
Diagnostics / "AI check" engine — reference (golden-sample) comparison.

Every new photo's measured pixel size is compared against that camera's
stored reference (the first/master product photo). The result is a percent
error: how far off this part is from the master sample, as a percentage.
This is deliberately explainable rather than a black-box model — an
operator needs to know WHY a part failed and WHAT to do, not just a label.
"""

from dataclasses import dataclass
from typing import List, Optional

from config import TREND_RUN_LENGTH


@dataclass
class Diagnosis:
    result: str                  # PASS / FAIL / ERROR
    width_error_pct: Optional[float]
    height_error_pct: Optional[float]
    error_pct: Optional[float]    # the worse of the two, used for the PASS/FAIL decision
    diagnosis: str
    recommendation: str
    severity: str                  # info / warning / critical


def _pct_error(measured, reference):
    if not reference:
        return None
    return round(100 * (measured - reference) / reference, 3)


def _run_length(history: List[float]) -> int:
    """How many of the most recent error% readings are on the same side of zero?"""
    if not history:
        return 0
    sign = lambda v: 1 if v > 0 else (-1 if v < 0 else 0)
    last_sign = sign(history[-1])
    if last_sign == 0:
        return 0
    run = 0
    for v in reversed(history):
        if sign(v) == last_sign:
            run += 1
        else:
            break
    return run


def diagnose(width_px: Optional[float], height_px: Optional[float],
             ref_width_px: Optional[float], ref_height_px: Optional[float],
             max_error_percent: float, history_error_pct: List[float],
             vision_error: Optional[str]) -> Diagnosis:

    if vision_error is not None:
        return Diagnosis(
            result="ERROR", width_error_pct=None, height_error_pct=None, error_pct=None,
            diagnosis=f"Measurement failed: {vision_error}",
            recommendation="Check the camera's focus, lighting, and that a part is actually present in frame. "
                            "If this keeps happening, the camera may have shifted — re-set the reference photo.",
            severity="warning",
        )

    if ref_width_px is None or ref_height_px is None:
        return Diagnosis(
            result="ERROR", width_error_pct=None, height_error_pct=None, error_pct=None,
            diagnosis="No reference sample set for this camera yet.",
            recommendation="Go to Admin \u2192 Cameras and upload a photo of a known-good ('master') part for "
                            "this camera before it can check anything.",
            severity="warning",
        )

    w_err = _pct_error(width_px, ref_width_px)
    h_err = _pct_error(height_px, ref_height_px)
    error_pct = max(abs(w_err), abs(h_err))
    signed_error = w_err if abs(w_err) >= abs(h_err) else h_err

    in_tolerance = error_pct <= max_error_percent
    result = "PASS" if in_tolerance else "FAIL"

    combined_history = history_error_pct + [signed_error]
    run_length = _run_length(combined_history)

    if in_tolerance and run_length < TREND_RUN_LENGTH:
        return Diagnosis(
            result="PASS", width_error_pct=w_err, height_error_pct=h_err, error_pct=error_pct,
            diagnosis=f"Matches reference sample (±{error_pct:.2f}% error).",
            recommendation="No action needed.",
            severity="info",
        )

    if in_tolerance and run_length >= TREND_RUN_LENGTH:
        side = "larger" if signed_error > 0 else "smaller"
        return Diagnosis(
            result="PASS", width_error_pct=w_err, height_error_pct=h_err, error_pct=error_pct,
            diagnosis=f"Within tolerance, but the last {run_length} parts are trending {side} than the "
                      f"reference sample. This usually precedes a rejection.",
            recommendation="Schedule a proactive check on this machine: tool wear/offset, fixture clamping, "
                            "or thermal drift are the usual causes of a slow drift.",
            severity="warning",
        )

    if not in_tolerance and run_length < 3:
        return Diagnosis(
            result="FAIL", width_error_pct=w_err, height_error_pct=h_err, error_pct=error_pct,
            diagnosis=f"Isolated {error_pct:.2f}% deviation from the reference sample \u2014 not part of a "
                       f"trend, looks like a one-off.",
            recommendation="Likely causes: the part wasn't seated flat/square under the camera, debris in "
                            "frame, or a genuine one-off defect. Re-inspect the same part; if it now passes, "
                            "treat this as a placement error rather than a process fault.",
            severity="warning",
        )

    if not in_tolerance and run_length >= TREND_RUN_LENGTH:
        side = "oversize" if signed_error > 0 else "undersize"
        cause = ("tool wear or an incorrect tool offset" if side == "undersize" else
                 "tool over-feed, an incorrect offset, or a fixture that has crept out of position")
        return Diagnosis(
            result="FAIL", width_error_pct=w_err, height_error_pct=h_err, error_pct=error_pct,
            diagnosis=f"Systematic {side} drift over the last {run_length} parts \u2014 a process shift, "
                      f"not a random defect ({error_pct:.2f}% off reference).",
            recommendation=f"Stop this machine and inspect for {cause}. Recalibrate/replace the tool, "
                            f"re-zero the offset, then re-run 3\u20135 parts before resuming production.",
            severity="critical",
        )

    side = "larger" if signed_error > 0 else "smaller"
    return Diagnosis(
        result="FAIL", width_error_pct=w_err, height_error_pct=h_err, error_pct=error_pct,
        diagnosis=f"{error_pct:.2f}% {side} than the reference sample \u2014 outside the allowed "
                  f"{max_error_percent:.1f}% tolerance.",
        recommendation="Re-measure to confirm, then check tool condition and fixture alignment on this "
                        "machine if the next part also fails.",
        severity="critical",
    )
