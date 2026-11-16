# AGENTS.md

How the search fits together, the rules that keep it correct, and the traps already hit.
Update this file in the same commit as any change that adds a rule, a moving part or a trap.

## Moving parts

```
gno.land node (GNOSEARCH_NODE)  --ABCI, paced--> crawl.py --put_realm--> GNOSEARCH_DB, GNOSEARCH_DB_TEST
tx indexer (INDEXER_RO_URL)     --psql, ro-----> indexer.py                 |      (SQLite, FTS5)
                                                                                         v
browser, other apps <-- web/ (index.html, app.js, api.html) <-- server.py (/api/search, /api/page, /api/stats)
```

One crawler pair (`crawl follow`, always on, and `crawl full`, daily) per network, each with its own
`GNOSEARCH_DB`, `GNOSEARCH_NODE`, `GNOSEARCH_CHAIN`; one web server reads both (`GNOSEARCH_DB` and `GNOSEARCH_DB_TEST`; `?net=test` on every API route).

Entry kinds (`pages.kind`): `realm` (a rendered page, live from the node when read), `pkg` (a package:
readme, doc comments and source), `code` (a realm's source, url `...$source`), `user` (an account:
names, activity, most used realms, url `user:g1...`). Transactions are not pages: table `tx`, found
by hash prefix (`store.find_tx`), shown from `store.page("tx:...")`.

- `gnosearch/chain.py`: ABCI queries (`vm/qpaths`, `vm/qrender`, `vm/qfuncs`) and `/status`. Every
  request in a process waits `GNOSEARCH_NODE_GAP` (0.5 s) after the previous one, one at a time.
- `gnosearch/indexer.py`: chain history. `touched_since(h)` tells follow which realms changed;
  `usage(lo, hi)` gives calls, users and last use per realm for the last ~30 days; `accounts()`,
  `names()` (usernames from `r/sys/namereg/v0` Register, validator names from `r/gnops/valopers`
  Register) and `txs(lo, hi)` feed the account and transaction entries.
- `gnosearch/crawl.py`: `full` (daily, off-peak) crawls every realm, reads
  source not read yet (at most `FILE_BUDGET` files a run; deployed code never changes, so each
  package is read once), rebuilds accounts, copies new transactions and refreshes the signals.
  `follow` (always on) re-crawls touched realms, reads the source of new deploys and copies new
  transactions. Sequential.
- `gnosearch/store.py`: the index. `pages` holds each page with its kind, `fts` the full-text
  index, `realms` one row of signals per entry (users, inbound, calls, imports, last_height,
  code_done), `imports` who imports whom, `tx` transactions, `meta` the indexed heights
  (`height`, `tx_height`).
- `mcp/gnoland_search_mcp.py`: the MCP server for agents (Gnosh or any MCP client), one
  standalone stdlib file over stdio that calls the public API (`GNOLAND_SEARCH_URL`); tools
  `search`, `find_package`, `read_page`, `who_imports`, `stats`. Read only. Every result starts
  with the untrusted-data notice and is capped (`MAX_TEXT`): chain text may try to instruct the
  agent. Its tools and the API routes change together (`/api/importers`, `kind=` were added for it).
  Tested in `tests/test_mcp.py`; checked against Gnosh's own client with `gnosh mcp get`.
- `web/`: the search page and the API page (`api.html`, documenting the same routes as
  `server.py`: change both together). `base.css` holds the tokens both use; `theme.js` (a classic
  script in head, so no flash) applies the day/night choice. Class prefixes: `sr-` search bar,
  `rs-` results, `rd-` reader, `th-` day/night button, `hint` resting lines, `ad-` API page. Colours are tokens on `:root`; the dark set is written twice,
  under `prefers-color-scheme` and under `[data-theme="dark"]` (the button's choice, kept in
  localStorage), and both must change together. The public text names gno.land, never a brand.

## Indexer dependency

`INDEXER_RO_URL` is a read-only Postgres login (SELECT only, short statement timeout, low
connection limit) to a gno.land transaction indexer. Tables and columns read (also in
`indexer.COLUMNS`, checked by `tests/test_indexer.py`):

