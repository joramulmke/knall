# Knall — Das linke Boulevard

Automatisierte Boulevard-Pipeline: links, arbeitnehmerfreundlich, ökologisch, antirassistisch — in BILD-Sprache, aber ohne Lügen.

## Wie es funktioniert

1. **Sammeln** (GitHub Actions, täglich 06:00): RSS-Feeds von taz, nd, tagesschau, Deutschlandfunk, Ostdeutscher Lokalpresse (via Google-News-Spiegelfeeds), Behörden und Gewerkschaften werden gepollt.
2. **Maschinelle Vorbewertung** (Python, kostenlos): Keyword-Score nach Ressort auf den Teasern.
3. **Originalartikel holen** (`fulltext.py`): Für die besten Teaser wird die Original-URL ermittelt (Google-News-Links werden aufgelöst) und der Volltext gelesen (robots.txt wird respektiert). Paywall-Teaser, Duplikate und nicht auflösbare Links fallen raus — **geschrieben wird nur aus Originalartikeln**. Bis zu 60 Abrufe pro Lauf; Feeds, die blocken (403, Paywall, robots.txt), werden nach 2 Blockaden im Lauf übersprungen und über ein 14-Tage-Gedächtnis nach hinten sortiert (Bilanz pro Feed: `stats.json` → `_volltext`). Danach erneute Bewertung auf dem Volltext.
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
- Bundesgerichte (BVerfG, BGH, BVerwG, BFH, BAG, BSG, BPatG) über rechtsprechung-im-internet.de. Für sie zählt das Feed-Datum (`datum: feed` in `config/feeds.yml`), nicht das ältere Entscheidungsdatum auf der Seite.
- Nur aktuelle Meldungen: Älter als `KNALL_MAX_ALTER_TAGE` (Default 3) → kein Volltext-Abruf, keine Story. Maßgeblich ist das Datum im Originalartikel, sonst das Feed-Datum; ohne Datum wird nicht aussortiert.
- Groq-Minutenlimit (Tokens pro Minute): Die von Groq genannte Wartezeit wird bis zu 3-mal abgewartet, dann folgt das nächste Modell.
- Früher Modellwechsel: Bei 429 gibt es nur einen kurzen Wiederholversuch (15 s), bei leerer Antwort einen sofortigen; danach kommt das nächste Modell der Kette. Ein Modell mit 429 rückt für `KNALL_MODELL_SPERRE_S` (Default 600 s) auch in späteren Stufen ans Ende der Kette.
- Quellen werden bereinigt (HTML, Steuerzeichen) und in `<quelle>`-Blöcke gesetzt; Quellen mit Anweisungs-Text (Prompt-Injection) werden verworfen (`scripts/safety.py`).
- KI2: Grün nur, wenn das Protokoll vollständig ist (jeder Satz geprüft, nur bekannte Befunde) und die nachgerechnete Ampel ebenfalls grün ergibt. Alles Unklare = rot.
- Sonderprüfungen (Zahlen, Namen/Kürzel, 20-Wort-Plagiat, Injection) laufen immer, auch nach Grün.
- KI3: Veröffentlicht wird nur eine eindeutige, vollständige „freigabe“ (R1–R6). „stopp“ oder Unklares → rejected, endgültig. „nachbesserung“ → alle Ersatzformulierungen müssen wörtlich greifen, danach genau EINE Nachprüfung (KI2 + Sonderprüfung + KI3); jedes Nichtbestehen → rejected, keine weitere Schleife.
- Eigener LLM-Tageszähler: Jede Anfrage zählt (429 separat). Ab `KNALL_TAGESLIMIT_ANFRAGEN` (Default 50) wird nicht mehr angefragt; neue Artikel starten nur, wenn noch mind. 8 Anfragen übrig sind. Die Tageswerte stehen dauerhaft in `stats.json`, `_kontingent` zeigt die letzten 28 Tage (Schnitt, Maximum, Tage am Limit, 429-Anteil).
- Fortsetzung bei Abbruch: Teaser, Volltexte und Entwürfe liegen **verschlüsselt** im Actions-Cache (Schlüssel abgeleitet aus `OPENROUTER_API_KEY`, den Fork-PRs nie bekommen). Ein erneuter Lauf überspringt Erledigtes und wiederholt nur technische Fehler; inhaltliche Ablehnungen sind endgültig.
- Ein Datum pro Lauf (`KNALL_DAY`), auch über Mitternacht.
- Offene Entwürfe von gestern werden in jeder Stufe vor den heutigen fertig gemacht und mit heutigem Datum veröffentlicht (`entstanden:` im Frontmatter). Was älter als gestern und noch offen ist, wird als „abgelaufen“ vermerkt und aufgeräumt.
- Weitere Läufe am selben Tag: Ist von heute nichts mehr offen, wählt KI0 neue Themen aus noch unbenutzten Teasern (ohne Wiederholung der Themen von gestern/heute), höchstens 3 neue pro Lauf. Eine Tagesgrenze gibt es nicht; es bremst das Gratis-Kontingent. Optional begrenzt `KNALL_MAX_ARTIKEL_PRO_TAG` (Default 0 = keine Grenze). KI0 schreibt ins Log, was es vorgeschlagen hat und warum Vorschläge verworfen wurden.
- SSH-Host-Schlüssel (Deploy-Server, Mirror) werden beim ersten Deploy gemerkt (Cache `sftp-hostkey-v1` / `mirror-hostkey-v1`) und danach strikt geprüft. Bei legitimem Schlüsselwechsel den Cache löschen.
- publish.py prüft die Freigabe ein zweites Mal (`ampel_final == "gruen"` und `freigabe_final`).

