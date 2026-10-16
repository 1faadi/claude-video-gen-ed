# LinkedIn Carousel — on-brand static content

Make a LinkedIn document-post carousel (PDF + PNG slides) that matches a project's
brand/theme. Static deliverable: no pipeline stages, no checkpoints — but same
decision contract (announce tools/providers before paid calls).

## Inputs to gather (ask only for what is missing)

1. **Topic + angle** — what the carousel teaches/argues. One sentence.
2. **Theme source**, first match wins:
   - the project's `project.json` → `style_playbook` (colors + fonts),
   - the project `brief` artifact / `scene_plan` visual direction,
   - a user-named playbook in `styles/`,
   - user-supplied brand: name, handle, hex colors, logo path.
3. **Length** — default 7 slides (hook, 5 points, CTA). LinkedIn max 300 pages; keep ≤ 10.
4. **Imagery** — text-only (free, default) or background images per slide (paid, via `image_selector`).

## Write the copy

- Slide 1 = hook: ≤ 8 words title, a kicker, "swipe" cue in body.
- Body slides: kicker = `01…05` or a short label; title ≤ 10 words; body ≤ 35 words.
- Last slide = CTA: what to do next + handle/link.
- One idea per slide. No hashtags on slides (they go in the post text).
- Show the copy to the user as a list before rendering if the brief was vague.

## Render

Tool: `carousel_render` (local, free). Output under `projects/<id>/carousel/<slug>/`
so the Backlot board picks it up (the board lists `carousel/*/slide_*.png` and the PDF).

```python
from tools.graphics.carousel_render import CarouselRender
CarouselRender().execute({
    "output_dir": "projects/<id>/carousel/<slug>",
    "style_playbook": "<playbook>",            # or omit and pass brand colors
    "brand": {"name": "<Brand>", "handle": "@handle", "logo_path": "assets/logo.png"},
    "size": "linkedin_portrait",                # 1080x1350; "square" also fine
    "slides": [{"kicker": "...", "title": "...", "body": "...", "image": "<optional png>"}, ...],
})
```

With background images: generate them first through `image_selector` using the
playbook's `asset_generation.image_prompt_prefix` + consistency anchors, save to
`projects/<id>/assets/images/carousel_<n>.png`, pass as `slides[n].image`. The tool
tints them with the background color so text stays legible.

No project open (library page)? Create one: `init_project("<slug>", title=..., pipeline_type="clip-factory")`
is overkill for a carousel — instead write to `projects/<slug>/carousel/...` after
`init_project` with any pipeline type; the board shows carousels even without checkpoints.

## Review before handing over

- Open `slide_01.png` and the last slide (Read tool renders images). Title not clipped, margins intact.
- Contrast: text vs background ≥ 4.5:1 — `styles.playbook_loader.validate_contrast`.
- Brand name/handle correct on every slide; page numbers continuous.
- Tell the user: the PDF path (upload that to LinkedIn as a document post) and suggested post text.
