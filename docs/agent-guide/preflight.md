# Preflight, Provider Menu, Composition Runtimes

> Deep reference split out of `AGENT_GUIDE.md`. The guide carries the binding rules; this file carries the detail. Read it when the guide points you here.

## Mandatory Preflight

Do this before any creative work. **Use `provider_menu_summary()` first — it's the human-ready rollup.** The raw `support_envelope()` dump is a firehose (megabytes of JSON on a well-configured machine); pasting it into chat will bury the user.

```bash
python -c "
from tools.tool_registry import registry
import json
registry.discover()
print(json.dumps(registry.provider_menu_summary(), indent=2))
"
```

The summary returns four fields the agent should translate into plain language:

- `composition_runtimes` — booleans for `ffmpeg`, `remotion`, `hyperframes`. This is the source of truth for the "Present Both Composition Runtimes (HARD RULE)" check.
- `capabilities[]` — one entry per capability family with `configured / total` counts and provider lists. Ready-made for the "N of M configured" menu.
- `setup_offers[]` — unavailable tools whose install is a 1-minute env-var fix. Lead with these when offering upgrades.
- `runtime_warnings[]` — specific signals like "hyperframes: npm package not resolvable". Surface these to the user verbatim — they're the kind of silent-failure bugs that break the governance contract.

