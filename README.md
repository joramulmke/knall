# Knall — Das linke Boulevard

Automatisierte Boulevard-Pipeline: links, arbeitnehmerfreundlich, ökologisch, antirassistisch — in BILD-Sprache, aber ohne Lügen.

## Wie es funktioniert

1. **Sammeln** (GitHub Actions, täglich 06:00): RSS-Feeds von taz, nd, tagesschau, Deutschlandfunk, Ostdeutscher Lokalpresse (via Google-News-Spiegelfeeds), Behörden und Gewerkschaften werden gepollt.
2. **Maschinelle Vorbewertung** (Python, kostenlos): Keyword-Score nach Ressort auf den Teasern.
3. **Originalartikel holen** (`fulltext.py`): Für die besten Teaser wird die Original-URL ermittelt (Google-News-Links werden aufgelöst) und der Volltext gelesen (robots.txt wird respektiert). Paywall-Teaser, Duplikate und nicht auflösbare Links fallen raus — **geschrieben wird nur aus Originalartikeln**. Danach erneute Bewertung auf dem Volltext.
4. **KI0 — Kuratorin** (kleines LLM): wählt 1–3 Themen, bildet Quellen-Cluster.
5. **KI1 — Schreiberin** (großes LLM): schreibt aus den Quellen, Maximum 250 Wörter, Sprachgesetz im Prompt.
6. **Regex-Gate** (Python): Stil- und Formfehler maschinell erkannt — Konjunktive, Passiv, Floskeln, Ansprache, GROSSBUCHSTABEN. Fehlertexte gehen als billige Korrekturrunde zurück zu KI1. Läuft nach **jeder** Textänderung (Entwurf, Gelb-Korrektur, KI3-Ersatzformulierung).
7. **KI2 — Prüferin** (andere Modellfamilie als KI1, Schreib-Modell als Backup): Satz-für-Satz-Fakt-Prüfung (P1–P10), Ampel-JSON.
8. **Maschinelle Sonderprüfungen** (Python, ohne KI): Zahlen-Diff gegen Quelle — exakt nach Wert, Ziffern und Zahlwörter inkl. Größenordnung („zwei“ = 2, „2,5 Mio.“ = 2.500.000), Entity-Check, N-Gramm-Plagiatscheck — der harte Boden unter allen LLMs. Abgesichert durch Tests (`tests/`), die vor jedem Lauf laufen.
9. **Ampel-Router**: Rot → rejected/ (mit Befund, fürs Statistiksystem). Gelb → max. 2 Korrekturrunden, dann Rot. Grün → weiter.
10. **KI3 — Rechtsabnahme** (Prüf-Modell wie KI2): Persönlichkeitsrechte, üble Nachrede. Freigabe → published/.
11. **Veröffentlichen**: `build_site.py` rendert published/ (HTML-escaped) in site/index.html, GitHub Pages veröffentlicht. Unter jedem Artikel stehen die Originalartikel mit Original-URL und Medium.

## Sicherheitsmodell (keine menschliche Freigabe → fail-closed)

- Nur `:free`-Modelle bei OpenRouter; andere Modell-IDs werden verweigert. Die Fallback-Kette wird zur Laufzeit gegen die OpenRouter-Modellliste abgeglichen. Tageslimit (429 „per-day“) bricht sofort ab.
- Quellen werden bereinigt (HTML, Steuerzeichen) und in `<quelle>`-Blöcke gesetzt; Quellen mit Anweisungs-Text (Prompt-Injection) werden verworfen (`scripts/safety.py`).
- KI2: Grün nur, wenn das Protokoll vollständig ist (jeder Satz geprüft, nur bekannte Befunde) und die nachgerechnete Ampel ebenfalls grün ergibt. Alles Unklare = rot.
- Sonderprüfungen (Zahlen, Namen/Kürzel, 20-Wort-Plagiat, Injection) laufen immer, auch nach Grün.
- KI3: Veröffentlicht wird nur eine eindeutige, vollständige „freigabe“ (R1–R6). „stopp“ oder Unklares → rejected, endgültig. „nachbesserung“ → alle Ersatzformulierungen müssen wörtlich greifen, danach genau EINE Nachprüfung (KI2 + Sonderprüfung + KI3); jedes Nichtbestehen → rejected, keine weitere Schleife.
- Eigener LLM-Tageszähler: Jede Anfrage zählt (429 separat). Ab `KNALL_TAGESLIMIT_ANFRAGEN` (Default 50) wird nicht mehr angefragt; neue Artikel starten nur, wenn noch mind. 8 Anfragen übrig sind. Die Tageswerte stehen dauerhaft in `stats.json`, `_kontingent` zeigt die letzten 28 Tage (Schnitt, Maximum, Tage am Limit, 429-Anteil).
- Fortsetzung bei Abbruch: Teaser, Volltexte und Entwürfe liegen **verschlüsselt** im Actions-Cache (Schlüssel abgeleitet aus `OPENROUTER_API_KEY`, den Fork-PRs nie bekommen). Ein erneuter Lauf überspringt Erledigtes und wiederholt nur technische Fehler; inhaltliche Ablehnungen sind endgültig.
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
│                          special_checks.py, router.py, stats.py, publish.py, fulltext.py
├── config/feeds.yml       Alle Feed-URLs, Bewertungen, Ressort-Zuordnung
├── sources/               Teaser + Volltexte des Tages (nur verschlüsselt im Actions-Cache, nicht committet)
├── drafts/                Entwürfe, Prüfprotokolle, Tageszähler (nur verschlüsselt im Actions-Cache)
├── tests/                 Tests der maschinellen Prüfungen (laufen vor jedem Lauf)
├── published/             Freigegebene Artikel (Markdown + Metadaten)
├── rejected/              Ablehnungs-Befunde OHNE Artikeltext (Gründe, Ampeln, Prüfposten)
├── site/                  GitHub-Pages-Skeleton
└── stats.json             Ampel-Statistik (Rot-Gründe, Trends)
```

## Setup

Benötigte GitHub-Secrets:
- `OPENROUTER_API_KEY` (oder Äquivalent) — für KI0–KI3

Optional (Actions-Variablen):
- `KI_MODEL_SMALL`, `KI_MODEL_LARGE`, `KI_MODEL_CHECK` (Prüferin KI2/KI3), `KI_FALLBACK_MODELS` (kommagetrennt) — nur `:free`-Modelle, Default siehe scripts/ki_client.py
- `KNALL_TAGESLIMIT_ANFRAGEN` — eigener Tageszähler, Default 50

Einmalig in den Repo-Einstellungen: **Settings → Pages → Source: „GitHub Actions“** (sonst schlägt der Pages-Job fehl).

Abhängigkeiten sind in `requirements.txt` fest gepinnt, Actions auf Commit-SHAs.

## Transparenz

Das komplette Repo ist offen: Prompts, Code, Ablehnungsbefunde (ohne Text). Volle Entwürfe und Quelltexte bleiben verschlüsselt im Cache. Jede Änderung am Regelwerk ist ein Git-Commit. Rote Artikel mit Befund im `rejected/`-Verzeichnis — Fehler werden nicht versteckt.
