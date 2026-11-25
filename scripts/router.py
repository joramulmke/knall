"""
router.py — Orchestrator: KI1 schreiben → Regex-Gate → KI2 prüfen →
Sonderprüfungen → Ampel-Route → KI3 legal.

Kernregel: Nur KI2 entscheidet über grün. Jede Korrektur (gelb) führt
zurück vor KI2 — der korrigierte Text wird erneut komplett geprüft.
Es gibt keinen Weg zu grün, der nicht durch eine KI2-Prüfung läuft.

Stages: write_stage, evaluate_stage, legal_stage
Status über drafts/_pipeline_state.json.
"""
import json
import sys
import datetime
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DRAFTS = ROOT / "drafts"
STATE_FILE = DRAFTS / "_pipeline_state.json"
MAX_GELB_ROUNDS = 2


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


def gelb_hinweis(ki2_protokoll):
    """Sammelt alle Gelb-Vorschläge aus dem KI2-Protokoll zu einem Korrekturtext."""
    teile = []
    if ki2_protokoll.get("korrekturhinweis_gesamt"):
        teile.append(ki2_protokoll["korrekturhinweis_gesamt"])
    for satz in ki2_protokoll.get("saetze", []):
        for v in satz.get("verstoesse", []):
            if v.get("vorschlag"):
                teile.append(f"Satz {satz.get('satz_nr')}: {v['vorschlag']}")
    if ki2_protokoll.get("titel_befund") == "gelb":
        teile.append("Auch der Titel hat einen Stil-Befund: passe ihn an.")
    return " ".join(teile) if teile else "Stil-Anmerkungen der Prüfung beachten."


def evaluate_stage():
    """KI2 prüft → Route. Gelb = KI1-Korrektur, danach IMMER erneute KI2-Prüfung
    des korrigierten Texts. Grün gibt es nur durch KI2 auf dem letzten Text.
    Danach Sonderprüfungen (deterministisch): auch Grün kann dort noch rot werden.
    Endzustände: checked_green oder rejected."""
    from ki_client import llm_json, llm_call
    from special_checks import run_all
    today = datetime.date.today().isoformat()
    curation = json.loads((DRAFTS / f"{today}-curation.json").read_text(encoding="utf-8"))
    state = load_state()
    finished = []
    for entry in state["artikel"]:
        dpath = ROOT / entry["draft_path"]
        draft = json.loads(dpath.read_text(encoding="utf-8"))
        cluster = curation["cluster"][entry["cluster_idx"]]
        quellen = sources_from_cluster(cluster, today)
        ki2_prompt_file = (ROOT / "prompts" / "ki2.txt").read_text(encoding="utf-8")
        ki1_base = (ROOT / "prompts" / "ki1.txt").read_text(encoding="utf-8").replace("{{QUELLEN}}", quellen)

        rounds = 0
        ampel = None
        while True:
            prompt = ki2_prompt_file.replace("{{ARTIKEL}}", draft["text"]).replace("{{QUELLEN}}", quellen)
            ki2 = llm_json(prompt, model="LARGE")
            draft["ki2_protokoll"] = ki2
            ampel = ki2.get("ampel", "rot")
            if ampel != "gelb":
                break  # rot oder gruen — Entscheidung liegt bei KI2
            rounds += 1
            if rounds > MAX_GELB_ROUNDS:
                ampel = "rot"  # Gelb-erschlöpft = rot
                draft["gelb_erschoepft"] = True
                break
            hinweis = gelb_hinweis(ki2)
            draft.setdefault("gelb_protokoll", []).append({"runde": rounds, "hinweis": hinweis})
            repair_prompt = (
                ki1_base
                + f"\n\nDie Prüfung hat Stil-Befunde: {hinweis}\n"
                + "Korrigiere NUR diese Stellen und antworte mit dem kompletten korrigierten Artikel."
            )
            draft["text"] = llm_call(repair_prompt, model="LARGE", max_tokens=1200)
            # Loop läuft weiter: der neue Text wird im nächsten Durchlauf wieder von KI2 geprüft

        draft["ki2_pruefrunden"] = rounds + 1
        draft["ki2_endampel"] = ampel

        # Sonderprüfungen laufen IMMER — auch bei grünem KI2-Urteil
        special = run_all(draft["text"], quellen)
        draft["special_checks"] = special
        final = "rot" if (ampel == "rot" or not special.get("bestaende")) else "gruen"
        draft["ampel_final"] = final

        if final == "rot":
            (ROOT / "rejected" / f"{today}-{dpath.stem}.json").write_text(
                json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        else:
            entry["stage"] = "checked_green"
            finished.append(entry)
        dpath.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")

    state["artikel"] = finished
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
            draft["text"] = apply_replacements(draft["text"], result.get("risiken", []))
            draft["ki3_nachbesserung"] = True
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
    """KI3-Ersatzformulierungen anwenden (String-Ersetzung, v1)."""
    for r in risiken:
        stelle = r.get("stelle")
        ersatz = r.get("ersatzformulierung")
        if stelle and ersatz and stelle != "null" and stelle in text:
            text = text.replace(stelle, ersatz)
    return text


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    stages = {"write_stage": write_stage, "evaluate_stage": evaluate_stage, "legal_stage": legal_stage}
    if cmd not in stages:
        raise SystemExit(f"Unbekannter Befehl: {cmd!r}. Erlaubt: {', '.join(stages)}")
    stages[cmd]()
