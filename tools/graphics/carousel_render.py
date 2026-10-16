"""LinkedIn carousel renderer — on-brand slide PNGs + a multi-page PDF.

LinkedIn carousels are PDF document posts. This tool takes slide copy (the
agent writes it) plus a style playbook / brand overrides and renders 1080x1350
(or square) slides with Pillow, then bundles them into one PDF. Optional
per-slide background images (e.g. from ``image_selector``) are placed under a
tinted overlay so text stays readable. Local, free, deterministic.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolStability,
    ToolStatus,
    ToolTier,
)

SIZES = {
    "linkedin_portrait": (1080, 1350),
    "square": (1080, 1080),
    "linkedin_landscape": (1200, 628),
}

# Playbook font names → Windows/Linux candidates. First file that loads wins.
_FONT_FALLBACKS = ["Inter", "segoeui", "arial", "DejaVuSans", "LiberationSans-Regular"]
_FONT_BOLD_FALLBACKS = ["Inter-Bold", "seguisb", "segoeuib", "arialbd", "DejaVuSans-Bold", "LiberationSans-Bold"]


def _hex(c: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
    try:
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except Exception:
        return default


def _font(names: list[str], size: int):
    from PIL import ImageFont
    for n in names:
        for cand in (n, f"{n}.ttf", f"{n}.otf", n.replace(" ", ""), n.replace(" ", "") + ".ttf"):
            try:
                return ImageFont.truetype(cand, size)
            except Exception:
                continue
    return ImageFont.load_default(size)


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    lines: list[str] = []
    for para in str(text).split("\n"):
        words, cur = para.split(), ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if draw.textlength(trial, font=font) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


class CarouselRender(BaseTool):
    name = "carousel_render"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "graphics"
    provider = "pillow"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC

    dependencies = ["python:PIL"]
    install_instructions = "pip install Pillow"
    agent_skills = []

    capabilities = ["render_carousel_slides", "carousel_pdf", "brand_themed_social_cards"]
    best_for = [
        "LinkedIn document-post carousels (PDF) on a project's style playbook or brand colors",
        "Quick on-brand text cards / stat cards for social posts",
    ]
    not_good_for = [
        "Photoreal imagery — generate backgrounds with image_selector and pass them as slide.image",
        "Animated content — use the video pipelines",
    ]

    input_schema = {
        "type": "object",
        "required": ["slides", "output_dir"],
        "properties": {
            "slides": {
                "type": "array", "minItems": 1,
                "items": {"type": "object", "properties": {
                    "kicker": {"type": "string", "description": "small label above the title"},
                    "title": {"type": "string"},
                    "body": {"type": "string", "description": "supporting copy; newlines = paragraphs"},
                    "image": {"type": "string", "description": "optional background image path"},
                    "accent_index": {"type": "integer", "description": "which accent color to use"},
                }},
            },
            "output_dir": {"type": "string", "description": "e.g. projects/<id>/carousel/<slug>"},
            "style_playbook": {"type": "string", "description": "name in styles/ (colors + fonts)"},
            "brand": {"type": "object", "description": "overrides: name, handle, logo_path, primary, background, text, accent, font, font_bold"},
            "size": {"type": "string", "enum": list(SIZES.keys()), "default": "linkedin_portrait"},
            "page_numbers": {"type": "boolean", "default": True},
            "pdf_name": {"type": "string", "default": "carousel.pdf"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50)
    idempotency_key_fields = ["slides", "style_playbook", "brand", "size"]
    side_effects = ["writes slide PNGs and a PDF into output_dir"]
    user_visible_verification = [
        "Open the PDF: every slide readable at phone width, text inside safe margins",
        "Colors/fonts match the brand or playbook",
    ]

    def get_status(self) -> ToolStatus:
        try:
            import PIL  # noqa: F401
            return ToolStatus.AVAILABLE
        except ImportError:
            return ToolStatus.UNAVAILABLE

    # ------------------------------------------------------------------
    def _theme(self, inputs: dict[str, Any]) -> dict[str, Any]:
        theme: dict[str, Any] = {
            "background": "#0a0a0c", "text": "#f2e9d5", "primary": "#f0a83c",
            "accent": ["#6aa1ff", "#4fc283", "#e5544b"], "muted": "#8a8a94",
            "font": None, "font_bold": None, "name": "", "handle": "", "logo_path": None,
        }
        pb = inputs.get("style_playbook")
        if pb:
            from styles.playbook_loader import load_playbook
            p = load_playbook(pb)
            vl = p.get("visual_language", {}).get("color_palette", {})
            theme["background"] = vl.get("background", theme["background"])
            theme["text"] = vl.get("text", theme["text"])
            theme["primary"] = (vl.get("primary") or [theme["primary"]])[0]
            theme["accent"] = vl.get("accent") or theme["accent"]
            theme["muted"] = vl.get("muted", theme["muted"])
            typ = p.get("typography", {})
            theme["font"] = typ.get("body", {}).get("font")
            theme["font_bold"] = typ.get("headings", {}).get("font")
        theme.update({k: v for k, v in (inputs.get("brand") or {}).items() if v is not None})
        if isinstance(theme["accent"], str):
            theme["accent"] = [theme["accent"]]
        return theme

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            return ToolResult(success=False, error="Pillow required: pip install Pillow")
        start = time.time()
        W, H = SIZES.get(inputs.get("size", "linkedin_portrait"), SIZES["linkedin_portrait"])
        out_dir = Path(inputs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        t = self._theme(inputs)
        bg, fg = _hex(t["background"], (10, 10, 12)), _hex(t["text"], (242, 233, 213))
        primary = _hex(t["primary"], (240, 168, 60))
        muted = _hex(t["muted"], (138, 138, 148))
        accents = [_hex(a, primary) for a in t["accent"]] or [primary]
        M = int(W * 0.085)  # safe margin
        f_kicker = _font(([t["font_bold"]] if t["font_bold"] else []) + _FONT_BOLD_FALLBACKS, int(W * 0.028))
        f_title = _font(([t["font_bold"]] if t["font_bold"] else []) + _FONT_BOLD_FALLBACKS, int(W * 0.078))
        f_body = _font(([t["font"]] if t["font"] else []) + _FONT_FALLBACKS, int(W * 0.036))
        f_foot = _font(([t["font"]] if t["font"] else []) + _FONT_FALLBACKS, int(W * 0.024))
        logo = None
        if t.get("logo_path") and Path(t["logo_path"]).is_file():
            logo = Image.open(t["logo_path"]).convert("RGBA")
            logo.thumbnail((int(W * 0.18), int(H * 0.06)))

        slides = inputs["slides"]
        pages: list[Image.Image] = []
        written: list[str] = []
        for i, slide in enumerate(slides):
            img = Image.new("RGB", (W, H), bg)
            if slide.get("image") and Path(slide["image"]).is_file():
                photo = Image.open(slide["image"]).convert("RGB")
                scale = max(W / photo.width, H / photo.height)
                photo = photo.resize((int(photo.width * scale) + 1, int(photo.height * scale) + 1))
                img.paste(photo.crop((0, 0, W, H)))
                overlay = Image.new("RGBA", (W, H), bg + (170,))
                img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
            d = ImageDraw.Draw(img)
            accent = accents[slide.get("accent_index", i) % len(accents)]
            # accent bar + kicker
            d.rectangle((M, M, M + int(W * 0.06), M + 8), fill=accent)
            y = M + 30
            if slide.get("kicker"):
                d.text((M, y), str(slide["kicker"]).upper(), font=f_kicker, fill=accent)
                y += int(W * 0.06)
            # title (shrink until it fits ~45% of height)
            title_font, size = f_title, int(W * 0.078)
            while True:
                lines = _wrap(d, slide.get("title", ""), title_font, W - 2 * M)
                lh = int(size * 1.12)
                if len(lines) * lh <= H * 0.42 or size <= int(W * 0.04):
                    break
                size -= 4
                title_font = _font(([t["font_bold"]] if t["font_bold"] else []) + _FONT_BOLD_FALLBACKS, size)
            for ln in lines:
                d.text((M, y), ln, font=title_font, fill=fg)
                y += lh
            if slide.get("body"):
                y += int(W * 0.03)
                blh = int(W * 0.036 * 1.5)
                for ln in _wrap(d, slide["body"], f_body, W - 2 * M):
                    if y + blh > H - M - int(W * 0.08):
                        break
                    d.text((M, y), ln, font=f_body, fill=fg if ln.strip() else fg)
                    y += blh
            # footer: brand, handle, page, swipe hint
            fy = H - M - int(W * 0.03)
            foot = " · ".join(x for x in (t.get("name"), t.get("handle")) if x)
            if logo:
                img.paste(logo, (M, fy - logo.height + int(W * 0.02)), logo)
                d.text((M + logo.width + 16, fy), foot, font=f_foot, fill=muted)
            elif foot:
                d.text((M, fy), foot, font=f_foot, fill=muted)
            right = ""
            if inputs.get("page_numbers", True):
                right = f"{i + 1} / {len(slides)}"
            if i < len(slides) - 1:
                right = (right + "   →") if right else "→"
            if right:
                d.text((W - M - d.textlength(right, font=f_foot), fy), right, font=f_foot, fill=muted)
            path = out_dir / f"slide_{i + 1:02d}.png"
            img.save(path, "PNG")
            written.append(str(path))
            pages.append(img)

        pdf_path = out_dir / (inputs.get("pdf_name") or "carousel.pdf")
        pages[0].save(pdf_path, "PDF", save_all=True, append_images=pages[1:], resolution=150)
        written.append(str(pdf_path))
        return ToolResult(
            success=True,
            data={"slides": written[:-1], "pdf": str(pdf_path), "size": [W, H], "theme": t},
            artifacts=written,
            duration_seconds=time.time() - start,
        )


if __name__ == "__main__":  # self-check: python -m tools.graphics.carousel_render
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        r = CarouselRender().execute({
            "output_dir": tmp, "style_playbook": "clean-professional",
            "brand": {"name": "OpenMontage", "handle": "@openmontage"},
            "slides": [
                {"kicker": "Carousel", "title": "Five things nobody tells you about AI video", "body": "Swipe →"},
                {"kicker": "01", "title": "The agent is the editor", "body": "Scripts, scene plans and assets all come from instructions, not code.\nPython only holds tools."},
                {"kicker": "Wrap", "title": "Try it", "body": "github.com/openmontage"},
            ],
        })
        assert r.success, r.error
        assert len(r.data["slides"]) == 3 and Path(r.data["pdf"]).stat().st_size > 1000
        print("carousel_render ok:", r.data["pdf"])
