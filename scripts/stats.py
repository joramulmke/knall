"""
stats.py — Fortlaufende Ampel-Statistik. Frühwarnsystem für Systemfehler.

Dazu der LLM-Tageszähler (drafts/<tag>-anfragen.json aus ki_client.py):
pro Tag "anfragen" (alle HTTP-Versuche), "r429" separat, ok/fehler/leer.
Bleibt dauerhaft in stats.json, damit sich langfristig prüfen lässt, ob die
Pipeline im Free-Kontingent (50/Tag) bleibt → "_kontingent".
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


def kontingent(anfragen, tage=28):
    """Langzeitblick: Reicht das Free-Kontingent? (letzte 28 Tage)"""
    letzte = [anfragen[d] for d in sorted(anfragen)[-tage:]]
    if not letzte:
        return {"tage": 0}
    n = [z.get("anfragen", 0) for z in letzte]
    limit = letzte[-1].get("limit") or 50
    return {
        "tage": len(n),
        "limit": limit,
        "schnitt_anfragen": round(sum(n) / len(n), 1),
        "max_anfragen": max(n),
        "tage_am_limit": sum(1 for x in n if x >= limit),
        "tage_ueber_80_prozent": sum(1 for x in n if x >= 0.8 * limit),
        "r429_gesamt": sum(z.get("r429", 0) for z in letzte),
        "anteil_429": round(sum(z.get("r429", 0) for z in letzte) / max(1, sum(n)), 3),
        "im_kontingent": max(n) < limit,
    }


def main():
    # Jeden Tag mit Einträgen neu berechnen (idempotent; erfasst auch
    # nachträglich "abgelaufen" markierte Entwürfe älterer Tage).
    days = set()
    for folder, pattern in (("rejected", "*.json"), ("published", "*.md")):
        for p in (ROOT / folder).glob(pattern):
            if re.match(r"\d{4}-\d{2}-\d{2}-", p.name):
                days.add(p.name[:10])
    try:
        alt = json.loads(STATS.read_text(encoding="utf-8")) if STATS.exists() else {}
    except json.JSONDecodeError:
        alt = {}
    anfragen = {d: v["anfragen"] for d, v in alt.items()
                if isinstance(v, dict) and isinstance(v.get("anfragen"), dict)}
    for f in (ROOT / "drafts").glob("*-anfragen.json"):
        try:
            anfragen[f.name[:10]] = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    days |= set(anfragen)
    data = {day: tag_stats(day) for day in sorted(days)}
    for day, z in anfragen.items():
        data[day]["anfragen"] = z
        limit = z.get("limit") or 50
        if z.get("anfragen", 0) >= 0.8 * limit:
            data[day]["warnung_kontingent"] = True
    data["_kontingent"] = kontingent(anfragen)
    STATS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False)[:2000])


if __name__ == "__main__":
    main()
