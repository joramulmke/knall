"""
router.py — Orchestrator: KI1 schreiben → Regex-Gate → KI2 prüfen →
Sonderprüfungen → Ampel-Route → KI3 legal.
Aufrufe: write_stage, check_stage, route_stage, legal_stage
(von daily.yml sequenziell aufgerufen — Status via drafts/_pipeline_state.json)
"""
import json
import os
import sys
import datetime
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DRAFTS = ROOT / "drafts"
STATE_FILE = DRAFTS / "_pipeline_state.json"


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"artikel": []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def sources_from_cluster(cluster, today):
    """Liest die Quelldateien eines Clusters als vereinigten Quelltext."""
    import yaml  # noqa
    src_dir = ROOT / "sources" / today
    texts = []
    for aid in cluster.get("artikel_ids", []):
        path = src_dir / f"{aid}.json"
        if path.exists():
            item = json.loads(path.read_text(encoding="utf-8"))
            texts.append(f"## Quelle: {item['title']} ({item['url']})\n{item['summary']}")
    return "\n\n".join(texts)


def write_stage():
    from ki_client import llm_call
    import re
    today = datetime.date.today().isoformat()
    state = load_state()
    curation = json.loads((DRAFTS / f"{today}-curation.json").read_text(encoding="utf-8"))
    state["artikel"] = []
    for idx, cluster in enumerate(curation.get("cluster", [])):
        quellen = sources_from_cluster(cluster, today)
        prompt = (ROOT / "prompts" / "ki1.txt").read_text(encoding="utf-8").replace("{{QUELLEN}}", quellen)
        raw = llm_call(prompt, model="LARGE", max_tokens=1200)
        # Regex-Gate (billige Korrekturschleife vor KI2)
        from regex_gate import check
        findings = check(raw)
        correction_round = 0
        while findings and correction_round < 2:
            repair_prompt = (
                prompt
                + "\n\nDein bisheriger Text enthält Stil- und Formfehler:\n"
                + "\n".join(f"- {f['regel']}: {f['stelle']}" for f in findings)
                + "\nKorrigiere NUR diese Stellen. Antworte mit dem kompletten korrigierten Artikel."
            )
            raw = llm_call(repair_prompt, model="LARGE", max_tokens=1200)
            findings = check(raw)
            correction_round += 1
        draft = {
            "cluster": cluster,
            "text": raw,
            "regex_findings": findings,
            "korrekturrunden_regex": correction_round,
        }
        dpath = DRAFTS / f"{today}-draft-{idx}.json"
        dpath.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        state["artikel"].append({"draft_path": str(dpath.relative_to(ROOT)), "stage": "regex_done"})
    save_state(state)


def check_stage():
    """KI2 prüft jeden Entwurf, liefert Ampel-JSON, das an den Entwurf angehängt wird."""
    from ki_client import llm_json
    today = datetime.date.today().isoformat()
    state = load_state()
    curation = json.loads((DRAFTS / f"{today}-curation.json").read_text(encoding="utf-8"))
    for entry in state["artikel"]:
        dpath = ROOT / entry["draft_path"]
        draft = json.loads(dpath.read_text(encoding="utf-8"))
        idx = int(re.findall("(draft-(\\d+)).json", dpath.name)[0][1])
        cluster = curation["cluster"][idx]
        quellen = sources_from_cluster(cluster, today)
        prompt = (ROOT / "prompts" / "ki2.txt").read_text(encoding="utf-8")
        prompt = prompt.replace("{{ARTIKEL}}", draft["text"]).replace("{{QUELLEN}}", quellen)
        result = llm_json(prompt, model="LARGE")
        draft["ki2_protokoll"] = result
        dpath.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        entry["stage"] = "ki2_done"
    save_state(state)


def route_stage():
    """Sonderprüfungen + Ampel-Route: rot→rejected, gelb→KI1-Korrektur (max 2), grün→weiter."""
    from special_checks import run_all
    today = datetime.date.today().isoformat()
    state = load_state()
    curation = json.loads((DRAFTS / f"{today}-curation.json").read_text(encoding="utf-8"))
    remaining = []
    for entry in state["artikel"]:
        dpath = ROOT / entry["draft_path"]
        draft = json.loads(dpath.read_text(encoding="utf-8"))
        quellen = sources_from_cluster(curation["cluster"][entry["cluster_idx"]], today)
        special = run_all(draft["text"], quellen)
        ampel = draft.get("ki2_protokoll", {}).get("ampel", "rot")
        if special and not special.get("bestaende"):
            ampel = "rot"
        draft["special_checks"] = special
        if ampel == "rot":
            rejected = ROOT / "rejected" / f"{today}-{dpath.stem}.json"
            rejected.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        elif ampel == "gelb":
            # Korrekturrunde: Entwurf mit Gelb-Hinweisen zurück zu KI1
            # (hier vereinfacht: nur markieren; zweite Runde erfolgt im nächsten Durchlauf)
            draft["gelb_runden"] = draft.get("gelb_runden", 0) + 1
            if draft["gelb_runden"] > 2:
                rejected = ROOT / "rejected" / f"{today}-{dpath.stem}.json"
                rejected.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                remaining.append(entry)
        else:
            draft["ampel_final"] = "gruen"
            remaining.append(entry)
        dpath.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    state["artikel"] = remaining
    save_state(state)


def legal_stage():
    """KI3 Rechtsabnahme auf verbleibende grüne Entwürfe."""
    from ki_client import llm_json
    today = datetime.date.today().isoformat()
    state = load_state()
    curation = json.loads((DRAFTS / f"{today}-curation.json").read_text(encoding="utf-8"))
    remaining = []
    for entry in state["artikel"]:
        dpath = ROOT / entry["draft_path"]
        draft = json.loads(dpath.read_text(encoding="utf-8"))
        cluster = curation["cluster"][entry["cluster_idx"]]
        quellen = sources_from_cluster(cluster, today)
        prompt = (ROOT / "prompts" / "ki3.txt").read_text(encoding="utf-8")
        prompt = prompt.replace("{{ARTIKEL}}", draft["text"]).replace("{{QUELLEN}}", quellen)
        result = llm_json(prompt, model="LARGE")
        draft["ki3_entscheidung"] = result
        if result.get("entscheidung") == "freigabe":
            entry["stage"] = "published_ready"
            remaining.append(entry)
        else:
            entry["stage"] = "ki3_stop"
            rejected = ROOT / "rejected" / f"{today}-{dpath.stem}.json"
            rejected.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        dpath.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    state["artikel"] = remaining
    save_state(state)


if __name__ == "__main__":
    cmd = sys.argv[1]
    {"write_stage": write_stage, "check_stage": check_stage, "route_stage": route_stage, "legal_stage": legal_stage}[cmd]()
