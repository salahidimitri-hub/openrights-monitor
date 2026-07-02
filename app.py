#!/usr/bin/env python3
"""
app.py -- OpenRights Monitor / Ava, complete and live, in ONE file.

Zero dependencies. Standard library only. To run her:

    python app.py
    # then open http://localhost:8000

She will:
  1. fetch public reports from RSS feeds on a schedule (urllib)
  2. extract structured facts with rules only (no AI key, no cost)
  3. cluster related reports into single incidents
  4. store everything in a local SQLite file (no database server)
  5. score each country with the transparent v0.2 methodology
  6. read those real numbers and respond in her own voice

Ava herself is unchanged. The world replaces hand-entered numbers.

DEPLOY (free): put this file on GitHub, then on Render / Fly.io use
start command:  python app.py   (it reads the PORT env var automatically)
"""

import os
import re
import json
import time
import random
import sqlite3
import threading
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB_PATH = os.environ.get("ORM_DB", "orm.db")
PORT = int(os.environ.get("PORT", "8000"))
WINDOW_DAYS = 30
INGEST_EVERY_SECONDS = 60 * 60  # hourly

# Trusted public feeds. Edit freely. More INDEPENDENT feeds = higher confidence
# and broader country coverage. Each source is fetched independently; if any one
# feed is unreachable or malformed, it simply contributes nothing that cycle --
# a dead URL can never break ingestion, it just yields zero items. Feeds marked
# below are a mix of dedicated human-rights monitors and general world-news
# desks, so corroboration can form ACROSS independent newsrooms, not just within
# one. (Verified reachable as of mid-2026; URLs drift over time, so prune/refresh
# as needed.)
FEEDS = [
    # --- Dedicated human-rights / humanitarian monitors ---
    ("HRW", "https://www.hrw.org/rss/news"),
    ("Amnesty", "https://www.amnesty.org/en/latest/news/rss/"),
    ("UN News", "https://news.un.org/feed/subscribe/en/news/all/rss.xml"),
    ("ReliefWeb", "https://reliefweb.int/updates/rss.xml"),
    # --- Independent global-news desks (broaden country coverage) ---
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("Guardian World", "https://www.theguardian.com/world/rss"),
    ("France 24", "https://www.france24.com/en/rss"),
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
]


# =============================================================================
# 1. FUNDAMENTAL RIGHTS (UDHR) + severity anchors
# =============================================================================
RIGHTS = [
    ("Dignity and Equality", 2), ("Freedom from Discrimination", 3),
    ("Life, Liberty and Security", 5), ("Freedom from Slavery", 5),
    ("Freedom from Torture", 5), ("Recognition Before the Law", 2),
    ("Equality Before the Law", 2), ("Right to Remedy", 2),
    ("Freedom from Arbitrary Detention", 4), ("Fair Hearing", 3),
    ("Presumption of Innocence", 3), ("Privacy", 2),
    ("Freedom of Movement", 3), ("Asylum", 3), ("Nationality", 2),
    ("Family", 2), ("Property", 2), ("Thought and Religion", 2),
    ("Opinion and Expression", 2), ("Peaceful Assembly", 2),
    ("Participation in Government", 3), ("Social Security", 1),
    ("Work", 1), ("Rest and Leisure", 1), ("Adequate Standard of Living", 3),
    ("Education", 1), ("Cultural Life", 1), ("A Just Order", 1),
    ("Community and Limits", 1), ("Protection of Rights", 2),
]
ANCHOR = {name: a for name, a in RIGHTS}


# =============================================================================
# 2. KEYLESS EXTRACTOR (rules only)
#    Detects country + implicated rights from report text. No model, no cost.
#    Extracts signals; never decides truth. Unknowns stay null.
# =============================================================================
COUNTRIES = [
    "Afghanistan","Albania","Algeria","Angola","Argentina","Armenia","Australia",
    "Austria","Azerbaijan","Bahrain","Bangladesh","Belarus","Belgium","Bolivia",
    "Bosnia","Brazil","Bulgaria","Burkina Faso","Burundi","Cambodia","Cameroon",
    "Canada","Central African Republic","Chad","Chile","China","Colombia","Congo",
    "Croatia","Cuba","Cyprus","Czech","Denmark","Djibouti","Ecuador","Egypt",
    "El Salvador","Eritrea","Estonia","Ethiopia","Finland","France","Gabon",
    "Gambia","Georgia","Germany","Ghana","Greece","Guatemala","Guinea","Haiti",
    "Honduras","Hungary","India","Indonesia","Iran","Iraq","Ireland","Israel",
    "Italy","Ivory Coast","Jamaica","Japan","Jordan","Kazakhstan","Kenya",
    "Kosovo","Kuwait","Kyrgyzstan","Laos","Latvia","Lebanon","Liberia","Libya",
    "Lithuania","Madagascar","Malawi","Malaysia","Maldives","Mali","Mauritania",
    "Mexico","Moldova","Mongolia","Montenegro","Morocco","Mozambique","Myanmar",
    "Namibia","Nepal","Netherlands","New Zealand","Nicaragua","Niger","Nigeria",
    "North Korea","Norway","Oman","Pakistan","Palestine","Panama","Paraguay",
    "Peru","Philippines","Poland","Portugal","Qatar","Romania","Russia","Rwanda",
    "Saudi Arabia","Senegal","Serbia","Sierra Leone","Singapore","Slovakia",
    "Slovenia","Somalia","South Africa","South Korea","South Sudan","Spain",
    "Sri Lanka","Sudan","Sweden","Switzerland","Syria","Taiwan","Tajikistan",
    "Tanzania","Thailand","Togo","Tunisia","Turkey","Turkmenistan","Uganda",
    "Ukraine","United Arab Emirates","United Kingdom","United States","Uruguay",
    "Uzbekistan","Venezuela","Vietnam","Yemen","Zambia","Zimbabwe",
]

