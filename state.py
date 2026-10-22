"""Remembers what has already been emailed (and when the last run was) so nothing is sent twice."""
import json
import re
import shutil
from datetime import datetime, timedelta

import config

META = "_meta"


def fingerprint(job):
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    return f"{norm(job['company'])}|{norm(job['title'])}"


def load():
    try:
        return json.loads(config.STATE_FILE.read_text())
    except Exception:
        return {}


def save(state):
    config.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.STATE_FILE.write_text(json.dumps(state, indent=1, ensure_ascii=False))


def get_meta(state):
    return state.get(META, {})


def set_meta(state, **kw):
    state.setdefault(META, {}).update(kw)


def record(state, job, now, resume):
    state[fingerprint(job)] = {
        "title": job["title"], "company": job["company"], "url": job["url"], "source": job["source"],
        "score": job["score"], "priority": job["priority"], "status": job["status"], "elig": job["elig"],
        "salary": job["salary"], "posted": job["posted"].isoformat() if job["posted"] else "",
        "sent": now.isoformat(), "update": job.get("update", ""),
        "variant": (resume or {}).get("variant", ""), "resume": (resume or {}).get("path", ""),
    }


def _jobs(state):
    return {k: v for k, v in state.items() if not k.startswith("_")}


def prune(state, now):
    cutoff = now - timedelta(days=config.STATE_KEEP_DAYS)
    for k, v in list(_jobs(state).items()):
        if datetime.fromisoformat(v["sent"]) < cutoff:
            del state[k]
    rcut = (now - timedelta(days=config.RESUME_KEEP_DAYS)).astimezone(config.TZ).date()
    if config.RESUME_DIR.exists():
        for d in config.RESUME_DIR.iterdir():
            try:
                day = datetime.strptime(d.name, "%Y-%m-%d").date()
            except ValueError:
                continue
            if d.is_dir() and day < rcut:
                shutil.rmtree(d, ignore_errors=True)


def week_records(state, now, days=7):
    cutoff = now - timedelta(days=days)
    rows = [v for v in _jobs(state).values() if datetime.fromisoformat(v["sent"]) >= cutoff]
    return sorted(rows, key=lambda r: -r["score"])
