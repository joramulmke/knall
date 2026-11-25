"""
publish.py — Übernimmt freigegebene Entwürfe nach published/ als Markdown.

Veröffentlicht wird nur, was ALLE Stufen eindeutig bestanden hat
(ampel_final == "gruen" UND freigabe_final is True) — doppelte Absicherung
zusätzlich zum Pipeline-State.
"""
import json
import datetime
import pathlib
import re
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parent.parent

UMLAUTE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"})


def slugify(text, fallback="artikel"):
    t = unicodedata.normalize("NFKD", str(text or "").translate(UMLAUTE))
    t = t.encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:60].strip("-")
    return t or fallback


def unique_path(base):
    path = base
    n = 2
    while path.exists():
        path = base.with_name(f"{base.stem}-{n}{base.suffix}")
        n += 1
    return path


def main():
    today = datetime.date.today().isoformat()
    state_path = ROOT / "drafts" / "_pipeline_state.json"
    if not state_path.exists():
        print("kein Pipeline-State — nichts zu veroeffentlichen")
        return
    state = json.loads(state_path.read_text(encoding="utf-8"))
    (ROOT / "published").mkdir(exist_ok=True)
    count = 0
    for entry in state.get("artikel", []):
        if entry.get("stage") != "published_ready":
            continue
        draft = json.loads((ROOT / entry["draft_path"]).read_text(encoding="utf-8"))
        if draft.get("ampel_final") != "gruen" or draft.get("freigabe_final") is not True:
            print(f"::warning::{entry['draft_path']} hat keine vollstaendige Freigabe — NICHT veroeffentlicht")
            continue
        cluster = draft.get("cluster", {})
        out = unique_path(ROOT / "published" / f"{today}-{slugify(cluster.get('thema'))}.md")
        quellen = [{"titel": q.get("titel"), "url": q.get("url")} for q in draft.get("quellen_meta", [])]
        meta = {
            "datum": today,
            "thema": cluster.get("thema"),
            "ressort": cluster.get("ressort"),
            "quellen": quellen,
            "ki2_ampel": draft.get("ki2_endampel"),
            "ki3": (draft.get("ki3_validierung") or {}).get("entscheidung"),
            "ki3_nachbesserung": bool(draft.get("ki3_nachbesserung")),
            "ki_generiert": True,
        }
        # JSON-Werte sind gültiges YAML → keine kaputten Frontmatter durch ':' o. ä.
        frontmatter = "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items())
        quellen_md = "\n".join(f"- [{q['titel'] or q['url']}]({q['url']})" for q in quellen if q.get("url"))
        body = draft["text"].strip()
        out.write_text(
            f"---\n{frontmatter}\n---\n\n{body}\n\n"
            f"Quellen:\n{quellen_md}\n\n"
            f"_Automatisiert mit KI erstellt und maschinell geprüft (KI-Faktenprüfung, Rechtsprüfung, Zahlen-/Namens-/Plagiatscheck)._\n",
            encoding="utf-8",
        )
        count += 1
        print(f"veroeffentlicht: {out.name}")
    print(f"publish fertig: {count} Artikel")


if __name__ == "__main__":
    main()
