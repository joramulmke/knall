"""
router.py — Orchestrator: KI1 schreiben → Regex-Gate → KI2 prüfen →
Sonderprüfungen → Ampel-Route → KI3 legal.

Kernregeln (keine menschliche Freigabe → fail-closed):
- Nur KI2 vergibt grün, und nur wenn safety.ki2_verdict() das Protokoll
  als vollständig und eindeutig "gruen" bestätigt. Alles andere = rot.
- Jede Gelb-Korrektur führt zurück vor KI2 (max. 2 Runden, dann rot).
- Sonderprüfungen laufen IMMER, auch nach grün.
- KI3: nur eindeutige "freigabe" wird veröffentlicht. "stopp" oder
  unklares Urteil → rejected, endgültig, ohne neue Schleife.
  "nachbesserung" → Ersatzformulierungen müssen ALLE greifen; danach
  EINE erneute Prüfung (KI2 + Sonderprüfungen + KI3). Nur wenn alle drei
  bestehen → veröffentlichen, sonst rejected. Kein zweiter Durchlauf.
- Ein Fehler in einem Cluster stoppt nicht die anderen.

Stages: write_stage, evaluate_stage, legal_stage
Status über drafts/_pipeline_state.json.
"""
import json
import re
import sys
import datetime
import pathlib
import traceback

ROOT = pathlib.Path(__file__).resolve().parent.parent
DRAFTS = ROOT / "drafts"
REJECTED = ROOT / "rejected"
STATE_FILE = DRAFTS / "_pipeline_state.json"
MAX_GELB_ROUNDS = 2
MAX_REGEX_ROUNDS = 2


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"artikel": []}


def save_state(state):
    DRAFTS.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def redact(draft):
    """Öffentlicher Ablehnungs-Befund OHNE Artikeltext, ohne beanstandete
    Zitate und ohne Namen — abgelehnte Texte dürfen nicht verbreitet werden.
    Der volle Entwurf bleibt nur im (nicht committeten) drafts/ des Laufs."""
    def ki2_kurz(p):
        if not isinstance(p, dict):
            return None
        saetze = []
        for s in p.get("saetze") or []:
            if isinstance(s, dict):
                saetze.append({
                    "satz_nr": s.get("satz_nr"),
                    "befund": s.get("befund"),
                    "pruefposten": [v.get("pruefposten") for v in (s.get("verstoesse") or []) if isinstance(v, dict)],
                })
        return {"ampel": p.get("ampel"), "titel_befund": p.get("titel_befund"), "saetze": saetze}

    def ki3_kurz(r):
        if not isinstance(r, dict):
            return None
        return {"entscheidung": r.get("entscheidung"),
                "risiken": [{"frage": x.get("frage"), "urteil": x.get("urteil")}
                            for x in (r.get("risiken") or []) if isinstance(x, dict)]}

    def special_kurz(s):
        if not isinstance(s, dict):
            return None
        return {"bestaende": s.get("bestaende"),
                "zahlen_fehler": len(s.get("zahlen_fehler") or []),
                "entities_fehler": len(s.get("entities_fehler") or []),
                "plagiat": bool(s.get("plagiat")),
                "injection": bool(s.get("injection")),
                "leer": s.get("leer")}

    def grund_kurz(g):
        g = str(g)
        if g.startswith("systemfehler"):
            return "systemfehler"
        g = re.sub(r"'[^']{0,2000}'", "'…'", g)  # keine LLM-Werte/Zitate
        return g[:200]

    def validierung(v):
        if isinstance(v, list):
            return [validierung(x) for x in v]
        if isinstance(v, dict):
            return {k: ([grund_kurz(g) for g in val] if k == "gruende" and isinstance(val, list) else val)
                    for k, val in v.items()}
        return v

    cluster = draft.get("cluster") or {}
    return {
        "thema": cluster.get("thema"),
        "ressort": cluster.get("ressort"),
        "quellen": [{"titel": q.get("titel"), "url": q.get("url")} for q in draft.get("quellen_meta", [])],
        "quellen_verworfen": len(draft.get("quellen_verworfen") or []),
        "ablehnungsgruende": [grund_kurz(g) for g in draft.get("ablehnungsgruende", [])],
        "abbruch": bool(draft.get("abbruch")),
        "gelb_erschoepft": bool(draft.get("gelb_erschoepft")),
        "ki2_endampel": draft.get("ki2_endampel"),
        "ki2_validierung": validierung(draft.get("ki2_validierung")),
        "ki2": ki2_kurz(draft.get("ki2_protokoll")),
        "special_checks": special_kurz(draft.get("special_checks")),
        "ki3": ki3_kurz(draft.get("ki3_entscheidung")),
        "ki3_validierung": validierung(draft.get("ki3_validierung")),
        "ki3_nachbesserung": bool(draft.get("ki3_nachbesserung")),
    }


