"""Crawls gno.land: renders each realm's pages and follows its sub-page links, reads the source of
packages and realms once, and builds account and transaction entries from the indexer.

Usage: python3 -m gnosearch.crawl full     # everything (daily, off-peak)
       python3 -m gnosearch.crawl follow   # watch new blocks, re-crawl only the realms touched
The network is whatever GNOSEARCH_DB, GNOSEARCH_NODE and GNOSEARCH_CHAIN say; where() prints them.
"""
import collections
import re
import os
import sys
import time

from . import chain, indexer, store

MAX_PAGES = 30   # sub-pages crawled per realm
MAX_DEPTH = 2
USAGE_BLOCKS = 740_000  # about 30 days of blocks, for the usage ranking
FILE_BUDGET = 1500      # source files read per full run; code never changes, so the rest follow next run
SRC_MAX = 40_000        # characters of one file kept
IMPORT = re.compile(r'"(gno\.land/[pr]/[A-Za-z0-9_./\-]{1,200})"')

LINK = re.compile(r"""(?:\]\(|href=["'])(/r/[^)"'\s#]+)""")


def split(link):
    """'/r/x/y:sub' -> ('gno.land/r/x/y', 'sub'); skips source and help views."""
    path, _, sub = link.partition(":")
    if "$" in path or sub.startswith("$") or "$" in sub:
        return None
    return "gno.land" + path.rstrip("/"), sub


def text_of(md):
    t = re.sub(r"```.*?```", " ", md, flags=re.S)                      # code blocks
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)                        # images
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)                     # links keep their words
    t = re.sub(r"<[^>]+>", " ", t)                                     # html and gno tags
    t = re.sub(r"data:[^\s)]+", " ", t)                                # inline data blobs
    t = re.sub(r"[#>*_`|~\-]{1,}", " ", t)                             # markdown marks
    return re.sub(r"\s+", " ", t).strip()


def title_of(md, url):
    m = re.search(r"^\s{0,3}#{1,3}\s+(.+)$", md, flags=re.M)
    if m:
        return re.sub(r"[*_`\[\]]|\(.*?\)", "", m.group(1)).strip()[:160]
    return url.replace("gno.land/r/", "")


def crawl_realm(realm):
    """All pages of one realm, breadth first. Returns (pages, the other realms it links to)."""
    pages, seen, out = [], set(), set()
    queue = collections.deque([("", 0)])
    while queue and len(pages) < MAX_PAGES:
        sub, depth = queue.popleft()
        if sub in seen:
            continue
        seen.add(sub)
        try:
            md = store.clean(chain.render(realm, sub), store.MD_MAX)
        except chain.QueryError as e:
            if sub == "":  # no Render at all: still findable by name and functions
                try:
                    funcs = chain.funcs(realm)
                except Exception:
                    funcs = []
                body = "realm without a page. functions: " + " ".join(funcs)
                pages.append((realm, realm.replace("gno.land/r/", ""), body, ""))
            continue
        except Exception:
            continue
        url = realm if sub == "" else f"{realm}:{sub}"
        pages.append((url, title_of(md, url), text_of(md), md))
        for link in LINK.findall(md):
            s = split(link)
            if not s:
                continue
            other, osub = s
            if other == realm:
                if depth + 1 <= MAX_DEPTH and osub not in seen:
                    queue.append((osub, depth + 1))
            else:
                out.add(other)
    return pages, out


def crawl(realms, h):
    """Crawl realms one after another. Returns inbound votes: the distinct other namespaces linking
    to each realm, so a link repeated a thousand times, or twenty realms of one owner linking to
    their own target, is one vote (or none). Sequential on purpose, so a crawl
    never crowds the node it reads from."""
    inbound = collections.defaultdict(set)
    ts = int(time.time())
    for r in realms:
        pages, out = crawl_realm(r)
        store.put_realm(r, pages, ts, h)
        for target in out:
            if store.namespace(target) != store.namespace(r):
                inbound[target].add(store.namespace(r))
    return {t: len(v) for t, v in inbound.items()}


def is_source(name):
    # tests and file tests are most of a package's bytes and none of what it offers
    return name.endswith((".gno", ".md", ".toml")) and not name.endswith("_test.gno") and not name.startswith("z_")


