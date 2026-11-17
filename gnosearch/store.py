"""The search index: one SQLite file per network with full-text search (FTS5) and the ranking signals.

GNOSEARCH_DB (mainnet) and GNOSEARCH_DB_TEST (testnet) have no default (2026-11-15): a hand-run
script that falls back to the production file is how dev code reaches production, so every entry
point must say which file it means. Writers (the crawler) only ever use the "main" slot; a testnet
crawler simply points GNOSEARCH_DB at the testnet file.

An entry has a kind: realm (a rendered page), pkg (a package: readme and source), code (a realm's
source), user (an account). A new kind must be added here (KIND_WEIGHT), in the extractor
(crawl.py), in page() if it is not rendered live, and in the result card (app.js meta()).
"""
import math
import os
import re
import sqlite3
import threading
import time

HI0, HI1 = "\x02", "\x03"  # highlight markers in snippets; the app turns them into mark elements
TITLE_MAX, BODY_MAX, MD_MAX = 160, 50_000, 200_000
WORDS_MAX, WORD_MAX, PREFIX_MIN = 8, 40, 2   # query cost limits; prefix='2 3' below serves PREFIX_MIN
PER_REALM, PER_NS = 3, 5                     # results one entry, and one namespace, may hold
MAX_RESULTS = 100                            # how deep "More results" may page (offset + n)
KIND_WEIGHT = {"realm": 1.0, "pkg": 1.0, "code": 0.6, "user": 0.9, "token": 1.0}
ADDR = re.compile(r"^g1[02-9ac-hj-np-z]{38}$")  # a gno.land address (bech32 characters)
HASH = re.compile(r"^[0-9A-Fa-f]{8,64}$")    # a transaction hash, or the start of one
# a package path typed as such: with or without https:// and gno.land, a leading or trailing slash,
# a :sub-page or $source; path segments as the node allows them
PATH = re.compile(r"^(?:https?://)?(?:gno\.land)?/?([rp](?:/[A-Za-z0-9_\-.]{1,100}){1,12})/?((?::[^\s]{0,300})|\$source)?$")
OWN_PAGES, CHILDREN = 3, 5  # an exact path shows itself, a few of its own pages, then its sub-paths

SCHEMA = """
pragma journal_mode=wal;
create table if not exists pages(
  url text primary key,      -- 'gno.land/r/x/y', 'gno.land/r/x/y:sub', 'gno.land/p/x$source', 'user:g1...'
  realm text not null,       -- the entry it belongs to: a package path, or 'user:g1...'
  title text, body text, md text,
  crawled_at integer, height integer, kind text not null default 'realm');
create virtual table if not exists fts using fts5(
  url unindexed, title, path, body,
  tokenize='unicode61 remove_diacritics 2', prefix='2 3');
create table if not exists realms(   -- one row per entry (realm, package or user) with its signals
  realm text primary key, inbound integer default 0, calls integer default 0, crawled_at integer,
  users integer default 0, imports integer default 0, last_height integer default 0, code_done integer default 0,
  gnot integer default 0,   -- ugnot sent in through calls over the usage window; shown, not ranked
  uweight real default 0);  -- users weighted by active days (indexer.usage); ranked, while users is shown
create index if not exists pages_kind on pages(kind);  -- find_token runs on every search (2026-11-16)
create table if not exists imports(src text, dst text, primary key(src, dst)) without rowid;
create table if not exists tx(hash text primary key, height integer, time text, ok integer,
  caller text, kind text, pkg text, func text, coins text) without rowid;
create table if not exists meta(k text primary key, v text);
drop table if exists owners;  -- the dropped directory realm's entries (2026-11-15)
"""
ADDED = {"pages": [("kind", "text not null default 'realm'")], "tx": [("coins", "text")],
         "realms": [("users", "integer default 0"), ("imports", "integer default 0"),
                    ("last_height", "integer default 0"), ("code_done", "integer default 0"), ("gnot", "integer default 0"),
                    ("uweight", "real default 0")]}

