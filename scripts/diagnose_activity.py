"""Compare aggregate scopes without exposing tokens, property IDs, or raw events."""
from datetime import datetime, timedelta, timezone
import json
import os
import re

from export_activity import HOST, account_rows, discover_site_tag, graphql, metric


def main():
    token = os.environ["CLOUDFLARE_API_TOKEN"].strip()
    account = os.environ["CLOUDFLARE_ACCOUNT_ID"].strip()
    now = datetime.now(timezone.utc)
    site = os.environ.get("CLOUDFLARE_SITE_TAG", "").strip() or discover_site_tag(token, account, now)
    if not all(re.fullmatch(r"[0-9a-fA-F]{32}", value) for value in (account, site)):
        raise ValueError("Invalid analytics configuration")
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for offset in (2, 1, 0):
        start = midnight - timedelta(days=offset)
        end = min(start + timedelta(days=1), now)
        for scoped in (False, True):
            scope = f"siteTag: {json.dumps(site)}," if scoped else ""
            query = f"""{{ viewer {{ accounts(filter: {{accountTag: {json.dumps(account)}}}) {{
              observations: rumPageloadEventsAdaptiveGroups(limit: 1000, filter: {{
                {scope} requestHost: {json.dumps(HOST)},
                datetime_geq: {json.dumps(start.isoformat().replace('+00:00', 'Z'))},
                datetime_lt: {json.dumps(end.isoformat().replace('+00:00', 'Z'))}
              }}) {{ count sum {{ visits }} avg {{ sampleInterval }} dimensions {{ siteTag date countryName }} }}
            }} }} }}"""
            rows = account_rows(graphql(token, query), "observations")
            properties = {row["dimensions"]["siteTag"] for row in rows}
            dates = sorted({row["dimensions"]["date"] for row in rows})
            print(json.dumps({"date": start.date().isoformat(),
                "scope": "selected_property_and_host" if scoped else "host_only",
                "pageviews": sum(metric(row["count"]) for row in rows),
                "visits": sum(metric(row["sum"]["visits"]) for row in rows),
                "propertyCount": len(properties), "selectedPropertyPresent": site in properties,
                "returnedDates": [date for date in dates if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)],
                "samplingIntervals": sorted({metric(row["avg"]["sampleInterval"]) for row in rows})}))


if __name__ == "__main__":
    main()
