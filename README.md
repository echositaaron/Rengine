# Remote Job Engine

Searches remote job boards and employer career sites every 5 hours, scores each role against your real resumes,
builds a tailored 2-page PDF resume for every match, and emails you only roles published since the last run.
Every Sunday it emails a roundup of the whole week with a tracker CSV and a zip of all tailored resumes.

## How a run works
1. **Window.** First ever run: jobs published in the last 5 hours. Every later run: jobs published since the last
   successful run (plus `OVERLAP_HOURS`, default 2, to catch jobs a feed listed late; already-sent jobs never repeat).
   Roles with no verifiable publish time (no time of day) are skipped and counted in the email.
2. **Relevance.** Whole-word title match, blocked titles (sales, solutions engineer, security-only, data, language-specific...),
   a core-skill check (>= 3 of your core skills in the description), Kenya-eligibility check (worldwide / Africa / Kenya
   only; US/EU/etc. dropped; unclear -> "Verify eligibility first"), then the 100-point fit score.
3. **Resume.** Picks the closest of your 5 resume angles, then selects and orders YOUR existing bullets, stats, skills,
   projects and certifications for the job. It never rewrites a bullet or adds a skill.
4. **Email.** Roles ranked by score, with gaps (skills/years the job wants that your resumes don't show), plus up to 12 PDFs.
   No email is sent when nothing new was found.
5. **Memory.** `data/seen_jobs.json` stores what was sent and when the last run happened; `data/resumes/<date>/` keeps the PDFs.

## One-time setup
1. Gmail app password (Google Account -> Security -> 2-Step Verification -> App passwords).
2. Repo secrets (Settings -> Secrets and variables -> Actions): `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `TO_EMAIL`.
   Optional: `RAPIDAPI_KEY` (JSearch, free tier) to also reach LinkedIn/Indeed/Glassdoor listings.
3. Settings -> Actions -> General -> Workflow permissions -> **Read and write**.
4. Keep the repo **private**.
5. Actions tab -> "Remote job engine" -> Run workflow -> `daily`, tick **dry run** -> open the `preview` download.

Work outside OneDrive (for example `C:\dev\Rengine`) and run `git pull` before every push: the bot commits after runs.

## Files
| File | Purpose |
|---|---|
| `profile.json` | Your resume content (the only source of resume text). Verify the links before applying. |
| `config.py` | Window, thresholds, title rules, eligibility rules, caps. |
| `companies.json` | Employer career sites (Greenhouse / Lever / Ashby slugs). Remove any the email reports as not found. |
| `sites.json` | Extra job boards read generically (feed, or job pages' structured data). |
| `sources.py`, `sites.py` | Fetchers. |
| `fit.py` | Eligibility + fit scoring + gaps. |
| `resume.py` | Tailoring and PDF generation. |
| `report.py`, `state.py`, `job_digest.py` | Emails, memory, main program. |
| `tests/test_engine.py` | Offline tests: `python -m unittest discover -s tests -v` |
| `.github/workflows/daily.yml` | Schedule: every 5 hours + Sunday roundup. |

## Common changes
- **Schedule:** edit the `cron` lines in `.github/workflows/daily.yml` (times are UTC; Nairobi = UTC+3).
- **Strict 5 hours (no late-listed jobs):** `OVERLAP_HOURS = 0` in `config.py`.
- **Get an email even when nothing is new:** `EMAIL_WHEN_EMPTY = True`.
- **Add a job board:** append to `sites.json`: `{"name": "Site", "remote_only": true, "urls": ["https://site.com/remote-devops-jobs"]}`.
- **Add an employer:** add its slug to `companies.json` (from `boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`, `jobs.ashbyhq.com/<slug>`).
- **Job you found yourself:** save the description to `jd.txt`, then
  `python job_digest.py --tailor jd.txt --title "Senior SRE" --company "Acme" --location "Remote - Worldwide"`.

## Limits
- Eligibility is inferred from listing text. "Verify eligibility first" means exactly that.
- LinkedIn, Indeed, Wellfound and YC block automated access and are not scraped (JSearch reaches some of their listings).
- Some boards block automated requests or publish only dates, not times; they appear in the email's coverage section.
- Years-of-experience checks count from Feb 2022 (`career_start` in profile.json).
- Reading some sites may be restricted by their terms; check the ones you rely on.
