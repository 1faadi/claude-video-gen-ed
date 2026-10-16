"""Backlot editor launcher — open a project's composition in its runtime's
visual editor (HyperFrames Studio / Remotion Studio).

Both runtimes ship a real timeline editor: ``npx hyperframes preview`` serves
Studio for an authored workspace, ``npx remotion studio`` does the same for a
Remotion composition. Backlot only starts/stops them per project and hands the
URL to the board; edits happen in those editors, re-rendering goes through the
agent (so artifacts and the render stay in sync).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from backlot.state import REPO_ROOT

EDITORS: dict[str, dict[str, Any]] = {}  # project_id -> {proc, url, runtime, port, cwd}
_STATE_FILE = REPO_ROOT / ".backlot" / "editors.json"


def _persist() -> None:
    """Editors are detached servers that outlive the board process; remember
    them so a restarted board still finds (and can stop) them."""
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps({k: {x: v[x] for x in ("url", "runtime", "port", "cwd")} for k, v in EDITORS.items()},
                                      indent=2), encoding="utf-8")


def _restore() -> None:
    for pid, rec in (_read(_STATE_FILE) or {}).items():
        if pid not in EDITORS and _listening(int(rec.get("port", 0) or 0)):
            EDITORS[pid] = {"proc": None, **rec}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def composition_info(project_dir: Path) -> Optional[dict[str, Any]]:
    """What the board can edit: runtime + workspace, from edit_decisions /
    compose result. None when nothing has been composed yet."""
    ed = _read(project_dir / "artifacts" / "edit_decisions.json") or {}
    res = (_read(project_dir / "artifacts" / "_compose_result.json") or {}).get("data") or {}
    runtime = ed.get("render_runtime")
    meta = ed.get("metadata") or {}
    workspace = res.get("workspace") or meta.get("workspace_path")
    if runtime == "hyperframes" and not workspace and (project_dir / "hyperframes" / "index.html").is_file():
        workspace = str(project_dir / "hyperframes")
    if not runtime:
        return None
    ws = Path(workspace) if workspace else None
    if ws and not ws.is_absolute():
        ws = REPO_ROOT / ws
    entry = meta.get("entry") or res.get("entry") or (str(ws / "index.html") if ws and runtime == "hyperframes" else None)
    entry_path = Path(entry) if entry else None
    if entry_path and not entry_path.is_absolute():
        entry_path = REPO_ROOT / entry_path
    return {
        "runtime": runtime,
        "workspace": str(ws) if ws else None,
        "entry": entry,
        "entry_mtime": entry_path.stat().st_mtime if entry_path and entry_path.is_file() else None,
        "props_path": res.get("props_path") or meta.get("props_path"),
        "composition_id": res.get("composition_id") or meta.get("composition_id"),
        "editable": bool(ws and ws.is_dir()) if runtime == "hyperframes" else runtime == "remotion",
    }


def start(project_id: str, project_dir: Path) -> dict[str, Any]:
    _restore()
    current = EDITORS.get(project_id)
    if current and _listening(current["port"]):
        return status(project_id)
    info = composition_info(project_dir)
    if not info or not info["editable"]:
        raise RuntimeError("nothing composed yet — run the pipeline through compose first")
    npx = shutil.which("npx")
    if not npx:
        raise RuntimeError("npx not on PATH (install Node.js)")
    port = _free_port()
    if info["runtime"] == "hyperframes":
        ws = Path(info["workspace"])
        cmd = [npx, "--yes", "hyperframes", "preview", "--port", str(port)]
        cwd = ws
        url = f"http://localhost:{port}/#project/{ws.name}"
    elif info["runtime"] == "remotion":
        composer = REPO_ROOT / "remotion-composer"
        entry = info.get("entry") or "src/index.ts"
        cmd = [npx, "remotion", "studio", str(entry), "--port", str(port)]
        if info.get("props_path"):
            cmd.append(f"--props={info['props_path']}")
        cwd = composer
        url = f"http://localhost:{port}"
    else:
        raise RuntimeError(f"no visual editor for runtime {info['runtime']!r} (ffmpeg cuts: edit edit_decisions via the agent)")
    kwargs: dict[str, Any] = {"cwd": str(cwd), "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                              "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(cmd, **kwargs)
    # HyperFrames' launcher detaches the Studio server and exits, so liveness
    # is "the port answers", never the launcher pid. Give it time to boot.
    for _ in range(60):
        if _listening(port) or proc.poll() not in (None, 0):
            break
        time.sleep(0.5)
    if not _listening(port):
        raise RuntimeError(f"{info['runtime']} editor did not open port {port} (run `{' '.join(cmd[1:])}` in {cwd} to see why)")
    EDITORS[project_id] = {"proc": proc, "url": url, "runtime": info["runtime"], "port": port, "cwd": str(cwd)}
    _persist()
    return status(project_id)


def stop(project_id: str) -> bool:
    _restore()
    current = EDITORS.pop(project_id, None)
    _persist()
    if not current:
        return False
    proc = current.get("proc")
    if current["runtime"] == "hyperframes":
        npx = shutil.which("npx")
        if npx:
            subprocess.run([npx, "--yes", "hyperframes", "preview", "--port", str(current["port"]), "--stop"],
                           cwd=current["cwd"], capture_output=True, timeout=60)
    if proc is not None and proc.poll() is None:
        if os.name == "nt":  # npx spawns a node child; kill the tree
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.kill()
    return True


def status(project_id: str) -> dict[str, Any]:
    _restore()
    current = EDITORS.get(project_id)
    if not current or not _listening(current["port"]):
        if EDITORS.pop(project_id, None):
            _persist()
        return {"running": False}
    return {"running": True, "url": current["url"], "runtime": current["runtime"], "port": current["port"]}


def stop_all() -> None:
    for pid in list(EDITORS):
        stop(pid)


def _read(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
