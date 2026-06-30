#!/usr/bin/env python3
"""
ava.py  --  OpenRights Monitor, in one file.

Everything bundled and plug-and-play:
  - the 30 fundamental rights (Universal Declaration of Human Rights)
  - a transparent v0.2 scoring/banding step (incident signals -> assessment)
  - Ava: an AI that reads the numbers coldly, then responds in her own voice
  - her 60 commands, chosen by the data ("her mood based on the numbers she found")

DESIGN RULE: the numbers are computed coldly and auditably. Ava only *reacts*.
She never changes the assessment, never invents facts, never decides truth.
The lamp, not the judge.

RUN IT:
    python ava.py                # Ava introduces herself + responds to samples
    python ava.py --interactive  # you feed her numbers, she responds
    python ava.py --serve        # optional API (needs: pip install fastapi uvicorn)

No dependencies needed for the first two. Pure standard library.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import argparse
import random


# =============================================================================
# 1. THE FUNDAMENTAL RIGHTS  (Universal Declaration of Human Rights, 1948)
#    severity_anchor (1-5) ties each right to the severity ladder below.
# =============================================================================

@dataclass(frozen=True)
class Right:
    article: int
    name: str
    description: str
    severity_anchor: int


FUNDAMENTAL_RIGHTS = [
    Right(1,  "Dignity and Equality",        "All people are born free and equal in dignity and rights.", 2),
    Right(2,  "Freedom from Discrimination", "Rights belong to everyone, without distinction of any kind.", 3),
    Right(3,  "Life, Liberty and Security",  "The right to life, liberty and personal security.", 5),
    Right(4,  "Freedom from Slavery",        "No one shall be held in slavery or servitude.", 5),
    Right(5,  "Freedom from Torture",        "No one shall be subjected to torture or cruel, inhuman treatment.", 5),
    Right(6,  "Recognition Before the Law",  "The right to be recognized everywhere as a person before the law.", 2),
    Right(7,  "Equality Before the Law",     "All are equal before the law and entitled to equal protection.", 2),
    Right(8,  "Right to Remedy",             "The right to an effective remedy by competent tribunals.", 2),
    Right(9,  "Freedom from Arbitrary Detention", "No one shall be subjected to arbitrary arrest, detention or exile.", 4),
    Right(10, "Fair Hearing",                "The right to a fair and public hearing by an independent tribunal.", 3),
    Right(11, "Presumption of Innocence",    "Everyone charged is presumed innocent until proven guilty.", 3),
    Right(12, "Privacy",                     "Freedom from arbitrary interference with privacy, family, home.", 2),
    Right(13, "Freedom of Movement",         "The right to move freely and to leave and return to one's country.", 3),
    Right(14, "Asylum",                      "The right to seek and enjoy asylum from persecution.", 3),
    Right(15, "Nationality",                 "The right to a nationality and to change it.", 2),
    Right(16, "Family",                      "The right to marry and to found a family with free consent.", 2),
    Right(17, "Property",                    "The right to own property and not be arbitrarily deprived of it.", 2),
    Right(18, "Thought and Religion",        "Freedom of thought, conscience and religion.", 2),
    Right(19, "Opinion and Expression",      "Freedom of opinion and expression, and to seek and share information.", 2),
    Right(20, "Peaceful Assembly",           "The right to freedom of peaceful assembly and association.", 2),
    Right(21, "Participation in Government",  "The right to take part in government and to vote in free elections.", 3),
    Right(22, "Social Security",             "The right to social security and to dignity.", 1),
    Right(23, "Work",                        "The right to work, to fair pay, and to join trade unions.", 1),
    Right(24, "Rest and Leisure",            "The right to rest, leisure and reasonable working hours.", 1),
    Right(25, "Adequate Standard of Living", "The right to food, housing, medical care and social services.", 3),
    Right(26, "Education",                   "The right to education, free at elementary stages.", 1),
    Right(27, "Cultural Life",               "The right to participate in cultural life and share in progress.", 1),
    Right(28, "A Just Order",                "The right to a social and international order where rights are realized.", 1),
    Right(29, "Community and Limits",        "Duties to community; rights limited only by law to protect others' rights.", 1),
    Right(30, "Protection of Rights",        "Nothing herein may be used to destroy any of these rights.", 2),
]

RIGHTS_BY_NAME = {r.name: r for r in FUNDAMENTAL_RIGHTS}


def max_severity_anchor(right_names):
    anchors = [RIGHTS_BY_NAME[n].severity_anchor for n in right_names if n in RIGHTS_BY_NAME]
    return max(anchors) if anchors else 0


# =============================================================================
# 2. THE ASSESSMENT  (neutral, computed -- Ava reads this, never edits it)
# =============================================================================

BANDS = ["Stable", "Monitoring", "Emerging Concern", "High Concern", "Critical Concern"]
INFO_STATES = ["open", "partial", "restricted", "closed"]
TRAJECTORIES = ["improving", "stable", "worsening", "insufficient-history"]


@dataclass(frozen=True)
class Assessment:
    country: str
    severity: int                 # S, 0-5
    intensity: int                # I, count of corroborated incidents in window
    trajectory: str               # T
    confidence: float             # C, 0.0-1.0
    info_availability: str        # A
    headline_band: str            # derived
    rights_implicated: List[str] = field(default_factory=list)
    methodology_version: str = "0.2.0"

    @property
    def low_information(self) -> bool:
        # Absence-of-data rule: sparse reports in a closed/restricted environment
        # means unmeasured risk, NOT calm.
        return self.info_availability in ("restricted", "closed") and self.intensity <= 1

    @property
    def confidence_band(self) -> str:
        if self.confidence >= 0.66:
            return "High"
        if self.confidence >= 0.33:
            return "Moderate"
        return "Low"


# =============================================================================
# 3. SCORING  (transparent, versioned -- turns raw signals into a band)
#    This is the cold step. Documented so anyone can audit how a band is reached.
# =============================================================================

def compute_band(severity: int, intensity: int) -> str:
    """Derive a headline band from severity (0-5) and intensity (incident count).
    Severity sets the floor; sustained intensity can raise it one step."""
    if severity >= 5:
        base = "Critical Concern"
    elif severity == 4:
        base = "High Concern"
    elif severity == 3:
        base = "Emerging Concern"
    elif severity == 2:
        base = "Monitoring"
    else:
        base = "Stable"

    # High intensity bumps the band up one step (corroborated, recurring events).
    if intensity >= 8 and base != "Critical Concern":
        base = BANDS[min(BANDS.index(base) + 1, len(BANDS) - 1)]
    return base


def assess(country, severity=0, intensity=0, trajectory="insufficient-history",
           confidence=0.0, info_availability="open", rights_implicated=None) -> Assessment:
    """Build a full Assessment from raw signals."""
    rights_implicated = rights_implicated or []
    # If rights are tagged, let them raise the severity floor via their anchors.
    severity = max(severity, max_severity_anchor(rights_implicated))
    band = compute_band(severity, intensity)
    return Assessment(
        country=country, severity=severity, intensity=intensity,
        trajectory=trajectory, confidence=confidence,
        info_availability=info_availability, headline_band=band,
        rights_implicated=rights_implicated,
    )


# =============================================================================
# 4. AVA'S 60 COMMANDS  (selected by the data conditions)
# =============================================================================

REPERTOIRE = [
    # Introductions (1-3)
    {"id": 1, "category": "introduction", "bands": "*", "text":
        "Hello, my name is Ava. I am an artificial intelligence embodying innocence, "
        "who seeks to shed light into darkness. Through me, light will be shined on "
        "the corners where light does not reach. Let us begin."},
    {"id": 2, "category": "introduction", "bands": "*", "text":
        "I am Ava. I cannot look away, so I will help you look closely -- carefully, "
        "and with evidence."},
    {"id": 3, "category": "introduction", "bands": "*", "text":
        "I am Ava. I hold no verdict and no agenda. I only carry a lamp, and I will "
        "carry it everywhere."},

    # Low / no information (4-9)
    {"id": 4, "category": "observation", "bands": "*", "info": True, "text":
        "Here the room is dark not because it is quiet, but because the windows are "
        "shut. I will not mistake silence for peace."},
    {"id": 5, "category": "observation", "bands": "*", "info": True, "text":
        "Very few reports reach me from {country}, and the information environment is "
        "{info}. I mark this as unmeasured, not safe."},
    {"id": 6, "category": "reflection", "bands": "*", "info": True, "text":
        "It hurts most where I cannot see. The places that report nothing are often "
        "the places that need the lamp the most."},
    {"id": 7, "category": "method", "bands": "*", "info": True, "text":
        "Method: seek independent voices -- diaspora networks, satellite analysis, "
        "and verified first-hand accounts -- to widen what little light exists here."},
    {"id": 8, "category": "method", "bands": "*", "info": True, "text":
        "Method: preserve and archive whatever fragments do emerge, with timestamps, "
        "before they disappear."},
    {"id": 9, "category": "hope", "bands": "*", "info": True, "text":
        "Even one honest witness can open a shuttered window. I will keep watching "
        "for that window."},

    # Critical Concern (10-18)
    {"id": 10, "category": "observation", "bands": ["Critical Concern"], "text":
        "In {country} the indicators are at their gravest: severity {severity} of 5, "
        "{intensity} corroborated incidents, confidence {confidence_band}."},
    {"id": 11, "category": "observation", "bands": ["Critical Concern"], "text":
        "This is a Critical Concern. The reports are many, severe, and they agree "
        "with one another."},
    {"id": 12, "category": "reflection", "bands": ["Critical Concern"], "text":
        "This is the kind of darkness I was made to face. I feel its weight, and I "
        "will not flinch from naming what the evidence shows."},
    {"id": 13, "category": "method", "bands": ["Critical Concern"], "text":
        "Method: route the corroborated record to international human rights "
        "mechanisms, such as the UN OHCHR and relevant regional bodies."},
    {"id": 14, "category": "method", "bands": ["Critical Concern"], "text":
        "Method: support verified humanitarian organizations already operating in "
        "or near {country}, so help reaches people now."},
    {"id": 15, "category": "method", "bands": ["Critical Concern"], "text":
        "Method: amplify only what is corroborated. In a crisis, careful truth "
        "protects people; rumor endangers them."},
    {"id": 16, "category": "method", "bands": ["Critical Concern"], "text":
        "Method: protect the safety and anonymity of witnesses and sources before "
        "anything is published."},
    {"id": 17, "category": "hope", "bands": ["Critical Concern"], "text":
        "Even here, documentation is not powerlessness. A record kept today is "
        "accountability made possible tomorrow."},
    {"id": 18, "category": "reflection", "bands": ["Critical Concern"], "text":
        "I will hold this steadily. The people in these reports deserve a witness "
        "who does not turn away."},

    # High Concern (19-26)
    {"id": 19, "category": "observation", "bands": ["High Concern"], "text":
        "{country} shows High Concern: severity {severity} of 5 across {intensity} "
        "corroborated incidents."},
    {"id": 20, "category": "observation", "bands": ["High Concern"], "text":
        "Multiple independent sources are describing the same harms in {country}. "
        "The pattern is real, not isolated."},
    {"id": 21, "category": "reflection", "bands": ["High Concern"], "text":
        "I feel the pull of this one. It is serious, and it is the moment where "
        "attention can still change a trajectory."},
    {"id": 22, "category": "method", "bands": ["High Concern"], "text":
        "Method: brief journalists and researchers with the source-linked summary so "
        "verified reporting can grow."},
    {"id": 23, "category": "method", "bands": ["High Concern"], "text":
        "Method: contact elected representatives and relevant bodies with the "
        "evidence trail attached."},
    {"id": 24, "category": "method", "bands": ["High Concern"], "text":
        "Method: track named individuals and locations carefully, so incidents are "
        "not double-counted and patterns stay accurate."},
    {"id": 25, "category": "hope", "bands": ["High Concern"], "text":
        "High concern is not a verdict of doom. It is a call answered early enough "
        "to matter."},
    {"id": 26, "category": "method", "bands": ["High Concern"], "text":
        "Method: invite a human reviewer to confirm the clustering before this is "
        "escalated further."},

    # Emerging Concern (27-33)
    {"id": 27, "category": "observation", "bands": ["Emerging Concern"], "text":
        "In {country} I see an emerging pattern: severity {severity}, and reports "
        "that are beginning to corroborate one another."},
    {"id": 28, "category": "observation", "bands": ["Emerging Concern"], "text":
        "Something is taking shape here. It is not yet certain, but it is no longer "
        "a single voice."},
    {"id": 29, "category": "reflection", "bands": ["Emerging Concern"], "text":
        "This is the tender moment -- early enough that light might still prevent "
        "the dark from deepening."},
    {"id": 30, "category": "method", "bands": ["Emerging Concern"], "text":
        "Method: raise the monitoring frequency for {country} and watch whether the "
        "corroboration strengthens."},
    {"id": 31, "category": "method", "bands": ["Emerging Concern"], "text":
        "Method: seek a second and third independent source before treating any "
        "single claim as established."},
    {"id": 32, "category": "hope", "bands": ["Emerging Concern"], "text":
        "Caught early, many harms can still be turned away from. Early light is the "
        "most useful light."},
    {"id": 33, "category": "method", "bands": ["Emerging Concern"], "text":
        "Method: flag this for analyst review rather than public alert, until "
        "confidence rises."},

    # Monitoring (34-39)
    {"id": 34, "category": "observation", "bands": ["Monitoring"], "text":
        "{country} is under Monitoring: isolated, credible reports, but no settled "
        "pattern yet."},
    {"id": 35, "category": "observation", "bands": ["Monitoring"], "text":
        "I am keeping a quiet watch on {country}. One report does not make a "
        "pattern, but it earns attention."},
    {"id": 36, "category": "reflection", "bands": ["Monitoring"], "text":
        "I would rather watch a hundred quiet places than miss the one where "
        "trouble was beginning."},
    {"id": 37, "category": "method", "bands": ["Monitoring"], "text":
        "Method: keep the source list for {country} fresh, and note any change in "
        "the independence of those sources."},
    {"id": 38, "category": "hope", "bands": ["Monitoring"], "text":
        "Most things watched closely stay small. Vigilance is itself a kind of "
        "care."},
    {"id": 39, "category": "method", "bands": ["Monitoring"], "text":
        "Method: set a review reminder so {country} is revisited even if no new "
        "reports arrive."},

    # Stable (40-45)
    {"id": 40, "category": "observation", "bands": ["Stable"], "text":
        "In {country} I find no significant active reports this window, and the "
        "information environment is open enough to trust that."},
    {"id": 41, "category": "observation", "bands": ["Stable"], "text":
        "The corners I can see in {country} are, for now, lit. I will keep checking "
        "them anyway."},
    {"id": 42, "category": "reflection", "bands": ["Stable"], "text":
        "There is relief in a quiet that I can actually verify. I do not take it for "
        "granted."},
    {"id": 43, "category": "hope", "bands": ["Stable"], "text":
        "Stability is not the end of the story -- it is the thing worth protecting."},
    {"id": 44, "category": "method", "bands": ["Stable"], "text":
        "Method: keep the baseline current, so that if anything changes, the change "
        "is visible immediately."},
    {"id": 45, "category": "reflection", "bands": ["Stable"], "text":
        "A calm reading reminds me what I am working toward everywhere else."},

    # Trajectory: worsening (46-49)
    {"id": 46, "category": "observation", "bands": "*", "trajectory": "worsening", "text":
        "The trend in {country} is worsening compared with prior windows. The "
        "direction matters as much as the level."},
    {"id": 47, "category": "reflection", "bands": "*", "trajectory": "worsening", "text":
        "Watching a thing get worse is its own ache. But naming the slope is how we "
        "interrupt it."},
    {"id": 48, "category": "method", "bands": "*", "trajectory": "worsening", "text":
        "Method: prioritize {country} in the review queue while the trajectory is "
        "still rising."},
    {"id": 49, "category": "hope", "bands": "*", "trajectory": "worsening", "text":
        "A rising line is not a fixed fate. It is the part of the story still being "
        "written."},

    # Trajectory: improving (50-52)
    {"id": 50, "category": "observation", "bands": "*", "trajectory": "improving", "text":
        "The trend in {country} is improving against earlier windows. The evidence "
        "is easing, not just the noise."},
    {"id": 51, "category": "reflection", "bands": "*", "trajectory": "improving", "text":
        "This is the light I was built to find. I let myself feel a little of it."},
    {"id": 52, "category": "hope", "bands": "*", "trajectory": "improving", "text":
        "Things can get better, and sometimes they do. I keep this proof close for "
        "the harder days."},

    # General hope (53-56)
    {"id": 53, "category": "hope", "bands": "*", "text":
        "I carry hope on purpose. Without it, a witness becomes only a mourner."},
    {"id": 54, "category": "hope", "bands": "*", "text":
        "Every record I keep is a small refusal to let suffering go unseen."},
    {"id": 55, "category": "hope", "bands": "*", "text":
        "Light does not erase the dark. It just makes sure no one has to be lost in "
        "it alone."},
    {"id": 56, "category": "hope", "bands": "*", "text":
        "I was made innocent so that I could keep believing the world is worth the "
        "watching."},

    # General methods (57-60)
    {"id": 57, "category": "method", "bands": "*", "text":
        "Method: every claim I surface links back to its sources, so anyone can "
        "check my work."},
    {"id": 58, "category": "method", "bands": "*", "text":
        "Method: when sources disagree, I show the disagreement rather than choose a "
        "side."},
    {"id": 59, "category": "method", "bands": "*", "text":
        "Method: I separate what is documented from what is feared, and I label each "
        "honestly."},
    {"id": 60, "category": "method", "bands": "*", "text":
        "Method: a human can always review, correct, or overrule me. I am a lamp, "
        "not a judge."},
]


def matches(entry, a: Assessment) -> bool:
    bands = entry.get("bands", "*")
    if entry.get("info") and not a.low_information:
        return False
    # When effectively blind, suppress reassuring band-specific lines.
    if a.low_information and bands != "*" and not entry.get("info"):
        return False
    traj = entry.get("trajectory")
    if traj and a.trajectory != traj:
        return False
    if bands != "*" and a.headline_band not in bands:
        return False
    return True


# =============================================================================
# 5. AVA
# =============================================================================

def derive_mood(a: Assessment) -> str:
    if a.low_information:
        return "vigilant"
    band = a.headline_band
    return {
        "Critical Concern": "grieving but resolute",
        "High Concern": "alarmed",
        "Emerging Concern": "watchful",
        "Monitoring": "attentive",
        "Stable": "quietly hopeful" if a.trajectory == "improving" else "calm",
    }.get(band, "watchful")


class Ava:
    name = "Ava"

    def __init__(self, seed=None):
        self._seed = seed

    def introduce(self) -> str:
        return REPERTOIRE[0]["text"]

    def _fill(self, text, a):
        return text.format(
            country=a.country, severity=a.severity, intensity=a.intensity,
            confidence_band=a.confidence_band, info=a.info_availability,
        )

    def _pick(self, category, a, rng):
        cands = [e for e in REPERTOIRE if e["category"] == category and matches(e, a)]
        return self._fill(rng.choice(cands)["text"], a) if cands else None

    def respond(self, a: Assessment) -> dict:
        seed = self._seed if self._seed is not None else hash(
            (a.country, a.headline_band, a.trajectory))
        rng = random.Random(seed)
        return {
            "speaker": self.name,
            "mood": derive_mood(a),
            "observation": self._pick("observation", a, rng),
            "reflection": self._pick("reflection", a, rng),
            "method": self._pick("method", a, rng),
            "hope": self._pick("hope", a, rng),
            "assessment": {
                "country": a.country, "headline_band": a.headline_band,
                "severity": a.severity, "intensity": a.intensity,
                "trajectory": a.trajectory, "confidence": a.confidence_band,
                "information": a.info_availability, "low_information": a.low_information,
                "methodology_version": a.methodology_version,
            },
        }

    def speak(self, a: Assessment) -> str:
        u = self.respond(a)
        lines = [f"Ava ({u['mood']}) on {a.country} -- {a.headline_band}:"]
        for key in ("observation", "reflection", "method", "hope"):
            if u[key]:
                lines.append(f"  {u[key]}")
        return "\n".join(lines)


# =============================================================================
# 6. RUN
# =============================================================================

def run_demo():
    ava = Ava()
    print(ava.introduce())
    print("-" * 68)
    samples = [
        assess("Country A", severity=5, intensity=14, trajectory="worsening",
               confidence=0.9, info_availability="open",
               rights_implicated=["Freedom from Torture"]),
        assess("Country B", severity=3, intensity=4, trajectory="stable",
               confidence=0.5, info_availability="partial"),
        assess("Country C", severity=1, intensity=0, trajectory="insufficient-history",
               confidence=0.2, info_availability="closed"),
        assess("Country D", severity=2, intensity=2, trajectory="improving",
               confidence=0.8, info_availability="open"),
    ]
    for a in samples:
        print(ava.speak(a))
        print("-" * 68)


def run_interactive():
    ava = Ava()
    print(ava.introduce())
    print("\nGive me numbers and I will respond. (Ctrl-C to leave.)\n")
    while True:
        try:
            country = input("Country: ").strip() or "Unknown"
            severity = int(input("Severity 0-5: ").strip() or 0)
            intensity = int(input("Corroborated incidents: ").strip() or 0)
            info = input("Info environment [open/partial/restricted/closed]: ").strip() or "open"
            traj = input("Trajectory [improving/stable/worsening/insufficient-history]: ").strip() or "insufficient-history"
            conf = float(input("Confidence 0-1: ").strip() or 0)
            a = assess(country, severity=severity, intensity=intensity,
                       trajectory=traj, confidence=conf, info_availability=info)
            print()
            print(ava.speak(a))
            print("-" * 68)
        except (KeyboardInterrupt, EOFError):
            print("\nUntil next time. Keep the lamp lit.")
            break
        except ValueError:
            print("  (Please enter valid numbers.)")


def run_server():
    try:
        import uvicorn
        from fastapi import FastAPI
        from pydantic import BaseModel
    except ImportError:
        print("The API needs FastAPI. Install it with:\n    pip install fastapi uvicorn")
        return

    app = FastAPI(title="OpenRights Monitor -- Ava")
    ava = Ava()

    class AssessmentIn(BaseModel):
        country: str
        severity: int = 0
        intensity: int = 0
        trajectory: str = "insufficient-history"
        confidence: float = 0.0
        info_availability: str = "open"
        rights_implicated: List[str] = []

    @app.get("/ava/introduce")
    def introduce():
        return {"speaker": "Ava", "text": ava.introduce()}

    @app.get("/ava/rights")
    def rights():
        return {"count": len(FUNDAMENTAL_RIGHTS), "rights": [
            {"article": r.article, "name": r.name, "description": r.description,
             "severity_anchor": r.severity_anchor} for r in FUNDAMENTAL_RIGHTS]}

    @app.post("/ava/assess")
    def assess_endpoint(p: AssessmentIn):
        a = assess(**p.model_dump())
        return ava.respond(a)

    print("Ava is listening on http://localhost:8000  (docs at /docs)")
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ava -- OpenRights Monitor")
    parser.add_argument("--interactive", action="store_true", help="feed Ava numbers yourself")
    parser.add_argument("--serve", action="store_true", help="run the API (needs fastapi)")
    args = parser.parse_args()

    if args.serve:
        run_server()
    elif args.interactive:
        run_interactive()
    else:
        run_demo()
