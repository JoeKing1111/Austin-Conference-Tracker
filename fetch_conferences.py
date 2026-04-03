#!/usr/bin/env python3
"""
fetch_conferences.py
Aggregates Austin, TX conference and event data from:
  1. Luma (lu.ma) — public API, no key required
  2. Meetup.com  — public GraphQL API, no key required
  3. Austin Chamber of Commerce — server-rendered HTML scrape

The original sources (Eventbrite API, 10times, AllConferences) were replaced
because Eventbrite deprecated its public search API, 10times blocks automated
requests, and AllConferences.com is a parked domain.

Usage:
    python fetch_conferences.py --apikey ANY_VALUE

Output:
    austin_conferences.csv   — deduplicated, non-destructive merge with existing data
    errors.log               — any source failures with reasons
"""

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OUTPUT_CSV = "austin_conferences.csv"
ERROR_LOG  = "errors.log"

CSV_COLUMNS = [
    "Conference Name",
    "Industry / Category",
    "Start Date",
    "End Date",
    "Venue Name",
    "Address",
    "Cost",
    "Website URL",
    "Organizer Name",
    "Contact Email",
    "Contact Phone",
    "CFP / Talk Submission URL",
    "Source",
]

EMPTY_ROW = {col: "" for col in CSV_COLUMNS}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    fh = logging.FileHandler(ERROR_LOG, mode="w", encoding="utf-8")
    fh.setLevel(logging.WARNING)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.getLogger().addHandler(fh)


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

JSON_HEADERS = {
    **BROWSER_HEADERS,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json",
    "X-Requested-With": "XMLHttpRequest",
}


