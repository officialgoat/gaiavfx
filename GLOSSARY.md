# GAIAVFX

A local, chat-first workspace for directing movies that ComfyUI renders: the user authors a movie, Gaia proposes and
carries out approved work, and the user reviews and chooses what is kept.

## Direction

**Movie**:
An ordered list of Segments with a cast, authored by the user and Gaia.
_Avoid_: project, timeline document

**Segment**:
One generation window of a movie: a span of delivered frames with its own phases, cuts and cast.
_Avoid_: clip, scene (a scene is a place in time, not a render unit)

**Phase**:
A timed beat of direction inside a Segment, starting at a frame.

**Cut**:
An angle change at a frame inside a Segment, with its relation to the previous angle and an optional insertion.
_Avoid_: edit point, transition

**Opening**:
A Segment's first shot, on its opening angle.

**Shot**:
The Opening or the stretch after a Cut; each has its own references and plan.

**Place**:
A reusable location with its text and its Plate.
_Avoid_: location, environment, set (a set is a built Stage)

**Angle**:
One camera view of a Place.
_Avoid_: camera (in direction), view

**Plate**:
The empty picture of a Place, with nobody in it.

**Evidence**:
An approved picture of an Angle that a Shot may use.

**Pin**:
An approved frame placed at a Cut's offset that a render must reach.
_Avoid_: keyframe (a keyframe is a Stage still), anchor

**Sheet**:
An assembled image of a Place's views used as one reference.

**Suitability**:
The user's verdict that a reference suits a Shot, or does not.

**Join**:
How a continuing Segment takes over its predecessor's take: the handoff method and its context length.
_Avoid_: continuation handoff, transition

**Receipt**:
The record of one authored intent (one command, or the commands of one edit such as a draft), which Undo reverses
whole while nothing has changed it since.

## Generation and review

**Operation**:
One requested piece of generation work, with its frozen inputs, jobs and outputs.
_Avoid_: run (a run is Recast's), task

**Attempt**:
One try at producing the same product; a retry is a new Attempt of it.

**Take**:
A rendered candidate of a Segment.
_Avoid_: candidate (in user-facing words), version (a version is Recast's or a Stage's)

**Take basis**:
What a Take was rendered from: the authored revisions, opening angle and Pins it saw.

**Current take**:
A selected Take whose basis still matches the authored movie; otherwise it has changed since the take.
_Avoid_: fresh, valid

**Selection**:
The Take chosen for a Segment, by the user or provisionally by Gaia.

**Review**:
The user's blind ranking of lettered Takes.

**Process file**:
Media a workflow makes along the way rather than for the user to use, hidden until asked for.
_Avoid_: intermediate, temp file

## Stages

**Stage**:
A Place built in 3D from a Take or a bundle, which views and scenes can use.
_Avoid_: set (in user-facing words), environment

**Version**:
An immutable snapshot of a Stage, proposed, accepted or discarded by the user.

**Keyframe**:
A still with its depth and pose that a Stage version is built from, labelled K0, K1, …
_Avoid_: pin, frame

**Panorama**:
K0, the Stage's ground-truth surround picture; it cannot be excluded.

**Issue**:
A problem marked on a Stage by the user, Gaia or the judge.

**Fix**:
What answers an Issue: reshoot, extend, retract (exclude) or keep.

**Build**:
The work that writes a Stage version: a seed from a Take, or a repair from queued Fixes.

**Run plan**:
What Run queued fixes would run on the Version the user looks at: its Fixes, in words, or why it cannot run.

**Fill more**:
A queued extension that fills the space no view has seen.
_Avoid_: extend (as a user word)

**Kept still**:
A probe still that filling set aside and the user brought back.

## Recast

**Recast**:
Replacing characters, and their voices, across one imported Film.
_Avoid_: replacement run

**Film**:
An imported source video that a Recast works on.

**Character**:
A person in a Film the user curates from face clusters.
_Avoid_: cluster, track

**Target**:
The picture a Character becomes, proposed by Gaia or confirmed by the user.

**Face sheet**:
A generated reference of a Target that becomes the effective Target once approved.

**Voice model**:
A voice built from a Character's recordings, auditioned on its lines and approved by the user.

**Derivative**:
What is made from a Character's chosen input and waits for the user's approval: its Face sheet or its Voice model.

**Run**:
A Recast's render pass over a frame range, in draft or final mode, within a budget.

**Run scope**:
The Shots a Run renders over its range, what it reserves for each, and what that costs.

**Version (Recast)**:
One complete, assembled coverage of a Shot, shown blind with a letter.

**Choice**:
The user's pick of a Shot's Version, or the original.

**Conform**:
The frozen film output of the chosen Versions.

## Gaia

**Gaia**:
The chat agent that reads the workspace, proposes plans and carries out what the user approves.
_Avoid_: the agent (in user-facing words), assistant

**Plan**:
A list of Steps Gaia proposes and the user approves with a job budget.

**Step**:
One kind of work in a Plan: edit, prepare, render, pick, review, answer or build.

**Current step**:
The first Step of a Plan that is not finished; no later Step runs before it.

**Review (of a Step)**:
What the user reviews to finish a Step: an Operation's candidates, a Stage version a Build proposed, or a Run's versions.

**Grant**:
What the user's approval of a Plan permits: the step kinds and the job budget.

**Board**:
The conversation's whiteboard of brief, understanding and decisions, which the user may edit.
