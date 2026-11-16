// Search for gno.land: one search button, results as you type, pages opened in place.
// Every string from the chain is written by anyone, so chain text only ever reaches the page as
// text nodes (results) or through DOMPurify (pages); it is never concatenated into HTML.
const $ = id => document.getElementById(id);
const body = document.body, q = $("q"), results = $("results"), count = $("count");
let sel = -1, items = [], ctl = null, current = "", height = 0;
// mainnet or testnet: one index each; the choice is remembered on this device
const NETS = { main: { name: "Mainnet", web: "https://gno.land" }, test: { name: "Testnet", web: "https://onyx.gno.land" } };
let net = "main", nets = ["main"];
try { if (localStorage.getItem("net") === "test") net = "test"; } catch {}

const fmt = n => n.toLocaleString("en-US");
const short = u => u.replace(/^gno\.land/, "");
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const cut = (s, n) => s.length > n ? s.slice(0, n - 1) + "…" : s;

// ---------- the moving background: a drifting field of points that leans toward the pointer ----------
const cv = $("bg"), cx = cv.getContext("2d");
const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
let W = 0, H = 0, DPR = 1, pts = [], px = -1e4, py = -1e4, tok = {};
function theme() {  // colours come from the CSS tokens, so the canvas follows light and dark
  const s = getComputedStyle(document.documentElement), v = k => s.getPropertyValue(k).trim();
  tok = { bg0: v("--bg0"), bg1: v("--bg1"), bg2: v("--bg2"), pt: v("--pt"), ln: v("--ln") };
}
// day/night: theme.js applies it before first paint; here the button flips it and the canvas follows
$("theme").onclick = () => {
  const pick = document.documentElement.hasAttribute("data-dark") ? "light" : "dark";
  window.theme.remember(pick); window.theme.look(pick); theme(); if (still) frame(0);
};
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { theme(); if (still) frame(0); });
function size() {
  DPR = Math.min(devicePixelRatio || 1, 2); W = innerWidth; H = innerHeight;
  cv.width = W * DPR; cv.height = H * DPR;
  const n = Math.round(Math.min(110, W * H / 14000));
  pts = Array.from({ length: n }, () => ({ x: Math.random() * W, y: Math.random() * H,
    vx: (Math.random() - .5) * .18, vy: (Math.random() - .5) * .18, r: Math.random() * 1.4 + .4 }));
  if (still) frame(0);
}
function frame(t) {
  cx.setTransform(DPR, 0, 0, DPR, 0, 0);
  const g = cx.createRadialGradient(W * .5, H * .45, 0, W * .5, H * .45, Math.max(W, H) * .75);
  g.addColorStop(0, tok.bg0); g.addColorStop(.55, tok.bg1); g.addColorStop(1, tok.bg2);
  cx.fillStyle = g; cx.fillRect(0, 0, W, H);
  // a faint perspective grid at the horizon
  cx.strokeStyle = `rgba(${tok.ln},.07)`; cx.lineWidth = 1;
  const hz = H * .72, off = still ? 0 : (t / 60) % 40;
  for (let i = 0; i < 14; i++) { const y = hz + Math.pow(i + off / 40, 2) * 3.2; if (y > H) break; cx.beginPath(); cx.moveTo(0, y); cx.lineTo(W, y); cx.stroke(); }
  for (let i = -12; i <= 12; i++) { cx.beginPath(); cx.moveTo(W / 2 + i * 22, hz); cx.lineTo(W / 2 + i * 160, H); cx.stroke(); }
  for (const p of pts) {
    if (!still) { p.x += p.vx; p.y += p.vy; }
    const dx = px - p.x, dy = py - p.y, d2 = dx * dx + dy * dy;
    if (d2 < 22000 && !still) { p.x += dx * .004; p.y += dy * .004; }
    if (p.x < -10) p.x = W + 10; if (p.x > W + 10) p.x = -10; if (p.y < -10) p.y = H + 10; if (p.y > H + 10) p.y = -10;
  }
  for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++) {
    const a = pts[i], b = pts[j], d = Math.hypot(a.x - b.x, a.y - b.y);
    if (d < 120) { cx.strokeStyle = `rgba(${tok.ln},${(1 - d / 120) * .14})`; cx.beginPath(); cx.moveTo(a.x, a.y); cx.lineTo(b.x, b.y); cx.stroke(); }
  }
  cx.fillStyle = tok.pt;
  for (const p of pts) { cx.beginPath(); cx.arc(p.x, p.y, p.r, 0, 6.283); cx.fill(); }
  if (!still) requestAnimationFrame(frame);
}
theme(); size();
addEventListener("resize", size);
addEventListener("pointermove", e => { px = e.clientX; py = e.clientY; }, { passive: true });
if (!still) requestAnimationFrame(frame);

