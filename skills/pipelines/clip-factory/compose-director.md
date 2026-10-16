# Compose Director - Clip Factory Pipeline

## When To Use

Render each clip and platform variant independently. The important behaviors here are consistency, batch resilience, and clear reporting of partial failures.

## Runtime Routing (HARD CONSTRAINT — Remotion or FFmpeg only)

This pipeline is Phase 1 deferred from the HyperFrames adoption schedule. `edit_decisions.render_runtime` must be `"remotion"` (default) or `"ffmpeg"` (pure-concat clip jobs with no composition). HyperFrames is NOT a valid runtime here — clip-factory depends on Remotion word-level caption burn, and HyperFrames caption parity is deferred work.

- If `edit_decisions.render_runtime == "hyperframes"`, stop. Re-open the idea stage so the user can be presented the real constraint and lock `remotion` with a `render_runtime_selection` decision that records `hyperframes` as `rejected_because: "caption-burn parity deferred on clip-factory"`.
- Per AGENT_GUIDE.md → "Present Both Composition Runtimes (HARD RULE)": the constraint is NOT an excuse to skip the conversation. The user still gets to see that HyperFrames exists and why it isn't viable here.
- Pass `proposal_packet`/`brief` to `video_compose.execute()` so the in-tool runtime-swap check runs end-to-end.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/render_report.schema.json` | Artifact validation |
| Prior artifacts | `state.artifacts["edit"]["edit_decisions"]`, `state.artifacts["assets"]["asset_manifest"]` | Clip edits and assets |
| Tools | `video_trimmer`, `video_compose`, `audio_mixer`, `color_grade` | Render pipeline |
| Media profiles | `lib/media_profiles.py` | Platform targets |

## Process

### 1. Treat Each Output As Its Own Job

One clip across three platforms is three render jobs. Name and track them explicitly.

### 2. Reuse What Can Be Shared

- shared audio mix where possible,
- shared subtitle styling,
- shared overlay assets,
- shared grading if the source needs it.

### 3. Fail Softly

If one clip or one platform variant fails:

- log it clearly,
- continue the rest of the batch,
- do not block successful exports.

### 4. Verify Every Output

Per render:

- correct duration,
- correct resolution/aspect ratio,
- no black opening frame,
- hook appears on time,
- subtitles render correctly,
- audio is present and consistent.

### 5. Use Render Report Metadata

Recommended metadata keys:

- `job_index`
- `failed_jobs`
- `shared_intermediates`
- `platform_groupings`

## Common Pitfalls

- Rendering sequentially without reason when jobs are independent.
- Treating a failed clip as a reason to stop the batch.
- Letting one platform variant quietly use the wrong framing or subtitle zone.

## Render QA Gate (enforced — compose cannot complete without it)

The self-review above is yours to do by eye. This step is the machine check and
`lib/checkpoint.py` refuses to write `compose` as `completed` unless it passed.

```python
from tools.analysis.render_qa import RenderQA
result = RenderQA().execute({
    "input_path": "projects/<id>/renders/final.mp4",
    "timestamps": scene_midpoints,                 # from scene_plan / edit_decisions
    "expected_duration_seconds": planned_duration,  # ±5% tolerance
    "output_dir": "projects/<id>/renders/review/render_qa",
    "golden_dir": "projects/<id>/renders/review/golden",   # optional, see below
})
render_report["metadata"] = {**render_report.get("metadata", {}), "render_qa": result.data["gate"]}
```

What it checks: video + audio streams present, duration drift, blank/black/white
sampled frames, `blackdetect` / `freezedetect` over the whole file, dead or
clipping audio, long silences. `passed` is false on any critical finding.

- **Failed?** Read `result.data["findings"]`, fix the composition, re-render, rerun.
  Do not present a failed render as done.
- **User accepts a finding** (e.g. an intentional black end card)? Record
  `render_report.metadata.render_qa = {"waived": true, "reason": "<their words>"}`.
  A waiver without a reason is rejected.
- **Golden frames.** On the first approved render pass `update_golden=True` so the
  sampled frames become the baseline. Every later re-render (cutting-room edits,
  text tweaks) is diffed against them; an unexpected visual change in a scene you
  did not touch fails the gate instead of slipping through. Refresh golden only
  when the user approves the new look.
