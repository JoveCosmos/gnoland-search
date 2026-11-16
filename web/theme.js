// Day/night before first paint, on every page: the system setting, unless the button chose one
// (remembered on this device). A classic script in <head>, not a module, because modules run after
// the first paint and the page would flash the wrong theme. app.js owns the button.
(() => {
  const sys = matchMedia("(prefers-color-scheme: dark)");
  const stored = () => { try { return localStorage.getItem("theme"); } catch { return null; } };
  const look = pick => {
    const root = document.documentElement;
    if (pick) root.dataset.theme = pick; else delete root.dataset.theme;
    root.toggleAttribute("data-dark", pick ? pick === "dark" : sys.matches);
  };
  const remember = pick => { try { localStorage.setItem("theme", pick); } catch {} };
  look(stored());
  sys.addEventListener("change", () => look(stored()));
  window.theme = { look, remember };
})();
