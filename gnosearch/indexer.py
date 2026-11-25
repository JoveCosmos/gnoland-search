"""Chain history from a gno.land transaction indexer (Postgres), read only.

INDEXER_RO_URL is a read-only login (SELECT only, a short statement timeout). Only the crawler
calls this, in batches by height; searches are served from our own index and never query the
indexer. The indexer's owner controls these tables, so the
columns we read are listed in COLUMNS and checked by tests/test_indexer.py: a query here and
COLUMNS change together.
"""
import csv
import io
import os
import subprocess

CHAIN = os.environ.get("GNOSEARCH_CHAIN", "gnoland-1")
COLUMNS = {"gno_tx_msg": ["chain_id", "tx_hash", "msg_index", "height", "success", "kind", "pkg_path", "func", "caller", "args",
                          "coins", "ugnot"],
           "gno_tx": ["chain_id", "hash", "height", "time", "success"],
           "gno_grc20_token": ["chain_id", "token", "realm", "name", "symbol", "decimals", "created_height"],
           "gno_grc20_balance": ["chain_id", "token", "address", "balance"],
           "gno_grc20_transfer": ["chain_id", "height", "token", "from_addr", "to_addr", "amount"]}
TOP_HOLDERS = 10
NAME_FUNCS = {("gno.land/r/sys/namereg/v0", "Register"): "name",       # usernames
              ("gno.land/r/gnops/valopers", "Register"): "validator"}  # validator names
TX_SPAN = 50_000  # blocks per transaction read; about 10,000 transactions at mainnet's busiest
FOLLOW_SPAN = 5000   # blocks read per follow step, so a long outage catches up in small reads
ESTABLISHED = 50_000  # an account counts as a user only if it existed this many blocks (about 2 days) before
BLOCKS_PER_DAY = 24_700  # mainnet's pace, ~3.5 s blocks; days are counted as height buckets, no time join
ACTIVE_DAYS = 3          # active days in the window for a user to weigh in full


class IndexerError(Exception):
    pass


def query(sql, **params):
    """Rows of a query. Values go in as psql variables (:'name' quotes them), never into the SQL text."""
    url = os.environ.get("INDEXER_RO_URL")
    if not url:
        raise IndexerError("INDEXER_RO_URL is not set")
    args = ["psql", url, "-X", "-q", "--csv", "-v", "ON_ERROR_STOP=1"]
    for k, v in params.items():
        args += ["-v", f"{k}={v}"]
    r = subprocess.run(args, input=sql, capture_output=True, text=True, timeout=30)
    if r.returncode:
        raise IndexerError(r.stderr.strip()[:300])
    return list(csv.reader(io.StringIO(r.stdout)))[1:]


def touched_since(h):
    """Packages and realms called or deployed by successful messages in the FOLLOW_SPAN blocks above h, and
    the height read up to: the last transaction there, or, when there is none, the block before the next
    transaction, so a quiet stretch longer than the span never stops follow (2026-11-25: it waited for a
    transaction inside the span and stood still). Reads by transaction height, which gno_tx indexes:
    gno_tx_msg has no height index, and polling it by height scanned the whole table every few seconds."""
    lo, hi = int(h), int(h) + FOLLOW_SPAN
    rows = query("""select coalesce(m.pkg_path, ''), max(t.height) from gno_tx t
                    left join gno_tx_msg m on m.chain_id = t.chain_id and m.tx_hash = t.hash and m.success
                    where t.chain_id = :'chain' and t.height > :'lo'::bigint and t.height <= :'hi'::bigint
                    group by 1""", chain=CHAIN, lo=lo, hi=hi)
    if rows:
        return {p for p, _ in rows if p.startswith("gno.land/")}, max(int(m) for _, m in rows)
    # one NULL column comes back from psql --csv as a blank line, which csv reads as an empty row
    nxt = [r[0] for r in query("select min(height) from gno_tx where chain_id = :'chain' and height > :'hi'::bigint",
                               chain=CHAIN, hi=hi) if r and r[0]]
    return set(), int(nxt[0]) - 1 if nxt else lo


