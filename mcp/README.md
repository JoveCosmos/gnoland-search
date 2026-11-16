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

Download the file, then point your agent at it, with `GNOLAND_SEARCH_URL` set to a search server.

Gnosh (`~/.config/gnosh/mcp.json`) and most other MCP clients take the same shape:

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