// ---------- searching ----------
// ---------- where you are: start, results or a page, kept in the browser history so the back
// button walks back through them (there is no Close button) ----------
function show(st) {
  const v = st?.v || "start";
  body.classList.toggle("open", v !== "start");
  body.classList.toggle("reading", v === "page");
  if (v === "start") { q.value = ""; clear(); q.blur(); current = ""; }
  if (v === "open") { current = ""; }
  if (v === "page" && st.url !== current) load(st.url);
}
function go(st) { history.pushState(st, ""); show(st); }
addEventListener("popstate", e => show(e.state));
// a reload keeps the entry's state, so a reload on results or a page would leave the start screen
// over a stale "results" entry and back would seem to do nothing (2026-11-15): every load starts clean
history.replaceState(null, "");
function open() { if (!body.classList.contains("open") || body.classList.contains("reading")) go({ v: "open" }); q.focus(); }
function clear() { items = []; sel = -1; results.replaceChildren(); count.textContent = ""; showNet(); }
// While searching, the count line's right side names the network and block, and a tap on it
// switches network. With an empty box the line is empty; the resting screen's hint has the switch.
function showNet() {
  const b = $("net"), on = !!(height && q.value.trim());
  b.replaceChildren(...(on ? [NETS[net].name, el("span", "rs-blk", `, block ${fmt(height)}`)] : []));
  b.hidden = !on; b.disabled = nets.length < 2;
}
function switchNet() {
  net = net === "main" ? "test" : "main"; height = 0;
  try { localStorage.setItem("net", net); } catch {}
  showNet(); stats(); if (q.value.trim()) search();
}
$("net").onclick = switchNet;
$("hint-net").onclick = switchNet;
function home() { if (body.classList.contains("open")) go({ v: "start" }); }

async function search() {
  const term = q.value.trim().slice(0, 200);
  if (!term) { ctl?.abort(); clear(); return; }
  ctl?.abort(); ctl = new AbortController();
  count.textContent = "Searching";
  const t0 = performance.now();
  try {
    const r = await fetch(`/api/search?q=${encodeURIComponent(term)}&net=${net}`, { signal: ctl.signal });
    if (r.status === 429) { count.textContent = "Slow down a little, then try again"; return; }
    const d = await r.json();
    height = d.height || height;
    showNet();
    items = d.results; sel = items.length ? 0 : -1;
    count.textContent = items.length
      ? `${items.length} result${items.length > 1 ? "s" : ""} in ${Math.round(performance.now() - t0)} ms`
      : `No realms match "${cut(term, 40)}"`;
    draw();
  } catch (e) { if (e.name !== "AbortError") count.textContent = "Search is offline, try again soon"; }
}

