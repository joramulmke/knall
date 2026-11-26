"""
collect.py — Holt RSS-Feeds, dedupliziert, archiviert in sources/YYYY-MM-DD/.

Hier entstehen nur Teaser. Den Volltext und die Original-URL (bei
Google-News-Spiegeln) holt danach fulltext.py — nur für die besten Kandidaten.
"""
import json
import hashlib
import socket
import datetime
import pathlib
import yaml
import feedparser
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from safety import clean_text  # noqa: E402
from common import run_day, medium_kurz  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = yaml.safe_load((ROOT / "config" / "feeds.yml").read_text(encoding="utf-8"))
DAY = run_day()
OUT = ROOT / "sources" / DAY
OUT.mkdir(parents=True, exist_ok=True)
# feedparser kennt keinen eigenen Timeout. Ohne diese Grenze hängt ein
# blockierender Server pro Feed gut 2 Minuten im TCP-Timeout.
FEED_TIMEOUT = 15


def item_id(url):
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def mirror_feed_url(query):
    import urllib.parse
    q = urllib.parse.quote_plus(query)
    return f"https://news.google.com/rss/search?q={q}&hl=de&gl=DE&ceid=DE:de"


def fetch(feed_key, feed_def):
    if feed_def.get("aktiv") is False:
        return "deaktiviert"
    if feed_def.get("type") == "google-news-mirror":
        url = mirror_feed_url(feed_def["query"])
    elif feed_def.get("url"):
        url = feed_def["url"]
    else:
        return "uebersprungen (keine url; url_pattern erst verifizieren)"
    parsed = feedparser.parse(url)
    if not parsed.entries:
        status = getattr(parsed, "status", "?")
        reason = getattr(parsed, "bozo_exception", "")
        print(f"::warning::{feed_key}: 0 Eintraege (HTTP {status}) {reason}")
    is_mirror = feed_def.get("type") == "google-news-mirror"
    feed_title = clean_text(getattr(parsed, "feed", {}).get("title", "")) if not is_mirror else ""
    saved = 0
    for entry in parsed.entries[:25]:
        link = getattr(entry, "link", None)
        if not link:
            continue
        iid = item_id(link)
        path = OUT / f"{iid}.json"
        if path.exists():
            continue
        # Google-News-Artikel haben oft nur Titel/Zusammenfassung — Vollständigkeit prüfen
        summary = clean_text(getattr(entry, "summary", ""))
        title = clean_text(getattr(entry, "title", ""))
        # Echter Name des Mediums: bei Google News aus <source>, sonst aus
        # der Konfiguration (name:) oder dem Feed-Titel
        if is_mirror:
            src = getattr(entry, "source", None) or {}
            medium = clean_text(src.get("title", "") if hasattr(src, "get") else "")
            if medium and title.endswith(" - " + medium):
                title = title[: -len(" - " + medium)].strip()
        else:
            medium = clean_text(feed_def.get("name") or feed_title)
        item = {
            "id": iid,
            "source_key": feed_key,
            "ressort": feed_def.get("ressort", "all"),
            "region": feed_def.get("region"),
            "datum_vom_feed": feed_def.get("datum") == "feed",
            "url": link,                      # Feed-Link (bei Spiegeln: Google-Weiterleitung)
            "google_news": is_mirror,
            "medium": medium_kurz(medium),
            "title": title,
            "summary": summary,
            "published": getattr(entry, "published", ""),
            "published_iso": _iso(getattr(entry, "published_parsed", None)
                                  or getattr(entry, "updated_parsed", None)),
            "collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "vollstaendig": False,            # erst fulltext.py setzt True (Originalartikel gelesen)
            "score": None,
        }
        path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        saved += 1
    return saved


def _iso(t):
    """feedparser-struct_time → ISO-Datum (UTC) oder None."""
    try:
        return datetime.datetime(*t[:6], tzinfo=datetime.timezone.utc).isoformat()
    except Exception:
        return None


def main():
    socket.setdefaulttimeout(FEED_TIMEOUT)
    total = 0
    for key, fdef in CONFIG.get("definitions", {}).items():
        try:
            n = fetch(key, fdef)
            if isinstance(n, str):
                print(f"{key}: {n}")
                continue
            print(f"{key}: {n} neu")
            total += n
        except Exception as exc:
            print(f"{key}: FEHLER {exc}")
    print(f"Gesamt neu: {total}")


if __name__ == "__main__":
    main()
