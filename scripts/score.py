"""
score.py — Deterministische Vorbewertung: Ressort-Keyword-Score, Region-Bonus.
Schreibt score in jede Quelldatei. Artikel unter minimum_score_to_advance
fliegen raus (Datei bleibt, Flag wird gesetzt).
"""
import json
import datetime
import pathlib
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = yaml.safe_load((ROOT / "config" / "feeds.yml").read_text(encoding="utf-8"))
SCORES = CONFIG.get("scores", {})
MIN_SCORE = SCORES.get("minimum_score_to_advance", 4)

KEYWORDS = {}
for ressort in ["kohle", "wohnen", "pflege", "klima", "alle_gleich"]:
    KEYWORDS[ressort] = SCORES.get(f"keywords_{ressort}", [])
REGION_BONUS_WORDS = SCORES.get("keywords_region_bonus", [])
REGION_BONUS_OST = SCORES.get("region_bonus_ost", 2)
CONFLICT_BONUS = SCORES.get("conflict_bonus", 1)

CONFLICT_WORDS = [
    "Protest", "Warnstreik", "Konflikt", "vor Gericht", "Urteil",
    "Klage", "Skandal", "kritisiert", "warf vor", "Tausende",
]


def score_item(item):
    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    s = 0
    best_ressort = None
    for ressort, words in KEYWORDS.items():
        hits = sum(1 for w in words if w.lower() in text)
        if hits > 0:
            s += hits
            if best_ressort is None or hits > 0:
                best_ressort = ressort
    if item.get("region"):
        s += REGION_BONUS_OST
    elif any(w.lower() in text for w in REGION_BONUS_WORDS):
        s += REGION_BONUS_OST
    s += CONFLICT_BONUS * sum(1 for w in CONFLICT_WORDS if w.lower() in text)
    if not item.get("vollstaendig"):
        s -= 2
    return s, best_ressort


def main():
    today = datetime.date.today().isoformat()
    src_dir = ROOT / "sources" / today
    candidates = 0
    for path in sorted(src_dir.glob("*.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        s, ressort = score_item(item)
        item["score"] = s
        item["ressort_detected"] = ressort
        item["advance"] = s >= MIN_SCORE
        path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        if item["advance"]:
            candidates += 1
    print(f"Kandidaten für KI0: {candidates}")


if __name__ == "__main__":
    main()
