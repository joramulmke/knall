"""
collect.py — Holt RSS-Feeds, dedupliziert, archiviert in sources/YYYY-MM-DD/.
"""
import json
import hashlib
import datetime
import pathlib
import yaml
import feedparser
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from safety import clean_text  # noqa: E402
from common import run_day  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = yaml.safe_load((ROOT / "config" / "feeds.yml").read_text(encoding="utf-8"))
DAY = run_day()
OUT = ROOT / "sources" / DAY
OUT.mkdir(parents=True, exist_ok=True)


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
        fulltext_ok = len(summary) > 400
        item = {
            "id": iid,
            "source_key": feed_key,
            "ressort": feed_def.get("ressort", "all"),
            "region": feed_def.get("region"),
            "url": link,
            "title": clean_text(getattr(entry, "title", "")),
            "summary": summary,
            "published": getattr(entry, "published", ""),
            "collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "vollstaendig": fulltext_ok,
            "score": None,
        }
        path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        saved += 1
    return saved


def main():
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
