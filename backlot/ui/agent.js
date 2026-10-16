// Studio panel — drive Claude Code from the board.
// Right-hand sidebar: run status card (state, elapsed, stage progress, ETA,
// plan usage) + a timeline of cards (prompt, thinking, reply, tool calls,
// result). Prompts go to /api/agent/<scope>/send; everything streams over SSE.
// scope = project id on a board, "_library" on the library page.

import { el, fmtMoney, getJSON, subscribe } from "/ui/lib.js";
import { renderMarkdown } from "/ui/md.js";

const mdBody = (text) => { const n = el("div", { class: "st-body md" }); n.innerHTML = renderMarkdown(text); return n; };

const LIBRARY = "_library";

const QUICK = {
  carousel: (scope) => scope === LIBRARY
    ? "Make a LinkedIn carousel (PDF + PNG slides) about: <topic>. Brand/theme: <describe or name a style playbook>. Read skills/meta/linkedin-carousel.md first."
    : "Make a LinkedIn carousel for this project on its brand/style theme, about: <topic>. Read skills/meta/linkedin-carousel.md first.",
  image: (scope) => scope === LIBRARY
    ? "Generate a single on-brand image: <describe>. Style: <style playbook or brand notes>. Save it under projects/<project>/assets/images/."
    : "Generate an on-brand still image for this project: <describe>. Use the project's style playbook; save under assets/images/.",
  video: () => "Make a <length> <pipeline> video about: <topic>. Run the full pipeline; pause at approval gates.",
  resume: () => "Continue the production from the last checkpoint. Summarize where it stands first.",
  approve: () => "Approved. Continue to the next stage.",
};

const TOOL_ICON = (name = "") => {
  const n = name.toLowerCase();
  if (n === "bash" || n === "powershell") return "⌘";
  if (n === "read" || n === "glob" || n === "grep") return "⌕";
  if (n === "write" || n === "edit") return "✎";
  if (n.includes("web")) return "⇗";
  if (n === "task" || n === "agent") return "⧉";
  return "⚙";
};

const fmtElapsed = (s) => {
  s = Math.max(0, Math.round(s));
  const m = Math.floor(s / 60), h = Math.floor(m / 60);
  return h ? `${h}h ${m % 60}m` : m ? `${m}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`;
};

