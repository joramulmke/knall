"""
publish.py — Übernimmt freigegebene Entwürfe nach published/ als Markdown.
"""
import json
import datetime
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    today = datetime.date.today().isoformat()
    state_path = ROOT / "drafts" / "_pipeline_state.json"
    if not state_path.exists():
        return
    state = json.loads(state_path.read_text(encoding="utf-8"))
    for entry in state.get("artikel", []):
        if entry.get("stage") != "published_ready":
            continue
        draft = json.loads((ROOT / entry["draft_path"]).read_text(encoding="utf-8"))
        slug = re.sub(r"[^a-z0-9]+", "-", draft["cluster"].get("thema", "artikel").lower()).strip("-")
        out = ROOT / "published" / f"{today}-{slug}.md"
        meta = {
            "datum": today,
            "thema": draft["cluster"].get("thema"),
            "ressort": draft["cluster"].get("ressort"),
            "quellen": [a for a in draft["cluster"].get("artikel_ids", [])],
            "ki2_ampel": draft.get("ki2_protokoll", {}).get("ampel"),
            "ki3": draft.get("ki3_entscheidung", {}).get("entscheidung"),
        }
        frontmatter = "\n".join(f"{k}: {v}" for k, v in meta.items())
        out.write_text(f"---\n{frontmatter}\n---\n\n{draft['text']}\n", encoding="utf-8")
    print("publish fertig")


if __name__ == "__main__":
    main()
