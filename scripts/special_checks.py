"""
special_checks.py — Harte, deterministische Faktenchecks nach KI2.
Zahlen-Diff, Entity-Check, N-Gramm-Plagiatscheck.
Diese Checks sind nicht täuschbar — sie laufen IMMER, auch wenn KI2 grün sagt.
"""
import re
import json
import pathlib


def extract_numbers(text):
    # Zahlen inkl. Dezimalstellen und Einheiten-Kontext
    return set(re.findall(r"\d+[\d.,]*\d*|\d+", text))


def check_numbers(article, source_text):
    """Jede Zahl im Artikel muss in der Quelle vorkommen."""
    art_nums = extract_numbers(article)
    src_nums = extract_numbers(source_text)
    missing = [n for n in art_nums if n not in src_nums and normalize(n) not in {normalize(x) for x in src_nums}]
    return missing


def normalize(n):
    return n.strip(". ,").replace(".", "").rstrip(",")


def extract_entities(text):
    # Grobe Entity-Heuristik: Grossgeschriebene Mehrwort-Phrasen
    return set(re.findall(r"(?:[A-ZÄÖÜ][a-zäöüß]+\s){1,3}[A-ZÄÖÜ][a-zäöüß]+", text))


def check_entities(article, source_text):
    """Entities im Artikel müssen in der Quelle vorkommen (mit Toleranz für Fallünde)."""
    art_ent = extract_entities(article)
    src_ent = extract_entities(source_text)
    src_text = source_text
    missing = []
    for e in art_ent:
        if e not in src_ent and e not in src_text:
            # Wort-für-Wort-Toleranz: alle Einzelwörter bekannt?
            words = e.split()
            if not all(w in src_text for w in words):
                missing.append(e)
    return missing


def check_plagiarism(article, source_text, max_ngram=20):
    """Longest-common-word-sequence: >= 20 Wörter Übereinstimmung = Plagiatsfund."""
    art_words = re.findall(r"\w+", article.lower())
    src_words = set()
    src_text_lower = source_text.lower()
    src_words_list = re.findall(r"\w+", src_text_lower)
    src_bigrams = set(zip(src_words_list, src_words_list[1:]))
    for n in range(max_ngram, 5, -1):
        for i in range(len(art_words) - n + 1):
            seq = art_words[i:i + n]
            joined = " ".join(seq)
            if joined in src_text_lower:
                return seq
    return None


def run_all(article_text, source_text):
    result = {
        "zahlen_fehler": check_numbers(article_text, source_text),
        "entities_fehler": check_entities(article_text, source_text),
        "plagiat": check_plagiarism(article_text, source_text),
    }
    result["bestaende"] = not (result["zahlen_fehler"] or result["entities_fehler"] or result["plagiat"])
    return result


if __name__ == "__main__":
    import sys
    art = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
    src = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
    print(json.dumps(run_all(art, src), ensure_ascii=False, indent=2))
