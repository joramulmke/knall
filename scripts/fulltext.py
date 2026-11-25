"""
fulltext.py — Holt für die besten Teaser des Tages den ORIGINALARTIKEL:
Original-URL (Google-News-Weiterleitungen werden aufgelöst) und Volltext.

Grundsatz: Geschrieben wird nur aus Originalartikeln. Ein Teaser, dessen
Original-URL oder Volltext nicht zu bekommen ist (Paywall, robots.txt,
Abruffehler), kommt NICHT weiter (advance=False nach score.py).

- Nur die Top-Kandidaten nach Teaser-Score (KNALL_VOLLTEXT_MAX, max.
  KNALL_VOLLTEXT_PRO_FEED pro Feed) — schont die Server.
- robots.txt wird respektiert, 1 s Pause zwischen Abrufen.
- Duplikate (gleiche Original-URL aus zwei Feeds) werden verworfen.
- Fortsetzbar: bereits bearbeitete Teaser (volltext_status) werden übersprungen.

Danach score.py erneut laufen lassen (bewertet den Volltext).
"""
import base64
import json
import os
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.robotparser

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from safety import clean_text  # noqa: E402
from common import run_day  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAX_ITEMS = int(os.environ.get("KNALL_VOLLTEXT_MAX") or 40)
MAX_PRO_FEED = int(os.environ.get("KNALL_VOLLTEXT_PRO_FEED") or 6)
MIN_ZEICHEN = 800        # darunter: Paywall-Teaser o. ä. → kein Originalartikel
MAX_ZEICHEN = 8000       # Prompt-Länge begrenzen (3 Quellen pro Cluster)
MAX_BYTES = 3_000_000
PAUSE_S = 1.0
UA = "Mozilla/5.0 (compatible; KnallBot/1.0; RSS-Volltext)"
ROBOTS_UA = "KnallBot"

_robots = {}


