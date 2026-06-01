"""
job-hunter/scripts/discover_yc.py

Brute-force ATS discovery using Y Combinator's full company list.

How it works:
  1. Fetches all ~5,900+ YC companies from the free yc-oss public JSON API
  2. Converts each company name into likely ATS slug variations
  3. Probes Greenhouse, Lever, and Ashby for each slug
  4. Saves every confirmed hit into your companies DB

Usage:
    python scripts/discover_yc.py                  # full run
    python scripts/discover_yc.py --resume         # continue interrupted run
    python scripts/discover_yc.py --limit 50       # test with first 50 companies
    python scripts/discover_yc.py --delay 0.3      # faster (riskier)
    python scripts/discover_yc.py --delay 1.0      # slower (safer)
    python scripts/discover_yc.py --status Active  # only active YC companies
"""

import urllib.request
import urllib.error
import sqlite3
import json
import time
import re
import os
import sys
import argparse
import socket
import ssl
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fetcher import init_db, DB_PATH

# ─────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────

YC_API_URL      = "https://yc-oss.github.io/api/companies/all.json"
CHECKPOINT_FILE = "data/yc_probed.json"

# All exceptions we want to silently swallow during probing
# Covers: HTTP errors, network errors, timeouts at every SSL/socket layer
SAFE_EXCEPTIONS = (
    urllib.error.HTTPError,
    urllib.error.URLError,
    TimeoutError,
    socket.timeout,
    ssl.SSLError,
    ConnectionResetError,
    ConnectionRefusedError,
    OSError,
    Exception,   # catch-all safety net
)


# ─────────────────────────────────────────────────────────────────
# SLUG GENERATOR
# ─────────────────────────────────────────────────────────────────

def generate_slug_variations(name, yc_slug):
    candidates = []
    seen = set()

    def add(s):
        s = s.strip().lower()
        if s and 2 <= len(s) <= 60 and not s.isdigit() and s not in seen:
            seen.add(s)
            candidates.append(s)

    if yc_slug:
        add(yc_slug)

    def normalize(s):
        s = s.lower()
        s = re.sub(r"[''`]", "", s)
        s = re.sub(r"[&+]", "and", s)
        s = re.sub(r"[^a-z0-9\s-]", "", s)
        return s.strip()

    base = normalize(name)

    add(re.sub(r"\s+", "-", base))   # "scale ai" → "scale-ai"
    add(re.sub(r"\s+", "", base))    # "scale ai" → "scaleai"

    suffixes = [
        " inc", " inc.", " llc", " llc.", " ltd", " ltd.",
        " corp", " corp.", " co", " co.", " technologies",
        " technology", " solutions", " labs", " lab",
        " studio", " studios", " ai", " hq", " app",
    ]
    stripped = base
    for suffix in suffixes:
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)].strip()
            break

    if stripped and stripped != base:
        add(re.sub(r"\s+", "-", stripped))
        add(re.sub(r"\s+", "", stripped))

    words = base.split()
    if 2 <= len(words) <= 4:
        initials = "".join(w[0] for w in words if w)
        if len(initials) >= 2:
            add(initials)

    return candidates


# ─────────────────────────────────────────────────────────────────
# PROBE FUNCTIONS
# Each returns True (valid board) or False (anything else).
# Every possible exception is caught here AND at the call site.
# ─────────────────────────────────────────────────────────────────

HEADERS = {"User-Agent": "Mozilla/5.0 (job-hunter discovery script)"}


