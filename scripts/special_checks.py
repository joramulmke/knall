"""
special_checks.py — Harte, deterministische Faktenchecks nach KI2.
Zahlen-Diff, Entity-Check, N-Gramm-Plagiatscheck, Injection-Check.
Diese Checks laufen IMMER, auch wenn KI2 grün sagt.
"""
import re
import json
import pathlib

PLAGIAT_NGRAM = 20  # P8: ab 20 aufeinanderfolgenden identischen Wörtern

URL_RX = re.compile(r"https?://\S+|www\.\S+", re.I)
WORD_RX = re.compile(r"[\wÄÖÜäöüß]+(?:[-.][\wÄÖÜäöüß]+)*")

# Wörter, die am Satzanfang/als Funktionswort großgeschrieben auftauchen
STOP = {
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer", "eines", "einem", "einen",
    "und", "oder", "aber", "doch", "nur", "auch", "noch", "schon", "jetzt", "heute", "dann",
    "im", "in", "am", "an", "auf", "aus", "bei", "mit", "nach", "von", "vor", "zu", "zum", "zur",
    "für", "über", "unter", "gegen", "ohne", "um", "bis", "seit", "wie", "was", "wer", "wo",
    "er", "sie", "es", "man", "kein", "keine", "alle", "viele", "mehr", "weg", "gefunden",
}

# Titel/Funktionen, nach denen ein Personen- oder Firmenname folgt
NAME_TRIGGER = re.compile(
    r"\b(?:Herr|Frau|Dr\.|Prof\.|Minister\w*|Ministerin|Bürgermeister\w*|Landrat|Landrätin|"
    r"Chef\w*|Vorstand\w*|Sprecher\w*|Geschäftsführer\w*|Firma|Konzern|Unternehmen|Vermieter\w*)"
    r"\s+((?:[A-ZÄÖÜ][\wäöüß-]+\s?){1,3})"
)


def strip_urls(text):
    return URL_RX.sub(" ", text or "")


# --- Zahlen: Ziffern UND Zahlwörter, mit Größenordnung, exakt nach Wert ---
from decimal import Decimal, InvalidOperation  # noqa: E402

EINER = {"eins": 1, "ein": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6,
         "sieben": 7, "acht": 8, "neun": 9}
DIREKT = {"zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8,
          "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "dreizehn": 13, "vierzehn": 14,
          "fünfzehn": 15, "sechzehn": 16, "siebzehn": 17, "achtzehn": 18, "neunzehn": 19,
          "zwanzig": 20, "dreißig": 30, "vierzig": 40, "fünfzig": 50, "sechzig": 60,
          "siebzig": 70, "achtzig": 80, "neunzig": 90, "hundert": 100, "tausend": 1000,
          "eins": 1}
ZEHNER = {k: v for k, v in DIREKT.items() if v in (20, 30, 40, 50, 60, 70, 80, 90)}
# "ein/eine/einer…" allein ist Artikel, keine Zahl — nur vor einer Größenordnung
ARTIKEL_EINS = {"ein", "eine", "einen", "einem", "einer", "eines"}
SKALA = {
    "tausend": 10 ** 3, "tsd": 10 ** 3,
    "million": 10 ** 6, "millionen": 10 ** 6, "mio": 10 ** 6,
    "milliarde": 10 ** 9, "milliarden": 10 ** 9, "mrd": 10 ** 9,
    "billion": 10 ** 12, "billionen": 10 ** 12,
}
TOKEN_RX = re.compile(
    r"(?P<num>\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:,\d+)?(?![\d.])|\d+(?:[.,]\d+)*)"
    r"|(?P<wort>[A-Za-zÄÖÜäöüß]+)"
)


def zahlwort(w):
    """Deutsches Zahlwort (0 < n < 1.000.000) → int, sonst None.
    'dreiundzwanzig' → 23, 'zweihundertfünfzig' → 250, 'viertausend' → 4000."""
    w = w.lower()
    if not w:
        return None
    if w in DIREKT:
        return DIREKT[w]
    for teil, faktor in (("tausend", 1000), ("hundert", 100)):
        if teil in w:
            links, rechts = w.split(teil, 1)
            lw = 1 if links in ("", "ein") else zahlwort(links)
            rw = 0 if rechts == "" else zahlwort(rechts)
            if lw is None or rw is None or (links and lw >= faktor):
                return None
            return lw * faktor + rw
    if "und" in w:
        einer, zehner = w.split("und", 1)
        if einer in EINER and zehner in ZEHNER:
            return EINER[einer] + ZEHNER[zehner]
    return None


