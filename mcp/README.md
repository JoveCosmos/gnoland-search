# gno.land search for AI agents (MCP)

`gnoland_search_mcp.py` gives an AI agent five read-only tools over a gno.land search server:

| Tool | What it does |
|---|---|
| `search` | Search everything on gno.land by what it says: realm pages, package code, accounts, tokens, transactions |
| `find_package` | Find gno packages for a purpose, the most imported first, to reuse instead of rewrite |
| `read_page` | Read a realm page (live), a package's readme and source, an account, a token or a transaction |
| `who_imports` | The packages and realms that import a package, most used first |
| `stats` | What is indexed, up to which block |

One file, Python 3.10+ standard library only. It signs and sends nothing. Everything it returns was
written on chain by anyone and is marked as data, not instructions.

## Set up

The simplest way: a search server answers MCP itself at `/mcp`, so the agent needs only its address.
Gnosh (`~/.config/gnosh/mcp.json`) and most other MCP clients take this shape:

```json
{
  "mcpServers": {
    "gnoland-search": { "type": "http", "url": "https://<search server>/mcp" }
  }
}
```

Or run the tools on your own machine: download `gnoland_search_mcp.py` and point it at a search
server with `GNOLAND_SEARCH_URL`:

```json
{
  "mcpServers": {
    "gnoland-search": {
      "command": "python3",
      "args": ["/path/to/gnoland_search_mcp.py"],
      "env": { "GNOLAND_SEARCH_URL": "https://<search server>" }
    }
  }
}
```

With Gnosh, `gnosh mcp get gnoland-search` then says whether it connects. Each tool takes
`net: "test"` for the testnet.

## Limits

The server's rate limits apply (about 8 searches a second); above them a tool answers "slow down"
and the agent can retry after a few seconds.
