#!/usr/bin/env python3
"""Remote job engine: search -> fit-score against your resumes -> tailored PDF resume per role -> email -> weekly roundup.

  python job_digest.py                      run: email roles published since the last run (first run: last 5 hours)
  python job_digest.py --weekly             weekly roundup of everything sent in the last 7 days (no fetching)
  python job_digest.py --dry-run [--weekly] build everything into ./preview, send nothing, save no state
  python job_digest.py --tailor jd.txt --title "Senior SRE" --company "Acme" [--location "Remote - Worldwide"]
                                            tailor a PDF resume for any job you found yourself
"""
import argparse
import os
import smtplib
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import config
import fit
import report
import resume
import sites
import sources
import state as st

RANK = {"ats": 0, "hn": 1, "board": 2}     # prefer the employer's own listing when the same job appears twice
PDF = "application/pdf"


def collect():
    jobs, coverage = [], []
    for name, fn in sources.BOARDS.items():
        try:
            got = fn()
            jobs += got
            coverage.append((name, True, f"{len(got)} listings fetched"))
        except sources.SourceSkipped as s:
            coverage.append((name, False, f"skipped - {s}"))
        except Exception as ex:
            coverage.append((name, False, f"could not be checked ({type(ex).__name__}: {str(ex)[:80]})"))
    for name, got, note, ok in sites.fetch_all():
        jobs += got
        coverage.append((name, ok, note))
    for name in sources.ATS:
        label = f"Employer sites via {name.title()}"
        try:
            got, note = sources.fetch_ats_group(name)
            jobs += got
            coverage.append((label, True, note))
        except sources.SourceSkipped as s:
            coverage.append((label, False, f"skipped - {s}"))
        except Exception as ex:
            coverage.append((label, False, f"could not be checked ({type(ex).__name__}: {str(ex)[:80]})"))
    return jobs, coverage


def window(state, now):
    """Start of the freshness window: the last successful run, or 5 hours ago on the first run."""
    last = sources.to_dt(st.get_meta(state).get("last_run"))
    if last:
        return max(last, now - timedelta(hours=config.MAX_WINDOW_HOURS))
    return now - timedelta(hours=config.FIRST_RUN_HOURS)


def select(jobs, state, now, window_start):
    cutoff = window_start - timedelta(hours=config.OVERLAP_HOURS)
    stats = {"undated": 0, "stale": 0, "truncated": 0}
    jobs = sorted(jobs, key=lambda j: RANK.get(j["kind"], 3))
    seen_run, picks = set(), []
    for j in jobs:
        if not j["title"] or not sources.title_ok(j["title"]) or not j["remote_confirmed"]:
            continue
        key = st.fingerprint(j)
        if key in seen_run:
            continue
        seen_run.add(key)
        prev = state.get(key)
        p0 = j["posted"]
        if not prev and p0 and getattr(p0, "precise", True) and p0 < cutoff:
            stats["stale"] += 1                     # cheap early exit: published before the window
            continue
        a = fit.assess(j, now)
        if not a or a["score"] < config.MIN_SCORE:
            continue
        a["update"] = ""
        if prev:                                    # already sent: only resend on a material change
            if a["salary"] and not prev.get("salary"):
                a["update"] = "UPDATED: salary now disclosed"
            elif prev.get("status") != "ok" and a["status"] == "ok":
                a["update"] = "UPDATED: eligibility now looks open"
            else:
                continue
        else:                                       # new to us: it must have been published inside the window
            p = a["posted"]
            if not p or not getattr(p, "precise", True):
                stats["undated"] += 1               # no verifiable publish time -> cannot prove it is fresh
                continue
        picks.append(a)
    picks.sort(key=lambda x: -x["score"])
    stats["truncated"] = max(0, len(picks) - config.MAX_JOBS)
    return picks[:config.MAX_JOBS], picks[config.MAX_JOBS:], stats


def send_email(subject, text, html_body, attachments):
    user, pwd = os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"]
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, os.environ.get("TO_EMAIL") or user
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    for fname, data, mime in attachments:
        main, _, sub = mime.partition("/")
        msg.add_attachment(data, maintype=main, subtype=sub, filename=fname)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, pwd)
        s.send_message(msg)


