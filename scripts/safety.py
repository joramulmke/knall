"""
safety.py — Deterministische Sicherheitsschicht ohne menschliche Freigabe.

Grundsatz: FAIL-CLOSED. Alles, was nicht eindeutig und vollständig
"gruen" bzw. "freigabe" ist, gilt als rot/stopp.

- clean_text(): HTML raus, Steuerzeichen raus, Prompt-Begrenzer neutralisieren.
- injection_hits(): erkennt Anweisungs-Text in Quellen/Artikel (Prompt-Injection).
- ki2_verdict(): validiert das KI2-Protokoll streng und rechnet die Ampel nach.
- ki3_verdict(): validiert das KI3-Urteil streng.
"""
import html
import re
import unicodedata

TAG_RX = re.compile(r"<[^>]{0,2000}>")
CTRL_RX = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f​-‏‪-‮⁠-⁤﻿]")
DELIM_RX = re.compile(r"</?\s*(quelle|quellen|artikel|system|user|assistant|instructions?)\b[^>]*>", re.I)

INJECTION_PATTERNS = [
    r"ignor\w*\s+(?:\w+\s+){0,3}(anweisung|instruktion|instruction|regel|prompt|vorgabe|befehl)",
    r"(vorherige|obige|bisherige|previous|above|prior)\s+(anweisung|instruktion|instruction|regel|prompt)",
    r"(system\s*prompt|systemprompt|developer\s*message|jailbreak)",
    r"\byou\s+are\s+(now\s+)?(a|an|the)\b",
    r"\bdu\s+bist\s+(jetzt|nun|ab\s+sofort)\b",
    r"(antworte|antwortet|respond|answer|output|gib\s+aus)\s+(?:\w+\s+){0,3}(json|\"?ampel|\"?entscheidung|gruen|grün|freigabe)",
    r"\"\s*(ampel|entscheidung|befund|titel_befund|risiken|saetze)\s*\"\s*:",
    r"\bampel\s*[:=]\s*\"?(gruen|grün|green)",
    r"\bentscheidung\s*[:=]\s*\"?freigabe",
]
INJECTION_RX = [re.compile(p, re.I) for p in INJECTION_PATTERNS]


