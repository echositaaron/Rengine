"""Generic reader for extra job boards listed in sites.json.

Strategy per site (polite, low volume, robots.txt respected):
  1. RSS/Atom feed (configured or auto-discovered from the page) -> jobs directly.
  2. Otherwise: listing page -> links whose title looks relevant -> read each job page's schema.org
     JobPosting JSON-LD (the structured data boards publish for Google Jobs): title, company, date posted,
     remote flag, eligible countries, salary. Pages without it fall back to the page title/description.
Sites that block bots, need JavaScript, or have no job links are reported in the coverage section.
"""
import json
import re
import time
import urllib.robotparser
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

import config
import sources

DELAY = 0.6          # seconds between requests to the same site
TIMEOUT = 20
_robots = {}
KNOWN = set()     # job-page URLs read in earlier runs (skipped, so each site is hit gently)
READ = set()      # job-page URLs read in this run


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors, self.feeds, self.jsonld, self.meta, self.title, self.text = [], [], [], {}, "", []
        self._href, self._atxt, self._mode, self._buf, self._skip = None, [], None, [], 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self._href, self._atxt = a["href"], []
        elif tag == "link" and "alternate" in (a.get("rel") or "") and re.search(r"rss|atom", a.get("type") or "") and a.get("href"):
            self.feeds.append(a["href"])
        elif tag == "meta":
            k = a.get("property") or a.get("name")
            if k and a.get("content"):
                self.meta[k.lower()] = a["content"]
        elif tag == "script":
            self._skip += 1
            if (a.get("type") or "").lower() == "application/ld+json":
                self._mode, self._buf = "ld", []
        elif tag == "style":
            self._skip += 1
        elif tag == "title":
            self._mode, self._buf = "title", []

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.anchors.append((self._href, " ".join("".join(self._atxt).split())))
            self._href = None
        elif tag == "script":
            self._skip = max(0, self._skip - 1)
            if self._mode == "ld":
                self.jsonld.append("".join(self._buf))
                self._mode = None
        elif tag == "style":
            self._skip = max(0, self._skip - 1)
        elif tag == "title" and self._mode == "title":
            self.title, self._mode = "".join(self._buf).strip(), None

    def handle_data(self, data):
        if self._mode in ("ld", "title"):
            self._buf.append(data)
        if self._skip:
            return
        if self._href is not None:
            self._atxt.append(data)
        self.text.append(data)


def parse_page(markup):
    p = Page()
    try:
        p.feed(markup)
    except Exception:
        pass
    return p


def allowed(url):
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    rp = _robots.get(base)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = requests.get(base + "/robots.txt", headers=config.HEADERS, timeout=10)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except Exception:
            rp.parse([])
        _robots[base] = rp
    return rp.can_fetch(config.HEADERS["User-Agent"], url)


