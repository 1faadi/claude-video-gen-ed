# Compose Director - Avatar Spokesperson Pipeline

## When To Use

Render the final spokesperson outputs. The bar is simple: the presenter must look stable, speech must be clear, and subtitles or support cards must not crowd the frame.

## Runtime Routing (HARD CONSTRAINT — Remotion only)

Phase 1 deferred from HyperFrames. `edit_decisions.render_runtime` must be `"remotion"`. This pipeline depends on the Remotion `TalkingHead` composition and `remotion_caption_burn` — both have no HyperFrames parity in Phase 1.

- If `edit_decisions.render_runtime == "hyperframes"`, stop. Re-open the idea stage and surface the constraint. Silent rewrite is a governance violation.
- Per AGENT_GUIDE.md → "Present Both Composition Runtimes (HARD RULE)": the lock to remotion is NOT an excuse to skip the conversation. The user deserves to know that HyperFrames exists as a runtime and why it isn't viable for avatar-spokesperson. Log a `render_runtime_selection` decision with hyperframes `rejected_because: "TalkingHead + caption parity deferred on avatar-spokesperson"`.
- Pass `proposal_packet`/`brief` to `video_compose.execute()` for in-tool runtime-swap detection.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/render_report.schema.json` | Artifact validation |
| Prior artifacts | `state.artifacts["edit"]["edit_decisions"]`, `state.artifacts["assets"]["asset_manifest"]` | What to render |
| Tools | `video_compose`, `audio_mixer`, `video_stitch`, `audio_enhance` | Render and audio finishing |
| Playbook | Active style playbook | Typography and layout rules |

## Process

### 1. Render The Hero Cut First

Prefer one strong master before derivatives. Compose:

- presenter video,
- subtitles,
- lower-thirds,
- CTA cards,
- mixed narration.

### 2. Keep The Frame Clean

Subtitle and CTA placement matter more here than flashy transitions. Leave the face and mouth region unobstructed.

### 3. Verify Mouth Timing And Audio

If the avatar path used lip sync or audio-driven talking head, check:

- mouth timing,
- face artifacts,
- drift on long sections,
- audio clarity.

### 4. Verify Every Output

Record important findings in:

- `render_report.verification_notes`
- `render_report.warnings`
- `render_report.metadata.variant_notes`

### 5. Quality Gate

- the output file is valid,
- speech is clear,
- subtitles stay readable,
- the presenter remains visually stable.

## Common Pitfalls

- Letting subtitles cover the chin or mouth area.
- Shipping a long lip-sync render without spot-checking drift.
- Making derivative crops that cut off the presenter or CTA.

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
