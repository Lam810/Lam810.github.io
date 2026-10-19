(() => {
  "use strict";
  const status = document.querySelector("#activity-status");
  const container = document.querySelector("#activity-data");
  if (!status || !container) return;

  const number = new Intl.NumberFormat("en");
  const dayLabel = new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" });
  const timestamp = new Intl.DateTimeFormat("en", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC"
  });
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const count = (value) => Number.isSafeInteger(value) && value >= 0;
  const regions = typeof Intl.DisplayNames === "function" ? new Intl.DisplayNames(["en"], { type: "region" }) : null;
  const share = new Intl.NumberFormat("en", { style: "percent", maximumFractionDigits: 1 });

  function validate(data) {
    if (data.schemaVersion !== 1 || data.provider !== "Cloudflare Web Analytics") throw new Error("Invalid source");
    if (data.status === "unconfigured") return false;
    if (data.status !== "ready" || data.timezone !== "UTC" || data.host !== "lam810.github.io") throw new Error("Invalid scope");
    const updated = Date.parse(data.updatedAt);
    if (!Number.isFinite(updated) || updated > Date.now() + 3600000) throw new Error("Invalid timestamp");
    if (!Array.isArray(data.daily) || data.daily.length !== 7) throw new Error("Invalid range");
    data.daily.forEach((day, index) => {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(day.date) || !count(day.pageviews) || !count(day.visits)) throw new Error("Invalid day");
      const value = Date.parse(`${day.date}T00:00:00Z`);
      if (!Number.isFinite(value) || new Date(value).toISOString().slice(0, 10) !== day.date) throw new Error("Invalid date");
      if (index && value - Date.parse(`${data.daily[index - 1].date}T00:00:00Z`) !== 86400000) throw new Error("Missing day");
    });
    const lastDay = Date.parse(`${data.daily[6].date}T00:00:00Z`);
    const today = new Date(updated).setUTCHours(0, 0, 0, 0);
    if (lastDay !== today - (data.includesToday === true ? 0 : 86400000)) throw new Error("Invalid date range");
    const sum = (metric) => data.daily.reduce((total, day) => total + day[metric], 0);
    if (!count(data.pageviews) || !count(data.visits) || data.pageviews !== sum("pageviews") || data.visits !== sum("visits")) throw new Error("Invalid totals");
    if (data.countries !== undefined) {
      if (!Array.isArray(data.countries) || data.countries.length >= 300) throw new Error("Invalid countries");
      const codes = new Set();
      let total = 0;
      for (const row of data.countries) {
        if (!/^[A-Z]{2}$/.test(row.country) || codes.has(row.country) || !count(row.pageviews) || !row.pageviews) throw new Error("Invalid country");
        codes.add(row.country);
        total += row.pageviews;
      }
      if (!count(total) || total !== data.pageviews) throw new Error("Invalid country totals");
    }
    return true;
  }

  function countryDistribution(data) {
    const section = element("div", "activity-countries");
    const heading = element("h3", "", "Countries / Regions");
    section.append(heading);
    if (!data.countries?.length) {
      section.append(element("p", "activity-country-empty", data.countries ? "No country data for this period." : "Country data will appear after the next update."));
      return section;
    }
    const rows = [...data.countries].sort((a, b) => b.pageviews - a.pageviews || a.country.localeCompare(b.country));
    const known = rows.filter(row => row.country !== "ZZ");
    const visible = known.slice(0, 5).map(row => ({
      label: regions?.of(row.country) || row.country, pageviews: row.pageviews
    }));
    if (known.length > 5) visible.push({label: "Other countries", pageviews: known.slice(5).reduce((sum, row) => sum + row.pageviews, 0)});
    const unknown = rows.find(row => row.country === "ZZ");
    if (unknown) visible.push({label: "Unknown", pageviews: unknown.pageviews});
    const table = element("table", "activity-country-table");
    const caption = element("caption", "", "Page views by country / region · Same 7-day period");
    table.append(caption);
    const header = element("tr");
    for (const text of ["Country / Region", "Page views", "Share"]) {
      const cell = element("th", "", text);
      cell.scope = "col";
      header.append(cell);
    }
    const head = element("thead");
    head.append(header);
    const body = element("tbody");
    for (const row of visible) {
      const item = element("tr");
      const label = element("th", "", row.label);
      label.scope = "row";
      const value = element("td", "", number.format(row.pageviews));
      const ratio = element("td", "activity-country-share");
      const bar = element("span", "activity-country-bar");
      bar.setAttribute("aria-hidden", "true");
      bar.style.setProperty("--country-share", `${row.pageviews / data.pageviews * 100}%`);
      ratio.append(bar, element("span", "", share.format(row.pageviews / data.pageviews)));
      item.append(label, value, ratio);
      body.append(item);
    }
    table.append(head, body);
    section.append(table);
    section.append(element("p", "activity-country-note", "Approximate network location; VPNs and proxies may affect attribution."));
    return section;
  }

  function render(data) {
    const totals = element("dl", "activity-totals");
    for (const [label, value] of [["Page views", data.pageviews], ["Visits", data.visits]]) {
      const pair = element("div");
      pair.append(element("dt", "", label), element("dd", "", number.format(value)));
      totals.append(pair);
    }
    const chart = element("ol", "activity-chart");
    chart.setAttribute("aria-label", "Daily page views, UTC");
    const max = Math.max(1, ...data.daily.map(day => day.pageviews));
    for (const day of data.daily) {
      const label = dayLabel.format(new Date(`${day.date}T00:00:00Z`));
      const item = element("li");
      const partial = data.includesToday === true && day === data.daily[6];
      item.title = `${day.date}: ${number.format(day.pageviews)} page views, ${number.format(day.visits)} visits${partial ? " (partial day)" : ""}`;
      item.setAttribute("aria-label", item.title);
      const track = element("div", "activity-track");
      track.setAttribute("aria-hidden", "true");
      const bar = element("span", "activity-bar");
      bar.style.setProperty("--bar-height", `${day.pageviews / max * 100}%`);
      track.append(bar);
      item.append(element("span", "", number.format(day.pageviews)), track, element("span", "", label));
      chart.append(item);
    }
    const endsToday = data.daily[6].date === new Date().toISOString().slice(0, 10);
    const delayed = data.refreshDelayed === true || Date.now() - Date.parse(data.updatedAt) > 36 * 3600000;
    const updated = element("p", "activity-updated", `Cloudflare estimates · Updated ${timestamp.format(new Date(data.updatedAt))} UTC${data.includesToday ? " · Today partial" : ""}${delayed ? " · Update delayed" : ""}`);
    container.replaceChildren(totals, chart, countryDistribution(data), updated);
    const period = document.querySelector(".activity-period");
    if (period) period.textContent = delayed || (data.includesToday && !endsToday)
      ? "Last available 7-day snapshot · UTC"
      : data.includesToday ? "Last 7 days · Including today · UTC" : "Last 7 complete days · UTC";
    status.textContent = `${dayLabel.format(new Date(`${data.daily[0].date}T00:00:00Z`))} – ${dayLabel.format(new Date(`${data.daily[6].date}T00:00:00Z`))} · Daily page views`;
  }

  async function load() {
    try {
      const response = await fetch("assets/activity.json", { cache: "no-cache", signal: AbortSignal.timeout(10000) });
      if (!response.ok) throw new Error("Unavailable");
      const data = await response.json();
      if (!validate(data)) {
        status.textContent = "Awaiting analytics connection.";
        return;
      }
      render(data);
    } catch {
      container.replaceChildren();
      status.textContent = "Statistics temporarily unavailable.";
    }
  }
  load();
})();
