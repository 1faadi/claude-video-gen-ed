// Cutting room — edit a composed video from the board. Renders into the
// persistent #editor container (outside #app) so the live canvas iframe
// survives board refreshes.
//
// Tabs:
//   Canvas  — the composition itself (hyperframes/index.html) in a same-origin
//             iframe driven through its GSAP timeline: play/scrub, click any
//             element to select it, edit its text instantly (exact-match
//             source replace) or ask the agent to change it.
//   Studio  — launches the runtime's own editor (HyperFrames / Remotion Studio)
//             in a new browser tab; nothing is embedded in the board.
//   Scenes  — per-scene text / visual / timing cards → agent edit.
//
// ponytail: canvas drives window.__timelines only (GSAP). Three.js / Lottie
// adapters (hf-seek) are not seeked here — Studio tab covers those.

import { el, fmtDuration, getJSON, thumbURL } from "/ui/lib.js";

const root = document.getElementById("editor");
const FIELDS = [
  ["narration", "Narration · on-screen text", "input"],
  ["description", "Visual direction", "textarea"],
  ["start_seconds", "Start", "number"],
  ["end_seconds", "End", "number"],
];

const ui = {
  tab: localStorage.getItem("backlot.cr.tab") || "canvas",
  editor: null,            // last /editor status
  state: null,             // last board state
  edits: new Map(),        // scene edits pending
  canvas: null,            // persistent canvas DOM {wrap, iframe, overlay, ...}
  sel: null,               // selected element inside iframe
};

const agent = () => window.backlotStudio;
const api = (s, p) => `/api/project/${encodeURIComponent(s.project_id)}/${p}`;

// ---------------------------------------------------------------------------
// canvas
// ---------------------------------------------------------------------------

function compositionSrc(s) {
  const comp = ui.editor && ui.editor.composition;
  if (!comp || comp.runtime !== "hyperframes") return null;
  const entry = (comp.entry || "").replace(/\\/g, "/");
  const idx = entry.indexOf(`projects/${s.project_id}/`);
  const rel = idx >= 0 ? entry.slice(idx + `projects/${s.project_id}/`.length) : "hyperframes/index.html";
  return { rel, url: `/media/${encodeURIComponent(s.project_id)}/${rel.split("/").map(encodeURIComponent).join("/")}` };
}

function timelineOf(iframe) {
  try {
    const w = iframe.contentWindow;
    const tls = w && w.__timelines;
    if (!tls) return null;
    return tls.main || Object.values(tls)[0] || null;
  } catch { return null; }
}