Then, for deeper inspection (only when the summary isn't enough):

```bash
# Full menu — grouped available/unavailable per capability.
python -c "from tools.tool_registry import registry; import json; registry.discover(); print(json.dumps(registry.provider_menu(), indent=2))"

# Raw envelope — every tool's full contract. Slow/firehose; use for debugging only.
python -c "from tools.tool_registry import registry; import json; registry.discover(); print(json.dumps(registry.support_envelope(), indent=2))"
```

Then:

1. Read the selected manifest in `pipeline_defs/`.
2. Check every `required_tools` entry against the registry.
3. Check `fallback_tools` for unavailable tools.
4. Report one of: `passed`, `degraded`, or `blocked`.
5. Do not start production until the user understands the real capability envelope.

### Provider Menu (Mandatory at Preflight)

Already fetched via `provider_menu_summary()` above. Read that output and **present it to the user as a capability menu**, not as a flat tool list. Use `provider_menu()` directly only when you need the per-tool detail the summary collapses.

**How to present:**

```
YOUR CAPABILITIES

  Video Generation:  0/13 configured
  Image Generation:  1/7 configured
  Text-to-Speech:    1/3 configured
  Music Generation:  1/1 configured
  Composition:       3/3 configured (FFmpeg, video_stitch, video_trimmer)

  You can produce videos now with images + TTS + FFmpeg.
  Quick upgrades available — see below.
```

For EACH capability with unavailable providers, read the `install_instructions` field from the menu output and present setup options grouped by effort:

```
QUICK SETUP OPTIONS (1-minute each — set an env var in .env)

  Video Generation (0/13 -> unlock the biggest upgrade):
    Each unavailable provider lists its own install_instructions.
    Read them from the provider_menu output and present grouped by env var.
    Example: if 3 tools need FAL_KEY, group them: "FAL_KEY unlocks 3 providers"

  Image Generation (1/7 -> more style options):
    Same pattern — read install_instructions from each unavailable tool.

  Text-to-Speech (1/3):
    Same pattern.

LOCAL OPTIONS (free, needs hardware):
  Tools with runtime=LOCAL or runtime=LOCAL_GPU — read from the menu.

Already Available:
  List what's working. The user should feel good about what they have.
```

**Rules:**
- Do NOT hardcode provider names, API key names, or setup URLs in your prompts.
  Read them from the registry's `install_instructions` field on each tool.
- Always show the ratio: "X of Y configured" — this makes breadth visible.
- Group by capability, not by individual tool.
- Show what they CAN do now, then what they COULD unlock.
- If the user declines setup, proceed with the best available path — no nagging.
- If a tool shares an env var with others, group them (read from `dependencies` field).

### Setup Offer Protocol

When tools are `UNAVAILABLE` but can be fixed with simple configuration, **offer the user setup help instead of silently working around the limitation.** Many tools are one env var away from working.

| Fix Complexity | Action |
|----------------|--------|
| **1-minute fix** (env var) | Offer to help configure now — read `install_instructions` from the tool |
| **5-minute fix** (install) | Explain what to install and why — read `install_instructions` from the tool |
| **Complex fix** (GPU, model download) | Note the limitation, explain what it would unlock, move on |

**Rules:**
- Always tell the user what they're missing AND what they'd gain
- Show the cost difference (free local vs. paid API)
- If the user declines setup, proceed with the best available path — no nagging
- Group related fixes (tools sharing the same env var dependency)

### Composition Runtimes (Inside video_compose)

`video_compose` has **three** render engines / runtimes. They are parallel, not ranked — the choice is made at proposal and locked in `edit_decisions.render_runtime`. Check which are available:

```bash
python -c "
from tools.tool_registry import registry
registry.discover()
info = registry._tools['video_compose'].get_info()
print('Render engines:', info.get('render_engines'))
print('Remotion note:', info.get('remotion_note'))
print('HyperFrames note:', info.get('hyperframes_note'))
"
```

| Engine | Used For | Requires |
|--------|----------|----------|
| **FFmpeg** | Video-only cuts, concat, trim, subtitle burn | `ffmpeg` binary (always available) |
| **Remotion** | React-based composition: still images → animated video, text cards, stat cards, charts, callouts, comparisons, transitions with spring physics, word-level caption burn, TalkingHead avatar | Node.js (`npx`) + `remotion-composer/` + `node_modules` |
| **HyperFrames** | HTML/CSS/GSAP composition: kinetic typography, product promos, launch reels, website-to-video, registry-block-driven scenes, SVG character rigs | Node.js ≥ 22 + FFmpeg + `npx` (consumed via `npx hyperframes`) |

`render_runtime` is **locked at proposal** (`proposal_packet.production_plan.render_runtime`) and **carried through edit_decisions unchanged**. `video_compose` routes based on this field; silent runtime swaps are forbidden. If the chosen runtime becomes unavailable at compose time, surface a structured blocker per "Escalate Blockers Explicitly" above. See `skills/core/hyperframes.md` for the Remotion-vs-HyperFrames decision matrix.

### Critical Rule: Motion-Required Requests

For any request where the deliverable inherently depends on motion rather than static coverage, treat motion as a hard requirement. Examples:

- sci-fi trailers,
- cinematic teasers built from generated clips,
- hype edits,
- avatar or agent videos,
- any brief whose promise depends on moving shots rather than still frames.

For these requests:

- The `render_runtime` chosen at proposal (Remotion, HyperFrames, or FFmpeg) must be confirmed available up front if the planned visual treatment depends on it.
- Still-image fallback is forbidden. Do not quietly convert the job into a Ken Burns teaser, animatic, or slide-based video.
- FFmpeg-only fallback is forbidden when it changes the approved deliverable from motion-led video to still-led video.
- **Silent runtime swap is forbidden.** If `render_runtime="hyperframes"` was locked and HyperFrames is unavailable, do NOT route to Remotion instead. Surface the blocker, propose options, get user approval, log a `render_runtime_selection` decision — then proceed.
- Bubble critical issues immediately. If the chosen runtime is unavailable, fails to render, or provider clip generation fails in a way that blocks the approved treatment, stop and tell the user before proceeding.
- Do not spend more tokens or time on downgraded output unless the user explicitly approves the downgrade as an animatic or proof-of-concept.

**When Remotion is available**, the agent should design production plans around it:
- Explainer videos with `flat-motion-graphics` playbook -> Remotion animated scenes, not Ken Burns
- Data-driven videos -> Remotion stat cards and charts, not static image screenshots
- Any pipeline using still images -> Remotion spring animations, not FFmpeg pan-and-zoom
- **Screen demos of a CLI/terminal/install flow -> `TerminalScene` (synthetic screen recording), not OS-level capture.** See `.agents/skills/synthetic-screen-recording/SKILL.md`. Faster, deterministic, privacy-safe. Use real capture (`screen_recorder`, `cap_recorder`, `playwright-recording`) only when the demo is a real app UI or requires unpredictable live behavior.

### Remotion scene types available in `remotion-composer/`

See `remotion-composer/SCENE_TYPES.md` for the authoritative list and their cut schemas. Current scene types usable via `cut.type`:
`text_card`, `stat_card`, `callout`, `comparison`, `hero_title`, `terminal_scene`, `anime_scene`, `bar_chart`, `line_chart`, `pie_chart`, `kpi_grid`, `progress_bar`. Overlay types include `section_title`, `stat_reveal`, `hero_title`, `provider_chip`.

These stock scene-types are the **templated** path — fast and reliable, but they are why videos look alike. For **hero work, prefer atelier mode** (hand-authored composition) over this catalog; read those types as a *mechanics codex*, not a menu to assemble. See "Composition Authoring Mode" above and `skills/meta/bespoke-composition.md`.

**When Remotion is NOT available** and `render_runtime="remotion"` was NOT locked, `video_compose` may use FFmpeg Ken Burns motion on still images. This still works but produces less engaging visuals. Mention this tradeoff in the proposal. When `render_runtime="remotion"` IS locked and Remotion is unavailable, that's a blocker — escalate, don't silently swap.

When `render_runtime="hyperframes"` is locked and HyperFrames is unavailable (Node < 22, missing `ffmpeg`/`npx`, or `hyperframes doctor` reports issues), that's also a blocker. Do not substitute Remotion or FFmpeg without user approval + a logged `render_runtime_selection` decision.

Routing is automatic — `video_compose` reads `edit_decisions.render_runtime` and dispatches to the matching engine (`_render_via_hyperframes`, `_remotion_render`, or `_render_via_ffmpeg`). But the **agent must know both Remotion and HyperFrames exist at proposal time** so it can design the visual approach intentionally. Don't default to Remotion for motion-graphics-heavy concepts that HTML/GSAP would express more naturally, and don't default to HyperFrames for briefs that reuse the existing React scene stack.
