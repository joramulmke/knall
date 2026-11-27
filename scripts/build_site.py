"""
build_site.py — Rendert published/*.md in site/index.html im Boulevard-Layout:
Aufmacher (neuester Artikel) + Kachelraster, Laufband, Ressort-Leiste.

Die Vorlage site/index.html enthält Marker-Paare, deren Inhalt ersetzt wird:
  <!-- DATUM -->…<!-- /DATUM -->        Datumszeile im Kopf
  <!-- TICKER -->…<!-- /TICKER -->      Laufband mit Schlagzeilen
  <!-- RESSORTS -->…<!-- /RESSORTS -->  Ressort-Filter
  <!-- ARTIKEL -->…<!-- /ARTIKEL -->    Aufmacher + Raster (Pflicht)
  <!-- META -->…<!-- /META -->          Beschreibung, Vorschaukarte (og:), JSON-LD

Daneben entstehen feed.xml (Atom), sitemap.xml und robots.txt. Absolute
Adressen kommen aus der Umgebungsvariable KNALL_URL (setzt der Workflow);
ohne sie fehlen og:url/canonical und der Feed nutzt relative Links.

Sicherheit: Jeder Text aus LLM/Quellen wird HTML-escaped; Links nur mit
http(s)-Schema. Kein Markdown-Rendering von Rohtext → kein XSS.
"""
import datetime as dt
import html
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAX_ARTIKEL = 30
MAX_TICKER = 8
TEASER_ZEICHEN = 220
MAX_FEED = 30
SITE_TITEL = "Knall — Das linke Boulevard"
SITE_BESCHREIBUNG = ("Linke Nachrichten im Boulevard-Format: Arbeit, Miete, Pflege, "
                     "Klima und Ostdeutschland — automatisch aus verlinkten Quellen, "
                     "mehrstufig geprüft.")

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
    # Ältere/abweichende Dateien: Knaller-Zeile im Text → in die Metadaten
    body, knaller = knaller_abtrennen(body)
    if knaller and not meta.get("knaller"):
        meta["knaller"] = knaller
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


# Satzanfänge, die als Schlagwort nichts taugen (am Titelanfang groß geschrieben)
STARTWORTE = {
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer", "eines", "einen", "einem",
    "im", "in", "am", "an", "auf", "nach", "vor", "trotz", "wegen", "bei", "mit", "für", "über",
    "unter", "aus", "zum", "zur", "vom", "so", "wie", "wer", "was", "wo", "warum", "jetzt", "heute",
    "neue", "neuer", "neues", "neuen", "mehr", "alle", "kein", "keine", "weil", "wenn", "und",
    "oder", "doch", "aber", "statt", "gegen", "seit", "bis", "ohne", "um", "hunderte", "tausende",
    "dutzende", "erst", "noch", "schon", "wieder",
}
EINHEITEN = (r"%|Prozent|Kilogramm|Kilo|Jahren|Jahre|Euro|Millionen|Milliarden|Stunden|Tagen|Tage|"
             r"Wochen|Monaten|Monate|Cent|Grad|Kilometer|Meter")
ZAHL_EINHEIT_RX = re.compile(
    r"(?<![\w,.])(\d{1,3}(?:[.\u00a0\u202f ]\d{3})*(?:,\d+)?)[\s\u00a0\u202f\u2010\u2011-]*(" + EINHEITEN + r")(?![\wäöüß])")
# "Knaller: …" — Schlagwort-Zeile von KI1 (zweite Zeile nach dem Titel),
# läuft wie der Titel durch alle Prüfungen; hier wird sie abgetrennt.
KNALLER_RX = re.compile(r"(?mi)^[ \t]*(?:\*\*|__)?knaller(?:\*\*|__)?[ \t]*:[ \t]*(.*?)[ \t]*(?:\n|$)")
KNALLER_MAX_WORTE = 3
KNALLER_MAX_ZEICHEN = 24


def knaller_abtrennen(text):
    """→ (text ohne Knaller-Zeile, knaller oder ""). Eine zu lange oder leere
    Zeile wird trotzdem entfernt, der Wert dann verworfen (→ Heuristik)."""
    text = str(text or "")
    m = KNALLER_RX.search(text)
    if not m:
        return text, ""
    wert = ohne_markdown(m.group(1)).strip().strip("*_\"'„“”‚‘’«»").strip().rstrip(".!").strip()
    rest = re.sub(r"\n{3,}", "\n\n", text[: m.start()] + text[m.end():]).strip()
    worte = [w for w in wert.split() if w not in ("%", "–", "-")]
    if not worte or len(worte) > KNALLER_MAX_WORTE or len(wert) > KNALLER_MAX_ZEICHEN:
        wert = ""
    return rest, wert