export function mountAgentDock(scope) {
  const api = (p) => `/api/agent/${encodeURIComponent(scope)}/${p}`;
  const OPEN_KEY = `backlot.studio.${scope}`;

  // ---- DOM ------------------------------------------------------------
  const statePill = el("span", { class: "st-pill idle" }, "idle");
  const elapsedEl = el("span", { class: "st-elapsed" }, "");
  const phaseEl = el("div", { class: "st-phase" }, "Ready. Tell the agent what to make.");
  const progFill = el("i", {});
  const progBar = el("div", { class: "st-prog" }, progFill);
  const progText = el("div", { class: "st-prog-text" }, "");
  const etaEl = el("span", {}, "");
  const usageEl = el("div", { class: "st-usage" });
  const runCard = el("div", { class: "st-card st-run" },
    el("div", { class: "st-row" }, statePill, elapsedEl, el("span", { class: "spacer" }), etaEl),
    phaseEl, progBar, progText, usageEl);

  const timeline = el("div", { class: "st-timeline" });
  const input = el("textarea", { class: "st-input", rows: "3",
    placeholder: scope === LIBRARY ? "New production, carousel, image… (Enter to send)" : "What should the agent do on this project? (Enter to send)" });
  const sendBtn = el("button", { class: "st-btn primary", type: "button" }, "Send");
  const stopBtn = el("button", { class: "st-btn danger", type: "button", disabled: "" }, "Stop");
  const resetBtn = el("button", { class: "st-btn ghost", type: "button", title: "Forget this conversation" }, "New chat");
  const hint = el("span", { class: "st-hint" }, "");
  const quick = el("div", { class: "st-quick" },
    ...Object.entries(QUICK).map(([k, fn]) => el("button", { class: "st-chip", type: "button",
      onclick: () => { input.value = fn(scope); selectPlaceholder(); } }, k)));

  const toggle = el("button", { class: "st-toggle", type: "button", title: "Toggle studio agent" }, "✦ Studio");
  const closeBtn = el("button", { class: "st-close", type: "button", title: "Collapse" }, "→");
  const panel = el("aside", { class: "studio" },
    el("div", { class: "st-head" }, el("span", { class: "st-wordmark" }, "Studio agent"),
      el("span", { class: "st-scope" }, scope === LIBRARY ? "library" : scope), closeBtn),
    runCard, timeline,
    el("div", { class: "st-compose" }, quick, input, el("div", { class: "st-row" }, sendBtn, stopBtn, resetBtn, hint)));
  document.body.append(toggle, panel);

  let open = localStorage.getItem(OPEN_KEY) !== "0";
  const setOpen = (v) => {
    open = v;
    document.documentElement.classList.toggle("studio-open", open);
    localStorage.setItem(OPEN_KEY, open ? "1" : "0");
  };
  toggle.onclick = () => setOpen(!open);
  closeBtn.onclick = () => setOpen(false);
  setOpen(open);

  // ---- run state --------------------------------------------------------
  let running = false, runStart = null, timer = null, phase = "idle";
  const live = { thinking: null, text: null, tools: new Map() }; // streaming cards

  function setPhase(p, label) {
    phase = p;
    statePill.className = `st-pill ${p}`;
    statePill.textContent = label || p;
    toggle.classList.toggle("busy", p !== "idle" && p !== "waiting");
    toggle.classList.toggle("waiting", p === "waiting");
  }

  function setRunning(v, startTs) {
    running = v;
    sendBtn.disabled = v; stopBtn.disabled = !v; resetBtn.disabled = v;
    if (v) {
      runStart = startTs || runStart || Date.now() / 1000;
      if (!timer) timer = setInterval(() => { elapsedEl.textContent = fmtElapsed(Date.now() / 1000 - runStart); }, 1000);
      if (phase === "idle" || phase === "waiting") setPhase("thinking", "thinking");
    } else {
      clearInterval(timer); timer = null;
      finishLive();
      // Nothing is running any more: no tool card may keep spinning.
      for (const n of live.tools.values()) settleTool(n, false, "interrupted — run ended before this tool reported back");
      live.tools.clear();
      if (phase !== "waiting") setPhase("idle", "idle");
      if (!phaseEl.textContent.startsWith("Turn complete")) phaseEl.textContent = "Not running. Send a message to continue.";
      refreshProgress();
    }
  }

  // Stage progress + ETA from the board's own state (projects only).
  async function refreshProgress() {
    if (scope === LIBRARY) { progBar.style.display = "none"; progText.style.display = "none"; return; }
    let s; try { s = await getJSON(`/api/project/${encodeURIComponent(scope)}/state`); } catch { return; }
    const stages = s.stages || [];
    if (!stages.length) { progBar.style.display = "none"; progText.style.display = "none"; return; }
    const done = stages.filter((x) => x.status === "completed").length;
    const active = stages.find((x) => x.status === "in_progress" || x.status === "awaiting_human");
    const pct = Math.round(((done + (active ? 0.5 : 0)) / stages.length) * 100);
    progFill.style.width = `${pct}%`;
    progText.textContent = `${done}/${stages.length} stages · ${pct}%` + (active ? ` · ${active.name.replace("_", " ")}` : "");
    const created = s.created_at ? Date.parse(s.created_at) / 1000 : null;
    const lastDone = stages.filter((x) => x.status === "completed" && x.timestamp)
      .map((x) => Date.parse(x.timestamp) / 1000).sort().pop();
    if (created && lastDone && done > 0 && done < stages.length) {
      const per = (lastDone - created) / done;
      etaEl.textContent = `~${fmtElapsed(per * (stages.length - done))} left`;
      etaEl.title = "estimate from average stage time so far";
    } else etaEl.textContent = done === stages.length && done ? "complete" : "";
    if (active && active.status === "awaiting_human" && !running) {
      setPhase("waiting", "needs your approval");
      phaseEl.textContent = `Stage “${active.name}” is waiting for you — reply here to approve or change it.`;
      gateCard(active, s);
    } else if (gate.node) { gate.node.remove(); gate.node = null; gate.key = null; }
  }

  // One approval card per gated stage version: what's waiting, where to look, two buttons.
  const gate = { node: null, key: null };
  function gateCard(stage, s) {
    const key = `${stage.name}@${stage.timestamp || ""}`;
    if (gate.key === key && gate.node && gate.node.isConnected) return;
    if (gate.node) gate.node.remove();
    const review = stage.review || {};
    const summary = review.summary || review.verdict || "";
    const produces = (stage.produces || []).join(", ");
    const approveBtn = el("button", { class: "st-btn primary", type: "button",
      onclick: () => { input.value = `Approved: ${stage.name}. Go with your recommended picks for anything I didn't answer. Continue to the next stage.`; send(); } }, "Approve");
    const changeBtn = el("button", { class: "st-btn ghost", type: "button",
      onclick: () => { input.value = `On the ${stage.name} stage, change: `; input.focus(); input.setSelectionRange(input.value.length, input.value.length); } }, "Request changes");
    gate.key = key;
    gate.node = push(card("gate",
      el("div", { class: "st-label" }, `approval needed · ${stage.name.replace("_", " ")}`),
      el("div", { class: "st-body" },
        `The agent paused at the ${stage.name.replace("_", " ")} gate.` +
        (produces ? ` Review ${produces} on the board (left).` : "") +
        (summary ? `\n\n${String(summary).slice(0, 400)}` : "") +
        `\n\nThe agent's questions are in its last message above. Reply here, or:`),
      el("div", { class: "st-row", style: "margin-top:8px" }, approveBtn, changeBtn)));
  }

  function renderUsage(windows) {
    usageEl.innerHTML = "";
    const names = { five_hour: "5h window", seven_day: "7d window", seven_day_overage_included: "7d window" };
    for (const [k, w] of Object.entries(windows)) {
      const pct = Math.round((w.utilization || 0) * 100);
      const reset = w.resetsAt ? new Date(w.resetsAt * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "";
      usageEl.append(el("span", { class: `st-limit${pct > 85 ? " hot" : ""}`, title: `Claude plan usage · resets ${reset}` },
        el("b", {}, names[k] || k), el("i", {}, el("u", { style: `width:${pct}%` })), `${pct}%`));
    }
  }

  // ---- timeline cards -------------------------------------------------
  const card = (cls, ...kids) => el("div", { class: `st-card ${cls}` }, ...kids);
  const push = (node) => { timeline.append(node); timeline.scrollTop = timeline.scrollHeight; return node; };

  function toolCard(b) {
    const spin = el("span", { class: "st-spin" });
    const node = card("tool",
      el("div", { class: "st-tool-head" }, el("span", { class: "st-tool-ic" }, TOOL_ICON(b.name)), el("b", {}, b.name || "tool"), spin),
      b.hint ? el("div", { class: "st-tool-hint" }, b.hint) : null);
    node.dataset.start = String(Date.now());
    return node;
  }
  function settleTool(node, ok, text) {
    node.classList.add(ok ? "ok" : "err");
    const spin = node.querySelector(".st-spin");
    const dur = fmtElapsed((Date.now() - Number(node.dataset.start || Date.now())) / 1000);
    if (spin) spin.replaceWith(el("span", { class: "st-tool-done" }, ok ? `✓ ${dur}` : `✕ ${dur}`));
    if (!ok && text) node.append(el("div", { class: "st-tool-err" }, text));
  }

  function finishLive() {
    if (live.thinking) { live.thinking.classList.remove("live"); live.thinking = null; }
    if (live.text) { live.text.classList.remove("live"); live.text = null; }
  }

  function append(entry) {
    switch (entry.role) {
      case "user":
        finishLive();
        push(card("user", el("div", { class: "st-label" }, "you"), el("div", { class: "st-body" }, entry.text)));
        phaseEl.textContent = "Reading the brief…";
        setPhase("thinking", "thinking");
        break;
      case "delta":
        if (entry.kind === "thinking") {
          if (!live.thinking) {
            live.thinking = push(card("thinking live", el("div", { class: "st-label" }, "thinking"), el("div", { class: "st-body" })));
            live.thinking.onclick = () => live.thinking && live.thinking.classList.toggle("expanded");
            setPhase("thinking", "thinking");
            phaseEl.textContent = "Reasoning about the next step…";
          }
          live.thinking.querySelector(".st-body").append(entry.text);
          timeline.scrollTop = timeline.scrollHeight;
        } else if (entry.kind === "text") {
          if (!live.text) {
            if (live.thinking) { live.thinking.classList.remove("live"); live.thinking = null; }
            live.text = push(card("assistant live", el("div", { class: "st-label" }, "agent"), el("div", { class: "st-body" })));
            setPhase("working", "writing");
          }
          live.text.querySelector(".st-body").append(entry.text);
          timeline.scrollTop = timeline.scrollHeight;
        } else if (entry.kind === "tool_start") {
          finishLive();
          const node = push(toolCard(entry));
          if (entry.id) live.tools.set(entry.id, node);
          setPhase("working", "working");
          phaseEl.textContent = `Running ${entry.name}…`;
        }
        break;
      case "assistant": {
        // Final message: replace live cards with the settled content.
        if (live.thinking) { live.thinking.remove(); live.thinking = null; }
        if (live.text) { live.text.remove(); live.text = null; }
        for (const b of entry.blocks || []) {
          if (b.kind === "thinking") {
            const n = push(card("thinking", el("div", { class: "st-label" }, "thinking"), el("div", { class: "st-body" }, b.text)));
            n.onclick = () => n.classList.toggle("expanded");
          } else if (b.kind === "text") {
            push(card("assistant", el("div", { class: "st-label" }, "agent"), mdBody(b.text)));
            phaseEl.textContent = b.text.split("\n")[0].slice(0, 120);
          } else if (b.kind === "tool") {
            if (b.id && live.tools.has(b.id)) {
              const n = live.tools.get(b.id);
              if (b.hint && !n.querySelector(".st-tool-hint")) n.append(el("div", { class: "st-tool-hint" }, b.hint));
            } else {
              const n = push(toolCard(b));
              if (b.id) live.tools.set(b.id, n);
              if (!running) settleTool(n, true);  // replayed from transcript
            }
            setPhase("working", "working");
            phaseEl.textContent = `Running ${b.name}${b.hint ? " · " + b.hint.slice(0, 80) : ""}`;
          }
        }
        break;
      }
      case "tool_result":
        for (const r of entry.results || []) {
          const n = live.tools.get(r.id);
          if (n) { settleTool(n, r.ok, r.ok ? "" : r.text); live.tools.delete(r.id); }
          else if (!r.ok) push(card("error", el("div", { class: "st-label" }, "tool error"), el("div", { class: "st-body" }, r.text)));
        }
        if (running) { setPhase("thinking", "thinking"); phaseEl.textContent = "Reviewing tool output…"; }
        break;
      case "limits":
        renderUsage(entry.windows || {});
        break;
      case "result":
        finishLive();
        for (const n of live.tools.values()) settleTool(n, true);
        live.tools.clear();
        push(card(entry.ok ? "result" : "error",
          el("div", { class: "st-label" }, entry.ok ? "turn complete" : "turn failed"),
          el("div", { class: "st-body" }, entry.ok
            ? `${fmtElapsed((entry.duration_ms || 0) / 1000)} · ${fmtMoney(entry.cost_usd)} est. API-equivalent (plan-covered)`
            : entry.error || "unknown error")));
        phaseEl.textContent = entry.ok ? "Turn complete — reply to continue." : "Turn failed — see card.";
        setPhase(entry.ok ? "waiting" : "idle", entry.ok ? "your turn" : "idle");
        break;
      case "system":
        push(card("sys", el("div", { class: "st-body" }, entry.text)));
        break;
    }
  }

  // ---- compose --------------------------------------------------------
  function selectPlaceholder(from = 0) {
    const m = /<[^<>\n]+>/.exec(input.value.slice(from));
    if (!m) return false;
    input.focus();
    input.setSelectionRange(from + m.index, from + m.index + m[0].length);
    return true;
  }

  async function send() {
    const prompt = input.value.trim();
    if (!prompt || running) return;
    if (selectPlaceholder()) { hint.textContent = "fill in the <placeholders> first"; return; }
    hint.textContent = "";
    setRunning(true, Date.now() / 1000);
    input.value = "";
    const res = await fetch(api("send"), { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }) });
    if (!res.ok) {
      append({ role: "system", text: `send failed: ${(await res.json().catch(() => ({}))).detail || res.status}` });
      setRunning(false);
      input.value = prompt;
    }
  }
  sendBtn.onclick = send;
  // Other board modules (cutting room) hand prompts to the agent through this.
  window.backlotStudio = {
    send: (prompt) => { if (running) { hint.textContent = "agent busy — wait for the turn to finish"; setOpen(true); return false; } input.value = prompt; setOpen(true); send(); return true; },
    isRunning: () => running,
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    else if (e.key === "Tab" && !e.shiftKey && selectPlaceholder(input.selectionEnd)) e.preventDefault();
  });
  stopBtn.onclick = () => { fetch(api("stop"), { method: "POST" }); phaseEl.textContent = "Stopping…"; };
  resetBtn.onclick = async () => {
    if (running) return;
    await fetch(api("reset"), { method: "POST" });
    timeline.innerHTML = "";
    append({ role: "system", text: "new session — the agent starts with no memory of earlier prompts" });
    phaseEl.textContent = "Ready.";
    setPhase("idle", "idle");
  };

  // ---- boot ----------------------------------------------------------
  (async () => {
    const st = await getJSON(api("status")).catch(() => ({}));
    if (st.claude_found === false) append({ role: "system", text: "claude CLI not found on PATH — install Claude Code to use the studio agent" });
    const transcript = await getJSON(api("transcript")).catch(() => []);
    for (const e of transcript) append(e);
    finishLive();
    const lastUser = [...transcript].reverse().find((e) => e.role === "user");
    const last = [...transcript].reverse().find((e) => ["result", "user", "assistant", "tool_result"].includes(e.role));
    if (!st.running && last && last.role !== "result") {
      append({ role: "system", text: "previous run was interrupted (server restarted or stopped) — send a message to resume; the session remembers where it was" });
    }
    setRunning(Boolean(st.running), lastUser ? lastUser.ts : undefined);
    if (!st.running) refreshProgress();
    if (scope !== LIBRARY) subscribe(`/api/project/${encodeURIComponent(scope)}/events`, () => { if (!running) refreshProgress(); });
    const src = new EventSource(api("stream"));
    src.onmessage = (m) => {
      let e; try { e = JSON.parse(m.data); } catch { return; }
      if (e.role === "hello" || e.role === "heartbeat") { if (Boolean(e.running) !== running) setRunning(Boolean(e.running)); return; }
      if (e.role === "done") { setRunning(false); return; }
      if (e.role === "user") setRunning(true, e.ts);
      append(e);
    };
  })();
}
