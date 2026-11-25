#!/bin/sh
# Smoke check of a running site: pages, headers, hostile input, rate limit.
#   tests/smoke.sh [base url]   (default http://127.0.0.1:8961, the dev server)
set -u
B=${1:-http://127.0.0.1:8961}; fail=0
ok() { echo "ok   $1"; }; bad() { echo "FAIL $1"; fail=1; }
code() { curl -s -o /dev/null -w '%{http_code}' "$B$1"; }
[ "$(code /)" = 200 ] && ok "home" || bad "home"
[ "$(code /app.js)" = 200 ] && ok "app" || bad "app"
curl -s "$B/robots.txt" | grep -q "Disallow: /api/" && ok "robots.txt" || bad "robots.txt"
curl -s -D - -o /dev/null "$B/" | grep -qi "img-src 'self';" && ok "csp" || bad "csp"
# scripts come from this site only: the policy says so, and every script the page names is here
curl -s -D - -o /dev/null "$B/" | grep -qi "script-src 'self';" && ok "csp scripts" || bad "csp scripts"
s=$(curl -s "$B/" | grep -o '<script[^>]* src="[^"]*"' | sed 's/.* src="//; s/"$//'); miss=""
for p in $s; do case "$p" in /*) [ "$(code "$p")" = 200 ] || miss="$miss $p" ;; *) miss="$miss $p" ;; esac; done
[ -n "$s" ] && [ -z "$miss" ] && ok "scripts served here" || bad "scripts served here:$miss"
curl -s "$B/api/search?q=board" | grep -q '"url"' && ok "search finds" || bad "search finds"
curl -s "$B/api/search?q=%3Cimg%20onerror%3Dx%3E%22%20OR%20NEAR(" | grep -q '"results"' && ok "hostile query" || bad "hostile query"
[ "$(code '/api/page?u=javascript:alert(1)')" = 400 ] && ok "bad page address" || bad "bad page address"
[ "$(code '/api/search?n=abc&q=x')" = 200 ] && ok "bad n" || bad "bad n"
curl -s "$B/" | grep -q 'og:image" content="http' && ok "preview card" || bad "preview card"
[ "$(code /api)" = 200 ] && ok "api docs" || bad "api docs"
curl -s -D - -o /dev/null "$B/api/stats" | grep -qi "access-control-allow-origin: \*" && ok "open api" || bad "open api"
[ "$(code '/api/search?q=x&net=moon')" = 400 ] && ok "unknown network" || bad "unknown network"
curl -s "$B/api/stats" | grep -q '"nets": \["main", "test"\]' && ok "testnet on" || bad "testnet on"
curl -s "$B/api/search?q=board&net=test" | grep -q '"results"' && ok "testnet search" || bad "testnet search"
h=$(curl -s "$B/api/search?q=avl" | grep -o '"kind": "pkg"' | head -1); [ -n "$h" ] && ok "packages" || bad "packages"
curl -s "$B/api/search?q=validator" | grep -q '"kind": "user"' && ok "accounts" || bad "accounts"
curl -s "$B/api/search?q=gno.land/r/gov/dao/&n=1" | grep -q '"url": "gno.land/r/gov/dao"' && ok "exact path first" || bad "exact path first"
curl -s "$B/api/search?q=g1qqqqr2u6rfydqjkm56a7s54397vfp4kmvnuwev" | grep -q '"url": "user:g1qqqqr2u6rfydqjkm56a7s54397vfp4kmvnuwev"' && ok "any address" || bad "any address"
curl -s "$B/api/search?q=GNS&n=1" | grep -q '"kind": "token"' && ok "token by symbol" || bad "token by symbol"
curl -s "$B/api/importers?u=gno.land/p/nt/avl/v0" | grep -q '"importers"' && ok "importers" || bad "importers"
curl -s "$B/api/search?q=tree&kind=pkg" | grep -q '"kind": "pkg"' && ok "kind filter" || bad "kind filter"
curl -s -X POST "$B/mcp" -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | grep -q '"find_package"' && ok "mcp" || bad "mcp"
# in parallel, so the result does not depend on how fast one search is
seq 1 60 | xargs -P 20 -I{} curl -s -o /dev/null -w '%{http_code}\n' "$B/api/search?q=r{}" | grep -q 429 && ok "rate limit" || bad "rate limit"
exit $fail
