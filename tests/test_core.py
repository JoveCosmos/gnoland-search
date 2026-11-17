"""Core checks without a browser or network: query parsing, cleaning, extraction, ranking.
Run: python3 -m unittest discover -s tests"""
import os
import tempfile
import unittest

os.environ["GNOSEARCH_DB"] = os.path.join(tempfile.mkdtemp(prefix="gnosearch-test-"), "index.db")

from gnosearch import crawl, store  # noqa: E402


class Query(unittest.TestCase):
    def test_words_are_quoted_and_last_is_prefix(self):
        self.assertEqual(store.fts_query("Board game"), '"board" "game"*')

    def test_operators_are_plain_text(self):
        self.assertEqual(store.fts_query('a" OR title:x NEAR(y) *'), '"a" "or" "title" "x" "near" "y"')

    def test_short_last_word_is_not_a_prefix(self):
        self.assertEqual(store.fts_query("b"), '"b"')

    def test_caps(self):
        fq = store.fts_query(" ".join(["w" * 100] * 50))
        self.assertEqual(fq.count('"') // 2, store.WORDS_MAX)
        self.assertNotIn("w" * (store.WORD_MAX + 1), fq)

    def test_nothing_to_search(self):
        self.assertIsNone(store.fts_query("<>!? ..."))


class Clean(unittest.TestCase):
    def test_markers_and_controls_removed(self):
        self.assertEqual(store.clean("a\x02b\x03c\x00‮d\n", 100), "abcd\n")

    def test_capped(self):
        self.assertEqual(len(store.clean("x" * 10_000, 160)), 160)


class Extract(unittest.TestCase):
    def test_split(self):
        self.assertEqual(crawl.split("/r/a/b:page/2"), ("gno.land/r/a/b", "page/2"))
        self.assertIsNone(crawl.split("/r/a/b$source"))

    def test_text_and_title(self):
        md = "# Hello *World*\n\n![x](https://e/x.png) [link words](https://e) `code`\n```go\nsecret\n```"
        self.assertEqual(crawl.title_of(md, "gno.land/r/a"), "Hello World")
        self.assertEqual(crawl.title_of("# memba_arcade_leaderboard_v1", "x"), "memba_arcade_leaderboard_v1")
        self.assertEqual(crawl.title_of("# _Big_ **news** [here](/r/x)", "x"), "Big news here")
        t = crawl.text_of(md)
        self.assertIn("link words", t)
        self.assertNotIn("secret", t)
        self.assertNotIn("x.png", t)


class Ranking(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c = store.db()
        for t in ("pages", "fts", "realms"):
            c.execute(f"delete from {t}")
        put = lambda r, title, body: store.put_realm(r, [(r, title, body, body)], 1, 1)
        put("gno.land/r/real/dex", "Dex", "swap tokens on a dex")
        put("gno.land/r/spam/dex", "dex dex dex swap swap", "dex swap dex swap dex swap dex swap")
        for i in range(8):
            put(f"gno.land/r/flood/dex{i}", "dex swap", "dex swap")
        store.put_realm("gno.land/r/big/site", [(f"gno.land/r/big/site:{i}", "swap page", "swap", "swap") for i in range(6)], 1, 1)
        store.set_signals(users={"gno.land/r/real/dex": 200}, uweight={"gno.land/r/real/dex": 150.0},
                          inbound={"gno.land/r/real/dex": 5}, calls={"gno.land/r/spam/dex": 100_000})

    def test_used_realm_beats_keyword_stuffing(self):
        self.assertEqual(store.search("dex swap")["results"][0]["realm"], "gno.land/r/real/dex")

    def test_one_namespace_cannot_fill_the_page(self):
        rs = store.search("dex swap", 50)["results"]
        self.assertLessEqual(sum(r["realm"].startswith("gno.land/r/flood/") for r in rs), store.PER_NS)

    def test_one_realm_at_most_three(self):
        rs = store.search("swap", 50)["results"]
        self.assertLessEqual(sum(r["realm"] == "gno.land/r/big/site" for r in rs), store.PER_REALM)

    def test_snippet_markers_only_from_us(self):
        store.put_realm("gno.land/r/evil/x", [("gno.land/r/evil/x", "evil", "zebra \x02<img src=x onerror=alert(1)>\x03", "")], 1, 1)
        s = store.search("zebra")["results"][0]["snippet"]
        self.assertEqual(s.count("\x02"), 1)  # only the match marker we added

    def test_unchanged_realm_is_not_rewritten(self):
        r = "gno.land/r/real/dex"
        before = store.db().execute("select rowid from fts where url=?", (r,)).fetchone()
        store.put_realm(r, [(r, "Dex", "swap tokens on a dex", "swap tokens on a dex")], 2, 2)
        self.assertEqual(before, store.db().execute("select rowid from fts where url=?", (r,)).fetchone())


class Kinds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.put_realm("gno.land/p/demo/tree", [("gno.land/p/demo/tree", "tree - balanced tree", "balanced tree code", "")], 1, 1, kind="pkg")
        store.put_realm("gno.land/p/spam/tree", [("gno.land/p/spam/tree", "tree tree tree", "balanced tree tree tree", "")], 1, 1, kind="pkg")
        for i in range(4):
            store.put_imports(f"gno.land/r/app{i}/x", ["gno.land/p/demo/tree"])
        store.set_signals(imports=store.import_counts())
        a = "g1" + "q" * 38
        store.put_realm("user:" + a, [("user:" + a, "alice", f"alice {a}", "# alice")], 1, 1, kind="user")
        store.put_txs([("ab" * 32, 7, "2026-11-15 00:00:00", True, a, "call", "gno.land/r/x/y", "Do<b>", "")])

    def test_imported_package_beats_stuffed_one(self):
        r = store.search("balanced tree")["results"][0]
        self.assertEqual((r["url"], r["kind"], r["imports"]), ("gno.land/p/demo/tree", "pkg", 4))

    def test_code_and_realm_pages_replace_separately(self):
        e = "gno.land/r/both/x"
        store.put_realm(e, [(e, "page", "words", "")], 1, 1)
        store.put_realm(e, [(e + "$source", "src", "code", "")], 1, 1, kind="code")
        store.put_realm(e, [(e, "page", "new words", "")], 2, 2)
        self.assertIsNotNone(store.page(e + "$source"))

    def test_user_by_name_and_address_prefix(self):
        self.assertEqual(store.search("alice")["results"][0]["kind"], "user")
        self.assertEqual(store.search("g1qqqq")["results"][0]["title"], "alice")

    def test_tx_by_hash_prefix_any_case(self):
        r = store.search("ABABABAB")["results"][0]
        self.assertEqual((r["kind"], r["url"]), ("tx", "tx:" + "AB" * 32))
        self.assertIn("/r/x/y.Do<b>", r["snippet"])  # raw here; the app writes it as text
        self.assertIn("Block 7", store.page(r["url"])["md"])

    def test_not_a_hash(self):
        self.assertEqual(store.find_tx("abc"), [])
        self.assertEqual(store.find_tx("zz" * 10), [])

    def test_no_testnet_without_its_file(self):
        self.assertEqual(store.nets(), ["main"])


class Urls(unittest.TestCase):
    def test_page_addresses(self):
        from gnosearch import server
        ok = ["gno.land/r/a/b", "gno.land/r/a/b:x/y", "gno.land/p/a/b", "gno.land/r/a/b$source", "user:g1" + "a" * 38, "tx:" + "A" * 64]
        bad = ["javascript:alert(1)", "gno.land/p/a/b:x", "user:g1<img>", "tx:zz", "gno.land/r/../../etc", "gno.land/x/a"]
        good = lambda u: bool(server.LIVE_URL.match(u) or server.INDEX_URL.match(u))
        self.assertTrue(all(good(u) for u in ok))
        self.assertEqual([u for u in bad if good(u)], ["gno.land/r/../../etc"])  # dots are path text to the node, never a file path


class Accounts(unittest.TestCase):
    def test_address_shape(self):
        self.assertTrue(store.ADDR.match("g1qqqqr2u6rfydqjkm56a7s54397vfp4kmvnuwev"))
        for bad in ["g1qqqq", "g1QQQQr2u6rfydqjkm56a7s54397vfp4kmvnuwev", "g1bbbbr2u6rfydqjkm56a7s54397vfp4kmvnuwev", "x1qqqqr2u6rfydqjkm56a7s54397vfp4kmvnuwev"]:
            self.assertIsNone(store.ADDR.match(bad), bad)  # b is not a bech32 character

    def test_genesis_account_page(self):
        a = "g1qqqqr2u6rfydqjkm56a7s54397vfp4kmvnuwev"
        md = store.account_md(a, {"address": a, "coins": "26426135ugnot", "sequence": "0",
                                  "vesting": {"original_vesting": "25369090ugnot", "start_time": "1789225200", "end_time": "1852383600"}})
        self.assertIn("- Balance 26.426135 GNOT", md)
        self.assertIn("- Vesting 25.36909 GNOT, from 2026-09-12 to 2028-09-12", md)
        self.assertIn("- No transactions yet", md)

    def test_no_account(self):
        self.assertIn("No account at this address", store.account_md("g1" + "q" * 38, None))


class Tokens(unittest.TestCase):
    def test_amounts_exact_with_decimals(self):
        self.assertEqual(store.amount(137286378825477, 6), "137,286,378.825477")
        self.assertEqual(store.amount(6844096763335, 0), "6,844,096,763,335")
        self.assertEqual(store.amount(2**70, 18), "1,180.591620717411303424")

    def test_token_page_and_search(self):
        r = "gno.land/r/gnoswap/gns"
        t = {"token": r + ".GNS.0000000", "realm": r, "name": "Gnoswap", "symbol": "GNS", "decimals": 6, "created": 5,
             "holders": 623, "supply": 2_000_000, "moves": 3, "moved": 1_500_000, "top": [("g1" + "a" * 38, 1_000_000)]}
        md = store.token_md(t, True)
        self.assertIn("- Supply 2 GNS", md)
        self.assertIn("1.5 GNS moved in the last 30 days", md)
        self.assertIn("[g1aaaaaaaa…aaaa](/u/g1" + "a" * 38 + ") 1 GNS (50.0%)", md)
        self.assertNotIn("moved", store.token_md(t, False))  # outside token coverage moves are unknown, not zero
        store.put_realm(r, [("token:" + t["token"], "Gnoswap (GNS)", "Gnoswap GNS token grc20", md)], 1, 1, kind="token")
        store.put_realm(r, [(r, "Gnoswap", "GNS GNS GNS emission of GNS", "")], 1, 1)  # a page full of the symbol
        self.assertEqual(store.search("GNS")["results"][0]["url"], "token:" + t["token"])
        self.assertEqual(store.search("gnoswap")["results"][0]["url"], "token:" + t["token"])  # the exact name too


class Paging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for i in range(60):  # 60 owners, so the per-realm and per-namespace caps do not thin the list
            r = f"gno.land/r/owner{i:02d}/pager"
            store.put_realm(r, [(r, f"pager {i}", "pagerword " * (1 + i % 7), "")], 1, 1)

    def test_pages_join_without_gaps_or_repeats(self):
        whole = [x["url"] for x in store.search("pagerword", 60)["results"]]
        paged = []
        for off in (0, 20, 40):
            r = store.search("pagerword", 20, offset=off)
            paged += [x["url"] for x in r["results"]]
        self.assertEqual(paged, whole)
        self.assertEqual(len(set(paged)), 60)

    def test_more_flag(self):
        self.assertTrue(store.search("pagerword", 20)["more"])
        self.assertFalse(store.search("pagerword", 20, offset=40)["more"])  # exactly 60 matches

    def test_depth_is_capped(self):
        r = store.search("pagerword", 50, offset=store.MAX_RESULTS + 50)
        self.assertLessEqual(len(r["results"]), 1)
        self.assertFalse(r["more"])


class ExactPath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        b = "gno.land/r/gnoland/blog"
        store.put_realm(b, [(b, "Gnoland's Blog", "posts", ""), (b + ":p/hello", "Hello", "post", ""),
                            (b + ":p/second", "Second", "post", "")], 1, 1)
        store.put_realm(b, [(b + "$source", "blog source", "code", "")], 1, 1, kind="code")
        store.put_realm(b + "/archive", [(b + "/archive", "Archive", "old posts", "")], 1, 1)
        store.put_realm("gno.land/p/gnoland/blog/v0", [("gno.land/p/gnoland/blog/v0", "blog", "gnoland blog gnoland blog", "")], 1, 1, kind="pkg")
        u = "user:g1" + "m" * 38  # an account whose text is nothing but the query's words
        store.put_realm(u, [(u, "manfred", "gno land r gnoland blog gnoland blog " * 20, "")], 1, 1, kind="user")
        store.set_signals(uweight={u: 500.0})

    def test_exact_path_is_first_in_every_spelling(self):
        for q in ["gno.land/r/gnoland/blog", "r/gnoland/blog", "/r/gnoland/blog", "gno.land/r/gnoland/blog/",
                  "https://gno.land/r/gnoland/blog", "  r/gnoland/blog/  "]:
            self.assertEqual(store.search(q)["results"][0]["url"], "gno.land/r/gnoland/blog", q)

    def test_own_pages_then_sub_paths_follow(self):
        urls = [r["url"] for r in store.search("r/gnoland/blog")["results"]]
        self.assertEqual(urls[:5], ["gno.land/r/gnoland/blog", "gno.land/r/gnoland/blog:p/hello",
                                    "gno.land/r/gnoland/blog:p/second", "gno.land/r/gnoland/blog$source",
                                    "gno.land/r/gnoland/blog/archive"])
        self.assertEqual(len(urls), len(set(urls)))  # no page twice

    def test_sub_page_and_package_paths(self):
        self.assertEqual(store.search("gno.land/r/gnoland/blog:p/second")["results"][0]["url"], "gno.land/r/gnoland/blog:p/second")
        self.assertEqual(store.search("p/gnoland/blog/v0")["results"][0]["url"], "gno.land/p/gnoland/blog/v0")

    def test_unknown_or_not_a_path_falls_back_to_text(self):
        self.assertEqual(store.find_path("r/nobody/here"), [])
        self.assertEqual(store.find_path("gnoland blog"), [])
        self.assertEqual(store.find_path("r/a%/b"), [])


class Votes(unittest.TestCase):
    def test_imports_count_owners_not_packages(self):
        for i in range(20):
            store.put_imports(f"gno.land/p/farm/x{i}", ["gno.land/p/farm/target", "gno.land/p/lib/util"])
        store.put_imports("gno.land/p/other/app", ["gno.land/p/lib/util"])
        n = store.import_counts()
        self.assertNotIn("gno.land/p/farm/target", n)       # own namespace: no vote
        self.assertEqual(n["gno.land/p/lib/util"], 2)        # farm (once) + other

    def test_links_count_owners_not_realms(self):
        old = crawl.crawl_realm
        crawl.crawl_realm = lambda r: ([(r, "t", "b", "")], {"gno.land/r/target/x", "gno.land/r/farm/a0"})
        try:
            got = crawl.crawl([f"gno.land/r/farm/a{i}" for i in range(20)] + ["gno.land/r/other/y"], 1)
        finally:
            crawl.crawl_realm = old
        self.assertEqual(got["gno.land/r/target/x"], 2)      # farm + other, not 21
        self.assertEqual(got.get("gno.land/r/farm/a0", 0), 1)  # only other links from outside the farm

    def test_jump_is_logged_after_a_baseline(self):
        import io, contextlib
        store.set_signals(uweight={"gno.land/r/real/dex": 2.0})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            crawl.jumps({"gno.land/r/real/dex": 40.0, "gno.land/r/spam/dex": 3.0})
        self.assertIn("rank jump: gno.land/r/real/dex user weight 2.0 -> 40.0", out.getvalue())
        self.assertNotIn("spam", out.getvalue())  # below the floor


class Coins(unittest.TestCase):
    def test_gnot_is_exact_past_2_53(self):
        self.assertEqual(store.gnot(2**60), "1,152,921,504,606.846976")
        self.assertEqual(store.gnot(3_540_000), "3.54")
        self.assertEqual(store.coins_text("3540000ugnot,7/gno.land/r/x:y"), "3.54 GNOT, 7 /gno.land/r/x:y")
        self.assertEqual(store.coins_text(""), "nothing")

    def test_tx_amount_only_inside_coverage(self):
        store.put_txs([("cd" * 32, 50, "2026-11-15 00:00:00", True, "g1" + "a" * 38, "send", "", "", ""),
                       ("ef" * 32, 5, "2026-11-15 00:00:00", True, "g1" + "a" * 38, "send", "", "", "")])
        store.put_coins([("cd" * 32, "2000000ugnot")])
        store.meta("coins_lo", 10); store.meta("coins_hi", 100)
        self.assertIn("Sent 2 GNOT", store.page("tx:" + "CD" * 32)["md"])
        self.assertNotIn("Sent nothing", store.page("tx:" + "EF" * 32)["md"])  # below coverage: unknown, not "nothing"
        store.meta("coins_lo", 0)  # coverage from genesis (2026-11-15: 0 once read as "no coverage")
        self.assertIn("Sent nothing", store.page("tx:" + "EF" * 32)["md"])

    def test_coins_follow_coverage_at_both_ends(self):
        calls = []
        old = (crawl.indexer.coverage, crawl.indexer.coins)
        crawl.indexer.coins = lambda lo, hi: calls.append((lo, hi)) or []
        try:
            store.meta("tx_height", 1000)
            for k in ("coins_done_lo", "coins_done_hi"):
                store.db().execute("delete from meta where k=?", (k,))
            crawl.indexer.coverage = lambda: (801, 1000); crawl.coins()
            calls.clear()
            crawl.indexer.coverage = lambda: (301, 1000); crawl.coins()  # the refill reached further back
            self.assertEqual(calls, [(300, 800)])
        finally:
            crawl.indexer.coverage, crawl.indexer.coins = old


class Source(unittest.TestCase):
    def test_tests_are_skipped(self):
        self.assertEqual([n for n in ["a.gno", "a_test.gno", "z_1_filetest.gno", "README.md", "gnomod.toml"] if crawl.is_source(n)],
                         ["a.gno", "README.md", "gnomod.toml"])

    def test_imports(self):
        self.assertEqual(crawl.IMPORT.findall('import (\n "gno.land/p/nt/avl/v0"\n "strings"\n)'), ["gno.land/p/nt/avl/v0"])


if __name__ == "__main__":
    unittest.main()
