"""
regex_gate.py — Maschinelle Stil- und Formprüfung zwischen KI1 und KI2.
Fängt billige Fehler deterministisch, damit der teure KI2-Aufruf nur
sauber formulierte Texte sieht. Befunde gehen als Korrekturrunde zurück zu KI1.
"""
import re
import json
import pathlib

# Formale Verstöße — jede Fundstelle wird protokolliert
RULES = [
    ("konjunktiv", re.compile(r"\b(könnte|würde|müsste|dürfte|wäre|käme|ginge|hätte|hätten|wollte nicht|sollte man)\b", re.I)),
    ("passiv", re.compile(r"\bwurde(n)?\b|\bwird \w+( ge\w+t| ge\w+n)\b", re.I)),
    ("floskel", re.compile(r"(schockierend|unglaublich|kaum zu glauben|und es wird noch schlimmer|das ist erst der Anfang|krass enthüllt)", re.I)),
    ("grossbuchstaben", re.compile(r"\b[A-Z]{4,}\b")),
    ("ansprache", re.compile(r"\b(Du|Ihr|Sie|Wir)\b(?=\s|$|[,\.!?])")),
    ("anfuehrung", re.compile(r'["„“»«]')),
]


def check(text):
    findings = []
    for name, rx in RULES:
        for match in rx.finditer(text):
            findings.append({
                "regel": name,
                "stelle": match.group(0),
                "position": match.start(),
            })
    # Sätze über 40 Wörter
    for satz in re.split(r"(?<=[.!?])\s+", text.replace("\n", " ")):
        words = [w for w in satz.split() if w]
        if len(words) > 40:
            findings.append({"regel": "langesatz", "stelle": (satz[:70] + "..."), "position": -1})
    # Duplikate nach Regel+Stelle entfernen
    seen = set()
    unique = []
    for f in findings:
        key = (f["regel"], f["stelle"])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def main():
    import sys
    text_file = sys.argv[1]
    text = pathlib.Path(text_file).read_text(encoding="utf-8")
    findings = check(text)
    print(json.dumps({"befund": "gelb" if findings else "gruen", "findings": findings}, ensure_ascii=False, indent=2))
    sys.exit(0 if not findings else 1)  # Exit 1 = gelb


if __name__ == "__main__":
    main()