def run_daily(dry):
    now = datetime.now(timezone.utc)                # run start; becomes the next window start
    state = st.load()
    meta = st.get_meta(state)
    sites.KNOWN.update(meta.get("site_urls", []))
    wstart = window(state, now)
    print(f"Window: {wstart.astimezone(config.TZ):%Y-%m-%d %H:%M} -> {now.astimezone(config.TZ):%Y-%m-%d %H:%M} Nairobi "
          f"(overlap {config.OVERLAP_HOURS}h for late-indexed jobs)")

    jobs, coverage = collect()
    picks, dropped, stats = select(jobs, state, now, wstart)
    healthy = sum(1 for _, ok, _ in coverage if ok) >= len(coverage) / 2    # don't advance the window after an outage

    folder = (config.PREVIEW_DIR if dry else config.RESUME_DIR) / now.astimezone(config.TZ).strftime("%Y-%m-%d")
    attachments, attached = [], set()
    for p in picks:
        try:
            p["resume"] = resume.make_resume(p, folder)
        except Exception as ex:
            p["resume"] = None
            print(f"resume failed for {p['title']} ({p['company']}): {ex}", file=sys.stderr)
        if p["resume"] and len(attachments) < config.ATTACH_MAX:
            path = Path(p["resume"]["path"])
            attachments.append((path.name, path.read_bytes(), PDF))
            attached.add(p["id"])

    subject, text, html_body = report.daily(picks, coverage, now, attached, wstart, stats)
    config.PREVIEW_DIR.mkdir(exist_ok=True)
    (config.PREVIEW_DIR / "digest_preview.html").write_text(html_body)
    print(text if picks else f"No new matches in this window. (skipped for no publish time: {stats['undated']})")
    if dry:
        print(f"\n[dry run] preview and resumes written to ./{config.PREVIEW_DIR.name}; nothing emailed, state unchanged.")
        return

    if picks or config.EMAIL_WHEN_EMPTY:
        send_email(subject, text, html_body, attachments)     # raises on failure, so state is saved only after success
        print(f"Email sent with {len(attachments)} attachment(s).")
    for p in picks:
        st.record(state, p, now, p.get("resume"))
    if healthy:
        new_last = now
        late = [d["posted"] for d in dropped if d["posted"] and not d["update"]]
        if late:                                    # overflow beyond MAX_JOBS: start the next window before it
            new_last = max(wstart, min(late) - timedelta(minutes=1))
        old = meta.get("site_urls", [])
        st.set_meta(state, last_run=new_last.isoformat(), site_urls=(old + [u for u in sites.READ if u not in old])[-4000:])
    else:
        print("Most sources failed; keeping the previous window so nothing is missed next run.", file=sys.stderr)
    st.prune(state, now)
    st.save(state)


def run_weekly(dry):
    now = datetime.now(timezone.utc)
    rows = st.week_records(st.load(), now)
    subject, text, html_body = report.weekly(rows, now)
    atts = [(f"tracker_{now.astimezone(config.TZ):%Y-%m-%d}.csv", report.week_csv(rows), "text/csv")]
    zdata, n = report.week_zip(rows)
    if n:
        atts.append((f"tailored_resumes_{now.astimezone(config.TZ):%Y-%m-%d}.zip", zdata, "application/zip"))
    config.PREVIEW_DIR.mkdir(exist_ok=True)
    (config.PREVIEW_DIR / "weekly_preview.html").write_text(html_body)
    print(text)
    if dry:
        print("\n[dry run] weekly preview written; nothing emailed.")
        return
    send_email(subject, text, html_body, atts)
    print(f"Weekly email sent ({len(rows)} roles, {n} resumes zipped).")


def run_tailor(args):
    jd = Path(args.tailor).read_text(encoding="utf-8", errors="ignore")
    job = sources.make_job("Manual", "ats", "manual", args.title, args.company, args.url or "https://example.invalid",
                           args.location or "Worldwide", None, "", jd, remote_confirmed=True)
    now = datetime.now(timezone.utc)
    a = fit.assess(job, now) or fit.assess({**job, "location": "Worldwide"}, now)
    if a is None:
        print("This job description mentions too few of your core skills to score; tailoring anyway.")
        job.update(jd_counts=fit.jd_skill_counts(job), hits=[], gaps=[], score=0, priority="n/a", elig="n/a")
        a = job
    r = resume.make_resume(a, config.PREVIEW_DIR / "manual")
    print(f"Fit score: {a['score']}/100  Priority: {a['priority']}\nEligibility: {a['elig']}")
    print("Matching skills:", ", ".join(a["hits"][:10]) or "none")
    print("Check before applying:\n - " + "\n - ".join(a["gaps"] or ["none found"]))
    print(f"Resume angle: {r['variant']}\nSaved: {r['path']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weekly", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tailor")
    ap.add_argument("--title", default="Cloud Engineer")
    ap.add_argument("--company", default="Company")
    ap.add_argument("--location")
    ap.add_argument("--url")
    args = ap.parse_args()
    if args.tailor:
        return run_tailor(args)
    (run_weekly if args.weekly else run_daily)(args.dry_run)


if __name__ == "__main__":
    main()