def schlagwort(titel):
    """Schlagwort für den Typo-Block, wenn KI1 keinen Knaller geliefert hat:
    1. harte Zahl mit Einheit aus dem Titel ("4,3 %", "20 Kilo", "10 Jahre"),
    2. sonst das Subjekt am Titelanfang; bei Bindestrich-Wörtern der erste
       Teil ("Amazon-Beschäftigte" → "Amazon"),
    3. sonst wie früher das längste großgeschriebene Wort."""
    titel = str(titel or "")
    m = ZAHL_EINHEIT_RX.search(titel)
    if m:
        zahl, einheit = m.group(1), m.group(2)
        return f"{zahl}\u202f%" if einheit in ("%", "Prozent") else f"{zahl} {einheit}"
    for tok in re.findall(r"[\wÄÖÜäöüß][\wÄÖÜäöüß.\u2010\u2011-]*", titel):
        tok = tok.strip(".-")
        if tok.lower() in STARTWORTE or tok in FUELLWORTE or not tok[:1].isupper():
            continue
        teile = [t for t in re.split(r"[-\u2010\u2011]", tok) if len(t) >= 3 and t[:1].isupper()]
        if teile:
            return teile[0]
        if len(tok) >= 3:
            return tok
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


def anker(stem):
    """Stabile Sprungmarke je Artikel (aus dem Dateinamen) — für Feed-Links."""
    return "a-" + (re.sub(r"[^a-z0-9-]", "", str(stem).lower()) or "artikel")


def render(meta, body, aufmacher=False, neu=False, id_=None):
    titel, absaetze, quellen = zerlegen(meta, body)
    slug = ressort_slug(meta.get("ressort"))
    klasse = "aufmacher" if aufmacher else "kachel"
    stoerer = []
    if aufmacher:
        stoerer.append('<span class="stoerer knall">Knall!</span>')
    if neu:
        stoerer.append('<span class="stoerer neu">Neu</span>')
    wort = str(meta.get("knaller") or "").strip()[:KNALLER_MAX_ZEICHEN] or schlagwort(titel)
    rest = absaetze[1:] if absaetze else []
    volltext = "".join(f"<p>{e(a)}</p>" for a in rest)
    return (
        f'<article class="{klasse}" data-ressort="{e(slug)}"'
        + (f' id="{e(id_)}"' if id_ else "") + '>\n'
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


def site_url():
    u = safe_url(os.environ.get("KNALL_URL"))
    return u.rstrip("/") + "/" if u else ""


def kopf_meta(base, beschreibung, artikel_ld):
    """Meta-Tags für Suchmaschinen und Vorschaukarten (Mastodon, Messenger)."""
    t = [
        f'<meta name="description" content="{e(beschreibung)}">',
        '<meta name="theme-color" content="#6d4aff">',
        '<link rel="icon" href="icon.png" type="image/png">',
        '<link rel="apple-touch-icon" href="icon.png">',
        f'<link rel="alternate" type="application/atom+xml" title="{e(SITE_TITEL)}" href="{e(base)}feed.xml">',
        '<meta property="og:type" content="website">',
        '<meta property="og:locale" content="de_DE">',
        '<meta property="og:site_name" content="Knall">',
        f'<meta property="og:title" content="{e(SITE_TITEL)}">',
        f'<meta property="og:description" content="{e(beschreibung)}">',
        f'<meta property="og:image" content="{e(base)}og.png">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        '<meta property="og:image:alt" content="Schriftzug KNALL — Das linke Boulevard">',
        '<meta name="twitter:card" content="summary_large_image">',
    ]
    if base:
        t += [f'<link rel="canonical" href="{e(base)}">',
              f'<meta property="og:url" content="{e(base)}">']
    ld = {"@context": "https://schema.org", "@type": "WebSite", "name": "Knall",
          "alternateName": SITE_TITEL, "description": SITE_BESCHREIBUNG, "inLanguage": "de"}
    if base:
        ld["url"] = base
    if artikel_ld:
        ld = [ld, {"@context": "https://schema.org", "@type": "ItemList",
                   "itemListElement": [dict(a, position=i + 1) for i, a in enumerate(artikel_ld)]}]
    # "</" im JSON escapen, damit kein Text das <script> schließen kann
    js = json.dumps(ld, ensure_ascii=False).replace("</", "<\\/")
    t.append(f'<script type="application/ld+json">{js}</script>')
    return "\n" + "\n".join(t) + "\n"


def atom_feed(base, eintraege, jetzt):
    """Atom-Feed: Titel, Teaser und Volltext (HTML-escaped) je Artikel."""
    def x(t):
        return html.escape(str(t or ""), quote=False)
    self_url = f"{base}feed.xml" if base else "feed.xml"
    teile = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="de">',
        f"<title>{x(SITE_TITEL)}</title>",
        f"<subtitle>{x(SITE_BESCHREIBUNG)}</subtitle>",
        f'<link rel="alternate" type="text/html" href="{e(base or "./")}"/>',
        f'<link rel="self" type="application/atom+xml" href="{e(self_url)}"/>',
        f"<id>{x(base or 'urn:knall:feed')}</id>",
        f"<updated>{jetzt}</updated>",
        "<author><name>Knall</name></author>",
        f'<icon>{x(base)}icon.png</icon>',
        f'<logo>{x(base)}og.png</logo>',
    ]
    for a in eintraege:
        inhalt = "".join(f"<p>{e(p)}</p>" for p in a["absaetze"])
        if a["quellen"]:
            inhalt += "<p>Quellen:</p><ul>" + "".join(a["quellen"]) + "</ul>"
        link = f"{base}#{a['id']}"
        teile += [
            "<entry>",
            f"<title>{x(a['titel'])}</title>",
            f'<link rel="alternate" type="text/html" href="{e(link)}"/>',
            f"<id>{x(link if base else 'urn:knall:' + a['id'])}</id>",
            f"<published>{a['zeit']}</published>",
            f"<updated>{a['zeit']}</updated>",
            f'<category term="{e(a["ressort"])}"/>',
            f"<summary>{x(a['teaser'])}</summary>",
            f'<content type="html">{x(inhalt)}</content>',
            "</entry>",
        ]
    teile.append("</feed>")
    return "\n".join(teile) + "\n"


