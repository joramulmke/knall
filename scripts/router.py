"""
router.py — Orchestrator: KI1 schreiben → Regex-Gate → KI2 prüfen →
Sonderprüfungen → Ampel-Route → KI3 legal → published_ready.
Stages: write_stage, check_stage, route_stage, legal_stage
Status über drafts/_pipeline_state.json.
"""
import json
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
    DRAFTS.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def sources_from_cluster(cluster, today):
    """Liest die Quelldateien eines Clusters als vereinigten Quelltext."""
    src_dir = ROOT / "sources" / today
    texts = []
    for aid in cluster.get("artikel_ids", []):
        path = src_dir / f"{aid}.json"
        if path.exists():
            item = json.loads(path.read_text(encoding="utf-8"))
            texts.append(
                f"## Quelle: {item['title']}\nURL: {item['url']}\nInhalt: {item['summary']}"
            )
    return "\n\n".join(texts)


def write_stage():
    """KI1 schreibt pro Cluster; danach Regex-Gate mit bis zu 2 billigen Korrekturrunden."""
    import os
    from ki_client import llm_call
    from regex_gate import check
    today = datetime.date.today().isoformat()
    curation = json.loads((DRAFTS / f"{today}-curation.json").read_text(encoding="utf-8"))
    state = {"artikel": []}
    for idx, cluster in enumerate(curation.get("cluster", [])):
        quellen = sources_from_cluster(cluster, today)
        base_prompt = (ROOT / "prompts" / "ki1.txt").read_text(encoding="utf-8").replace("{{QUELLEN}}", quellen)
        raw = llm_call(base_prompt, model="LARGE", max_tokens=1200)
        findings = check(raw)
        rounds = 0
        while findings and rounds < 2:
            repair_prompt = (
                base_prompt
                + "\n\nDein bisheriger Text enthält Stil- und Formfehler. Korrigiere NUR diese Stellen und antworte mit dem kompletten korrigierten Artikel:\n"
                + "\n".join(f"- {f['regel']}: {f['stelle']}" for f in findings)
            )
            raw = llm_call(repair_prompt, model="LARGE", max_tokens=1200)
            findings = check(raw)
            rounds += 1
        draft = {
            "cluster_idx": idx,
            "cluster": cluster,
            "text": raw,
            "regex_rest_findings": findings,
            "korrekturrunden_regex": rounds,
        }
        dpath = DRAFTS / f"{today}-draft-{idx}.json"
        dpath.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        state["artikel"].append({"draft_path": str(dpath.relative_to(ROOT)), "cluster_idx": idx, "stage": "regex_done"})
    save_state(state)


def check_stage():
    """KI2 prüft jeden Entwurf, Ampel-JSON wird an den Entwurf angehängt."""
    from ki_client import llm_json
    today = datetime.date.today().isoformat()
    curation = json.loads((DRAFTS / f"{today}-curation.json").read_text(encoding="utf-8"))
    state = load_state()
    for entry in state["artikel"]:
        dpath = ROOT / entry["draft_path"]
        draft = json.loads(dpath.read_text(encoding="utf-8"))
        cluster = curation["cluster"][entry["cluster_idx"]]
        quellen = sources_from_cluster(cluster, today)
        prompt = (ROOT / "prompts" / "ki2.txt").read_text(encoding="utf-8")
        prompt = prompt.replace("{{ARTIKEL}}", draft["text"]).replace("{{QUELLEN}}", quellen)
        result = llm_json(prompt, model="LARGE")
        draft["ki2_protokoll"] = result
        dpath.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        entry["stage"] = "ki2_done"
    save_state(state)


