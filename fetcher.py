"""
job-hunter/fetcher.py
Fetches jobs from Greenhouse, Lever, and Ashby ATS APIs.
No API keys required — all public endpoints.
"""

import urllib.request
import urllib.error
import json
import time
import sqlite3
import re
from datetime import datetime, date

# ─────────────────────────────────────────────
# CONFIGURATION — edit these to your liking
# ─────────────────────────────────────────────

INCLUDE_KEYWORDS = [
    "junior", "entry", "entry-level", "entry level",
    "associate", "new grad", "new graduate",
    "0-2 years", "0-1 year", "1-2 years",
    "early career", "intern", "internship",
    "analyst", "coordinator", "assistant",
    "software engineer i", "engineer i",
    "level 1", "level i", "l1",
]

EXCLUDE_KEYWORDS = [
    "senior", "sr.", "sr ", "staff", "lead", "principal",
    "director", "manager", "vp ", "vice president", "head of",
    "5+ years", "6+ years", "7+ years", "8+ years", "10+ years",
    "5 years", "6 years", "7 years", "8 years",
    "secret clearance", "top secret",
]

# Only include jobs matching these role areas (leave empty to allow all)
ROLE_KEYWORDS = [
    "engineer", "developer", "software", "data", "analyst",
    "product", "design", "marketing", "operations", "finance",
    "research", "science", "backend", "frontend", "fullstack",
    "devops", "security", "machine learning", "ml", "ai",
]

DB_PATH = "data/jobs.db"
REQUEST_DELAY = 0.5  # seconds between requests, be polite


# ─────────────────────────────────────────────
# DATABASE SETUP
# ─────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            company     TEXT NOT NULL,
            ats         TEXT NOT NULL,
            location    TEXT,
            remote      INTEGER DEFAULT 0,
            description TEXT,
            apply_url   TEXT NOT NULL,
            date_found  TEXT NOT NULL,
            applied     INTEGER DEFAULT 0,
            saved       INTEGER DEFAULT 0,
            notes       TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            slug        TEXT NOT NULL,
            ats         TEXT NOT NULL,
            name        TEXT,
            last_checked TEXT,
            valid       INTEGER DEFAULT 1,
            PRIMARY KEY (slug, ats)
        )
    """)
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
# HTTP HELPER
# ─────────────────────────────────────────────

def fetch_json(url, timeout=10):
    """Fetch a URL and return parsed JSON, or None on failure."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (job-hunter personal project)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        return None


# ─────────────────────────────────────────────
# ATS FETCHERS
# ─────────────────────────────────────────────

