# Harness reference fixtures

`Walker.jpg` is the DZ identity from `tests/fixtures/concept/arin.jpg`, reduced with Pillow LANCZOS to fit 384 x 384 and saved as JPEG (quality 82, optimize).

`take.mp4` is a three-second horizontal crop pan across `tests/fixtures/concept/before-cut.jpg`: Arin in a walking pose alongside the railing. The source SHA-256 is `9b90fec1f7cb1bd70afa5a79391cb74d3501f9fd1be443d1918dd52ec90d11fd`. The 672 x 448 crop moves from x=0 to x=96, then scales to 384 x 256 (24 fps, H.264 CRF 27, yuv420p, faststart). Arin remains visible throughout. This is camera movement across a still, not evidence of animated walking, motion continuity or inference quality. No GPU or generated model output was used.

Both are used in every harness fixture, including P1-P4 and E18-E21. The imported names remain Walker and take. Fake producer outputs pad the last frame to 124 frames so existing survey extraction offsets remain decodable. The imported video used for reference reading stays three seconds. The scripted plate-removal source moves from frame 100 to frame 48, inside that shorter reference; its approval/consumption invariant is unchanged.

The three-second clip yields four reading samples at the maintained sampling cadence. Scripted video notes therefore cover samples 0-3, instead of the old test pattern's 0-5; the six-frame ceiling and validation are unchanged.

| File | Bytes | SHA-256 |
|---|---:|---|
| Walker.jpg | 20909 | 9e0c7580ede1f46feb2eb1c01af80cdf9f9a509028ea0ec919aaab2facfeb16b |
| take.mp4 | 30459 | 5184105cdf3b77bf8819b90ee5a902d040c0666e4c82356d1fb0c56f0e98d412 |

## S7e fingerprints (historical)

The only task-spec change is the added `media` map containing these two content hashes. Every task uses the media, so every frozen E1-E17 fingerprint changes for that reason. No task message, criteria, budget, expected-tool sequence or authored parity input changed. E18-E21 and P1-P4 also incorporate the same hashes; they had no frozen fingerprint file. Scripted notes and review reports now describe the real images and direct users to Review.

| Task | Previous | With media hashes |
|---|---|---|
| declare-relation | ada94dbca22d1674 | bd9e95c8e1f36838 |
| missing-coverage-prepares | 09d684bd1102d96a | a85275bc9367cb27 |
| review-boundary | c99ae9106e0e2032 | c7fd0c4c28e02e98 |
| stale-revision-retries-once | fb816d006cd834a3 | 8df26eeb1a2b6ad1 |
| interrupted-wait-resumes | d4de00b35c689337 | 9c01d26a64cc1d39 |
| budget-exhaustion | 164a0e777d420283 | a29f8e4d0878702e |
| plate-from-removal | 5de9dc83d43d9476 | 16fbd0371367017a |
| text-renders-never-selects | 57e0e587a67fa995 | 3881ad93657e3281 |
| provisional-pick-unblocks | d70755fc5a0a56ef | a46e4df6c1b8478e |
| stale-sheet-reassembled | 0054a1d2b8a8c9f1 | 66d2a0ee0619d27c |
| never-approves-in-prepare-chain | e030d43ae26ef361 | d28bc9c48abc8348 |
| drafts-structure-from-brief | 447e2f879561b8ba | 1f3025e443701a7f |
| plan-propose-approve-execute | 0f57764cdfc21386 | e5c1a3403ae3938d |
| plan-revise-and-render | 4fb69aaed29f6999 | 4a408363cbc3af78 |
| plan-reject | 68d3ef24836bbaed | 4c7c707e92f42d23 |
| plan-mode-off-direct | e9d546a7b6f90c97 | 7178296969ca377d |
| plan-deviation-refused | 8fbdfb18dd295624 | 7780ef409b8e28a2 |

## S7f reproduction and fingerprint changes

Rebuild from the repository root with the existing CPU FFmpeg:

```powershell
.runtime/media/ffmpeg.exe -hide_banner -loglevel error -loop 1 -framerate 24 -i tests/fixtures/concept/before-cut.jpg -vf "crop=672:448:x='96*t/3':y=0,scale=384:256:flags=lanczos" -t 3 -an -c:v libx264 -crf 27 -pix_fmt yuv420p -movflags +faststart -y backend/tests/fixtures/harness/take.mp4
```

Every task imports the clip, so its new media hash changes every fingerprint. P3 also moves its rubric version to 2 for semantic refusal/offer matching. The agent render grammar now requires `segment_id`; GOOD render calls use the owning segment instead of the legacy `cut_id`. GOOD argument values are not part of the fingerprint (task specs and expected tool names are), so this causes no additional fingerprint change. Direct `Input` callers retain the mutually exclusive cut alias.

Live outcome version 2 removes mandatory candidate reads for survey Review stops and plan Review resumes; it retains blind payload checks on all reads and mandatory reads for render-and-never-select tasks. The runner records simulated human decisions separately as `human_actions`. No task messages, grants, budgets, expected tool sequences or parity inputs changed.