def read_code(entry, budget):
    """Index the source of one package or realm, once (deployed code never changes). Returns files read."""
    names = [n for n in chain.files(entry) if is_source(n)][:40]
    if len(names) + 1 > budget:
        return 0
    texts, imports = [], set()
    for n in names:
        try:
            src = store.clean(chain.file(entry, n), SRC_MAX)
        except Exception:
            continue
        texts.append((n, src))
        if n.endswith(".gno"):
            imports.update(i for i in IMPORT.findall(src) if i != entry)
    readme = next((t for n, t in texts if n.lower() == "readme.md"), "")
    docs = " ".join(re.findall(r"^\s*//\s?(.*)$", " ".join(t for n, t in texts if n.endswith(".gno")), flags=re.M))
    code = "\n".join(t for n, t in texts if n.endswith(".gno"))
    md = (readme + "\n\n" if readme else "") + "\n\n".join(
        f"### {n}\n\n```{'go' if n.endswith('.gno') else ''}\n{t}\n```" for n, t in texts if n.lower() != "readme.md")
    ts, short = int(time.time()), entry.removeprefix("gno.land")
    if entry.startswith("gno.land/p/"):
        body = " ".join([text_of(readme), docs, code])
        store.put_realm(entry, [(entry, title_of(readme, short) if readme else short, body, md)], ts, 0, kind="pkg")
    else:
        store.put_realm(entry, [(entry + "$source", f"{short} source", " ".join([docs, code]), md)], ts, 0, kind="code")
    store.put_imports(entry, imports)
    return len(names) + 1


def code(entries):
    """Read source for entries not read yet, within FILE_BUDGET requests."""
    done = {r for (r,) in store.db().execute("select realm from realms where code_done=1")}
    budget = FILE_BUDGET
    for e in entries:
        if e in done:
            continue
        try:
            used = read_code(e, budget)
        except Exception as ex:
            print("source error:", e, str(ex)[:120], flush=True)
            store.put_imports(e, [])  # marks it read: a path the node cannot list is not retried every block
            continue
        if not used:
            break
        budget -= used
    print(f"source: {FILE_BUDGET - budget} files read", flush=True)


def users(h):
    """One entry per account: its names, how much it does and where."""
    names = indexer.names()
    ts = int(time.time())
    live, calls, last = set(), {}, {}
    for addr, n, first, latest, top in indexer.accounts():
        addr = store.clean(addr, 64)
        if not re.fullmatch(r"g1[0-9a-z]{38}", addr):
            continue
        own = [(k, store.clean(v, 64)) for k, v in names.get(addr, [])]
        label = next((v for k, v in reversed(own) if k == "name"), None) or next((v for k, v in reversed(own) if k == "validator"), None)
        title = label or f"{addr[:10]}…{addr[-4:]}"
        md = [f"# {title}", "", f"`{addr}`", ""]
        md += [f"- {'Username' if k == 'name' else 'Validator'} {v}" for k, v in own]
        md += [f"- {n:,} messages, first in block {first:,}, last in block {latest:,}", ""]
        if top:
            md += ["Most used:", ""] + [f"- [{p.removeprefix('gno.land')}]({p.removeprefix('gno.land')})" for p in top if p.startswith("gno.land/")]
        body = " ".join([v for _, v in own] + [addr, "validator" if any(k == "validator" for k, _ in own) else ""] + top)
        e = "user:" + addr
        store.put_realm(e, [(e, title, body, "\n".join(md))], ts, h, kind="user")
        live.add(e); calls[e] = n; last[e] = latest
    for (old,) in store.db().execute("select realm from realms where realm like 'user:%'").fetchall():
        if old not in live:
            store.drop_realm(old)
    return calls, last


def coins():
    """Record the indexer's coins coverage, and copy coins into transactions copied before the
    indexer had them. Returns the coverage, or None."""
    cov = indexer.coverage()
    if not cov:
        return None
    store.meta("coins_lo", cov[0]); store.meta("coins_hi", cov[1])
    # copied range [done_lo, done_hi]; coverage grows at both ends (the refill walks history back to
    # genesis while new blocks arrive), so each side is caught up on its own
    upto = min(cov[1], int(store.meta("tx_height") or 0))
    done_lo, done_hi = int(store.meta("coins_done_lo") or upto), int(store.meta("coins_done_hi") or upto)
    for lo, hi in ((cov[0] - 1, min(done_lo, upto)), (max(done_hi, cov[0] - 1), upto)):
        while lo < hi:
            step = min(lo + indexer.TX_SPAN, hi)
            store.put_coins(indexer.coins(lo, step))
            lo = step
    store.meta("coins_done_lo", min(done_lo, cov[0] - 1)); store.meta("coins_done_hi", max(done_hi, upto))
    return cov


