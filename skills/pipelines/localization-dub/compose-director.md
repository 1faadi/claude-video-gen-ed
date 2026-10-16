# Compose Director - Localization Dub Pipeline

## When To Use

Render the localized outputs. The quality bar is intelligibility, timing coherence, and clear version labeling across every language package.

## Runtime Routing (HARD CONSTRAINT — Remotion or FFmpeg only)

Phase 1 deferred from HyperFrames. `edit_decisions.render_runtime` must be `"remotion"` or `"ffmpeg"`. Localization depends on Remotion's caption stack (per-locale subtitle burn) and, when dubbing with lip-sync, on the Remotion TalkingHead pipeline. HyperFrames has no parity for either in Phase 1.

- If `edit_decisions.render_runtime == "hyperframes"`, stop. Re-open the idea stage and surface the constraint — don't silently rewrite the runtime.
- Per AGENT_GUIDE.md → "Present Both Composition Runtimes (HARD RULE)": the pipeline's constraint does NOT skip the conversation. Present the constraint to the user so they know HyperFrames exists but isn't viable here. Log a `render_runtime_selection` decision with hyperframes `rejected_because: "caption + lip-sync parity deferred on localization-dub"`.
- Pass `proposal_packet`/`brief` to `video_compose.execute()` for end-to-end runtime-swap detection.

## Prerequisites

| Layer | Resource | Purpose |
|-------|----------|---------|
| Schema | `schemas/artifacts/render_report.schema.json` | Artifact validation |
| Prior artifacts | `state.artifacts["edit"]["edit_decisions"]`, `state.artifacts["assets"]["asset_manifest"]` | Locale-specific render instructions |
| Tools | `video_compose`, `audio_mixer`, `video_trimmer`, `audio_enhance` | Final render and audio finishing |
| Playbook | Active style playbook | Subtitle placement and output quality |

## Process

### 1. Render By Locale

Treat each target language as its own deliverable set. Keep names and output directories explicit.

### 2. Expect Timing Adjustments

Allow for:

- subtitle reflow,
- dub-audio duration drift,
- longer CTA holds,
- optional trims or coverage sections.

### 3. Verify Every Locale

Record important findings in:

- `render_report.verification_notes`
- `render_report.warnings`
- `render_report.metadata.locale_notes`

Check:

- intelligibility,
- subtitle fit,
- obvious sync drift,
- version labeling.

### 4. Quality Gate

- each locale output exists,
- the dub and subtitle timing are acceptable,
- labels and filenames are unambiguous,
- warnings are preserved.

## Common Pitfalls

- Rendering all locales as if they were timing-identical.
- Forgetting to re-check subtitle line length after translation.
- Naming outputs in ways that hide the locale or treatment mode.

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
