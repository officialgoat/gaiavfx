"""Model-free request for one fill probe (ADR 0020 §4): a short camera move a video model renders so that space a
stage's memory never held comes into view. The maintained integration compiles it (`minimax_h3.probe`)."""

from dataclasses import dataclass

MOVES = ("hold", "turn", "step")


@dataclass(frozen=True)
class ProbeRequest:
    place: str  # what the place is, in words a viewer could check against the picture
    move: str  # hold (the camera stays), turn (it pans on the spot) or step (it moves a short way)
    turn: float  # degrees about the vertical, positive to the right
    width: int
    height: int
    length: int  # frames
    seed: int

    def __post_init__(self):
        if self.move not in MOVES:
            raise ValueError(f"a probe's move is one of {', '.join(MOVES)}")
        if not self.place.strip():
            raise ValueError("a probe needs the place in words")
        if self.move == "turn" and not self.turn:
            raise ValueError("a turn turns")
        if self.width <= 0 or self.height <= 0 or self.width % 32 or self.height % 32:
            raise ValueError("a probe's frame is a positive multiple of 32 each way")
        if self.length < 9 or (self.length - 1) % 8:
            raise ValueError("a probe's length is 8n + 1 frames, at least 9")
