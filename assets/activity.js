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
    if (data.daily[6].date >= new Date(updated).toISOString().slice(0, 10)) throw new Error("Incomplete day");
    const sum = (metric) => data.daily.reduce((total, day) => total + day[metric], 0);
    if (!count(data.pageviews) || !count(data.visits) || data.pageviews !== sum("pageviews") || data.visits !== sum("visits")) throw new Error("Invalid totals");
    return true;
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
      item.title = `${day.date}: ${number.format(day.pageviews)} page views, ${number.format(day.visits)} visits`;
      item.setAttribute("aria-label", item.title);
      const track = element("div", "activity-track");
      track.setAttribute("aria-hidden", "true");
      const bar = element("span", "activity-bar");
      bar.style.setProperty("--bar-height", `${day.pageviews / max * 100}%`);
      track.append(bar);
      item.append(element("span", "", number.format(day.pageviews)), track, element("span", "", label));
      chart.append(item);
    }
    const delayed = Date.now() - Date.parse(data.updatedAt) > 36 * 3600000;
    const updated = element("p", "activity-updated", `Cloudflare estimates · Updated ${timestamp.format(new Date(data.updatedAt))} UTC${delayed ? " · Update delayed" : ""}`);
    container.replaceChildren(totals, chart, updated);
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
