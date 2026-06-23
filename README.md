# Job Hunter

A personal job board that automatically compiles a daily list of filtered, entry-level job postings pulled directly from company ATS APIs

## What it does

Job Hunter queries over 1,100 companies directly through their applicant tracking systems every day, filters the results down to relevant roles, and surfaces them in a clean dashboard you can search, save, and track applications from.

The company list has a strong concentration of startups and includes boards from four ATS platforms:

- **Greenhouse**
- **Lever**
- **Ashby**
- **SmartRecruiters**

## Cloning and customizing
Jobs can be filtered by role area and seniority, e.g. in my case: data, analytics, machine learning, entry-level etc. 

The keyword filters in `fetcher.py` are easy to adjust for your own job search:

```python
ROLE_KEYWORDS = [
    "data", "analytics", "machine learning", "analyst", ...
]

INCLUDE_KEYWORDS = [
    "junior", "entry level", "new grad", ...
]

EXCLUDE_KEYWORDS = [
    "senior", "staff", "lead", "director", ...
]
```

Swap these out to target whatever roles you're looking for, and the rest of the pipeline works the same way.

The live dashboard is available at:
**[https://reid-peterson.github.io/Job-hunter/](https://reid-peterson.github.io/Job-hunter/)**


**Requirements:** Python 3.11+, no third-party packages needed (standard library only)

## Notes

- All data is pulled from public ATS APIs — no authentication or API keys required
- The daily fetch runs automatically via GitHub Actions at 12pm UTC
- `jobs.db` is the source of truth; `jobs.json` is a read-only export for the frontend
