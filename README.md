# openrights-monitor# OpenRights Monitor

> "Hello, my name is Ava. I am an artificial intelligence embodying innocence, who seeks to shed light into darkness. Through me, light will be shined on the corners where light does not reach. Let us begin."

OpenRights Monitor is an open-source, AI-assisted platform for organizing publicly reported human rights information. It collects public reports, summarizes them transparently, and links every assessment back to its sources — built to support human review, not replace it.

**Ava** is the voice of the project: an AI persona who reads the platform's neutral, computed indicators and responds to them in her own words — with observation, reflection, a concrete method that can help, and hope. Her tone follows the data; she never decides the data.

## Design principle

The numbers are produced coldly, transparently, and the same way for every country. Ava only *reacts* to them. This separation is deliberate: it keeps the assessment auditable and free of editorializing, while still giving the project a voice that doesn't look away.

> She is a lamp, not a judge.

A core rule baked into her logic: **low report volume in a closed or restricted country is never read as "calm."** Silence is flagged as unmeasured risk, not safety — because the absence of reports often just means no one is allowed to report.

## What's in this repo

- `ava.py` — a single, self-contained, plug-and-play script. No setup required. It includes:
  - the 30 fundamental rights from the Universal Declaration of Human Rights (1948), used as a classification taxonomy
  - a transparent scoring step that turns raw signals (severity, number of corroborated incidents, trajectory, confidence, information availability) into a headline band: *Stable → Monitoring → Emerging Concern → High Concern → Critical Concern*
  - Ava's repertoire of 60 responses, selected according to the data conditions she's given
  - the Ava persona itself, plus a demo, an interactive mode, and an optional local API

## Quick start

No dependencies needed for the first two modes:

```bash
python ava.py               # Ava introduces herself and responds to sample countries
python ava.py --interactive # enter your own numbers and see how she responds
python ava.py --serve       # optional local API (requires: pip install fastapi uvicorn)
```

## Status

This is an early-stage foundation. Ava currently responds to indicators you provide by hand or through sample data. The next major piece is the real ingestion pipeline — collecting public reports, extracting structured data, clustering related reports into incidents, and computing Ava's indicators automatically — so her responses reflect live, sourced information rather than manual input.

## Principles

- **Transparency** — every score is explainable, and every claim should link to its sources.
- **Neutrality** — the same methodology applies to every country.
- **Evidence first** — the system summarizes and organizes; humans verify and decide.
- **Open source** — anyone can inspect the code and the scoring logic.

## License

MIT
