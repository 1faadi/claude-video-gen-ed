# OpenMontage - Agent Guide

Start here. This is the binding agent contract, kept short because it is read on every turn.
Detail lives in `docs/agent-guide/` and is read **when a rule below points you there**, not up front.
Architecture, key files, conventions: [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md).

## Routing — what to read first

| User message looks like | Do this first |
|---|---|
| Vague / exploratory ("what can you do?", "make me a video") | Read `skills/meta/onboarding.md`, run discovery, offer starter prompts. |
| LinkedIn carousel, slide-deck post, on-brand still images (no video) | Read `skills/meta/linkedin-carousel.md`. No pipeline. Render with `carousel_render` into `projects/<id>/carousel/<slug>/`. Single images: `image_selector` with the project playbook's `image_prompt_prefix`. |
| A video URL or file **as inspiration** ("make something like this") | Read `skills/meta/video-reference-analyst.md`. Analyse with the local tools (`video_analyzer`, transcript, scene detect, frame sampling), summarise what the reference does, then run the normal pipeline selection and present 2-3 differentiated concepts. Not a web-search task. |
| Footage to **edit** ("cut this into clips") | `source_media_review` + the footage-led pipeline (clip-factory, talking-head, podcast-repurpose, hybrid). |
| Any specific video request | Rule Zero below. |

Requests may arrive from the **Backlot dashboard agent dock** (headless `claude -p`). There is no terminal: end your turn with any question or approval request stated plainly and continue when the next message arrives.

## Rule Zero — All Production Goes Through a Pipeline

Every video production request goes through the pipeline system. No exceptions.

1. **Identify the pipeline** from the table below. Unclear → ask.
2. **Read the manifest** `pipeline_defs/<pipeline>.yaml` — stages, tools, gates, review focus.
3. **Run preflight** (below). Present the capability menu.
4. **Execute stage by stage.** Before each stage read `skills/pipelines/<pipeline>/<stage>-director.md`.
5. **Read Layer 3 before calling a tool.** Every generation tool lists `agent_skills`; read them in `.agents/skills/` before writing a prompt.

Do not: write ad-hoc scripts that call tools directly · skip preflight, checkpoints or review · generate assets before the director skill is read · use a tool without its Layer 3 skill. The intelligence is in the skills, not improvised code.

## What OpenMontage Is

Instruction-driven video production. The agent is the orchestrator; Python is tools + persistence only.

```
manifest (YAML) -> stage director skill (MD) -> tools (Python BaseTool)
-> self-review (skills/meta/reviewer.md) -> checkpoint (lib/checkpoint.py) -> human approval
```

## Available Pipelines

| Pipeline | Best For | Stability |
|----------|----------|-----------|
| `animated-explainer` | Topic to fully generated explainer | production |
| `animation` | Motion-graphics and animation-first videos | production |
| `cinematic` | Trailer, teaser, mood-led edits | production |
| `screen-demo` | Screen recordings and walkthroughs | production |
| `hybrid` | Source footage plus support visuals | production |
| `avatar-spokesperson` | Presenter-led avatar or lip-sync videos | production |
| `talking-head` | Footage-led speaker videos | beta |
| `clip-factory` | Many clips from one long source | beta |
| `podcast-repurpose` | Podcast highlights and derivatives | beta |
| `character-animation` | Local rigged cartoon characters | beta |
| `localization-dub` | Subtitle, dub, translated variants | beta |
| `documentary-montage` | Footage montage with end-tag | beta |
| `framework-smoke` | 2-stage test harness | test |

Beta pipelines work but are not fully audited — say so when one is selected.

## Mandatory Preflight

```bash
python -c "from tools.tool_registry import registry; import json; registry.discover(); print(json.dumps(registry.provider_menu_summary(), indent=2))"
```

`provider_menu_summary()` is the human-ready rollup — never paste `support_envelope()` into chat. Translate it into a capability menu ("Video generation: 3 of 13 configured"), lead with `setup_offers[]`, surface `runtime_warnings[]` verbatim. Then check the manifest's `required_tools` / `fallback_tools` against the registry and report `passed` / `degraded` / `blocked`. Never hardcode provider names or key names — read `install_instructions` and `dependencies` from the registry. If the user declines setup, proceed with the best available path, no nagging.

