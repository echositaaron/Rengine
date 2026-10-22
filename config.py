"""Settings you may want to tweak. Candidate data lives in profile.json; employers in companies.json."""
import re
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
DATA = ROOT / "data"
STATE_FILE = DATA / "seen_jobs.json"
RESUME_DIR = DATA / "resumes"
PREVIEW_DIR = ROOT / "preview"

TZ = ZoneInfo("Africa/Nairobi")

# ---- freshness window -------------------------------------------------------
FIRST_RUN_HOURS = 5         # first ever run: only jobs published in the last 5 hours
MAX_WINDOW_HOURS = 48       # later runs: everything since the last run, but never reach back further than this
OVERLAP_HOURS = 2           # also accept jobs published up to 2h before the last run (feeds often list jobs late);
                            # already-sent jobs are never repeated, so this only catches late-indexed ones. 0 = strict.
EMAIL_WHEN_EMPTY = False    # no email when nothing new was found

# ---- selection --------------------------------------------------------------
MIN_SCORE = 60              # minimum fit score to include
MIN_CORE_SKILLS = 3         # a job must mention at least this many of CORE_SKILLS (2 for short Hacker News posts)
MAX_JOBS = 40               # cap per email (if exceeded, the overflow is picked up by the next run)
ATTACH_MAX = 12             # PDFs attached to each email (all are saved in data/resumes/)
STATE_KEEP_DAYS = 120
RESUME_KEEP_DAYS = 45
HEADERS = {"User-Agent": "Mozilla/5.0 (personal job digest)"}

# Skills that define a real match for your profile (names from profile.json "skills").
CORE_SKILLS = {"kubernetes", "terraform", "azure", "aws", "gcp", "ci/cd", "helm", "docker", "ansible", "observability",
               "prometheus", "grafana", "datadog", "incident response", "reliability", "finops", "gitops",
               "platform engineering", "backup/dr", "azure devops", "elk"}


def _words(terms):
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(t) for t in terms) + r")(?![a-z0-9])", re.I)


# A listing's title must contain one of these WHOLE words/phrases ("cloud" no longer matches "Cloudflare").
TITLE_TERMS = [
    "devops", "devsecops", "sre", "site reliability", "reliability", "platform engineer", "platform engineering",
    "cloud", "infrastructure", "kubernetes", "systems engineer", "production engineer", "observability", "finops",
    "terraform", "release engineer", "build engineer", "container", "cloudops", "technical operations",
    "ai platform", "gpu", "developer productivity", "developer experience", "cloud security", "infrastructure security",
]
# ...and none of these.
TITLE_BLOCK = [
    "sales", "account executive", "recruiter", "marketing", "customer success", "account manager", "business development",
    "intern", "internship", "solutions architect", "solutions engineer", "sales engineer", "support engineer",
    "technical account", "customer engineer", "presales", "pre-sales", "product manager", "designer", "data scientist",
    "data engineer", "machine learning", "ml engineer", "analyst", "frontend", "front-end", "front end", "ios",
    "android", "mobile", "appsec", "application security", "grc", "penetration", "pentest", "detection", "threat",
    "security software", "qa", "embedded", "firmware", "hardware", "electrical",
]
TITLE_RE = _words(TITLE_TERMS)
BLOCK_RE = _words(TITLE_BLOCK)
LANG_RE = re.compile(r"\b[a-z]+(?:\s+(?:or|and|/)\s+[a-z]+)?[- ]speaking\b", re.I)   # e.g. "Russian or Hebrew speaking"

_US_STATES = ("alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|"
              "illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|"
              "missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|"
              "ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|"
              "virginia|washington|west virginia|wisconsin|wyoming")
_COUNTRIES = ("austria|belgium|bulgaria|croatia|cyprus|czech republic|czechia|denmark|estonia|finland|greece|hungary|"
              "italy|latvia|lithuania|luxembourg|malta|norway|romania|serbia|slovakia|slovenia|sweden|switzerland|turkey|"
              "ukraine|england|scotland|wales|argentina|chile|colombia|peru|costa rica|pakistan|bangladesh|vietnam|"
              "indonesia|malaysia|thailand|china|korea|hong kong|taiwan|uae|united arab emirates|saudi arabia|qatar|dubai|oceania")

WORLDWIDE = re.compile(r"\b(worldwide|world wide|anywhere|global|globally|work from anywhere|any country)\b", re.I)
AFRICA = re.compile(r"\b(kenya|nairobi|africa|east africa)\b", re.I)
OTHER_AFRICA = re.compile(r"\b(south africa|nigeria|egypt|ghana|morocco|tunisia|uganda|tanzania|rwanda|ethiopia|senegal)\b", re.I)
EMEA = re.compile(r"\b(emea|europe, middle east)\b", re.I)
RESTRICTED_REGION = re.compile(
    r"\b(us|usa|u\.s\.|united states|uk|united kingdom|canada|europe|european union|eu|germany|france|spain|netherlands|"
    r"poland|australia|new zealand|latam|latin america|north america|americas|india|brazil|mexico|philippines|ireland|"
    r"portugal|israel|singapore|japan|apac|asia|london|berlin|toronto|sydney|san francisco|"
    + _US_STATES + "|" + _COUNTRIES + r")\b", re.I)
RESTRICT_DESC = re.compile(
    r"(must (be|reside|live)( located| based)? in|only (open|available) to (candidates|residents)|"
    r"authori[sz]ed to work in|work authori[sz]ation|(us|u\.s\.) citizens? only|"
    r"security clearance|must be a resident|right to work in)", re.I)
REMOTE_IN_DESC = re.compile(r"(fully remote|100% remote|remote[- ]first|this is a remote|remote position|remote role|work from anywhere|distributed team)", re.I)
