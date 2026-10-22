"""Builds a job-specific resume from profile.json.

Truthfulness rule: this module only SELECTS, ORDERS and EMPHASIZES content that already exists in
profile.json (taken from your master resumes). It never rewrites a bullet or adds a skill.
"""
import re
from pathlib import Path

from xml.sax.saxutils import escape as esc

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import fit
from fit import PROFILE

W = PROFILE["skills"]


def _weight(skills, counts):
    return sum(W[k]["w"] * (1 + 0.5 * min(counts.get(k, 0), 4)) for k in skills if k in counts)


def choose_variant(job):
    title = f"{job['title']} {' '.join(job['tags'])}".lower()
    body = job["desc"].lower()
    best, best_score = "cloud", -1
    for name, v in PROFILE["variants"].items():
        s = 0
        for kw in v["keywords"]:
            r = fit.term_re(kw)
            s += 3 * len(r.findall(title)) + min(len(r.findall(body)), 3)
        if s > best_score:
            best, best_score = name, s
    return best


def tailor(job):
    counts = job.get("jd_counts") or fit.jd_skill_counts(job)
    variant = choose_variant(job)
    v = PROFILE["variants"][variant]

    experience = []
    for role in PROFILE["experience"]:
        scored = []
        for i, b in enumerate(role["bullets"]):
            s = _weight(fit.text_skills(b["t"]), counts) + (0.4 if re.search(r"\d", b["t"]) else 0) + (1.5 if b["pin"] else 0)
            scored.append((s, -i, b["t"]))
        scored.sort(reverse=True)
        relevant = sum(1 for s, _, _ in scored if s >= 2)
        n = max(role["min"], min(role["max"], relevant))
        if role["title"].startswith("Software Engineer"):
            n = role["max"] if ("java" in counts or "messaging" in counts) else role["min"]
        experience.append({**{k: role[k] for k in ("title", "company", "dates")}, "bullets": [t for _, _, t in scored[:n]]})

    pool = []
    for i, it in enumerate(PROFILE["impact_pool"]):
        s = (3 if it.get("pin") else 0) + _weight(it["terms"], counts)
        pool.append((s, -i, it["text"]))
    pool.sort(reverse=True)
    impact = [t for _, _, t in pool[:8]]

    lines = []
    for i, ln in enumerate(PROFILE["skill_lines"]):
        scored_items = []
        for j, item in enumerate(ln["items"]):
            m = fit.text_skills(item) & set(counts)
            scored_items.append((_weight(m, counts), -j, item))
        total = sum(s for s, _, _ in scored_items)
        scored_items.sort(reverse=True)
        lines.append((total, -i, ln["label"], [it for _, _, it in scored_items]))
    lines.sort(reverse=True)
    skill_lines = [(lab, items) for _, _, lab, items in lines[:9]]

    projects = sorted(PROFILE["projects"], key=lambda p: -_weight(p["terms"], counts))[:3]
    certs = sorted(PROFILE["certifications"], key=lambda c: -_weight(c["terms"], counts))

    return {"variant": variant, "headline": v["headline"], "summary": v["summary"], "impact": impact,
            "skill_lines": skill_lines, "experience": experience, "projects": projects,
            "certs": [c["name"] for c in certs]}


# ------------------------------- PDF --------------------------------------
NAVY = colors.HexColor("#1F2A44")
_B = dict(fontName="Helvetica", fontSize=9.5, leading=12, textColor=colors.black)
ST = {
    "name": ParagraphStyle("name", **{**_B, "fontName": "Helvetica-Bold", "fontSize": 20, "leading": 23, "textColor": NAVY}, alignment=TA_CENTER),
    "headline": ParagraphStyle("headline", **{**_B, "fontName": "Helvetica-Bold", "fontSize": 10.5, "leading": 13}, alignment=TA_CENTER, spaceAfter=1),
    "contact": ParagraphStyle("contact", **{**_B, "fontSize": 9, "leading": 11}, alignment=TA_CENTER),
    "h": ParagraphStyle("h", **{**_B, "fontName": "Helvetica-Bold", "fontSize": 10.5, "leading": 13, "textColor": NAVY}, spaceBefore=7),
    "body": ParagraphStyle("body", **_B, spaceAfter=1.6),
    "bullet": ParagraphStyle("bullet", **_B, leftIndent=11, bulletIndent=2, spaceAfter=1.3),
    "role": ParagraphStyle("role", **{**_B, "fontName": "Helvetica-Bold", "fontSize": 9.8}),
    "date": ParagraphStyle("date", **{**_B, "fontName": "Helvetica-Oblique", "fontSize": 9.2}, alignment=TA_RIGHT),
}


def _heading(story, text):
    story.append(Paragraph(esc(text.upper()), ST["h"]))
    story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#555555"), spaceBefore=1, spaceAfter=3))


def build_pdf(t, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=1.7 * cm, rightMargin=1.7 * cm,
                            topMargin=1.4 * cm, bottomMargin=1.3 * cm,
                            title=f"{PROFILE['name'].title()} - Resume", author=PROFILE["name"].title())
    width = A4[0] - 3.4 * cm
    c = PROFILE["contact"]
    story = [Paragraph(esc(PROFILE["name"]), ST["name"]), Paragraph(esc(t["headline"]), ST["headline"]),
             Paragraph(esc(f"{c['location']} | {c['phone']} | {c['email']}"), ST["contact"]),
             Paragraph(esc(" | ".join(c["links"])), ST["contact"])]

    _heading(story, "Professional Summary")
    story.append(Paragraph(esc(t["summary"]), ST["body"]))
    story.append(Paragraph("<b>Selected impact:</b> " + esc("  |  ".join(t["impact"])), ST["body"]))

    _heading(story, "Core Competencies & Technical Skills")
    for label, items in t["skill_lines"]:
        story.append(Paragraph(f"<b>{esc(label)}:</b> {esc(', '.join(items))}", ST["body"]))

    _heading(story, "Professional Experience")
    for role in t["experience"]:
        row = Table([[Paragraph(esc(f"{role['title']} — {role['company']}"), ST["role"]), Paragraph(esc(role["dates"]), ST["date"])]],
                    colWidths=[width - 4.4 * cm, 4.4 * cm])
        row.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                 ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                                 ("VALIGN", (0, 0), (-1, -1), "BOTTOM")]))
        bullets = [Paragraph(esc(b), ST["bullet"], bulletText="•") for b in role["bullets"]]
        story.append(Spacer(1, 3))
        story.append(KeepTogether([row] + bullets[:1]))
        story.extend(bullets[1:])

    _heading(story, "Selected Projects")
    for pr in t["projects"]:
        story.append(Paragraph(f"<b>{esc(pr['name'])}:</b> {esc(pr['text'])}", ST["body"]))
    _heading(story, "Certifications")
    story.append(Paragraph(esc(" | ".join(t["certs"])), ST["body"]))
    _heading(story, "Education")
    story.append(Paragraph(esc(PROFILE["education"]), ST["body"]))
    doc.build(story)
    return path


def safe_name(job):
    base = f"Aaron_Echosit_{job['company']}_{job['title']}"
    return re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_")[:90] + ".pdf"


def make_resume(job, folder):
    t = tailor(job)
    path = Path(folder) / safe_name(job)
    n = 2
    while path.exists():                     # same company+title twice in one day
        path = Path(folder) / safe_name(job).replace(".pdf", f"_{n}.pdf")
        n += 1
    build_pdf(t, path)
    return {"path": str(path), "variant": t["variant"], "lead": t["experience"][0]["bullets"][:2]}