function buildCanvas(s) {
  const src = compositionSrc(s);
  if (!src) return null;
  const W = 1920, H = 1080;
  const iframe = el("iframe", { class: "cv-frame", src: `${src.url}?v=${Date.now()}`, title: "composition preview" });
  const overlay = el("div", { class: "cv-overlay" });
  const box = el("div", { class: "cv-box" });
  const stage = el("div", { class: "cv-stage" }, iframe, overlay, box);
  const playBtn = el("button", { class: "st-btn", type: "button" }, "▶");
  const slider = el("input", { class: "cv-slider", type: "range", min: "0", max: "1000", value: "0" });
  const timeEl = el("span", { class: "cv-time" }, "0:00 / 0:00");
  const popover = el("div", { class: "cv-pop" });
  const cv = { wrap: null, iframe, overlay, box, stage, playBtn, slider, timeEl, popover, tl: null, playing: false, scale: 1, raf: null, audio: null, rel: src.rel };

  const fit = () => {
    const w = stage.clientWidth || 960;
    cv.scale = w / W;
    stage.style.height = `${H * cv.scale}px`;
    iframe.style.transform = `scale(${cv.scale})`;
    if (ui.sel) highlight(cv);
  };
  window.addEventListener("resize", fit);

  const sync = () => {
    const tl = cv.tl;
    if (!tl) return;
    const t = tl.time(), d = tl.duration() || 1;
    slider.value = String(Math.round((t / d) * 1000));
    timeEl.textContent = `${fmtDuration(t)} / ${fmtDuration(d)}`;
    if (cv.playing && t >= d - 0.01) stop();
    cv.raf = requestAnimationFrame(sync);
  };
  const play = () => {
    if (!cv.tl) return;
    if (cv.tl.time() >= cv.tl.duration() - 0.01) cv.tl.seek(0);
    cv.tl.play(); cv.playing = true; playBtn.textContent = "❚❚";
    if (cv.audio) { try { cv.audio.currentTime = cv.tl.time(); cv.audio.play(); } catch {} }
  };
  const stop = () => {
    if (cv.tl) cv.tl.pause();
    cv.playing = false; playBtn.textContent = "▶";
    if (cv.audio) try { cv.audio.pause(); } catch {}
  };
  playBtn.onclick = () => (cv.playing ? stop() : play());
  slider.oninput = () => {
    if (!cv.tl) return;
    stop();
    cv.tl.seek((Number(slider.value) / 1000) * cv.tl.duration());
    if (cv.audio) try { cv.audio.currentTime = cv.tl.time(); } catch {}
    if (ui.sel) highlight(cv);
  };

  iframe.addEventListener("load", () => {
    const pending = cv.resumeAt;
    cv.resumeAt = null;
    // GSAP timeline is built synchronously at parse time; poll briefly in case
    // fonts/scripts delay it.
    let tries = 0;
    const grab = () => {
      cv.tl = timelineOf(iframe);
      try { cv.audio = iframe.contentDocument.querySelector("audio"); } catch { cv.audio = null; }
      if (!cv.tl && tries++ < 40) return setTimeout(grab, 150);
      if (!cv.tl) { overlay.textContent = "No GSAP timeline found on window.__timelines — use the Studio tab."; return; }
      cv.tl.pause();
      cv.tl.seek(pending != null ? pending : Math.min(1, cv.tl.duration()));
      if (!cv.raf) sync();
      fit();
    };
    grab();
  });

  overlay.addEventListener("click", (e) => {
    const r = overlay.getBoundingClientRect();
    const x = (e.clientX - r.left) / cv.scale, y = (e.clientY - r.top) / cv.scale;
    let node;
    try { node = iframe.contentDocument.elementFromPoint(x, y); } catch { node = null; }
    if (!node) return;
    // Climb to something addressable: an id, or the nearest element with its own text.
    let target = node;
    while (target && target !== iframe.contentDocument.body && !target.id && !ownText(target)) target = target.parentElement;
    ui.sel = target && target !== iframe.contentDocument.body ? target : node;
    highlight(cv);
    showPopover(cv, s);
  });

  const toolbar = el("div", { class: "cv-bar" }, playBtn, slider, timeEl,
    el("span", { class: "cv-hint" }, "click anything in the frame to select it · scrub to the moment first"));
  cv.wrap = el("div", { class: "cv-wrap" }, stage, toolbar, popover);
  requestAnimationFrame(fit);
  return cv;
}

function ownText(elm) {
  return Array.from(elm.childNodes).filter((n) => n.nodeType === 3).map((n) => n.textContent).join("").trim();
}

function selectorFor(elm) {
  if (elm.id) return `#${CSS.escape(elm.id)}`;
  const parts = [];
  let cur = elm;
  while (cur && cur.nodeType === 1 && cur.tagName !== "BODY") {
    if (cur.id) { parts.unshift(`#${CSS.escape(cur.id)}`); break; }
    let part = cur.tagName.toLowerCase();
    const cls = Array.from(cur.classList).slice(0, 2).map((c) => `.${CSS.escape(c)}`).join("");
    if (cls) part += cls;
    const sibs = cur.parentElement ? Array.from(cur.parentElement.children).filter((c) => c.tagName === cur.tagName) : [];
    if (sibs.length > 1) part += `:nth-of-type(${sibs.indexOf(cur) + 1})`;
    parts.unshift(part);
    cur = cur.parentElement;
  }
  return parts.join(" > ");
}

function highlight(cv) {
  const elm = ui.sel;
  if (!elm || !elm.isConnected) { cv.box.style.display = "none"; return; }
  const r = elm.getBoundingClientRect();
  Object.assign(cv.box.style, { display: "block", left: `${r.left * cv.scale}px`, top: `${r.top * cv.scale}px`,
    width: `${r.width * cv.scale}px`, height: `${r.height * cv.scale}px` });
}

