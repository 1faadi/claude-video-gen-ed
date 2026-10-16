# Project Workspace and Music Library

> Deep reference split out of `AGENT_GUIDE.md`. The guide carries the binding rules; this file carries the detail. Read it when the guide points you here.

## Project Directory Convention

Every production run creates a project workspace under `projects/`. This directory is gitignored — all generated assets are regenerable.

```
projects/<project-name>/
├── artifacts/          # JSON artifacts from each stage (research_brief, script, scene_plan, etc.)
├── assets/
│   ├── images/         # Generated images (PNG)
│   ├── video/          # Generated video clips (MP4)
│   ├── audio/          # Narration segments + final mix (MP3/WAV)
│   ├── music/          # Background music track (MP3)
│   └── subtitles.srt   # Generated subtitles
└── renders/
    └── final.mp4       # Final rendered video (the deliverable)
```

**Naming convention**: Use kebab-case derived from the video title (e.g., `hidden-math-of-nature`, `how-music-rewires-brain`).

At pipeline initialization, before any stage runs:

1. **Initialize the workspace**: `python -c "from lib.checkpoint import init_project; init_project('<project-id>', title='<Title>', pipeline_type='<pipeline>')"` — creates the layout above and writes `project.json` (the marker the Backlot board reads).
2. **Open the board**: run `python -m backlot open <project-id>`. This starts the Backlot server if needed and opens the user's browser at the project's live board. If the command fails, continue the production — the board is an observer, never a blocker. This is the agent's ONLY board duty; the board derives everything else from disk.

All tools and agents must write outputs to these paths — **always pass an explicit `output_path` under `projects/<project-id>/`**. Assets written to the repo root, cwd, or temp dirs are invisible to the user's board and violate the workspace contract.

**This applies to atelier and HyperFrames-skill runs too**: hand-authored compositions still write the canonical artifacts they have (script or beats-plan, scene_plan-equivalent, asset manifest) plus checkpoints into `projects/<project-id>/`. The board is runtime-agnostic; only runs that skip the artifacts get a degraded board.

## Music Library

Users can place royalty-free music tracks in `music_library/` (gitignored). The asset director will check this folder before falling back to API-based music generation.

```
music_library/
├── ambient_track.mp3
├── cinematic_epic.mp3
└── ...
```

If the folder has tracks, the proposal and asset stages should present them as options alongside generated music. See the proposal-director and asset-director skills for details.