## Verzeichnisse

```
├── .github/workflows/     daily.yml (Pipeline), deploy-and-mirror.yml (Deploy-Server + Mirror, nur bei manuellen Pushes),
│                          archiv-export.yml (Fehler-Archiv verschlüsselt herunterladen)
├── prompts/               ki0.txt, ki1.txt, ki2.txt, ki3.txt
├── scripts/               collect.py, score.py, ki_client.py, regex_gate.py,
│                          special_checks.py, router.py, stats.py, publish.py, fulltext.py
├── config/feeds.yml       Alle Feed-URLs, Bewertungen, Ressort-Zuordnung
├── sources/               Teaser + Volltexte des Tages (nur verschlüsselt im Actions-Cache, nicht committet)
├── drafts/                Entwürfe, Prüfprotokolle, Tageszähler (nur verschlüsselt im Actions-Cache)
├── tests/                 Tests der maschinellen Prüfungen (laufen vor jedem Lauf)
├── archiv/                Privates Fehler-Archiv abgelehnter Artikel (nur verschlüsselt im Cache)
├── published/             Freigegebene Artikel (Markdown + Metadaten)
├── rejected/              Ablehnungs-Befunde OHNE Artikeltext (Gründe, Ampeln, Prüfposten)
├── site/                  GitHub-Pages-Skeleton
└── stats.json             Ampel-Statistik (Rot-Gründe, Trends)
```

## Setup

Benötigte GitHub-Secrets:
- `OPENROUTER_API_KEY` (oder Äquivalent) — für KI0–KI3
- `GROQ_API_KEY` (optional, kostenloser Groq-Tarif ohne Zahlungsmittel) — dann prüfen KI2/KI3 zuerst mit `groq:openai/gpt-oss-120b`; Nemotron und das Schreib-Modell bleiben Backup. Eigenes Tageskontingent `KNALL_GROQ_TAGESLIMIT` (Default 900), eigene Zähler `groq_*` in `stats.json`. Groq-Tageslimit, Schlüsselfehler oder zu große Anfrage → automatisch weiter mit OpenRouter. Für KI0/KI1 steht Groq direkt hinter Gemma. Groq-Modelle: Prüfen/Auswählen `KI_GROQ_MODELS` (Default `openai/gpt-oss-120b`, `openai/gpt-oss-20b`), Schreiben `KI_GROQ_SCHREIBER` (Default `openai/gpt-oss-120b`, `qwen/qwen3.8-27b` — kein kleines Modell). Ein Groq-Minutenlimit stellt ein Modell nur 90 s nach hinten.

Optional (Actions-Variablen):
- `KI_MODEL_SMALL`, `KI_MODEL_LARGE`, `KI_MODEL_CHECK` (Prüferin KI2/KI3), `KI_FALLBACK_MODELS` (kommagetrennt) — nur `:free`-Modelle, Default siehe scripts/ki_client.py
- `KNALL_TAGESLIMIT_ANFRAGEN` — eigener Tageszähler, Default 50

Einmalig in den Repo-Einstellungen: **Settings → Pages → Source: „GitHub Actions“** (sonst schlägt der Pages-Job fehl).

Abhängigkeiten sind in `requirements.txt` fest gepinnt, Actions auf Commit-SHAs.

## Fehler-Archiv auswerten (privat)

Abgelehnte Artikel werden zusätzlich **vollständig** archiviert: letzter Text, alle Fassungen (KI1-Entwurf, Regex-/Gelb-/KI3-Korrekturen), alle KI2/KI3-Protokolle, Sonderprüfungen und die Quelltexte. Das Archiv (`archiv/abgelehnt/`) liegt nur im verschlüsselten Actions-Cache, nie im Repo, und wird nach 90 Tagen gelöscht (`KNALL_ARCHIV_TAGE`).

1. Actions → **„Fehler-Archiv exportieren“** → Run workflow
2. Artefakt `knall-archiv-verschluesselt` herunterladen und entpacken (`work.tar.enc`, bleibt verschlüsselt)
3. Lokal: `python scripts/archiv_auswerten.py work.tar.enc` → fragt nach dem OpenRouter-API-Key → schreibt `archiv_bericht.md` (häufigste Gründe, KI2-Befunde, Texte, Quellen)

Der Bericht enthält abgelehnte Texte — nicht veröffentlichen.

## Transparenz

Das komplette Repo ist offen: Prompts, Code, Ablehnungsbefunde (ohne Text). Volle Entwürfe und Quelltexte bleiben verschlüsselt im Cache. Jede Änderung am Regelwerk ist ein Git-Commit. Rote Artikel mit Befund im `rejected/`-Verzeichnis — Fehler werden nicht versteckt.