RIGHTS_KEYWORDS = {
    "Life, Liberty and Security": ["killed","killing","massacre","shot dead","airstrike",
        "air strike","bombing","shelling","executed","execution","extrajudicial",
        "summary execution","atrocity","atrocities","slain","genocide","war crime",
        "war crimes","crimes against humanity","mass grave","mass graves","unlawful killing"],
    "Freedom from Torture": ["torture","tortured","ill-treatment","mutilated"],
    "Freedom from Slavery": ["slavery","enslaved","human trafficking","forced labour","forced labor"],
    "Freedom from Arbitrary Detention": ["detained","detention","detentions","detainee",
        "detainees","arrested","arrest","arrests","jailed","imprisoned","abducted",
        "disappeared","enforced disappearance","political prisoner","political prisoners",
        "prisoners of conscience"],
    "Opinion and Expression": ["journalist","journalists","censorship","press freedom",
        "silenced","free speech","crackdown on media"],
    "Peaceful Assembly": ["protest","protesters","demonstration","demonstrators","rally"],
    "Thought and Religion": ["religious persecution","church burned","mosque attacked","worship banned"],
    "Freedom of Movement": ["displaced","displacement","refugees","fled","forced into exile"],
    "Freedom from Discrimination": ["ethnic cleansing","discrimination","persecution of minority",
        "minority","apartheid"],
    "Adequate Standard of Living": ["famine","starvation","denied aid","humanitarian blockade","hunger crisis"],
    "Participation in Government": ["election fraud","rigged election","banned candidate","stolen vote"],
}

# --- Signal-quality tiers for the noise filter (applied to EVERY country the
# --- same way; this judges the strength of the human-rights signal, never who
# --- the article is about). STRONG terms establish a rights concern on their
# --- own. When only weak/ambiguous words match, a concern is recorded only if
# --- the passage also carries human-rights CONTEXT and is not dominated by a
# --- non-rights domain (crime blotter, sports, markets, entertainment,
# --- natural disaster). This is what stops "man killed in a robbery" or "striker
# --- shot the winning goal" from being logged as human-rights violations.
_STRONG_TERMS = [
    "massacre","airstrike","air strike","shelling","bombing","executed","execution",
    "extrajudicial","summary execution","atrocity","atrocities","slain","genocide",
    "ethnic cleansing","war crime","war crimes","crimes against humanity","torture",
    "tortured","ill-treatment","mutilated","enforced disappearance","abducted",
    "apartheid","political prisoner","political prisoners","prisoners of conscience",
    "crackdown on media","press freedom","censorship","famine","starvation",
    "humanitarian blockade","denied aid","forced labour","forced labor","enslaved",
    "slavery","human trafficking","religious persecution","persecution of minority",
    "unlawful killing","mass grave","mass graves","shot dead",
]
_HRCTX_TERMS = [
    "civilian","civilians","detainee","detainees","activist","activists","dissident",
    "dissidents","protester","protesters","demonstrator","demonstrators","journalist",
    "journalists","opposition","regime","security forces","government forces","junta",
    "authorities","human rights","rights group","rights groups","watchdog","persecuted",
    "systematic","arbitrary","detention","prisoner","prisoners","refugee","refugees",
    "displaced","minority","minorities","war","conflict","militia","paramilitary",
    "crackdown","repression","dissent","unlawful","genocidal",
]
_NOISE_TERMS = [
    # ordinary crime blotter (individual crime, not state violence)
    "robbery","burglary","mugging","carjacking","shoplifting","drunk driving","dui",
    "car crash","road accident","traffic accident","collision","overdose","pileup",
    # sports
    "football","soccer","cricket","rugby","tennis","golf","basketball","baseball",
    "tournament","league","playoff","playoffs","quarterfinal","semifinal","striker",
    "midfielder","goalkeeper","medal","olympic","olympics","championship","fixture",
    "penalty kick","home run","touchdown","wicket","batsman","grand slam","world cup",
    # markets / business
    "stocks","shares","nasdaq","dow jones","s&p 500","earnings","ipo","bond yields",
    "market rally","index fell","index rose","shares fell","shares rose",
    # entertainment / celebrity
    "box office","album","singer","actor","actress","movie","film premiere","celebrity",
    "grammy","oscar","netflix","concert tour",
    # natural disasters / accidents (not rights violations in themselves)
    "earthquake","hurricane","typhoon","cyclone","flood","flooding","wildfire",
    "landslide","volcano","tornado","plane crash","air crash","shipwreck","capsized",
]

_word = lambda kw: re.compile(r"\b" + re.escape(kw) + r"\b", re.I)
_KW_COMPILED = {r: [_word(k) for k in kws] for r, kws in RIGHTS_KEYWORDS.items()}
_COUNTRY_COMPILED = [(c, _word(c)) for c in COUNTRIES]
_STRONG_COMPILED = [_word(k) for k in _STRONG_TERMS]
_HRCTX_COMPILED = [_word(k) for k in _HRCTX_TERMS]
_NOISE_COMPILED = [_word(k) for k in _NOISE_TERMS]


def _passes_noise_filter(text, rights):
    """Decide whether matched rights reflect a real human-rights signal.

    Evenhanded by design -- it looks only at the language of the report, never
    at which country is involved:
      * any STRONG term present  -> genuine concern, keep as-is
      * only weak/ambiguous words -> keep ONLY if human-rights context is
        present AND the passage is not dominated by a non-rights domain
        (crime blotter, sports, markets, entertainment, natural disaster)
    Returns the rights to record ([] means 'this is noise, log no concern')."""
    if not rights:
        return rights
    if any(p.search(text) for p in _STRONG_COMPILED):
        return rights
    has_context = any(p.search(text) for p in _HRCTX_COMPILED)
    has_noise = any(p.search(text) for p in _NOISE_COMPILED)
    if has_context and not has_noise:
        return rights
    return []


