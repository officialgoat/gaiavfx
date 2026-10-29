"""Evidence-backed, model-free classification of target face detections."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TargetFacePolicy:
    min_det_score: float
    evidence: str
    note: str

    def __post_init__(self):
        from gaiavfx.modules.direction.replacement import _evidence, _number

        _number(self.min_det_score, "min_det_score")
        if not 0 < self.min_det_score <= 1:
            raise ValueError("min_det_score must be in (0, 1]")
        _evidence(self.evidence, self.note, "target_face")


def classify_target_faces(faces, policy: TargetFacePolicy):
    """Keep the best detection, with no identity or multi-person inference."""
    best = max(faces, key=lambda face: face[0], default=None)
    if best is None:
        return {"state": "no_face", "score": None, "box": None, "height": None}
    score, box = best
    return {
        "state": "face" if score >= policy.min_det_score else "no_face",
        "score": score,
        "box": list(box),
        "height": box[3] - box[1],
    }
