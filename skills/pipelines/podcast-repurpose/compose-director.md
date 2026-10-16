# Compose Director - Podcast Repurpose Pipeline

## When To Use

Render the podcast-derived outputs with audio fidelity as the top priority. The visuals need to support the speech, not compete with it.

## Runtime Routing (HARD CONSTRAINT — Remotion or FFmpeg only)

Phase 1 deferred from HyperFrames. `edit_decisions.render_runtime` must be `"remotion"` (audiograms, composed outputs) or `"ffmpeg"` (pure-audio-led clip exports). HyperFrames caption-burn parity is deferred, and podcast outputs lean on Remotion's word-level caption stack.

- If `edit_decisions.render_runtime == "hyperframes"`, stop. Re-open the idea stage and surface the constraint to the user. Never silently rewrite the runtime.
- Per AGENT_GUIDE.md → "Present Both Composition Runtimes (HARD RULE)": tell the user HyperFrames exists and why it isn't viable on this pipeline, rather than silently locking remotion. Record a `render_runtime_selection` decision with hyperframes `rejected_because: "caption-burn parity deferred on podcast-repurpose"`.
- Pass `proposal_packet`/`brief` to `video_compose.execute()` for end-to-end runtime-swap detection.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/render_report.schema.json` | Artifact validation |
| Prior artifacts | `state.artifacts["edit"]["edit_decisions"]`, `state.artifacts["assets"]["asset_manifest"]` | Output plans and asset paths |
| Tools | `video_compose`, `audio_mixer` | Rendering and mix control |
| Playbook | Active style playbook | Brand consistency |

## Process

### 1. Render Highest-Value Outputs First

Priority order:

1. short highlight clips
2. quote-led clips
3. optional long-form companion video

This keeps the most publishable assets available first.

### 2. Preserve Audio Quality

- avoid unnecessary re-encoding,
- keep speech intelligible and stable,
- use music sparingly and only when it does not compete,
- verify subtitle sync after render.

### 3. Respect Platform Shapes

- `9:16` for short-form social
- `1:1` for quote-led or feed-safe clips
- `16:9` for long-form YouTube companion output

### 4. Verify Every Deliverable

- correct duration,
- correct aspect ratio,
- readable subtitles,
- accurate speaker attribution,
- stable audio,
- consistent brand treatment.

### 5. Use Render Report Metadata

Recommended metadata keys:

- `deliverable_groups`
- `audio_notes`
- `subtitle_checks`
- `failed_outputs`

## Common Pitfalls

- Letting visual treatments degrade audio quality.
- Rendering the full companion first and delaying the clips that matter most.
- Forgetting that a simple, readable clip beats a technically elaborate but confusing one.

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