function snippet(s) {  // the server marks matches with \x02 and \x03; build marks around text nodes
  const out = el("div", "rs-s");
  let on = false;
  for (const part of (s || "").split(/([\x02\x03])/)) {
    if (part === "\x02") on = true; else if (part === "\x03") on = false;
    else if (part) out.append(on ? el("mark", null, part) : document.createTextNode(part));
  }
  return out;
}
const plural = (n, word) => `${fmt(n)} ${word}${n === 1 ? "" : "s"}`;
function meta(it) {
  if (it.kind === "tx") return `Transaction, block ${fmt(it.height)}`;
  if (it.kind === "user") return it.txs ? `Account, ${plural(it.txs, "message")}` + (it.last ? `, last in block ${fmt(it.last)}` : "") : "Account, no transactions yet";
  const bits = [{ pkg: "Package", code: "Source code", token: "Token" }[it.kind] || "Realm"];
  if (it.users) bits.push(`${plural(it.users, "user")} this month`);
  if (it.gnot) bits.push(`${it.gnot} GNOT in`);  // a ready string from the server: sums can pass 2^53
  if (it.imports) bits.push(`imported by ${fmt(it.imports)}`);
  if (it.links) bits.push(`linked from ${plural(it.links, "realm")}`);
  return bits.join(", ");
}
// what a result is called and where it lives, in the words people use
const label = u => u.startsWith("user:") ? u.slice(5) : u.startsWith("tx:") ? u.slice(3) : u.startsWith("token:") ? short(u.slice(6)) : short(u);
function webUrl(u) {
  const w = NETS[net].web;
  if (u.startsWith("user:")) return `${w}/u/${u.slice(5)}`;
  if (u.startsWith("tx:")) return "";
  if (u.startsWith("token:")) return w + short(u.slice(6).split(".").slice(0, -2).join("."));  // id = realm.SYMBOL.serial
  return w + short(u);
}
function draw() {
  if (!items.length) { results.replaceChildren(); return; }
  results.replaceChildren(...items.map((it, i) => {
    const li = el("li", "rs-card" + (i === sel ? " sel" : ""));
    li.setAttribute("role", "option"); li.setAttribute("aria-selected", i === sel); li.dataset.i = i;
    li.append(el("div", "rs-t", cut(it.title || label(it.url), 160)), el("div", "rs-u", cut(label(it.url), 300)),
      snippet(it.snippet), el("div", "rs-m", meta(it)));
    return li;
  }));
}
function mark() {
  [...results.children].forEach((li, i) => { li.classList.toggle("sel", i === sel); li.setAttribute("aria-selected", i === sel); });
  results.children[sel]?.scrollIntoView({ block: "nearest" });
}
results.addEventListener("click", e => { const li = e.target.closest("li[data-i]"); if (li) read(items[+li.dataset.i].url); });

