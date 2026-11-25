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
10. **Veröffentlichen**: `build_site.py` rendert published/ (HTML-escaped) in site/index.html, GitHub Pages veröffentlicht.

## Sicherheitsmodell (keine menschliche Freigabe → fail-closed)

- Nur `:free`-Modelle bei OpenRouter; andere Modell-IDs werden verweigert. Die Fallback-Kette wird zur Laufzeit gegen die OpenRouter-Modellliste abgeglichen. Tageslimit (429 „per-day“) bricht sofort ab.
- Quellen werden bereinigt (HTML, Steuerzeichen) und in `<quelle>`-Blöcke gesetzt; Quellen mit Anweisungs-Text (Prompt-Injection) werden verworfen (`scripts/safety.py`).
- KI2: Grün nur, wenn das Protokoll vollständig ist (jeder Satz geprüft, nur bekannte Befunde) und die nachgerechnete Ampel ebenfalls grün ergibt. Alles Unklare = rot.
- Sonderprüfungen (Zahlen, Namen/Kürzel, 20-Wort-Plagiat, Injection) laufen immer, auch nach Grün.
- KI3: Veröffentlicht wird nur eine eindeutige, vollständige „freigabe“ (R1–R6). „stopp“ oder Unklares → rejected, endgültig. „nachbesserung“ → alle Ersatzformulierungen müssen wörtlich greifen, danach genau EINE Nachprüfung (KI2 + Sonderprüfung + KI3); jedes Nichtbestehen → rejected, keine weitere Schleife.
- Fortsetzung bei Abbruch: Teaser und Entwürfe liegen im Actions-Cache. Ein erneuter Lauf überspringt Erledigtes und wiederholt nur technische Fehler; inhaltliche Ablehnungen sind endgültig.
- Ein Datum pro Lauf (`KNALL_DAY`), auch über Mitternacht.
- Offene Entwürfe von gestern werden in jeder Stufe vor den heutigen fertig gemacht und mit heutigem Datum veröffentlicht (`entstanden:` im Frontmatter). Was älter als gestern und noch offen ist, wird als „abgelaufen“ vermerkt und aufgeräumt.
- Weitere Läufe am selben Tag: Ist von heute nichts mehr offen, wählt KI0 neue Themen aus noch unbenutzten Teasern (ohne Wiederholung der Themen von gestern/heute), bis max. 6 Artikel pro Tag (`KNALL_MAX_ARTIKEL_PRO_TAG`).
- SSH-Host-Schlüssel (Deploy-Server, Mirror) werden beim ersten Deploy gemerkt (Cache `sftp-hostkey-v1` / `mirror-hostkey-v1`) und danach strikt geprüft. Bei legitimem Schlüsselwechsel den Cache löschen.
- publish.py prüft die Freigabe ein zweites Mal (`ampel_final == "gruen"` und `freigabe_final`).

## Verzeichnisse

```
├── .github/workflows/     daily.yml (Pipeline), deploy-and-mirror.yml (Deploy-Server + Mirror, nur bei manuellen Pushes)
├── prompts/               ki0.txt, ki1.txt, ki2.txt, ki3.txt
├── scripts/               collect.py, score.py, ki_client.py, regex_gate.py,
│                          special_checks.py, router.py, stats.py, publish.py
├── config/feeds.yml       Alle Feed-URLs, Bewertungen, Ressort-Zuordnung
├── sources/               Teaser des Tages (nur im privaten Actions-Cache, nicht committet)
├── drafts/                Entwürfe + Prüfprotokolle (nur im privaten Actions-Cache, nicht committet)
├── published/             Freigegebene Artikel (Markdown + Metadaten)
├── rejected/              Ablehnungs-Befunde OHNE Artikeltext (Gründe, Ampeln, Prüfposten)
├── site/                  GitHub-Pages-Skeleton
└── stats.json             Ampel-Statistik (Rot-Gründe, Trends)
```

## Setup

Benötigte GitHub-Secrets:
- `OPENROUTER_API_KEY` (oder Äquivalent) — für KI0–KI3

Optional:
- `KI_MODEL_SMALL`, `KI_MODEL_LARGE`, `KI_FALLBACK_MODELS` (kommagetrennt) — nur `:free`-Modelle, Default siehe scripts/ki_client.py

## Transparenz

Das komplette Repo ist offen: Prompts, Quellen, Entwürfe, Ablehnungen. Jede Änderung am Regelwerk ist ein Git-Commit. Rote Artikel mit Befund im `rejected/`-Verzeichnis — Fehler werden nicht versteckt.
