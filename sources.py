"""Job fetchers: remote job boards, Hacker News 'Who is hiring', and employer career sites (Greenhouse, Lever, Ashby)."""
import html
import json
import os
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config


class SourceSkipped(Exception):
    pass


# ----------------------------- helpers ------------------------------------
def strip_html(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


class Posted(datetime):
    """A datetime that remembers whether the source gave a real time of day (precise) or only a date."""
    precise = True


def _posted(dt, precise=True):
    p = Posted(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, dt.microsecond, tzinfo=dt.tzinfo)
    p.precise = precise
    return p


def to_dt(value):
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            v = value / 1000 if value > 1e11 else value
            return _posted(datetime.fromtimestamp(v, tz=timezone.utc))
        s = str(value).strip()
        if s.isdigit():
            return to_dt(int(s))
        precise = not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s)       # "2026-10-22" alone has no time of day
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            d = parsedate_to_datetime(s)
        return _posted(d if d.tzinfo else d.replace(tzinfo=timezone.utc), precise)
    except Exception:
        return None


SALARY_RE = re.compile(
    r"((?:US\$|\$|USD|EUR|€|£|GBP)\s?\d{2,3}(?:,\d{3}|k|K)?\s*(?:-|–|—|to)\s*(?:US\$|\$|USD|EUR|€|£|GBP)?\s?\d{2,3}(?:,\d{3}|k|K)?)")


def find_salary(text):
    m = SALARY_RE.search(text or "")
    return m.group(1).strip() if m else ""


def to_num(x):
    if isinstance(x, (int, float)):
        return float(x)
    m = re.search(r"(\d+(?:\.\d+)?)\s*([km])?", str(x or "").lower().replace(",", ""))
    if not m:
        return None
    return float(m.group(1)) * {"k": 1e3, "m": 1e6}.get(m.group(2), 1)


def money(lo, hi, cur=""):
    if hi in (None, "") and isinstance(lo, str):                 # e.g. "US$119.9k - US$193.2k" in one field
        nums = re.findall(r"\d+(?:\.\d+)?\s*[km]?", lo.lower().replace(",", ""))
        if len(nums) >= 2:
            lo, hi = nums[0], nums[1]
    lo, hi = to_num(lo), to_num(hi)
    if lo and hi:
        return f"{cur} {int(lo):,}-{int(hi):,}".strip()
    if lo or hi:
        return f"{cur} {int(lo or hi):,}".strip()
    return ""


def make_job(source, kind, jid, title, company, url, location, posted, salary, desc, tags=None, remote_confirmed=True):
    desc = strip_html(desc)[:8000]
    return {"source": source, "kind": kind, "id": f"{source}:{jid}", "title": (title or "").strip(),
            "company": (company or "").strip(), "url": url or "", "location": (location or "").strip(),
            "posted": posted, "salary": (salary or find_salary(desc) or "").strip(), "desc": desc,
            "tags": [t.lower() for t in (tags or []) if isinstance(t, str)], "remote_confirmed": remote_confirmed}


def title_ok(title):
    t = title or ""
    return bool(config.TITLE_RE.search(t)) and not config.BLOCK_RE.search(t) and not config.LANG_RE.search(t)


