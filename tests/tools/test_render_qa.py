"""render_qa: the compose gate must pass a sane render and fail a broken one."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tools.analysis.render_qa import RenderQA

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg not installed")


def _make(path: Path, *, video: str, audio: bool = True, seconds: int = 4) -> Path:
    sep = ":" if "=" in video else "="
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"{video}{sep}size=320x180:rate=24"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
    cmd += ["-t", str(seconds), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast"]
    if audio:
        cmd += ["-c:a", "aac"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def test_good_render_passes_and_writes_report(tmp_path: Path) -> None:
    src = _make(tmp_path / "good.mp4", video="testsrc2")
    res = RenderQA().execute({"input_path": str(src), "expected_duration_seconds": 4, "output_dir": str(tmp_path / "qa")})
    assert res.success, res.error
    assert res.data["passed"] is True, res.data["findings"]
    assert (tmp_path / "qa" / "render_qa.json").is_file()
    assert len([f for f in res.data["frames"] if f["path"]]) == 5
    assert res.data["gate"]["passed"] is True


def test_black_and_silent_render_fails(tmp_path: Path) -> None:
    src = _make(tmp_path / "bad.mp4", video="color=c=black", audio=False)
    res = RenderQA().execute({"input_path": str(src), "output_dir": str(tmp_path / "qa")})
    assert res.success
    codes = {f["code"] for f in res.data["findings"]}
    assert res.data["passed"] is False
    assert "no_audio_stream" in codes
    assert "black_frame" in codes


def test_duration_drift_is_critical(tmp_path: Path) -> None:
    src = _make(tmp_path / "short.mp4", video="testsrc2", seconds=2)
    res = RenderQA().execute({"input_path": str(src), "expected_duration_seconds": 4, "output_dir": str(tmp_path / "qa")})
    assert any(f["code"] == "duration_drift" for f in res.data["findings"])
    assert res.data["passed"] is False


def test_golden_frames_detect_regression(tmp_path: Path) -> None:
    golden = tmp_path / "golden"
    a = _make(tmp_path / "a.mp4", video="testsrc2")
    first = RenderQA().execute({"input_path": str(a), "output_dir": str(tmp_path / "qa1"), "golden_dir": str(golden), "update_golden": True})
    assert first.data["passed"] and first.data["golden"]["missing_golden"]
    # same content again: no drift
    again = RenderQA().execute({"input_path": str(a), "output_dir": str(tmp_path / "qa2"), "golden_dir": str(golden)})
    assert again.data["passed"], again.data["findings"]
    assert again.data["golden"]["drifted"] == []
    # different picture: drift
    b = _make(tmp_path / "b.mp4", video="smptebars")
    drift = RenderQA().execute({"input_path": str(b), "output_dir": str(tmp_path / "qa3"), "golden_dir": str(golden)})
    assert drift.data["passed"] is False
    assert drift.data["golden"]["drifted"]