_local = threading.local()
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")


def path(net="main"):
    p = os.environ.get("GNOSEARCH_DB" if net == "main" else "GNOSEARCH_DB_TEST")
    if not p:
        raise SystemExit(f"no index file for the {net} network: set GNOSEARCH_DB" + ("" if net == "main" else "_TEST"))
    return p


def nets():
    return ["main"] + (["test"] if os.environ.get("GNOSEARCH_DB_TEST") else [])


def db(net="main"):
    cache = getattr(_local, "c", None)
    if cache is None:
        cache = _local.c = {}
    c = cache.get(net)
    if c is None:
        p = path(net)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        c = sqlite3.connect(p, timeout=30, check_same_thread=False)
        c.executescript(SCHEMA)
        for table, cols in ADDED.items():  # indexes built before a column existed gain it in place
            have = {r[1] for r in c.execute(f"pragma table_info({table})")}
            for col, decl in cols:
                if col not in have:
                    c.execute(f"alter table {table} add column {col} {decl}")
        cache[net] = c
    return c


def clean(s, n):
    """Chain text as stored: no control or bidi characters (which also keeps our snippet markers
    unforgeable), at most n characters."""
    return _CTRL.sub("", s or "")[:n]


def namespace(entry):
    # 'gno.land/r/gnoswap/pool' -> 'gnoswap'; one deployer's namespace is one voice in the results
    if entry.startswith("user:"):
        return entry
    return re.sub(r"^gno\.land/[rp]/", "", entry).split("/")[0]


def path_words(url):
    # 'gno.land/r/gnoswap/pool:x' -> 'gnoswap pool x' so names are searchable as words
    return " ".join(w for w in re.split(r"[/:._\-$?=&]+", re.sub(r"^gno\.land/|^user:", "", url)) if w)


def put_realm(entry, pages, ts, height, kind="realm"):
    """Replace the pages of one kind of one entry. pages = [(url, title, body, md)]. Unchanged
    entries are not rewritten: the disks are worn, and most do not change between crawls."""
    pages = [(u, clean(t, TITLE_MAX), clean(b, BODY_MAX), clean(m, MD_MAX)) for u, t, b, m in pages]
    c = db()
    old = c.execute("select url, title, body, md from pages where realm=? and kind=? order by url", (entry, kind)).fetchall()
    with c:
        if sorted(old) != sorted(pages):
            c.executemany("delete from fts where url=?", [(r[0],) for r in old])
            c.execute("delete from pages where realm=? and kind=?", (entry, kind))
            for url, title, body, md in pages:
                c.execute("insert or replace into pages(url,realm,title,body,md,crawled_at,height,kind) values(?,?,?,?,?,?,?,?)",
                          (url, entry, title, body, md, ts, height, kind))
                c.execute("insert into fts(url,title,path,body) values(?,?,?,?)", (url, title, path_words(url), body))
        c.execute("insert into realms(realm,crawled_at) values(?,?) on conflict(realm) do update set crawled_at=excluded.crawled_at", (entry, ts))


def drop_realm(entry):
    c = db()
    with c:
        old = [r[0] for r in c.execute("select url from pages where realm=?", (entry,))]
        c.executemany("delete from fts where url=?", [(u,) for u in old])
        c.execute("delete from pages where realm=?", (entry,))
        c.execute("delete from realms where realm=?", (entry,))
        c.execute("delete from imports where src=?", (entry,))


def set_signals(**cols):
    """set_signals(users={...}, inbound={...}, ...): each named column is reset, then set per entry."""
    c = db()
    with c:
        for col, vals in cols.items():
            if col not in ("users", "uweight", "inbound", "calls", "imports", "last_height", "gnot"):
                raise ValueError(col)
            if vals is not None:
                c.execute(f"update realms set {col}=0")
                c.executemany(f"update realms set {col}=? where realm=?", [(n, r) for r, n in vals.items()])


