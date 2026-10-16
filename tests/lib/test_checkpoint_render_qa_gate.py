"""compose may only complete with a passing render_qa report (or an explicit waiver)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.checkpoint import CheckpointValidationError, init_project, write_checkpoint

RENDER_REPORT = {
    "version": "1.0",
    "outputs": [{"path": "renders/final.mp4", "format": "mp4", "resolution": "1920x1080", "duration_seconds": 20.0}],
}


def _ready_project(tmp_path: Path) -> tuple[Path, str]:
    pid = "gate-test"
    init_project(pid, title="Gate", pipeline_type="framework-smoke", pipeline_dir=tmp_path)
    return tmp_path, pid


def _write_compose(root: Path, pid: str, metadata: dict | None):
    report = dict(RENDER_REPORT)
    if metadata is not None:
        report["metadata"] = metadata
    return write_checkpoint(
        root, pid, "compose", "completed", {"render_report": report},
        pipeline_type="unknown", human_approved=True,
    )


def test_compose_completed_without_render_qa_is_refused(tmp_path: Path) -> None:
    root, pid = _ready_project(tmp_path)
    with pytest.raises(CheckpointValidationError, match="RENDER QA GATE"):
        _write_compose(root, pid, None)
    with pytest.raises(CheckpointValidationError, match="RENDER QA GATE"):
        _write_compose(root, pid, {"render_qa": {"passed": False, "critical": 2}})


def test_compose_completed_with_passing_gate_is_written(tmp_path: Path) -> None:
    root, pid = _ready_project(tmp_path)
    path = _write_compose(root, pid, {"render_qa": {"passed": True, "report_path": "renders/review/render_qa/render_qa.json"}})
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "completed"


def test_waiver_needs_a_reason(tmp_path: Path) -> None:
    root, pid = _ready_project(tmp_path)
    with pytest.raises(CheckpointValidationError, match="RENDER QA GATE"):
        _write_compose(root, pid, {"render_qa": {"waived": True}})
    _write_compose(root, pid, {"render_qa": {"waived": True, "reason": "user accepted the black end card"}})


def test_only_completed_compose_is_gated(tmp_path: Path) -> None:
    root, pid = _ready_project(tmp_path)
    write_checkpoint(root, pid, "compose", "in_progress", {}, pipeline_type="unknown")
    write_checkpoint(root, pid, "compose", "awaiting_human", {"render_report": RENDER_REPORT}, pipeline_type="unknown")