def erlaubt(url, session):
    """robots.txt des Hosts respektieren (Cache pro Host)."""
    p = urllib.parse.urlsplit(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = session.get(base + "/robots.txt", timeout=15)
            if r.status_code in (401, 403):
                rp.disallow_all = True
            elif r.status_code >= 400:
                rp.allow_all = True
            else:
                rp.parse(r.text.splitlines())
        except requests.RequestException:
            rp.allow_all = True  # wie Browser/Crawler üblich: ohne robots.txt erlaubt
        _robots[base] = rp
    return _robots[base].can_fetch(ROBOTS_UA, url)


def ist_original(url):
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and p.netloc and not p.netloc.endswith("news.google.com")


def google_original_url(url, session):
    """Löst einen news.google.com/rss/articles/<id>-Link zur Original-URL auf.
    Rückgabe: URL oder None."""
    m = re.search(r"/articles/([A-Za-z0-9_-]+)", url)
    if not m:
        return None
    gid = m.group(1)
    # Altes Format: Original-URL steckt direkt im base64-Blob
    try:
        raw = base64.urlsafe_b64decode(gid + "=" * (-len(gid) % 4))
        mm = re.search(rb"https?://[\x21-\x7e]+", raw)
        if mm and b"AU_yqL" not in raw[:12]:
            cand = mm.group(0).decode("ascii", "ignore")
            if ist_original(cand):
                return cand
    except Exception:
        pass
    # Neues Format: Signatur + Zeitstempel von der Artikelseite holen,
    # dann über den batchexecute-Endpunkt auflösen
    r = session.get(f"https://news.google.com/rss/articles/{gid}", timeout=20)
    r.raise_for_status()
    sg = re.search(r'data-n-a-sg="([^"]+)"', r.text)
    ts = re.search(r'data-n-a-ts="([^"]+)"', r.text)
    if not sg or not ts:
        return None
    inner = (
        '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
        f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{gid}",{ts.group(1)},"{sg.group(1)}"]'
    )
    freq = json.dumps([[["Fbv4je", inner, None, "generic"]]])
    r = session.post(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute",
        data={"f.req": freq},
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        timeout=20,
    )
    r.raise_for_status()
    teil = r.text.split("\n\n", 1)[1]
    daten = json.loads(teil)
    cand = json.loads(daten[0][2])[1]
    return cand if ist_original(cand) else None


def volltext(url, session):
    """Rückgabe (text, sitename) oder (None, grund)."""
    import trafilatura
    if not erlaubt(url, session):
        return None, "robots.txt verbietet Abruf"
    r = session.get(url, timeout=25, stream=True)
    if r.status_code >= 400:
        return None, f"HTTP {r.status_code}"
    if "html" not in r.headers.get("Content-Type", "html").lower():
        return None, "keine HTML-Seite"
    body = r.raw.read(MAX_BYTES + 1, decode_content=True)
    if len(body) > MAX_BYTES:
        return None, "Seite zu gross"
    html = body.decode(r.encoding or "utf-8", errors="replace")
    text = trafilatura.extract(html, url=url, favor_precision=True,
                               include_comments=False, include_tables=False)
    text = clean_text(text or "")
    if len(text) < MIN_ZEICHEN:
        return None, f"zu wenig Text ({len(text)} Zeichen, Paywall?)"
    sitename = None
    try:
        meta = trafilatura.extract_metadata(html, default_url=url)
        sitename = clean_text(getattr(meta, "sitename", None) or "") or None
    except Exception:
        pass
    return text[:MAX_ZEICHEN], sitename


def main():
    src_dir = ROOT / "sources" / run_day()
    if not src_dir.exists():
        print("Keine Teaser fuer heute")
        return
    items = []
    for path in sorted(src_dir.glob("*.json")):
        items.append((path, json.loads(path.read_text(encoding="utf-8"))))

    bekannte_urls = {it.get("url_original") for _, it in items if it.get("volltext")}
    erledigt = sum(1 for _, it in items if it.get("volltext_status"))
    offen = [(p, it) for p, it in items if not it.get("volltext_status")]
    offen.sort(key=lambda x: -(x[1].get("score") or 0))

    pro_feed = {}
    for _, it in items:
        if it.get("volltext_status"):
            pro_feed[it["source_key"]] = pro_feed.get(it["source_key"], 0) + 1
    auswahl = []
    for p, it in offen:
        if erledigt + len(auswahl) >= MAX_ITEMS:
            break
        if pro_feed.get(it["source_key"], 0) >= MAX_PRO_FEED:
            continue
        pro_feed[it["source_key"]] = pro_feed.get(it["source_key"], 0) + 1
        auswahl.append((p, it))

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"})
    google_gesperrt = False
    stat = {}
    for path, item in auswahl:
        status = "ok"
        try:
            url = item.get("url", "")
            if item.get("google_news"):
                if google_gesperrt:
                    status = "Google-Aufloesung pausiert (Rate-Limit)"
                    url = None
                else:
                    url = google_original_url(url, session)
                    if not url:
                        status = "Original-URL nicht aufloesbar"
            elif not ist_original(url):
                url, status = None, "keine Original-URL"
            if url:
                if url in bekannte_urls:
                    status = "Duplikat (Original schon vorhanden)"
                else:
                    text, info = volltext(url, session)
                    if text is None:
                        status = info
                    else:
                        item.update({
                            "url_original": url,
                            "volltext": text,
                            "vollstaendig": True,
                            "medium": item.get("medium") or info or urllib.parse.urlsplit(url).netloc,
                        })
                        bekannte_urls.add(url)
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else "?"
            status = f"HTTP {code}"
            if code == 429 and "google.com" in str(exc.request.url if exc.request else ""):
                google_gesperrt = True
        except Exception as exc:
            status = f"Fehler: {str(exc)[:120]}"
        item["volltext_status"] = status
        stat[status.split(" (")[0]] = stat.get(status.split(" (")[0], 0) + 1
        path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{item['id']} [{item['source_key']}]: {status}")
        time.sleep(PAUSE_S)
    print(f"Volltext: {len(auswahl)} abgerufen — " + ", ".join(f"{k}: {v}" for k, v in sorted(stat.items())))


if __name__ == "__main__":
    main()
