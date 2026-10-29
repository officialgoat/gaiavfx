"""Versioned measurements, never a visual judge or a candidate selector. No IO."""

VERSION = 1
VERDICTS = {"pass", "fail", "unsupported"}
CHECKS = {
    "decodes": {"video", "image"},
    "frame_count": {"video"},
    "duration": {"video"},
    "resolution": {"video", "image"},
    "audio_present": {"video"},
    "black_frames": {"video"},
    "frozen_frames": {"video"},
    "screen_direction": {"video"},
    "facing": {"video", "image"},
    "markers_visible": {"video", "image"},
    "position_at_cut": {"video"},
}
BLACK_THRESHOLDS = {"d": 0.1, "pic_th": 0.98, "pix_th": 0.1}
FREEZE_THRESHOLDS = {"n": 0.001, "d": 2.0}
UNCALIBRATED = {
    "screen_direction": "no calibrated tracker",
    "facing": "no calibrated detector",
    "markers_visible": "no calibrated detector",
    "position_at_cut": "not solved (ADR 0009 §6)",
}


def evaluate(kind, expect, measured) -> list[dict]:
    """Compare only frozen expectations; duration tolerates strictly less than half a frame."""
    results = []
    for check, kinds in CHECKS.items():
        if kind not in kinds:
            continue
        verdict = "unsupported"
        if check in UNCALIBRATED:
            evidence = {"reason": UNCALIBRATED[check]}
        elif "error" in measured:
            evidence = {"error": str(measured["error"])[:300]}
        elif check in {"black_frames", "frozen_frames"}:
            evidence = {
                "ranges": measured.get(check, []),
                "detector": "blackdetect" if check == "black_frames" else "freezedetect",
                "thresholds": dict(
                    BLACK_THRESHOLDS if check == "black_frames" else FREEZE_THRESHOLDS
                ),
            }
        elif check == "decodes":
            counted, declared = measured.get("frames_counted"), measured.get("frames_declared")
            evidence = {"frames_counted": counted, "frames_declared": declared}
            if kind == "image":
                verdict = "pass" if measured.get("kind") == "image" else "fail"
            elif counted is None or declared is None:
                evidence["reason"] = "frame counts unavailable"
            else:
                verdict = "pass" if counted > 0 and counted == declared else "fail"
        else:
            required = {
                "frame_count": ("frames",),
                "duration": ("frames", "fps"),
                "resolution": ("width", "height"),
                "audio_present": ("audio",),
            }[check]
            if expect is None or any(key not in expect for key in required):
                evidence = {"reason": "no expectation recorded"}
            else:
                if check == "frame_count":
                    actual = measured.get("frames_counted")
                    evidence = {"expected": expect["frames"], "measured": actual}
                    passed = actual == expect["frames"]
                elif check == "duration":
                    actual = measured.get("duration")
                    seconds = expect["frames"] / expect["fps"]
                    evidence = {
                        "expected_seconds": seconds,
                        "measured_seconds": actual,
                        "fps": expect["fps"],
                    }
                    passed = actual is not None and abs(actual - seconds) < 0.5 / expect["fps"]
                elif check == "resolution":
                    actual = [measured.get("width"), measured.get("height")]
                    evidence = {"expected": [expect["width"], expect["height"]], "measured": actual}
                    passed = actual == evidence["expected"]
                else:
                    actual = measured.get("has_audio")
                    evidence = {"expected": expect["audio"], "measured": actual}
                    passed = actual == expect["audio"]
                if actual is None or isinstance(actual, list) and None in actual:
                    evidence["reason"] = "measurement unavailable"
                else:
                    verdict = "pass" if passed else "fail"
        results.append({"check": check, "verdict": verdict, "evidence": evidence})
    return results