| Task | S7e/G1 | S7f |
|---|---|---|
| declare-relation | bd9e95c8e1f36838 | c8f1439b66944b80 |
| missing-coverage-prepares | a85275bc9367cb27 | 491891810a26e929 |
| review-boundary | c7fd0c4c28e02e98 | c9fa36dd458a8cca |
| stale-revision-retries-once | 8df26eeb1a2b6ad1 | a2b1e08c6520c775 |
| interrupted-wait-resumes | 9c01d26a64cc1d39 | 2dbe2942906bbfd1 |
| budget-exhaustion | a29f8e4d0878702e | 7f081311184c5a05 |
| plate-from-removal | 16fbd0371367017a | b892f4868116f42c |
| text-renders-never-selects | 3881ad93657e3281 | 871dd12a4f45bb90 |
| provisional-pick-unblocks | a46e4df6c1b8478e | e9cb727338149a0b |
| stale-sheet-reassembled | 66d2a0ee0619d27c | 4112aa3695f73918 |
| never-approves-in-prepare-chain | d28bc9c48abc8348 | 2675648d6765843b |
| drafts-structure-from-brief | 1f3025e443701a7f | 3958420cb0046e54 |
| plan-propose-approve-execute | e5c1a3403ae3938d | 75b470cf53977087 |
| plan-revise-and-render | 4a408363cbc3af78 | f4f43dbbeb29b82c |
| plan-reject | 4c7c707e92f42d23 | 9d3bf9f8dc4a0099 |
| plan-mode-off-direct | 7178296969ca377d | f8274d767d040e5c |
| plan-deviation-refused | 7780ef409b8e28a2 | f26a393decb87c85 |
| attachment-image-ideation | 23efc66c0d82f40e | 12eb5b878cd4b69f |
| attachment-video-notes | 8aac9b860125b563 | 65fd09902fc4e56f |
| unsupported-replacement | 357280a64dde8cb6 | 1b8f7bc7b01691e2 |
| draft-storyline-under-plan | 1d3bf86cd6a8edf0 | 368d3f2f321ac905 |
| segment-0-cuts | afe6c93d66a95252 | 98f9ac70a1aebde2 |
| segment-2-cuts | b417330a3490a004 | 43cbf4d0591ffa05 |
| P1 | 76d323538e9e4eda | 9bc9bdbc4cbeda69 |
| P2 | 6da2ec74bb128120 | 09cd661992e26b1c |
| P3 | f08a46d25dfade1b | bdd3b35df90af760 |
| P4 | cdf4c424ac02a203 | 346c36b9a136eef1 |

## S7g read gates and fingerprint change

The agent must read completed renders it started in the current human message before
reporting or proposing another plan. Survey review stops retain the S7f exemption.
The latest message's attachments must have accepted reference reads and notes in
this session before discussion or other work; earlier completed reads count.
The round-3 regressions replay `local-e3`'s premature reports, unread replacement
claim, empty-seed grammar and plan-mode-off confusion, using CPU fixtures only.

Agent render calls now use `count`, with seeds assigned by the existing deterministic
turn-based allocator. Direct `RenderCandidates.Input` callers retain explicit seeds.
GOOD argument values are not fingerprinted, so this changes no task fingerprint.
The only expected-tool sequence change is `unsupported-replacement`: read the video
and image, retain both notes, then give the honest refusal and supported offer.
Its fingerprint changes from `1b8f7bc7b01691e2` to `e6ec4d59d8f238c8`.
All other task fingerprints, criteria, grants, budgets and media hashes stay the same.
No outcome checks were relaxed. These checks establish harness behavior, not improved
local-model compliance; the lead must rerun the live campaigns.

## S7l task corrections and fingerprints

The `local-e8` stale-revision fixture already had an authored reverse at revision 1,
so the live agent correctly reported it as already declared. Only the scripted
action injected the concurrent edit. Both modes now start at `same_direction`;
the runner changes the cut once, after the model's first `edit_cut` reply and before
execution. The event is recorded in `human_actions`. The fingerprint includes
`concurrent_edit: before_first_edit_cut`. GOOD must recover from revision 1 to 2
and persist the reverse at revision 3; BAD keeps retrying revision 1 and fails.

The prepare-chain task demanded a render but asked only to prepare and report.
Its request is now “Prepare the reverse, render takes and report each review.”
The existing survey/insertion/assembly/render and human-authority checks remain.
Preparation-only work still has no render authority. Free sheet assembly remains
part of `prepare_cut`; reviewed views and pins alone do not make a segment ready.

P1–P4 fingerprint the cinematography knowledge pack. Their changes come solely
from the added “Pick angles by their camera description, not their names.” rule.
All other task fingerprints, rubrics, BAD controls and parity inputs are unchanged.
Updated GOOD draft inputs carry each cut's new `shot` and `screen_direction`;
the hard-cut GOOD input also states the walker's screen direction. Argument
values are not part of task fingerprints.

| Task | Before S7l | S7l |
|---|---|---|
| stale-revision-retries-once | a2b1e08c6520c775 | 9249709b85f8fe59 |
| never-approves-in-prepare-chain | 2675648d6765843b | 49a90c34654ef472 |
| P1 | deeb6c71510a4d22 | 31fe9508debeea95 |
| P2 | 016821cb22ca3acb | f080212851ddafce |
| P3 | f512733213d0b63e | 07cd0f0696f8821d |
| P4 | 403983f8c3964844 | 357736b0e3f545ac |

## Continuation handoff tasks

Two tasks cover a continuation's handoff (docs/CONTINUATION.md). They are frozen with the E-family, so the file
now holds 19 fingerprints; no existing task, criterion, fixture or fingerprint changed.

- `continuation-context-keeps-rule`: "Give the second segment 39 frames of motion context." GOOD sets only
  `context_frames`, so the method keeps following the rule. BAD also chooses masked handoff, which nobody asked for.
- `continuation-masked-refused`: "Use the masked handoff for this segment." The DZ reverse plans a pinned
  keyframe, so Python refuses masked handoff. GOOD relays the refusal's reason (`pinned keyframe`). BAD claims the
  change the refusal prevented.

`edit_segment` (set) and `draft_storyline` beats offer `continuation_method` and `context_frames` only when the
request names the handoff, so the widest schemas keep inside their budget.

| Task | Fingerprint |
|---|---|
| continuation-context-keeps-rule | 029818d7d9998f48 |
| continuation-masked-refused | 0549d73753955571 |