// ---------- reading a page in place ----------
// Links in chain pages are untrusted: they show where they go, carry nofollow, and images or
// embeds are never loaded (the CSP also blocks them); an image becomes a link to its address.
function tame(frag) {
  for (const img of frag.querySelectorAll("img")) {
    const src = img.getAttribute("src") || "";
    const a = /^https?:\/\//i.test(src) ? el("a", null, `Image: ${cut(img.getAttribute("alt") || src, 120)}`) : el("span", "rd-dim", "Image");
    if (a.tagName === "A") { a.href = src; a.target = "_blank"; a.rel = "nofollow noopener noreferrer"; a.title = src; }
    img.replaceWith(a);
  }
  for (const a of frag.querySelectorAll("a[href]")) {
    const h = a.getAttribute("href");
    if (inPlace(h)) continue;  // another gno.land page, account or package, opened in place
    if (/^https?:\/\//i.test(h)) {
      a.target = "_blank"; a.rel = "nofollow noopener noreferrer"; a.title = h;
      let host = ""; try { host = new URL(h).host; } catch {}
      if (host && !a.textContent.includes(host)) a.append(" ", el("span", "rd-host", `(${cut(host, 60)})`));
    } else { a.replaceWith(el("span", null, a.textContent)); }
  }
  return frag;
}
function read(url) { go({ v: "page", url }); }
async function load(url) {
  current = url;
  const src = $("src"), href = webUrl(url);
  src.textContent = cut(label(url), 300);
  if (href) src.href = href; else src.removeAttribute("href");
  $("state").textContent = "Loading page";
  $("body").replaceChildren();
  $("reader").scrollTop = 0;
  const t0 = performance.now();
  try {
    const r = await fetch(`/api/page?u=${encodeURIComponent(url)}&net=${net}`);
    if (current !== url) return;
    if (r.status === 429) { $("state").textContent = "Slow down a little, then open it again"; return; }
    const d = await r.json();
    if (current !== url) return;
    const md = (d.md || "").replace(/<\/?gno-columns[^>]*>|<gno-columns-sep\s*\/?>/g, "\n");
    const frag = DOMPurify.sanitize(marked.parse(md), { RETURN_DOM_FRAGMENT: true,
      FORBID_TAGS: ["style", "form", "input", "button", "textarea", "select", "iframe", "object", "embed", "video", "audio", "source", "picture", "svg", "math"],
      FORBID_ATTR: ["style", "srcset", "id", "class"] });
    if (frag.textContent.trim() || frag.querySelector("img")) $("body").replaceChildren(tame(frag));
    else {
      const box = [el("h2", null, cut(short(url), 200)), el("p", "rd-dim", "This realm has no page of its own.")];
      if (d.funcs?.length) {
        const p = el("p"); d.funcs.forEach((f, i) => p.append(i ? " " : "", el("code", null, cut(f, 64))));
        box.push(el("p", null, "What it can do:"), p);
      }
      const p = el("p"), a = el("a", null, "Read its code on gno.land");
      a.href = `${NETS[net].web}${short(url.split(":")[0])}$source`; a.target = "_blank"; a.rel = "noopener noreferrer";
      p.append(a); box.push(p);
      $("body").replaceChildren(...box);
    }
    $("state").textContent = `${d.live ? "Live from " + NETS[net].web.slice(8) : "From the index"}, ${Math.round(performance.now() - t0)} ms`;
  } catch { if (current === url) $("state").textContent = "Could not load this page"; }
}
// links inside pages: realm pages, packages and accounts open in place
function inPlace(h) { return /^\/(r|p)\/[^$]*$/.test(h) || /^\/u\/g1[0-9a-z]{38}$/.test(h); }
$("body").addEventListener("click", e => {
  const a = e.target.closest("a[href]"); if (!a) return;
  const h = a.getAttribute("href");
  if (!inPlace(h)) return;
  e.preventDefault();
  read(h.startsWith("/u/") ? "user:" + h.slice(3) : "gno.land" + h);
});
$("home").onclick = home;

// ---------- input ----------
$("go").onclick = open;
q.addEventListener("input", search);
q.addEventListener("keydown", e => {
  if (e.key === "ArrowDown" && items.length) { sel = Math.min(items.length - 1, sel + 1); mark(); e.preventDefault(); }
  else if (e.key === "ArrowUp" && items.length) { sel = Math.max(0, sel - 1); mark(); e.preventDefault(); }
  else if (e.key === "Enter" && sel >= 0) read(items[sel].url);
});
addEventListener("keydown", e => {
  if (e.key === "/" && document.activeElement !== q) { e.preventDefault(); open(); }
  else if (e.key === "Escape" && body.classList.contains("open")) history.back();
});
q.addEventListener("focus", () => { if (body.classList.contains("reading")) go({ v: "open" }); });
function stats() {
  fetch(`/api/stats?net=${net}`).then(r => r.json()).then(s => {
    nets = s.nets || ["main"];
    if (!nets.includes(net)) { net = "main"; return stats(); }
    height = s.height;
    const coarse = matchMedia("(pointer: coarse)").matches;
    $("hint-stats").textContent = `${fmt(s.realms)} realms, ${fmt(s.packages)} packages, ${fmt(s.users)} active accounts` + (coarse ? "" : ", press / to search");
    $("hint-how").textContent = `Searching ${NETS[net].name.toLowerCase()}` + (nets.length > 1 ? "," : "");
    const other = net === "main" ? "test" : "main";
    $("hint-net").textContent = `switch to ${NETS[other].name.toLowerCase()}`;
    $("hint-net").hidden = nets.length < 2;
    showNet();
  }).catch(() => {});
}
stats();