def put_imports(src, dsts):
    c = db()
    with c:
        c.execute("insert or ignore into realms(realm) values(?)", (src,))
        c.execute("delete from imports where src=?", (src,))
        c.executemany("insert or ignore into imports values(?,?)", [(src, d) for d in dsts])
        c.execute("update realms set code_done=1 where realm=?", (src,))


def import_counts():
    """Importers per package, counted by namespace: twenty packages of one owner importing a target
    are one vote, and an owner importing their own package is none."""
    votes = {}
    for src, dst in db().execute("select src, dst from imports"):
        if namespace(src) != namespace(dst):
            votes.setdefault(dst, set()).add(namespace(src))
    return {d: len(v) for d, v in votes.items()}


def weights():
    return dict(db().execute("select realm, uweight from realms where uweight > 0"))


def put_txs(rows):
    """rows = [(hash, height, time, ok, caller, kind, pkg, func, coins)], from the indexer."""
    c = db()
    with c:
        c.executemany("insert or ignore into tx values(?,?,?,?,?,?,?,?,?)",
                      [(h.upper(), ht, t, ok, clean(ca, 64), clean(k, 16), clean(p, 200), clean(f, 64), clean(co, 300))
                       for h, ht, t, ok, ca, k, p, f, co in rows])


def put_coins(rows):
    """rows = [(hash, coins)]: coins the indexer has filled in since a transaction was copied. Only
    rows that differ are written (worn disks)."""
    c = db()
    with c:
        c.executemany("update tx set coins=? where hash=? and coins is not ?", [(clean(co, 300), h.upper(), clean(co, 300)) for h, co in rows])


def amount(raw, decimals):
    """'1,234.5' from an integer amount and the token's decimals, exact (Decimal): amounts pass 2^53."""
    from decimal import Decimal
    g = Decimal(int(raw)) / (Decimal(10) ** max(0, min(int(decimals), 30)))
    whole, _, frac = f"{g:f}".partition(".")
    frac = frac.rstrip("0")[:max(0, min(int(decimals), 30))]
    return f"{int(whole):,}" + (f".{frac}" if frac else "")


def token_md(t, window_ok):
    """The page of one GRC20 token. Names and symbols are chain input (cleaned by put_realm)."""
    sym, dec = clean(t["symbol"], 32), t["decimals"]
    realm = t["realm"].removeprefix("gno.land")
    md = [f"# {clean(t['name'], 80)} ({sym})", "", f"- Token of [{realm}]({realm})",
          f"- Supply {amount(t['supply'], dec)} {sym}", f"- {t['holders']:,} holders"]
    if window_ok:
        md.append(f"- {t['moves']:,} transfers, {amount(t['moved'], dec)} {sym} moved in the last 30 days")
    if t["created"]:
        md.append(f"- Created in block {t['created']:,}")
    if t["top"]:
        md += ["", "Top holders:", ""]
        for a, v in t["top"]:
            share = f" ({v * 100 / t['supply']:.1f}%)" if t["supply"] else ""
            a = clean(a, 64)
            md.append(f"1. [{a[:10]}…{a[-4:]}](/u/{a}) {amount(v, dec)} {sym}{share}")  # short text: phones wrap full addresses
    return "\n".join(md) + "\n"


def gnot(ugnot):
    """'1,234.5' from ugnot as an int: exact (Decimal), because sums can pass 2^53."""
    from decimal import Decimal
    g = Decimal(int(ugnot)) / Decimal(1_000_000)
    whole, _, frac = f"{g:f}".partition(".")
    frac = frac.rstrip("0")[:6]
    return f"{int(whole):,}" + (f".{frac}" if frac else "")


def coins_text(coins):
    """'3540000ugnot,7/gno.land/r/x:y' -> '3.54 GNOT, 7 /gno.land/r/x:y'; '' -> 'nothing'."""
    out = []
    for part in (coins or "").split(","):
        m = re.fullmatch(r"(\d{1,40})(\S{1,120})", part.strip())
        if m:
            out.append(f"{gnot(int(m[1]))} GNOT" if m[2] == "ugnot" else f"{int(m[1]):,} {m[2]}")
    return ", ".join(out) or "nothing"