def reject(today, name, draft, grund):
    draft["ampel_final"] = "rot"
    draft.setdefault("ablehnungsgruende", []).append(grund)
    fname = name if name.startswith(today) else f"{today}-{name}"
    write_json(REJECTED / f"{fname}.json", redact(draft))
    print(f"  → rejected: {grund}")


def sources_from_cluster(cluster, today):
    """Liest die Quelldateien eines Clusters als bereinigten, klar begrenzten
    Quelltext. Quellen mit Prompt-Injection-Mustern werden verworfen.
    Rückgabe: (quelltext, verworfene_ids, quellen_meta)."""
    from safety import clean_text, injection_hits
    src_dir = ROOT / "sources" / today
    texts, dropped, meta = [], [], []
    for n, aid in enumerate(cluster.get("artikel_ids", []), start=1):
        path = src_dir / f"{aid}.json"
        if not path.exists():
            continue
        item = json.loads(path.read_text(encoding="utf-8"))
        title = clean_text(item.get("title", ""))
        body = clean_text(item.get("summary", ""))
        if injection_hits(title + "\n" + body):
            print(f"  Quelle {aid} verworfen: Anweisungs-Text erkannt (Prompt-Injection?)")
            dropped.append(aid)
            continue
        texts.append(
            f"<quelle nr=\"{n}\">\nTitel: {title}\nQuelle: {clean_text(item.get('source_key', ''))}\n"
            f"URL: {clean_text(item.get('url', ''))}\nInhalt: {body}\n</quelle>"
        )
        meta.append({"id": aid, "titel": title, "url": item.get("url", ""), "quelle": item.get("source_key", "")})
    return "\n\n".join(texts), dropped, meta


def fill(template, **kw):
    out = template
    for k, v in kw.items():
        out = out.replace("{{" + k + "}}", v)
    return out


def write_stage():
    """KI1 schreibt pro Cluster; danach Regex-Gate mit bis zu 2 billigen Korrekturrunden."""
    from ki_client import llm_call, QuotaExhausted
    from regex_gate import check
    today = datetime.date.today().isoformat()
    curation = json.loads((DRAFTS / f"{today}-curation.json").read_text(encoding="utf-8"))
    state = {"artikel": []}
    save_state(state)
    ki1_tpl = (ROOT / "prompts" / "ki1.txt").read_text(encoding="utf-8")
    for idx, cluster in enumerate(curation.get("cluster", [])):
        name = f"draft-{idx}"
        draft = {"cluster_idx": idx, "cluster": cluster}
        try:
            quellen, dropped, meta = sources_from_cluster(cluster, today)
            draft["quellen_verworfen"] = dropped
            draft["quellen_meta"] = meta
            if not quellen:
                reject(today, name, draft, "keine verwertbaren Quellen")
                continue
            base_prompt = fill(ki1_tpl, QUELLEN=quellen)
            raw = llm_call(base_prompt, model="LARGE").strip()
            findings = check(raw, quellen)
            rounds = 0
            while findings and rounds < MAX_REGEX_ROUNDS:
                repair_prompt = (
                    base_prompt
                    + "\n\nDein bisheriger Text:\n<artikel>\n" + raw + "\n</artikel>\n\n"
                    + "Er enthält Stil- und Formfehler. Korrigiere NUR diese Stellen und antworte mit dem kompletten korrigierten Artikel:\n"
                    + "\n".join(f"- {f['regel']}: {f['stelle']}" for f in findings)
                )
                raw = llm_call(repair_prompt, model="LARGE").strip()
                findings = check(raw, quellen)
                rounds += 1
            draft.update({"text": raw, "regex_rest_findings": findings, "korrekturrunden_regex": rounds})
            dpath = DRAFTS / f"{today}-{name}.json"
            write_json(dpath, draft)
            state["artikel"].append({"draft_path": str(dpath.relative_to(ROOT)), "cluster_idx": idx, "stage": "regex_done"})
            save_state(state)
        except QuotaExhausted as exc:
            print(f"::error::Free-Tier-Tageslimit erreicht — restliche Cluster entfallen: {exc}")
            break
        except Exception as exc:
            traceback.print_exc()
            print(f"::error::Cluster {idx} in write_stage fehlgeschlagen: {exc}")
            draft["abbruch"] = f"write_stage: {exc}"
            reject(today, name, draft, f"technischer Fehler: {exc}")
    save_state(state)


