"""Builds the daily and weekly emails (HTML + plain text) and the weekly CSV/zip."""
import csv
import html
import io
import zipfile
from datetime import datetime
from pathlib import Path

import config

e = html.escape


def age_text(posted, now):
    if not posted:
        return "posting date not provided"
    h = (now - posted).total_seconds() / 3600
    d = f"{posted.astimezone(config.TZ):%Y-%m-%d}"
    return f"{d} ({int(h)}h ago)" if h < 48 else f"{d} ({int(h // 24)}d ago)"


def _card(i, p, now, resume_note):
    tag = f" <span style='color:#b00'>[{e(p['update'])}]</span>" if p.get("update") else ""
    color = "#1a7f37" if p["status"] == "ok" else "#b35900"
    why = ", ".join(p["hits"][:8]) or "role title"
    r = p.get("resume") or {}
    resume_html = ""
    if r:
        resume_html = (f"<div style='background:#f4f7fb;padding:6px 8px;margin-top:6px;border-radius:6px'>"
                       f"<b>Tailored resume:</b> {e(Path(r['path']).name)} "
                       f"(base angle: <b>{e(r['variant'])}</b>; leads with: {e(r['lead'][0][:110])}…) "
                       f"<i>{e(resume_note(p))}</i></div>")
    return (f"<div style='border:1px solid #ddd;border-radius:8px;padding:12px;margin:12px 0'>"
            f"<div style='font-size:16px'><b>{i}. {e(p['title'])}</b> — {e(p['company'])}{tag}</div>"
            f"<div><b>Fit {p['score']}/100</b> · <span style='color:{color}'><b>{e(p['priority'])}</b></span></div>"
            f"<div>Your matching skills: {e(why)}</div>"
            f"<div>Posted: {e(age_text(p['posted'], now))} · via {e(p['source'])}</div>"
            f"<div>Remote eligibility: {e(p['elig'])}</div>"
            f"<div>Salary: {e(p['salary'] or 'not disclosed')}</div>"
            f"<div style='color:#555'>Check before applying: {e('; '.join(p['gaps']))}</div>"
            f"{resume_html}"
            f"<div style='margin-top:6px'><a href='{e(p['url'])}'>Apply / view listing</a></div></div>")


def daily(picks, coverage, now, attached, window_start=None, stats=None):
    stats = stats or {}
    local = now.astimezone(config.TZ)
    subject = f"Remote Engineering Jobs — Best Matches — {local:%Y-%m-%d %H:%M}"
    ok_n = sum(1 for p in picks if p["status"] == "ok")
    since = (f", published since {window_start.astimezone(config.TZ):%a %d %b %H:%M} Nairobi time"
             if window_start else "")
    summary = (f"{len(picks)} new match(es){since}: {ok_n} with eligibility looking open, {len(picks) - ok_n} needing verification. "
               if picks else f"No new strong matches{since}. ")
    summary += "Nothing here has been sent before. Eligibility is inferred from listing text, so confirm on the employer's page."
    if stats.get("undated"):
        summary += (f" {stats['undated']} otherwise-matching role(s) were skipped because the site does not publish a "
                    "posting time, so freshness could not be verified.")
    if stats.get("truncated"):
        summary += f" {stats['truncated']} more matches exceeded the per-email cap and will arrive in the next run."
    note = lambda p: "(attached)" if p["id"] in attached else "(saved in the repo under data/resumes/ and in Sunday's zip)"

    text = [subject, "", summary, ""]
    h = ["<div style='font-family:Arial,sans-serif;max-width:740px;margin:auto;color:#222'>",
         "<h2>Your Remote Engineering Job Digest</h2>", f"<p>{e(summary)}</p>"]
    for i, p in enumerate(picks, 1):
        r = p.get("resume") or {}
        text += [f"{i}. {p['title']} - {p['company']}{' [' + p['update'] + ']' if p.get('update') else ''}",
                 f"   Fit {p['score']}/100 | {p['priority']}",
                 f"   Matching skills: {', '.join(p['hits'][:8]) or 'role title'}",
                 f"   Posted: {age_text(p['posted'], now)} | via {p['source']}",
                 f"   Eligibility: {p['elig']}", f"   Salary: {p['salary'] or 'not disclosed'}",
                 f"   Check: {'; '.join(p['gaps'])}",
                 f"   Resume: {Path(r['path']).name if r else 'n/a'} ({r.get('variant', '')} angle) {note(p) if r else ''}",
                 f"   Apply: {p['url']}", ""]
        h.append(_card(i, p, now, note))
    top = picks[:3]
    if top:
        text += ["Top 3 to pursue first:"] + [f" - {p['title']} - {p['company']} ({p['score']})" for p in top] + [""]
        h.append("<h3>Top 3 to pursue first</h3><ol>" +
                 "".join(f"<li><a href='{e(p['url'])}'>{e(p['title'])}</a> — {e(p['company'])} ({p['score']})</li>" for p in top) + "</ol>")
    verify = [p for p in picks if p["status"] == "verify"]
    if verify:
        text += ["Eligibility uncertain: " + "; ".join(f"{p['title']} ({p['company']})" for p in verify), ""]
        h.append("<h3>Eligibility uncertain</h3><ul>" +
                 "".join(f"<li>{e(p['title'])} — {e(p['company'])}: {e(p['elig'])}</li>" for p in verify) + "</ul>")
    text.append("Search coverage:")
    h.append("<h3>Search coverage</h3><ul>")
    for name, ok, msg in coverage:
        text.append(f" - {name}: {msg}")
        h.append(f"<li>{'✅' if ok else '⚠️'} <b>{e(name)}</b>: {e(msg)}</li>")
    nc = ("Not covered: LinkedIn, Indeed, Wellfound and Y Combinator directly (they block automated access; the optional "
          "JSearch source reaches some LinkedIn/Indeed listings). Employer pages only for companies in companies.json.")
    text.append(nc)
    h.append(f"</ul><p style='color:#666'>{e(nc)}</p></div>")
    return subject, "\n".join(text), "".join(h)


