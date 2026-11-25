# Knall — Das linke Boulevard

Automatisierte Boulevard-Pipeline: links, arbeitnehmerfreundlich, ökologisch, antirassistisch — in BILD-Sprache, aber ohne Lügen.

## Wie es funktioniert

1. **Sammeln** (GitHub Actions, täglich 06:00): RSS-Feeds von taz, nd, tagesschau, Deutschlandfunk, Ostdeutscher Lokalpresse (via Google-News-Spiegelfeeds), Behörden und Gewerkschaften werden gepollt.
2. **Maschinelle Vorbewertung** (Python, kostenlos): Keyword-Score nach Ressort, Duplikat-Filters, Vollständigkeit. Nur Top-Kandidaten kommen weiter.
3. **KI0 — Kuratorin** (kleines LLM): wählt 1–3 Themen, bildet Quellen-Cluster.
4. **KI1 — Schreiberin** (großes LLM): schreibt aus den Quellen, Maximum 250 Wörter, Sprachgesetz im Prompt.
5. **Regex-Gate** (Python): Stil- und Formfehler maschinell erkannt — Konjunktive, Eskalationsfloskeln, ANÜGRUPPE, erummelte Zahlen. Fehlertexte gehen billige Korrekturrunde zurück zu KI1, vor dem teuren KI2-Aufruf.
6. **KI2 — Prüferin** (großes LLM): Satz-für-Satz-Fakt-Prüfung (P1–P10), Ampel-JSON.
7. **Maschinelle Sonderprüfungen** (Python): Zahlen-Diff gegen Quelle, Entity-Check, N-Gramm-Plagiatscheck — der harte Boden unter allen LLMs.
8. **Ampel-Router**: Rot → rejected/ (mit Befund, fürs Statistiksystem). Gelb → max. 2 Korrekturrunden, dann Rot. Grün → weiter.
9. **KI3 — Rechtsabnahme** (LLM): Persönlichkeitsrechte, üble Nachrede. Freigabe → published/.
10. **Veröffentlichen**: GitHub Pages rendert published/ zur News-Seite.

## Verzeichnisse

```
├── .github/workflows/     daily.yml (Pipeline), deploy.yml (Ionos, vorhandene)
├── prompts/               ki0.txt, ki1.txt, ki2.txt, ki3.txt
├── scripts/               collect.py, score.py, ki_client.py, regex_gate.py,
│                          special_checks.py, router.py, stats.py, publish.py
├── config/feeds.yml       Alle Feed-URLs, Bewertungen, Ressort-Zuordnung
├── sources/               RSS-Archiv (JSON pro Artikel)
├── drafts/                KI1-Entwürfe + KI2-Protokolle
├── published/             Freigegebene Artikel (Markdown + Metadaten)
├── rejected/              Rote/gelb-erschlöpfte Artikel + Befund
├── site/                  GitHub-Pages-Skeleton
└── stats.json             Ampel-Statistik (Rot-Gründe, Trends)
```

## Setup

Benötigte GitHub-Secrets:
- `OPENROUTER_API_KEY` (oder Äquivalent) — für KI0–KI3

Optional:
- `KI_MODEL_SMALL`, `KI_MODEL_LARGE` — Modellwahl, Default siehe scripts/ki_client.py

## Transparenz

Das komplette Repo ist offen: Prompts, Quellen, Entwürfe, Ablehnungen. Jede Änderung am Regelwerk ist ein Git-Commit. Rote Artikel mit Befund im `rejected/`-Verzeichnis — Fehler werden nicht versteckt.
