"""
job-hunter/scripts/seed_companies.py

Seeds the companies table by pulling ATS slugs LIVE from community
GitHub repos that track entry-level / new-grad job postings.

Sources:
  - SimplifyJobs/New-Grad-Positions        (new grad full-time)
  - SimplifyJobs/Summer2025-Internships    (internships)
  - coderQuad/New-Grad-Positions-2023      (extra coverage)
  - pittcsc/Summer2024-Internships         (extra coverage)

Slugs are extracted by regex from Greenhouse, Lever, and Ashby URLs
embedded in the markdown tables of those repos — no API keys needed.

Usage:
    python scripts/seed_companies.py              # normal run
    python scripts/seed_companies.py --dry-run    # preview without writing
    python scripts/seed_companies.py --probe      # validate slugs first (slower)
"""

import urllib.request
import urllib.error
import sqlite3
import re
import sys
import os
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fetcher import init_db, DB_PATH


# ─────────────────────────────────────────────────────────────────
# SOURCES
# Raw GitHub markdown URLs. Each is scraped for ATS URLs.
# Add more rows here as you discover other useful repos.
# ─────────────────────────────────────────────────────────────────

SOURCES = [
    {
        "name": "SimplifyJobs — New Grad Positions",
        "url":  "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md",
    },
    {
        "name": "SimplifyJobs — Summer 2025 Internships",
        "url":  "https://raw.githubusercontent.com/SimplifyJobs/Summer2025-Internships/dev/README.md",
    },
    {
        "name": "coderQuad — New Grad 2023",
        "url":  "https://raw.githubusercontent.com/coderQuad/New-Grad-Positions-2023/main/README.md",
    },
    {
        "name": "pittcsc — Summer 2024 Internships",
        "url":  "https://raw.githubusercontent.com/pittcsc/Summer2024-Internships/dev/README.md",
    },
    # ── Add more raw markdown URLs here ──────────────────────────
    # {
    #     "name": "Your custom list",
    #     "url":  "https://raw.githubusercontent.com/you/repo/main/companies.md",
    # },
]


# ─────────────────────────────────────────────────────────────────
# REGEX PATTERNS
# Captures the company slug (first path segment) from ATS URLs.
# ─────────────────────────────────────────────────────────────────

PATTERNS = {
    "greenhouse": [
        # https://boards.greenhouse.io/stripe
        # https://boards-api.greenhouse.io/v1/boards/stripe/jobs
        r'boards(?:-api)?\.greenhouse\.io/(?:v\d+/boards/)?([a-zA-Z0-9_-]+)',
    ],
    "lever": [
        # https://jobs.lever.co/scale-ai
        r'jobs\.lever\.co/([a-zA-Z0-9_-]+)',
    ],
    "ashby": [
        # https://jobs.ashbyhq.com/linear
        r'jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)',
    ],
}

# These are ATS path segments or generic words, not real company slugs
BLOCKLIST = {
    "v1", "v2", "v3", "boards", "jobs", "api", "postings",
    "embed", "careers", "apply", "index", "search", "feed", "rss",
    "all", "eng", "engineering", "design", "product", "sales",
    "template", "example", "demo", "test", "staging", "content",
}


# ─────────────────────────────────────────────────────────────────
# FETCH HELPERS
# ─────────────────────────────────────────────────────────────────

