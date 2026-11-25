"""
build_site.py — Rendert published/*.md in site/index.html (zwischen den
Markern <!-- ARTIKEL --> und <!-- /ARTIKEL -->).

Sicherheit: Jeder Text aus LLM/Quellen wird HTML-escaped; Links nur mit
http(s)-Schema. Kein Markdown-Rendering von Rohtext → kein XSS.
"""
import html
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAX_ARTIKEL = 30
START, END = "<!-- ARTIKEL -->", "<!-- /ARTIKEL -->"


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


def safe_url(u):
    u = str(u or "").strip()
    return u if re.match(r"^https?://", u, re.I) else None


def render(meta, body):
    lines = [l for l in body.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    titel = lines[0].lstrip("# ").strip() if lines else str(meta.get("thema") or "")
    text = "\n".join(lines[1:]).strip()
    quellen = []
    for q in meta.get("quellen") or []:
        if isinstance(q, dict) and safe_url(q.get("url")):
            quellen.append(
                f'<li><a href="{html.escape(safe_url(q["url"]), quote=True)}" rel="nofollow noopener">'
                f'{html.escape(str(q.get("titel") or q["url"]))}</a></li>'
            )
    return (
        "<article>\n"
        f"<h2>{html.escape(titel)}</h2>\n"
        f'<div class="meta">{html.escape(str(meta.get("datum", "")))} · {html.escape(str(meta.get("ressort") or ""))}'
        " · KI-generiert, maschinell geprüft</div>\n"
        f'<div class="body">{html.escape(text)}</div>\n'
        + (f'<ul class="quellen">{"".join(quellen)}</ul>\n' if quellen else "")
        + "</article>"
    )


def main():
    target = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "site" / "index.html"
    page = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    files = sorted((ROOT / "published").glob("*.md"), reverse=True)[:MAX_ARTIKEL]
    blocks = [render(*parse(p.read_text(encoding="utf-8"))) for p in files]
    inner = "\n".join(blocks) if blocks else "<p>Noch keine Artikel. Die Pipeline läuft täglich um 06:00.</p>"
    if START not in page or END not in page:
        raise SystemExit("Marker fehlen in site/index.html")
    pre, rest = page.split(START, 1)
    _, post = rest.split(END, 1)
    target.write_text(f"{pre}{START}\n{inner}\n{END}{post}", encoding="utf-8")
    print(f"Seite gebaut: {len(blocks)} Artikel → {target}")


if __name__ == "__main__":
    main()