def gelb_hinweis(ki2_protokoll):
    """Sammelt alle Gelb-Vorschläge aus dem KI2-Protokoll zu einem Korrekturtext."""
    teile = []
    if ki2_protokoll.get("korrekturhinweis_gesamt"):
        teile.append(str(ki2_protokoll["korrekturhinweis_gesamt"]))
    for satz in ki2_protokoll.get("saetze", []) or []:
        if not isinstance(satz, dict):
            continue
        for v in satz.get("verstoesse", []) or []:
            if isinstance(v, dict) and v.get("vorschlag"):
                teile.append(f"Satz {satz.get('satz_nr')}: {v['vorschlag']}")
    if str(ki2_protokoll.get("titel_befund", "")).lower() == "gelb":
        teile.append("Auch der Titel hat einen Stil-Befund: passe ihn an.")
    return " ".join(teile) if teile else "Stil-Anmerkungen der Prüfung beachten."


def ki2_check(text, quellen):
    """Eine KI2-Prüfung + strenge Validierung. Rückgabe (ampel, protokoll, gründe)."""
    from ki_client import llm_json
    from safety import ki2_verdict, injection_hits
    hits = injection_hits(text)
    if hits:
        return "rot", {"systemfehler": "Anweisungs-Text im Artikel"}, [f"Injection-Muster im Artikel: {hits}"]
    tpl = (ROOT / "prompts" / "ki2.txt").read_text(encoding="utf-8")
    protokoll = llm_json(fill(tpl, ARTIKEL=text, QUELLEN=quellen), model="LARGE", temperature=0.0)
    ampel, gruende = ki2_verdict(protokoll, text)
    return ampel, protokoll, gruende


