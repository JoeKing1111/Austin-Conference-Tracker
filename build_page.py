#!/usr/bin/env python3
"""
build_page.py
Reads austin_conferences.csv and produces austin_events.html —
a self-contained, Austin-themed event card page with search and filters.

Usage (run from the Conference Tracker folder):
    python build_page.py

Then open austin_events.html in any browser, or upload it to any web host.
"""

import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

CSV_FILE  = "austin_conferences.csv"
HTML_FILE = "austin_events.html"

# ---------------------------------------------------------------------------
# Data cleaning helpers
# ---------------------------------------------------------------------------

def clean_cost(raw: str) -> str:
    """Normalise cost strings into something card-friendly."""
    if not raw:
        return "Unknown"
    raw = raw.strip()
    if raw.lower() in ("free", ""):
        return "Free"
    if raw.lower() in ("unknown",):
        return "Unknown"
    if raw == "Members Only":
        return "Members Only"

    # Luma dict format: "Paid — {'cents': 2800, 'currency': 'usd', ...}"
    m = re.search(r"'cents':\s*(\d+)", raw)
    if m:
        dollars = int(m.group(1)) / 100
        return f"${dollars:,.2f}".rstrip("0").rstrip(".")

    # "Paid — 40.0 USD"  /  "Paid — 17.99 USD"
    m = re.search(r"Paid\s*[—-]\s*([\d.]+)\s*USD", raw, re.IGNORECASE)
    if m:
        amt = float(m.group(1))
        formatted = f"${amt:,.2f}".rstrip("0").rstrip(".")
        return formatted

    # Generic "Paid" with no amount
    if raw.lower().startswith("paid"):
        return "Paid"

    return raw


def friendly_date(iso: str) -> str:
    """Convert YYYY-MM-DD to 'Mon D, YYYY'."""
    if not iso or iso.strip() == "":
        return ""
    try:
        return datetime.strptime(iso.strip(), "%Y-%m-%d").strftime("%b %-d, %Y")
    except ValueError:
        try:
            return datetime.strptime(iso.strip(), "%Y-%m-%d").strftime("%b %d, %Y")
        except Exception:
            return iso.strip()


def friendly_date_range(start: str, end: str) -> str:
    """Return a concise date range string."""
    s = friendly_date(start)
    e = friendly_date(end)
    if not s:
        return "TBD"
    if not e or e == s:
        return s
    # Same month → "Apr 15 – 17, 2026"
    try:
        ds = datetime.strptime(start.strip(), "%Y-%m-%d")
        de = datetime.strptime(end.strip(),   "%Y-%m-%d")
        if ds.year == de.year and ds.month == de.month:
            return f"{ds.strftime('%b %-d')} – {de.strftime('%-d, %Y')}"
    except Exception:
        pass
    return f"{s} – {e}"


def primary_source(src: str) -> str:
    """Return the first listed source for badge display."""
    return src.split(",")[0].strip() if src else "Unknown"


SOURCE_ORDER = ["10times.com", "Luma (lu.ma)", "Eventbrite",
                "Austin Chamber of Commerce", "Meetup.com"]

def source_sort_key(src: str) -> int:
    ps = primary_source(src)
    try:
        return SOURCE_ORDER.index(ps)
    except ValueError:
        return len(SOURCE_ORDER)


# ---------------------------------------------------------------------------
# Load & clean CSV
# ---------------------------------------------------------------------------