def fetch_greenhouse(slug):
    """Fetch all jobs from a Greenhouse board."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    data = fetch_json(url)
    if not data or "jobs" not in data:
        return None  # invalid slug

    jobs = []
    for j in data["jobs"]:
        location = ""
        if j.get("location"):
            location = j["location"].get("name", "")

        description = ""
        if j.get("content"):
            # Strip HTML tags from description
            description = re.sub(r"<[^>]+>", " ", j["content"])
            description = re.sub(r"\s+", " ", description).strip()

        jobs.append({
            "id": f"gh_{j['id']}",
            "title": j.get("title", ""),
            "company": slug,
            "ats": "greenhouse",
            "location": location,
            "remote": 1 if "remote" in location.lower() else 0,
            "description": description[:2000],  # cap length
            "apply_url": j.get("absolute_url", ""),
        })
    return jobs


def fetch_lever(slug):
    """Fetch all jobs from a Lever board."""
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    data = fetch_json(url)
    if data is None:
        return None

    # Lever returns a list directly
    if not isinstance(data, list):
        return None

    jobs = []
    for j in data:
        location = j.get("categories", {}).get("location", "")
        commitment = j.get("categories", {}).get("commitment", "")

        # Build description from lists
        desc_parts = []
        for section in j.get("lists", []):
            desc_parts.append(section.get("text", ""))
            for item in section.get("content", "").split("<li>"):
                clean = re.sub(r"<[^>]+>", "", item).strip()
                if clean:
                    desc_parts.append(clean)
        description = " ".join(desc_parts)[:2000]

        jobs.append({
            "id": f"lv_{j['id']}",
            "title": j.get("text", ""),
            "company": slug,
            "ats": "lever",
            "location": location,
            "remote": 1 if "remote" in location.lower() or "remote" in commitment.lower() else 0,
            "description": description,
            "apply_url": j.get("hostedUrl", ""),
        })
    return jobs


def fetch_ashby(slug):
    """Fetch all jobs from an Ashby board."""
    url = f"https://jobs.ashbyhq.com/api/non-user-graphql"
    # Ashby uses a simple public endpoint
    payload = json.dumps({
        "operationName": "ApiJobBoardWithTeams",
        "variables": {"organizationHostedJobsPageName": slug},
        "query": """
            query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) {
                jobBoard: jobBoardWithTeams(
                    organizationHostedJobsPageName: $organizationHostedJobsPageName
                ) {
                    jobPostings {
                        id title locationName isRemote
                        jobRequisition { internalLink }
                    }
                }
            }
        """
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (job-hunter personal project)"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    try:
        postings = data["data"]["jobBoard"]["jobPostings"]
    except (KeyError, TypeError):
        return None

    jobs = []
    for j in postings:
        jobs.append({
            "id": f"ash_{j['id']}",
            "title": j.get("title", ""),
            "company": slug,
            "ats": "ashby",
            "location": j.get("locationName", ""),
            "remote": 1 if j.get("isRemote") else 0,
            "description": "",
            "apply_url": f"https://jobs.ashbyhq.com/{slug}/{j['id']}",
        })
    return jobs



def fetch_smartrecruiters(slug):
    """
    Fetch all jobs from a SmartRecruiters board.
    SmartRecruiters has a fully public REST API — no key needed.
    The slug is the company identifier:
    https://careers.smartrecruiters.com/{slug}
    """
    jobs = []
    offset = 0
    limit  = 100

    while True:
        url = (
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
            f"?limit={limit}&offset={offset}&status=PUBLIC"
        )
        data = fetch_json(url)

        if data is None or "content" not in data:
            return None if offset == 0 else jobs

        postings = data.get("content", [])
        if not postings:
            break

        for j in postings:
            loc_obj  = j.get("location", {})
            city     = loc_obj.get("city", "")
            country  = loc_obj.get("country", "")
            remote   = loc_obj.get("remote", False)
            location = ", ".join(filter(None, [city, country]))

            dept      = j.get("department", {}).get("label", "")
            exp       = j.get("experienceLevel", {})
            exp_label = exp.get("label", "") if isinstance(exp, dict) else ""
            description = f"{dept} {exp_label}".strip()

            jobs.append({
                "id":          f"sr_{j['id']}",
                "title":       j.get("name", ""),
                "company":     slug,
                "ats":         "smartrecruiters",
                "location":    location,
                "remote":      1 if remote else 0,
                "description": description,
                "apply_url":   f"https://careers.smartrecruiters.com/{slug}/{j['id']}",
            })

        total = data.get("totalFound", 0)
        offset += limit
        if offset >= total:
            break
        time.sleep(REQUEST_DELAY)

    return jobs

# ─────────────────────────────────────────────
# FILTER ENGINE
# ─────────────────────────────────────────────

def passes_filter(job):
    """Return True if a job matches our criteria."""
    searchable = f"{job['title']} {job['description']}".lower()

    # Must match at least one role keyword (if list is non-empty)
    if ROLE_KEYWORDS:
        if not any(kw in searchable for kw in ROLE_KEYWORDS):
            return False

    # Must match at least one include keyword
    if not any(kw in searchable for kw in INCLUDE_KEYWORDS):
        return False

    # Must not match any exclude keyword in the TITLE specifically
    title_lower = job["title"].lower()
    if any(kw in title_lower for kw in EXCLUDE_KEYWORDS):
        return False

    return True


# ─────────────────────────────────────────────
# STORAGE
# ─────────────────────────────────────────────

def save_job(conn, job):
    """Insert a job if it doesn't already exist. Returns True if new."""
    c = conn.cursor()
    c.execute("SELECT id FROM jobs WHERE id = ?", (job["id"],))
    if c.fetchone():
        return False  # already stored

    c.execute("""
        INSERT INTO jobs (id, title, company, ats, location, remote,
                          description, apply_url, date_found)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job["id"], job["title"], job["company"], job["ats"],
        job["location"], job["remote"], job["description"],
        job["apply_url"], date.today().isoformat()
    ))
    conn.commit()
    return True


# ─────────────────────────────────────────────
# COMPANY LIST LOADER
# ─────────────────────────────────────────────

def load_companies(conn):
    """Load valid companies from DB."""
    c = conn.cursor()
    c.execute("SELECT slug, ats FROM companies WHERE valid = 1")
    return c.fetchall()


def mark_invalid(conn, slug, ats):
    """Mark a company slug as invalid (404'd)."""
    c = conn.cursor()
    c.execute(
        "UPDATE companies SET valid = 0, last_checked = ? WHERE slug = ? AND ats = ?",
        (datetime.now().isoformat(), slug, ats)
    )
    conn.commit()


def update_checked(conn, slug, ats):
    c = conn.cursor()
    c.execute(
        "UPDATE companies SET last_checked = ? WHERE slug = ? AND ats = ?",
        (datetime.now().isoformat(), slug, ats)
    )
    conn.commit()


# ─────────────────────────────────────────────
# MAIN RUN
# ─────────────────────────────────────────────

FETCHERS = {
    "greenhouse":      fetch_greenhouse,
    "lever":           fetch_lever,
    "ashby":           fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
}


def run():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    companies = load_companies(conn)

    if not companies:
        print("⚠️  No companies in DB. Run: python scripts/seed_companies.py first.")
        conn.close()
        return

    total_new = 0
    total_checked = 0

    print(f"🔍 Scanning {len(companies)} companies...\n")

    for slug, ats in companies:
        fetcher = FETCHERS.get(ats)
        if not fetcher:
            continue

        jobs = fetcher(slug)
        total_checked += 1

        if jobs is None:
            print(f"  ✗ {ats}/{slug} — invalid or unreachable")
            mark_invalid(conn, slug, ats)
            time.sleep(REQUEST_DELAY)
            continue

        new_count = 0
        for job in jobs:
            if passes_filter(job):
                if save_job(conn, job):
                    new_count += 1
                    total_new += 1

        update_checked(conn, slug, ats)

        if new_count > 0:
            print(f"  ✓ {ats}/{slug} — {new_count} new job(s) added")
        else:
            print(f"  · {ats}/{slug} — {len(jobs)} jobs checked, none matched")

        time.sleep(REQUEST_DELAY)

    conn.close()
    print(f"\n✅ Done. {total_new} new jobs added from {total_checked} companies.")


if __name__ == "__main__":
    import argparse as _ap
    p = _ap.ArgumentParser(description="Job Hunter fetcher")
    p.add_argument("--export", action="store_true",
                   help="Export jobs to data/jobs.json for the dashboard")
    args = p.parse_args()
    if args.export:
        init_db()
        export_json()
    else:
        run()


def export_json(path="data/jobs.json"):
    """Export all jobs from SQLite to a JSON file for the dashboard."""
    import json as _json
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT id, title, company, ats, location, remote,
               apply_url, date_found
        FROM jobs
        ORDER BY date_found DESC, id DESC
    """)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        _json.dump(rows, f, indent=2)

    print(f"✅ Exported {len(rows)} jobs to {path}")
    print(f"   Open dashboard.html and drop in {path} to view.")