def weekly(rows, now):
    end = now.astimezone(config.TZ)
    subject = f"Weekly Remote Engineering Jobs Roundup — week ending {end:%Y-%m-%d}"
    groups = {"Apply now": [], "Apply": [], "Verify eligibility first": []}
    for r in rows:
        groups.setdefault(r["priority"], []).append(r)
    by_src = {}
    for r in rows:
        by_src[r["source"]] = by_src.get(r["source"], 0) + 1
    summary = (f"{len(rows)} roles were sent to you in the last 7 days: {len(groups['Apply now'])} 'Apply now', "
               f"{len(groups['Apply'])} 'Apply', {len(groups['Verify eligibility first'])} to verify. "
               "Attached: the full tracker (CSV) and every tailored resume from the week (zip). "
               "Postings may have closed since they were sent, so check the link before applying.")
    text = [subject, "", summary, ""]
    h = ["<div style='font-family:Arial,sans-serif;max-width:820px;margin:auto;color:#222'>",
         "<h2>Weekly Roundup</h2>", f"<p>{e(summary)}</p>"]
    if by_src:
        line = ", ".join(f"{k}: {v}" for k, v in sorted(by_src.items(), key=lambda x: -x[1]))
        text += [f"By source: {line}", ""]
        h.append(f"<p style='color:#555'><b>By source:</b> {e(line)}</p>")
    for g in ("Apply now", "Apply", "Verify eligibility first"):
        items = groups.get(g) or []
        if not items:
            continue
        text.append(f"== {g} ({len(items)}) ==")
        h.append(f"<h3>{e(g)} ({len(items)})</h3><table style='border-collapse:collapse;width:100%;font-size:13px'>"
                 "<tr style='background:#eef2f7;text-align:left'><th>Fit</th><th>Role</th><th>Eligibility</th><th>Salary</th><th>Sent</th></tr>")
        for r in items:
            sent = datetime.fromisoformat(r["sent"]).astimezone(config.TZ).strftime("%a %d %b")
            text.append(f" {r['score']:>3}  {r['title']} - {r['company']} | {r['elig']} | {r['salary'] or 'no salary'} | {sent}\n      {r['url']}")
            h.append(f"<tr style='border-top:1px solid #ddd;vertical-align:top'><td><b>{r['score']}</b></td>"
                     f"<td><a href='{e(r['url'])}'>{e(r['title'])}</a><br><span style='color:#666'>{e(r['company'])} · {e(r['source'])}</span></td>"
                     f"<td>{e(r['elig'])}</td><td>{e(r['salary'] or '—')}</td><td>{e(sent)}</td></tr>")
        h.append("</table>")
        text.append("")
    if not rows:
        h.append("<p>Nothing was sent this week.</p>")
    h.append("</div>")
    return subject, "\n".join(text), "".join(h)


def week_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["sent", "fit_score", "priority", "title", "company", "eligibility", "salary", "posted", "source", "url", "resume_angle", "resume_file", "applied (fill in)"])
    for r in rows:
        w.writerow([r["sent"][:10], r["score"], r["priority"], r["title"], r["company"], r["elig"], r["salary"],
                    r["posted"][:10], r["source"], r["url"], r["variant"], Path(r["resume"]).name if r["resume"] else "", ""])
    return buf.getvalue().encode("utf-8-sig")


def week_zip(rows):
    buf = io.BytesIO()
    n = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in rows:
            p = Path(r["resume"]) if r["resume"] else None
            if p and p.exists():
                z.write(p, arcname=p.name)
                n += 1
    return buf.getvalue(), n