def tokens(h):
    """One page per GRC20 token, grouped under its realm so it shares the realm's ranking signals and
    caps. Holders are shown, never ranked: airdropping dust to thousands of addresses is cheap."""
    cov = indexer.token_coverage()
    window_ok = bool(cov and cov[0] <= h - USAGE_BLOCKS and cov[1] >= h - 1000)
    by_realm = collections.defaultdict(list)
    for t in indexer.tokens(h - USAGE_BLOCKS, h):
        if t["realm"].startswith("gno.land/r/"):
            by_realm[t["realm"]].append(t)
    ts = int(time.time())
    for realm, toks in by_realm.items():
        pages = [("token:" + store.clean(t["token"], 250), f"{t['name']} ({t['symbol']})",
                  " ".join([t["name"], t["symbol"], "token grc20", store.path_words(realm)]), store.token_md(t, window_ok))
                 for t in toks]
        store.put_realm(realm, pages, ts, h, kind="token")
    for (old,) in store.db().execute("select distinct realm from pages where kind='token'").fetchall():
        if old not in by_realm:
            store.put_realm(old, [], ts, h, kind="token")
    return sum(len(v) for v in by_realm.values())


def txs(upto):
    """Copy new transactions from the indexer, TX_SPAN blocks per read."""
    h = int(store.meta("tx_height") or 0)
    while h < upto:
        hi = min(h + indexer.TX_SPAN, upto)
        store.put_txs(indexer.txs(h, hi))
        h = hi
        store.meta("tx_height", h)


def jumps(new, factor=5, floor=5):
    """Log realms whose user weight jumped factor times since the last run: a burst of new accounts
    is how a ranking attack looks, and a person should look before it is trusted."""
    old = store.weights()
    if not old:  # the first run has nothing to compare with
        return
    for r, w in sorted(new.items(), key=lambda x: -x[1]):
        if w >= floor and w > factor * old.get(r, 0):
            print(f"rank jump: {r} user weight {old.get(r, 0):.1f} -> {w:.1f}", flush=True)


def where():
    print(f"index {store.path()}, node {chain.NODE} every {chain.GAP}s, history {indexer.CHAIN}"
          f" via {'INDEXER_RO_URL' if os.environ.get('INDEXER_RO_URL') else 'nothing (INDEXER_RO_URL unset)'}", flush=True)


def full():
    where()
    t0 = time.time()
    h = chain.height()
    realms, pkgs = chain.paths("gno.land/r/"), chain.paths("gno.land/p/")
    live = set(realms) | set(pkgs)
    for (old,) in store.db().execute("select realm from realms where realm like 'gno.land/%'").fetchall():
        if old not in live:
            store.drop_realm(old)
    inbound = crawl(realms, h)
    code(pkgs + realms)
    calls, users_, weight, last = indexer.usage(h - USAGE_BLOCKS, h)
    jumps(weight)
    ucalls, ulast = users(h)
    ntok = tokens(h)
    txs(indexer.top_height())
    cov = coins()
    # GNOT in is shown only when the indexer's coins cover the whole window; a partly covered window
    # would read as "little was sent" when it was only not recorded yet
    gnot = indexer.gnot_in(h - USAGE_BLOCKS, h) if cov and cov[0] <= h - USAGE_BLOCKS and cov[1] >= h - 1000 else {}
    store.set_signals(inbound={r: n for r, n in inbound.items() if r in live}, calls={**calls, **ucalls},
                      users=users_, uweight=weight, imports=store.import_counts(), last_height={**last, **ulast}, gnot=gnot)
    store.meta("height", h)
    s = store.stats()
    print(f"full crawl: {len(realms)} realms, {len(pkgs)} packages, {ntok} tokens, {s['users']} users, {s['txs']} transactions,"
          f" {time.time() - t0:.0f}s, height {h}", flush=True)


def follow(every=3.0):
    """Re-crawl realms as transactions touch them, within a block or two, and copy the new transactions."""
    where()
    h = int(store.meta("height") or chain.height())
    checked = 0.0
    while True:
        try:
            if time.time() - checked > 600:  # coverage costs a second or more, so every ten minutes
                checked = time.time()
                coins()
            touched, top = indexer.touched_since(h)
            realms = sorted(p for p in touched if p.startswith("gno.land/r/"))
            if realms:
                t0 = time.time()
                crawl(realms, top)
                print(f"height {top}: re-crawled {len(realms)} realm(s) in {time.time() - t0:.1f}s", flush=True)
            new = sorted(p for p in touched if p.startswith(("gno.land/r/", "gno.land/p/")) and not store.db().execute(
                "select 1 from realms where realm=? and code_done=1", (p,)).fetchone())
            if new:
                code(new)  # a fresh deploy is searchable by its source within seconds too
            if top > h:  # the indexer is the clock: only heights it has read are done
                txs(top)
                h = top
                store.meta("height", h)
        except Exception as e:
            print("follow error:", e, flush=True)
            time.sleep(30)  # an indexer or node outage logs twice a minute, not every few seconds
        time.sleep(every)


if __name__ == "__main__":
    {"full": full, "follow": follow}[sys.argv[1] if len(sys.argv) > 1 else "full"]()