def zeitstempel(iso):
    try:
        d = dt.date.fromisoformat(str(iso)[:10])
    except ValueError:
        d = dt.date.today()
    return f"{d.isoformat()}T06:00:00Z"


def main():
    target = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "site" / "index.html"
    page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    files = sorted((ROOT / "published").glob("*.md"), reverse=True)[:MAX_ARTIKEL]
    artikel = [(p.stem, *parse(p.read_text(encoding="utf-8"))) for p in files]
    heute = dt.date.today()
    base = site_url()

    blocks, ticker, ressorts, feed, artikel_ld = [], [], {}, [], []
    for i, (stem, meta, body) in enumerate(artikel):
        neu = str(meta.get("datum", ""))[:10] == heute.isoformat()
        id_ = anker(stem)
        blocks.append(render(meta, body, aufmacher=(i == 0), neu=neu, id_=id_))
        titel, absaetze, quellen = zerlegen(meta, body)
        if len(feed) < MAX_FEED:
            feed.append({"id": id_, "titel": titel, "absaetze": absaetze, "quellen": quellen,
                         "teaser": teaser(absaetze), "zeit": zeitstempel(meta.get("datum")),
                         "ressort": ressort_name(meta.get("ressort"))})
            ld = {"@type": "ListItem", "name": titel}
            if base:
                ld["url"] = f"{base}#{id_}"
            artikel_ld.append(ld)
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
    beschreibung = SITE_BESCHREIBUNG
    page = ersetze(page, "META", kopf_meta(base, beschreibung, artikel_ld))

    target.write_text(page, encoding="utf-8")
    ziel = target.parent
    jetzt = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (ziel / "feed.xml").write_text(atom_feed(base, feed, jetzt), encoding="utf-8")
    robots = "User-agent: *\nAllow: /\n"
    if base:
        robots += f"Sitemap: {base}sitemap.xml\n"
        (ziel / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"<url><loc>{e(base)}</loc><lastmod>{heute.isoformat()}</lastmod>"
            "<changefreq>hourly</changefreq></url>\n</urlset>\n", encoding="utf-8")
    (ziel / "robots.txt").write_text(robots, encoding="utf-8")
    print(f"Seite gebaut: {len(blocks)} Artikel, Feed {len(feed)} Einträge → {target}")


if __name__ == "__main__":
    main()
