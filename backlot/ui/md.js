// Tiny markdown → HTML for agent replies. Covers what the agent actually
// emits: headings, bold/italic, inline code, fenced code (json pretty-printed),
// bullet / numbered lists, links, paragraphs. Escapes everything first.

const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|\s)(https?:\/\/[^\s<]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
}

function codeBlock(lang, body) {
  let text = body.replace(/\n$/, "");
  if ((lang || "").toLowerCase() === "json" || (!lang && /^\s*[{[]/.test(text))) {
    try { text = JSON.stringify(JSON.parse(text), null, 2); } catch { /* leave as-is */ }
  }
  return `<pre class="md-code" data-lang="${esc(lang || "")}"><code>${esc(text)}</code></pre>`;
}

export function renderMarkdown(src) {
  const out = [];
  const lines = String(src || "").replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = /^```(\w*)\s*$/.exec(line);
    if (fence) {
      const buf = []; i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) buf.push(lines[i++]);
      i++; out.push(codeBlock(fence[1], buf.join("\n") + "\n")); continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) { out.push(`<h${h[1].length + 2} class="md-h">${inline(h[2])}</h${h[1].length + 2}>`); i++; continue; }
    if (/^\s*[-*•]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) items.push(`<li>${inline(lines[i++].replace(/^\s*[-*•]\s+/, ""))}</li>`);
      out.push(`<ul class="md-list">${items.join("")}</ul>`); continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) items.push(`<li>${inline(lines[i++].replace(/^\s*\d+[.)]\s+/, ""))}</li>`);
      out.push(`<ol class="md-list">${items.join("")}</ol>`); continue;
    }
    if (/^\s*(---|\*\*\*)\s*$/.test(line)) { out.push("<hr>"); i++; continue; }
    if (!line.trim()) { i++; continue; }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^```|^#{1,4}\s|^\s*[-*•]\s+|^\s*\d+[.)]\s+/.test(lines[i])) para.push(inline(lines[i++]));
    out.push(`<p>${para.join("<br>")}</p>`);
  }
  return out.join("");
}
