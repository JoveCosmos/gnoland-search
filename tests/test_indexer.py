"""The indexer tables and columns we read still exist (the explorer owns that schema).
Skipped without INDEXER_RO_URL; run it with a real read-only login before every deploy."""
import os
import unittest

from gnosearch import indexer


@unittest.skipUnless(os.environ.get("INDEXER_RO_URL"), "INDEXER_RO_URL not set")
class Schema(unittest.TestCase):
    def test_columns(self):
        for table, cols in indexer.COLUMNS.items():
            have = {r[0] for r in indexer.query(
                "select column_name from information_schema.columns where table_name = :'t'", t=table)}
            self.assertFalse(set(cols) - have, f"{table} lost columns")

    def test_follow_reads(self):
        touched, top = indexer.touched_since(1)
        self.assertGreaterEqual(top, 1)

    def test_history_reads(self):
        top = indexer.top_height()
        self.assertGreater(top, 0)
        rows = indexer.txs(top - 2000, top)
        self.assertTrue(all(len(r) == 9 for r in rows))
        cov = indexer.coverage()
        self.assertTrue(cov is None or cov[0] <= cov[1])
        self.assertIsInstance(indexer.names(), dict)
        calls, users, weight, last = indexer.usage(top - 20000, top)
        self.assertTrue(set(users) == set(weight) == set(last) == set(calls))
        self.assertTrue(all(0 <= weight[p] <= users[p] for p in users))  # each user weighs at most 1


if __name__ == "__main__":
    unittest.main()