def fetch(url, *, method="GET", headers=None, json_body=None, params=None,
          max_retries=4, backoff=2.0, timeout=20):
    h = {**BROWSER_HEADERS, **(headers or {})}
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.request(
                method, url, params=params, headers=h,
                json=json_body, timeout=timeout
            )
            if resp.status_code == 429:
                wait = backoff ** attempt
                logger.warning("Rate-limited on %s — waiting %.1fs (attempt %d/%d)",
                               url, wait, attempt, max_retries)
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                wait = backoff ** attempt
                logger.warning("Server error %d on %s — waiting %.1fs",
                               resp.status_code, url, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.exceptions.ConnectionError as exc:
            wait = backoff ** attempt
            logger.warning("Connection error on %s: %s — retrying in %.1fs", url, exc, wait)
            time.sleep(wait)
        except requests.exceptions.Timeout:
            wait = backoff ** attempt
            logger.warning("Timeout on %s — retrying in %.1fs", url, wait)
            time.sleep(wait)
    raise RuntimeError(f"All {max_retries} attempts failed for {url}")


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def parse_date(raw: str) -> str:
    if not raw:
        return ""
    raw = raw.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y",
        "%d %B %Y", "%d %b %Y", "%m/%d/%Y",
        "%B %d %Y", "%b %d %Y",
    ):
        try:
            s = raw[:len(fmt) + 6].strip()
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    clean = re.sub(r"[TZ].*$", "", raw).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime(clean, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return raw


def _parse_date_range(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    text = text.strip()
    # "15 Apr 2025 - 17 Apr 2025"
    m = re.search(r"(\d{1,2}[- /]\w+[- /]\d{4})\s*[-–]\s*(\d{1,2}[- /]\w+[- /]\d{4})", text)
    if m:
        return parse_date(m.group(1)), parse_date(m.group(2))
    # "Apr 15-17, 2025"
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2})-(\d{1,2}),?\s+(\d{4})", text)
    if m:
        mo, d1, d2, yr = m.group(1), m.group(2), m.group(3), m.group(4)
        return parse_date(f"{mo} {d1}, {yr}"), parse_date(f"{mo} {d2}, {yr}")
    # "15-17 Apr 2025"
    m = re.match(r"(\d{1,2})-(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if m:
        d1, d2, mo, yr = m.group(1), m.group(2), m.group(3), m.group(4)
        return parse_date(f"{mo} {d1}, {yr}"), parse_date(f"{mo} {d2}, {yr}")
    single = parse_date(text)
    return single, ""


def _parse_chamber_date(text: str) -> tuple[str, str]:
    """Parse Austin Chamber date: 'Wed, Apr 15 at 3:30 PM - Thu, Apr 16 at 5 PM'"""
    if not text:
        return "", ""
    year = datetime.now(tz=timezone.utc).year
    matches = re.findall(r'(\w{3},\s+\w{3}\s+\d{1,2})', text)
    if not matches:
        return "", ""
    start = parse_date(f"{matches[0]}, {year}")
    end   = parse_date(f"{matches[-1]}, {year}") if len(matches) > 1 else start
    return start, end


# ---------------------------------------------------------------------------
# SOURCE 1: Luma (lu.ma) — parse __NEXT_DATA__ SSR JSON from the page HTML
# ---------------------------------------------------------------------------

LUMA_PAGE_URL = "https://luma.com/austin"


def fetch_luma() -> list[dict]:
    """
    Pull Austin events from Luma by parsing the __NEXT_DATA__ JSON blob
    that Next.js embeds server-side in the luma.com/austin page HTML.
    No API key required — no API call at all; the data is in the raw HTML.
    """
    source = "Luma (lu.ma)"
    logger.info("=== Fetching from %s ===", source)
    records = []
    seen: set[str] = set()
    now = datetime.now(tz=timezone.utc)

    try:
        resp = fetch(LUMA_PAGE_URL, headers=BROWSER_HEADERS)
    except Exception as exc:
        logger.error("[%s] Failed to fetch page: %s", source, exc)
        return records

    soup = BeautifulSoup(resp.text, "html.parser")
    script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script_tag:
        logger.error("[%s] Could not find __NEXT_DATA__ script tag in page HTML", source)
        return records

    try:
        next_data = json.loads(script_tag.string)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.error("[%s] Failed to parse __NEXT_DATA__ JSON: %s", source, exc)
        return records

    # Navigate to the event list:
    #   props → pageProps → initialData → data → events / featured_events
    try:
        data = next_data["props"]["pageProps"]["initialData"]["data"]
    except (KeyError, TypeError) as exc:
        logger.error("[%s] Unexpected __NEXT_DATA__ structure: %s", source, exc)
        logger.debug("[%s] Top-level keys: %s", source, list(next_data.keys()))
        return records

    all_entries = list(data.get("events", [])) + list(data.get("featured_events", []))
    logger.info("[%s] Found %d event entries in __NEXT_DATA__", source, len(all_entries))

    for entry in all_entries:
        # Outer entry is a wrapper; real event data lives in entry["event"]
        ev = entry.get("event") if isinstance(entry, dict) and "event" in entry else entry
        if not isinstance(ev, dict):
            continue

        ev_id = ev.get("api_id") or ev.get("id") or ""
        if ev_id and ev_id in seen:
            continue
        if ev_id:
            seen.add(ev_id)

        name = ev.get("name") or ev.get("title") or ""
        if not name:
            continue

        # start_at may be on the outer entry (more reliable) or the inner event
        start_raw = entry.get("start_at") or ev.get("start_at") or ""
        end_raw   = ev.get("end_at") or ""

        # Skip events that have already started
        if start_raw:
            try:
                dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                if dt < now:
                    continue
            except Exception:
                pass

        # Venue / address — lives in geo_address_info on the event object
        geo = ev.get("geo_address_info") or {}
        venue_name  = geo.get("place") or geo.get("name") or ""
        full_address = geo.get("full_address") or ""
        address = full_address or "Austin, TX"

        # URL: slug is the path component after lu.ma/
        slug = ev.get("url") or ev.get("slug") or ev_id
        url  = f"https://lu.ma/{slug}" if slug else ""

        # Organizer from hosts list on the outer entry wrapper
        hosts = entry.get("hosts") or []
        organizer = ", ".join(h.get("name", "") for h in hosts if h.get("name"))

        # Cost from ticket_info on the outer entry wrapper
        ticket_info = entry.get("ticket_info") or {}
        is_free = ticket_info.get("is_free")
        price   = ticket_info.get("price")
        if is_free is True or str(is_free).lower() == "true":
            cost = "Free"
        elif price:
            cost = f"Paid — {price}"
        else:
            ttype = ev.get("ticket_type") or ""
            if ttype.lower() in ("free", "none", ""):
                cost = "Free"
            elif ttype.lower() == "paid":
                cost = "Paid"
            else:
                cost = "Unknown"

        # Tags / category
        tags = ev.get("tags") or []
        if isinstance(tags, list):
            category = ", ".join(
                t if isinstance(t, str) else (t.get("label") or t.get("name") or "")
                for t in tags
            ).strip(", ")
        else:
            category = str(tags) if tags else ""

        row = {**EMPTY_ROW}
        row["Conference Name"]     = name
        row["Industry / Category"] = category or "Business & Professional"
        row["Start Date"]          = parse_date(start_raw)
        row["End Date"]            = parse_date(end_raw)
        row["Venue Name"]          = venue_name
        row["Address"]             = address
        row["Cost"]                = cost
        row["Website URL"]         = url
        row["Organizer Name"]      = organizer
        row["Source"]              = source
        records.append(row)

    logger.info("[%s] Total records collected: %d", source, len(records))
    return records


# ---------------------------------------------------------------------------
# SOURCE 2: Meetup.com — GraphQL API (Austin professional groups)
# ---------------------------------------------------------------------------

MEETUP_GQL_URL = "https://www.meetup.com/gql2"

# Verified working query (Apr 2026) — uses eventSearch with EventSearchFilter!
# Date format must include IANA zone suffix e.g. "2026-04-03T00:00:00-05:00[US/Central]"
_MEETUP_QUERY = """
query eventSearch($filter: EventSearchFilter!, $sort: KeywordSort, $after: String) {
  eventSearch(filter: $filter, sort: $sort, after: $after) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        id
        title
        dateTime
        endTime
        eventUrl
        isOnline
        venue { name address city state postalCode }
        group { name urlname }
        feeSettings { amount currency }
      }
    }
  }
}
"""

# Search terms — each is a separate request; results are merged + deduped
_MEETUP_QUERIES = [
    "conference", "summit", "symposium", "expo",
    "workshop", "hackathon", "tech meetup",
]


def fetch_meetup() -> list[dict]:
    """
    Pull Austin conference/professional events from Meetup's GraphQL API.
    Endpoint: https://www.meetup.com/gql2 (verified Apr 2026, no auth required).
    """
    source = "Meetup.com"
    logger.info("=== Fetching from %s ===", source)
    records = []
    seen: set[str] = set()
    now = datetime.now(tz=timezone.utc)

    # Meetup requires a zoned datetime string with IANA zone suffix
    start_date_range = now.strftime("%Y-%m-%dT%H:%M:%S-05:00[US/Central]")

    for query_term in _MEETUP_QUERIES:
        cursor = None
        page = 0
        max_pages = 3

        while page < max_pages:
            variables = {
                "filter": {
                    "query": query_term,
                    "city": "Austin", "state": "TX", "country": "us",
                    "lat": 30.2672, "lon": -97.7431,
                    "doConsolidateEvents": True,
                    "startDateRange": start_date_range,
                },
                "sort": {"sortField": "RELEVANCE"},
            }
            if cursor:
                variables["after"] = cursor

            try:
                resp = fetch(
                    MEETUP_GQL_URL,
                    method="POST",
                    headers={
                        **JSON_HEADERS,
                        "Origin": "https://www.meetup.com",
                        "Referer": (
                            "https://www.meetup.com/find/"
                            "?keywords=conference&location=us--tx--Austin"
                        ),
                    },
                    json_body={
                        "operationName": "eventSearch",
                        "query": _MEETUP_QUERY,
                        "variables": variables,
                    },
                )
                data = resp.json()
            except Exception as exc:
                logger.error("[%s] Request failed (query=%s, page=%d): %s",
                             source, query_term, page + 1, exc)
                break

            if data.get("errors"):
                for err in data["errors"]:
                    logger.error("[%s] GraphQL error: %s", source, err.get("message"))
                break

            result    = data.get("data", {}).get("eventSearch", {})
            edges     = result.get("edges", [])
            page_info = result.get("pageInfo", {})

            logger.info("[%s] query='%s' page=%d — %d edges",
                        source, query_term, page + 1, len(edges))

            for edge in edges:
                ev = edge.get("node") or {}
                ev_id = ev.get("id", "")
                if not ev_id or ev_id in seen:
                    continue
                seen.add(ev_id)

                name = ev.get("title", "")
                if not name:
                    continue

                start_raw = ev.get("dateTime", "")
                end_raw   = ev.get("endTime", "")

                venue = ev.get("venue") or {}
                vname = venue.get("name", "")
                addr  = ", ".join(filter(None, [
                    venue.get("address", ""), venue.get("city", ""),
                    venue.get("state", ""), venue.get("postalCode", ""),
                ])) or "Austin, TX"

                fee = ev.get("feeSettings") or {}
                if fee.get("amount"):
                    cost = f"Paid — {fee['amount']} {fee.get('currency', '')}".strip()
                else:
                    cost = "Free"

                group     = ev.get("group") or {}
                organizer = group.get("name", "")
                url       = ev.get("eventUrl", "")

                row = {**EMPTY_ROW}
                row["Conference Name"]     = name
                row["Industry / Category"] = "Business & Professional"
                row["Start Date"]          = parse_date(start_raw)
                row["End Date"]            = parse_date(end_raw)
                row["Venue Name"]          = vname
                row["Address"]             = addr
                row["Cost"]                = cost
                row["Website URL"]         = url
                row["Organizer Name"]      = organizer
                row["Source"]              = source
                records.append(row)

            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            page += 1
            time.sleep(0.5)

        time.sleep(0.5)

    logger.info("[%s] Total records collected: %d", source, len(records))
    return records


# ---------------------------------------------------------------------------
# SOURCE 3: Austin Chamber of Commerce — server-rendered HTML
# ---------------------------------------------------------------------------

AUSTIN_CHAMBER_URL = "https://www.austinchamber.com/events"


def fetch_austin_chamber() -> list[dict]:
    """
    Scrape upcoming events from the Austin Chamber of Commerce events page.
    Page structure (verified Apr 2026):
      <main> contains event <li> items each with a heading and date text.
    """
    source = "Austin Chamber of Commerce"
    logger.info("=== Fetching from %s ===", source)
    records = []
    seen: set[str] = set()

    page = 1
    while True:
        url = AUSTIN_CHAMBER_URL if page == 1 else f"{AUSTIN_CHAMBER_URL}?page={page}"
        try:
            resp = fetch(url)
        except Exception as exc:
            logger.error("[%s] Failed page %d: %s", source, page, exc)
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        main_el = soup.find("main") or soup

        # Find all <li> that contain a heading — those are event cards
        cards = [li for li in main_el.find_all("li") if li.find(["h1", "h2", "h3"])]

        if not cards:
            logger.warning("[%s] No event cards on page %d — page may be JS-rendered.", source, page)
            logger.debug("[%s] HTML snippet: %s", source, resp.text[:500])
            break

        new_this_page = 0
        for card in cards:
            name_el = card.find(["h1", "h2", "h3"])
            name = name_el.get_text(strip=True) if name_el else ""
            if not name or name in seen:
                continue
            seen.add(name)

            # URL — link with /events/ in href
            url_el = card.find("a", href=re.compile(r"/events/"))
            href = ""
            if url_el:
                href = url_el.get("href", "")
                if href and not href.startswith("http"):
                    href = "https://www.austinchamber.com" + href

            # Date — element containing day-of-week pattern
            date_text = ""
            for el in card.find_all(["p", "span", "div", "time", "small", "em"]):
                t = el.get_text(strip=True)
                if re.search(r'\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+\w+\s+\d+\s+at\b', t):
                    date_text = t
                    break
            start_date, end_date = _parse_chamber_date(date_text)

            # Contact email in card text
            full_text = card.get_text(" ", strip=True)
            email_m = re.search(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}', full_text)
            contact_email = email_m.group(0) if email_m else ""

            row = {**EMPTY_ROW}
            row["Conference Name"]     = name
            row["Industry / Category"] = "Business & Professional"
            row["Start Date"]          = start_date
            row["End Date"]            = end_date
            row["Address"]             = "Austin, TX"
            row["Cost"]                = "Unknown"
            row["Website URL"]         = href
            row["Organizer Name"]      = "Austin Chamber of Commerce"
            row["Contact Email"]       = contact_email
            row["Source"]              = source
            records.append(row)
            new_this_page += 1

        logger.info("[%s] Page %d — %d records", source, page, new_this_page)
        if new_this_page == 0:
            break

        next_link = (soup.select_one("a[rel='next']")
                     or soup.select_one(".pager-next a")
                     or soup.select_one("li.next a"))
        if not next_link:
            break
        page += 1
        time.sleep(1.0)

    logger.info("[%s] Total records collected: %d", source, len(records))
    return records


# ---------------------------------------------------------------------------
# Deduplication & merge
# ---------------------------------------------------------------------------

def _count_populated(row: dict) -> int:
    return sum(1 for k, v in row.items() if k != "Source" and str(v).strip())


def _merge_rows(primary: dict, secondary: dict) -> dict:
    merged = {**primary}
    for col in CSV_COLUMNS:
        if col == "Source":
            sources = set()
            for s in (primary.get("Source",""), secondary.get("Source","")):
                for p in s.split(","):
                    p = p.strip()
                    if p:
                        sources.add(p)
            merged["Source"] = ", ".join(sorted(sources))
        elif not merged.get(col) and secondary.get(col):
            merged[col] = secondary[col]
    return merged


def deduplicate(records: list[dict]) -> list[dict]:
    logger.info("Deduplicating %d records...", len(records))
    def key(r):
        name = re.sub(r"\s+", " ", r.get("Conference Name","").strip().lower())
        return (name, r.get("Start Date","").strip())

    seen: dict[tuple, dict] = {}
    for row in records:
        k = key(row)
        if not k[0]:
            continue
        if k not in seen:
            seen[k] = row
        else:
            ex = seen[k]
            if _count_populated(row) > _count_populated(ex):
                seen[k] = _merge_rows(row, ex)
            else:
                seen[k] = _merge_rows(ex, row)

    result = list(seen.values())
    logger.info("After dedup: %d unique records.", len(result))
    return result


# ---------------------------------------------------------------------------
# CSV I/O — non-destructive: existing manual data is never overwritten
# ---------------------------------------------------------------------------

def load_existing_csv(path: str) -> dict[tuple, dict]:
    existing = {}
    if not os.path.exists(path):
        return existing
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = re.sub(r"\s+", " ", row.get("Conference Name","").strip().lower())
            date = row.get("Start Date","").strip()
            existing[(name, date)] = row
    logger.info("Loaded %d existing rows from %s", len(existing), path)
    return existing


def save_csv(records: list[dict], path: str, existing: dict[tuple, dict]):
    final: dict[tuple, dict] = {k: v for k, v in existing.items()}

    for row in records:
        name = re.sub(r"\s+", " ", row.get("Conference Name","").strip().lower())
        date = row.get("Start Date","").strip()
        k = (name, date)
        if k in final:
            ex = final[k]
            for col in CSV_COLUMNS:
                if col == "Source":
                    src_ex  = set(s.strip() for s in ex.get(col,"").split(",") if s.strip())
                    src_new = set(s.strip() for s in row.get(col,"").split(",") if s.strip())
                    ex[col] = ", ".join(sorted(src_ex | src_new))
                elif not ex.get(col):
                    ex[col] = row.get(col,"")
            final[k] = ex
        else:
            final[k] = {col: row.get(col,"") for col in CSV_COLUMNS}

    sorted_rows = sorted(
        final.values(),
        key=lambda r: (r.get("Start Date","") or "9999", r.get("Conference Name","").lower()),
    )
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted_rows)
    logger.info("Wrote %d rows to %s", len(sorted_rows), path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Fetch Austin, TX conference/event data from Luma, Meetup, "
            "and Austin Chamber of Commerce. Merges non-destructively into "
            "an existing CSV (manual edits are never overwritten)."
        )
    )
    parser.add_argument(
        "--apikey",
        required=True,
        help="Kept for compatibility — not used (Eventbrite API was deprecated). Pass any value.",
    )
    parser.add_argument("--output", default=OUTPUT_CSV,
                        help=f"Output CSV (default: {OUTPUT_CSV})")
    parser.add_argument("--skip-luma",    action="store_true", help="Skip Luma scraper.")
    parser.add_argument("--skip-meetup",  action="store_true", help="Skip Meetup scraper.")
    parser.add_argument("--skip-chamber", action="store_true",
                        help="Skip Austin Chamber scraper.")
    args = parser.parse_args()

    setup_logging()
    logger.info("Starting Austin conference aggregator (v3 — Luma + Meetup + Chamber)...")
    logger.info("Output: %s | Error log: %s", args.output, ERROR_LOG)

    all_records: list[dict] = []

    if not args.skip_luma:
        try:
            all_records.extend(fetch_luma())
        except Exception as exc:
            logger.error("[Luma] Fatal: %s", exc)

    if not args.skip_meetup:
        try:
            all_records.extend(fetch_meetup())
        except Exception as exc:
            logger.error("[Meetup] Fatal: %s", exc)

    if not args.skip_chamber:
        try:
            all_records.extend(fetch_austin_chamber())
        except Exception as exc:
            logger.error("[Austin Chamber] Fatal: %s", exc)

    if not all_records:
        logger.warning("No records collected. Check %s for details.", ERROR_LOG)
    else:
        deduped  = deduplicate(all_records)
        existing = load_existing_csv(args.output)
        save_csv(deduped, args.output, existing)
        logger.info("Done! %s is ready to import into Google Sheets.", args.output)

    logger.info("Error log: %s", ERROR_LOG)


if __name__ == "__main__":
    main()