_SESSION = requests.Session()
_retry = Retry(total=2, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
_SESSION.mount("https://", HTTPAdapter(max_retries=_retry))
_SESSION.mount("http://", HTTPAdapter(max_retries=_retry))


def http_get(url, **kw):
    kw.setdefault("headers", config.HEADERS)
    kw.setdefault("timeout", 30)
    return _SESSION.get(url, **kw)


def get_json(url, **kw):
    r = http_get(url, **kw)
    r.raise_for_status()
    return r.json()


# ----------------------------- job boards ---------------------------------
def fetch_remotive():
    out, seen = [], set()
    for q in ["devops", "sre", "cloud engineer", "platform engineer", "kubernetes", "infrastructure", "site reliability"]:
        for j in get_json("https://remotive.com/api/remote-jobs", params={"search": q, "limit": 100}).get("jobs", []):
            if j["id"] in seen:
                continue
            seen.add(j["id"])
            out.append(make_job("Remotive", "board", j["id"], j.get("title"), j.get("company_name"), j.get("url"),
                                j.get("candidate_required_location"), to_dt(j.get("publication_date")),
                                j.get("salary"), j.get("description"), j.get("tags")))
    return out


def fetch_remoteok():
    out = []
    for j in get_json("https://remoteok.com/api"):
        if not isinstance(j, dict) or "position" not in j:
            continue
        out.append(make_job("RemoteOK", "board", j.get("id"), j.get("position"), j.get("company"),
                            j.get("apply_url") or j.get("url"), j.get("location"),
                            to_dt(j.get("epoch") or j.get("date")),
                            money(j.get("salary_min"), j.get("salary_max"), "USD"), j.get("description"), j.get("tags")))
    return out


def fetch_wwr():
    out = []
    for f in ["remote-devops-sysadmin-jobs", "remote-back-end-programming-jobs"]:
        r = http_get(f"https://weworkremotely.com/categories/{f}.rss", headers=config.HEADERS, timeout=30)
        r.raise_for_status()
        for it in ET.fromstring(r.content).iter("item"):
            raw = it.findtext("title") or ""
            company, _, title = raw.partition(":")
            if not title:
                company, title = "", raw
            out.append(make_job("WeWorkRemotely", "board", it.findtext("guid") or it.findtext("link"), title, company,
                                it.findtext("link"), it.findtext("region") or "", to_dt(it.findtext("pubDate")),
                                "", it.findtext("description")))
    return out


def fetch_workingnomads():
    out = []
    for j in get_json("https://www.workingnomads.com/api/exposed_jobs/"):
        tags = j.get("tags")
        tags = tags.split(",") if isinstance(tags, str) else (tags or [])
        out.append(make_job("WorkingNomads", "board", j.get("url"), j.get("title"), j.get("company_name"), j.get("url"),
                            j.get("location"), to_dt(j.get("pub_date")), "", j.get("description"), tags))
    return out


def fetch_himalayas():
    out = []
    for offset in (0, 20, 40, 60, 80, 100):
        for j in get_json("https://himalayas.app/jobs/api", params={"limit": 20, "offset": offset}).get("jobs", []):
            restr = j.get("locationRestrictions") or []
            out.append(make_job("Himalayas", "board", j.get("guid") or j.get("applicationLink"), j.get("title"),
                                j.get("companyName"), j.get("applicationLink"), ", ".join(restr) if restr else "Worldwide",
                                to_dt(j.get("pubDate")), money(j.get("minSalary"), j.get("maxSalary"), j.get("currency") or ""),
                                j.get("description") or j.get("excerpt"), j.get("categories")))
    return out


def fetch_jobicy():
    out, seen = [], set()
    for tag in ["devops", "kubernetes", "aws", "sre", "cloud", "infrastructure"]:
        for j in get_json("https://jobicy.com/api/v2/remote-jobs", params={"count": 50, "tag": tag}).get("jobs", []):
            if j.get("id") in seen:
                continue
            seen.add(j.get("id"))
            geo = j.get("jobGeo")
            out.append(make_job("Jobicy", "board", j.get("id"), j.get("jobTitle"), j.get("companyName"), j.get("url"),
                                ", ".join(geo) if isinstance(geo, list) else geo, to_dt(j.get("pubDate")),
                                money(j.get("annualSalaryMin") or j.get("salaryMin"), j.get("annualSalaryMax") or j.get("salaryMax"),
                                      j.get("salaryCurrency") or ""), j.get("jobDescription")))
    return out


def fetch_hn_whoishiring():
    """Newest monthly 'Ask HN: Who is hiring?' thread; keeps top-level posts that mention remote."""
    hits = get_json("https://hn.algolia.com/api/v1/search_by_date",
                    params={"tags": "story,author_whoishiring", "hitsPerPage": 6}).get("hits", [])
    threads = [h for h in hits if "who is hiring" in (h.get("title") or "").lower()][:1]
    out = []
    for th in threads:
        tree = get_json(f"https://hn.algolia.com/api/v1/items/{th['objectID']}", timeout=60)
        for c in tree.get("children", []):
            raw = c.get("text") or ""
            if not raw or "remote" not in raw.lower():
                continue
            header = strip_html(re.split(r"<p>", raw)[0])
            parts = [p.strip() for p in header.split("|")]
            title = next((p for p in parts if title_ok(p)), None)
            if not title:
                continue
            company = parts[0][:60]
            loc = " | ".join(p for p in parts[1:] if re.search(r"remote|anywhere|worldwide|global|emea|africa|us|eu|uk|onsite|hybrid", p, re.I))
            out.append(make_job("HN-WhoIsHiring", "hn", c["id"], title, company,
                                f"https://news.ycombinator.com/item?id={c['id']}", loc or "Remote", to_dt(c.get("created_at_i")),
                                "", raw))
    return out


def fetch_jsearch():
    """Optional: Google-for-Jobs style aggregation (covers LinkedIn/Indeed/Glassdoor listings). Needs RAPIDAPI_KEY."""
    key = os.environ.get("RAPIDAPI_KEY")
    if not key:
        raise SourceSkipped("not configured (optional: set RAPIDAPI_KEY to include LinkedIn/Indeed/Glassdoor listings)")
    out = []
    for q in ["remote devops engineer", "remote site reliability engineer", "remote platform engineer", "remote cloud engineer"]:
        r = http_get("https://jsearch.p.rapidapi.com/search",
                         headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
                         params={"query": q, "remote_jobs_only": "true", "date_posted": "3days", "num_pages": 1}, timeout=30)
        r.raise_for_status()
        for j in r.json().get("data", []):
            country = j.get("job_country") or ""
            out.append(make_job("JSearch", "board", j.get("job_id"), j.get("job_title"), j.get("employer_name"),
                                j.get("job_apply_link"), f"Remote - {country}" if country else "Remote",
                                to_dt(j.get("job_posted_at_datetime_utc")),
                                money(j.get("job_min_salary"), j.get("job_max_salary"), j.get("job_salary_currency") or ""),
                                j.get("job_description")))
    return out


# ----------------------------- employer career sites ----------------------
REMOTE_LOC = re.compile(r"remote|anywhere|worldwide|distributed|work from home", re.I)


def _remote(location, title, desc, flag=None):
    if flag:
        return True
    return bool(REMOTE_LOC.search(f"{location} {title}") or config.REMOTE_IN_DESC.search((desc or "")[:2500]))


def _greenhouse(slug):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"}, timeout=25)
    out = []
    for j in data.get("jobs", []):
        if not title_ok(j.get("title")):
            continue
        loc = (j.get("location") or {}).get("name", "")
        desc = html.unescape(j.get("content") or "")
        out.append(make_job("Greenhouse", "ats", f"{slug}-{j['id']}", j["title"], slug.replace("-", " ").title(),
                            j.get("absolute_url"), loc, to_dt(j.get("first_published") or j.get("updated_at")), "", desc,
                            remote_confirmed=_remote(loc, j["title"], strip_html(desc))))
    return out


def _lever(slug):
    out = []
    for j in get_json(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}, timeout=25):
        if not title_ok(j.get("text")):
            continue
        cats = j.get("categories") or {}
        loc = "; ".join(cats.get("allLocations") or [cats.get("location") or ""])
        sr = j.get("salaryRange") or {}
        desc = j.get("descriptionPlain") or strip_html(j.get("description"))
        flag = (j.get("workplaceType") or "").lower() == "remote"
        out.append(make_job("Lever", "ats", f"{slug}-{j['id']}", j["text"], slug.replace("-", " ").title(),
                            j.get("hostedUrl") or j.get("applyUrl"), loc, to_dt(j.get("createdAt")),
                            money(sr.get("min"), sr.get("max"), sr.get("currency") or ""), desc,
                            remote_confirmed=_remote(loc, j["text"], desc, flag)))
    return out