def route_stage():
    """Sonderprüfungen + Ampel: rot→rejected, gelb→KI1-Korrektur (hier: Entwurf behalten + Markierung, max 2 Runden), grün→weiter.
    Hinweis: Gelb-Korrektur via KI1 erfolgt vereinfacht in v1: Text wird mit KI2-Vorschlägen
    einmal nachgeschärft (weiterer LLM-Aufruf), danach gilt der Text als endgültig geroutet."""
    from ki_client import llm_call
    from special_checks import run_all
    today = datetime.date.today().isoformat()
    curation = json.loads((DRAFTS / f"{today}-curation.json").read_text(encoding="utf-8"))
    state = load_state()
    remaining = []
    for entry in state["artikel"]:
        dpath = ROOT / entry["draft_path"]
        draft = json.loads(dpath.read_text(encoding="utf-8"))
        cluster = curation["cluster"][entry["cluster_idx"]]
        quellen = sources_from_cluster(cluster, today)
        ampel = draft.get("ki2_protokoll", {}).get("ampel", "rot")

        # Gelb: eine KI1-Nachbesserung mit den KI2-Vorschlägen
        if ampel == "gelb":
            draft["gelb_runden"] = draft.get("gelb_runden", 0) + 1
            hinweis = draft.get("ki2_protokoll", {}).get("korrekturhinweis_gesamt", "")
            if draft["gelb_runden"] <= 2 and hinweis:
                base_prompt = (ROOT / "prompts" / "ki1.txt").read_text(encoding="utf-8").replace("{{QUELLEN}}", quellen)
                repair_prompt = (
                    base_prompt
                    + f"\n\nDein bisheriger Text hat Stil-Befunde der Prüfung: {hinweis}\n"
                    + "Korrigiere NUR diese Stellen und antworte mit dem kompletten korrigierten Artikel."
                )
                draft["text"] = llm_call(repair_prompt, model="LARGE", max_tokens=1200)
                draft["ki2_protokoll"]["ampel"] = "gruen"  # v1: Nachbesserung gilt als angenommen
        # Sonderprüfungen laufen IMMER, auch bei grün
        special = run_all(draft["text"], quellen)
        draft["special_checks"] = special
        if not special.get("bestaende"):
            ampel = "rot"
        draft["ampel_final"] = ampel if ampel != "gelb" else "gruen"

        if draft["ampel_final"] == "rot":
            (ROOT / "rejected" / f"{today}-{dpath.stem}.json").write_text(
                json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        else:
            remaining.append(entry)
        dpath.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    state["artikel"] = remaining
    save_state(state)


def legal_stage():
    """KI3 Rechtsabnahme: freigabe → published_ready, sonst → rejected."""
    from ki_client import llm_json
    today = datetime.date.today().isoformat()
    curation = json.loads((DRAFTS / f"{today}-curation.json").read_text(encoding="utf-8"))
    state = load_state()
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
        elif result.get("entscheidung") == "nachbesserung":
            # v1: Ersatzformulierungen werden angewendet, dann als freigegeben markiert
            ersatz = [r.get("ersatzformulierung") for r in result.get("risiken", []) if r.get("ersatzformulierung")]
            draft["ki3_ersatz"] = ersatz
            draft["text"] = apply_replacements(draft["text"], result.get("risiken", []))
            entry["stage"] = "published_ready"
            remaining.append(entry)
        else:
            (ROOT / "rejected" / f"{today}-{dpath.stem}.json").write_text(
                json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        dpath.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    state["artikel"] = remaining
    save_state(state)


def apply_replacements(text, risiken):
    """KI3-Ersatzformulierungen anwenden. v1: einfach, mann könnne das künftig präziser machen."""
    for r in risiken:
        stelle = r.get("stelle")
        ersatz = r.get("ersatzformulierung")
        if stelle and ersatz and stelle != "null" and stelle in text:
            text = text.replace(stelle, ersatz)
    return text


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    stages = {"write_stage": write_stage, "check_stage": check_stage, "route_stage": route_stage, "legal_stage": legal_stage}
    if cmd not in stages:
        raise SystemExit(f"Unbekannter Befehl: {cmd!r}. Erlaubt: {', '.join(stages)}")
    stages[cmd]()
