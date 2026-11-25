"""
build_site.py — Rendert published/*.md in site/index.html im Boulevard-Layout:
Aufmacher (neuester Artikel) + Kachelraster, Laufband, Ressort-Leiste.

Die Vorlage site/index.html enthält Marker-Paare, deren Inhalt ersetzt wird:
  <!-- DATUM -->…<!-- /DATUM -->        Datumszeile im Kopf
  <!-- TICKER -->…<!-- /TICKER -->      Laufband mit Schlagzeilen
  <!-- RESSORTS -->…<!-- /RESSORTS -->  Ressort-Filter
  <!-- ARTIKEL -->…<!-- /ARTIKEL -->    Aufmacher + Raster (Pflicht)

Sicherheit: Jeder Text aus LLM/Quellen wird HTML-escaped; Links nur mit
http(s)-Schema. Kein Markdown-Rendering von Rohtext → kein XSS.
"""
import datetime as dt
import html
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAX_ARTIKEL = 30
MAX_TICKER = 8
TEASER_ZEICHEN = 220

RESSORT_NAMEN = {
    "kohle": "Kohle",
    "alle-gleich": "Alle gleich",
    "pflege": "Pflege",
    "klima": "Klima",
    "lokal": "Lokal",
    "woche": "Die Woche",
    "wohnen": "Wohnen",
    "all": "Politik",
}
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember"]
WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag",
              "Samstag", "Sonntag"]
# Wörter, die als Riesen-Schlagwort im Typo-Block nichts taugen
FUELLWORTE = {"Bundesgerichtshof", "Pressemitteilung", "Bundesregierung"}


def parse(md):
    meta, body = {}, md
    m = re.match(r"---\n(.*?)\n---\n(.*)", md, re.S)
    if m:
        body = m.group(2)
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            try:
                meta[k.strip()] = json.loads(v.strip())
            except json.JSONDecodeError:
                meta[k.strip()] = v.strip()
    # Quellen-/Hinweisblock aus dem Body entfernen — wird aus meta gerendert
    body = body.split("\nQuellen:\n", 1)[0].strip()
    return meta, body


def ohne_markdown(t):
    """KI-Ausgaben enthalten mitunter **fett**/__fett__ oder # — als Klartext
    sähe man die Zeichen. Entfernen statt rendern (kein Markdown → kein XSS)."""
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t, flags=re.S)
    t = re.sub(r"^\s*#+\s*", "", t, flags=re.M)
    return t.replace("**", "")


def safe_url(u):
    u = str(u or "").strip()
    return u if re.match(r"^https?://", u, re.I) else None


def e(t):
    return html.escape(str(t or ""), quote=True)


def ressort_name(r):
    r = str(r or "").strip()
    return RESSORT_NAMEN.get(r, r.replace("-", " ").title() or "Politik")


def ressort_slug(r):
    r = str(r or "all").strip().lower() or "all"
    return re.sub(r"[^a-z0-9-]", "", r) or "all"


def datum_lang(iso):
    try:
        d = dt.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso or "")
    return f"{d.day}. {MONATE[d.month - 1]} {d.year}"


def schlagwort(titel):
    """Längstes großgeschriebenes Wort der Schlagzeile für den Typo-Block."""
    worte = re.findall(r"[A-ZÄÖÜ][\wÄÖÜäöüß-]{2,}", titel)
    worte = [w.strip("-") for w in worte if w not in FUELLWORTE]
    if not worte:
        worte = re.findall(r"[\wÄÖÜäöüß-]{3,}", titel) or ["Knall"]
    return max(worte, key=len)


def zerlegen(meta, body):
    """→ (titel, absätze, quellen-html)"""
    lines = body.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    titel = ohne_markdown(lines[0]).strip().strip("*_ ") if lines else str(meta.get("thema") or "")
    if titel.lower().startswith("titel:"):
        titel = titel[6:].strip()
    text = ohne_markdown("\n".join(lines[1:]).strip())
    absaetze = [a.strip() for a in re.split(r"\n\s*\n", text) if a.strip()]
    quellen = []
    for q in meta.get("quellen") or []:
        if isinstance(q, dict) and safe_url(q.get("url")):
            quellen.append(
                f'<li><a href="{e(safe_url(q["url"]))}" rel="nofollow noopener">'
                f'{e(q.get("titel") or q["url"])}</a>'
                + (f' <span class="medium">{e(q["medium"])}</span>' if q.get("medium") else "")
                + "</li>"
            )
    return titel or str(meta.get("thema") or ""), absaetze, quellen


