"""
score.py — Deterministische Vorbewertung: Ressort-Keyword-Score, Region-Bonus.
Schreibt score in jede Quelldatei. Artikel unter minimum_score_to_advance
fliegen raus (Datei bleibt, Flag wird gesetzt).

Läuft zweimal: vor fulltext.py (Teaser → Auswahl, welche Originale geholt
werden) und danach (Volltext). Weiter kommt nur, wer einen gelesenen
Originalartikel hat (vollstaendig=True) UND genug Punkte.
"""
import json
import datetime
import pathlib
import re
import sys
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import run_day  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = yaml.safe_load((ROOT / "config" / "feeds.yml").read_text(encoding="utf-8"))
SCORES = CONFIG.get("scores", {})
MIN_SCORE = SCORES.get("minimum_score_to_advance", 4)

# Ressort-Name im Output wie in den Prompts (alle-gleich mit Bindestrich)
KEYWORDS = {}
for key, name in [("kohle", "kohle"), ("wohnen", "wohnen"), ("pflege", "pflege"),
                  ("klima", "klima"), ("alle_gleich", "alle-gleich")]:
    KEYWORDS[name] = SCORES.get(f"keywords_{key}", [])
REGION_BONUS_WORDS = SCORES.get("keywords_region_bonus", [])
REGION_BONUS_OST = SCORES.get("region_bonus_ost", 2)
CONFLICT_BONUS = SCORES.get("conflict_bonus", 1)

CONFLICT_WORDS = [
    "Protest", "Warnstreik", "Konflikt", "vor Gericht", "Urteil",
    "Klage", "Skandal", "kritisiert", "warf vor", "Tausende",
]


def hit(word, text):
    """Treffer am Wortanfang (Komposita wie 'Mindestlohnerhöhung' zählen,
    'Datenschutz' zählt nicht für 'Schutz')."""
    return re.search(r"(?<![\wäöüß])" + re.escape(word.lower()), text) is not None


def score_item(item):
    text = (item.get("title", "") + " " + (item.get("volltext") or item.get("summary", ""))).lower()
    s = 0
    best_ressort, best_hits = None, 0
    for ressort, words in KEYWORDS.items():
        hits = sum(1 for w in words if hit(w, text))
        s += hits
        if hits > best_hits:
            best_ressort, best_hits = ressort, hits
    if item.get("region"):
        s += REGION_BONUS_OST
    elif any(hit(w, text) for w in REGION_BONUS_WORDS):
        s += REGION_BONUS_OST
    s += CONFLICT_BONUS * sum(1 for w in CONFLICT_WORDS if hit(w, text))
    return s, best_ressort


def main():
    today = run_day()
    src_dir = ROOT / "sources" / today
    candidates = 0
    for path in sorted(src_dir.glob("*.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        s, ressort = score_item(item)
        item["score"] = s
        item["ressort_detected"] = ressort
        item["advance"] = s >= MIN_SCORE and item.get("vollstaendig") is True
        path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        if item["advance"]:
            candidates += 1
    print(f"Kandidaten für KI0: {candidates}")


if __name__ == "__main__":
    main()
