import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import review_cache
import server


class ReviewCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = self.root / "views"
        self.history = self.root / "klines"
        self.history.mkdir()
        (self.history / "000001.json").write_text('{}')
        self.review = {"selected_strategy": {"id": "a"}, "rounds": [],
                       "replay_rounds": [{"detail": [{"code": "000001"}]}],
                       "capital_equity": {"replay": {"pnl": -1257.17}}}

    def write(self, identity="a"):
        review_cache.write(self.cache, identity, "r1", self.history, self.review)

    def read(self, identity="a", revision="r1"):
        return review_cache.read(self.cache, identity, revision, self.history)

    def test_result_persists_and_preserves_money(self):
        self.write()
        self.assertEqual(self.read(), self.review)
        self.assertEqual(json.loads(review_cache.cache_path(self.cache, "a").read_text())["version"], review_cache.VERSION)

    def test_strategy_caches_are_isolated(self):
        self.write()
        self.assertIsNone(self.read("b"))

    def test_data_revision_invalidates_saved_result(self):
        self.write()
        self.assertIsNone(self.read(revision="r2"))

    def test_kline_change_invalidates_even_without_manifest_update(self):
        self.write()
        (self.history / "000001.json").write_text('{"adjustment":"qfq"}')
        self.assertIsNone(self.read())

    def test_removed_kline_invalidates(self):
        self.write()
        (self.history / "000001.json").unlink()
        self.assertIsNone(self.read())

    def test_corrupt_cache_recomputes_instead_of_serving_garbage(self):
        self.write()
        review_cache.cache_path(self.cache, "a").write_text('broken')
        self.assertIsNone(self.read())

    def test_identity_cannot_escape_cache_directory(self):
        self.assertEqual(review_cache.cache_path(self.cache, "../../elsewhere").parent, self.cache)

    def test_cache_hit_does_not_recompute_or_update(self):
        with patch.object(server.review_cache, "read", return_value=self.review), \
             patch.object(server, "recommendation_review_revision", return_value="r1"), \
             patch.object(server, "update_recommendation_review") as update, \
             patch.object(server, "recommendation_review_summary") as summarize:
            self.assertEqual(server.cached_recommendation_review("a"), self.review)
            update.assert_not_called()
            summarize.assert_not_called()

    def test_existing_strategy_cache_miss_only_reads_summary(self):
        with patch.object(server.review_cache, "read", return_value=None), \
             patch.object(server.review_cache, "write"), \
             patch.object(server, "recommendation_review_revision", return_value="r1"), \
             patch.object(server, "read_dashboard_cache", return_value={}), \
             patch.object(server, "load_recommendation_log", return_value={}), \
             patch.object(server, "recommendation_review_summary", return_value=self.review), \
             patch.object(server, "update_recommendation_review") as update:
            self.assertEqual(server.cached_recommendation_review("a"), self.review)
            update.assert_not_called()

    def test_manual_update_bypasses_cache(self):
        with patch.object(server.review_cache, "read") as read, \
             patch.object(server.review_cache, "write"), \
             patch.object(server, "recommendation_review_revision", return_value="r1"), \
             patch.object(server, "read_dashboard_cache", return_value={}), \
             patch.object(server, "update_recommendation_review", return_value=self.review) as update:
            self.assertEqual(server.cached_recommendation_review("a", force=True), self.review)
            read.assert_not_called()
            update.assert_called_once_with({}, strategy_id="a")


if __name__ == "__main__":
    unittest.main()