def evaluate_stage():
    """KI2 prüft → Route. Gelb = KI1-Korrektur, danach IMMER erneute KI2-Prüfung.
    Danach Sonderprüfungen (deterministisch): auch Grün kann dort noch rot werden.
    Endzustände: checked_green oder rejected."""
    from ki_client import llm_call, QuotaExhausted
    from special_checks import run_all
    today = datetime.date.today().isoformat()
    curation = json.loads((DRAFTS / f"{today}-curation.json").read_text(encoding="utf-8"))
    state = load_state()
    ki1_tpl = (ROOT / "prompts" / "ki1.txt").read_text(encoding="utf-8")
    finished = []
    quota_hit = False
    for entry in state["artikel"]:
        dpath = ROOT / entry["draft_path"]
        draft = json.loads(dpath.read_text(encoding="utf-8"))
        if quota_hit:
            reject(today, dpath.stem, draft, "nicht geprueft: Free-Tier-Tageslimit")
            continue
        try:
            cluster = curation["cluster"][entry["cluster_idx"]]
            quellen, _, _ = sources_from_cluster(cluster, today)
            ki1_base = fill(ki1_tpl, QUELLEN=quellen)

            rounds = 0
            while True:
                ampel, protokoll, gruende = ki2_check(draft["text"], quellen)
                draft["ki2_protokoll"] = protokoll
                draft.setdefault("ki2_validierung", []).append({"ampel": ampel, "gruende": gruende})
                if ampel != "gelb":
                    break
                rounds += 1
                if rounds > MAX_GELB_ROUNDS:
                    ampel = "rot"
                    draft["gelb_erschoepft"] = True
                    break
                hinweis = gelb_hinweis(protokoll)
                draft.setdefault("gelb_protokoll", []).append({"runde": rounds, "hinweis": hinweis})
                repair_prompt = (
                    ki1_base
                    + "\n\nDein bisheriger Text:\n<artikel>\n" + draft["text"] + "\n</artikel>\n\n"
                    + f"Die Prüfung hat Stil-Befunde: {hinweis}\n"
                    + "Korrigiere NUR diese Stellen und antworte mit dem kompletten korrigierten Artikel."
                )
                draft["text"] = llm_call(repair_prompt, model="LARGE").strip()

            draft["ki2_pruefrunden"] = rounds + 1
            draft["ki2_endampel"] = ampel

            special = run_all(draft["text"], quellen)
            draft["special_checks"] = special
            final = "gruen" if (ampel == "gruen" and special.get("bestaende") is True) else "rot"
            draft["ampel_final"] = final

            if final != "gruen":
                grund = "KI2 rot/gelb-erschoepft" if ampel != "gruen" else "Sonderpruefung nicht bestanden"
                reject(today, dpath.stem, draft, grund)
            else:
                entry["stage"] = "checked_green"
                finished.append(entry)
        except QuotaExhausted as exc:
            print(f"::error::Free-Tier-Tageslimit erreicht: {exc}")
            quota_hit = True
            reject(today, dpath.stem, draft, "nicht geprueft: Free-Tier-Tageslimit")
        except Exception as exc:
            traceback.print_exc()
            print(f"::error::{dpath.stem} in evaluate_stage fehlgeschlagen: {exc}")
            draft["abbruch"] = f"evaluate_stage: {exc}"
            reject(today, dpath.stem, draft, f"technischer Fehler: {exc}")
        write_json(dpath, draft)

    state["artikel"] = finished
    save_state(state)


def apply_replacements(text, risiken):
    """KI3-Ersatzformulierungen anwenden. Rückgabe (neuer_text, fehlgeschlagen).
    Jede beanstandete Stelle (grenzwertig/kritisch mit Ersatz) MUSS wörtlich
    gefunden werden — sonst gilt die Nachbesserung als gescheitert."""
    fehlgeschlagen = []
    angewendet = 0
    for r in risiken or []:
        if not isinstance(r, dict):
            continue
        urteil = str(r.get("urteil", "")).lower()
        stelle = str(r.get("stelle") or "").strip()
        ersatz = str(r.get("ersatzformulierung") or "").strip()
        if urteil.startswith("unauff") or not stelle or stelle.lower() == "null":
            continue
        if not ersatz:
            if urteil == "kritisch":
                fehlgeschlagen.append(stelle)
            continue
        if stelle not in text:
            fehlgeschlagen.append(stelle)
            continue
        text = text.replace(stelle, ersatz)
        angewendet += 1
    if angewendet == 0 and not fehlgeschlagen:
        fehlgeschlagen.append("nachbesserung ohne anwendbare Ersatzformulierung")
    return text, fehlgeschlagen


def ki3_check(text, quellen):
    from ki_client import llm_json
    from safety import ki3_verdict
    tpl = (ROOT / "prompts" / "ki3.txt").read_text(encoding="utf-8")
    result = llm_json(fill(tpl, ARTIKEL=text, QUELLEN=quellen), model="LARGE", temperature=0.0)
    entscheidung, gruende = ki3_verdict(result)
    return entscheidung, result, gruende


