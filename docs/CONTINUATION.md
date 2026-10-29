# Continuation

A continuing segment starts from the end of its predecessor's selected take. Two choices decide how that motion
is handed over: the **method** and the **context length**. Both are per segment and both default to a rule.
Python resolves the rule ([continuation.py](../backend/src/gaiavfx/integrations/minimax_h3/continuation.py)).
The timeline, Properties and Gaia all show what Python resolved; none of them computes it.

## The two methods

| | Paired latents (the default) | Masked handoff |
|---|---|---|
| Value | `paired` | `masked` |
| Picture | MiniMax's motion context re-encodes the source's last frames as conditioning | The source take's original video latent tail is copied into the new window under a binary mask, so the sampler keeps those frames and paints the rest |
| Sound | Its own audio window: `render.toml`'s `audio_context_frames` (24), independent of the picture context | The source's decoded audio tail, re-encoded as an end-aligned reference; the audio window is always the picture context |
| Graph | `MiniMaxH3MotionContextLoadLatent` → `MiniMaxH3MotionContext` | `MiniMaxH3MotionContextLoadLatent` → `GaiaH3ContinuityMasked` (model, conditioning, latent and trim) |
| Origin | MiniMax H3 core | Continuity 00704a4, ported from the research adapter the campaigns measured (exact CPU parity) |

Both methods start from the same source: the original paired AV latent (`h3_context` artifact) saved with the
predecessor's selected take. Neither has a pixel fallback. Both trim the context frames from the delivered clip
with `GaiaH3ContinuityTrim`, and both save the new take's own latent, so chains continue with either method.

Levelled latents and Silver history are research methods only; they are not offered.

## The rule

The rule is a hint the user sees, never a silent switch. A segment whose method is `null` follows it:

1. **The window cuts to another angle: paired latents.** Across cuts, paired latents crossed the line on 9 of 11
   renders and masked handoff on 6 of 11. The masked prefix carries the old walking direction past the cut.
2. **The window continues the same shot and masked handoff qualifies: masked handoff.** It kept the smallest joins
   and the most faithful context in the continuity campaign.
3. **Otherwise: paired latents.**

An explicit choice overrides the rule. Masked handoff remains a valid choice across a cut when sound matters more
than geography.

### When masked handoff qualifies

Masked handoff qualifies only with the original paired artifact at the selected source ending, equal picture and
audio windows, and no timed or anchor guides. In GAIAVFX:

- **Source.** Every continuation already reads the predecessor's selected take's paired latent, or an explicit
  `h3_context` artifact. The plan refuses a take with no saved context (`continuation_source`).
- **Equal windows.** These hold by construction: the node re-encodes exactly the picture context's audio.
- **No guides.** A pinned keyframe (`MiniMaxH3AddGuide`) disqualifies masked handoff. Pins a shot's technique
  plans count too, so the plan refuses masked handoff before preparation adds the pin.

A choice that cannot render is refused when it is made, with the reason (HTTP 422, and the same message in Gaia's
tool result). Some choices only become impossible later, such as a pin added after masked handoff was chosen, or a
shorter take selected for the predecessor. Those are refused by the segment plan instead (`continuation_method`,
`continuation_context`), so the render cannot start, and the timeline and Properties show the reason.

## Context length

H3 windows are 17n+5 frames, so a context is **5, 22, 39 or 56 frames**: 2, 7, 12 or 17 latent steps. The
default is 22, and `null` follows it. A context is the end of the source take's window, so it is never longer
than that window. The plan lists the lengths each join can carry (`context_choices`).

The context adds no movie time. The model window grows to hold the context plus the delivered frames, and the
context frames are trimmed afterwards. Changing the length changes the window and every window timecode in the
prompt: a cut moves by the difference, for example by 17 frames from 22 to 39.

The audio coupling is part of the method. Paired latents keep their own 24-frame audio window whatever the picture
context is. Masked handoff's audio history is always the picture context. Each render's snapshot records the
method, the context, the audio window, whether it was chosen and what the rule recommended.

## Where to choose

- **Timeline.** The Continuation track draws each join as a band over the predecessor's last frames, anchored at
  the join. Drag its leading edge, or use the arrow keys and Home/End, to pick a length; Escape cancels a drag.
  Right-click the band, the segment or the Segment menu for **Handoff method** and **Context length**.
- **Properties.** The method, the context, the audio window and the rule's reason, with a method select and a
  numeric context field. Clearing the field, or entering the default, follows the default again.
