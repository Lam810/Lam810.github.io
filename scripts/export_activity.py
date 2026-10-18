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


def query_for_day(account, site, day):
    if not all(re.fullmatch(r"[0-9a-fA-F]{32}", value) for value in (account, site)):
        raise ValueError("Account ID and Web Analytics Site Tag must each contain 32 hexadecimal characters")
    end = day + timedelta(days=1)
    values = [json.dumps(value) for value in (account, site, HOST, day.isoformat(), end.isoformat())]
    account, site, host, start, end = values
    return f"""{{ viewer {{ accounts(filter: {{accountTag: {account}}}) {{
      daily: rumPageloadEventsAdaptiveGroups(limit: 2, filter: {{
        siteTag: {site}, requestHost: {host}, datetime_geq: {start}, datetime_lt: {end}
      }}) {{ count sum {{ visits }} }}
    }} }} }}"""


def fetch_day(token, account, site, day):
    payload = json.dumps({"query": query_for_day(account, site, day)}).encode()
    request = urllib.request.Request(ENDPOINT, data=payload, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        "User-Agent": "homepage-aggregate-analytics/1.0",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                data = json.load(response)
            return parse_day(data, day)
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f"Cloudflare HTTP {error.code}; check read-only access") from None
        except urllib.error.URLError:
            if attempt == 2:
                raise RuntimeError("Cloudflare network request failed") from None
        time.sleep(2 ** attempt)


def metric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Invalid aggregate count")
    rounded = round(value)
    if rounded > 9007199254740991:
        raise ValueError("Aggregate count exceeds browser precision")
    return rounded


def parse_day(data, day):
    # GraphQL can return HTTP 200 while reporting authorization or schema errors.
    if data.get("errors"):
        raise RuntimeError("Cloudflare GraphQL query failed; verify permissions and the RUM schema")
    accounts = data.get("data", {}).get("viewer", {}).get("accounts")
    if not isinstance(accounts, list) or len(accounts) != 1:
        raise RuntimeError("Cloudflare account is not available to this API token")
    rows = accounts[0].get("daily")
    if not isinstance(rows, list) or len(rows) > 1:
        raise ValueError("Unexpected aggregate groups")
    views = visits = 0
    if rows:
        views = metric(rows[0].get("count"))
        visits = metric(rows[0].get("sum", {}).get("visits"))
    return {"date": day.date().isoformat(), "pageviews": views, "visits": visits}


def snapshot(now, daily):
    if len(daily) != 7:
        raise ValueError("Exactly seven complete UTC days are required")
    end = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    expected = [(end - timedelta(days=offset)).date().isoformat() for offset in range(7, 0, -1)]
    if [item["date"] for item in daily] != expected:
        raise ValueError("Daily data are not contiguous or include an incomplete day")
    for item in daily:
        metric(item["pageviews"])
        metric(item["visits"])
    return {
        "schemaVersion": 1, "status": "ready", "provider": "Cloudflare Web Analytics",
        "host": HOST, "timezone": "UTC", "updatedAt": now.astimezone(timezone.utc).isoformat(),
        "pageviews": sum(item["pageviews"] for item in daily),
        "visits": sum(item["visits"] for item in daily), "daily": daily,
    }


def export(path, env=os.environ, now=None, fetcher=fetch_day):
    values = [env.get(key, "").strip() for key in ENV_KEYS]
    if not all(values):
        if any(values):
            raise ValueError("Cloudflare configuration is incomplete; all three secrets are required")
        print("::notice::Cloudflare analytics secrets are not configured; publishing the connection-pending state")
        return False
    now = now or datetime.now(timezone.utc)
    end = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    daily = [fetcher(*values, end - timedelta(days=offset)) for offset in range(7, 0, -1)]
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
        export(args.output)
    except (RuntimeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
