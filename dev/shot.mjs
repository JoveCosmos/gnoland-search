// Screenshots in headless Chrome at phone, small phone, landscape and desktop, light and dark,
// and a check that the bar, count line and list do not move while results arrive.
//   node --experimental-websocket dev/shot.mjs [--url URL] [--out DIR]
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const A = process.argv.slice(2), opt = (k, d) => { const i = A.indexOf(k); return i < 0 ? d : A[i + 1]; };
const URL0 = opt("--url", "http://127.0.0.1:8961/"), OUT = opt("--out", "/tmp/gnosearch-shots");
mkdirSync(OUT, { recursive: true });
const PORT = 9700 + Math.floor(Math.random() * 100), PROFILE = mkdtempSync(join(tmpdir(), "gshot-"));
const chrome = spawn("google-chrome", ["--headless=new", "--no-sandbox", "--hide-scrollbars", "--disable-gpu",
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${PROFILE}`, "about:blank"], { stdio: "ignore" });
const sleep = ms => new Promise(r => setTimeout(r, ms));
let tab;
for (let i = 0; i < 50 && !tab; i++) {
  try { tab = (await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()).find(t => t.type === "page"); } catch { await sleep(200); }
}
const ws = new WebSocket(tab.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r));
let seq = 0; const waiting = new Map(), logs = [];
ws.addEventListener("message", m => {
  const d = JSON.parse(m.data);
  if (d.id && waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); }
  if (d.method === "Runtime.exceptionThrown") logs.push("EXCEPTION: " + d.params.exceptionDetails.exception?.description);
  if (d.method === "Log.entryAdded" && d.params.entry.level === "error") logs.push("LOG: " + d.params.entry.text);
});
const cdp = (method, params = {}) => new Promise(r => { const id = ++seq; waiting.set(id, r); ws.send(JSON.stringify({ id, method, params })); });
const ev = async expr => (await cdp("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;
const shot = async name => { const r = await cdp("Page.captureScreenshot", { format: "png" }); writeFileSync(join(OUT, name + ".png"), Buffer.from(r.result.data, "base64")); };
const rects = () => ev(`JSON.stringify(["#q", ".rs-count", "#results"].map(s => { const r = document.querySelector(s).getBoundingClientRect(); return [r.x, r.y, r.width, r.height].map(Math.round); }))`);
// a real pointer click, so the page sees a user gesture (Chrome skips history entries made without one)
const tap = async sel => {
  const c = await ev(`(() => { const r = document.querySelector(${JSON.stringify(sel)})?.getBoundingClientRect(); return r && [r.x + r.width / 2, r.y + r.height / 2]; })()`);
  if (!c) return;
  for (const type of ["mousePressed", "mouseReleased"]) await cdp("Input.dispatchMouseEvent", { type, x: c[0], y: c[1], button: "left", clickCount: 1 });
};
const typeIn = async text => { await ev(`(() => { const q = document.getElementById("q"); q.value = ${JSON.stringify(text)}; q.dispatchEvent(new Event("input")); })()`); };

await cdp("Runtime.enable"); await cdp("Page.enable"); await cdp("Log.enable");
const sizes = [["phone", 390, 844, true], ["p375", 375, 812, true], ["p360", 360, 780, true], ["small", 320, 640, true], ["land", 844, 390, true], ["tablet", 768, 1024, true], ["desk", 1440, 860, false]];
let moved = 0;
for (const scheme of ["dark", "light"]) for (const [name, w, h, mobile] of sizes) {
  const tag = `${name}-${scheme}`;
  await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: mobile ? 2 : 1, mobile });
  await cdp("Emulation.setTouchEmulationEnabled", { enabled: mobile, maxTouchPoints: mobile ? 5 : 1 });
  await cdp("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: scheme }, { name: "prefers-reduced-motion", value: "reduce" }] });
  await cdp("Page.navigate", { url: URL0 }); await sleep(300);
  await ev(`localStorage.clear()`);  // the day/night choice is remembered; each run starts from the system setting
  await cdp("Page.navigate", { url: URL0 }); await sleep(1200);
  await cdp("Page.resetNavigationHistory");  // one tab serves every size; the back-walk check needs only this run's history
  await shot(`${tag}-1-rest`);
  // nothing on the resting lines may be cut: text wider than its box is a bug, not an ellipsis
  const cut = await ev(`[...document.querySelectorAll(".hint > span, .hint-net, .hint-links a")].filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.textContent).join(" | ")`);
  if (cut) { moved++; console.log("TEXT CUT", tag, cut); }

  await tap("#go"); await sleep(300);
  await ev(`document.getElementById("theme").click()`); await sleep(200);
  await shot(`${tag}-1b-flipped`);
  await ev(`document.getElementById("theme").click()`); await sleep(100);
  const r0 = await rects();
  await typeIn("swap"); await sleep(5);
  const r1 = await rects(); await shot(`${tag}-2-searching`);
  await sleep(600);
  const r2 = await rects(); await shot(`${tag}-3-results`);
  const redges = await ev(`(() => { const b = document.querySelector(".sr-bar").getBoundingClientRect(), r = document.querySelector(".rs-wrap").getBoundingClientRect();
    return Math.abs(b.left - r.left) < 1.5 && Math.abs(b.right - r.right) < 1.5 ? "" : "misaligned"; })()`);
  if (redges) { moved++; console.log("BAR NOT ALIGNED WITH RESULTS", tag); }
  await typeIn("swap <img src=x onerror=alert(1)> zzqq"); await sleep(600);
  const r3 = await rects(); await shot(`${tag}-4-none`);
  const wide = await ev("document.documentElement.scrollWidth > innerWidth");
  if (new Set([r0, r1, r2, r3]).size > 1 || wide) { moved++; console.log("MOVED", tag, r0, r1, r2, r3, wide ? "horizontal scroll" : ""); }
  await typeIn(""); await sleep(300);
  const left = await ev(`document.querySelector(".rs-count").textContent.trim()`);
  if (left) { moved++; console.log("LEFT AFTER CLEAR", tag, JSON.stringify(left)); }
  await shot(`${tag}-4b-cleared`);
  await typeIn("memba arcade leaderboard"); await sleep(600);
  await tap(".rs-card"); await sleep(1500);
  await shot(`${tag}-5-reader`);
  await ev(`history.back()`); await sleep(300);
  await typeIn("GNS"); await sleep(600); await tap(".rs-card"); await sleep(1200);
  await shot(`${tag}-5b-token`);
  await ev(`history.back()`); await sleep(300);
  await typeIn("memba arcade leaderboard"); await sleep(600); await tap(".rs-card"); await sleep(1500);
  // the open bar and the page content share both edges
  const edges = await ev(`(() => { const b = document.querySelector(".sr-bar").getBoundingClientRect(),
    d = document.querySelector(".rd-doc").getBoundingClientRect();
    return Math.abs(b.left - d.left) < 1.5 && Math.abs(b.right - d.right) < 1.5 ? "" : [b.left, b.right, d.left, d.right].map(Math.round).join(" "); })()`);
  if (edges) { moved++; console.log("BAR NOT ALIGNED WITH PAGE", tag, edges); }
  // back walks page -> results -> start
  await ev(`history.back()`); await sleep(300);
  const walk1 = await ev(`document.body.className`);
  await ev(`history.back()`); await sleep(1500);  // the start entry can be another page load, which takes longer
  const walk2 = await ev(`document.body.className`);
  if (walk1.trim() !== "open" || walk2.trim() !== "") { moved++; console.log("BACK DID NOT WALK", tag, JSON.stringify([walk1, walk2])); }
  await ev(`document.getElementById("go").click()`); await sleep(300);
  for (const [i, term] of [["6-tx", "AD7D3390"], ["7-user", "validator"], ["7b-address", "g1qqqqr2u6rfydqjkm56a7s54397vfp4kmvnuwev"], ["7c-token", "GNS"], ["8-pkg", "avl tree"]]) {
    await typeIn(term); await sleep(600); await shot(`${tag}-${i}`);
  }
  await ev(`document.querySelector(".rs-card")?.click()`); await sleep(1200); await shot(`${tag}-9-pkg-reader`);
  await cdp("Page.navigate", { url: URL0 }); await sleep(1000);
  await ev(`document.getElementById("hint-net").click()`); await sleep(600); await shot(`${tag}-10-testnet-rest`);
  await ev(`document.getElementById("go").click()`); await sleep(300); await typeIn("board"); await sleep(700);
  await shot(`${tag}-11-testnet-results`);
  await ev(`localStorage.removeItem("net")`);
  const cards = await ev(`JSON.stringify([...new Set([...document.querySelectorAll(".rs-card")].map(c => c.offsetHeight))])`);
  console.log("shots", tag, "card heights", cards);
}
console.log(moved ? `${moved} layouts moved` : "nothing moved");
console.log(logs.length ? logs.join("\n") : "no console errors");
ws.close(); chrome.kill(); await new Promise(r => chrome.on("exit", r)); rmSync(PROFILE, { recursive: true, force: true });
