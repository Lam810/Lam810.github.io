"""Publish only daily aggregate Cloudflare RUM counts, never raw events or credentials."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request

ENDPOINT = "https://api.cloudflare.com/client/v4/graphql"
HOST = "lam810.github.io"
ENV_KEYS = ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_SITE_TAG")
COUNTRY_LIMIT = 300
PUBLISHED_SNAPSHOT = f"https://{HOST}/assets/activity.json"


def utc_timestamp(value):
    if value.tzinfo is None:
        raise ValueError("Analytics timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def query_for_day(account, site, day):
    if not all(re.fullmatch(r"[0-9a-fA-F]{32}", value) for value in (account, site)):
        raise ValueError("Account ID and Web Analytics Site Tag must each contain 32 hexadecimal characters")
    end = day + timedelta(days=1)
    values = [json.dumps(value) for value in (account, site, HOST, utc_timestamp(day), utc_timestamp(end))]
    account, site, host, start, end = values
    return f"""{{ viewer {{ accounts(filter: {{accountTag: {account}}}) {{
      daily: rumPageloadEventsAdaptiveGroups(limit: {COUNTRY_LIMIT}, filter: {{
        siteTag: {site}, requestHost: {host}, datetime_geq: {start}, datetime_lt: {end}
      }}) {{ count sum {{ visits }} dimensions {{ date countryName }} }}
    }} }} }}"""


def graphql(token, query):
    payload = json.dumps({"query": query}).encode()
    request = urllib.request.Request(ENDPOINT, data=payload, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        "User-Agent": "homepage-aggregate-analytics/1.0",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                data = json.load(response)
            return data
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f"Cloudflare HTTP {error.code}; check read-only access") from None
        except urllib.error.URLError:
            if attempt == 2:
                raise RuntimeError("Cloudflare network request failed") from None
        time.sleep(2 ** attempt)


def account_rows(data, field):
    if data.get("errors"):
        # Classify errors without publishing upstream messages or private identifiers.
        messages = " ".join(str(error.get("message", "")) for error in data["errors"]).lower()
        if any(word in messages for word in ("auth", "permission", "access denied")):
            raise RuntimeError("Cloudflare rejected analytics access; verify Account Analytics Read permission and account scope")
        if any(word in messages for word in ("unknown", "not defined", "cannot query")):
            raise RuntimeError("Cloudflare analytics schema rejected the query")
        raise RuntimeError("Cloudflare GraphQL query failed; verify permissions and the RUM schema")
    accounts = data.get("data", {}).get("viewer", {}).get("accounts")
    if not isinstance(accounts, list) or len(accounts) != 1:
        raise RuntimeError("Cloudflare account is not available to this API token")
    rows = accounts[0].get(field)
    if not isinstance(rows, list):
        raise ValueError("Missing aggregate groups")
    return rows


def discover_site_tag(token, account, now, requester=graphql):
    if not re.fullmatch(r"[0-9a-fA-F]{32}", account):
        raise ValueError("Account ID must contain 32 hexadecimal characters")
    end = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    # One-day windows also work with restricted RUM query-range limits.
    for offset in range(8):
        stop = end - timedelta(days=offset)
        start = stop - timedelta(days=1)
        query = f"""{{ viewer {{ accounts(filter: {{accountTag: {json.dumps(account)}}}) {{
          properties: rumPageloadEventsAdaptiveGroups(limit: 2, filter: {{
            requestHost: {json.dumps(HOST)}, datetime_geq: {json.dumps(utc_timestamp(start))},
            datetime_lt: {json.dumps(utc_timestamp(stop))}
          }}) {{ dimensions {{ siteTag }} }}
        }} }} }}"""
        rows = account_rows(requester(token, query), "properties")
        tags = {row.get("dimensions", {}).get("siteTag") for row in rows}
        if any(not isinstance(tag, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", tag) for tag in tags):
            raise ValueError("Cloudflare returned an invalid Web Analytics property identifier")
        if len(tags) > 1:
            raise RuntimeError("Multiple Web Analytics properties match this homepage; set CLOUDFLARE_SITE_TAG explicitly")
        if tags:
            print("Identified the homepage Web Analytics property automatically")
            return tags.pop()
    raise RuntimeError("No recent homepage traffic found for property discovery; set CLOUDFLARE_SITE_TAG from Web Analytics")


def fetch_day(token, account, site, day):
    return parse_day(graphql(token, query_for_day(account, site, day)), day)


def metric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Invalid aggregate count")
    rounded = round(value)
    if rounded > 9007199254740991:
        raise ValueError("Aggregate count exceeds browser precision")
    return rounded


def parse_day(data, day):
    # GraphQL can return HTTP 200 while reporting authorization or schema errors.
    rows = account_rows(data, "daily")
    if len(rows) >= COUNTRY_LIMIT:
        raise ValueError("Country query may be truncated; refusing to publish incomplete totals")
    views = visits = 0
    countries = {}
    for row in rows:
        if row.get("dimensions", {}).get("date") != day.astimezone(timezone.utc).date().isoformat():
            raise RuntimeError("Cloudflare returned data for another UTC date; previous published snapshot preserved")
        country = row.get("dimensions", {}).get("countryName")
        if country is None or country in ("", "XX", "T1"):
            country = "ZZ"
        if not isinstance(country, str) or not re.fullmatch(r"[A-Z]{2}", country):
            raise ValueError("Unexpected country code in Cloudflare aggregates")
        pageviews = metric(row.get("count"))
        views += pageviews
        visits += metric(row.get("sum", {}).get("visits"))
        countries[country] = countries.get(country, 0) + pageviews
    return {"date": day.date().isoformat(), "pageviews": views, "visits": visits,
            "countries": country_rows(countries)}


def country_rows(counts):
    return [{"country": code, "pageviews": value} for code, value in
            sorted(counts.items(), key=lambda item: (-item[1], item[0])) if value > 0]


def previous_daily(data):
    if data is None or data.get("status") == "unconfigured":
        return {}
    if (data.get("schemaVersion") != 1 or data.get("status") != "ready"
            or data.get("host") != HOST or data.get("timezone") != "UTC"
            or data.get("provider") != "Cloudflare Web Analytics"):
        raise ValueError("Invalid previous analytics snapshot")
    rows = data.get("daily")
    if not isinstance(rows, list) or len(rows) != 7:
        raise ValueError("Invalid previous analytics date range")
    by_date = {}
    previous_date = None
    for row in rows:
        date = datetime.strptime(row["date"], "%Y-%m-%d").date()
        if date.isoformat() != row["date"] or (previous_date and date - previous_date != timedelta(days=1)):
            raise ValueError("Invalid previous analytics dates")
        previous_date = date
        metric(row["pageviews"])
        metric(row["visits"])
        by_date[row["date"]] = row
    for key in ("pageviews", "visits"):
        metric(data.get(key))
        if sum(row[key] for row in rows) != data[key]:
            raise ValueError("Invalid previous analytics totals")
    return by_date


def load_previous_snapshot():
    request = urllib.request.Request(PUBLISHED_SNAPSHOT + f"?check={int(time.time())}", headers={
        "Cache-Control": "no-cache", "Accept": "application/json",
        "User-Agent": "homepage-aggregate-analytics/1.0",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(131073)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise RuntimeError("Could not verify previous published analytics; deployment stopped") from None
    except urllib.error.URLError:
        raise RuntimeError("Could not verify previous published analytics; deployment stopped") from None
    if len(raw) > 131072:
        raise ValueError("Previous analytics snapshot exceeds size limit")
    data = json.loads(raw)
    previous_daily(data)
    return data


def verify_history(daily, previous, now, values, fetcher):
    old = previous_daily(previous)
    today = now.astimezone(timezone.utc).date().isoformat()
    for index, row in enumerate(daily):
        baseline = old.get(row["date"])
        if row["date"] >= today or baseline is None:
            continue
        erased = [key for key in ("pageviews", "visits") if baseline[key] > 0 and row[key] == 0]
        if not erased:
            continue
        print(f"Rechecking unexpectedly empty historical aggregate for {row['date']}")
        day = datetime.strptime(row["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        checked = fetcher(*values, day)
        if checked["date"] != row["date"] or any(checked[key] == 0 for key in erased):
            raise RuntimeError("Historical analytics unexpectedly became zero; previous published snapshot preserved")
        daily[index] = checked
    return daily


def snapshot(now, daily):
    if len(daily) != 7:
        raise ValueError("Exactly seven UTC dates including today are required")
    end = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    expected = [(end - timedelta(days=offset)).date().isoformat() for offset in range(7, 0, -1)]
    if [item["date"] for item in daily] != expected:
        raise ValueError("Daily data must be contiguous and end on today's UTC date")
    countries = {}
    for item in daily:
        metric(item["pageviews"])
        metric(item["visits"])
        rows = item["countries"]
        if sum(row["pageviews"] for row in rows) != item["pageviews"]:
            raise ValueError("Country totals do not match daily page views")
        seen = set()
        for row in rows:
            country = row["country"]
            if not isinstance(country, str) or not re.fullmatch(r"[A-Z]{2}", country) or country in seen:
                raise ValueError("Invalid or duplicate country code")
            seen.add(country)
            countries[country] = countries.get(country, 0) + metric(row["pageviews"])
    return {
        "schemaVersion": 1, "status": "ready", "provider": "Cloudflare Web Analytics",
        "includesToday": True,
        "host": HOST, "timezone": "UTC", "updatedAt": now.astimezone(timezone.utc).isoformat(),
        "pageviews": sum(item["pageviews"] for item in daily),
        "visits": sum(item["visits"] for item in daily),
        "daily": [{key: item[key] for key in ("date", "pageviews", "visits")} for item in daily],
        "countries": country_rows(countries),
    }


def export(path, env=os.environ, now=None, fetcher=fetch_day, discoverer=discover_site_tag, previous=None):
    values = [env.get(key, "").strip() for key in ENV_KEYS]
    if not all(values[:2]):
        if any(values):
            raise ValueError("Cloudflare configuration is incomplete; API token and Account ID are required")
        print("::notice::Cloudflare analytics secrets are not configured; publishing the connection-pending state")
        return False
    now = now or datetime.now(timezone.utc)
    if not values[2]:
        values[2] = discoverer(values[0], values[1], now)
    end = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    daily = [fetcher(*values, end - timedelta(days=offset)) for offset in range(7, 0, -1)]
    daily = verify_history(daily, previous, now, values, fetcher)
    result = snapshot(now, daily)
    # An allowlisted schema prevents raw API responses from reaching the public site.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    print("Published seven days of aggregate homepage traffic")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("assets/activity.json"))
    args = parser.parse_args()
    try:
        export(args.output, previous=load_previous_snapshot())
    except (RuntimeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