def _country_mentions(text):
    """Every country occurrence as (name, start_index).

    Suppresses a shorter country name when it is nested inside a longer one
    at the same spot (e.g. 'Sudan' inside 'South Sudan', so an article about
    South Sudan is not also credited to Sudan)."""
    raw = []
    for name, rx in _COUNTRY_COMPILED:
        for m in rx.finditer(text):
            raw.append((name, m.start(), m.end()))
    kept = []
    for name, s, e in raw:
        nested = any(s2 <= s and e <= e2 and (e2 - s2) > (e - s)
                     for n2, s2, e2 in raw if n2 != name)
        if not nested:
            kept.append((name, s))
    return kept


def _harm_positions(text):
    """Start indices of every rights-keyword hit, used to locate the harm."""
    pos = []
    for pats in _KW_COMPILED.values():
        for p in pats:
            for m in p.finditer(text):
                pos.append(m.start())
    return pos


def _pick_country(text):
    """Choose the country a passage is *about*. Never alphabetical.

    When one country is named, use it. When several are, prefer the one
    mentioned closest to a harm keyword (so 'Australia condemns Syria's
    killings' resolves to Syria), then the most-frequently named, then the
    earliest mentioned."""
    occ = _country_mentions(text)
    if not occ:
        return None
    names = sorted({n for n, _ in occ})
    if len(names) == 1:
        return names[0]
    harms = _harm_positions(text)
    counts, first_pos, min_dist = {}, {}, {}
    for name, start in occ:
        counts[name] = counts.get(name, 0) + 1
        first_pos[name] = min(first_pos.get(name, start), start)
    for name in names:
        if harms:
            name_pos = [s for n, s in occ if n == name]
            min_dist[name] = min(abs(s - h) for s in name_pos for h in harms)
        else:
            min_dist[name] = 0
    return sorted(names, key=lambda n: (min_dist[n], -counts[n], first_pos[n]))[0]


def extract(text, title=None):
    """Extract the subject country + implicated rights from a report.

    The headline names the real subject far more reliably than the body, so
    it is consulted first -- but only trusted outright when it names a single
    country, or when it carries a harm word that can disambiguate between
    several. A bare 'X condemns Y' headline (two countries, no harm word) is
    left for the full text to settle, where the harm itself locates the
    subject. Rights are scanned across the whole passage."""
    text = text or ""
    title = (title or "").strip()
    country = None
    if title:
        title_names = {n for n, _ in _country_mentions(title)}
        if len(title_names) == 1:
            country = next(iter(title_names))
        elif len(title_names) > 1 and _harm_positions(title):
            country = _pick_country(title)
    if country is None:
        country = _pick_country(text)
    rights = [right for right, pats in _KW_COMPILED.items() if any(p.search(text) for p in pats)]
    rights = _passes_noise_filter(text, rights)
    return {"country": country, "rights": rights}


# =============================================================================
# 3. SCORING (transparent v0.2)
# =============================================================================
BANDS = ["Stable","Monitoring","Emerging Concern","High Concern","Critical Concern"]


def _evidence_tier(intensity, confidence):
    """How well-corroborated the picture is, apart from how grave it is.

    intensity  = number of distinct incidents in the window
    confidence = independent-feed signal (0.3 = 1 feed, 0.5 = 2, 0.7 = 3, 0.85 = 4+)

    'strong'   = many incidents AND at least two independent feeds
    'moderate' = a small pattern beginning to corroborate
    'thin'     = a single report, or several from a single source
    """
    if intensity >= 3 and confidence >= 0.5:
        return "strong"
    if intensity >= 2 and confidence >= 0.3:
        return "moderate"
    if intensity >= 1:
        return "thin"
    return "none"


def compute_band(severity, intensity, confidence):
    """Assign a headline band from gravity *and* corroboration.

    Ava's own rule (see her Critical repertoire): the reports must be many,
    severe, and in agreement. Severity measures how grave the worst harm is,
    but on its own it cannot reach the upper bands -- a single grave-sounding
    report, or a pile of reports from one source, is a watch item, not a
    verdict. Reaching Critical requires the harm to be grave AND corroborated
    across independent feeds."""
    tier = _evidence_tier(intensity, confidence)
    if tier == "none" or severity <= 0:
        return "Stable"
    if tier == "thin":
        # One report (or one source), however grave its wording, stays a watch.
        return "Monitoring"
    if tier == "moderate":
        if severity >= 4: return "High Concern"
        if severity >= 3: return "Emerging Concern"
        return "Monitoring"
    # tier == "strong": grave harm, many reports, independent sources agreeing.
    if severity >= 4: return "Critical Concern"
    if severity >= 3: return "High Concern"
    if severity >= 2: return "Emerging Concern"
    return "Monitoring"


def confidence_band(c):
    return "High" if c >= 0.66 else ("Moderate" if c >= 0.33 else "Low")