def probe_greenhouse(slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except SAFE_EXCEPTIONS:
        return False


def probe_lever(slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read().decode())
            return isinstance(body, list)
    except SAFE_EXCEPTIONS:
        return False


def probe_ashby(slug):
    url = "https://jobs.ashbyhq.com/api/non-user-graphql"
    payload = json.dumps({
        "operationName": "ApiJobBoardWithTeams",
        "variables": {"organizationHostedJobsPageName": slug},
        "query": "query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) { jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) { jobPostings { id } } }"
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            url, data=payload,
            headers={**HEADERS, "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read().decode())
            return body.get("data", {}).get("jobBoard") is not None
    except SAFE_EXCEPTIONS:
        return False


PROBERS = {
    "greenhouse": probe_greenhouse,
    "lever":      probe_lever,
    "ashby":      probe_ashby,
}


# ─────────────────────────────────────────────────────────────────
# HTTP HELPER
# ─────────────────────────────────────────────────────────────────

def fetch_json(url):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except SAFE_EXCEPTIONS as e:
        print(f"  ✗ Could not fetch {url}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────
# CHECKPOINT
# ─────────────────────────────────────────────────────────────────

def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE) as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_checkpoint(probed_set):
    os.makedirs(os.path.dirname(CHECKPOINT_FILE), exist_ok=True)
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(list(probed_set), f)


# ─────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────

def insert_company(conn, slug, ats, name):
    try:
        c = conn.cursor()
        c.execute(
            "SELECT slug FROM companies WHERE slug = ? AND ats = ?",
            (slug, ats)
        )
        if c.fetchone():
            return False
        c.execute(
            "INSERT INTO companies (slug, ats, name, valid) VALUES (?, ?, ?, 1)",
            (slug, ats, name)
        )
        conn.commit()
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────
# TIME HELPERS
# ─────────────────────────────────────────────────────────────────

def fmt_elapsed(start_time):
    total_s = int((datetime.now() - start_time).total_seconds())
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def fmt_eta(start_time, done, total):
    if done < 2:
        return "calculating..."
    elapsed = (datetime.now() - start_time).total_seconds()
    rate    = done / elapsed
    remaining_s = int((total - done) / rate)
    h = remaining_s // 3600
    m = (remaining_s % 3600) // 60
    if h > 0:
        return f"~{h}h {m:02d}m left"
    return f"~{m}m left"


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

def run(delay=0.2, resume=False, limit=None, status_filter=None):
    init_db()

    print(f"\n📡 Fetching YC company list...")
    companies = fetch_json(YC_API_URL)
    if not companies:
        print("❌ Could not fetch YC company list. Check your internet connection.")
        return

    print(f"   {len(companies)} total YC companies found")

    if status_filter:
        companies = [c for c in companies if c.get("status") == status_filter]
        print(f"   Filtered to {len(companies)} with status='{status_filter}'")

    if limit:
        companies = companies[:limit]
        print(f"   Limited to first {limit} for this run")

    probed = load_checkpoint() if resume else set()
    if resume and probed:
        before = len(companies)
        companies = [c for c in companies if c.get("slug") not in probed]
        print(f"   Resuming — skipping {before - len(companies)} already probed")

    total      = len(companies)
    conn       = sqlite3.connect(DB_PATH)
    found_total = 0
    start_time  = datetime.now()

    est_low  = int(total * 3 * delay / 60)
    est_high = int(total * 3 * delay / 60 * 1.5)
    print(f"\n🔍 Probing {total} companies across Greenhouse / Lever / Ashby")
    print(f"   {delay}s delay per request  |  Est: {est_low}–{est_high} min")
    print(f"   Ctrl+C anytime — progress saved every 50 companies\n")
    print("─" * 62)

    try:
        for i, company in enumerate(companies, 1):
            name    = company.get("name", "")
            yc_slug = company.get("slug", "")

            if i % 25 == 1:
                print(
                    f"  [{i}/{total}  {i/total*100:.1f}%  "
                    f"{fmt_elapsed(start_time)}  "
                    f"{fmt_eta(start_time, i, total)}]"
                    f"  found: {found_total}"
                )

            slugs = generate_slug_variations(name, yc_slug)

            for ats, prober in PROBERS.items():
                for slug in slugs:
                    # ── Triple-layered exception safety ──────────
                    # Catches anything that escapes the probe fn,
                    # including SSL-layer TimeoutError on Win/Py3.13
                    try:
                        result = prober(slug)
                    except SAFE_EXCEPTIONS:
                        result = False

                    try:
                        time.sleep(delay)
                    except KeyboardInterrupt:
                        raise   # let Ctrl+C bubble up to outer handler

                    if result:
                        try:
                            added = insert_company(conn, slug, ats, name)
                            if added:
                                print(f"  ✓ {ats:12s} {slug:32s} ← {name}")
                                found_total += 1
                        except Exception:
                            pass
                        break   # valid slug found for this ATS, next ATS

            probed.add(yc_slug)
            if i % 50 == 0:
                save_checkpoint(probed)

    except KeyboardInterrupt:
        print(f"\n\n⚠️  Interrupted by user — saving checkpoint...")
        save_checkpoint(probed)
        print(f"   Saved. Resume with: python scripts/discover_yc.py --resume")

    finally:
        save_checkpoint(probed)
        conn.close()

    elapsed = fmt_elapsed(start_time)
    print("\n" + "─" * 62)
    print(f"  ✅ Finished in {elapsed}")
    print(f"  ✓  {found_total} new ATS boards discovered and saved")
    print(f"  ·  Hit rate: {found_total/max(total,1)*100:.1f}%")
    print("─" * 62)
    if found_total > 0:
        print(f"\n  Next step: python fetcher.py\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Discover ATS boards for all YC-funded companies"
    )
    parser.add_argument(
        "--delay", type=float, default=0.5,
        help="Seconds between each probe request (default: 0.5)"
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip companies already probed in a previous run"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Only probe the first N companies (good for testing)"
    )
    parser.add_argument(
        "--status", type=str, default=None,
        choices=["Active", "Public", "Inactive", "Acquired"],
        help="Filter YC companies by status before probing"
    )
    args = parser.parse_args()

    run(
        delay=args.delay,
        resume=args.resume,
        limit=args.limit,
        status_filter=args.status,
    )