def fetch(url):
    """Returns response text, or raises. Raises PermissionError when robots.txt disallows."""
    if not allowed(url):
        raise PermissionError("robots.txt disallows")
    time.sleep(DELAY)
    r = requests.get(url, headers=config.HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content if url.endswith((".xml", ".rss")) or "xml" in r.headers.get("content-type", "") else r.text


# ------------------------------ JSON-LD -----------------------------------
def _walk(obj, out):
    if isinstance(obj, list):
        for o in obj:
            _walk(o, out)
    elif isinstance(obj, dict):
        t = obj.get("@type")
        if "JobPosting" in (t if isinstance(t, list) else [t]):
            out.append(obj)
        for v in obj.values():
            if isinstance(v, (list, dict)):
                _walk(v, out)


def jobpostings(page):
    out = []
    for raw in page.jsonld:
        try:
            _walk(json.loads(raw), out)
        except Exception:
            continue
    return out


def _names(x):
    if isinstance(x, str):
        return [x]
    if isinstance(x, dict):
        for k in ("name", "addressCountry", "addressRegion", "addressLocality"):
            if x.get(k):
                return _names(x[k])
        addr = x.get("address")
        return _names(addr) if addr else []
    if isinstance(x, list):
        return [n for i in x for n in _names(i)]
    return []


def jp_to_job(jp, site, page_url, now):
    vt = sources.to_dt(jp.get("validThrough"))
    if vt and vt < now:
        return None
    org = jp.get("hiringOrganization")
    company = (org.get("name") if isinstance(org, dict) else org) or ""
    telecommute = "TELECOMMUTE" in json.dumps(jp.get("jobLocationType", "")).upper()
    names = _names(jp.get("applicantLocationRequirements")) or _names(jp.get("jobLocation"))
    loc = ", ".join(dict.fromkeys(names))
    if telecommute:
        loc = f"Remote - {loc}" if loc else "Remote"
    sal = ""
    bs = jp.get("baseSalary")
    if isinstance(bs, dict):
        val = bs.get("value") if isinstance(bs.get("value"), dict) else bs
        sal = sources.money(val.get("minValue") or val.get("value"), val.get("maxValue"), bs.get("currency") or "")
        if sal and isinstance(bs.get("value"), dict) and bs["value"].get("unitText"):
            sal += f" / {str(bs['value']['unitText']).lower()}"
    desc = jp.get("description") or ""
    url = jp.get("url") if str(jp.get("url", "")).startswith("http") else page_url
    title = str(jp.get("title") or "")
    remote = site.get("remote_only", True) or telecommute or sources._remote(loc, title, sources.strip_html(desc))
    return sources.make_job(site["name"], "board", url, title, company, url, loc, sources.to_dt(jp.get("datePosted")),
                            sal, desc, remote_confirmed=bool(remote))


# ------------------------------ RSS / Atom --------------------------------
def parse_feed(content, site):
    local = lambda t: t.rsplit("}", 1)[-1]
    root = ET.fromstring(content)
    jobs = []
    for it in root.iter():
        if local(it.tag) not in ("item", "entry"):
            continue
        d = {"tags": []}
        for ch in it:
            k = local(ch.tag)
            if k == "link" and "link" not in d:
                d["link"] = ch.get("href") or (ch.text or "").strip()
            elif k in ("pubDate", "published", "updated", "date") and "date" not in d:
                d["date"] = ch.text
            elif k in ("description", "summary", "content", "encoded") and not d.get("desc"):
                d["desc"] = ch.text or ""
            elif k in ("creator", "author") and "company" not in d:
                d["company"] = (ch.text or "").strip() or next((x.text for x in ch if local(x.tag) == "name"), "")
            elif k in ("region", "location", "jobLocation") and "loc" not in d:
                d["loc"] = ch.text or ""
            elif k == "category" and ch.text:
                d["tags"].append(ch.text)
            elif k == "title" and "title" not in d:
                d["title"] = (ch.text or "").strip()
        title, company = d.get("title", ""), d.get("company", "")
        m = re.match(r"(.+?)\s+(?:at|@)\s+(.+)$", title)
        if m:
            title, company = m.group(1), m.group(2)
        elif ": " in title and not company:
            company, _, title = title.partition(": ")
        if title and d.get("link"):
            jobs.append(sources.make_job(site["name"], "board", d["link"], title, company, d["link"], d.get("loc", ""),
                                         sources.to_dt(d.get("date")), "", d.get("desc"), d["tags"],
                                         remote_confirmed=site.get("remote_only", True) or sources._remote(d.get("loc", ""), title, d.get("desc", ""))))
    return jobs


# ------------------------------ link discovery ----------------------------
BAD_URL = re.compile(r"(/category|/categories|/tag/|/tags/|/page/|[?&]page=|login|signin|sign-in|signup|register|post-a-job|"
                     r"pricing|/about|contact|privacy|terms|/blog|/companies|/company/|salary|\.xml$|\.rss$|\.pdf$)", re.I)


def candidates(page_url, anchors):
    host = urlparse(page_url).netloc.replace("www.", "")
    here = urlparse(page_url).path.rstrip("/")
    seen, out = set(), []
    for href, text in anchors:
        u = urljoin(page_url, href.split("#")[0])
        p = urlparse(u)
        path = p.path.rstrip("/")
        if p.scheme not in ("http", "https") or p.netloc.replace("www.", "") != host:
            continue
        if u in seen or path == here or not path or BAD_URL.search(u) or re.search(r"-jobs$", path):
            continue
        slug = path.split("/")[-1].replace("-", " ").replace("_", " ")
        label = text if 4 <= len(text) <= 160 else ""
        if re.search(r"\bjobs?$", label.lower().strip()) or re.search(r"\bjobs?$", slug):
            continue                                   # category/listing pages, not a single job
        if (sources.title_ok(label) or sources.title_ok(slug)) and (len(slug.split()) >= 3 or path.count("/") >= 2 or re.search(r"\d", path)):
            seen.add(u)
            out.append((u, label))
    return out


def job_from_plain_page(page, url, site):
    raw_title = page.meta.get("og:title") or page.title
    title = re.split(r"\s+[|–—]\s+", raw_title)[0].strip()
    company = ""
    m = re.match(r"(.+?)\s+(?:at|@)\s+(.+)$", title)
    if m:
        title, company = m.group(1), m.group(2)
    desc = page.meta.get("og:description") or page.meta.get("description") or ""
    body = " ".join(page.text)[:6000]
    return sources.make_job(site["name"], "board", url, title, company or "(see listing)", url, "", None, "",
                            desc + " " + body, remote_confirmed=site.get("remote_only", True) or sources._remote("", title, body))


# ------------------------------ per-site ----------------------------------
def fetch_site(site):
    now = datetime.now(timezone.utc)
    jobs, links, feeds = [], [], list(site.get("feeds", []))
    stats = {"pages": 0, "details": 0, "blocked": 0, "failed": 0, "jsonld": 0, "feed": 0}
    for i, url in enumerate(site["urls"]):
        try:
            page = parse_page(fetch(url))
        except PermissionError:
            stats["blocked"] += 1
            continue
        except Exception:
            stats["failed"] += 1
            continue
        stats["pages"] += 1
        for jp in jobpostings(page):
            j = jp_to_job(jp, site, url, now)
            if j:
                jobs.append(j)
                stats["jsonld"] += 1
        if not site.get("feeds") and i == 0:
            feeds += [urljoin(url, f) for f in page.feeds[:2]]
        links += candidates(url, page.anchors)
    for f in feeds:
        try:
            got = parse_feed(fetch(f), site)
            jobs += got
            stats["feed"] += len(got)
        except Exception:
            stats["failed"] += 1
    have = {j["url"] for j in jobs}
    todo, seen = [], set()
    for u, _ in links:
        if u not in have and u not in seen and u not in KNOWN:
            seen.add(u)
            todo.append(u)
    for u in todo[: site.get("max_details", 25)]:
        try:
            page = parse_page(fetch(u))
        except PermissionError:
            stats["blocked"] += 1
            continue
        except Exception:
            stats["failed"] += 1
            continue
        stats["details"] += 1
        READ.add(u)
        found = [j for j in (jp_to_job(jp, site, u, now) for jp in jobpostings(page)) if j]
        if found:
            jobs += found
            stats["jsonld"] += len(found)
        elif site.get("allow_plain"):           # pages without structured data have no verifiable post date
            jobs.append(job_from_plain_page(page, u, site))
    if not stats["pages"]:
        raise RuntimeError("no listing page could be read (" + ("blocked by robots.txt" if stats["blocked"] else "site unreachable or blocking bots") + ")")
    note = (f"{stats['pages']}/{len(site['urls'])} listing pages read, {stats['details']} job pages read, "
            f"{len(jobs)} listings ({stats['jsonld']} structured, {stats['feed']} from feed)")
    if stats["blocked"]:
        note += f"; {stats['blocked']} skipped (robots.txt)"
    if stats["failed"]:
        note += f"; {stats['failed']} failed"
    if not jobs:
        note += "; no job links found (page may need JavaScript)"
    return jobs, note


def fetch_all():
    """Returns [(name, jobs, note, ok)]."""
    try:
        sites = json.loads((config.ROOT / "sites.json").read_text())["sites"]
    except Exception as e:
        return [("Extra job boards (sites.json)", [], f"skipped - sites.json unreadable ({e})", False)]

    def one(site):
        try:
            jobs, note = fetch_site(site)
            return site["name"], jobs, note, True
        except Exception as ex:
            return site["name"], [], f"could not be checked ({str(ex)[:110]})", False

    with ThreadPoolExecutor(max_workers=4) as ex:
        return list(ex.map(one, sites))