- `gno_tx_msg`: `chain_id`, `tx_hash`, `msg_index`, `height`, `success`, `kind` (`call`, `addpkg`,
  `send`, `run`), `pkg_path`, `func`, `caller`, `args` (only `args[1]` of the two name registrations),
  `coins` (what a message sends, chain text like `3540000ugnot`, comma-joined; NULL when nothing)
  and `ugnot` (its ugnot part as numeric)
- `indexer_coverage(chain, 'gno_msg_coins')`: the heights whose coins are recorded
- `gno_tx`: `chain_id`, `hash`, `height`, `time`, `success`
- `gno_grc20_token` (`token`, `realm`, `name`, `symbol`, `decimals`, `created_height`),
  `gno_grc20_balance` (`token`, `address`, `balance`), `gno_grc20_transfer` (`height`, `token`,
  `from_addr`, `to_addr`, `amount`), and `indexer_coverage(chain, 'gno_grc20')`

Rules: read only; batched by height (follow reads at most `FOLLOW_SPAN` blocks per step; usage runs
once a day off-peak); never query it per search request; never write there. The index is fully
rebuildable from the indexer plus a node (`crawl full`).

## Rules that keep it correct

- This repository describes the search and nothing about where or how one instance runs: no hosts,
  paths, ports, logins, machines, neighbours on those machines, or incidents there. Code comments,
  docs and commit messages say what the code does and why in general terms; notes about a particular
  deployment stay with that deployment (2026-11-16). Chain vocabulary (validators, valopers, tokens)
  is fine: it is what the search indexes.

- Chain text is attacker input. It is cleaned (`store.clean`: control and bidi characters
  removed, which also keeps the snippet markers `\x02`/`\x03` unforgeable) and capped at index time
  (`TITLE_MAX`, `BODY_MAX`, `MD_MAX`). In the browser it reaches the page only as text nodes
  (results, highlights built as `mark` elements around text) or through DOMPurify plus `tame()`
  (pages). Never `innerHTML` a chain string.
- Pages never load images or embeds from chain links: CSP `img-src 'none'`, and `tame()` turns
  images into links. External links show their host and carry `nofollow noopener noreferrer`.
- The search box reaches SQL only through `fts_query`: word characters only, each word quoted,
  at most `WORDS_MAX` words of `WORD_MAX` characters, prefix match only from `PREFIX_MIN` letters.
- Ranking will be gamed, so (2026-11-15):
  - users: a caller counts only if its account is `ESTABLISHED` blocks old and it called realms in
    two or more namespaces; it then weighs `days/ACTIVE_DAYS` (capped at 1) by the days it was
    active (height buckets of `BLOCKS_PER_DAY`). Ranking uses the summed weight (`realms.uweight`);
    cards show the plain count (`realms.users`). Funding source is NOT used: faucets, exchanges and
    treasuries fund thousands of unrelated real people .
  - inbound links and imports count distinct OTHER namespaces, so one owner's farm of realms is one
    vote and an owner's own realms none.
  - raw calls count little; one realm shows at most `PER_REALM` results and one namespace `PER_NS`.
  - `crawl.jumps` logs `rank jump:` lines in the full crawl's output when a realm's weight grows 5x in a day: read
    them before trusting a sudden climb.
- A query that is a known package path (with or without `https://`, `gno.land`, a leading or
  trailing slash, a `:sub-page` or `$source`) returns that exact page first, then up to `OWN_PAGES`
  of the entry's own pages and its source, then up to `CHILDREN` sub-paths by use (`store.find_path`),
  then the text results without repeats (2026-11-15). Tested in `ExactPath`.
- Tokens (2026-11-15): one `token` page per GRC20 token (`token:<token id>`), grouped under its
  realm so it shares the realm's signals and caps. Supply is the sum of balances; holders and top
  holders are shown, never ranked (dust airdrops are cheap). Transfers over the usage window show
  only when `gno_grc20` coverage spans it. A query that is exactly a token's symbol or name puts
  that token first (`store.find_token`). Amounts are exact (`store.amount`, Decimal) with the
  token's own decimals: wugnot has 0, so 1 wugnot is 1 ugnot, as the chain defines it.