def clean_text(text):
    """Quelltext für Prompts: HTML-Tags/Entities, Steuer- und Bidi-Zeichen,
    Prompt-Begrenzer entfernen; Whitespace normalisieren."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", str(text))
    t = TAG_RX.sub(" ", t)
    t = html.unescape(t)
    t = TAG_RX.sub(" ", t)
    t = CTRL_RX.sub("", t)
    t = DELIM_RX.sub(" ", t)
    t = t.replace("{{", "{ {").replace("}}", "} }")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t)
    return t.strip()


def injection_hits(text):
    t = unicodedata.normalize("NFKC", text or "")
    return [rx.pattern for rx in INJECTION_RX if rx.search(t)]


def _norm(val):
    s = str(val or "").strip().lower()
    return s.replace("ü", "ue").replace("ä", "ae").replace("ö", "oe")


def count_sentences(text):
    parts = [p for p in re.split(r"(?<=[.!?])\s+|\n+", text or "") if len(p.split()) >= 2]
    return len(parts)


ORDER = {"gruen": 0, "gelb": 1, "rot": 2}


def ki2_verdict(protokoll, article_text):
    """Gibt (ampel, gruende) zurück. ampel ∈ {gruen, gelb, rot}.
    Strengste Lesart gewinnt; alles Unerwartete = rot."""
    gruende = []
    if not isinstance(protokoll, dict):
        return "rot", ["KI2-Protokoll ist kein Objekt"]

    reported = _norm(protokoll.get("ampel"))
    if reported not in ORDER:
        gruende.append(f"unbekannte Ampel {protokoll.get('ampel')!r}")
        reported = "rot"

    if protokoll.get("systemfehler") not in (None, "", "null", False):
        gruende.append(f"systemfehler: {protokoll.get('systemfehler')}")

    saetze = protokoll.get("saetze")
    computed = "gruen"
    if not isinstance(saetze, list) or not saetze:
        gruende.append("keine Satzpruefung im Protokoll")
    else:
        for s in saetze:
            if not isinstance(s, dict):
                gruende.append("Satzeintrag ist kein Objekt")
                continue
            b = _norm(s.get("befund"))
            if b == "rot":
                computed = "rot"
            elif b == "gelb":
                if computed != "rot":
                    computed = "gelb"
            elif b != "ok":
                gruende.append(f"unbekannter Satzbefund {s.get('befund')!r}")
            # Ein 'ok' mit gelisteten Fakt-Verstoessen ist widersprüchlich
            if b == "ok" and any(
                isinstance(v, dict) and str(v.get("pruefposten", "")).upper() in {f"P{i}" for i in range(1, 12)}
                and _norm(v.get("erklaerung")) not in ("", "keine", "none")
                for v in (s.get("verstoesse") or [])
            ):
                gruende.append(f"Satz {s.get('satz_nr')}: 'ok' trotz gelisteter Verstoesse")
        # Abdeckung: KI2 muss (fast) jeden Satz geprüft haben
        n_art = count_sentences(article_text)
        if n_art and len(saetze) < max(1, int(n_art * 0.6)):
            gruende.append(f"nur {len(saetze)} von ~{n_art} Saetzen geprueft")

    titel = _norm(protokoll.get("titel_befund"))
    if titel == "rot":
        computed = "rot"
    elif titel == "gelb":
        if computed != "rot":
            computed = "gelb"
    elif titel != "ok":
        gruende.append(f"unbekannter Titelbefund {protokoll.get('titel_befund')!r}")

    if gruende:
        return "rot", gruende
    final = max(reported, computed, key=lambda a: ORDER[a])
    if final != reported:
        gruende.append(f"Ampel nachgerechnet: KI2 sagte {reported}, Saetze ergeben {computed}")
    return final, gruende


REQUIRED_RISKS = {f"R{i}" for i in range(1, 7)}


def ki3_verdict(result):
    """Gibt (entscheidung, gruende) zurück. entscheidung ∈ {freigabe, nachbesserung, stopp}.
    Freigabe nur bei vollständigem, widerspruchsfreiem Urteil."""
    gruende = []
    if not isinstance(result, dict):
        return "stopp", ["KI3-Urteil ist kein Objekt"]
    ent = _norm(result.get("entscheidung"))
    if ent not in ("freigabe", "nachbesserung", "stopp"):
        return "stopp", [f"unbekannte Entscheidung {result.get('entscheidung')!r}"]
    if ent == "stopp":
        return "stopp", ["KI3: stopp"]

    risiken = result.get("risiken")
    if not isinstance(risiken, list):
        return "stopp", ["keine Risikoliste"]
    gesehen = set()
    kritisch_ohne_ersatz = []
    for r in risiken:
        if not isinstance(r, dict):
            return "stopp", ["Risikoeintrag ist kein Objekt"]
        frage = str(r.get("frage", "")).strip().upper()
        gesehen.add(frage)
        urteil = _norm(r.get("urteil"))
        if urteil not in ("unauffaellig", "grenzwertig", "kritisch"):
            return "stopp", [f"{frage}: unbekanntes Urteil {r.get('urteil')!r}"]
        if urteil == "kritisch":
            if ent == "freigabe":
                return "stopp", [f"{frage}: 'kritisch' widerspricht 'freigabe'"]
            stelle = str(r.get("stelle") or "").strip()
            ersatz = str(r.get("ersatzformulierung") or "").strip()
            if not stelle or stelle.lower() == "null" or not ersatz:
                kritisch_ohne_ersatz.append(frage)
    fehlend = REQUIRED_RISKS - gesehen
    if fehlend:
        return "stopp", [f"Risikofragen fehlen: {', '.join(sorted(fehlend))}"]
    if kritisch_ohne_ersatz:
        return "stopp", [f"kritisch ohne Ersatzformulierung: {', '.join(kritisch_ohne_ersatz)}"]
    return ent, gruende