def meta(k, v=None, net="main"):
    c = db(net)
    if v is None:
        r = c.execute("select v from meta where k=?", (k,)).fetchone()
        return r[0] if r else None
    with c:
        c.execute("insert or replace into meta values(?,?)", (k, str(v)))


def fts_query(q):
    """The FTS5 expression for a search box value, or None. Only word characters reach FTS5, each
    word quoted, so operators (NEAR, OR, column filters, quotes) typed in the box are plain text.
    Every word must match; the last is a prefix so results follow each key press, but only from
    PREFIX_MIN characters, because a one-letter prefix matches almost every row."""
    words = [w[:WORD_MAX] for w in re.findall(r"\w+", q.lower())][:WORDS_MAX]
    if not words:
        return None
    last = f'"{words[-1]}"' + ("*" if len(words[-1]) >= PREFIX_MIN else "")
    return " ".join([f'"{w}"' for w in words[:-1]] + [last])


def find_tx(q, net="main", n=5):
    q = q.strip()
    if not HASH.match(q):
        return []
    lo = q.upper()
    rows = db(net).execute("select hash, height, time, ok, caller, kind, pkg, func from tx where hash >= ? and hash < ? limit ?",
                           (lo, lo + "G", n)).fetchall()
    return [{"url": f"tx:{h}", "realm": f"tx:{h}", "kind": "tx", "title": f"Transaction {h[:12]}",
             "snippet": tx_line(ca, k, p, f, ok), "height": ht, "time": t} for h, ht, t, ok, ca, k, p, f in rows]


def tx_what(kind, pkg, func, ok):
    what = {"call": f"called {pkg.removeprefix('gno.land')}.{func}", "addpkg": f"deployed {pkg.removeprefix('gno.land')}",
            "send": "sent coins", "run": "ran a script"}.get(kind, kind or "sent a transaction")
    return what + ("" if ok else ", failed")


def tx_line(caller, kind, pkg, func, ok):
    return f"{caller or 'Someone'} {tx_what(kind, pkg, func, ok)}"


def entry_result(url, net="main"):
    """One page as a result, with the same fields search() gives, or None."""
    r = db(net).execute("""select p.url, p.realm, p.kind, p.title, substr(p.body, 1, 200), coalesce(r.users,0), coalesce(r.inbound,0),
                                  coalesce(r.imports,0), coalesce(r.calls,0), coalesce(r.last_height,0), coalesce(r.gnot,0)
                           from pages p left join realms r on r.realm=p.realm where p.url=?""", (url,)).fetchone()
    if not r:
        return None
    url, realm, kind, title, snip, users, inbound, imports, calls, last, ug = r
    return {"url": url, "realm": realm, "kind": kind, "title": title, "snippet": snip, "users": users, "links": inbound,
            "imports": imports, "txs": calls if kind == "user" else 0, "last": last,
            "gnot": gnot(ug) if ug and ":" not in url and kind == "realm" else ""}


def find_path(q, net="main"):
    """Results for a query that is a package path (2026-11-15: an exact path must come first): the
    exact page, then the entry's own pages and source, then its sub-paths by use. [] otherwise."""
    m = PATH.match(q.strip())
    if not m:
        return []
    entry, tail = "gno.land/" + m[1], m[2] or ""
    c = db(net)
    if not c.execute("select 1 from pages where realm=? limit 1", (entry,)).fetchone():
        return []
    urls = [entry + tail, entry]
    urls += [u for (u,) in c.execute("select url from pages where realm=? and url like ? order by length(url), url limit ?",
                                     (entry, entry + ":%", OWN_PAGES))]
    urls.append(entry + "$source")
    urls += [r for (r,) in c.execute("""select realm from realms where realm like ? escape '\\' and realm <> ?
                                         order by uweight desc, realm limit ?""",
                                     (entry.replace("_", "\\_").replace("%", "\\%") + "/%", entry, CHILDREN))]
    out, seen = [], set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        r = entry_result(u, net)
        if r:
            out.append(r)
    return out