def _ziffern_alternativen(tok):
    """Ziffern-Token → Liste von Lesarten; jede Lesart ist eine Menge von
    Werten, die ALLE in der Quelle stehen müssen.
    4.000 / 4 000 → [{4000}] · 3,5 → [{3.5}] · 14.30 → [{14.3}, {14, 30}]
    26.11.2026 → [{26, 11, 2026}] (Datum: jeder Teil muss gedeckt sein)"""
    t = tok.strip(".,")
    t2 = re.sub(r"[ \u00a0\u202f]", "", t)
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", t2) or t2 != t:
        return [{Decimal(t2.replace(".", "").replace(",", "."))}]
    if re.fullmatch(r"\d+,\d+", t):
        return [{Decimal(t.replace(",", "."))}]
    if re.fullmatch(r"\d+\.\d+", t):
        return [{Decimal(t)}, {Decimal(x) for x in t.split(".")}]
    if "." in t or "," in t:
        return [{Decimal(x) for x in re.split(r"[.,]", t) if x}]
    return [{Decimal(t)}]


def zahlen_mit_werten(text):
    """Alle Zahlen im Text als Liste (originalstelle, [lesarten]).
    Größenordnungen (Millionen, Mio., Mrd., Tausend) werden eingerechnet."""
    toks = [(m.group(0), m.group("num") is not None) for m in TOKEN_RX.finditer(strip_urls(text or ""))]
    out = []
    i = 0
    while i < len(toks):
        tok, ist_ziffer = toks[i]
        nxt = toks[i + 1][0].lower().rstrip(".") if i + 1 < len(toks) else ""
        alts = None
        if ist_ziffer:
            try:
                alts = _ziffern_alternativen(tok)
            except InvalidOperation:
                alts = None
        else:
            lw = tok.lower()
            if lw in ARTIKEL_EINS:
                alts = [{Decimal(1)}] if nxt in SKALA and nxt != "tausend" else None
            else:
                z = zahlwort(lw)
                alts = [{Decimal(z)}] if z is not None else None
        if alts is None:
            i += 1
            continue
        if nxt in SKALA:
            alts = [{w * SKALA[nxt] for w in a} for a in alts if len(a) == 1]
            if alts:
                out.append((f"{tok} {toks[i + 1][0]}", alts))
                i += 2
                continue
        out.append((tok, alts or [set()]))
        i += 1
    return out


def extract_numbers(text):
    return {stelle for stelle, _ in zahlen_mit_werten(text)}


def normalize_number(n):
    # Kompatibilität: 4.000 == 4000 ; 3,5 bleibt 3,5
    n = n.strip(".,")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", n):
        n = n.replace(".", "")
    return n


def check_numbers(article, source_text):
    """Jede Zahl im Artikel (Ziffer ODER Zahlwort, inkl. Größenordnung) muss
    WERTGLEICH in der Quelle stehen — egal ob dort als Ziffer oder Wort.
    'zwei Stationen' (Quelle) deckt '2 Stationen' (Artikel) und umgekehrt;
    '2 Millionen' deckt NICHT '2' und nicht '2,5 Millionen'."""
    quelle = set()
    for _, alts in zahlen_mit_werten(source_text):
        for a in alts:
            quelle |= a
    fehler = set()
    for stelle, alts in zahlen_mit_werten(article):
        if not any(a and a <= quelle for a in alts):
            fehler.add(stelle)
    return sorted(fehler)


def source_words(source_text):
    return {w.lower() for w in WORD_RX.findall(strip_urls(source_text))}


def known(word, src_words):
    """Wort (mit Flexionstoleranz) in der Quelle bekannt?"""
    w = word.lower().strip(".-")
    if not w or w in STOP or w in src_words:
        return True
    for suf in ("s", "es", "n", "en", "e", "er", "ern", "em"):
        if w.endswith(suf) and len(w) - len(suf) >= 3 and w[: -len(suf)] in src_words:
            return True
    stem = w[:-2] if len(w) > 6 else w
    return any(s.startswith(stem) for s in src_words if len(s) >= len(stem))


DETERMINANTEN = (
    r"der|die|das|den|dem|des|ein|eine|einen|einem|einer|eines|kein|keine|keinen|keinem|keiner|"
    r"im|am|zum|zur|vom|beim|ins|ans|alle|allen|viele|vielen|mehrere|einige|diese|dieser|diesen|"
    r"ihre|ihren|ihrer|seine|seinen|seiner|jede|jeder|jeden|\d[\d.,]*"
)
NOMEN_NACH_ARTIKEL = re.compile(
    # (?i:…) nur für den Artikel — Adjektive müssen klein, das Nomen groß sein
    r"(?<![\wäöüß])(?i:" + DETERMINANTEN + r")\s+(?:[a-zäöüß][\wäöüß-]*\s+){0,2}([A-ZÄÖÜ][\wäöüß-]+)",
)
SATZANFANG_RX = re.compile(r"(?:^|[.!?:;—–\n]\s*|\n\s*)$")
MEHRWORT_RX = re.compile(r"(?<![\wäöüß])(?:[A-ZÄÖÜ][a-zäöüß]+[ -]){1,3}[A-ZÄÖÜ][a-zäöüß]+(?![\wäöüß])")