function showPopover(cv, s) {
  const elm = ui.sel;
  const pop = cv.popover;
  pop.innerHTML = "";
  if (!elm) { pop.style.display = "none"; return; }
  const selector = selectorFor(elm);
  const text = ownText(elm);
  const t = cv.tl ? cv.tl.time() : 0;
  const ask = el("input", { class: "cr-extra", type: "text", placeholder: "What should change here? (colour, size, motion, timing, wording…)" });
  const askBtn = el("button", { class: "st-btn primary", type: "button", onclick: () => {
    const what = ask.value.trim();
    if (!what || !agent()) return;
    agent().send(`Cutting-room edit in ${cv.rel} at t=${t.toFixed(2)}s.\nElement: \`${selector}\`${text ? ` (text: "${text.slice(0, 120)}")` : ""}.\nChange: ${what}\nKeep everything else identical, run the checks, re-render renders/final.mp4 as a new version.`);
    ask.value = "";
  } }, "Ask agent");
  ask.addEventListener("keydown", (e) => { if (e.key === "Enter") askBtn.click(); });

  let textRow = null;
  if (text) {
    const inp = el("input", { class: "cr-extra", type: "text", value: text });
    const saveBtn = el("button", { class: "st-btn", type: "button", onclick: async () => {
      const nv = inp.value;
      if (nv === text) return;
      saveBtn.disabled = true; saveBtn.textContent = "saving…";
      const res = await fetch(api(s, "source"), { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: cv.rel, old: text, new: nv }) });
      if (res.ok) {
        elm.childNodes.forEach((n) => { if (n.nodeType === 3 && n.textContent.trim()) n.textContent = n.textContent.replace(text, nv); });
        saveBtn.textContent = "saved ✓";
        setTimeout(() => { saveBtn.disabled = false; saveBtn.textContent = "Save text"; }, 1200);
        ui.dirty = true; markDirty();
      } else {
        const d = await res.json().catch(() => ({}));
        saveBtn.disabled = false; saveBtn.textContent = "Save text";
        if (agent()) agent().send(`In ${cv.rel}, change the text of \`${selector}\` from "${text}" to "${nv}". Keep everything else identical and re-render. (direct replace refused: ${d.detail || res.status})`);
      }
    } }, "Save text");
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") saveBtn.click(); });
    textRow = el("div", { class: "cv-row" }, el("span", { class: "cv-k" }, "text"), inp, saveBtn);
  }
  pop.append(
    el("div", { class: "cv-row" }, el("span", { class: "cv-k" }, "selected"), el("code", {}, selector),
      el("span", { class: "chip" }, `t = ${t.toFixed(2)}s`),
      el("button", { class: "st-close", type: "button", onclick: () => { ui.sel = null; highlight(cv); showPopover(cv, s); } }, "×")),
    textRow,
    el("div", { class: "cv-row" }, el("span", { class: "cv-k" }, "ask"), ask, askBtn));
  pop.style.display = "block";
}

function markDirty() {
  const b = root.querySelector(".cr-rerender");
  if (b) { b.classList.add("primary"); b.textContent = "Re-render (source changed)"; }
}

// Source changed on disk (agent or Save text) → reload iframe at same time.
export function reloadCanvas() {
  const cv = ui.canvas;
  if (!cv || !cv.tl) return;
  cv.resumeAt = cv.tl.time();
  ui.sel = null; cv.box.style.display = "none"; cv.popover.style.display = "none";
  cv.iframe.src = cv.iframe.src.replace(/\?v=\d+/, `?v=${Date.now()}`);
}

// ---------------------------------------------------------------------------
// scenes
// ---------------------------------------------------------------------------

function pendingFor(card) { return ui.edits.get(card.id) || {}; }

function setPending(card, field, value) {
  const cur = pendingFor(card);
  const orig = card[field] == null ? "" : String(card[field]);
  if (String(value) === orig) delete cur[field]; else cur[field] = value;
  if (Object.keys(cur).length) ui.edits.set(card.id, cur); else ui.edits.delete(card.id);
  const btn = root.querySelector(".cr-apply");
  if (btn) btn.disabled = ui.edits.size === 0;
  const count = root.querySelector(".cr-count");
  if (count) count.textContent = ui.edits.size ? `${ui.edits.size} scene${ui.edits.size === 1 ? "" : "s"} changed` : "";
}

function scenePrompt(s, extra) {
  const lines = ["Edit the composed video. Keep everything not listed below identical (same look, timing, assets)."];
  for (const [sid, changes] of ui.edits) {
    const card = s.storyboard.scenes.find((c) => c.id === sid);
    lines.push(`\nScene ${sid}${card && card.section_label ? ` (${card.section_label})` : ""}:`);
    for (const [field, value] of Object.entries(changes)) {
      const label = FIELDS.find((f) => f[0] === field)?.[1] || field;
      lines.push(`- ${label}: "${card ? card[field] ?? "" : ""}" -> "${value}"`);
    }
  }
  if (extra) lines.push(`\nAlso: ${extra}`);
  lines.push("\nUpdate script.json / scene_plan.json / edit_decisions.json to match, edit the composition source accordingly, "
    + "run the runtime checks, re-render renders/final.mp4 as a new version, and report what changed. No approval gate needed; proceed.");
  return lines.join("\n");
}