def load_events(path: str) -> list[dict]:
    events = []
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        content = f.read().replace("\x00", "")
    import io
    for row in csv.DictReader(io.StringIO(content)):
        if True:
            name = row.get("Conference Name", "").strip()
            if not name:
                continue

            start_iso = row.get("Start Date", "").strip()
            end_iso   = row.get("End Date",   "").strip()

            events.append({
                "name":      name,
                "category":  row.get("Industry / Category", "").strip() or "General",
                "start_iso": start_iso,
                "end_iso":   end_iso,
                "date_label": friendly_date_range(start_iso, end_iso),
                "venue":     row.get("Venue Name", "").strip(),
                "address":   row.get("Address", "").strip(),
                "cost":      clean_cost(row.get("Cost", "")),
                "url":       row.get("Website URL", "").strip(),
                "organizer": row.get("Organizer Name", "").strip(),
                "source":    row.get("Source", "").strip(),
                "source_primary": primary_source(row.get("Source", "")),
            })

    # Sort: upcoming first, then by name
    def sort_key(e):
        d = e["start_iso"] or "9999-99-99"
        return (d, e["name"].lower())

    events.sort(key=sort_key)
    return events


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Austin Events & Conferences</title>
<style>
  /* ── Reset & base ── */
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --orange:   #BF5700;
    --orange-lt:#F5E6D3;
    --teal:     #00706E;
    --teal-lt:  #D4EFED;
    --cream:    #FBF7F1;
    --card-bg:  #FFFFFF;
    --border:   #E8DDD0;
    --text:     #2C2416;
    --muted:    #6B5D4F;
    --radius:   10px;
    --shadow:   0 2px 8px rgba(44,36,22,.09);
    --shadow-hv:0 6px 20px rgba(44,36,22,.15);
  }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--cream);
    color: var(--text);
    min-height: 100vh;
  }

  /* ── Header ── */
  header {
    background: linear-gradient(135deg, #2C1810 0%, #5C2E00 50%, #00706E 100%);
    color: #fff;
    padding: 2.5rem 2rem 2rem;
    text-align: center;
    position: relative;
    overflow: hidden;
  }
  header::before {
    content: "🤠";
    position: absolute;
    font-size: 8rem;
    opacity: .06;
    top: -1rem;
    left: 1rem;
    pointer-events: none;
  }
  header::after {
    content: "🎸";
    position: absolute;
    font-size: 7rem;
    opacity: .06;
    bottom: -1rem;
    right: 2rem;
    pointer-events: none;
  }
  header h1 {
    font-size: 2.2rem;
    font-weight: 800;
    letter-spacing: -0.5px;
    margin-bottom: .4rem;
  }
  header h1 span { color: #F5A623; }
  header p { opacity: .8; font-size: .95rem; }
  #event-count { font-weight: 700; color: #F5A623; }

  /* ── Controls bar ── */
  .controls {
    background: #fff;
    border-bottom: 1px solid var(--border);
    padding: 1rem 2rem;
    display: flex;
    gap: 1rem;
    flex-wrap: wrap;
    align-items: center;
    position: sticky;
    top: 0;
    z-index: 100;
    box-shadow: 0 2px 6px rgba(0,0,0,.06);
  }
  .search-wrap {
    flex: 1;
    min-width: 200px;
    position: relative;
  }
  .search-wrap svg {
    position: absolute;
    left: .75rem;
    top: 50%;
    transform: translateY(-50%);
    color: var(--muted);
    pointer-events: none;
  }
  #search {
    width: 100%;
    padding: .6rem .75rem .6rem 2.4rem;
    border: 1.5px solid var(--border);
    border-radius: var(--radius);
    font-size: .95rem;
    background: var(--cream);
    color: var(--text);
    transition: border-color .2s;
  }
  #search:focus { outline: none; border-color: var(--orange); }

  /* ── Filter chips ── */
  .chip-group { display: flex; flex-wrap: wrap; gap: .4rem; }
  .chip {
    padding: .35rem .85rem;
    border-radius: 20px;
    border: 1.5px solid var(--border);
    font-size: .8rem;
    font-weight: 600;
    cursor: pointer;
    background: #fff;
    color: var(--muted);
    transition: all .15s;
    white-space: nowrap;
    user-select: none;
  }
  .chip:hover { border-color: var(--orange); color: var(--orange); }
  .chip.active { background: var(--orange); border-color: var(--orange); color: #fff; }
  .chip.teal.active  { background: var(--teal);  border-color: var(--teal); }
  .chip.cost.active  { background: #2C6E49; border-color: #2C6E49; }

  /* ── Grid ── */
  .grid-wrap { padding: 1.5rem 2rem 3rem; }
  .grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 1.25rem;
  }
  @media (max-width: 1100px) { .grid { grid-template-columns: repeat(2, 1fr); } }
  @media (max-width: 680px)  { .grid { grid-template-columns: 1fr; } }

  .no-results {
    grid-column: 1/-1;
    text-align: center;
    padding: 4rem 1rem;
    color: var(--muted);
  }
  .no-results span { font-size: 3rem; display: block; margin-bottom: 1rem; }

  /* ── Card ── */
  .card {
    background: var(--card-bg);
    border-radius: var(--radius);
    border: 1px solid var(--border);
    box-shadow: var(--shadow);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    transition: box-shadow .2s, transform .2s;
  }
  .card:hover { box-shadow: var(--shadow-hv); transform: translateY(-2px); }

  .card-accent {
    height: 4px;
    background: var(--orange);
  }
  .card[data-source="Luma (lu.ma)"] .card-accent { background: #6D28D9; }
  .card[data-source="10times.com"]  .card-accent { background: #0369A1; }
  .card[data-source="Eventbrite"]   .card-accent { background: #D97706; }
  .card[data-source="Austin Chamber of Commerce"] .card-accent { background: var(--teal); }

  .card-body { padding: 1rem 1.1rem .8rem; flex: 1; display: flex; flex-direction: column; gap: .55rem; }

  .card-badges { display: flex; gap: .4rem; flex-wrap: wrap; align-items: center; }
  .badge {
    font-size: .7rem;
    font-weight: 700;
    padding: .2rem .55rem;
    border-radius: 20px;
    letter-spacing: .3px;
    text-transform: uppercase;
  }
  .badge-source { background: var(--orange-lt); color: var(--orange); }
  .card[data-source="Luma (lu.ma)"]  .badge-source { background: #EDE9FE; color: #6D28D9; }
  .card[data-source="10times.com"]   .badge-source { background: #E0F2FE; color: #0369A1; }
  .card[data-source="Eventbrite"]    .badge-source { background: #FEF3C7; color: #B45309; }
  .card[data-source="Austin Chamber of Commerce"] .badge-source { background: var(--teal-lt); color: var(--teal); }

  .badge-free { background: #DCFCE7; color: #166534; }
  .badge-paid { background: #FEE2E2; color: #991B1B; }
  .badge-unknown { background: #F3F4F6; color: #6B7280; }

  .card-name {
    font-size: 1rem;
    font-weight: 700;
    line-height: 1.35;
    color: var(--text);
  }

  .card-meta { display: flex; flex-direction: column; gap: .3rem; }
  .meta-row {
    display: flex;
    align-items: flex-start;
    gap: .5rem;
    font-size: .82rem;
    color: var(--muted);
  }
  .meta-row svg { flex-shrink: 0; margin-top: .1rem; }
  .meta-row span { line-height: 1.4; }

  .card-footer { padding: .8rem 1.1rem 1rem; }
  .btn-event {
    display: block;
    text-align: center;
    background: var(--orange);
    color: #fff;
    font-weight: 700;
    font-size: .85rem;
    padding: .55rem 1rem;
    border-radius: 7px;
    text-decoration: none;
    transition: background .15s, opacity .15s;
    letter-spacing: .2px;
  }
  .btn-event:hover { background: #A34800; }
  .btn-event.disabled {
    background: #D1C5BB;
    cursor: default;
    pointer-events: none;
  }
  .card[data-source="Luma (lu.ma)"] .btn-event  { background: #6D28D9; }
  .card[data-source="Luma (lu.ma)"] .btn-event:hover { background: #5B21B6; }
  .card[data-source="10times.com"]  .btn-event  { background: #0369A1; }
  .card[data-source="10times.com"]  .btn-event:hover { background: #025883; }
  .card[data-source="Eventbrite"]   .btn-event  { background: #D97706; }
  .card[data-source="Eventbrite"]   .btn-event:hover { background: #B45309; }
  .card[data-source="Austin Chamber of Commerce"] .btn-event { background: var(--teal); }
  .card[data-source="Austin Chamber of Commerce"] .btn-event:hover { background: #005856; }

  /* ── Footer ── */
  footer {
    text-align: center;
    padding: 1.5rem;
    font-size: .8rem;
    color: var(--muted);
    border-top: 1px solid var(--border);
  }
</style>
</head>
<body>

<header>
  <h1>Austin <span>Events</span> & Conferences</h1>
  <p>Showing <span id="event-count">__COUNT__</span> upcoming events · Updated __DATE__</p>
</header>

<div class="controls">
  <div class="search-wrap">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>
    </svg>
    <input type="text" id="search" placeholder="Search events by name…" autocomplete="off">
  </div>

  <div class="chip-group" id="source-chips">
    <span class="chip active" data-source="all">All Sources</span>
    __SOURCE_CHIPS__
  </div>

  <div class="chip-group" id="cost-chips">
    <span class="chip cost active" data-cost="all">Any Cost</span>
    <span class="chip cost" data-cost="Free">Free</span>
    <span class="chip cost" data-cost="paid">Paid</span>
  </div>
</div>

<div class="grid-wrap">
  <div class="grid" id="grid">
    <!-- cards injected by JS -->
  </div>
</div>

<footer>
  Austin Events &amp; Conferences · Data from Luma, Meetup, 10times, Eventbrite &amp; Austin Chamber of Commerce
</footer>

<script>
const EVENTS = __JSON__;

function costBadge(cost) {
  if (!cost || cost === "Unknown") return `<span class="badge badge-unknown">?</span>`;
  if (cost === "Free") return `<span class="badge badge-free">Free</span>`;
  return `<span class="badge badge-paid">${cost}</span>`;
}

function icon(type) {
  const icons = {
    cal:  `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>`,
    loc:  `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z"/><circle cx="12" cy="10" r="3"/></svg>`,
    tag:  `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.59 13.41l-7.17 7.17a2 2 0 01-2.83 0L2 12V2h10l8.59 8.59a2 2 0 010 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>`,
    user: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`,
  };
  return icons[type] || "";
}

function makeCard(ev) {
  const venue = [ev.venue, ev.address].filter(Boolean).join(" · ") || "Austin, TX";
  const venueShort = venue.length > 55 ? venue.slice(0, 53) + "…" : venue;
  const nameDisplay = ev.name.length > 70 ? ev.name.slice(0, 68) + "…" : ev.name;
  const hasURL = ev.url && ev.url.trim() !== "";
  const btnClass = hasURL ? "" : " disabled";
  const btnHref  = hasURL ? `href="${ev.url}" target="_blank" rel="noopener"` : "";

  return `
<div class="card" data-source="${ev.source_primary}" data-cost="${ev.cost}" data-name="${ev.name.toLowerCase()}">
  <div class="card-accent"></div>
  <div class="card-body">
    <div class="card-badges">
      <span class="badge badge-source">${ev.source_primary}</span>
      ${costBadge(ev.cost)}
    </div>
    <div class="card-name">${nameDisplay}</div>
    <div class="card-meta">
      <div class="meta-row">${icon("cal")}<span>${ev.date_label || "Date TBD"}</span></div>
      ${ev.category ? `<div class="meta-row">${icon("tag")}<span>${ev.category}</span></div>` : ""}
      <div class="meta-row">${icon("loc")}<span>${venueShort}</span></div>
      ${ev.organizer ? `<div class="meta-row">${icon("user")}<span>${ev.organizer}</span></div>` : ""}
    </div>
  </div>
  <div class="card-footer">
    <a class="btn-event${btnClass}" ${btnHref}>View Event →</a>
  </div>
</div>`;
}

// ── State & rendering ──
let activeSource = "all";
let activeCost   = "all";
let searchQuery  = "";

function visible(ev) {
  if (activeSource !== "all" && ev.source_primary !== activeSource) return false;
  if (activeCost === "Free" && ev.cost !== "Free") return false;
  if (activeCost === "paid" && (ev.cost === "Free" || ev.cost === "Unknown" || !ev.cost)) return false;
  if (searchQuery && !ev.name.toLowerCase().includes(searchQuery)) return false;
  return true;
}

function render() {
  const filtered = EVENTS.filter(visible);
  document.getElementById("event-count").textContent = filtered.length.toLocaleString();
  const grid = document.getElementById("grid");
  if (filtered.length === 0) {
    grid.innerHTML = `<div class="no-results"><span>🔍</span>No events match your filters.<br>Try adjusting your search or clearing a filter.</div>`;
    return;
  }
  grid.innerHTML = filtered.map(makeCard).join("");
}

// ── Source chips ──
document.querySelectorAll("#source-chips .chip").forEach(chip => {
  chip.addEventListener("click", () => {
    document.querySelectorAll("#source-chips .chip").forEach(c => c.classList.remove("active"));
    chip.classList.add("active");
    activeSource = chip.dataset.source;
    render();
  });
});

// ── Cost chips ──
document.querySelectorAll("#cost-chips .chip").forEach(chip => {
  chip.addEventListener("click", () => {
    document.querySelectorAll("#cost-chips .chip").forEach(c => c.classList.remove("active"));
    chip.classList.add("active");
    activeCost = chip.dataset.cost;
    render();
  });
});

// ── Search ──
document.getElementById("search").addEventListener("input", e => {
  searchQuery = e.target.value.toLowerCase().trim();
  render();
});

// ── Init ──
render();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(csv_path: str = CSV_FILE, html_path: str = HTML_FILE):
    if not os.path.exists(csv_path):
        print(f"ERROR: {csv_path} not found. Run fetch_conferences.py first.")
        sys.exit(1)

    print(f"Reading {csv_path}…")
    events = load_events(csv_path)
    print(f"  {len(events)} events loaded.")

    # Unique sources for chip buttons
    sources = []
    seen_src = set()
    for ev in events:
        s = ev["source_primary"]
        if s not in seen_src:
            seen_src.add(s)
            sources.append(s)
    # Sort by predefined order
    sources.sort(key=lambda s: SOURCE_ORDER.index(s) if s in SOURCE_ORDER else 99)

    source_chips_html = "\n    ".join(
        f'<span class="chip" data-source="{s}">{s}</span>' for s in sources
    )

    today = datetime.now().strftime("%b %-d, %Y")
    json_blob = json.dumps(events, ensure_ascii=False)

    html = (HTML_TEMPLATE
            .replace("__COUNT__",        str(len(events)))
            .replace("__DATE__",         today)
            .replace("__SOURCE_CHIPS__", source_chips_html)
            .replace("__JSON__",         json_blob))

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  Written → {html_path}")
    print(f"\nDone! Open {html_path} in your browser, or upload it to any web host.")


if __name__ == "__main__":
    build()