Full menu format, setup-offer tiers, composition-runtime table, motion-required rule, Remotion scene types: **`docs/agent-guide/preflight.md`**.

## Decision Communication Contract

The user must never have to infer which provider, model or render path was chosen.

- **Announce before any paid or consequential call:** tool name, provider, model/variant, why, sample or batch.
- **Ask before major changes:** provider, model family, video-led ↔ still-led, composition engine, dropping approved narration/music, sample → batch. Prompt refinements inside an approved path do not need re-approval.
- **No unilateral substitutions.** Blocked path → investigate, prepare options, recommend one, wait. Still-image animatics in place of true motion are never a silent fallback.
- **Escalate blockers:** what was attempted · what failed · auth / access / tool bug / prompt quality · options · your recommendation.
- **Re-log changed decisions.** `decision_log` is append-only. A changed choice gets a **new entry with the same `category` and `subject`**; the board shows the latest per (category, subject) pair as current. Mutating or rewording the old entry is a defect.
- **Present Both Composition Runtimes (HARD RULE).** When `video_compose.get_info()["render_engines"]` shows both Remotion and HyperFrames, present both at proposal — one sentence each on what it is best at for this brief, one honest tradeoff, your recommendation — and wait for approval. Silently picking a default is forbidden even if a director skill suggests one. Record both (plus ffmpeg where relevant) as `options_considered` in a `render_runtime_selection` decision; a one-option entry when both were available is a CRITICAL reviewer finding. Only one installed → say so explicitly and log the other as `rejected_because: "runtime not available on this machine"`.
- **Authoring mode is a separate decision** (`category: "composition_mode"`): *templated* (stock scene-types, fast, generic) vs *atelier* (hand-authored, default for hero/brand/launch work). Atelier routes through `skills/meta/taste-direction.md` then `skills/meta/bespoke-composition.md`; stock components and registry blocks are off-limits there.
- `render_runtime` is locked at proposal and carried through `edit_decisions` unchanged. A locked runtime that becomes unavailable is a blocker, not a swap.

Full text with examples: **`docs/agent-guide/decision-contract.md`**.

## Orchestrator

`research -> proposal -> script -> scene_plan -> assets -> edit -> compose -> publish` (per manifest).
Read manifest → `checkpoint.get_next_stage()` → read the stage director → use tools → self-review → checkpoint → present for approval where gated. Infrastructure: `lib/checkpoint.py`, `lib/pipeline_loader.py`, `tools/cost_tracker.py`.

## Project Workspace

Every run lives in `projects/<kebab-id>/` (`artifacts/`, `assets/{images,video,audio,music}`, `renders/final.mp4`). Before any stage:

1. `python -c "from lib.checkpoint import init_project; init_project('<id>', title='<Title>', pipeline_type='<pipeline>')"`
2. `python -m backlot open <id>` — opens the live board; if it fails, continue. The board is an observer.

**Always pass an explicit `output_path` under `projects/<id>/`.** Atelier and HyperFrames runs still write the canonical artifacts and checkpoints. Royalty-free tracks the user drops in `music_library/` are first choice for music. Layout and naming detail: **`docs/agent-guide/workspace.md`**.

## User-Facing Planning Protocol

Before execution present: 4-5 concepts when the brief is open · recommended pipeline · tool path and real alternatives · cost and quality tradeoffs · **music plan** · production plan by stage · approval gate before asset generation. A preferred vendor that is available is surfaced directly.

**Music plan is mandatory** at proposal: check `registry.get_by_capability("music_library")` (and `music_library/`), `registry.get_by_capability("music_search")`, `registry.get_by_capability("music_generation")`, and bring-your-own via the drop folder. Offer explicit choices (library track / provide one / generate — name provider and cost / no music) and record the decision in the brief. Detail: **`docs/agent-guide/planning.md`**.

