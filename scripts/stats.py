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
    day = data.setdefault(today, {"rot": 0, "gelb": 0, "gruen": 0, "ki3_stopp": 0, "published": 0})
    rejected_dir = ROOT / "rejected"
    for p in rejected_dir.glob(f"{today}-*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        ampel = d.get("ki2_protokoll", {}).get("ampel")
        if ampel == "rot" or d.get("special_checks") and not d["special_checks"].get("bestaende"):
            day["rot"] += 1
        elif ampel == "gelb":
            day["gelb"] += 1
        elif d.get("ki3_entscheidung", {}).get("entscheidung") == "stopp":
            day["ki3_stopp"] += 1
    day["published"] = len(list((ROOT / "published").glob(f"{today}-*.md")))
    # Systemwarnung: mehr als 30% rot
    total = day["rot"] + day["gelb"] + day["published"]
    if total and day["rot"] / total > 0.3:
        day["warnung_rot_quote"] = True
    STATS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(day, ensure_ascii=False))


if __name__ == "__main__":
    main()
