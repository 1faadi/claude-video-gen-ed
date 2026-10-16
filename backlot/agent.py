"""Backlot agent bridge — drive Claude Code from the board.

Spawns ``claude -p --output-format stream-json`` in the repo root, so every
run uses the user's own Claude Code login and usage limits (no API key).
One session per project, resumed with ``--resume`` so the conversation keeps
its context across prompts. Transcripts persist under ``.backlot/chat/``.

The bridge never writes into ``projects/`` itself — the agent does, through
the normal tools — so the board's "disk is truth" contract holds.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional

from backlot.state import REPO_ROOT

CHAT_DIR = REPO_ROOT / ".backlot" / "chat"
SESSIONS_FILE = REPO_ROOT / ".backlot" / "sessions.json"

# Tools a dashboard-driven run may use without a human at the terminal.
# Covers the OpenMontage toolchain (python tools, ffmpeg, remotion) and
# file I/O; anything else is denied, not prompted (--permission-prompts none).
_SHELL_CMDS = ["python *", "python3 *", "py *", "uv *", "ffmpeg *", "ffprobe *", "npx *", "npm *", "node *",
               "ls *", "cat *", "mkdir *", "cd *", "git status*", "git diff*", "git log*"]
_PS_CMDS = _SHELL_CMDS + ["Get-ChildItem *", "Get-Content *", "Set-Location *", "New-Item *", "dir *", "Test-Path *",
                          "Get-Item *", "Copy-Item *", "Select-String *", "Write-Output *", "echo *"]
DEFAULT_ALLOWED_TOOLS = (
    ["Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch"]
    + [f"Bash({c})" for c in _SHELL_CMDS]
    + [f"PowerShell({c})" for c in _PS_CMDS]  # Windows: Claude Code prefers the PowerShell tool
)
# Sibling repos the agent may read for brand/theme context ("use my other
# project's design system"). Default: the folder that contains this repo.
ADD_DIRS = [d for d in os.environ.get("BACKLOT_ADD_DIRS", str(REPO_ROOT.parent)).split(os.pathsep) if d]

LIBRARY_ID = "_library"  # chat scope when no project is open
PREFERRED_RUNTIME = os.environ.get("BACKLOT_PREFERRED_RUNTIME", "remotion")


def _system_prompt(project_id: Optional[str]) -> str:
    scope = (
        f"The user is looking at the Backlot board for project '{project_id}' "
        f"(workspace projects/{project_id}/)."
        if project_id and project_id != LIBRARY_ID
        else "The user is on the Backlot library page (no project open). "
             "If they ask for a new production, init a project via lib.checkpoint.init_project "
             "and tell them its project id."
    )
    return (
        "You are being driven from the OpenMontage Backlot dashboard, non-interactively. "
        f"{scope} "
        "Follow AGENT_GUIDE.md exactly as in a terminal session. "
        "There is no terminal: when you reach a human approval gate or need a decision, "
        "end your turn with the question stated plainly; the user's next message answers it "
        "and the conversation resumes. Never wait or poll for input mid-turn. "
        "When you stop at a gate, the last thing you write must be a numbered list of the exact "
        "decisions you need, each with your recommended answer, so the user can reply 'approve' or 'go with your picks'. "
        f"Render runtime preference for this user: {PREFERRED_RUNTIME}. Still present both runtimes as "
        f"AGENT_GUIDE requires, but recommend {PREFERRED_RUNTIME} unless the brief clearly needs the other. "
        "Edit requests from the board's cutting room may name a CSS selector, an element's text and a "
        "timeline time inside the composition source (hyperframes/index.html or the Remotion scene): change "
        "exactly that element/moment, keep everything else identical, run the runtime checks, re-render "
        "renders/final.mp4 as a new version, and summarize the diff. Such edits need no approval gate. "
        "Write replies in plain markdown (headings, lists, code fences for JSON/paths); no raw asterisks."
    )


def adopt_library_session(scope: str) -> bool:
    """A production started from the library page keeps its chat in the
    library scope. When its board opens with no chat of its own, inherit the
    library session + transcript so the conversation continues in place."""
    if scope == LIBRARY_ID or _load_sessions().get(scope):
        return False
    lib_session = _load_sessions().get(LIBRARY_ID)
    lib_log = CHAT_DIR / f"{LIBRARY_ID}.jsonl"
    if not lib_session or not lib_log.is_file():
        return False
    text = lib_log.read_text(encoding="utf-8")
    if scope not in text:
        return False
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    (CHAT_DIR / f"{scope}.jsonl").write_text(text, encoding="utf-8")
    _save_session(scope, lib_session)
    _append_transcript(scope, {"role": "system", "ts": time.time(),
                               "text": "conversation continued from the library page"})
    return True


def _load_sessions() -> dict[str, str]:
    try:
        return json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_session(scope: str, session_id: str) -> None:
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = _load_sessions()
    data[scope] = session_id
    SESSIONS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def reset_session(scope: str) -> None:
    data = _load_sessions()
    if data.pop(scope, None) is not None:
        SESSIONS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log = CHAT_DIR / f"{scope}.jsonl"
    if log.is_file():
        log.unlink()


def read_transcript(scope: str, limit: int = 400) -> list[dict]:
    log = CHAT_DIR / f"{scope}.jsonl"
    if not log.is_file():
        return []
    lines = log.read_text(encoding="utf-8").splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _append_transcript(scope: str, entry: dict) -> None:
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    with (CHAT_DIR / f"{scope}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _compact(raw: dict) -> Optional[dict]:
    """Reduce a stream-json line to what the dock renders. None = drop."""
    t = raw.get("type")
    if t == "stream_event":
        # live typing: text / thinking deltas (broadcast only, never persisted)
        ev = raw.get("event") or {}
        if ev.get("type") == "content_block_delta":
            d = ev.get("delta") or {}
            if d.get("type") == "text_delta" and d.get("text"):
                return {"role": "delta", "kind": "text", "text": d["text"], "_live": True}
            if d.get("type") == "thinking_delta" and d.get("thinking"):
                return {"role": "delta", "kind": "thinking", "text": d["thinking"], "_live": True}
        elif ev.get("type") == "content_block_start":
            cb = ev.get("content_block") or {}
            if cb.get("type") == "tool_use":
                return {"role": "delta", "kind": "tool_start", "name": cb.get("name"), "id": cb.get("id"), "_live": True}
        return None
    if t == "assistant":
        blocks = []
        for b in (raw.get("message") or {}).get("content") or []:
            if b.get("type") == "text" and b.get("text"):
                blocks.append({"kind": "text", "text": b["text"]})
            elif b.get("type") == "thinking" and b.get("thinking"):
                blocks.append({"kind": "thinking", "text": b["thinking"]})
            elif b.get("type") == "tool_use":
                inp = b.get("input") or {}
                hint = (inp.get("description") or inp.get("command") or inp.get("file_path")
                        or inp.get("pattern") or inp.get("prompt") or inp.get("query") or "")
                blocks.append({"kind": "tool", "id": b.get("id"), "name": b.get("name"), "hint": str(hint)[:200]})
        return {"role": "assistant", "blocks": blocks} if blocks else None
    if t == "user":
        results = []
        for b in (raw.get("message") or {}).get("content") or []:
            if b.get("type") == "tool_result":
                content = b.get("content")
                if isinstance(content, list):
                    content = " ".join(x.get("text", "") for x in content if isinstance(x, dict))
                text = str(content or "")
                results.append({"id": b.get("tool_use_id"), "ok": not b.get("is_error"),
                                "text": text[-400:] if b.get("is_error") else text[:160]})
        return {"role": "tool_result", "results": results} if results else None
    if t == "rate_limit_event":
        info = raw.get("rate_limit_info") or {}
        windows = info.get("unifiedWindows") or {}
        return {"role": "limits", "status": info.get("status"), "windows": windows}
    if t == "result":
        return {
            "role": "result",
            "ok": not raw.get("is_error"),
            "cost_usd": raw.get("total_cost_usd"),
            "duration_ms": raw.get("duration_ms"),
            "error": raw.get("result") if raw.get("is_error") else None,
        }
    return None


class AgentRun:
    """One in-flight `claude -p` process for a scope, with SSE fan-out."""

    def __init__(self, scope: str) -> None:
        self.scope = scope
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.subscribers: set[asyncio.Queue] = set()
        self.done = asyncio.Event()
        self.stderr_tail: deque[str] = deque(maxlen=40)

    def _broadcast(self, entry: dict) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                pass

    async def start(self, prompt: str, project_id: Optional[str]) -> None:
        exe = shutil.which("claude")
        if not exe:
            raise RuntimeError("claude CLI not found on PATH")
        # Headless: edits auto-accepted, allow-listed tools run, anything else
        # is denied (never prompts, never hangs). Widen DEFAULT_ALLOWED_TOOLS
        # if a pipeline needs more.
        args = [exe, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                "--model", os.environ.get("BACKLOT_AGENT_MODEL", "opus"),
                "--append-system-prompt", _system_prompt(project_id),
                "--permission-mode", "acceptEdits", "--permission-prompts", "none"]
        for d in ADD_DIRS:
            args += ["--add-dir", d]
        args += ["--allowedTools", *DEFAULT_ALLOWED_TOOLS]
        session_id = _load_sessions().get(self.scope)
        if session_id:
            args += ["--resume", session_id]
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}  # allow nesting
        self.proc = await asyncio.create_subprocess_exec(
            *args, cwd=str(REPO_ROOT), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=256 * 1024 * 1024,  # one stream-json line can carry a base64 image (Read on a .jpg)
        )
        user_entry = {"role": "user", "text": prompt, "ts": time.time()}
        _append_transcript(self.scope, user_entry)
        self._broadcast(user_entry)
        assert self.proc.stdin
        self.proc.stdin.write(prompt.encode("utf-8"))
        await self.proc.stdin.drain()
        self.proc.stdin.close()
        asyncio.create_task(self._pump())

    async def _drain_stderr(self) -> None:
        # Must drain concurrently: an undrained pipe fills (~64 KB) and the
        # child blocks on write → the run silently stalls mid-production.
        assert self.proc and self.proc.stderr
        async for raw_line in self.proc.stderr:
            self.stderr_tail.append(raw_line.decode("utf-8", "replace").rstrip())

    async def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        resumed_ok = True
        asyncio.create_task(self._drain_stderr())
        try:
            async for raw_line in self.proc.stdout:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line.startswith("{"):
                    continue
                try:
                    raw = json.loads(line)
                except Exception:
                    continue
                if raw.get("type") == "system" and raw.get("subtype") == "init" and raw.get("session_id"):
                    _save_session(self.scope, raw["session_id"])
                entry = _compact(raw)
                if entry:
                    entry["ts"] = time.time()
                    if not entry.pop("_live", False):
                        _append_transcript(self.scope, entry)
                    self._broadcast(entry)
            rc = await self.proc.wait()
            if rc != 0:
                err = "\n".join(self.stderr_tail)[-600:]
                if "No conversation found" in err or "session" in err.lower() and "not found" in err.lower():
                    resumed_ok = False
                entry = {"role": "system", "text": f"claude exited {rc}: {err.strip() or 'no stderr'}", "ts": time.time()}
                _append_transcript(self.scope, entry)
                self._broadcast(entry)
        except Exception as exc:  # reader failure must never orphan the child silently
            self.stop()
            entry = {"role": "system", "text": f"bridge error, run stopped: {type(exc).__name__}: {str(exc)[:300]}", "ts": time.time()}
            _append_transcript(self.scope, entry)
            self._broadcast(entry)
        finally:
            if not resumed_ok:
                # stale session id → drop it so the next prompt starts fresh
                data = _load_sessions(); data.pop(self.scope, None)
                SESSIONS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self.done.set()
            self._broadcast({"role": "done", "ts": time.time()})
            RUNS.pop(self.scope, None)

    def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.kill()


RUNS: dict[str, AgentRun] = {}  # ponytail: one run per scope, in-process; fine for a local board
LISTENERS: dict[str, set[asyncio.Queue]] = {}  # open SSE streams per scope, attached to each new run


async def send(scope: str, prompt: str) -> AgentRun:
    if scope in RUNS:
        raise RuntimeError("a run is already in progress for this project")
    run = AgentRun(scope)
    run.subscribers.update(LISTENERS.get(scope, ()))
    RUNS[scope] = run
    try:
        await run.start(prompt, None if scope == LIBRARY_ID else scope)
    except Exception:
        RUNS.pop(scope, None)
        raise
    return run


def status(scope: str) -> dict[str, Any]:
    return {
        "running": scope in RUNS,
        "session_id": _load_sessions().get(scope),
        "claude_found": bool(shutil.which("claude")),
    }