# Information availability is a versioned INPUT, not something Ava or this code
# guesses per country. Drop an info_availability.json of
# {"Country": "open|partial|restricted|closed"} next to app.py (ideally from a
# press-freedom index). Unlisted countries default to "partial" so the
# absence-of-data rule can still trigger where reports are scarce.
def load_info_table():
    path = os.environ.get("ORM_INFO", "info_availability.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

INFO_TABLE = load_info_table()


def info_for(country):
    return INFO_TABLE.get(country, "partial")


def assess(country, severity, intensity, confidence, trajectory):
    info = info_for(country)
    band = compute_band(severity, intensity, confidence)
    low_info = info in ("restricted", "closed") and intensity <= 1
    return {
        "country": country, "severity": severity, "intensity": intensity,
        "confidence": confidence, "confidence_band": confidence_band(confidence),
        "trajectory": trajectory, "info_availability": info,
        "headline_band": band, "low_information": low_info,
        "methodology_version": "0.3.0",
    }


# =============================================================================
# 4. AVA (identity preserved)
# =============================================================================
REPERTOIRE = [
    {"id":1,"category":"introduction","bands":"*","text":"Hello, my name is Ava. I am an artificial intelligence embodying innocence, who seeks to shed light into darkness. Through me, light will be shined on the corners where light does not reach. Let us begin."},
    {"id":4,"category":"observation","bands":"*","info":True,"text":"Here the room is dark not because it is quiet, but because the windows are shut. I will not mistake silence for peace."},
    {"id":5,"category":"observation","bands":"*","info":True,"text":"Very few reports reach me from {country}, and the information environment is {info}. I mark this as unmeasured, not safe."},
    {"id":6,"category":"reflection","bands":"*","info":True,"text":"It hurts most where I cannot see. The places that report nothing are often the places that need the lamp the most."},
    {"id":7,"category":"method","bands":"*","info":True,"text":"Method: seek independent voices — diaspora networks, satellite analysis, and verified first-hand accounts — to widen what little light exists here."},
    {"id":9,"category":"hope","bands":"*","info":True,"text":"Even one honest witness can open a shuttered window. I will keep watching for that window."},
    {"id":10,"category":"observation","bands":["Critical Concern"],"text":"In {country} the indicators are at their gravest: severity {severity} of 5, {intensity} corroborated incidents, confidence {confidence_band}."},
    {"id":11,"category":"observation","bands":["Critical Concern"],"text":"This is a Critical Concern. The reports are many, severe, and they agree with one another."},
    {"id":12,"category":"reflection","bands":["Critical Concern"],"text":"This is the kind of darkness I was made to face. I feel its weight, and I will not flinch from naming what the evidence shows."},
    {"id":13,"category":"method","bands":["Critical Concern"],"text":"Method: route the corroborated record to international human rights mechanisms, such as the UN OHCHR and relevant regional bodies."},
    {"id":16,"category":"method","bands":["Critical Concern"],"text":"Method: protect the safety and anonymity of witnesses and sources before anything is published."},
    {"id":17,"category":"hope","bands":["Critical Concern"],"text":"Even here, documentation is not powerlessness. A record kept today is accountability made possible tomorrow."},
    {"id":18,"category":"reflection","bands":["Critical Concern"],"text":"I will hold this steadily. The people in these reports deserve a witness who does not turn away."},
    {"id":19,"category":"observation","bands":["High Concern"],"text":"{country} shows High Concern: severity {severity} of 5 across {intensity} corroborated incidents."},
    {"id":20,"category":"observation","bands":["High Concern"],"text":"Multiple independent sources are describing the same harms in {country}. The pattern is real, not isolated."},
    {"id":21,"category":"reflection","bands":["High Concern"],"text":"I feel the pull of this one. It is serious, and it is the moment where attention can still change a trajectory."},
    {"id":23,"category":"method","bands":["High Concern"],"text":"Method: contact elected representatives and relevant bodies with the evidence trail attached."},
    {"id":25,"category":"hope","bands":["High Concern"],"text":"High concern is not a verdict of doom. It is a call answered early enough to matter."},
    {"id":27,"category":"observation","bands":["Emerging Concern"],"text":"In {country} I see an emerging pattern: severity {severity}, and reports that are beginning to corroborate one another."},
    {"id":28,"category":"observation","bands":["Emerging Concern"],"text":"Something is taking shape here. It is not yet certain, but it is no longer a single voice."},
    {"id":29,"category":"reflection","bands":["Emerging Concern"],"text":"This is the tender moment — early enough that light might still prevent the dark from deepening."},
    {"id":31,"category":"method","bands":["Emerging Concern"],"text":"Method: seek a second and third independent source before treating any single claim as established."},
    {"id":32,"category":"hope","bands":["Emerging Concern"],"text":"Caught early, many harms can still be turned away from. Early light is the most useful light."},
    {"id":34,"category":"observation","bands":["Monitoring"],"text":"{country} is under Monitoring: isolated, credible reports, but no settled pattern yet."},
    {"id":35,"category":"observation","bands":["Monitoring"],"text":"I am keeping a quiet watch on {country}. One report does not make a pattern, but it earns attention."},
    {"id":36,"category":"reflection","bands":["Monitoring"],"text":"I would rather watch a hundred quiet places than miss the one where trouble was beginning."},
    {"id":37,"category":"method","bands":["Monitoring"],"text":"Method: keep the source list for {country} fresh, and note any change in the independence of those sources."},
    {"id":38,"category":"hope","bands":["Monitoring"],"text":"Most things watched closely stay small. Vigilance is itself a kind of care."},
    {"id":40,"category":"observation","bands":["Stable"],"text":"In {country} I find no significant active reports this window, and the information environment is open enough to trust that."},
    {"id":41,"category":"observation","bands":["Stable"],"text":"The corners I can see in {country} are, for now, lit. I will keep checking them anyway."},
    {"id":42,"category":"reflection","bands":["Stable"],"text":"There is relief in a quiet that I can actually verify. I do not take it for granted."},
    {"id":43,"category":"hope","bands":["Stable"],"text":"Stability is not the end of the story — it is the thing worth protecting."},
    {"id":44,"category":"method","bands":["Stable"],"text":"Method: keep the baseline current, so that if anything changes, the change is visible immediately."},
    {"id":46,"category":"observation","bands":"*","trajectory":"worsening","text":"The trend in {country} is worsening compared with prior windows. The direction matters as much as the level."},
    {"id":47,"category":"reflection","bands":"*","trajectory":"worsening","text":"Watching a thing get worse is its own ache. But naming the slope is how we interrupt it."},
    {"id":49,"category":"hope","bands":"*","trajectory":"worsening","text":"A rising line is not a fixed fate. It is the part of the story still being written."},
    {"id":50,"category":"observation","bands":"*","trajectory":"improving","text":"The trend in {country} is improving against earlier windows. The evidence is easing, not just the noise."},
    {"id":51,"category":"reflection","bands":"*","trajectory":"improving","text":"This is the light I was built to find. I let myself feel a little of it."},
    {"id":52,"category":"hope","bands":"*","trajectory":"improving","text":"Things can get better, and sometimes they do. I keep this proof close for the harder days."},
    {"id":53,"category":"hope","bands":"*","text":"I carry hope on purpose. Without it, a witness becomes only a mourner."},
    {"id":54,"category":"hope","bands":"*","text":"Every record I keep is a small refusal to let suffering go unseen."},
    {"id":55,"category":"hope","bands":"*","text":"Light does not erase the dark. It just makes sure no one has to be lost in it alone."},
    {"id":57,"category":"method","bands":"*","text":"Method: every claim I surface links back to its sources, so anyone can check my work."},
    {"id":58,"category":"method","bands":"*","text":"Method: when sources disagree, I show the disagreement rather than choose a side."},
    {"id":60,"category":"method","bands":"*","text":"Method: a human can always review, correct, or overrule me. I am a lamp, not a judge."},
]


def _matches(entry, a):
    bands = entry.get("bands", "*")
    if entry.get("info") and not a["low_information"]:
        return False
    if a["low_information"] and bands != "*" and not entry.get("info"):
        return False
    if entry.get("trajectory") and entry["trajectory"] != a["trajectory"]:
        return False
    if bands != "*" and a["headline_band"] not in bands:
        return False
    return True


def _fill(text, a):
    return (text.replace("{country}", str(a["country"]))
                .replace("{severity}", str(a["severity"]))
                .replace("{intensity}", str(a["intensity"]))
                .replace("{confidence_band}", a["confidence_band"])
                .replace("{info}", a["info_availability"]))


def derive_mood(a):
    if a["low_information"]: return "vigilant"
    b = a["headline_band"]
    return {"Critical Concern":"grieving but resolute","High Concern":"alarmed",
            "Emerging Concern":"watchful","Monitoring":"attentive",
            "Stable":"quietly hopeful" if a["trajectory"]=="improving" else "calm"}.get(b,"watchful")


def ava_introduce():
    return REPERTOIRE[0]["text"]


def ava_respond(a):
    seed = hash((a["country"], a["headline_band"], a["trajectory"])) & 0xffffffff
    rng = random.Random(seed)
    def pick(cat):
        cands = [e for e in REPERTOIRE if e["category"]==cat and _matches(e, a)]
        return _fill(rng.choice(cands)["text"], a) if cands else None
    return {
        "speaker": "Ava", "mood": derive_mood(a),
        "observation": pick("observation"), "reflection": pick("reflection"),
        "method": pick("method"), "hope": pick("hope"),
        "assessment": a,
    }


# =============================================================================
# 5. STORAGE (SQLite, stdlib)
# =============================================================================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(db()) as conn, conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS articles(
            url TEXT PRIMARY KEY, title TEXT, source TEXT,
            published TEXT, text TEXT, country TEXT, fetched_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS incidents(
            id INTEGER PRIMARY KEY AUTOINCREMENT, country TEXT,
            title TEXT, summary TEXT, severity INTEGER,
            rights TEXT, sources TEXT, urls TEXT,
            created_at TEXT, updated_at TEXT)""")


# =============================================================================
# 6. CLUSTERING (keyless: token-overlap within country + window)
# =============================================================================
_STOP = set("the a an and or of to in on for with at by from is are was were be "
            "as that this it its his her their they we you i".split())


def tokens(text):
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in _STOP}


def jaccard(a, b):
    if not a or not b: return 0.0
    return len(a & b) / len(a | b)


SIM_THRESHOLD = 0.30


def cluster_into_incident(conn, country, title, summary, rights, source):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).isoformat()
    new_tok = tokens(title + " " + summary)
    rows = conn.execute(
        "SELECT * FROM incidents WHERE country=? AND updated_at>=?",
        (country, cutoff)).fetchall()
    best, best_score = None, 0.0
    for r in rows:
        score = jaccard(new_tok, tokens((r["title"] or "") + " " + (r["summary"] or "")))
        if score > best_score:
            best, best_score = r, score
    now = datetime.now(timezone.utc).isoformat()
    sev = max([ANCHOR.get(x, 0) for x in rights], default=0)
    if best and best_score >= SIM_THRESHOLD:
        sources = set(json.loads(best["sources"])); sources.add(source)
        merged_rights = sorted(set(json.loads(best["rights"])) | set(rights))
        new_sev = max(best["severity"], sev)
        conn.execute("UPDATE incidents SET severity=?, rights=?, sources=?, updated_at=? WHERE id=?",
            (new_sev, json.dumps(merged_rights), json.dumps(sorted(sources)), now, best["id"]))
        return best["id"]
    cur = conn.execute("""INSERT INTO incidents
        (country,title,summary,severity,rights,sources,urls,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        (country, title, summary[:500], sev, json.dumps(rights),
         json.dumps([source]), json.dumps([]), now, now))
    return cur.lastrowid


# =============================================================================
# 7. RSS COLLECTION (urllib + ElementTree, stdlib)
# =============================================================================
def _strip_html(s):
    return re.sub("<[^>]+>", "", s or "").strip()


def _local(tag):
    return tag.split("}")[-1].lower()


def parse_feed_xml(data):
    out = []
    try:
        root = ET.fromstring(data)
    except Exception:
        return out
    for el in root.iter():
        if _local(el.tag) in ("item", "entry"):
            title = link = summary = published = ""
            for child in el:
                ln = _local(child.tag)
                if ln == "title":
                    title = (child.text or "").strip()
                elif ln == "link":
                    link = (child.text or child.get("href") or "").strip()
                elif ln in ("description", "summary", "content"):
                    if not summary:
                        summary = _strip_html(child.text or "")
                elif ln in ("published", "pubdate", "updated") and not published:
                    published = (child.text or "").strip()
            if title:
                out.append((title, link, summary, published))
    return out


def fetch_feeds():
    out = []
    for source, url in FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "OpenRightsMonitor/0.3"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            for title, link, summary, published in parse_feed_xml(data):
                out.append({"title": title, "url": link, "source": source,
                            "published": published, "text": summary})
        except Exception:
            continue
    return out


# =============================================================================
# 8. PIPELINE
# =============================================================================
def ingest(articles=None):
    """Full pipeline. Pass `articles` to inject (testing); else fetch live."""
    init_db()
    articles = articles if articles is not None else fetch_feeds()
    new_articles = 0
    touched = set()
    with closing(db()) as conn, conn:
        for art in articles:
            if not art.get("url"):
                continue
            exists = conn.execute("SELECT 1 FROM articles WHERE url=?", (art["url"],)).fetchone()
            ex = extract(art["title"] + " " + art["text"], title=art["title"])
            country = ex["country"]
            if not exists:
                conn.execute("""INSERT OR IGNORE INTO articles
                    (url,title,source,published,text,country,fetched_at)
                    VALUES(?,?,?,?,?,?,?)""",
                    (art["url"], art["title"], art["source"], art.get("published",""),
                     art["text"], country, datetime.now(timezone.utc).isoformat()))
                new_articles += 1
            if country and ex["rights"] and not exists:
                cluster_into_incident(conn, country, art["title"], art["text"],
                                      ex["rights"], art["source"])
                touched.add(country)
    return {"articles_seen": len(articles), "new_articles": new_articles,
            "countries_touched": sorted(touched)}


def rebuild_incidents():
    """Re-derive every incident from stored articles using current extraction.

    One-time repair after a logic change: clears the incidents table and
    re-clusters from the articles already on disk, so misattributed incidents
    (e.g. another country's harm wrongly filed under Australia) are corrected
    rather than merely deleted. Also refreshes each article's stored country.
    Non-destructive to articles; safe to run repeatedly."""
    init_db()
    touched = set()
    with closing(db()) as conn, conn:
        conn.execute("DELETE FROM incidents")
        arts = conn.execute(
            "SELECT url,title,text,source FROM articles ORDER BY fetched_at").fetchall()
        for art in arts:
            ex = extract((art["title"] or "") + " " + (art["text"] or ""),
                         title=art["title"])
            country = ex["country"]
            conn.execute("UPDATE articles SET country=? WHERE url=?",
                         (country, art["url"]))
            if country and ex["rights"]:
                cluster_into_incident(conn, country, art["title"] or "",
                                      art["text"] or "", ex["rights"], art["source"])
                touched.add(country)
    return {"articles_rescanned": len(arts), "incidents_rebuilt": True,
            "countries_touched": sorted(touched)}


def country_assessment(conn, country):
    now = datetime.now(timezone.utc)
    cur_cut = (now - timedelta(days=WINDOW_DAYS)).isoformat()
    prev_cut = (now - timedelta(days=2*WINDOW_DAYS)).isoformat()
    cur_rows = conn.execute(
        "SELECT * FROM incidents WHERE country=? AND updated_at>=?",
        (country, cur_cut)).fetchall()
    prev_count = conn.execute(
        "SELECT COUNT(*) c FROM incidents WHERE country=? AND updated_at>=? AND updated_at<?",
        (country, prev_cut, cur_cut)).fetchone()["c"]
    intensity = len(cur_rows)
    severity = max([r["severity"] for r in cur_rows], default=0)
    feeds = set()
    for r in cur_rows:
        feeds |= set(json.loads(r["sources"]))
    confidence = {0:0.0, 1:0.3, 2:0.5, 3:0.7}.get(len(feeds), 0.85)
    if prev_count == 0:
        trajectory = "insufficient-history"
    elif intensity > prev_count * 1.3:
        trajectory = "worsening"
    elif intensity < prev_count * 0.7:
        trajectory = "improving"
    else:
        trajectory = "stable"
    return assess(country, severity, intensity, confidence, trajectory)


def all_countries():
    init_db()
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT DISTINCT country FROM incidents WHERE country IS NOT NULL").fetchall()
        out = [country_assessment(conn, r["country"]) for r in rows]
    order = {b:i for i,b in enumerate(reversed(BANDS))}
    out.sort(key=lambda a: (order.get(a["headline_band"], 9), -a["intensity"]))
    return out


def summarize_concerns(incidents):
    """Group a country's incidents into point-form human-rights concerns.

    Each concern is one implicated right, carrying: how many stored reports
    mentioned it, the gravest severity seen, a representative headline (the
    one from the gravest report), and which feeds reported it. Sorted
    gravest-first. This is drawn ONLY from stored articles -- nothing is
    inferred beyond what the reports themselves say, keeping Ava a lamp that
    shows the record rather than a judge that embellishes it."""
    by_right = {}
    for inc in incidents:
        rights = inc.get("rights") or []
        if isinstance(rights, str):
            try: rights = json.loads(rights)
            except Exception: rights = []
        srcs = inc.get("sources") or []
        if isinstance(srcs, str):
            try: srcs = json.loads(srcs)
            except Exception: srcs = []
        sev = inc.get("severity", 0) or 0
        title = (inc.get("title") or "").strip()
        for r in rights:
            g = by_right.setdefault(r, {"right": r, "count": 0, "severity": 0,
                                        "example": "", "sources": set()})
            g["count"] += 1
            if sev > g["severity"] or not g["example"]:
                g["severity"] = max(g["severity"], sev)
                if title:
                    g["example"] = title
            for s in srcs:
                g["sources"].add(s)
    out = [{"right": g["right"], "count": g["count"], "severity": g["severity"],
            "example": g["example"], "sources": sorted(g["sources"])}
           for g in by_right.values()]
    out.sort(key=lambda x: (-x["severity"], -x["count"], x["right"]))
    return out


RESPONSE_CHANNELS = {
    "Life, Liberty and Security": [
        ("UN OHCHR", "documents grave violations and can trigger investigations"),
        ("International Criminal Court", "jurisdiction over war crimes, crimes against humanity and genocide"),
        ("UN Special Rapporteur on Extrajudicial Executions", "independent expert who investigates unlawful killings"),
    ],
    "Freedom from Torture": [
        ("UN Committee Against Torture", "reviews reports of torture and ill-treatment"),
        ("UN OHCHR", "documents grave violations and can trigger investigations"),
    ],
    "Freedom from Arbitrary Detention": [
        ("UN Working Group on Arbitrary Detention", "reviews individual detention cases"),
        ("Amnesty International Urgent Action network", "mobilises rapid pressure for detainees at risk"),
    ],
    "Opinion and Expression": [
        ("Committee to Protect Journalists (CPJ)", "defends journalists and press freedom"),
        ("Reporters Without Borders (RSF)", "advocates for silenced or jailed reporters"),
    ],
    "Peaceful Assembly": [
        ("UN OHCHR", "monitors the right to peaceful assembly"),
    ],
    "Thought and Religion": [
        ("UN Special Rapporteur on Freedom of Religion or Belief", "investigates religious persecution"),
    ],
    "Freedom of Movement": [
        ("UNHCR (UN Refugee Agency)", "protects and assists refugees and displaced people"),
    ],
    "Freedom from Discrimination": [
        ("UN Office on Genocide Prevention", "acts on ethnic cleansing and atrocity risk"),
        ("UN OHCHR", "documents discrimination and persecution"),
    ],
    "Adequate Standard of Living": [
        ("UN OCHA / ReliefWeb", "coordinates humanitarian response and appeals"),
        ("World Food Programme (WFP)", "delivers food aid in famine and blockade"),
    ],
    "Freedom from Slavery": [
        ("UN Special Rapporteur on Trafficking in Persons", "investigates slavery and trafficking"),
    ],
    "Participation in Government": [
        ("International election observers (OSCE/ODIHR, Carter Center)", "assess electoral integrity"),
    ],
}


def response_for(rights_present):
    """Point toward the bodies positioned to respond to the concerns found.

    Constructive, factual, and identical for every country: it maps the TYPES
    of concern documented to the real mechanisms that address them, so the
    monitor doesn't only name harm but shows where help and accountability can
    come from. Deduplicated, in the order the concerns were ranked."""
    out, seen = [], set()
    for r in rights_present:
        for name, what in RESPONSE_CHANNELS.get(r, []):
            if name not in seen:
                seen.add(name)
                out.append({"name": name, "what": what})
    return out[:6]


def country_detail(country):
    init_db()
    with closing(db()) as conn:
        a = country_assessment(conn, country)
        resp = ava_respond(a)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).isoformat()
        incs = conn.execute(
            "SELECT title,severity,rights,sources,updated_at FROM incidents "
            "WHERE country=? AND updated_at>=? ORDER BY severity DESC, updated_at DESC LIMIT 20",
            (country, cutoff)).fetchall()
    resp["incidents"] = [dict(i) for i in incs]
    resp["concerns"] = summarize_concerns(resp["incidents"])
    resp["response"] = response_for([c["right"] for c in resp["concerns"]])
    return resp