function sceneCard(s, card) {
  const pend = pendingFor(card);
  const take = card.takes && card.takes[0];
  const thumb = el("div", { class: `cr-thumb${take && take.path ? "" : " cr-empty"}`, title: "jump to scene", onclick: () => jumpTo(card.start_seconds) },
    take && take.path ? el("img", { src: thumbURL(s.project_id, take.path, 640), loading: "lazy", alt: "" }) : null,
    el("div", { class: "cr-thumb-bar" },
      el("b", {}, card.id),
      card.section_label ? el("span", { class: "cr-section" }, card.section_label) : null,
      el("span", { class: "cr-time" }, `${fmtDuration(card.start_seconds)} – ${fmtDuration(card.end_seconds)}`)));
  const field = (key) => {
    const [, label, kind] = FIELDS.find((f) => f[0] === key);
    const val = key in pend ? pend[key] : (card[key] == null ? "" : card[key]);
    const attrs = { class: `cr-field${key in pend ? " dirty" : ""}`, value: kind === "textarea" ? null : val,
      oninput: (e) => { setPending(card, key, kind === "number" ? Number(e.target.value) : e.target.value); e.target.classList.toggle("dirty", key in pendingFor(card)); } };
    const input = kind === "textarea" ? el("textarea", { ...attrs, rows: "4" }, val)
      : kind === "number" ? el("input", { ...attrs, type: "number", step: "0.1", min: "0" })
      : el("input", { ...attrs, type: "text" });
    return el("label", { class: "cr-label" }, el("span", {}, label), input);
  };
  return el("div", { class: "cr-scene" }, thumb,
    el("div", { class: "cr-fields" },
      field("narration"),
      field("description"),
      el("div", { class: "cr-timing" }, field("start_seconds"), field("end_seconds"),
        el("span", { class: "cr-dur" }, `${fmtDuration(card.duration_seconds ?? (card.end_seconds - card.start_seconds))} long`))));
}

function jumpTo(t) {
  const cv = ui.canvas;
  if (!cv || !cv.tl) return;
  setTab("canvas");
  cv.tl.pause(); cv.tl.seek(Math.max(0, Number(t) || 0) + 0.5);
  cv.playing = false; cv.playBtn.textContent = "▶";
  cv.wrap.scrollIntoView({ behavior: "smooth", block: "center" });
}

// ---------------------------------------------------------------------------
// layout
// ---------------------------------------------------------------------------

function setTab(t) { ui.tab = t; localStorage.setItem("backlot.cr.tab", t); paint(); }

// Studio is a separate server; if it dies (stopped elsewhere, machine slept)
// swap the Open link for a relaunch notice.
setInterval(async () => {
  if (ui.tab !== "studio" || !ui.state || !(ui.editor && ui.editor.running)) return;
  await pollEditor(ui.state);
  if (!(ui.editor && ui.editor.running)) { ui.studioDied = true; paint(); }
}, 10000);

async function pollEditor(s) {
  try { ui.editor = await getJSON(api(s, "editor")); } catch { ui.editor = null; }
}

async function launchStudio(s, btn) {
  btn.disabled = true; btn.textContent = "starting…";
  // Open the tab synchronously (inside the click) so popup blockers allow it,
  // then point it at the Studio once the server is up.
  const win = window.open("about:blank", "_blank");
  try {
    const r = await fetch(api(s, "editor"), { method: "POST" });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.status);
    ui.editor = { ...ui.editor, ...d };
    if (win) win.location = ui.editor.url;
  } catch (e) {
    if (win) win.close();
    alert(`could not start editor: ${e.message}`);
  }
  paint();
}

