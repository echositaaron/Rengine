"""Fit assessment of a job against the candidate's real resumes (profile.json)."""
import json
import re
from datetime import datetime, timezone

import config

PROFILE = json.loads((config.ROOT / "profile.json").read_text(encoding="utf-8"))


def term_re(alias):
    return re.compile(r"(?<![a-z0-9])" + re.escape(alias.lower()) + r"(?![a-z0-9])")


_SKILL_RES = {k: [term_re(a) for a in v["aliases"]] for k, v in PROFILE["skills"].items()}
_GAP_RES = {k: [term_re(a) for a in v] for k, v in PROFILE["gap_terms"].items()}
_GO_RE = re.compile(r"(?:,|/|\(|\band\b|\bor\b|\bwith\b|\bin\b|\busing\b|\bof\b)\s*Go(?![\w-])"
                    r"(?!\s+(?:to|beyond|the|with|from|live|further|above|ahead|forward|through))"
                    r"|(?<![\w-])Go(?=\s*(?:,|/|\))|\s+(?:and|or)\s+[A-Z])")   # the language Go, not the verb


def count_terms(text, regs):
    t = text.lower()
    return sum(len(r.findall(t)) for r in regs)


def jd_skill_counts(job):
    """{canonical skill: weighted count} for skills the job mentions (title counts triple)."""
    body, title = job["desc"], f"{job['title']} {' '.join(job['tags'])}"
    out = {}
    for k, regs in _SKILL_RES.items():
        c = count_terms(body, regs) + 3 * count_terms(title, regs)
        if c:
            out[k] = c
    return out


def text_skills(text):
    """Canonical skills present in a piece of resume text."""
    t = text.lower()
    return {k for k, regs in _SKILL_RES.items() if any(r.search(t) for r in regs)}


def years_experience(now=None):
    now = now or datetime.now(timezone.utc)
    y, m = map(int, PROFILE["career_start"].split("-"))
    return round((now.year - y) + (now.month - m) / 12, 1)


_REQ_YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years?|yrs?)[^.\n]{0,70}?(?:experience|exp\b)|"
                        r"experience[^.\n]{0,50}?(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I)


def required_years(text):
    vals = []
    for m in _REQ_YEARS.finditer(text):
        v = int(m.group(1) or m.group(2))
        if 1 <= v <= 20:
            vals.append(v)
    return max(vals) if vals else None


def classify_eligibility(job):
    """Returns (status, label). status: ok | verify | no."""
    loc = job["location"]
    if loc and config.AFRICA.search(re.sub(r"south africa", "", loc, flags=re.I)):
        return "ok", f"Open to Kenya/Africa ({loc})"
    if config.WORLDWIDE.search(loc):
        if re.search(r"anywhere in (the )?(us|usa|u\.s\.|united states|europe|eu|uk|canada)\b", loc, re.I):
            return "no", f"Restricted to {loc}"
        if config.RESTRICTED_REGION.search(loc):
            return "verify", f"Worldwide wording but also names a region ({loc})"
        if config.RESTRICT_DESC.search(job["desc"]):
            return "verify", f"Listed '{loc}' but description mentions a location/work-authorization limit"
        return "ok", f"Worldwide ({loc})"
    if config.EMEA.search(loc):
        return "verify", f"EMEA - Kenya not explicitly named ({loc})"
    if config.OTHER_AFRICA.search(loc):
        return "verify", f"Listed for a specific African country ({loc}); Kenya not named"
    if not loc:
        return "verify", "No location stated on the listing"
    if config.RESTRICTED_REGION.search(loc):
        return "no", f"Restricted to {loc}"
    return "verify", f"Unclear location: {loc}"


def assess(job, now):
    """Adds score, hits, gaps, priority, etc. Returns None if the job should be dropped."""
    status, elig = classify_eligibility(job)
    if status == "no":
        return None
    counts = jd_skill_counts(job)
    need = 2 if job["kind"] == "hn" else config.MIN_CORE_SKILLS
    if len(set(counts) & config.CORE_SKILLS) < need:      # not really a cloud/platform/SRE job description
        return None
    hits = sorted(counts, key=lambda k: -PROFILE["skills"][k]["w"] * (1 + min(counts[k], 4)))
    title = job["title"].lower()

    raw = sum(PROFILE["skills"][k]["w"] * (1 + 0.3 * min(counts[k] - 1, 3)) for k in counts)
    tech = min(35.0, min(27.0, raw * 0.9) + (8 if any(t in title for t in config.TITLE_TERMS) else 0))
    geo = {"ok": 25, "verify": 10}[status]

    yrs = years_experience(now)
    req = required_years(job["desc"])
    if any(w in title for w in ("principal", "director", "head of", "vp ", "distinguished")):
        t_sen = 5
    elif "staff" in title:
        t_sen = 8
    elif "lead" in title or "manager" in title:
        t_sen = 10
    elif any(w in title for w in ("senior", "sr.", "sr ")):
        t_sen = 14
    elif any(w in title for w in ("junior", "associate", "entry")):
        t_sen = 9
    else:
        t_sen = 15
    if req is None:
        y_sen, years_gap = 15, False
    elif req <= yrs + 1:
        y_sen, years_gap = 15, False
    elif req <= yrs + 3:
        y_sen, years_gap = 10, False
    else:
        y_sen, years_gap = 4, True
    sen = min(t_sen, y_sen)

    fresh = 0
    if job["posted"]:
        age = (now - job["posted"]).total_seconds() / 86400
        fresh = 10 if age < 1 else 8 if age < 3 else 5 if age < 7 else 2 if age < 14 else 0
    text = f"{title} {job['desc'].lower()}"
    comp = (6 if job["salary"] else 3) + (4 if any(k in text for k in ("kubernetes", "platform", "staff", "senior", "lead")) else 2)
    clarity = 5 if job["url"].startswith("http") else 0
    if job["kind"] == "ats":
        clarity = 5          # employer's own application system
    total = round(tech + geo + sen + fresh + comp + clarity)

    # ---- gaps: only things we can actually point to
    gaps = []
    if status == "verify":
        gaps.append("Kenya eligibility NOT confirmed: " + elig)
    if req and years_gap:
        gaps.append(f"Asks for {req}+ years of experience; your resumes show about {yrs} years (since Feb 2022)")
    elif req and req > yrs:
        gaps.append(f"Asks for {req}+ years; you have about {yrs}, which is close but under")
    gap_hits = []
    for name, regs in _GAP_RES.items():
        c = count_terms(job["desc"], regs) + 3 * count_terms(job["title"], regs)
        if name == "Go/Golang":
            c += len(_GO_RE.findall(job["desc"]))
        if c:
            gap_hits.append((c, name))
    gap_hits.sort(reverse=True)
    if gap_hits:
        gaps.append("Mentions skills not on your resumes: " + ", ".join(n for _, n in gap_hits[:5]))
    if config.RESTRICT_DESC.search(job["desc"]) and status == "ok":
        gaps.append("Description has location/work-authorization wording; read the application page carefully")
    if not job["salary"]:
        gaps.append("No salary disclosed")
    if not job["posted"]:
        gaps.append("Posting date not provided")
    if job["kind"] != "ats":
        gaps.append("Aggregator listing; confirm details on the employer's own page")

    if status == "verify":
        prio = "Verify eligibility first"
    elif total >= 80 and not years_gap:
        prio = "Apply now"
    else:
        prio = "Apply"
    job.update(status=status, elig=elig, score=total, hits=hits, gaps=gaps, priority=prio,
               req_years=req, jd_counts=counts)
    return job
