# Search gno.land

A search engine for gno.land, mainnet and testnet: every realm page, package and its source, account
and transaction, ranked by how the chain is really used. Results arrive as you type, pages open in
place, and there is a free open API. How it works and the rules that keep it correct: AGENTS.md.

## Layout

```
gnosearch/     crawler, index, indexer reader, HTTP server (Python 3, standard library only)
web/           the app (index.html, app.js), the API page (api.html), shared base.css and theme.js
web/vendor/    marked and DOMPurify, unchanged copies under their own licenses
tests/         unit tests (python3 -m unittest discover -s tests) and smoke.sh for a running site
dev/shot.mjs   screenshots and a layout-shift check in headless Chrome
mcp/           gno.land search as tools for AI agents (MCP): see mcp/README.md
```

## Run

You need a gno.land node's RPC and, for history and usage, read-only access to a Postgres
transaction indexer with the tables listed in AGENTS.md.

```
export GNOSEARCH_DB=$PWD/data/index.db            # the index file (required, no default)
export GNOSEARCH_NODE=http://127.0.0.1:26657      # a gno.land node
export INDEXER_RO_URL=postgres://...              # read-only indexer login
python3 -m gnosearch.crawl full                   # build the index (daily)
python3 -m gnosearch.crawl follow &               # keep it fresh within seconds
python3 -m gnosearch.server                       # http://127.0.0.1:8960
```

For a testnet index next to it, run a second crawler pair with its own `GNOSEARCH_DB`,
`GNOSEARCH_NODE` and `GNOSEARCH_CHAIN=onyx-1`, and give the server `GNOSEARCH_DB_TEST` and
`GNOSEARCH_NODE_TEST`. Requests to the node are paced (`GNOSEARCH_NODE_GAP`, 0.5 s) so a crawl
never crowds the node it reads from.

## API and agents

`GET /api/search?q=...&net=main|test`, `GET /api/page?u=...`, `GET /api/stats`: see `/api` on a
running server. Agents such as Gnosh, and any other MCP client, can use it through `mcp/`.

## License

GNO Network General Public License, version 6 or later (a fork of the GNU AGPL 3): see LICENSE.md.
If you run a modified version as a service, its users are entitled to its source.
`web/vendor/` holds marked (MIT) and DOMPurify (Apache 2.0 or MPL 2.0), each under its own license, kept next to it.
