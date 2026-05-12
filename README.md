# job-hunter

A free, self-hosted job tracker that pulls entry-level listings directly
from company ATS platforms (Greenhouse, Lever, Ashby) — bypassing LinkedIn
and Indeed entirely.

No API keys. No paid services. Just Python + SQLite.

---

## How It Works

```
GitHub repos (community slug lists)
        ↓
seed_companies.py  →  companies table in SQLite
        ↓
fetcher.py  →  hits Greenhouse / Lever / Ashby public APIs
        ↓
keyword filter  →  saves matching jobs to SQLite
        ↓
fetcher.py --export  →  jobs.json
        ↓
dashboard.html  →  browse, save, track applications
```

---

## Setup (one time)

```bash
# 1. No dependencies needed — pure Python stdlib
python --version   # 3.8+ required

# 2. Seed your company list from GitHub repos
python scripts/seed_companies.py

# 3. Run the fetcher
python fetcher.py

# 4. Export to JSON for the dashboard
python fetcher.py --export

# 5. Open dashboard.html in your browser and drop in data/jobs.json
```

---

## Daily Use

```bash
# Fetch new jobs + export in one go
python fetcher.py && python fetcher.py --export
```

Or automate with cron (runs every morning at 8am):
```
0 8 * * * cd /path/to/job-hunter && python fetcher.py && python fetcher.py --export
```

Or with GitHub Actions — commit `data/jobs.json` so it updates automatically
without your computer being on (see `.github/workflows/` if you set that up).

---

## Customizing Filters

Edit the top of `fetcher.py`:

```python
INCLUDE_KEYWORDS = [
    "junior", "entry", "associate", "new grad",
    "0-2 years", "early career", ...
]

EXCLUDE_KEYWORDS = [
    "senior", "staff", "lead", "principal", ...
]

ROLE_KEYWORDS = [
    "engineer", "analyst", "product", "design", ...
]
```

Leave `ROLE_KEYWORDS = []` to match all job types.

---

## Adding Companies

**Option A — Re-run the seed script** (picks up any new additions to the GitHub repos):
```bash
python scripts/seed_companies.py
```

**Option B — Add a single company manually:**
```bash
# If you know the ATS:
python scripts/add_company.py stripe --ats greenhouse

# If you don't know which ATS they use:
python scripts/add_company.py notion --probe
```

**Option C — Add more GitHub sources** by editing the `SOURCES` list
in `scripts/seed_companies.py`. Any raw markdown URL that contains
Greenhouse/Lever/Ashby links will work.

---

## Dashboard

Open `dashboard.html` in any browser (no server needed).

- Drop or paste your `data/jobs.json`
- Filter by platform, remote, keyword
- Save jobs you like (★)
- Mark jobs as applied
- Add notes per job
- All state (saved/applied/notes) is stored in your browser's localStorage

---

## Project Structure

```
job-hunter/
├── fetcher.py               # Core: fetch, filter, store, export
├── dashboard.html           # Browser UI
├── data/
│   ├── jobs.db              # SQLite database (auto-created)
│   └── jobs.json            # Dashboard export (auto-created)
└── scripts/
    ├── seed_companies.py    # Pull slugs from GitHub repos
    └── add_company.py       # Add a single company manually
```

---

## ATS Coverage

| Platform   | API Type       | Auth Needed | Notes                        |
|------------|---------------|-------------|------------------------------|
| Greenhouse | Public REST   | None        | Best coverage, cleanest data |
| Lever      | Public REST   | None        | Strong startup coverage      |
| Ashby      | Public GraphQL| None        | Fast-growing with AI startups|

---

## Slug Validation

If you want to validate slugs before they're added (removes dead/stale ones):
```bash
python scripts/seed_companies.py --probe
```
Note: this adds ~0.3s per slug so it's slower but produces a cleaner list.

Preview what would be added without touching the DB:
```bash
python scripts/seed_companies.py --dry-run
```
