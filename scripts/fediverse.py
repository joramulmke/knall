"""
fediverse.py — Stellt neue Artikel als Fediverse-Posts zu (Schlagzeile,
Teaser, Link).

Ablauf: Artikel der letzten Tage an den Dienst schicken (POST /api/notes).
Der Dienst speichert nur neue und gibt deren "Create"-Aktivitäten zurück.
Diese stellt das Skript signiert an alle Follower-Server zu.

Umgebung (fehlt etwas → still überspringen, Pipeline läuft weiter):
  FEDI_ENDPOINT     Adresse des Dienstes (Secret)
  FEDI_TOKEN        Zugangstoken (Secret)
  FEDI_PRIVATE_KEY  privater Schlüssel, PEM (Secret)
  KNALL_URL         öffentliche Adresse der Seite
Signiert wird mit openssl (auf dem Runner vorhanden) — keine neue Abhängigkeit.
"""
import base64
import datetime as dt
import email.utils
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import urllib.parse

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_site import anker, parse, safe_url, teaser, zeitstempel, zerlegen  # noqa: E402

TAGE_ZURUECK = 2   # verpasste Läufe nachholen; der Dienst filtert Doppelte
TIMEOUT = 20
AS = "application/activity+json"


def artikel(base, heute):
    grenze = (heute - dt.timedelta(days=TAGE_ZURUECK)).isoformat()
    items = []
    for p in sorted((ROOT / "published").glob("*.md")):
        meta, body = parse(p.read_text(encoding="utf-8"))
        datum = str(meta.get("datum", ""))[:10]
        if datum < grenze:
            continue
        titel, absaetze, _ = zerlegen(meta, body)
        id_ = anker(p.stem)
        items.append({"id": id_, "titel": titel, "teaser": teaser(absaetze),
                      "url": f"{base}#{id_}", "published": zeitstempel(datum)})
    return items


def signier_text(method, url, date, digest):
    u = urllib.parse.urlsplit(url)
    pfad = u.path + (f"?{u.query}" if u.query else "")
    return (f"(request-target): {method.lower()} {pfad}\nhost: {u.netloc}\n"
            f"date: {date}\ndigest: {digest}")


def signiere(key_path, text):
    out = subprocess.run(["openssl", "dgst", "-sha256", "-sign", key_path],
                         input=text.encode(), capture_output=True, check=True).stdout
    return base64.b64encode(out).decode()


def zustellen(key_path, key_id, inbox, aktivitaet):
    body = json.dumps(aktivitaet).encode()
    date = email.utils.formatdate(usegmt=True)
    digest = "SHA-256=" + base64.b64encode(hashlib.sha256(body).digest()).decode()
    sig = signiere(key_path, signier_text("POST", inbox, date, digest))
    headers = {
        "Date": date, "Digest": digest, "Content-Type": AS, "Accept": AS,
        "Signature": (f'keyId="{key_id}",algorithm="rsa-sha256",'
                      f'headers="(request-target) host date digest",signature="{sig}"'),
    }
    return requests.post(inbox, data=body, headers=headers, timeout=TIMEOUT, allow_redirects=False)


def main():
    endpoint = safe_url(os.environ.get("FEDI_ENDPOINT"))
    token = os.environ.get("FEDI_TOKEN", "")
    key = os.environ.get("FEDI_PRIVATE_KEY", "")
    base = safe_url(os.environ.get("KNALL_URL"))
    if not (endpoint and token and key and base):
        print("Fediverse: nicht eingerichtet — übersprungen")
        return
    endpoint = endpoint.rstrip("/")
    base = base.rstrip("/") + "/"
    items = artikel(base, dt.date.today())
    if not items:
        print("Fediverse: keine neuen Artikel")
        return
    auth = {"Authorization": f"Bearer {token}"}
    r = requests.post(f"{endpoint}/api/notes", json=items, headers=auth, timeout=TIMEOUT)
    r.raise_for_status()
    antwort = r.json()
    neu = antwort.get("neu") or []
    print(f"Fediverse: {len(items)} Artikel geprüft, {len(neu)} neu")
    if not neu:
        return
    r = requests.get(f"{endpoint}/api/inboxes", headers=auth, timeout=TIMEOUT)
    r.raise_for_status()
    inboxes = [i for i in r.json().get("inboxes", []) if safe_url(i)]
    print(f"Fediverse: Zustellung an {len(inboxes)} Server")
    ok = fehler = 0
    with tempfile.TemporaryDirectory() as d:
        key_path = os.path.join(d, "key.pem")
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(key)
        for akt in neu:
            for inbox in inboxes:
                try:
                    res = zustellen(key_path, antwort["keyId"], inbox, akt)
                    if res.status_code < 300:
                        ok += 1
                    else:
                        fehler += 1
                        print(f"  {urllib.parse.urlsplit(inbox).netloc}: HTTP {res.status_code}")
                except (requests.RequestException, subprocess.CalledProcessError) as exc:
                    fehler += 1
                    print(f"  {urllib.parse.urlsplit(inbox).netloc}: {type(exc).__name__}")
    print(f"Fediverse: {ok} zugestellt, {fehler} fehlgeschlagen")


if __name__ == "__main__":
    main()