def find_token(q, net="main"):
    """Tokens whose symbol or name is exactly the query, any case: typing a symbol means that token.
    The title is 'Name (SYMBOL)', so both halves are matched without a column of their own."""
    t = q.strip().lower()
    if not t or len(t) > 80:
        return []
    rows = db(net).execute("""select url, title from pages where kind='token' and
                              (lower(title) = ? or lower(title) like ? or lower(title) like ?)""",
                           (t, "% (" + t + ")", t + " (%")).fetchall()
    return [r for r in (entry_result(u, net) for u, _ in rows[:5]) if r]


def importers(dst, net="main", n=50):
    """Packages and realms importing dst, most used first: [(path, kind, users)]."""
    return db(net).execute("""select i.src, case when i.src like 'gno.land/p/%' then 'pkg' else 'realm' end, coalesce(r.users, 0)
                              from imports i left join realms r on r.realm=i.src where i.dst=?
                              order by coalesce(r.uweight,0) desc, i.src limit ?""", (dst, n)).fetchall()


def search(q, n=20, net="main", kind=None, offset=0):
    """Ranked results offset..offset+n (up to MAX_RESULTS deep), and whether more follow."""
    t0 = time.perf_counter()
    offset = max(0, min(int(offset), MAX_RESULTS - 1))
    if kind:  # one kind only (agents asking for packages): rank as usual, then keep that kind
        ranked = [x for x in _ranked(q, 200, net) if x["kind"] == kind]
    else:
        ranked = _ranked(q, min(offset + n + 1, MAX_RESULTS + 1), net)
    end = min(offset + n, MAX_RESULTS)
    return {"results": ranked[offset:end], "more": len(ranked) > end and end < MAX_RESULTS,
            "took_ms": round((time.perf_counter() - t0) * 1000, 1)}


def _ranked(q, n, net):
    """The first n results for q, best first: transaction, exact path and token matches, then text."""
    out = find_tx(q, net) + find_path(q, net)
    out += [r for r in find_token(q, net) if r["url"] not in {x["url"] for x in out}]
    fq = fts_query(q)
    if not fq:
        return out[:n]
    rows = db(net).execute(
        f"""select f.url, p.realm, p.kind, p.title, snippet(fts, 3, '{HI0}', '{HI1}', '…', 14),
                   bm25(fts, 0, 8.0, 4.0, 1.0), coalesce(r.inbound,0), coalesce(r.calls,0), coalesce(r.users,0),
                   coalesce(r.imports,0), coalesce(r.last_height,0), coalesce(r.gnot,0), coalesce(r.uweight,0)
            from fts f join pages p on p.url=f.url left join realms r on r.realm=p.realm
            where fts match ? order by bm25(fts, 0, 8.0, 4.0, 1.0) limit 200""", (fq,)).fetchall()
    scored = []
    for url, realm, kind, title, snip, bm, inbound, calls, users, imports, last, ug, uw in rows:
        # Ranking will be gamed, so text match alone decides little. Real use counts most: uw is users
        # weighted by account age, breadth and active days (see indexer.usage). Inbound and imports
        # count distinct other namespaces linking here or importing it. Raw calls count little,
        # because one bot makes thousands.
        boost = (1 + 0.45 * math.log1p(uw) + 0.05 * math.log1p(calls) + 0.25 * math.log1p(inbound)
                 + 0.3 * math.log1p(imports) + (0.3 if ":" not in url and "$" not in url else 0))
        if snip.startswith("realm without a page"):
            boost *= 0.35  # a realm with something to read beats one that is only a name
        boost *= KIND_WEIGHT.get(kind, 1.0)
        scored.append((bm * boost, url, realm, kind, title, snip, users, inbound, imports, calls, last, ug))  # bm25 < 0: lower is better
    scored.sort()
    per_realm, per_ns, seen = {}, {}, {r["url"] for r in out}
    for r in out:  # exact-path results count towards the caps too
        per_realm[r["realm"]] = per_realm.get(r["realm"], 0) + 1
    for _, url, realm, kind, title, snip, users, inbound, imports, calls, last, ug in scored:
        ns = namespace(realm)
        if url in seen:
            continue
        if per_realm.get(realm, 0) >= PER_REALM or per_ns.get(ns, 0) >= PER_NS:
            continue  # one entry, or one namespace full of look-alikes, cannot fill the page
        per_realm[realm] = per_realm.get(realm, 0) + 1
        per_ns[ns] = per_ns.get(ns, 0) + 1
        out.append({"url": url, "realm": realm, "kind": kind, "title": title, "snippet": snip, "users": users,
                    "links": inbound, "imports": imports, "txs": calls if kind == "user" else 0, "last": last,
                    "gnot": gnot(ug) if ug and ":" not in url and kind == "realm" else ""})
        if len(out) >= n:
            break
    return out[:n]


