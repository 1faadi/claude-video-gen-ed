"""Render QA gate — deterministic checks on a rendered video before compose completes.

The compose directors already ask the agent to "extract keyframes and look at
them". That is advisory prose; nothing fails when it is skipped. This tool is
the machine half of that review and `lib/checkpoint.py` refuses to write a
`compose` checkpoint as ``completed`` unless its report says ``passed`` (or a
waiver with a reason is recorded).

Checks (all local, ffmpeg/ffprobe + Pillow):

- container probe: video stream, audio stream (unless ``require_audio`` is
  false), duration vs ``expected_duration_seconds`` (±5 %)
- sampled frames (scene midpoints or an even spread): blank / black / white
  frames via luma mean + stddev
- whole-video ``blackdetect`` and ``freezedetect`` passes
- audio ``volumedetect`` + ``silencedetect`` (long silences, dead track)
- golden frames: if ``golden_dir`` holds frames with the same names, mean
  absolute luma difference above ``golden_threshold`` is a regression.
  ``update_golden=True`` promotes the current frames to golden.

Output: ``<output_dir>/render_qa.json`` plus the sampled PNGs. Put the report's
``gate`` block under ``render_report.metadata.render_qa`` when checkpointing.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ToolResult,
    ToolStability,
    ToolTier,
)

_BLANK_STDDEV = 4.0        # luma stddev below this = uniform frame
_BLACK_MEAN = 8.0
_WHITE_MEAN = 247.0
_DURATION_TOLERANCE = 0.05
_SILENCE_SECONDS = 4.0     # one silence this long is a finding
_DEAD_TRACK_DB = -60.0     # mean volume below this = no audio content


class RenderQA(BaseTool):
    name = "render_qa"
    version = "0.1.0"
    tier = ToolTier.ANALYZE
    capability = "analysis"
    provider = "ffmpeg"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC

    dependencies = ["cmd:ffmpeg", "cmd:ffprobe", "python:PIL"]
    install_instructions = "Install FFmpeg (https://ffmpeg.org/download.html) and `pip install Pillow`."
    agent_skills = ["ffmpeg"]

    capabilities = ["render_gate", "golden_frame_regression", "blank_frame_detection"]

    input_schema = {
        "type": "object",
        "required": ["input_path"],
        "properties": {
            "input_path": {"type": "string", "description": "Rendered video to gate."},
            "timestamps": {
                "type": "array", "items": {"type": "number"},
                "description": "Seconds at which to sample frames (scene midpoints). Default: even spread of 5.",
            },
            "expected_duration_seconds": {"type": "number", "description": "Planned duration; ±5% tolerance."},
            "output_dir": {"type": "string", "description": "Where frames + render_qa.json go. Default: <renders>/review/render_qa."},
            "golden_dir": {"type": "string", "description": "Directory of approved frames to diff against."},
            "update_golden": {"type": "boolean", "description": "Copy the sampled frames into golden_dir after the run."},
            "golden_threshold": {"type": "number", "description": "Mean abs luma diff (0-255) above which a frame drifted. Default 12."},
            "require_audio": {"type": "boolean", "description": "Missing audio stream is critical. Default true."},
        },
    }
    output_schema = {
        "type": "object",
        "properties": {
            "passed": {"type": "boolean"},
            "findings": {"type": "array"},
            "report_path": {"type": "string"},
            "gate": {"type": "object", "description": "Drop this under render_report.metadata.render_qa"},
        },
    }
    side_effects = ["writes frames and render_qa.json to output_dir"]
    best_for = ["Gate the compose stage on a rendered file", "Catch blank/black scenes and silent audio", "Golden-frame regression across re-renders"]

    # ------------------------------------------------------------------ main
    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        src = Path(inputs["input_path"])
        if not src.is_file():
            return ToolResult(success=False, error=f"Input not found: {src}")
        out_dir = Path(inputs.get("output_dir") or src.parent / "review" / "render_qa")
        out_dir.mkdir(parents=True, exist_ok=True)
        findings: list[dict[str, str]] = []

        probe = self._probe(src)
        self._check_probe(probe, inputs, findings)
        duration = probe.get("duration_seconds") or 0.0

        stamps = inputs.get("timestamps") or self._spread(duration)
        frames = self._sample(src, stamps, out_dir, findings)
        self._scan_video(src, findings)
        if probe.get("has_audio"):
            self._scan_audio(src, duration, findings)

        golden_dir = inputs.get("golden_dir")
        golden = self._golden(frames, golden_dir, float(inputs.get("golden_threshold") or 12.0), findings) if golden_dir else None
        if golden_dir and inputs.get("update_golden"):
            gd = Path(golden_dir); gd.mkdir(parents=True, exist_ok=True)
            for f in frames:
                if f.get("path"):
                    shutil.copyfile(f["path"], gd / Path(f["path"]).name)

        passed = not any(f["severity"] == "critical" for f in findings)
        report = {
            "version": "1.0",
            "input_path": str(src),
            "passed": passed,
            "probe": probe,
            "frames": frames,
            "golden": golden,
            "findings": findings,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        report_path = out_dir / "render_qa.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        gate = {"passed": passed, "report_path": str(report_path), "critical": sum(f["severity"] == "critical" for f in findings),
                "warnings": sum(f["severity"] == "warning" for f in findings)}
        return ToolResult(
            success=True,
            data={"passed": passed, "findings": findings, "report_path": str(report_path), "gate": gate, "frames": frames, "probe": probe, "golden": golden},
            artifacts=[str(report_path)] + [f["path"] for f in frames if f.get("path")],
            duration_seconds=round(time.time() - start, 2),
        )

    # ----------------------------------------------------------------- probe
    def _probe(self, src: Path) -> dict[str, Any]:
        res = self.run_command(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(src)])
        try:
            info = json.loads(res.stdout or "{}")
        except json.JSONDecodeError:
            info = {}
        video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
        audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
        fps = None
        if video and video.get("r_frame_rate"):
            num, _, den = video["r_frame_rate"].partition("/")
            try:
                fps = round(float(num) / float(den or 1), 3)
            except (ValueError, ZeroDivisionError):
                fps = None
        return {
            "has_video": video is not None,
            "has_audio": audio is not None,
            "duration_seconds": float(info.get("format", {}).get("duration") or 0.0),
            "resolution": f"{video.get('width')}x{video.get('height')}" if video else None,
            "fps": fps,
            "video_codec": video.get("codec_name") if video else None,
            "audio_codec": audio.get("codec_name") if audio else None,
            "file_size_bytes": src.stat().st_size,
        }

    def _check_probe(self, probe: dict[str, Any], inputs: dict[str, Any], findings: list) -> None:
        if not probe["has_video"]:
            findings.append(_f("critical", "no_video_stream", "ffprobe found no video stream"))
        if inputs.get("require_audio", True) and not probe["has_audio"]:
            findings.append(_f("critical", "no_audio_stream", "ffprobe found no audio stream — render did not embed audio"))
        if probe["file_size_bytes"] == 0:
            findings.append(_f("critical", "empty_file", "output file is 0 bytes"))
        expected = inputs.get("expected_duration_seconds")
        if expected and probe["duration_seconds"]:
            drift = abs(probe["duration_seconds"] - float(expected)) / float(expected)
            if drift > _DURATION_TOLERANCE:
                findings.append(_f("critical", "duration_drift",
                                   f"duration {probe['duration_seconds']:.2f}s vs expected {float(expected):.2f}s ({drift * 100:.1f}% off, tolerance 5%)"))

    # ---------------------------------------------------------------- frames
    @staticmethod
    def _spread(duration: float, n: int = 5) -> list[float]:
        if duration <= 1:
            return [max(0.0, duration / 2)]
        pad = min(0.5, duration / 10)
        return [round(pad + (duration - 2 * pad) * i / (n - 1), 3) for i in range(n)]

    def _sample(self, src: Path, stamps: list[float], out_dir: Path, findings: list) -> list[dict[str, Any]]:
        from PIL import Image, ImageStat

        frames: list[dict[str, Any]] = []
        for i, ts in enumerate(stamps):
            path = out_dir / f"frame_{i:02d}_{ts:.2f}s.png"
            self.run_command(["ffmpeg", "-y", "-v", "error", "-ss", str(ts), "-i", str(src), "-frames:v", "1", str(path)])
            if not path.is_file():
                findings.append(_f("critical", "frame_extract_failed", f"could not extract a frame at {ts}s"))
                frames.append({"timestamp": ts, "path": None})
                continue
            with Image.open(path) as im:
                stat = ImageStat.Stat(im.convert("L"))
            mean, std = stat.mean[0], stat.stddev[0]
            kind = "ok"
            if std < _BLANK_STDDEV:
                kind = "black" if mean < _BLACK_MEAN else "white" if mean > _WHITE_MEAN else "blank"
                findings.append(_f("critical", f"{kind}_frame", f"frame at {ts}s is {kind} (luma mean {mean:.1f}, stddev {std:.1f})"))
            frames.append({"timestamp": ts, "path": str(path), "luma_mean": round(mean, 2), "luma_stddev": round(std, 2), "kind": kind})
        return frames

    # ------------------------------------------------------------ full scans
    def _scan_video(self, src: Path, findings: list) -> None:
        res = self.run_command(["ffmpeg", "-v", "info", "-nostats", "-i", str(src),
                                "-vf", "blackdetect=d=0.5:pix_th=0.10,freezedetect=n=-60dB:d=2", "-an", "-f", "null", "-"])
        log = res.stderr or ""
        for m in re.finditer(r"black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)", log):
            findings.append(_f("critical", "black_segment", f"black video from {m.group(1)}s to {m.group(2)}s ({m.group(3)}s)"))
        for m in re.finditer(r"freeze_start: ([\d.]+)", log):
            findings.append(_f("warning", "frozen_segment", f"frame frozen for 2s+ starting at {m.group(1)}s"))

    def _scan_audio(self, src: Path, duration: float, findings: list) -> None:
        res = self.run_command(["ffmpeg", "-v", "info", "-nostats", "-i", str(src),
                                "-af", f"volumedetect,silencedetect=n=-50dB:d={_SILENCE_SECONDS}", "-vn", "-f", "null", "-"])
        log = res.stderr or ""
        m = re.search(r"mean_volume: (-?[\d.]+) dB", log)
        if m and float(m.group(1)) < _DEAD_TRACK_DB:
            findings.append(_f("critical", "dead_audio", f"audio mean volume {m.group(1)} dB — track is effectively silent"))
        for m in re.finditer(r"silence_start: ([\d.]+)", log):
            start = float(m.group(1))
            # a trailing fade-out is normal; silence that ends before the last second is not
            end_m = re.search(rf"silence_start: {re.escape(m.group(1))}.*?silence_end: ([\d.]+)", log, re.S)
            end = float(end_m.group(1)) if end_m else duration
            if end < duration - 1.0:
                findings.append(_f("warning", "long_silence", f"audio silent from {start:.1f}s to {end:.1f}s"))
        m = re.search(r"max_volume: (-?[\d.]+) dB", log)
        if m and float(m.group(1)) >= 0.0:
            findings.append(_f("warning", "clipping", "audio peaks at 0 dBFS — likely clipping"))

    # ---------------------------------------------------------------- golden
    def _golden(self, frames: list[dict[str, Any]], golden_dir: str, threshold: float, findings: list) -> dict[str, Any]:
        from PIL import Image, ImageChops, ImageStat

        gd = Path(golden_dir)
        compared, drifted, missing = [], [], []
        for f in frames:
            if not f.get("path"):
                continue
            ref = gd / Path(f["path"]).name
            if not ref.is_file():
                missing.append(ref.name)
                continue
            with Image.open(f["path"]) as a, Image.open(ref) as b:
                a2 = a.convert("L").resize((320, 180))
                b2 = b.convert("L").resize((320, 180))
                diff = ImageStat.Stat(ImageChops.difference(a2, b2)).mean[0]
            compared.append({"frame": ref.name, "mean_abs_diff": round(diff, 2)})
            if diff > threshold:
                drifted.append(ref.name)
                findings.append(_f("critical", "golden_drift", f"{ref.name} differs from golden by {diff:.1f} (threshold {threshold})"))
        return {"dir": str(gd), "threshold": threshold, "compared": compared, "drifted": drifted, "missing_golden": missing}


def _f(severity: str, code: str, message: str) -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}
