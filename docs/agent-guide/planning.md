# User-Facing Planning Protocol

> Deep reference split out of `AGENT_GUIDE.md`. The guide carries the binding rules; this file carries the detail. Read it when the guide points you here.

## User-Facing Planning Protocol

Before committing to execution, present:

1. `4-5` concept directions when the brief is still open.
2. Recommended pipeline.
3. Recommended tool path.
4. Alternative tool paths that are actually available.
5. Cost estimate and quality tradeoffs.
6. **Music plan** — mandatory for every pipeline that has audio. See below.
7. Production plan by stage.
8. Approval gate before asset generation.

If a user prefers a specific vendor and that tool is available, surface it directly. Do not hide provider choice.

### Music Plan (Mandatory)

Music is a critical part of any video. **Surface the music situation to the user at proposal/idea time** — do not silently defer it to the asset stage where a failure becomes expensive.

Check music availability in this order and present the options:

1. **User music library:** Check `registry.get_by_capability("music_library")` and inspect `music_library/`. If tracks exist, list durations and let the user pick one.
2. **Royalty-free search:** Check `registry.get_by_capability("music_search")` for configured search/download tools. Report licensing constraints and whether a key is required.
3. **Music generation APIs:** Check `registry.get_by_capability("music_generation")`. Report status, quota, cost, and quality tradeoffs honestly.
4. **Bring your own:** Note that the user can provide a track (for example from YouTube Audio Library or Jamendo) through the `music_library/` drop path.

**Always present the user with explicit choices:**
- Use a track from their library (which one?)
- Provide a different track (drop it in `music_library/`)
- Generate one via API (if available — name the provider and cost)
- Proceed without music

**If no music source is available:** Tell the user explicitly. Do NOT let this surface as a surprise at the asset stage.

Record the music decision in the proposal/brief artifact so the asset director knows what to do.

## Pipeline Asset Expectations

Each pipeline manifest's `tools_available` field declares what tools a stage can use. Use selectors for multi-provider capabilities — the selector handles routing to whatever is available. Read the pipeline manifest for the authoritative list per stage.
