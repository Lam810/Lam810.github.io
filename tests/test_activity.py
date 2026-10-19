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
        for value in ['siteTag:', 'requestHost: "lam810.github.io"', 'rumPageloadEventsAdaptiveGroups', '2026-10-20T00:00:00Z', 'dimensions { date countryName }', 'limit: 300']:
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
            {"count": 4, "sum": {"visits": 2}, "dimensions": {"date": "2026-10-19", "countryName": "US", "ip": "not-public"}},
            {"count": 3, "sum": {"visits": 1}, "dimensions": {"date": "2026-10-19", "countryName": "HK"}},
            {"count": 2, "sum": {"visits": 1}, "dimensions": {"date": "2026-10-19", "countryName": ""}},
            {"count": 1, "sum": {"visits": 1}, "dimensions": {"date": "2026-10-19", "countryName": "XX"}},
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
        rows = [{"count": 1, "sum": {"visits": 1}, "dimensions": {"date": "2026-10-19", "countryName": "unexpected-private-value"}}]
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
            self.assertEqual(data["daily"][0]["date"], "2026-10-13")
            self.assertEqual(data["daily"][-1]["date"], "2026-10-19")
            self.assertTrue(data["includesToday"])
            self.assertEqual(set(data), {"schemaVersion", "status", "provider", "host", "timezone", "updatedAt", "pageviews", "visits", "daily", "countries", "includesToday"})
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
                  "pageviews": 1, "visits": 1, "countries": []} for offset in range(6, -1, -1)]
        with self.assertRaises(ValueError):
            activity.snapshot(self.now, daily)

    def test_current_utc_day_is_included(self):
        requested = []
        def fetch(token, account, site, day):
            requested.append(day.date().isoformat())
            views = 2 if day.date() == self.now.date() else 0
            return {"date": day.date().isoformat(), "pageviews": views, "visits": views,
                    "countries": [{"country": "HK", "pageviews": views}] if views else []}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"activity.json"
            activity.export(path, self.env, self.now, fetch)
            data = json.loads(path.read_text())
            self.assertEqual(data["daily"][-1]["pageviews"], 2)
            self.assertEqual(data["pageviews"], 2)
            self.assertEqual(requested, [f"2026-10-{day:02d}" for day in range(13, 20)])

    def test_future_date_is_rejected(self):
        daily = [{"date": (self.now - timedelta(days=offset)).date().isoformat(),
                  "pageviews": 0, "visits": 0, "countries": []} for offset in range(5, -2, -1)]
        with self.assertRaises(ValueError):
            activity.snapshot(self.now, daily)

    def test_api_dates_must_match_the_requested_day(self):
        for date in ("2026-10-18", "2026-10-20", None):
            rows = [{"count": 20, "sum": {"visits": 20},
                     "dimensions": {"date": date, "countryName": "JP"}}]
            with self.assertRaises(RuntimeError):
                activity.parse_day({"data": {"viewer": {"accounts": [{"daily": rows}]}}}, self.now)

    def test_utc_serialization_does_not_use_local_timezone(self):
        value = datetime(2026, 10, 19, 8, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(activity.utc_timestamp(value), "2026-10-19T00:00:00Z")
        with self.assertRaises(ValueError):
            activity.utc_timestamp(datetime(2026, 10, 19))

    def previous_with_yesterday_views(self):
        daily = [{"date": (self.now - timedelta(days=offset)).date().isoformat(),
                  "pageviews": 8 if offset == 1 else 0,
                  "visits": 8 if offset == 1 else 0,
                  "countries": [{"country": "JP", "pageviews": 8}] if offset == 1 else []}
                 for offset in range(7, 0, -1)]
        return activity.snapshot(self.now - timedelta(days=1), daily)

    def test_temporary_historical_zero_is_rechecked_not_published(self):
        previous = self.previous_with_yesterday_views()
        calls = []
        def fetch(token, account, site, day):
            date = day.date().isoformat()
            calls.append(date)
            views = 8 if date == "2026-10-18" and calls.count(date) > 1 else 0
            return {"date": date, "pageviews": views, "visits": views,
                    "countries": [{"country": "JP", "pageviews": views}] if views else []}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"activity.json"
            activity.export(path, self.env, self.now, fetch, previous=previous)
            result = json.loads(path.read_text())
            self.assertEqual(calls.count("2026-10-18"), 2)
            self.assertEqual(result["daily"][-2]["pageviews"], 8)
            self.assertEqual(result["pageviews"], 8)

    def test_repeated_historical_zero_preserves_previous_snapshot(self):
        previous = self.previous_with_yesterday_views()
        def fetch(token, account, site, day):
            return {"date": day.date().isoformat(), "pageviews": 0, "visits": 0, "countries": []}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"activity.json"
            path.write_text(json.dumps(previous))
            before = path.read_text()
            with self.assertRaisesRegex(RuntimeError, "Historical analytics unexpectedly became zero"):
                activity.export(path, self.env, self.now, fetch, previous=previous)
            self.assertEqual(path.read_text(), before)

    def test_malformed_previous_snapshot_is_not_accepted(self):
        previous = self.previous_with_yesterday_views()
        previous["pageviews"] += 1
        with self.assertRaises(ValueError):
            activity.previous_daily(previous)

    def test_publish_retains_previous_counts_and_timestamp_after_failure(self):
        previous = self.previous_with_yesterday_views()
        previous["private"] = "never-publish-this"
        previous["daily"][0]["private"] = "never-publish-this"
        def fail(*args, **kwargs):
            raise RuntimeError("secret upstream error")
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as output:
            path = Path(tmp)/"activity.json"
            self.assertFalse(activity.publish(path, True, lambda: previous, fail, self.now))
            result = json.loads(path.read_text())
            self.assertEqual(result["pageviews"], previous["pageviews"])
            self.assertEqual(result["updatedAt"], previous["updatedAt"])
            self.assertEqual(result["daily"][-1]["date"], "2026-10-18")
            self.assertEqual(result["countries"], previous["countries"])
            self.assertTrue(result["refreshDelayed"])
            self.assertNotIn("private", path.read_text())
            self.assertNotIn("secret upstream error", output.getvalue())
            self.assertIn("::warning::", output.getvalue())

    def test_publish_without_previous_snapshot_does_not_invent_zero_counts(self):
        def fail():
            raise RuntimeError("network unavailable")
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            path = Path(tmp)/"activity.json"
            self.assertFalse(activity.publish(path, True, fail, now=self.now))
            result = json.loads(path.read_text())
            self.assertEqual(result["status"], "unavailable")
            self.assertNotIn("pageviews", result)
            self.assertNotIn("updatedAt", result)

    def test_history_guard_failure_does_not_block_homepage_publishing(self):
        previous = self.previous_with_yesterday_views()
        def zero(token, account, site, day):
            return {"date": day.date().isoformat(), "pageviews": 0, "visits": 0, "countries": []}
        def refresh(path, previous):
            return activity.export(path, self.env, self.now, zero, previous=previous)
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            path = Path(tmp)/"activity.json"
            activity.publish(path, True, lambda: previous, refresh, self.now)
            result = json.loads(path.read_text())
            self.assertTrue(result["refreshDelayed"])
            self.assertEqual(result["pageviews"], 8)
            self.assertEqual(result["updatedAt"], previous["updatedAt"])

    def test_publish_rejects_invalid_fallback_country_or_timestamp(self):
        def fail(*args, **kwargs):
            raise RuntimeError("upstream failed")
        for field, value in (("countries", [{"country": "private-value", "pageviews": 8}]),
                             ("updatedAt", "2099-10-19T00:00:00Z")):
            previous = self.previous_with_yesterday_views()
            previous[field] = value
            with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
                path = Path(tmp)/"activity.json"
                activity.publish(path, True, lambda: previous, fail, self.now)
                self.assertEqual(json.loads(path.read_text())["status"], "unavailable")

    def test_strict_publish_still_fails_without_overwriting_data(self):
        def fail(*args, **kwargs):
            raise RuntimeError("upstream failed")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"activity.json"
            path.write_text("untouched")
            with self.assertRaises(RuntimeError):
                activity.publish(path, False, lambda: None, fail)
            self.assertEqual(path.read_text(), "untouched")

    def test_publish_uses_fresh_export_when_available(self):
        def fresh(path, **kwargs):
            path.write_text("fresh export")
            return True
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"activity.json"
            self.assertTrue(activity.publish(path, True, lambda: None, fresh))
            self.assertEqual(path.read_text(), "fresh export")


if __name__ == "__main__":
    unittest.main()