def teaser(absaetze):
    t = absaetze[0] if absaetze else ""
    if len(t) <= TEASER_ZEICHEN:
        return t
    cut = t[:TEASER_ZEICHEN].rsplit(" ", 1)[0]
    return cut.rstrip(",;:–-") + " …"


def render(meta, body, aufmacher=False, neu=False):
    titel, absaetze, quellen = zerlegen(meta, body)
    slug = ressort_slug(meta.get("ressort"))
    klasse = "aufmacher" if aufmacher else "kachel"
    stoerer = []
    if aufmacher:
        stoerer.append('<span class="stoerer knall">Knall!</span>')
    if neu:
        stoerer.append('<span class="stoerer neu">Neu</span>')
    wort = schlagwort(titel)
    rest = absaetze[1:] if absaetze else []
    volltext = "".join(f"<p>{e(a)}</p>" for a in rest)
    return (
        f'<article class="{klasse}" data-ressort="{e(slug)}">\n'
        f'<div class="typo r-{e(slug)}" aria-hidden="true"><span style="--n:{max(len(wort), 5)}">{e(wort)}</span></div>\n'
        f'<div class="inhalt">\n'
        f'<div class="dach">{"".join(stoerer)}<span class="ressort">{e(ressort_name(meta.get("ressort")))}</span></div>\n'
        f"<h2>{e(titel)}</h2>\n"
        f'<p class="teaser">{e(absaetze[0] if aufmacher and absaetze else teaser(absaetze))}</p>\n'
        f'<details><summary>Weiterlesen</summary>\n'
        # Kachel: Teaser ist evtl. gekürzt → beim Aufklappen voller erster
        # Absatz (Teaser wird dann per CSS ausgeblendet)
        + (f'<p class="lead">{e(absaetze[0])}</p>' if not aufmacher and absaetze else "")
        + f'<div class="body">{volltext}</div>\n'
        + (f'<div class="quellen-titel">Quellen</div><ul class="quellen">{"".join(quellen)}</ul>\n' if quellen else "")
        + "</details>\n"
        f'<div class="meta">{e(datum_lang(meta.get("datum", "")))} · KI-generiert, maschinell geprüft</div>\n'
        "</div>\n</article>"
    )


def ersetze(page, name, inhalt, pflicht=False):
    start, end = f"<!-- {name} -->", f"<!-- /{name} -->"
    if start not in page or end not in page:
        if pflicht:
            raise SystemExit(f"Marker {name} fehlen in site/index.html")
        return page
    pre, rest = page.split(start, 1)
    _, post = rest.split(end, 1)
    return f"{pre}{start}{inhalt}{end}{post}"


def main():
    target = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "site" / "index.html"
    page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    files = sorted((ROOT / "published").glob("*.md"), reverse=True)[:MAX_ARTIKEL]
    artikel = [parse(p.read_text(encoding="utf-8")) for p in files]
    heute = dt.date.today()

    blocks, ticker, ressorts = [], [], {}
    for i, (meta, body) in enumerate(artikel):
        neu = str(meta.get("datum", ""))[:10] == heute.isoformat()
        blocks.append(render(meta, body, aufmacher=(i == 0), neu=neu))
        titel = zerlegen(meta, body)[0]
        if len(ticker) < MAX_TICKER:
            ticker.append(f"<span>{e(titel)}</span>")
        ressorts.setdefault(ressort_slug(meta.get("ressort")), ressort_name(meta.get("ressort")))

    if blocks:
        inner = blocks[0] + '\n<div class="raster">\n' + "\n".join(blocks[1:]) + "\n</div>"
    else:
        inner = ('<p class="leer">Noch keine Artikel. Die Pipeline läuft täglich '
                 'um 06:00 — morgen knallt’s.</p>')
    page = ersetze(page, "ARTIKEL", f"\n{inner}\n", pflicht=True)

    tick = "".join(ticker) or "<span>Knall — das linke Boulevard</span>"
    # Laufband doppelt, damit die Animation nahtlos durchläuft
    page = ersetze(page, "TICKER", f'<div class="lauf">{tick}{tick}</div>')
    knoepfe = ['<button type="button" class="aktiv" data-filter="">Alles</button>']
    knoepfe += [f'<button type="button" data-filter="{e(s)}">{e(n)}</button>'
                for s, n in sorted(ressorts.items(), key=lambda x: x[1])]
    page = ersetze(page, "RESSORTS", "".join(knoepfe))
    page = ersetze(page, "DATUM", e(f"{WOCHENTAGE[heute.weekday()]}, {datum_lang(heute.isoformat())}"))

    target.write_text(page, encoding="utf-8")
    print(f"Seite gebaut: {len(blocks)} Artikel → {target}")


if __name__ == "__main__":
    main()
