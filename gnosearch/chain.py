"""Read-only ABCI queries on a gno.land node.

Every request from one process is paced, so a crawl never crowds the node it reads from
(GNOSEARCH_NODE_GAP seconds apart, 0.5 by default) and callers run one at a time.
"""
import base64
import json
import os
import threading
import time
import urllib.parse
import urllib.request

NODE = os.environ.get("GNOSEARCH_NODE", "http://127.0.0.1:26657")  # a gno.land node's RPC
GAP = float(os.environ.get("GNOSEARCH_NODE_GAP", "0.5"))
# vm/qpaths lists 1,000 paths unless the query asks for more, and never more than 10,000, with no paging
# (2026-11-25: the testnet passed 900 realms while every crawl took the first 1,000 as the whole chain)
PATHS_MAX = 10_000

_pace, _last = threading.Lock(), [0.0]


class QueryError(Exception):
    pass


def _get(url, timeout):
    with _pace:  # one request at a time per process, GAP apart
        wait = _last[0] + GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.load(r)
        finally:
            _last[0] = time.monotonic()


def abci(path, data, timeout=20, node=None):
    q = urllib.parse.urlencode({"path": f'"{path}"', "data": "0x" + data.encode().hex()})
    resp = _get(f"{node or NODE}/abci_query?{q}", timeout)["result"]["response"]["ResponseBase"]
    if resp.get("Error"):
        raise QueryError((resp.get("Log") or "query error")[:300])
    return base64.b64decode(resp.get("Data") or b"").decode("utf-8", "replace")


def paths(prefix):
    """All package paths under a prefix, e.g. 'gno.land/r/'. A list as long as the node's cap may be cut
    short, and a crawl that trusts it drops every path past the cut, so that is an error."""
    out = [p.strip() for p in abci(f"vm/qpaths?limit={PATHS_MAX}", prefix).splitlines() if p.strip()]
    if len(out) >= PATHS_MAX:
        raise QueryError(f"the node listed {len(out)} paths under {prefix}, its most; the list may be cut")
    return out


def render(realm, sub="", node=None):
    """A realm page as markdown. realm = 'gno.land/r/x/y', sub = the part after ':'."""
    return abci("vm/qrender", f"{realm}:{sub}", node=node)


def funcs(realm, node=None):
    """Exported function names of a realm, for realms without a page."""
    import re
    return sorted(set(re.findall(r'"FuncName":"([A-Z]\w{0,63})"', abci("vm/qfuncs", realm, node=node))))


def files(pkg):
    """File names of a package or realm."""
    return [f.strip() for f in abci("vm/qfile", pkg).splitlines() if f.strip()]


def file(pkg, name):
    return abci("vm/qfile", f"{pkg}/{name}")


def account(addr, node=None):
    """An account's state from the node (balance, vesting, signed count), or None if it has none.
    Most mainnet accounts exist only from the genesis airdrop (3.26 million) and never transacted,
    so they are looked up live instead of indexed (2026-11-15)."""
    raw = abci("auth/accounts/" + addr, "", node=node)
    if not raw.strip() or raw.strip() == "null":
        return None
    a = json.loads(raw).get("BaseAccount") or {}
    return a if a.get("address") == addr else None


def height():
    return int(_get(f"{NODE}/status", 10)["result"]["sync_info"]["latest_block_height"])
