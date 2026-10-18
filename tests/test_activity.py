import importlib.util
from contextlib import redirect_stdout
from pathlib import Path
from datetime import datetime, timedelta, timezone
import json
import io
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("activity", Path(__file__).parents[1]/"scripts"/"export_activity.py")
activity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(activity)


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
        self.env = dict(zip(activity.ENV_KEYS, ["test-token", "a"*32, "b"*32]))

    def test_scope_is_the_homepage_not_a_zone_or_repository(self):
        query = activity.query_for_day("a"*32, "b"*32, self.now.replace(hour=0))
        for value in ['siteTag:', 'requestHost: "lam810.github.io"', 'rumPageloadEventsAdaptiveGroups', '2026-10-20T00:00:00+00:00', 'dimensions { countryName }', 'limit: 300']:
            self.assertIn(value, query)

    def test_invalid_ids_rejected(self):
        with self.assertRaises(ValueError):
            activity.query_for_day('"} injection', "b"*32, self.now)

    def test_graphql_error_is_not_zero_traffic(self):
        with self.assertRaises(RuntimeError):
            activity.parse_day({"errors": [{"message": "unauthorized"}]}, self.now)

    def test_inaccessible_account_is_not_zero_traffic(self):
        with self.assertRaises(RuntimeError):
            activity.parse_day({"data": {"viewer": {"accounts": []}}}, self.now)

    def test_valid_empty_day_is_zero(self):
        row = activity.parse_day({"data": {"viewer": {"accounts": [{"daily": []}]}}}, self.now)
        self.assertEqual(row, {"date": "2026-10-19", "pageviews": 0, "visits": 0, "countries": []})

    def test_country_groups_are_added_and_private_fields_discarded(self):
        rows = [
            {"count": 4, "sum": {"visits": 2}, "dimensions": {"countryName": "US", "ip": "not-public"}},
            {"count": 3, "sum": {"visits": 1}, "dimensions": {"countryName": "HK"}},
            {"count": 2, "sum": {"visits": 1}, "dimensions": {"countryName": ""}},
            {"count": 1, "sum": {"visits": 1}, "dimensions": {"countryName": "XX"}},
        ]
        row = activity.parse_day({"data": {"viewer": {"accounts": [{"daily": rows}]}}}, self.now)
        self.assertEqual(row["pageviews"], 10)
        self.assertEqual(row["visits"], 5)
        self.assertEqual(row["countries"], [{"country": "US", "pageviews": 4},
            {"country": "HK", "pageviews": 3}, {"country": "ZZ", "pageviews": 3}])
        self.assertNotIn("not-public", json.dumps(row))

    def test_country_query_limit_is_not_silently_truncated(self):
        rows = [{"count": 1, "sum": {"visits": 1}, "dimensions": {"countryName": "US"}}] * 300
        with self.assertRaises(ValueError):
            activity.parse_day({"data": {"viewer": {"accounts": [{"daily": rows}]}}}, self.now)

    def test_invalid_country_is_not_published(self):
        rows = [{"count": 1, "sum": {"visits": 1}, "dimensions": {"countryName": "unexpected-private-value"}}]
        with self.assertRaises(ValueError):
            activity.parse_day({"data": {"viewer": {"accounts": [{"daily": rows}]}}}, self.now)

    def test_bad_counts_rejected(self):
        for value in [None, -1, True, float("nan"), "12"]:
            with self.assertRaises(ValueError):
                activity.metric(value)

    def test_complete_daily_export_and_no_private_fields(self):
        def fetch(token, account, site, day):
            return {"date": day.date().isoformat(), "pageviews": 8, "visits": 3,
                    "countries": [{"country": "HK", "pageviews": 5}, {"country": "US", "pageviews": 3}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"activity.json"
            self.assertTrue(activity.export(path, self.env, self.now, fetch))
            data = json.loads(path.read_text())
            self.assertEqual(data["pageviews"], 56)
            self.assertEqual(data["visits"], 21)
            self.assertEqual(data["daily"][0]["date"], "2026-10-12")
            self.assertEqual(data["daily"][-1]["date"], "2026-10-18")
            self.assertEqual(set(data), {"schemaVersion", "status", "provider", "host", "timezone", "updatedAt", "pageviews", "visits", "daily", "countries"})
            self.assertEqual(data["countries"], [{"country": "HK", "pageviews": 35}, {"country": "US", "pageviews": 21}])
            self.assertEqual(set(data["daily"][0]), {"date", "pageviews", "visits"})
            for secret in self.env.values():
                self.assertNotIn(secret, path.read_text())

    def test_no_secrets_preserve_pending_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"activity.json"
            path.write_text('{"status":"unconfigured"}')
            with redirect_stdout(io.StringIO()):
                self.assertFalse(activity.export(path, {}, self.now))
            self.assertEqual(path.read_text(), '{"status":"unconfigured"}')

    def test_partial_configuration_fails(self):
        with self.assertRaises(ValueError):
            activity.export(Path("unused.json"), {"CLOUDFLARE_API_TOKEN":"test"}, self.now)

    def test_discovery_is_scoped_and_does_not_use_beacon_token(self):
        queries = []
        def request(token, query):
            queries.append(query)
            rows = [] if len(queries) == 1 else [{"dimensions": {"siteTag": "c"*32}}]
            return {"data": {"viewer": {"accounts": [{"properties": rows}]}}}
        self.assertEqual(activity.discover_site_tag("test", "a"*32, self.now, request), "c"*32)
        self.assertEqual(len(queries), 2)
        self.assertIn('requestHost: "lam810.github.io"', queries[0])
        self.assertIn('dimensions { siteTag }', queries[0])

    def test_ambiguous_discovery_fails(self):
        def request(*args):
            return {"data": {"viewer": {"accounts": [{"properties": [
                {"dimensions": {"siteTag": "b"*32}}, {"dimensions": {"siteTag": "c"*32}}
            ]}]}}}
        with self.assertRaises(RuntimeError):
            activity.discover_site_tag("test", "a"*32, self.now, request)

    def test_no_discovery_events_is_not_zero_traffic(self):
        def request(*args):
            return {"data": {"viewer": {"accounts": [{"properties": []}]}}}
        with self.assertRaises(RuntimeError):
            activity.discover_site_tag("test", "a"*32, self.now, request)

    def test_export_automatically_discovers_missing_site(self):
        env = {key: value for key, value in self.env.items() if key != "CLOUDFLARE_SITE_TAG"}
        def discover(token, account, now):
            self.assertEqual(account, "a"*32)
            return "c"*32
        def fetch(token, account, site, day):
            self.assertEqual(site, "c"*32)
            return {"date": day.date().isoformat(), "pageviews": 1, "visits": 1,
                    "countries": [{"country": "US", "pageviews": 1}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"activity.json"
            self.assertTrue(activity.export(path, env, self.now, fetch, discover))
            self.assertEqual(json.loads(path.read_text())["pageviews"], 7)

    def test_failed_fetch_does_not_overwrite_existing_data(self):
        def fail(*args):
            raise RuntimeError("API unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"activity.json"
            path.write_text("previous data")
            with self.assertRaises(RuntimeError):
                activity.export(path, self.env, self.now, fail)
            self.assertEqual(path.read_text(), "previous data")

    def test_inconsistent_country_counts_are_rejected(self):
        daily = [{"date": (self.now - timedelta(days=offset)).date().isoformat(),
                  "pageviews": 1, "visits": 1, "countries": []} for offset in range(7, 0, -1)]
        with self.assertRaises(ValueError):
            activity.snapshot(self.now, daily)


if __name__ == "__main__":
    unittest.main()