def fetch_text(url):
    """Fetch URL and return text, or None on failure."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (job-hunter seed script)"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        print(f"    ⚠️  Fetch failed: {e}")
        return None


def probe_slug(slug, ats):
    """
    Hit the ATS API and confirm the slug returns a real board (not 404).
    Returns True = valid, False = dead slug.
    Ashby probing requires a POST so we skip it and assume valid.
    """
    probe_urls = {
        "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        "lever":      f"https://api.lever.co/v0/postings/{slug}?mode=json",
    }
    url = probe_urls.get(ats)
    if not url:
        return True  # Ashby: skip probe, assume valid

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (job-hunter seed script)"}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        return e.code != 404
    except urllib.error.URLError:
        return False


# ─────────────────────────────────────────────────────────────────
# EXTRACTION
# ─────────────────────────────────────────────────────────────────

def extract_slugs_from_text(text):
    """
    Run all ATS regex patterns against raw markdown text.
    Returns { "greenhouse": {slug, ...}, "lever": {...}, "ashby": {...} }
    """
    found = {ats: set() for ats in PATTERNS}

    for ats, pattern_list in PATTERNS.items():
        for pattern in pattern_list:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                slug = match.group(1).lower().strip("/")

                if slug in BLOCKLIST:
                    continue
                if len(slug) < 2 or len(slug) > 64:
                    continue
                if re.match(r'^v\d+$', slug):   # version strings
                    continue

                found[ats].add(slug)

    return found


# ─────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────

def insert_company(conn, slug, ats):
    """Insert company if not already present. Returns True if new."""
    c = conn.cursor()
    c.execute(
        "SELECT slug FROM companies WHERE slug = ? AND ats = ?",
        (slug, ats)
    )
    if c.fetchone():
        return False
    c.execute(
        "INSERT INTO companies (slug, ats, valid) VALUES (?, ?, 1)",
        (slug, ats)
    )
    conn.commit()
    return True


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

def seed(dry_run=False, probe=False):
    if not dry_run:
        init_db()
        conn = sqlite3.connect(DB_PATH)

    # Collect slugs from all sources, deduped per ATS
    all_slugs = {ats: set() for ats in PATTERNS}

    print(f"\n📡 Fetching {len(SOURCES)} GitHub source(s)...\n")

    for source in SOURCES:
        print(f"  → {source['name']}")
        text = fetch_text(source["url"])
        if not text:
            print(f"     Skipped.\n")
            continue

        extracted = extract_slugs_from_text(text)
        for ats, slugs in extracted.items():
            all_slugs[ats].update(slugs)

        print(
            f"     greenhouse: {len(extracted['greenhouse'])}  "
            f"lever: {len(extracted['lever'])}  "
            f"ashby: {len(extracted['ashby'])}\n"
        )

    print("─" * 50)
    total_discovered = sum(len(s) for s in all_slugs.values())
    print(f"  Total unique slugs found: {total_discovered}")
    for ats, slugs in all_slugs.items():
        print(f"    {ats:12s} {len(slugs)}")
    print("─" * 50)

    if probe:
        print(f"\n🔎 Probing slugs to validate (adds ~0.3s each)...")

    added = 0
    skipped = 0
    invalid = 0

    for ats, slugs in all_slugs.items():
        for slug in sorted(slugs):

            # Optional: hit the ATS API to confirm slug is live
            if probe and ats in ("greenhouse", "lever"):
                if not probe_slug(slug, ats):
                    invalid += 1
                    continue
                time.sleep(0.3)

            if dry_run:
                print(f"  + {ats}/{slug}")
                added += 1
            else:
                if insert_company(conn, slug, ats):
                    added += 1
                else:
                    skipped += 1

    if not dry_run:
        conn.close()

    # ── Summary ──────────────────────────────────────────────────
    print(f"\n{'─' * 50}")
    if dry_run:
        print(f"  DRY RUN — {added} slugs would be inserted")
    else:
        print(f"  ✅ {added} new companies added to DB")
        print(f"  ·  {skipped} already existed (skipped)")
    if probe:
        print(f"  ✗  {invalid} slugs failed probe check (skipped)")
    print(f"{'─' * 50}\n")

    if not dry_run and added > 0:
        print(f"  Next step:  python fetcher.py\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Seed job-hunter DB from community GitHub repos"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview slugs without writing to the database"
    )
    parser.add_argument(
        "--probe", action="store_true",
        help="Validate each slug against the ATS API before inserting (slower)"
    )
    args = parser.parse_args()
    seed(dry_run=args.dry_run, probe=args.probe)