def legal_stage():
    """KI3 Rechtsabnahme. freigabe → published_ready. stopp/unklar → rejected
    (endgültig). nachbesserung → EINE Nachprüfung (KI2 + Sonderprüfung + KI3),
    nur eindeutiges Bestehen aller drei → published_ready."""
    from ki_client import QuotaExhausted
    from special_checks import run_all
    today = datetime.date.today().isoformat()
    curation = json.loads((DRAFTS / f"{today}-curation.json").read_text(encoding="utf-8"))
    state = load_state()
    remaining = []
    quota_hit = False
    for entry in state["artikel"]:
        dpath = ROOT / entry["draft_path"]
        draft = json.loads(dpath.read_text(encoding="utf-8"))
        if quota_hit:
            reject(today, dpath.stem, draft, "nicht rechtlich geprueft: Free-Tier-Tageslimit")
            continue
        try:
            cluster = curation["cluster"][entry["cluster_idx"]]
            quellen, _, _ = sources_from_cluster(cluster, today)
            entscheidung, result, gruende = ki3_check(draft["text"], quellen)
            draft["ki3_entscheidung"] = result
            draft["ki3_validierung"] = {"entscheidung": entscheidung, "gruende": gruende}

            if entscheidung == "freigabe":
                draft["freigabe_final"] = True
            elif entscheidung == "nachbesserung":
                neu, fehl = apply_replacements(draft["text"], result.get("risiken", []))
                if fehl:
                    # Stellen selbst nicht loggen (Zitat aus abgelehntem Text)
                    reject(today, dpath.stem, draft, f"KI3-Nachbesserung nicht anwendbar ({len(fehl)} Stelle(n) nicht gefunden)")
                    write_json(dpath, draft)
                    continue
                draft["text_vor_ki3"] = draft["text"]
                draft["text"] = neu
                draft["ki3_nachbesserung"] = True
                # EINE Nachprüfung, keine Schleife
                ampel2, prot2, gr2 = ki2_check(neu, quellen)
                draft["ki2_nachpruefung"] = {"ampel": ampel2, "gruende": gr2, "protokoll": prot2}
                special2 = run_all(neu, quellen)
                draft["special_checks_nachpruefung"] = special2
                if ampel2 != "gruen" or special2.get("bestaende") is not True:
                    reject(today, dpath.stem, draft, "Nachpruefung nach KI3-Nachbesserung nicht bestanden (KI2/Sonderpruefung)")
                    write_json(dpath, draft)
                    continue
                ent2, res2, gr3 = ki3_check(neu, quellen)
                draft["ki3_nachpruefung"] = {"entscheidung": ent2, "gruende": gr3, "urteil": res2}
                if ent2 != "freigabe":
                    reject(today, dpath.stem, draft, f"KI3-Nachpruefung: {ent2}")
                    write_json(dpath, draft)
                    continue
                draft["freigabe_final"] = True
            else:
                reject(today, dpath.stem, draft, f"KI3: stopp ({'; '.join(gruende)})")
                write_json(dpath, draft)
                continue

            entry["stage"] = "published_ready"
            remaining.append(entry)
        except QuotaExhausted as exc:
            print(f"::error::Free-Tier-Tageslimit erreicht: {exc}")
            quota_hit = True
            reject(today, dpath.stem, draft, "nicht rechtlich geprueft: Free-Tier-Tageslimit")
        except Exception as exc:
            traceback.print_exc()
            print(f"::error::{dpath.stem} in legal_stage fehlgeschlagen: {exc}")
            draft["abbruch"] = f"legal_stage: {exc}"
            reject(today, dpath.stem, draft, f"technischer Fehler: {exc}")
        write_json(dpath, draft)
    state["artikel"] = remaining
    save_state(state)


if __name__ == "__main__":
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    stages = {"write_stage": write_stage, "evaluate_stage": evaluate_stage, "legal_stage": legal_stage}
    if cmd not in stages:
        raise SystemExit(f"Unbekannter Befehl: {cmd!r}. Erlaubt: {', '.join(stages)}")
    stages[cmd]()