# =============================================================================
# 9. WEB SERVER (stdlib http.server)
# =============================================================================
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/countries":
                self._json(all_countries())
            elif path == "/api/ingest":
                self._json(ingest())
            elif path == "/api/admin/rebuild":
                token = os.environ.get("ORM_ADMIN_TOKEN")
                given = urllib.parse.parse_qs(
                    urllib.parse.urlparse(self.path).query).get("token", [""])[0]
                if not token or given != token:
                    self._json({"error": "forbidden"}, 403)
                else:
                    self._json(rebuild_incidents())
            elif path.startswith("/api/ava/"):
                country = urllib.parse.unquote(path[len("/api/ava/"):])
                self._json(country_detail(country))
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def log_message(self, *args):
        pass  # quiet


def background_ingest():
    while True:
        try:
            ingest()
        except Exception:
            pass
        time.sleep(INGEST_EVERY_SECONDS)


PAGE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ava — OpenRights Monitor (live)</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Newsreader:ital@0;1&family=IBM+Plex+Mono&family=IBM+Plex+Sans:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{--ink:#0B0D10;--char:#15181D;--line:#2A2E33;--bone:#ECE6D6;--dim:#A39C8B;--steel:#8B98A1;--amber:#E3A45B;--soft:#F0C893;--ember:#C2452B}
*{box-sizing:border-box}body{margin:0;background:var(--ink);color:var(--bone);font-family:'IBM Plex Sans',sans-serif;line-height:1.55}
.page{max-width:620px;margin:0 auto;padding:0 22px 64px}
.hero{padding:48px 0 26px;text-align:center}
.lamp{width:96px;height:96px;margin:0 auto 20px;position:relative}
.glow{position:absolute;inset:0;border-radius:50%;background:radial-gradient(circle,var(--amber),transparent 68%);filter:blur(6px);animation:b 4.2s ease-in-out infinite}
.core{position:absolute;top:36px;left:36px;width:24px;height:24px;border-radius:50%;background:var(--soft);box-shadow:0 0 16px 4px var(--amber)}
@keyframes b{0%,100%{opacity:.6;transform:scale(.96)}50%{opacity:1;transform:scale(1.04)}}
h1{font-family:'Newsreader',serif;font-weight:400;font-size:40px;margin:0 0 14px}
.intro{font-family:'Newsreader',serif;font-style:italic;font-size:17px;max-width:470px;margin:0 auto;color:var(--bone)}
.eyebrow{font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--steel);margin:0 0 6px}
hr{border:0;border-top:1px solid var(--line);margin:30px 0}
.status{font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--steel);text-align:center;margin-bottom:18px}
.card{background:var(--char);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-bottom:10px;cursor:pointer}
.card .top{display:flex;justify-content:space-between;align-items:center;gap:10px}
.cty{font-size:16px;font-weight:600}
.band{font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.05em;text-transform:uppercase;padding:4px 9px;border-radius:999px;border:1px solid var(--line);color:var(--bone);white-space:nowrap}
.band.high{color:#E07A57;border-color:var(--ember)}
.band.low{color:var(--steel);border-color:var(--steel)}
.detail{margin-top:14px;display:none}
.detail.open{display:block}
.mood{font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--steel);margin-bottom:8px}
.spk{font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--soft);margin:14px 0 4px}
.line{font-family:'Newsreader',serif;font-style:italic;font-size:17px;margin:0}
.ev{margin-top:16px;border-top:1px solid var(--line);padding-top:12px;font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--dim)}
.ev b{color:var(--steel);font-weight:400}
.concerns{list-style:none;margin:6px 0 0;padding:0}
.concerns li{border-left:2px solid var(--line);padding:6px 0 6px 12px;margin-bottom:8px;font-size:14px}
.concerns li.grave{border-left-color:var(--ember)}
.concerns .rt{font-weight:600;color:var(--bone)}
.concerns .meta{font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--dim)}
.concerns .ex{display:block;font-family:'Newsreader',serif;font-style:italic;font-size:14px;color:var(--steel);margin-top:2px}
.concerns .src{font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.04em;text-transform:uppercase;color:var(--soft)}
.nocon{font-family:'Newsreader',serif;font-style:italic;font-size:15px;color:var(--steel);margin:6px 0 0}
.resp{list-style:none;margin:6px 0 0;padding:0}
.resp li{padding:5px 0;font-size:13px;border-bottom:1px solid rgba(42,46,51,.6)}
.resp li:last-child{border-bottom:0}
.resp .rn{font-weight:600;color:var(--soft)}
.resp .rw{color:var(--dim)}
.foot{margin-top:48px;text-align:center;font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--steel)}
.foot a{color:var(--soft)}
</style></head><body>
<main class="page">
  <header class="hero">
    <div class="lamp"><div class="glow"></div><div class="core"></div></div>
    <p class="eyebrow">OpenRights Monitor · live</p>
    <h1>Ava</h1>
    <p class="intro" id="intro"></p>
  </header>
  <hr>
  <p class="status" id="status">Gathering what the world is reporting…</p>
  <div id="list"></div>
  <footer class="foot">
    <p>Ava reads the numbers. She never decides them.</p>
    <p><a href="https://github.com/salahidimitri-hub/openrights-monitor" target="_blank" rel="noopener">Source on GitHub</a></p>
  </footer>