def _ashby(slug):
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", params={"includeCompensation": "true"}, timeout=25)
    out = []
    for j in data.get("jobs", []):
        if not title_ok(j.get("title")):
            continue
        loc = j.get("location") or ""
        sec = [s.get("location") if isinstance(s, dict) else s for s in (j.get("secondaryLocations") or [])]
        loc = "; ".join([loc] + [s for s in sec if s])
        comp = ((j.get("compensation") or {}).get("compensationTierSummary")) or ""
        desc = j.get("descriptionPlain") or strip_html(j.get("descriptionHtml"))
        flag = bool(j.get("isRemote")) or (j.get("workplaceType") or "").lower() == "remote"
        out.append(make_job("Ashby", "ats", f"{slug}-{j['id']}", j["title"], slug.replace("-", " ").title(),
                            j.get("jobUrl") or j.get("applyUrl"), loc, to_dt(j.get("publishedAt")), comp, desc,
                            remote_confirmed=_remote(loc, j["title"], desc, flag)))
    return out


ATS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}


def fetch_ats_group(name):
    """Returns (jobs, note). Slugs that 404 are reported, not fatal."""
    try:
        slugs = json.loads((config.ROOT / "companies.json").read_text()).get(name, [])
    except Exception as e:
        raise SourceSkipped(f"companies.json unreadable ({e})")
    fn, jobs, missing, errors = ATS[name], [], [], 0

    def one(slug):
        try:
            return slug, fn(slug), None
        except requests.HTTPError as e:
            return slug, [], "missing" if e.response is not None and e.response.status_code in (404, 410) else "error"
        except Exception:
            return slug, [], "error"

    with ThreadPoolExecutor(max_workers=8) as ex:
        for slug, got, err in ex.map(one, slugs):
            jobs += got
            if err == "missing":
                missing.append(slug)
            elif err:
                errors += 1
    ok = len(slugs) - len(missing) - errors
    note = f"{ok}/{len(slugs)} company boards reachable, {len(jobs)} matching-title listings"
    if missing:
        note += f"; not found (remove from companies.json): {', '.join(missing)}"
    if errors:
        note += f"; {errors} errored"
    return jobs, note


BOARDS = {
    "Remotive": fetch_remotive, "RemoteOK": fetch_remoteok, "We Work Remotely": fetch_wwr,
    "Working Nomads": fetch_workingnomads, "Himalayas": fetch_himalayas, "Jobicy": fetch_jobicy,
    "Hacker News: Who is hiring": fetch_hn_whoishiring, "JSearch (LinkedIn/Indeed/Glassdoor)": fetch_jsearch,
}
