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


def extract_numbers(text):
    return set(re.findall(r"\d+(?:[.,]\d+)*", strip_urls(text)))


def normalize_number(n):
    # 4.000 == 4000 ; 3,5 bleibt 3,5
    n = n.strip(".,")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", n):
        n = n.replace(".", "")
    return n


def check_numbers(article, source_text):
    """Jede Zahl im Artikel muss (normalisiert) in der Quelle vorkommen."""
    src = {normalize_number(x) for x in extract_numbers(source_text)}
    return sorted({n for n in extract_numbers(article) if normalize_number(n) not in src})


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


def extract_entities(text):
    text = strip_urls(text)
    ents = set()
    # Mehrwort-Eigennamen
    for m in re.findall(r"(?:[A-ZÄÖÜ][a-zäöüß]+[ -]){1,3}[A-ZÄÖÜ][a-zäöüß]+", text):
        words = re.split(r"[ -]", m)
        while words and words[0].lower() in STOP:  # "Die Bosse" ist kein Name
            words = words[1:]
        if len(words) >= 2:
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
    for e in sorted(extract_entities(article)):
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