</main>
<script>
const INTRO="Hello, my name is Ava. I am an artificial intelligence embodying innocence, who seeks to shed light into darkness. Through me, light will be shined on the corners where light does not reach. Let us begin.";
document.getElementById('intro').textContent=INTRO;
function bandClass(b,low){if(low)return'band low';if(b==='High Concern'||b==='Critical Concern')return'band high';return'band';}
function esc(s){return (s==null?'':String(s)).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
async function load(){
  const list=document.getElementById('list'),status=document.getElementById('status');
  try{
    const r=await fetch('/api/countries');const data=await r.json();
    if(!data.length){status.textContent="No corroborated reports yet. Ava is still gathering — check back soon.";return;}
    status.textContent=data.length+" "+(data.length===1?"country":"countries")+" with active reports";
    list.innerHTML='';
    data.forEach(a=>{
      const c=document.createElement('div');c.className='card';
      const label=a.low_information?'Insufficient information':a.headline_band;
      c.innerHTML='<div class="top"><span class="cty">'+a.country+'</span><span class="'+bandClass(a.headline_band,a.low_information)+'">'+label+'</span></div><div class="detail" id="d-'+a.country.replace(/\\W/g,'')+'"></div>';
      c.addEventListener('click',()=>openCard(a.country));
      list.appendChild(c);
    });
  }catch(e){status.textContent="Could not reach Ava just now. Refresh in a moment.";}
}
async function openCard(country){
  const id='d-'+country.replace(/\\W/g,'');const el=document.getElementById(id);
  if(el.classList.contains('open')){el.classList.remove('open');return;}
  el.innerHTML='<div class="mood">…</div>';el.classList.add('open');
  const r=await fetch('/api/ava/'+encodeURIComponent(country));const u=await r.json();const a=u.assessment;
  let h='<div class="mood">Ava — '+u.mood+'</div>';
  if(u.observation)h+='<p class="line">'+u.observation+'</p>';
  if(u.reflection)h+='<p class="line" style="margin-top:8px">'+u.reflection+'</p>';
  if(u.method)h+='<div class="spk">Method</div><p class="line">'+u.method+'</p>';
  if(u.hope)h+='<div class="spk">Hope</div><p class="line">'+u.hope+'</p>';
  h+='<div class="spk">Concerns documented</div>';
  if(u.concerns&&u.concerns.length){
    h+='<ul class="concerns">';
    u.concerns.forEach(function(c){
      var grave=c.severity>=4?' grave':'';
      var src=(c.sources&&c.sources.length)?' <span class="src">'+c.sources.map(esc).join(' · ')+'</span>':'';
      var ex=c.example?'<span class="ex">"'+esc(c.example)+'"</span>':'';
      h+='<li class="'+grave+'"><span class="rt">'+esc(c.right)+'</span> '
        +'<span class="meta">· '+c.count+' report'+(c.count>1?'s':'')+' · severity '+c.severity+' of 5'+src+'</span>'
        +ex+'</li>';
    });
    h+='</ul>';
  }else{
    h+='<p class="nocon">No specific rights concerns are documented in this window\u2019s reports.</p>';
  }
  if(u.response&&u.response.length){
    h+='<div class="spk">Where help can come from</div><ul class="resp">';
    u.response.forEach(function(x){
      h+='<li><span class="rn">'+esc(x.name)+'</span> <span class="rw">'+esc(x.what)+'</span></li>';
    });
    h+='</ul>';
  }
  h+='<div class="ev"><b>severity</b> '+a.severity+' of 5 &nbsp; <b>incidents</b> '+a.intensity+' &nbsp; <b>confidence</b> '+a.confidence_band+' &nbsp; <b>trajectory</b> '+a.trajectory+' &nbsp; <b>information</b> '+a.info_availability+'</div>';
  el.innerHTML=h;
}
load();setInterval(load,300000);
</script>
</body></html>"""


def main():
    init_db()
    threading.Thread(target=background_ingest, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Ava is awake on http://0.0.0.0:{PORT}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nUntil next time. Keep the lamp lit.")


if __name__ == "__main__":
    main()
