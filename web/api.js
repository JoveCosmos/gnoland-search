// The developers page: examples show this site's own address (the repository names none), and the
// copy buttons copy the example as it reads.
for (const s of document.querySelectorAll("[data-o]")) s.textContent = location.origin;
for (const b of document.querySelectorAll(".ad-copy")) {
  b.addEventListener("click", async () => {
    const text = b.parentElement.querySelector("pre").textContent;
    try { await navigator.clipboard.writeText(text); b.setAttribute("aria-label", "Copied"); b.style.color = "var(--accent)"; }
    catch { getSelection().selectAllChildren(b.parentElement.querySelector("pre")); }  // no clipboard: select it instead
    setTimeout(() => { b.setAttribute("aria-label", "Copy"); b.style.color = ""; }, 1500);
  });
}
