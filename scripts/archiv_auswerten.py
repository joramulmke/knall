"""
archiv_auswerten.py — Privates Fehler-Archiv lokal entschlüsseln und als
lesbaren Bericht ausgeben (abgelehnte Artikel mit allen Fassungen, Gründen,
KI2-Befunden, Sonderprüfungen und Quelltexten).

Ablauf:
  1. GitHub → Actions → "Fehler-Archiv exportieren" → Run workflow
  2. Artefakt "knall-archiv-verschluesselt" herunterladen und entpacken
     (enthält work.tar.enc)
  3. python scripts/archiv_auswerten.py work.tar.enc
     → fragt nach dem OpenRouter-API-Key (wird nicht gespeichert)
     → schreibt archiv_bericht.md (+ entpackt nach ./archiv_export/)

Benötigt: Python 3, openssl, tar. Der Bericht enthält abgelehnte Texte —
nicht veröffentlichen.
"""
import getpass
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import io


def entschluesseln(enc_path, api_key, ziel):
    pw = hashlib.sha256(api_key.encode()).hexdigest()
    env = dict(os.environ, KNALL_CACHE_PASS=pw)
    res = subprocess.run(
        ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
         "-pass", "env:KNALL_CACHE_PASS", "-in", str(enc_path)],
        capture_output=True, env=env,
    )
    if res.returncode != 0:
        raise SystemExit("Entschluesseln fehlgeschlagen — falscher API-Key oder Datei beschaedigt.")
    ziel.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(res.stdout), mode="r:gz") as tar:
        tar.extractall(ziel, filter="data")


def zitat(text):
    return "\n".join("> " + z for z in str(text or "").splitlines()) or "> (leer)"


def bericht(eintraege):
    out = ["# Knall — Fehler-Archiv (abgelehnte Artikel)", "",
           f"{len(eintraege)} Einträge. Enthält abgelehnte Texte — nicht veröffentlichen.", ""]
    gruende = {}
    for _, d in eintraege:
        for g in d.get("ablehnungsgruende") or []:
            k = str(g).split(":")[0][:60]
            gruende[k] = gruende.get(k, 0) + 1
    out += ["## Häufigste Gründe", ""]
    out += [f"- {n}× {g}" for g, n in sorted(gruende.items(), key=lambda x: -x[1])] + [""]

    for name, d in eintraege:
        cl = d.get("cluster") or {}
        out += [f"## {name}", "",
                f"**Thema:** {cl.get('thema')} · **Ressort:** {cl.get('ressort')} · **Stand:** {d.get('stage')}", "",
                "**Gründe:** " + "; ".join(str(g) for g in d.get("ablehnungsgruende") or []), ""]
        sc = d.get("special_checks") or {}
        if sc and not sc.get("bestaende"):
            out += [f"**Sonderprüfung:** Zahlen {sc.get('zahlen_fehler')}, Namen {sc.get('entities_fehler')}, "
                    f"Plagiat {bool(sc.get('plagiat'))}, Injection {bool(sc.get('injection'))}, leer {sc.get('leer')}", ""]
        prot = d.get("ki2_protokoll") or {}
        rote = [s for s in prot.get("saetze") or [] if isinstance(s, dict) and s.get("befund") in ("rot", "gelb")]
        if rote:
            out += ["**KI2-Befunde (letzte Prüfung):**", ""]
            for s in rote:
                for v in s.get("verstoesse") or [{}]:
                    out.append(f"- Satz {s.get('satz_nr')} [{s.get('befund')}] {v.get('pruefposten', '')}: "
                               f"{v.get('erklaerung', '')} — Quelle: {v.get('quellenstelle', '')}")
            out.append("")
        ki3 = d.get("ki3_entscheidung") or {}
        if ki3:
            out += [f"**KI3:** {ki3.get('entscheidung')} — {ki3.get('begruendung', '')}", ""]
        out += ["**Letzter Text:**", "", zitat(d.get("text")), ""]
        for v in d.get("verlauf") or []:
            if "text" in v and v.get("stufe") != "ki1-entwurf":
                continue
            if v.get("stufe") == "ki1-entwurf" and v.get("text") != d.get("text"):
                out += ["<details><summary>Erster KI1-Entwurf</summary>", "", zitat(v["text"]), "", "</details>", ""]
        stufen = " → ".join(f"{v.get('stufe')}" + (f" ({v.get('ampel') or v.get('entscheidung')})"
                                                   if v.get("ampel") or v.get("entscheidung") else "")
                            for v in d.get("verlauf") or [])
        if stufen:
            out += [f"**Verlauf:** {stufen}", ""]
        out += ["<details><summary>Quelltexte</summary>", "", "```", str(d.get("quellen_text") or ""), "```",
                "", "</details>", "", "---", ""]
    return "\n".join(out)


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Aufruf: python scripts/archiv_auswerten.py work.tar.enc")
    enc = pathlib.Path(sys.argv[1])
    key = os.environ.get("OPENROUTER_API_KEY") or getpass.getpass("OpenRouter-API-Key: ")
    ziel = pathlib.Path("archiv_export")
    entschluesseln(enc, key.strip(), ziel)
    eintraege = []
    for f in sorted((ziel / "archiv" / "abgelehnt").glob("*.json"), reverse=True):
        eintraege.append((f.stem, json.loads(f.read_text(encoding="utf-8"))))
    out = pathlib.Path("archiv_bericht.md")
    out.write_text(bericht(eintraege), encoding="utf-8")
    print(f"{len(eintraege)} abgelehnte Artikel → {out} (Rohdaten in {ziel}/)")


if __name__ == "__main__":
    main()