- **Gaia.** `edit_segment` (set) and `draft_storyline` accept `continuation_method` and `context_frames` when
  the request names the handoff. Otherwise Gaia leaves the rule in charge; its world view names the
  recommendation for each continuing segment.
- **API.** `PATCH /api/direction/segments/{id}/continuation` with `expected_revision`, `method` and/or
  `context_frames`. The edit is revision-checked, and Undo and Redo go through its receipt.

## Measured

All research numbers use a 22-frame context, from the H3 research campaigns of 21–26 September 2026.

**Same shot** (H3_QUALITY_CONTINUITY_2026-09-21, "the normalized matrix"). Two fixtures × three
methods × three joins make 18 renders, all with 73 raw and 51 delivered frames. The table gives the range over
joins 1–3:

| Fixture | Method | Join jump | Context MAD | ΔE76 vs source end | Framing change vs source end |
|---|---|---|---|---|---|
| moving person, 768×448 | paired | 0.47–1.25× | 0.007–0.008 | 0.2 → 1.3 | ≤0.6°, 22–42 px |
| moving person, 768×448 | masked | 0.33–1.10× | 0.0008–0.0011 | 0.8 → 2.8 | ≤0.5°, 14–30 px |
| detail close-up, 1024×576 | paired | 3.8–4.6× | 0.008–0.009 | 0.2 → 0.3 | ≤1.3°, 2–20 px |
| detail close-up, 1024×576 | masked | 2.4–3.3× | 0.0008–0.0009 | 0.1 → 0.4 | ≤0.3°, 1–7 px |

How to read the columns:

- **Join jump** is the luma change across the join divided by the incoming clip's median frame-to-frame change.
- **Context MAD** is how closely the re-rendered context matches the frames actually delivered.

Masked handoff had the smallest joins and by far the most faithful context on both fixtures. In the user's review,
masked looked better on the detail close-up, and the application continuations showed little difference. Audio
joins were measured, but no listening verdict was given.

**Across cuts** (H3_CROSS_ANGLE_2026-09-24, day round 5, v9 prompts, first three seeds of each scene):

| | Cut (frames) | Sound after the cut | Crosses |
|---|---|---|---|
| Harbour, paired | −1, −1, −3 | −42, −42, −32 dB | 3 / 3 |
| Harbour, masked | +1, −2, −3 | −32, −33, −33 dB | 2 / 3 |
| Courtyard, paired | −1, +1, −1 | −46, −43, −46 dB | 2 / 3 + 1 weak |
| Courtyard, masked | −1, +2, −1 | −38, −42, −41 dB | 3 / 3 |

Masked handoff kept the sound 3 to 10 dB above paired at no cost in timing. With fresh seeds included, masked
crossed 6 of 11 against paired's 9 of 11. A dialogue shot and reverse held 4 of 4 with both methods.

## Limits

- Only the 22-frame context was measured by the research. 5, 39 and 56 frames are allowed by the grid, the node
  and the trim, and are covered here by CPU contracts and graph goldens, not by quality measurements.
- Masked handoff carries the old walking direction past a cut. That is why the rule keeps paired latents there.
- In a chain, masked handoff keeps the faded sound level it is handed (near −46 dB after a take that faded in its
  last second). That fade needs its own fix.
- Neither method has a pixel fallback; a take without its saved latent cannot be continued.
- Masked handoff needs the same canvas as the source take; a take rendered at another canvas fails in ComfyUI.
- Recast keeps paired latents and the 22-frame default. Its replacement graphs read the grid default, not the
  segment, and nothing here changes them.

## Files

| Part | Where |
|---|---|
| Rule, audio coupling, refusals | `backend/src/gaiavfx/integrations/minimax_h3/continuation.py` |
| Lowering | `backend/src/gaiavfx/integrations/minimax_h3/graph.py` |
| Context lengths | `FrameGrid.context_lengths` in `backend/src/gaiavfx/modules/direction/timebase.py` |
| Stored choice | `direction_segments.continuation_method`, `context_frames` (migration 0014) |
| Command | `backend/src/gaiavfx/application/continuation.py` |
| Node | `comfy_nodes/gaiavfx/continuity.py` (`GaiaH3ContinuityMasked`, `GaiaH3ContinuityTrim`) |
| Timeline and Properties | `frontend/src/features/timeline/movie-doc.tsx`, `frontend/src/features/properties/ContinuationProperties.tsx` |
| Tests | `backend/tests/test_continuation.py`, `backend/tests/test_harness_continuation.py`, `tests/test_community_continuity.py` (ComfyUI's Python), `tests/browser/continuation.spec.ts` |
