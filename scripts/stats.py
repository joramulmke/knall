"""
stats.py — Fortlaufende Ampel-Statistik. Frühwarnsystem für Systemfehler.
"""
import json
import datetime
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATS = ROOT / "stats.json"


def main():
    today = datetime.date.today().isoformat()
    data = {}
    if STATS.exists():
        data = json.loads(STATS.read_text(encoding="utf-8"))
    # Tag immer neu berechnen (sonst zählen Wiederholungsläufe doppelt)
    day = data[today] = {"rot": 0, "gelb": 0, "ki3_stopp": 0, "fehler": 0, "published": 0, "gruende": []}
    rejected_dir = ROOT / "rejected"
    for p in rejected_dir.glob(f"{today}-*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        gruende = d.get("ablehnungsgruende") or []
        day["gruende"].extend(gruende)
        text = " ".join(gruende)
        if d.get("abbruch") or "Tageslimit" in text or "technischer Fehler" in text:
            day["fehler"] += 1
        elif "KI3" in text:
            day["ki3_stopp"] += 1
        elif d.get("gelb_erschoepft"):
            day["gelb"] += 1
        else:
            day["rot"] += 1
    day["published"] = len(list((ROOT / "published").glob(f"{today}-*.md")))
    # Systemwarnung: mehr als 30% rot
    total = day["rot"] + day["gelb"] + day["published"]
    if total and day["rot"] / total > 0.3:
        day["warnung_rot_quote"] = True
    STATS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(day, ensure_ascii=False))


if __name__ == "__main__":
    main()
