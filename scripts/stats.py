"""
stats.py — Fortlaufende Ampel-Statistik. Frühwarnsystem für Systemfehler.
"""
import json
import datetime
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATS = ROOT / "stats.json"


def tag_stats(day):
    d_stats = {"rot": 0, "gelb": 0, "ki3_stopp": 0, "fehler": 0, "abgelaufen": 0, "published": 0, "gruende": []}
    for p in (ROOT / "rejected").glob(f"{day}-*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        gruende = d.get("ablehnungsgruende") or []
        d_stats["gruende"].extend(gruende)
        text = " ".join(gruende)
        if d.get("abgelaufen"):
            d_stats["abgelaufen"] += 1
        elif d.get("abbruch") or "Tageslimit" in text or "technischer Fehler" in text:
            d_stats["fehler"] += 1
        elif "KI3" in text:
            d_stats["ki3_stopp"] += 1
        elif d.get("gelb_erschoepft"):
            d_stats["gelb"] += 1
        else:
            d_stats["rot"] += 1
    d_stats["published"] = len(list((ROOT / "published").glob(f"{day}-*.md")))
    total = d_stats["rot"] + d_stats["gelb"] + d_stats["published"]
    if total and d_stats["rot"] / total > 0.3:
        d_stats["warnung_rot_quote"] = True
    return d_stats


def main():
    # Jeden Tag mit Einträgen neu berechnen (idempotent; erfasst auch
    # nachträglich "abgelaufen" markierte Entwürfe älterer Tage).
    days = set()
    for folder, pattern in (("rejected", "*.json"), ("published", "*.md")):
        for p in (ROOT / folder).glob(pattern):
            if re.match(r"\d{4}-\d{2}-\d{2}-", p.name):
                days.add(p.name[:10])
    data = {day: tag_stats(day) for day in sorted(days)}
    STATS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False)[:2000])


if __name__ == "__main__":
    main()
