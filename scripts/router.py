"""
router.py — Orchestrator: KI1 schreiben → Regex-Gate → KI2 prüfen →
Sonderprüfungen → Ampel-Route → KI3 legal.

Kernregeln (keine menschliche Freigabe → fail-closed):
- Nur KI2 vergibt grün, und nur wenn safety.ki2_verdict() das Protokoll
  als vollständig und eindeutig "gruen" bestätigt. Alles andere = rot.
- Jede Gelb-Korrektur führt zurück vor KI2 (max. 2 Runden, dann rot).
- Sonderprüfungen laufen IMMER, auch nach grün.
- Regex-Gate läuft nach JEDER Textänderung (KI1-Entwurf, Gelb-Korrektur,
  KI3-Ersatzformulierung) — nicht nur nach dem ersten Entwurf.
- Quellen sind ausschließlich Originalartikel (Volltext + Original-URL aus
  fulltext.py); reine Teaser werden nie als Quelle verwendet.
- KI3: nur eindeutige "freigabe" wird veröffentlicht. "stopp" oder
  unklares Urteil → rejected, endgültig, ohne neue Schleife.
  "nachbesserung" → Ersatzformulierungen müssen ALLE greifen; danach
  EINE erneute Prüfung (KI2 + Sonderprüfungen + KI3). Nur wenn alle drei
  bestehen → veröffentlichen, sonst rejected. Kein zweiter Durchlauf.

Fortsetzbar (Resume): Jeder Entwurf drafts/<tag>-draft-<n>.json trägt
seinen Stand im Feld "stage":
  geschrieben → gruen → freigegeben → veroeffentlicht   (Erfolgsweg)
  abgelehnt                                              (endgültig)
  fehler (+ "fehler_in": write|evaluate|legal)            (wird wiederholt)
Ein erneuter Lauf (drafts/ + sources/ kommen aus dem Actions-Cache)
überspringt Erledigtes und wiederholt nur Fehler/Offenes — zuerst die
offenen Entwürfe von GESTERN, dann die von heute (common.work_days()).
Inhaltliche Ablehnungen werden NIE wiederholt.
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
# Privates Fehler-Archiv: abgelehnte Artikel VOLLSTÄNDIG (Text, alle Fassungen,
# alle Prüfprotokolle, Quelltexte) — nur im verschlüsselten Actions-Cache,
# nie im Repo. Zum späteren Auswerten (scripts/archiv_auswerten.py).
ARCHIV = ROOT / "archiv" / "abgelehnt"
MAX_GELB_ROUNDS = 2
MAX_REGEX_ROUNDS = 2


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def draft_path(today, idx):
    return DRAFTS / f"{today}-draft-{idx}.json"


def load_drafts():
    """Entwürfe von gestern und heute, gestern zuerst. Rückgabe (tag, pfad, entwurf)."""
    from common import work_days
    out = []
    for day in work_days():
        for p in sorted(DRAFTS.glob(f"{day}-draft-*.json"), key=lambda x: int(x.stem.rsplit("-", 1)[1])):
            out.append((day, p, json.loads(p.read_text(encoding="utf-8"))))
    return out


def needs(draft, stage, fehler_in):
    """Soll diese Stufe den Entwurf (erneut) bearbeiten?"""
    st = draft.get("stage")
    return st == stage or (st == "fehler" and draft.get("fehler_in") == fehler_in)


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
        "quellen": [{"titel": q.get("titel"), "url": q.get("url"), "medium": q.get("medium")}
                    for q in draft.get("quellen_meta", [])],
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


def reject_path(today, name):
    fname = name if name.startswith(today) else f"{today}-{name}"
    return REJECTED / f"{fname}.json"


def verlauf(draft, stufe, **daten):
    """Jede Textfassung und jedes Prüfprotokoll mitschreiben (nur privat:
    redact() übernimmt das Feld nicht)."""
    import datetime as _dt
    eintrag = {"stufe": stufe, "zeit": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")}
    eintrag.update(daten)
    draft.setdefault("verlauf", []).append(eintrag)


def archivieren(today, name, draft):
    """Abgelehnten Entwurf vollständig ins private Archiv legen — inkl. der
    Quelltexte, damit sich Fehler später nachvollziehen lassen."""
    try:
        rec = dict(draft)
        try:
            rec["quellen_text"], _, _ = sources_from_cluster(draft.get("cluster") or {}, draft.get("tag") or today)
        except Exception as exc:  # Quellen fehlen (z. B. schon aufgeräumt)
            rec["quellen_text"] = f"(nicht verfuegbar: {exc})"
        rec["archiviert_am"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        fname = name if name.startswith(today) else f"{today}-{name}"
        write_json(ARCHIV / f"{fname}.json", rec)
    except Exception as exc:  # Archiv darf die Pipeline nie aufhalten
        print(f"::warning::Archivieren von {name} fehlgeschlagen: {exc}")


def reject(today, name, draft, grund):
    """Inhaltliche Ablehnung — endgültig. Öffentlich nur der Befund ohne Text,
    privat (Archiv) der vollständige Entwurf."""
    draft["stage"] = "abgelehnt"
    draft["ampel_final"] = "rot"
    draft.setdefault("ablehnungsgruende", []).append(grund)
    write_json(reject_path(today, name), redact(draft))
    archivieren(today, name, draft)
    print(f"  → rejected: {grund}")


def fail(today, name, draft, wo, exc):
    """Technischer Fehler/Tageslimit — wird beim nächsten Lauf wiederholt.
    Öffentlicher Befund wird angelegt und bei späterem Erfolg wieder entfernt."""
    draft["stage"] = "fehler"
    draft["fehler_in"] = wo
    draft["abbruch"] = f"{wo}: {exc}"
    rec = redact(dict(draft, ablehnungsgruende=[f"technischer Fehler in {wo} (wird wiederholt): {exc}"]))
    write_json(reject_path(today, name), rec)
    print(f"  → Fehler in {wo}, wird beim naechsten Lauf wiederholt: {exc}")


def clear_fail(today, name, draft):
    draft.pop("fehler_in", None)
    draft.pop("abbruch", None)
    rp = reject_path(today, name)
    if rp.exists():
        rp.unlink()


def sources_from_cluster(cluster, today):
    """Liest die Quelldateien eines Clusters als bereinigten, klar begrenzten
    Quelltext. Nur Originalartikel (Volltext + Original-URL); Teaser ohne
    Original und Quellen mit Prompt-Injection-Mustern werden verworfen.
    Rückgabe: (quelltext, verworfene_ids, quellen_meta)."""
    from safety import clean_text, injection_hits
    src_dir = ROOT / "sources" / today
    texts, dropped, meta = [], [], []
    for n, aid in enumerate(cluster.get("artikel_ids", []), start=1):
        path = src_dir / f"{aid}.json"
        if not path.exists():
            continue
        item = json.loads(path.read_text(encoding="utf-8"))
        url = item.get("url_original") or ""
        if not item.get("volltext") or not url:
            print(f"  Quelle {aid} verworfen: kein Originalartikel (nur Teaser)")
            dropped.append(aid)
            continue
        title = clean_text(item.get("title", ""))
        body = clean_text(item.get("volltext", ""))
        medium = clean_text(item.get("medium") or item.get("source_key", ""))
        if injection_hits(title + "\n" + body):
            print(f"  Quelle {aid} verworfen: Anweisungs-Text erkannt (Prompt-Injection?)")
            dropped.append(aid)
            continue
        texts.append(
            f"<quelle nr=\"{n}\">\nTitel: {title}\nMedium: {medium}\n"
            f"URL: {clean_text(url)}\nInhalt: {body}\n</quelle>"
        )
        meta.append({"id": aid, "titel": title, "url": url, "medium": medium,
                     "quelle": item.get("source_key", "")})
    return "\n\n".join(texts), dropped, meta


def fill(template, **kw):
    out = template
    for k, v in kw.items():
        out = out.replace("{{" + k + "}}", v)
    return out


def regex_runden(text, base_prompt, quellen, draft=None, stufe="regex"):
    """Regex-Gate + bis zu MAX_REGEX_ROUNDS billige KI1-Korrekturen.
    Rückgabe (text, rest_findings, runden). Läuft nach JEDER Textänderung."""
    from ki_client import llm_call
    from regex_gate import check
    findings = check(text, quellen)
    rounds = 0
    while findings and rounds < MAX_REGEX_ROUNDS:
        repair_prompt = (
            base_prompt
            + "\n\nDein bisheriger Text:\n<artikel>\n" + text + "\n</artikel>\n\n"
            + "Er enthält Stil- und Formfehler. Korrigiere NUR diese Stellen und antworte mit dem kompletten korrigierten Artikel:\n"
            + "\n".join(f"- {f['regel']}: {f['stelle']}" for f in findings)
        )
        befunde_vorher = findings
        text = llm_call(repair_prompt, model="LARGE").strip()
        findings = check(text, quellen)
        rounds += 1
        if draft is not None:
            verlauf(draft, f"{stufe}-korrektur", runde=rounds, befunde=befunde_vorher, text=text)
    return text, findings, rounds


def write_stage():
    """KI1 schreibt pro Cluster; danach Regex-Gate mit bis zu 2 billigen Korrekturrunden.
    Bereits geschriebene/entschiedene Entwürfe werden übersprungen."""
    from ki_client import llm_call, QuotaExhausted, budget_rest, MIN_BUDGET_NEUER_ARTIKEL
    from common import work_days
    ki1_tpl = (ROOT / "prompts" / "ki1.txt").read_text(encoding="utf-8")
    jobs = []
    for day in work_days():  # gestern zuerst
        cfile = DRAFTS / f"{day}-curation.json"
        if cfile.exists():
            for idx, cluster in enumerate(json.loads(cfile.read_text(encoding="utf-8")).get("cluster", [])):
                jobs.append((day, idx, cluster))
    for today, idx, cluster in jobs:
        dpath = draft_path(today, idx)
        name = dpath.stem
        draft = json.loads(dpath.read_text(encoding="utf-8")) if dpath.exists() else {}
        if draft and not needs(draft, None, "write"):
            print(f"{name}: bereits '{draft.get('stage')}' — uebersprungen")
            continue
        if budget_rest() < MIN_BUDGET_NEUER_ARTIKEL:
            # Nicht anfangen, was das Tageskontingent nicht mehr fertig bekommt.
            # Kein Fehler-Befund: der Cluster bleibt offen für den nächsten Lauf.
            print(f"{name}: nur noch {budget_rest()} Anfragen im Tageskontingent — "
                  f"neue Artikel erst im naechsten Lauf")
            break
        draft.update({"tag": today, "cluster_idx": idx, "cluster": cluster})
        try:
            quellen, dropped, meta = sources_from_cluster(cluster, today)
            draft["quellen_verworfen"] = dropped
            draft["quellen_meta"] = meta
            if not quellen:
                reject(today, name, draft, "keine verwertbaren Quellen")
                write_json(dpath, draft)
                continue
            base_prompt = fill(ki1_tpl, QUELLEN=quellen)
            raw = llm_call(base_prompt, model="LARGE").strip()
            draft.pop("verlauf", None)  # Neuschreiben (Wiederholung nach Fehler) → frischer Verlauf
            verlauf(draft, "ki1-entwurf", text=raw)
            raw, findings, rounds = regex_runden(raw, base_prompt, quellen, draft, "regex")
            draft.update({"text": raw, "regex_rest_findings": findings, "korrekturrunden_regex": rounds,
                          "stage": "geschrieben"})
            clear_fail(today, name, draft)
            write_json(dpath, draft)
        except QuotaExhausted as exc:
            print(f"::error::Free-Tier-Tageslimit erreicht — restliche Cluster beim naechsten Lauf: {exc}")
            fail(today, name, draft, "write", "Free-Tier-Tageslimit")
            write_json(dpath, draft)
            break
        except Exception as exc:
            traceback.print_exc()
            print(f"::error::Cluster {idx} in write_stage fehlgeschlagen: {exc}")
            fail(today, name, draft, "write", exc)
            write_json(dpath, draft)


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
    protokoll = llm_json(fill(tpl, ARTIKEL=text, QUELLEN=quellen), model="CHECK", temperature=0.0)
    ampel, gruende = ki2_verdict(protokoll, text)
    return ampel, protokoll, gruende


def evaluate_stage():
    """KI2 prüft → Route. Gelb = KI1-Korrektur, danach IMMER erneute KI2-Prüfung.
    Danach Sonderprüfungen (deterministisch): auch Grün kann dort noch rot werden.
    Ergebnis: stage "gruen" oder "abgelehnt" (bzw. "fehler" → Wiederholung)."""
    from ki_client import llm_call, QuotaExhausted
    from special_checks import run_all
    ki1_tpl = (ROOT / "prompts" / "ki1.txt").read_text(encoding="utf-8")
    for today, dpath, draft in load_drafts():
        if not needs(draft, "geschrieben", "evaluate"):
            continue
        name = dpath.stem
        try:
            cluster = draft["cluster"]
            quellen, _, _ = sources_from_cluster(cluster, today)
            ki1_base = fill(ki1_tpl, QUELLEN=quellen)
            draft.pop("ki2_validierung", None)
            draft.pop("gelb_protokoll", None)

            rounds = 0
            while True:
                ampel, protokoll, gruende = ki2_check(draft["text"], quellen)
                verlauf(draft, "ki2", ampel=ampel, gruende=gruende, protokoll=protokoll)
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
                neu = llm_call(repair_prompt, model="LARGE").strip()
                verlauf(draft, "gelb-korrektur", runde=rounds, hinweis=hinweis, text=neu)
                neu, rest, rr = regex_runden(neu, ki1_base, quellen, draft, "regex-nach-gelb")
                draft["gelb_protokoll"][-1].update({"regex_runden": rr, "regex_rest": len(rest)})
                draft["text"] = neu
                write_json(dpath, draft)  # Zwischenstand sichern

            draft["ki2_pruefrunden"] = rounds + 1
            draft["ki2_endampel"] = ampel
            special = run_all(draft["text"], quellen)
            verlauf(draft, "sonderpruefung", ergebnis=special)
            draft["special_checks"] = special
            final = "gruen" if (ampel == "gruen" and special.get("bestaende") is True) else "rot"
            draft["ampel_final"] = final
            clear_fail(today, name, draft)
            if final != "gruen":
                grund = "KI2 rot/gelb-erschoepft" if ampel != "gruen" else "Sonderpruefung nicht bestanden"
                reject(today, name, draft, grund)
            else:
                draft["stage"] = "gruen"
        except QuotaExhausted as exc:
            print(f"::error::Free-Tier-Tageslimit erreicht: {exc}")
            fail(today, name, draft, "evaluate", "Free-Tier-Tageslimit")
            write_json(dpath, draft)
            break
        except Exception as exc:
            traceback.print_exc()
            print(f"::error::{name} in evaluate_stage fehlgeschlagen: {exc}")
            fail(today, name, draft, "evaluate", exc)
        write_json(dpath, draft)


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
    result = llm_json(fill(tpl, ARTIKEL=text, QUELLEN=quellen), model="CHECK", temperature=0.0)
    entscheidung, gruende = ki3_verdict(result)
    return entscheidung, result, gruende


def legal_stage():
    """KI3 Rechtsabnahme. freigabe → stage "freigegeben". stopp/unklar → abgelehnt
    (endgültig). nachbesserung → EINE Nachprüfung (KI2 + Sonderprüfung + KI3),
    nur eindeutiges Bestehen aller drei → "freigegeben"."""
    from ki_client import QuotaExhausted
    from special_checks import run_all
    for today, dpath, draft in load_drafts():
        if not needs(draft, "gruen", "legal"):
            continue
        name = dpath.stem
        # Wiederholung nach Abbruch mitten in einer Nachbesserung: zurück auf
        # den von KI2 geprüften Text — nie ungeprüft nachgebesserten Text nutzen.
        if draft.get("text_vor_ki3"):
            draft["text"] = draft.pop("text_vor_ki3")
            for k in ("ki3_nachbesserung", "ki3_regex", "ki2_nachpruefung", "special_checks_nachpruefung", "ki3_nachpruefung"):
                draft.pop(k, None)
        try:
            cluster = draft["cluster"]
            quellen, _, _ = sources_from_cluster(cluster, today)
            entscheidung, result, gruende = ki3_check(draft["text"], quellen)
            verlauf(draft, "ki3", entscheidung=entscheidung, gruende=gruende, urteil=result)
            draft["ki3_entscheidung"] = result
            draft["ki3_validierung"] = {"entscheidung": entscheidung, "gruende": gruende}
            clear_fail(today, name, draft)

            if entscheidung == "freigabe":
                pass
            elif entscheidung == "nachbesserung":
                neu, fehl = apply_replacements(draft["text"], result.get("risiken", []))
                if fehl:
                    # Stellen selbst nicht loggen (Zitat aus abgelehntem Text)
                    reject(today, name, draft, f"KI3-Nachbesserung nicht anwendbar ({len(fehl)} Stelle(n) nicht gefunden)")
                    write_json(dpath, draft)
                    continue
                # Regex-Gate auch nach den Ersatzformulierungen; die anschließende
                # Nachprüfung (KI2 + Sonderprüfung + KI3) sieht den Endtext.
                ki1_base = fill((ROOT / "prompts" / "ki1.txt").read_text(encoding="utf-8"), QUELLEN=quellen)
                verlauf(draft, "ki3-ersatz", text=neu)
                neu, rest, rr = regex_runden(neu, ki1_base, quellen, draft, "regex-nach-ki3")
                draft["text_vor_ki3"] = draft["text"]
                draft["text"] = neu
                draft["ki3_nachbesserung"] = True
                draft["ki3_regex"] = {"runden": rr, "rest": len(rest)}
                # EINE Nachprüfung, keine Schleife
                ampel2, prot2, gr2 = ki2_check(neu, quellen)
                verlauf(draft, "ki2-nachpruefung", ampel=ampel2, gruende=gr2, protokoll=prot2)
                draft["ki2_nachpruefung"] = {"ampel": ampel2, "gruende": gr2, "protokoll": prot2}
                special2 = run_all(neu, quellen)
                verlauf(draft, "sonderpruefung-nachpruefung", ergebnis=special2)
                draft["special_checks_nachpruefung"] = special2
                if ampel2 != "gruen" or special2.get("bestaende") is not True:
                    reject(today, name, draft, "Nachpruefung nach KI3-Nachbesserung nicht bestanden (KI2/Sonderpruefung)")
                    write_json(dpath, draft)
                    continue
                ent2, res2, gr3 = ki3_check(neu, quellen)
                verlauf(draft, "ki3-nachpruefung", entscheidung=ent2, gruende=gr3, urteil=res2)
                draft["ki3_nachpruefung"] = {"entscheidung": ent2, "gruende": gr3, "urteil": res2}
                if ent2 != "freigabe":
                    reject(today, name, draft, f"KI3-Nachpruefung: {ent2}")
                    write_json(dpath, draft)
                    continue
                draft.pop("text_vor_ki3", None)
            else:
                reject(today, name, draft, f"KI3: stopp ({'; '.join(gruende)})")
                write_json(dpath, draft)
                continue

            draft["freigabe_final"] = True
            draft["stage"] = "freigegeben"
        except QuotaExhausted as exc:
            print(f"::error::Free-Tier-Tageslimit erreicht: {exc}")
            fail(today, name, draft, "legal", "Free-Tier-Tageslimit")
            write_json(dpath, draft)
            break
        except Exception as exc:
            traceback.print_exc()
            print(f"::error::{name} in legal_stage fehlgeschlagen: {exc}")
            fail(today, name, draft, "legal", exc)
        write_json(dpath, draft)


if __name__ == "__main__":
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    stages = {"write_stage": write_stage, "evaluate_stage": evaluate_stage, "legal_stage": legal_stage}
    if cmd not in stages:
        raise SystemExit(f"Unbekannter Befehl: {cmd!r}. Erlaubt: {', '.join(stages)}")
    stages[cmd]()