def quell_nomen(source_text):
    """Gewöhnliche Nomen der Quelle: groß geschriebene Wörter, die dort nach
    einem Artikel/Determinativ stehen ('das Klinikum', 'die neuen Stationen',
    '400 Stellen'). Eigennamen stehen meist ohne Artikel."""
    nomen = set()
    for m in NOMEN_NACH_ARTIKEL.finditer(strip_urls(source_text)):
        w = m.group(1)
        nomen.add(w.lower())
    return nomen


def _ist_quell_nomen(word, nomen):
    w = word.lower()
    if w in nomen:
        return True
    for suf in ("s", "es", "n", "en", "e", "er", "ern"):
        if w.endswith(suf) and w[: -len(suf)] in nomen:
            return True
        if (w + suf) in nomen:
            return True
    return False


def extract_entities(text, source_text=""):
    text = strip_urls(text)
    nomen = quell_nomen(source_text) if source_text else set()
    ents = set()
    # Mehrwort-Eigennamen. Nicht als Name zählen:
    # - das erste Wort am Satzanfang (dort ist alles groß geschrieben)
    # - Funktionswörter ("Die Bosse" ist kein Name)
    # - Folgen, in denen ein Wort ein gewöhnliches Nomen aus der Quelle ist
    #   ("Klinikum Riesa": Quelle sagt "das Klinikum")
    for m in MEHRWORT_RX.finditer(text):
        words = re.split(r"[ -]", m.group(0))
        if SATZANFANG_RX.search(text[: m.start()]):
            words = words[1:]
        while words and words[0].lower() in STOP:
            words = words[1:]
        if len(words) < 2:
            continue
        if any(_ist_quell_nomen(w, nomen) for w in words):
            continue
        ents.add(" ".join(words))
    # Abkürzungen/Firmenkürzel (BMAS, DGB, ver.di wird über Wortcheck erfasst)
    ents |= set(re.findall(r"\b[A-ZÄÖÜ]{2,}[a-z]?\b", text))
    # Wörter mit Binnenmajuskel oder Ziffern (z. B. "McKinsey", "A14")
    ents |= set(re.findall(r"\b[A-ZÄÖÜ][a-zäöüß]+[A-ZÄÖÜ]\w*\b", text))
    # Namen nach Funktionsbezeichnungen ("Minister Müller", "Konzern Tesla")
    ents |= {m.group(1).strip() for m in NAME_TRIGGER.finditer(text)}
    return ents


def check_entities(article, source_text):
    """Jedes Namens-/Kürzel-Element muss Wort für Wort in der Quelle vorkommen."""
    src_words = source_words(source_text)
    src_lower = strip_urls(source_text).lower()
    missing = []
    for e in sorted(extract_entities(article, source_text)):
        if e.lower() in src_lower:
            continue
        words = [w for w in re.split(r"[\s-]+", e) if w]
        if not all(known(w, src_words) for w in words):
            missing.append(e)
    return missing


def check_plagiarism(article, source_text, n=PLAGIAT_NGRAM):
    """>= n identische aufeinanderfolgende Wörter (satzzeichen-unabhängig) = Plagiat."""
    art = [w.lower() for w in WORD_RX.findall(strip_urls(article))]
    src = [w.lower() for w in WORD_RX.findall(strip_urls(source_text))]
    if len(art) < n or len(src) < n:
        return None
    src_grams = {tuple(src[i:i + n]) for i in range(len(src) - n + 1)}
    for i in range(len(art) - n + 1):
        gram = tuple(art[i:i + n])
        if gram in src_grams:
            return list(gram)
    return None


def run_all(article_text, source_text):
    from safety import injection_hits
    result = {
        "zahlen_fehler": check_numbers(article_text, source_text),
        "entities_fehler": check_entities(article_text, source_text),
        "plagiat": check_plagiarism(article_text, source_text),
        "injection": injection_hits(article_text),
        "leer": len((article_text or "").split()) < 30,
    }
    result["bestaende"] = not any(result[k] for k in ("zahlen_fehler", "entities_fehler", "plagiat", "injection", "leer"))
    return result


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    art = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
    src = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
    print(json.dumps(run_all(art, src), ensure_ascii=False, indent=2))