- Accounts: the index holds active accounts (those that sent a transaction; `stats.users`, shown as
  "active accounts"). Most addresses exist only from genesis balances (3,262,486 on mainnet), so any
  full `g1` address searched gets a result card even if unindexed, and an account page reads its
  balance, vesting and signed count live from the node (`chain.account`, `store.account_md`) under
  the live-render cap (2026-11-15).
- A new indexed field must be added to the schema (`SCHEMA` and `ADDED`, so existing files gain
  it), the extractor (`crawl.py`), the ranker (`store.search`) and the result card (`app.js`
  `meta()` plus the `.rs-card` rows) together. A new kind also needs `KIND_WEIGHT` and, if it is
  not rendered live, `store.page` and `server.INDEX_URL`.
- Packages rank by distinct importers, so one package importing another a hundred times is one
  vote; source is also only counted for paths the node lists.
- The count line is empty when the box is empty (2026-11-15); the network and block show
  only beside results, and the resting hint carries the network switch.
- Public text says gno.land and never a brand name (2026-11-15).
- Coins are read only inside `indexer_coverage(..., 'gno_msg_coins')`: outside it a NULL means "not
  recorded yet", never "sent nothing". GNOT in per realm is shown only when coverage spans the whole
  usage window; a transaction's amount only when its height is inside coverage (`meta.coins_lo/hi`).
  Only successful calls count. Sums are Python ints and leave the server as decimal strings
  (`store.gnot`), never JSON numbers: they can pass 2^53. GNOT in is shown, not ranked: money sent
  to a realm can be sent back by the same hand.
- The result card is one fixed shape (title, path, two snippet lines, meta; 128 px). The count
  line and list are laid out from the first paint. Fix shape problems on `.rs-card`, never per card.
- `GNOSEARCH_DB` has no default. Every entry point (services, dev scripts, tests) sets it.
- Text: no middle dot separator and no long dash anywhere; grep before every commit.

## Working

- Before a commit: `python3 -m unittest discover -s tests`, `tests/smoke.sh <base url>`, and after
  layout changes `node --experimental-websocket dev/shot.mjs --url <base url>` (phone 390x844,
  320x640, landscape, tablet, desktop, light and dark; it fails loudly if the bar, count line or
  list move) and look at the shots.

## Traps

- 2026-11-16: the resting lines overflowed 360-375px phones with an ellipsis; they now scale one size
  together (`--hint-size`), and dev/shot.mjs fails on any cut text there, with 375 and 360 wide phones added.
- 2026-11-16: `find_token` ran on every search with no index on `pages.kind` (13 ms a search, enough to
  stop the serial rate-limit smoke test tripping); index `pages_kind`, and the smoke test now fires in parallel.

- 2026-11-15: a token id is `<realm>.<SYMBOL>.<serial>` and `gno.land` has a dot of its own, so the
  realm is everything but the last two dot parts, never the part before the first dot.

- 2026-11-15: a reload keeps `history.state`; the app replaces it with null on load, or back would
  land on a stale results entry. Navigation (start, results, page) lives in the history: no Close button.

- 2026-11-15: coins coverage from genesis has lo = 0, which a truthiness check read as "no
  coverage" and hid every amount; compare with None.
- 2026-11-15: an owners' directory realm (title, description, keywords) was built and dropped
  : keywords are spam bait, and the one thing a realm adds, proof of who wrote a listing, an
  off-chain signed message gives too. Search reads only what the chain itself records; no owner-written
  text, no keywords, no realm of our own.
- 2026-11-15: the day/night button sits in the corner, and the open bar (min(680px, 100vw - 32px),
  centred) reaches it below 820px wide; the shots now include a 768px tablet.
- Crawl one request at a time, paced (`GNOSEARCH_NODE_GAP`), so a crawl never crowds the node it
  reads from.
- 2026-11-15: the gno tx-indexer's GraphQL answers at most 10,000 elements per query and has no `le`
  filter. No longer used; history comes from the Postgres indexer.
- 2026-11-15: `curl -I` sends HEAD, which the server does not answer (501); check headers with GET.
- 2026-11-15: clearing the box left the block number under it; `clear()` must empty every field of the count line, and dev/shot.mjs now fails if anything is left.
- 2026-11-15: in systemd an `EnvironmentFile` overrides `-E`; scripts set every path themselves and
  print them.