function paint() {
  const s = ui.state;
  if (!s || !s.storyboard || !s.storyboard.scenes.length) { root.innerHTML = ""; ui.shell = null; return; }
  // Fixed shell: iframes must never be detached (detach = reload = lost playhead).
  if (!ui.shell) {
    ui.shell = { head: el("div", {}), bar: el("div", { class: "cr-toolbar" }), host: el("div", { class: "cr-host" }) };
    root.replaceChildren(ui.shell.head, ui.shell.bar, ui.shell.host);
  }
  const comp = ui.editor && ui.editor.composition;
  const runtime = comp ? comp.runtime : (s.artifacts.edit_decisions || {}).render_runtime;
  const studioName = runtime === "remotion" ? "Remotion Studio" : runtime === "hyperframes" ? "HyperFrames Studio" : "editor";
  const running = ui.editor && ui.editor.running;

  const tabs = el("div", { class: "cr-tabs" }, ["canvas", "studio", "scenes"].map((t) =>
    el("button", { class: `cr-tab${ui.tab === t ? " active" : ""}`, type: "button", onclick: () => setTab(t) },
      t === "canvas" ? "Canvas · click to edit" : t === "studio" ? `Studio · ${studioName}` : "Scenes · text & timing")));
  const rerender = el("button", { class: `st-btn cr-rerender${ui.dirty ? " primary" : ""}`, type: "button",
    title: "Re-render final.mp4 from the current composition source",
    onclick: () => { if (agent() && agent().send(`Re-render renders/final.mp4 from the current composition source exactly as it is now (edited from the cutting room). Update edit_decisions/render_report if timings changed. Proceed without asking.`)) { ui.dirty = false; paint(); } } },
    ui.dirty ? "Re-render (source changed)" : "Re-render from source");
  const head = el("div", { class: "section-title" }, "Cutting room",
    el("span", { class: "meta" }, runtime ? `${runtime} · ${comp && comp.editable ? "editable" : "read-only"}` : "not composed yet"));

  let body;
  if (ui.tab === "canvas") {
    if (!ui.canvas) ui.canvas = buildCanvas(s);
    body = ui.canvas ? ui.canvas.wrap : el("p", { class: "hint" },
      runtime === "remotion" ? "Remotion compositions preview in the Studio tab (click-to-edit canvas is HyperFrames-only for now)." : "Compose the video first — the live canvas appears here.");
  } else if (ui.tab === "studio") {
    // Studio always runs in its own browser tab — never embedded here.
    if (running) {
      body = el("div", { class: "cr-toolbar" },
        el("a", { class: "st-btn primary", href: ui.editor.url, target: "_blank", rel: "noopener" }, `Open ${studioName} ⇗`),
        el("button", { class: "st-btn ghost", type: "button", onclick: async () => { await fetch(api(s, "editor"), { method: "DELETE" }); await pollEditor(s); paint(); } }, "Stop editor"),
        el("span", { class: "cr-help" }, `${studioName} is running in a separate tab — drag clips, retime, edit text, shapes and motion there. Changes save to the source; press Re-render when done.`));
    } else {
      const b = el("button", { class: "st-btn primary", type: "button", disabled: comp && comp.editable ? null : "" }, `${ui.studioDied ? "Relaunch" : "Launch"} ${studioName} ⇗`);
      b.onclick = () => { ui.studioDied = false; launchStudio(s, b); };
      body = el("div", { class: "cr-toolbar" }, b, el("span", { class: "cr-help" },
        ui.studioDied ? `${studioName} stopped (its server is gone) — relaunch to continue; saved edits are on disk.`
          : comp && comp.editable ? `Starts ${studioName} for this composition and opens it in a new tab.` : "Compose the video first."));
    }
  } else {
    const extra = el("input", { class: "cr-extra", type: "text", placeholder: "Anything else to change (rounder cards, slower camera in s3, swap music…)" });
    const applyBtn = el("button", { class: "st-btn primary cr-apply", type: "button", disabled: ui.edits.size ? null : "",
      onclick: () => { if (agent() && agent().send(scenePrompt(s, extra.value.trim()))) { ui.edits.clear(); extra.value = ""; paint(); } } }, "Apply changes with agent");
    const freeBtn = el("button", { class: "st-btn", type: "button",
      onclick: () => { if (agent() && extra.value.trim() && agent().send(scenePrompt(s, extra.value.trim()))) extra.value = ""; } }, "Send instruction");
    body = el("div", {},
      el("div", { class: "cr-grid" }, s.storyboard.scenes.map((c) => sceneCard(s, c))),
      el("div", { class: "cr-toolbar" }, extra, applyBtn, freeBtn,
        el("span", { class: "cr-count" }, ui.edits.size ? `${ui.edits.size} scene${ui.edits.size === 1 ? "" : "s"} changed` : "")));
  }
  ui.shell.head.replaceChildren(head);
  ui.shell.bar.replaceChildren(tabs, rerender);
  if (ui.shell.host.firstChild !== body) ui.shell.host.replaceChildren(body);
}

// Called by board.js on every state refresh (any file change in the project).
let lastSrcKey = null;
export function renderEditor(s) {
  ui.state = s;
  pollEditor(s).then(() => {
    const comp = ui.editor && ui.editor.composition;
    const key = comp ? `${comp.runtime}|${comp.entry}` : "";
    if (key !== lastSrcKey) { lastSrcKey = key; ui.canvas = null; ui.mtime = comp ? comp.entry_mtime : null; }
    // Composition source changed on disk (agent edit / Save text) → reload the
    // canvas at the same playhead. Own Save-text edits bump mtime too; harmless.
    if (comp && ui.mtime != null && comp.entry_mtime !== ui.mtime) reloadCanvas();
    ui.mtime = comp ? comp.entry_mtime : null;
    paint();
  });
}
