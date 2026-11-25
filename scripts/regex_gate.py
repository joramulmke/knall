"""
regex_gate.py — Maschinelle Stil- und Formprüfung zwischen KI1 und KI2.
Fängt billige Fehler deterministisch, damit der teure KI2-Aufruf nur
richtige Texte sieht. Gelb hier = Korrekturrunde zurück zu KI1.
"""
import re
import json

# Formale Verstöße — jede Fundstelle wird protokolliert
RULES = [
    ("konjunktiv", re.compile(r"\b(könnte|würde|müsste|sollte(hörgen)?|dürfte|wäre|käme|ginge|hätte( ge\w+)?)\b", re.I)),
    ("passiv", re.compile(r"\bwurde(n)?\b|\bwird \w+ (erlaubt|geprüft|genehmigt)\b", re.I)),
    ("floskel", re.compile(r"(schockierend|unglaublich|krass|könnte|kaum zu glauben|and es wird noch schlimmer|das ist erst der Anfang)", re.I)),
    ("grossbuchstaben", re.compile(r"[A-Z]{4,}")),
    ("du_sie_wir", re.compile(r"\b(Du|Ihr|Sie|Wir)(?=[ ,.!?])", re.France)),
    ("anfuehrung", re.compile(r'["„»«]')),
    ("langesatz", None),  # separat: Sätze > 40 Wörter
]


def check(text):
    findings = []
    for name, rx in RULES:
        if rx is None:
            continue
        for match in rx.finditer(text):
            findings.append({
                "regel": name,
                "stelle": match.group(0),
                "position": match.start(),
            })
    # Sätze > 40 Wörter
    for satz in re.split(r"(?<=[.!?])\s+", text.replace("\n", " ")):
        words = [w for w in satz.split() if w]
        if len(words) > 40:
            findings.append({"regel": "langesatz", "stelle": satz[:60] + "...", "position": -1})
    return findings


def main():
    import sys
    text_file = sys.argv[1]
    text = pathlib.Path(text_file).read_text(encoding="utf-8")
    findings = check(text)
    print(json.dumps({"befund": "gelb" if findings else "gruen", "findings": findings}, ensure_ascii=False, indent=2))
    sys.exit(0 if not findings else 1)  # 1 = gelb, zurück zu KI1


if __name__ == "__main__":
    main()