def usage(lo, hi):
    """Per realm between two heights: calls, users, user weight and the last height it was called.

    A caller is a user only if its account existed ESTABLISHED blocks before hi and it called realms
    in at least two namespaces (a farm cannot qualify on its own decoy realms). Its weight then grows
    with the days it was active, full from ACTIVE_DAYS on: a hundred accounts made in an afternoon
    weigh almost nothing, and keeping them busy for weeks costs time and stands out. Funding is not
    used: faucets, exchanges and treasuries fund thousands of unrelated real people (Jove, 2026-11-15).
    Ranking uses the summed weight; cards show the count of users with any weight."""
    rows = query("""
        with w as (select pkg_path, caller, height from gno_tx_msg
                   where chain_id = :'chain' and kind = 'call' and success
                     and height > :'lo'::bigint and height <= :'hi'::bigint and pkg_path like 'gno.land/r/%'),
             who as (select caller, count(distinct split_part(pkg_path, '/', 3)) spaces,
                            count(distinct height / :'day'::bigint) days from w group by 1),
             born as (select caller, min(height) h from gno_tx_msg
                      where chain_id = :'chain' and caller in (select caller from who) group by 1),
             wt as (select who.caller, case when who.spaces >= 2 and born.h < :'hi'::bigint - :'est'::bigint
                                            then least(1.0, who.days / :'full'::numeric) else 0 end weight
                    from who join born using (caller)),
             pc as (select pkg_path, caller, count(*) n, max(height) h from w group by 1, 2)
        select pc.pkg_path, sum(pc.n), count(*) filter (where wt.weight > 0), round(sum(wt.weight), 3), max(pc.h)
        from pc join wt using (caller)
        group by 1""",
        chain=CHAIN, lo=int(lo), hi=int(hi), est=ESTABLISHED, day=BLOCKS_PER_DAY, full=ACTIVE_DAYS)
    return ({p: int(c) for p, c, _, _, _ in rows}, {p: int(u) for p, _, u, _, _ in rows},
            {p: float(x) for p, _, _, x, _ in rows}, {p: int(h) for p, _, _, _, h in rows})


def coverage():
    """(lo, hi): the heights whose coins are recorded (2026-11-15: a refill is filling history in).
    Outside it a NULL coins means "not read yet", not "sent nothing", so nothing about coins is
    shown or summed there."""
    rows = query("select lo, hi from indexer_coverage(:'chain', 'gno_msg_coins')", chain=CHAIN)
    if not rows or not rows[0][0]:
        return None
    return int(rows[0][0]), int(rows[0][1])


def gnot_in(lo, hi):
    """ugnot sent to each realm through successful calls between two heights, as Python ints (sums
    can pass 2^53, so they never travel as JSON numbers). Only call with heights inside coverage()."""
    rows = query("""select pkg_path, sum(ugnot) from gno_tx_msg
                    where chain_id = :'chain' and kind = 'call' and success and ugnot is not null
                      and height > :'lo'::bigint and height <= :'hi'::bigint and pkg_path like 'gno.land/r/%'
                    group by 1""", chain=CHAIN, lo=int(lo), hi=int(hi))
    return {p: int(u) for p, u in rows}


def coins(lo, hi):
    """(hash, coins) of first messages between two heights, for transactions already copied. By
    transaction height, like touched_since: gno_tx_msg has no height index."""
    return query("""select m.tx_hash, coalesce(m.coins, '') from gno_tx t
                    join gno_tx_msg m on m.chain_id = t.chain_id and m.tx_hash = t.hash and m.msg_index = 0
                    where t.chain_id = :'chain' and t.height > :'lo'::bigint and t.height <= :'hi'::bigint""",
                 chain=CHAIN, lo=int(lo), hi=int(hi))


