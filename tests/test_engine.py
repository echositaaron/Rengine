"""Offline tests (no network, no email). Run from the project folder:  python -m unittest discover -s tests -v"""
import pathlib
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config
import fit
import job_digest as jd
import resume
import sources
import state as st

NOW = datetime.now(timezone.utc)
DESC = "Kubernetes, Terraform, AWS, observability (Prometheus, Grafana), incident response, CI/CD. 4+ years experience."
mk = sources.make_job


class TitleAndEligibility(unittest.TestCase):
    def test_titles(self):
        bad = ["Senior Specialist Solutions Engineer, Cloudflare One - Russian or Hebrew speaking",
               "Senior Security Engineer, Detection & Response", "Cloud Security Analyst", "Sales Engineer, Cloud",
               "Data Engineer, Platform", "Sr. Staff Security Software Engineer"]
        good = ["Cloud Network Engineer", "Internal Developer Platform Engineer", "Senior Site Reliability Engineer",
                "Platform Engineer, Managed Postgres", "DevOps Engineer (Azure)", "Cloud Security Engineer"]
        for t in bad:
            self.assertFalse(sources.title_ok(t), t)
        for t in good:
            self.assertTrue(sources.title_ok(t), t)

    def test_eligibility(self):
        want = {"Remote - California": "no", "Austria": "no", "Cyprus": "no", "Anywhere in the US": "no",
                "Remote - Africa": "ok", "Worldwide": "ok", "Anywhere in the World": "ok", "Remote": "verify",
                "EMEA": "verify", "Remote - Nigeria": "verify", "Global (US, UK preferred)": "verify", "": "verify"}
        for loc, status in want.items():
            j = mk("x", "board", "1", "SRE", "c", "http://x", loc, NOW, "", "")
            self.assertEqual(fit.classify_eligibility(j)[0], status, loc)

    def test_core_skill_gate(self):
        weak = mk("x", "board", "2", "Cloud Security Engineer", "c", "http://x", "Worldwide", NOW, "", "Security, compliance, python.")
        self.assertIsNone(fit.assess(weak, NOW))

    def test_money_and_dates(self):
        self.assertEqual(sources.money("US$119.9k - US$193.2k", None), "119,900-193,200")
        self.assertEqual(sources.money(90000, "120,000", "USD"), "USD 90,000-120,000")
        self.assertEqual(sources.money(None, None), "")
        self.assertFalse(sources.to_dt("2026-10-22").precise)
        self.assertTrue(sources.to_dt("2026-10-22T10:00:00Z").precise)
        self.assertTrue(sources.to_dt(1761120000).precise)


class Runs(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self._saved = (config.STATE_FILE, config.RESUME_DIR, config.PREVIEW_DIR, jd.send_email, jd.collect, jd.datetime)
        config.STATE_FILE, config.RESUME_DIR, config.PREVIEW_DIR = self.tmp / "seen.json", self.tmp / "resumes", self.tmp / "preview"
        self.sent = []
        jd.send_email = lambda s, t, h, a: self.sent.append([x[0] for x in a])

    def tearDown(self):
        config.STATE_FILE, config.RESUME_DIR, config.PREVIEW_DIR, jd.send_email, jd.collect, jd.datetime = self._saved

    def test_windows_dedupe_and_empty(self):
        later = NOW + timedelta(hours=5)

        class Later(datetime):
            @classmethod
            def now(cls, tz=None):
                return later

        jd.collect = lambda: ([
            mk("Ashby", "ats", "a", "Senior SRE", "FreshCo", "https://a", "Remote - Worldwide", NOW - timedelta(hours=2), "", DESC),
            mk("Greenhouse", "ats", "b", "Platform Engineer", "StaleCo", "https://b", "Remote - Africa", NOW - timedelta(hours=9), "", DESC),
            mk("Remotive", "board", "c", "DevOps Engineer", "DateOnlyCo", "https://c", "Worldwide", sources.to_dt("2026-10-22"), "", DESC),
            mk("Remotive", "board", "d", "DevOps Engineer", "NoDateCo", "https://d", "Worldwide", None, "", DESC),
            mk("Lever", "ats", "e", "Cloud Engineer", "USCo", "https://e", "Remote - California", NOW - timedelta(hours=1), "", DESC),
        ], [("Src", True, "ok")])
        jd.run_daily(False)                                   # run 1: no previous run -> last 5 hours only
        self.assertEqual(self.sent, [["Aaron_Echosit_FreshCo_Senior_SRE.pdf"]])

        jd.datetime = Later
        jd.collect = lambda: ([
            mk("Ashby", "ats", "a", "Senior SRE", "FreshCo", "https://a", "Remote - Worldwide", NOW - timedelta(hours=2), "", DESC),  # already sent
            mk("Ashby", "ats", "f", "Infrastructure Engineer", "LateCo", "https://f", "Remote - Worldwide", later - timedelta(hours=6), "", DESC),  # late-indexed, inside overlap
            mk("Ashby", "ats", "g", "Cloud Engineer", "NewCo", "https://g", "Remote - Worldwide", later - timedelta(hours=1), "", DESC),
            mk("Ashby", "ats", "h", "SRE", "OldCo", "https://h", "Remote - Worldwide", later - timedelta(hours=12), "", DESC),         # too old
        ], [("Src", True, "ok")])
        self.sent.clear()
        jd.run_daily(False)                                   # run 2: since last run (+overlap), never repeats
        self.assertEqual(sorted(self.sent[0]), ["Aaron_Echosit_LateCo_Infrastructure_Engineer.pdf", "Aaron_Echosit_NewCo_Cloud_Engineer.pdf"])

        jd.collect = lambda: ([], [("Src", True, "ok")])
        self.sent.clear()
        jd.run_daily(False)                                   # run 3: nothing new -> no email
        self.assertEqual(self.sent, [])

    def test_failed_sources_do_not_advance_window(self):
        jd.collect = lambda: ([], [("A", False, "down"), ("B", False, "down"), ("C", True, "ok")])
        jd.run_daily(False)
        self.assertNotIn("last_run", st.get_meta(st.load()))


class Resume(unittest.TestCase):
    def test_pdf_is_two_pages_or_less_and_truthful(self):
        j = mk("Ashby", "ats", "a", "Senior SRE", "Acme", "https://a", "Worldwide", NOW, "", DESC + " Go and Istio.")
        a = fit.assess(j, NOW)
        out = pathlib.Path(tempfile.mkdtemp())
        r = resume.make_resume(a, out)
        data = pathlib.Path(r["path"]).read_bytes()
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertLessEqual(len(re.findall(rb"/Type\s*/Page[^s]", data)), 2)
        # every bullet in the tailored resume must exist verbatim in profile.json
        allowed = {b["t"] for role in fit.PROFILE["experience"] for b in role["bullets"]}
        for role in resume.tailor(a)["experience"]:
            self.assertTrue(set(role["bullets"]) <= allowed)
        self.assertIn("Go/Golang", " ".join(a["gaps"]))

    def test_prune_uses_folder_dates(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        saved = config.RESUME_DIR
        config.RESUME_DIR = tmp
        try:
            old = tmp / (NOW - timedelta(days=60)).strftime("%Y-%m-%d")
            new = tmp / NOW.strftime("%Y-%m-%d")
            for d in (old, new):
                d.mkdir()
                (d / "x.pdf").write_bytes(b"%PDF")
            st.prune({}, NOW)
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())
        finally:
            config.RESUME_DIR = saved


if __name__ == "__main__":
    unittest.main()
