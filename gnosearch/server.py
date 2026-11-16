"""HTTP: the search API and the single-page app.

GET /api/search?q=...&net=test   results as you type (net: main by default, or test)
GET /api/page?u=...&net=test     a realm page rendered live from chain (falls back to the index
                                 copy); packages, source, users and transactions from the index
GET /api/stats?net=test          counts and the indexed block height
GET /api/importers?u=...         packages and realms that import a package, most used first
(/api/search also takes kind=realm|pkg|code|user|token|tx)
The API is open (any site may call it) and documented at /api, which describes the same routes:
change both together.
"""
import json
import os
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import chain, store

WEB = os.path.join(os.path.dirname(__file__), "..", "web")
PORT = int(os.environ.get("GNOSEARCH_PORT", "8960"))
BIND = os.environ.get("GNOSEARCH_BIND", "127.0.0.1")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/robots.txt": ("robots.txt", "text/plain; charset=utf-8"), "/api": ("api.html", "text/html; charset=utf-8"),
          "/base.css": ("base.css", "text/css; charset=utf-8"), "/theme.js": ("theme.js", "text/javascript; charset=utf-8")}
LIVE_URL = re.compile(r"^gno\.land/r/[A-Za-z0-9_./\-]{1,200}(:[^\s]{0,500})?$")   # rendered from the node
INDEX_URL = re.compile(r"^(gno\.land/[rp]/[A-Za-z0-9_./\-]{1,200}(\$source)?|user:g1[0-9a-z]{38}|tx:[0-9A-F]{64}|token:gno\.land/r/[A-Za-z0-9_./\-]{1,250})$")
NODES = {"main": chain.NODE, "test": os.environ.get("GNOSEARCH_NODE_TEST", "")}
# img-src 'none': chain pages may embed images, and loading them would let any realm author
# see who reads their page. The reader shows them as links instead.
CSP = ("default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline' "
       "https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'none'; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
# requests per second and burst, per visitor; crawlers getting 429 is by design
LIMITS = {"/api/search": (8, 30), "/api/page": (1, 6), "/api/stats": (1, 5), "/api/importers": (2, 10)}
KINDS = {"realm", "pkg", "code", "user", "token", "tx"}
LIVE_PER_SEC = 2  # live renders from the node for everyone together; above it, pages come from the index


class Bucket:
    def __init__(self):
        self.lock, self.b = threading.Lock(), {}

    def take(self, key, rate, burst):
        now = time.monotonic()
        with self.lock:
            if len(self.b) > 50_000:
                self.b.clear()
            tokens, t = self.b.get(key, (burst, now))
            tokens = min(burst, tokens + (now - t) * rate)
            if tokens < 1:
                self.b[key] = (tokens, now)
                return False
            self.b[key] = (tokens - 1, now)
            return True


visitors, live = Bucket(), Bucket()


class Handler(BaseHTTPRequestHandler):
    server_version = "gnosearch"
    sys_version = ""

    def log_message(self, *a):
        pass

    def visitor(self):
        # a local proxy or tunnel connects from this machine and names the real visitor; trust that header only then
        ip = self.client_address[0]
        return (self.headers.get("CF-Connecting-IP") or ip)[:64] if ip == "127.0.0.1" else ip

    def send(self, code, body, ctype="application/json", cache="no-store", extra=()):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if self.path.startswith("/api/"):
            self.send_header("Access-Control-Allow-Origin", "*")  # the open API: wallets and explorers may call it
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query[:2000]))
        if u.path in LIMITS and not visitors.take((self.visitor(), u.path), *LIMITS[u.path]):
            return self.send(429, {"error": "too many requests"}, extra=[("Retry-After", "2")])
        net = q.get("net", "main")
        if u.path.startswith("/api/") and net not in store.nets():
            return self.send(400, {"error": "unknown network"})
        try:
            if u.path == "/api/search":
                try:
                    n = max(1, min(int(q.get("n", 20)), 50))
                except ValueError:
                    n = 20
                term = q.get("q", "")[:200]
                kind = q.get("kind") or None
                if kind and kind not in KINDS:
                    return self.send(400, {"error": "unknown kind"})
                r = store.search(term, n, net, kind)
                addr = term.strip()
                if store.ADDR.match(addr) and not any(x["url"] == "user:" + addr for x in r["results"]):
                    # any address is findable, even one we never indexed: most exist only from genesis
                    r["results"].insert(0, {"url": "user:" + addr, "realm": "user:" + addr, "kind": "user",
                                            "title": f"{addr[:10]}…{addr[-4:]}", "snippet": "No transactions yet. Open it to see its balance.",
                                            "users": 0, "links": 0, "imports": 0, "txs": 0, "last": 0, "gnot": ""})
                r["height"] = int(store.meta("height", net=net) or 0)
                return self.send(200, r)
            if u.path == "/api/page":
                url = q.get("u", "")
                if not (LIVE_URL.match(url) or INDEX_URL.match(url)):
                    return self.send(400, {"error": "bad page address"})
                realm, _, sub = url.partition(":")
                t0 = time.perf_counter()
                md, is_live = None, False
                if LIVE_URL.match(url) and NODES[net] and live.take("all", LIVE_PER_SEC, LIVE_PER_SEC * 2):
                    try:
                        md, is_live = store.clean(chain.render(realm, sub, node=NODES[net]), store.MD_MAX), True
                    except Exception:
                        pass
                if md is None:
                    p = store.page(url, net)
                    if p:
                        md = p["md"] or ""
                    elif not url.startswith("user:"):
                        return self.send(404, {"error": "page not found"})
                if url.startswith("user:") and NODES[net] and live.take("all", LIVE_PER_SEC, LIVE_PER_SEC * 2):
                    # balance and vesting are live from the node, for indexed and never-indexed accounts alike
                    try:
                        acct, is_live = chain.account(url[5:], node=NODES[net]), True
                        live_md = store.account_md(url[5:], acct)
                        if md:  # the live lines go under the address, above the indexed activity
                            head, rest = md.split("\n")[:4], md.split("\n")[4:]
                            md = "\n".join(head + [l for l in live_md.splitlines()[4:] if l.startswith("- ")] + rest)
                        else:
                            md = live_md
                    except Exception:
                        pass
                if md is None:
                    return self.send(503, {"error": "busy, try again"})
                funcs = []
                if not md.strip() and is_live:  # no page: list what the realm can do instead
                    try:
                        funcs = chain.funcs(realm, node=NODES[net])[:60]
                    except Exception:
                        pass
                return self.send(200, {"url": url, "md": md, "live": is_live, "funcs": funcs,
                                       "took_ms": round((time.perf_counter() - t0) * 1000, 1)})
            if u.path == "/api/importers":
                path = q.get("u", "")
                if not re.fullmatch(r"gno\.land/[rp]/[A-Za-z0-9_./\-]{1,200}", path):
                    return self.send(400, {"error": "bad package path"})
                rows = store.importers(path, net)
                return self.send(200, {"path": path, "importers": [{"path": p, "kind": k, "users": u} for p, k, u in rows]})
            if u.path == "/api/stats":
                return self.send(200, {**store.stats(net), "nets": store.nets()})
            if u.path in STATIC:
                name, ctype = STATIC[u.path]
                with open(os.path.join(WEB, name), "rb") as f:
                    return self.send(200, f.read(), ctype, "no-cache")
            return self.send(404, {"error": "not found"})
        except Exception as e:
            return self.send(500, {"error": type(e).__name__})


def main():
    for n in store.nets():
        store.db(n)
    print(f"serving {os.path.realpath(WEB)} on {BIND}:{PORT}, " + ", ".join(
        f"{n}: index {store.path(n)} node {NODES[n] or 'none'}" for n in store.nets()), flush=True)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