def page(url, net="main"):
    if url.startswith("tx:"):
        r = db(net).execute("select hash, height, time, ok, caller, kind, pkg, func, coins from tx where hash=?", (url[3:],)).fetchone()
        if not r:
            return None
        h, ht, t, ok, ca, k, p, f, co = r
        lo, hi = meta("coins_lo", net=net), meta("coins_hi", net=net)  # None until coverage is known; 0 is genesis
        md = (f"# Transaction\n\n`{h}`\n\n- Block {ht:,}\n- {t[:19].replace('T', ' ')} UTC\n- {'Succeeded' if ok else 'Failed'}\n"
              f"- From [{ca}](/u/{ca})\n- {(lambda w: w[:1].upper() + w[1:])(tx_what(k, p, f, True))}\n")
        if lo is not None and int(lo) <= ht <= int(hi) and co is not None:  # outside the indexer's coins coverage an empty value means unknown
            md += f"- Sent {coins_text(co)}{'' if ok else ' (failed, so nothing moved)'}\n"
        if p.startswith("gno.land/r/"):
            md += f"\n[Open {p.removeprefix('gno.land')}]({p.removeprefix('gno.land')})\n"
        return {"url": url, "realm": url, "title": "Transaction", "md": md, "crawled_at": 0, "height": ht}
    r = db(net).execute("select url, realm, title, md, crawled_at, height from pages where url=?", (url,)).fetchone()
    return dict(zip(["url", "realm", "title", "md", "crawled_at", "height"], r)) if r else None


def account_md(addr, acct):
    """Markdown for an account read live from the node (chain.account). Values are chain input."""
    if not acct:
        return f"# {addr[:10]}…{addr[-4:]}\n\n`{addr}`\n\nNo account at this address on chain yet.\n"
    md = [f"# {addr[:10]}…{addr[-4:]}", "", f"`{addr}`", "", f"- Balance {coins_text(clean(acct.get('coins'), 300))}"]
    v = acct.get("vesting") or {}
    if v.get("original_vesting"):
        when = lambda t: time.strftime("%Y-%m-%d", time.gmtime(int(t))) if str(t).isdigit() else "?"
        md.append(f"- Vesting {coins_text(clean(v['original_vesting'], 300))}, from {when(v.get('start_time'))} to {when(v.get('end_time'))}")
    md.append("- No transactions yet" if str(acct.get("sequence", "0")) == "0" else f"- Signed {int(acct['sequence']):,} transactions")
    return "\n".join(md) + "\n"


def stats(net="main"):
    c = db(net)
    kinds = dict(c.execute("select kind, count(*) from pages group by kind"))
    return {"pages": kinds.get("realm", 0), "realms": c.execute("select count(*) from realms where realm like 'gno.land/r/%'").fetchone()[0],
            "packages": kinds.get("pkg", 0), "users": kinds.get("user", 0),
            "txs": c.execute("select count(*) from tx").fetchone()[0], "height": int(meta("height", net=net) or 0)}