def tokens(lo, hi):
    """GRC20 tokens with holders, supply (sum of balances), top holders, and transfers between two
    heights. Amounts are raw integers as text (they pass 2^53); decimals say where the point goes.
    Token coverage (indexer_coverage 'gno_grc20') must span the window, or moves are left out."""
    rows = query("""
        with b as (select token, count(*) filter (where balance > 0) holders, coalesce(sum(balance), 0) supply
                   from gno_grc20_balance where chain_id = :'chain' group by 1),
             t as (select token, count(*) n, coalesce(sum(amount), 0) moved from gno_grc20_transfer
                   where chain_id = :'chain' and height > :'lo'::bigint and height <= :'hi'::bigint group by 1),
             top as (select token, string_agg(address || '=' || balance::text, ' ' order by balance desc) list
                     from (select token, address, balance, row_number() over (partition by token order by balance desc) r
                           from gno_grc20_balance where chain_id = :'chain' and balance > 0) x
                     where r <= :'top'::int group by 1)
        select k.token, k.realm, k.name, k.symbol, k.decimals, k.created_height,
               coalesce(b.holders, 0), coalesce(b.supply, 0)::text, coalesce(t.n, 0), coalesce(t.moved, 0)::text, coalesce(top.list, '')
        from gno_grc20_token k left join b using (token) left join t using (token) left join top using (token)
        where k.chain_id = :'chain'""", chain=CHAIN, lo=int(lo), hi=int(hi), top=TOP_HOLDERS)
    out = []
    for tok, realm, name, sym, dec, born, holders, supply, n, moved, top in rows:
        tops = [(a, int(v)) for a, _, v in (x.partition("=") for x in top.split()) if v.isdigit()]
        out.append({"token": tok, "realm": realm, "name": name, "symbol": sym, "decimals": int(dec or 0),
                    "created": int(born or 0), "holders": int(holders), "supply": int(supply), "moves": int(n),
                    "moved": int(moved), "top": tops})
    return out


def token_coverage():
    rows = query("select lo, hi from indexer_coverage(:'chain', 'gno_grc20')", chain=CHAIN)
    return (int(rows[0][0]), int(rows[0][1])) if rows and rows[0][0] != "" else None


def accounts():
    """Every account that ever sent a message: (address, messages, first height, last height, its
    five most used packages)."""
    rows = query("""
        with c as (select caller, pkg_path, count(*) n, min(height) f, max(height) l from gno_tx_msg
                   where chain_id = :'chain' and caller <> '' group by 1, 2)
        select caller, sum(n), min(f), max(l), array_to_string((array_agg(pkg_path order by n desc))[1:5], ' ')
        from c group by 1""", chain=CHAIN)
    return [(a, int(n), int(f), int(l), [p for p in top.split() if p]) for a, n, f, l, top in rows]


def names():
    """address -> [(kind, name)] from successful registrations, oldest first. Names are chain input."""
    out = {}
    for (pkg, func), kind in NAME_FUNCS.items():
        for addr, name in query("""select caller, args[1] from gno_tx_msg
                                   where chain_id = :'chain' and success and pkg_path = :'pkg' and func = :'func'
                                   order by height""", chain=CHAIN, pkg=pkg, func=func):
            if name:
                out.setdefault(addr, []).append((kind, name[:64]))
    return out


def txs(lo, hi):
    """Transactions between two heights with their first message: (hash, height, time, ok, caller, kind, pkg, func, coins)."""
    rows = query("""select t.hash, t.height, t.time, t.success, coalesce(m.caller, ''), coalesce(m.kind, ''),
                           coalesce(m.pkg_path, ''), coalesce(m.func, ''), coalesce(m.coins, '')
                    from gno_tx t left join gno_tx_msg m on m.chain_id = t.chain_id and m.tx_hash = t.hash and m.msg_index = 0
                    where t.chain_id = :'chain' and t.height > :'lo'::bigint and t.height <= :'hi'::bigint""",
                 chain=CHAIN, lo=int(lo), hi=int(hi))
    return [(h, int(ht), t, ok == "t", ca, k, p, f, c) for h, ht, t, ok, ca, k, p, f, c in rows]


def top_height():
    return int(query("select coalesce(max(height), 0) from gno_tx where chain_id = :'chain'", chain=CHAIN)[0][0])