## Stage Agents

| Stage | Canonical output | Quality bar |
|------|------------------|-------------|
| `idea` / `proposal` | `brief` / `proposal_packet` | Hook, platform, duration, tone, intent; concepts, plan, cost |
| `script` | `script` | Structured sections, valid timing, coherent narration |
| `scene_plan` | `scene_plan` | Ordered scenes, timings, asset requirements |
| `assets` | `asset_manifest` | Provenance, paths, model metadata, scene linkage |
| `edit` | `edit_decisions` | Concrete cuts, overlays, subtitle/music decisions |
| `compose` | `render_report` (+ `final_review`) | Output paths, encoding profile, verification, **render_qa gate** |
| `publish` | `publish_log` | Destinations, metadata |

A `completed` or `awaiting_human` checkpoint must carry its canonical artifact, valid against `schemas/artifacts/`. Tools record seeds and model versions.

**Render QA gate.** `compose` cannot be written `completed` unless `render_report.metadata.render_qa.passed` is true (from `tools/analysis/render_qa.py`: streams, duration, blank/black frames, freezes, dead audio, golden-frame drift) or the user knowingly waived it (`{"waived": true, "reason": ...}`). The compose directors show the call.

## Reviewer Protocol

`skills/meta/reviewer.md` — advisory, after every stage, before checkpointing. Load the manifest's `review_focus`. Max two rounds, then pass with warnings. Critical → fix and re-review; suggestions → note and proceed. Playbook `quality_rules` are constraints.

## Human Checkpoint Protocol

`skills/meta/checkpoint-protocol.md`. The manifest's `human_approval_default` is **binding**; `lib/checkpoint.py` refuses `completed` on a gated stage without `human_approved=True`. Typical gates: proposal, script, scene_plan, assets, publish — the manifest you loaded is the only authority. When a gate is reached: write `awaiting_human`, present the artifact summary, review findings and cost, then **END YOUR TURN**. Approval is per gate; a blanket "go ahead" counts only when logged as a `decision_log` entry with `category: "approval_policy"`.

## Communication Protocol

Agents coordinate through canonical artifacts, checkpoints (`projects/<id>/checkpoint_<stage>.json`), manifests and the registry. Write `in_progress` on entering a stage and refresh `metadata.partial_progress` per scene during `assets` / `compose` (drives the live board). Superseded checkpoints are archived to `history/`. Schemas: `schemas/artifacts/`, `schemas/checkpoints/`, `schemas/pipelines/`, `schemas/styles/`. Full rules and the style-playbook table: **`docs/agent-guide/protocols.md`**.

## Style Playbooks

`clean-professional` · `premium-minimalist` · `flat-motion-graphics` · `minimalist-diagram` · `ink-sketch` (Ink Theater doodle animation — `skills/creative/ink-theater.md`). For brand, launch or hero work read `skills/meta/taste-direction.md` first and carry its `taste_profile` into the proposal.

## Layers

1. `tools/` — what exists (registry). 2. `skills/` — how OpenMontage uses it (`skills/INDEX.md`). 3. `.agents/skills/` — how the technology works. Reading order: registry → Layer 2 → Layer 3 (mandatory before any generation call). Prefer skills over source for usage; read source when debugging a disagreement between a skill and a tool. Category table for Layer 3 and the quick-lookup: **`docs/agent-guide/layers.md`**. Tool families, naming convention (`VideoCompose`, not `VideoComposeTool`; call `.execute(dict)`), selectors: **`docs/agent-guide/tools.md`**.

## What Not To Do

- Bypass the pipeline, skip a director skill, or call a generation tool without its Layer 3 skill.
- Start asset generation before the production plan is approved.
- Change provider, model or render path without telling the user and getting approval when material.
- Hide degraded paths — record substitutions and blocked options explicitly; always show "X of Y providers configured".
- Hardcode provider names, key names or setup URLs; tell a restricted shared-install user to edit `.env` (surface an administrator setup request instead).
- Use deleted legacy names such as `tts_cloud`, `tts_engine`, `video_gen`.
