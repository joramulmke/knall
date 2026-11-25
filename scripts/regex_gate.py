"""
regex_gate.py — Maschinelle Stil- und Formprüfung zwischen KI1 und KI2.
Fängt billige Fehler deterministisch, damit KI2 nur sauber formulierte
Texte sieht. Befunde gehen als Korrekturrunde zurück zu KI1.

v2: weniger Fehlalarme
- "Sie"/"Ihr" nur mitten im Satz großgeschrieben (= Höflichkeitsform),
  nicht am Satzanfang (= sie/ihr, 3. Person).
- Großbuchstaben-Wörter, die in den Quellen stehen (BMAS, NATO), sind erlaubt.
- Passiv nur bei Hilfsverb + Partizip (nicht "wurde Chefin").
"""
import re
import json
import pathlib

GE_INFINITIVE = {
    "gehen", "geben", "gelten", "gewinnen", "genießen", "geschehen", "gehören", "gefallen",
    "gelingen", "gebären", "genesen", "gestehen", "gewähren", "gewöhnen", "gestalten",
}

PASSIV_RX = re.compile(
    r"\b(wurde|wurden|wird|werden|worden|wurdest)\b((?:\s+[\wäöüÄÖÜß-]+){0,5}?)\s+(ge[\wäöüß]+(?:t|en))\b",
    re.I,
)

RULES = [
    ("konjunktiv", re.compile(r"\b(könnte|könnten|würde|würden|müsste|müssten|dürfte|dürften|wäre|wären|käme|ginge|hätte|hätten|sollte man)\b", re.I)),
    ("floskel", re.compile(r"(schockierend|unglaublich|kaum zu glauben|und es wird noch schlimmer|das ist erst der Anfang|krass enthüllt)", re.I)),
    ("ansprache", re.compile(r"\b(du|dich|dir|dein\w*|euch|euer|eure\w*|wir|uns|unser\w*)\b", re.I)),
    # Höflichkeitsform: großes Sie/Ihr/Ihnen NICHT am Satz-/Zeilenanfang
    ("ansprache", re.compile(r"(?<=[\wäöüß,;:)])\s+(Sie|Ihnen|Ihr|Ihre\w*)\b")),
    ("anfuehrung", re.compile(r'["„“”»«]')),
]

CAPS_RX = re.compile(r"\b[A-ZÄÖÜ]{4,}\b")


def check(text, quellen=""):
    findings = []
    for name, rx in RULES:
        for match in rx.finditer(text):
            findings.append({"regel": name, "stelle": match.group(0).strip(), "position": match.start()})
    for match in PASSIV_RX.finditer(text):
        partizip = match.group(3).lower()
        if partizip in GE_INFINITIVE:
            continue
        findings.append({"regel": "passiv", "stelle": match.group(0), "position": match.start()})
    for match in CAPS_RX.finditer(text):
        if match.group(0) not in (quellen or ""):
            findings.append({"regel": "grossbuchstaben", "stelle": match.group(0), "position": match.start()})
    # Sätze über 40 Wörter
    for satz in re.split(r"(?<=[.!?])\s+", text.replace("\n", " ")):
        words = [w for w in satz.split() if w]
        if len(words) > 40:
            findings.append({"regel": "langesatz", "stelle": (satz[:70] + "..."), "position": -1})
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
    text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
    quellen = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8") if len(sys.argv) > 2 else ""
    findings = check(text, quellen)
    print(json.dumps({"befund": "gelb" if findings else "gruen", "findings": findings}, ensure_ascii=False, indent=2))
    sys.exit(0 if not findings else 1)  # Exit 1 = gelb


if __name__ == "__main__":
    main()
