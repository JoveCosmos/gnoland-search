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
  `paths()` asks for `PATHS_MAX` (the node's most) and fails on a list that long, which may be cut.
- `gnosearch/indexer.py`: chain history. `touched_since(h)` tells follow which realms and packages changed,
  reading by transaction height (`gno_tx` has a height index, `gno_tx_msg` has none);
  `usage(lo, hi)` gives calls, users and last use per realm for the last ~30 days; `accounts()`,
  `names()` (usernames from `r/sys/namereg/v0` Register, validator names from `r/gnops/valopers`
  Register) and `txs(lo, hi)` feed the account and transaction entries.
- `gnosearch/crawl.py`: `full` (daily, off-peak) crawls every realm, reads
  source not read yet (at most `FILE_BUDGET` files a run; deployed code never changes, so each
  package is read once), rebuilds accounts, copies new transactions and refreshes the signals.
  `follow` (always on) re-crawls touched realms, reads the source of new deploys (realms and packages)
  and copies new transactions. Sequential. For a health check, follow writes `meta.follow_at` (its last
  good step) and `follow_behind` (blocks between the indexer's newest transaction and the index) once a
  minute, and full writes `full_at` when it finishes.
- `gnosearch/store.py`: the index. `pages` holds each page with its kind, `fts` the full-text
  index, `realms` one row of signals per entry (users, inbound, calls, imports, last_height,
  code_done), `imports` who imports whom, `tx` transactions, `meta` the indexed heights
  (`height`, `tx_height`).
- `mcp/gnoland_search_mcp.py`: the MCP server for agents (Gnosh or any MCP client), one
  standalone stdlib file over stdio that calls the public API (`GNOLAND_SEARCH_URL`); tools
  `search`, `find_package`, `read_page`, `who_imports`, `stats`. Read only. Every result starts
  with the untrusted-data notice and is capped (`MAX_TEXT`): chain text may try to instruct the
  agent. Its tools and the API routes change together (`/api/importers`, `kind=` were added for it).
  The server also answers MCP at `POST /mcp` (no sessions: one request, one answer; GET is 405), loading
  this same file, so the two never differ. Its tools call the API on 127.0.0.1 naming the visitor, so each
  agent has its own rate limits; a foreign `Origin` is refused (a web page may not drive an agent's tools).
  Tested in `tests/test_mcp.py` and the smoke check; checked against Gnosh's own client by command and by URL.
- `web/`: the search page and the developers page (`api.html`: two copy-ready examples, for apps and
  for agents, filled with the site's own address by `api.js` so the repository names none, then a folded
  full reference; it documents the same routes as `server.py`: change both together). `base.css` holds the tokens both use; `theme.js` (a classic
  script in head, so no flash) applies the day/night choice. The reader turns a page's markdown into HTML with marked
  and cleans it with DOMPurify, unchanged copies in `vendor/` (see the rules). Class prefixes: `sr-` search bar,
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
- Pages never load images or embeds from chain links: CSP `img-src 'self'` (only the site's own icon),
  and `tame()` turns images into links. External links show their host and carry `nofollow noopener noreferrer`.
- Scripts load from this site only (CSP `script-src 'self'`, 2026-11-25). `web/vendor/` holds marked and DOMPurify
  byte for byte as their npm releases ship them, each with its license; never edit them. The file name carries the
  version, so browsers keep them a year: an upgrade is a new file, its `server.STATIC` line and its script tag,
  changed together (`Scripts` in `tests/test_core.py` checks they match). Before switching, check the new copy
  against the npm registry's integrity hash and compare what the old and new copies make of every indexed page
  (DOMPurify 3.1.6 to 3.5.0 changed only the order of attributes).
- Only the node's own answer drops indexed text: a realm whose crawl hits a failed request keeps its
  pages (`FAILS_MAX` failures in a row stop a full crawl), a source read that fails leaves the entry
  unread for a later run, and a full crawl drops no path when more than `DROP_MAX` of them are missing
  from the node's list (`crawl.stale`). Deployed paths never leave the chain (2026-11-25).
- Snippets are built only for the results returned (`store._snippets`), after ranking and caps.
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
- Paging (2026-11-17): `store.search(..., offset)` returns a slice of one ranking (`_ranked`) and
  `more`; pages join without gaps or repeats because ranking and caps are deterministic and applied in
  order, at most `MAX_RESULTS` (100) deep. The app's "More results" link appends the next 20 below.
- The resting screen has three lines: what is indexed, the network switch, and links to the API page, its
  agent (MCP) section and the source repository (2026-11-17).
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

- 2026-11-25: `vm/qpaths` answers at most 1,000 paths unless the query path asks (`vm/qpaths?limit=N`, at
  most 10,000, no paging). The crawl took the first 1,000 as the whole chain and would have dropped every
  testnet realm past them; the testnet had 929.
- 2026-11-25: a source read that failed marked the entry read, so a deploy that was not readable yet
  (parked until approved) never got its source: 8 gnoswap realms on mainnet, 64 on testnet. Full now
  re-reads entries marked read without a source page (`store.reread_missing_source`).
- 2026-11-25: follow moved only to the last transaction it saw, so a span of `FOLLOW_SPAN` blocks with no
  transaction stopped it for good (the testnet had eleven); `touched_since` now steps to the block
  before the next transaction. It also polled `gno_tx_msg` by height, which has no height index: a
  full table scan every few seconds per network. Read windows by `gno_tx` height and join.
- 2026-11-25: `snippet()` in the ranking query ran for every match before `limit`, so "the" cost half a
  second to a second; ranking now reads bm25 only and `_snippets` builds the few that are shown.
- 2026-11-25: a crawl that hit a timeout saved what it had, so one node hiccup emptied a realm's pages.

- 2026-11-17: `.hint > span + span` (the second resting line's flex row) also matched the new third
  line and spread "API, MCP, GitHub" apart; the third line has its own `.hint > .hint-links` rule.

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
