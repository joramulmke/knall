"""
ki_client.py — Einheitliche LLM-Aufrufe über OpenRouter, JSON-Zwang.

v5:
- NUR :free-Modelle. Jedes Modell ohne ":free"-Suffix wird verweigert.
- Fallback-Kette wird zur Laufzeit gegen openrouter.ai/api/v1/models
  abgeglichen: nicht (mehr) existierende Modelle fliegen raus.
- Reasoning-Modelle bekommen reasoning.effort=low + exclude und mehr
  max_tokens, damit content nicht leer bleibt.
- Tageslimit des Free-Tiers (429 "per-day") bricht SOFORT ab
  (QuotaExhausted) statt 30 Minuten zu warten.
- Kein Sleep nach dem letzten Versuch; 400/404 eines Modells → nächstes Modell.
- 429-Antworttext wird bei jedem Versuch geloggt.

v6:
- Eigener Tageszähler (drafts/<tag>-anfragen.json): JEDER HTTP-Versuch zählt,
  429 und andere Fehler werden separat gezählt. Ab KNALL_TAGESLIMIT_ANFRAGEN
  (Default 50, wie das OpenRouter-Free-Kontingent) wird nicht mehr angefragt.
- Rolle "CHECK" für KI2/KI3: andere Modellfamilie als die Schreiberin
  (unabhängigere Prüfung); die Schreib-Modelle bleiben als Backup in der Kette.

Modell-Auflösung: Env-Variablen KI_MODEL_SMALL / KI_MODEL_LARGE /
KI_MODEL_CHECK / KI_FALLBACK_MODELS (kommagetrennt), alle optional.
"""
import os
import json
import sys
import time
import pathlib
import urllib.request
import urllib.error

ROOT = pathlib.Path(__file__).resolve().parent.parent

DEFAULT_SMALL = "google/gemma-4-26b-a4b-it:free"
DEFAULT_LARGE = "google/gemma-4-31b-it:free"
# Prüferin (KI2/KI3): bewusst andere Modellfamilie als KI1. Fällt sie aus,
# übernimmt das Schreib-Modell als Backup (siehe model_chain).
DEFAULT_CHECK = "nvidia/nemotron-3.5-lightning:free"

# Fallback-Kette bei persistentem 429, leeren Antworten oder fehlendem Modell.
DEFAULT_FALLBACK_CHAIN = [
    "nvidia/nemotron-3.5-lightning:free",
    "baidu/ernie-5.5-flash-preview:free",
    "inclusionai/ling-3.2-flash:free",
]

BACKOFF = [20, 60, 120]          # Wartezeit NACH Versuch 1, 2 (und 3 entfällt)
CALL_WAIT_BUDGET_S = 600         # max. Summe aller Wartezeiten pro llm_call
DEFAULT_MAX_TOKENS = 4000        # Free-Modelle: Tokens kosten nichts, Reasoning braucht Platz
# Ein Artikel braucht realistisch ~6, im ungünstigsten Fall ~12 Anfragen.
MIN_BUDGET_NEUER_ARTIKEL = int(os.environ.get("KNALL_MIN_BUDGET_ARTIKEL") or 8)
TAGESLIMIT = int(os.environ.get("KNALL_TAGESLIMIT_ANFRAGEN") or 50)


class EmptyResponse(Exception):
    """Modell antwortete mit leerem content (z. B. nur Reasoning)."""


class QuotaExhausted(RuntimeError):
    """Free-Tier-Tageslimit erreicht — weitere Versuche sind heute sinnlos."""


def env_or(name, default):
    val = (os.environ.get(name) or "").strip()
    return val if val else default


def require_free(model):
    if not str(model).endswith(":free"):
        raise ValueError(f"Nur :free-Modelle erlaubt, bekommen: {model!r}")
    return model


def _zaehler_pfad():
    sys.path.insert(0, str(ROOT / "scripts"))
    from common import run_day
    return ROOT / "drafts" / f"{run_day()}-anfragen.json"


def zaehler():
    """Tageszähler: anfragen (alle HTTP-Versuche), ok, r429, fehler, leer, modelle."""
    f = _zaehler_pfad()
    try:
        z = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except json.JSONDecodeError:
        z = {}
    for k in ("anfragen", "ok", "r429", "fehler", "leer"):
        z.setdefault(k, 0)
    z.setdefault("modelle", {})
    z["limit"] = TAGESLIMIT
    return z


def _zaehle(art, model=None):
    z = zaehler()
    z[art] = z.get(art, 0) + 1
    if art == "anfragen" and model:
        z["modelle"][model] = z["modelle"].get(model, 0) + 1
    f = _zaehler_pfad()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(z, ensure_ascii=False, indent=2), encoding="utf-8")


def budget_rest():
    """Wie viele Anfragen heute noch ins Kontingent passen."""
    return max(0, TAGESLIMIT - zaehler()["anfragen"])


def resolve_model(model):
    if model == "CHECK":
        return require_free(env_or("KI_MODEL_CHECK", DEFAULT_CHECK))
    if model == "LARGE":
        return require_free(env_or("KI_MODEL_LARGE", DEFAULT_LARGE))
    if not model or not str(model).strip() or model == "SMALL":
        return require_free(env_or("KI_MODEL_SMALL", DEFAULT_SMALL))
    return require_free(str(model).strip())


_AVAILABLE = None


def available_free_models():
    """Set der aktuell bei OpenRouter gelisteten :free-Modelle (oder None)."""
    global _AVAILABLE
    if _AVAILABLE is not None:
        return _AVAILABLE or None
    try:
        with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=30) as resp:
            data = json.loads(resp.read())
        _AVAILABLE = {m["id"] for m in data.get("data", []) if str(m.get("id", "")).endswith(":free")}
    except Exception as exc:  # Liste nicht verfügbar → konfigurierte Kette ungeprüft nutzen
        print(f"  Modell-Liste nicht abrufbar ({exc}) — nutze Kette ungeprüft")
        _AVAILABLE = set()
    return _AVAILABLE or None


def model_chain(primary, backup=None):
    """[primary, backup (z. B. Schreib-Modell für die Prüferin), Fallbacks…]"""
    raw = os.environ.get("KI_FALLBACK_MODELS", "")
    fallbacks = [m.strip() for m in raw.split(",") if m.strip()] or DEFAULT_FALLBACK_CHAIN
    chain = []
    for m in [primary] + ([backup] if backup else []) + fallbacks:
        if m in chain:
            continue
        try:
            require_free(m)
        except ValueError as exc:
            print(f"  ueberspringe {m}: {exc}")
            continue
        chain.append(m)
    avail = available_free_models()
    if avail:
        missing = [m for m in chain if m not in avail]
        for m in missing:
            print(f"  Modell {m} ist bei OpenRouter nicht (mehr) gelistet — uebersprungen")
        chain = [m for m in chain if m in avail]
    if not chain:
        raise RuntimeError("Keine gueltigen :free-Modelle in der Kette")
    return chain


def _post_once(model, prompt, max_tokens, temperature):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY fehlt")
    if budget_rest() <= 0:
        raise QuotaExhausted(f"eigener Tageszaehler: {TAGESLIMIT} Anfragen erreicht")
    _zaehle("anfragen", model)
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        # Reasoning-Modelle: wenig denken, Denktext nicht zurückgeben —
        # sonst bleibt content leer. Nicht-Reasoning-Modelle ignorieren das.
        "reasoning": {"effort": "low", "exclude": True},
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read())
    if data.get("error"):
        raise EmptyResponse(f"{model} lieferte Fehlerobjekt: {str(data['error'])[:300]}")
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
    if not content or not str(content).strip():
        raise EmptyResponse(f"{model} lieferte leeren content (nur Reasoning?)")
    return str(content)


def _is_daily_quota(body):
    b = body.lower()
    return "per-day" in b or "per day" in b or "free-models-per-day" in b


def llm_call(prompt, model=None, max_tokens=DEFAULT_MAX_TOKENS, temperature=0.3):
    """Ruft LLM auf. 429/leere Antwort: kurzes Backoff, dann nächstes :free-Modell.
    Tageslimit → QuotaExhausted sofort. 401/402/403 → sofort Abbruch."""
    primary = resolve_model(model)
    backup = resolve_model("LARGE") if model == "CHECK" else None
    chain = model_chain(primary, backup)
    last_error = None
    waited = 0
    for chain_model in chain:
        for attempt in range(1, len(BACKOFF) + 2):
            wait = BACKOFF[attempt - 1] if attempt <= len(BACKOFF) else 0
            is_last = attempt == len(BACKOFF) + 1
            try:
                out = _post_once(chain_model, prompt, max_tokens, temperature)
                _zaehle("ok")
                if chain_model != primary:
                    print(f"  (Antwort kam via Fallback-Modell: {chain_model})")
                return out
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                _zaehle("r429" if exc.code == 429 else "fehler")
                if exc.code == 429:
                    print(f"  429 bei {chain_model} (Versuch {attempt}): {body}")
                    if _is_daily_quota(body):
                        raise QuotaExhausted(f"Free-Tier-Tageslimit erreicht: {body}") from exc
                    last_error = RuntimeError(f"HTTP 429 fuer {chain_model!r}: {body}")
                elif exc.code in (401, 402, 403):
                    raise RuntimeError(f"OpenRouter HTTP {exc.code} (Auth/Konto): {body}") from exc
                elif exc.code in (400, 404, 410, 422):
                    print(f"  HTTP {exc.code} bei {chain_model}: {body} — naechstes Modell")
                    last_error = RuntimeError(f"HTTP {exc.code} fuer {chain_model!r}: {body}")
                    break
                else:
                    print(f"  HTTP {exc.code} bei {chain_model} (Versuch {attempt}): {body}")
                    last_error = RuntimeError(f"HTTP {exc.code} fuer {chain_model!r}: {body}")
            except EmptyResponse as exc:
                _zaehle("leer")
                print(f"  leere Antwort bei {chain_model} (Versuch {attempt})")
                last_error = exc
            except QuotaExhausted:
                raise
            except Exception as exc:
                _zaehle("fehler")
                print(f"  Netzwerkfehler bei {chain_model} (Versuch {attempt}): {exc}")
                last_error = RuntimeError(f"Netzwerkfehler bei {chain_model!r}: {exc}")
                wait = min(wait, 10)
            if is_last:
                break
            if waited + wait > CALL_WAIT_BUDGET_S:
                print("  Warte-Budget fuer diesen Aufruf aufgebraucht")
                raise RuntimeError(f"LLM-Aufruf: Warte-Budget erschoepft. Letzter Fehler: {last_error}")
            time.sleep(wait)
            waited += wait
        print(f"  {chain_model} fehlgeschlagen — naechstes Modell")
    raise RuntimeError(f"LLM-Aufruf endgueltig gescheitert (alle Modelle probiert): {last_error}")


def llm_json(prompt, model=None, max_tokens=DEFAULT_MAX_TOKENS, temperature=0.0):
    """Ruft LLM auf und erzwingt ein JSON-Objekt; invalid = Retry, dann Crash."""
    current = prompt
    for _ in range(3):
        raw = llm_call(current, model=model, max_tokens=max_tokens, temperature=temperature)
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            try:
                obj = json.loads(raw[start:end + 1])
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass
        current = prompt + "\n\n(Antworte NUR mit einem validen JSON-Objekt, ohne Text davor oder danach.)"
    raise RuntimeError("LLM hat 3x kein valides JSON geliefert")


def curate():
    """KI0 wählt 1–3 Cluster aus den heutigen Teasern.

    Folgeläufe am selben Tag: KI0 wählt NEUE Cluster aus noch unbenutzten
    Teasern dazu (angehängt, bestehende Indizes bleiben stabil) — aber nur,
    wenn von heute nichts mehr offen ist (erst fertig machen)
    und höchstens bis MAX_ARTIKEL_PRO_TAG. Bereits behandelte Themen von
    gestern und heute gehen als Ausschlussliste an KI0."""
    from common import run_day, prev_day, is_open, MAX_ARTIKEL_PRO_TAG
    today = run_day()
    drafts_dir = ROOT / "drafts"
    drafts_dir.mkdir(exist_ok=True)
    out = drafts_dir / f"{today}-curation.json"

    def load(day):
        f = drafts_dir / f"{day}-curation.json"
        try:
            return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        except json.JSONDecodeError:
            return {}

    existing = load(today)
    cluster_alt = existing.get("cluster") or []

    # Erst fertig machen: heutige Cluster, die noch offen sind (Abbruch eines
    # früheren Laufs) → keine neuen dazu. Offene Entwürfe von GESTERN blockieren
    # nicht; sie werden in jeder Stufe vor den heutigen abgearbeitet.
    offen = []
    for idx in range(len(cluster_alt)):
        dp = drafts_dir / f"{today}-draft-{idx}.json"
        d = json.loads(dp.read_text(encoding="utf-8")) if dp.exists() else {"stage": None}
        if is_open(d):
            offen.append(f"{today}-draft-{idx}")
    if offen:
        print(f"KI0 uebersprungen: erst offene Entwuerfe fertig machen ({', '.join(offen)})")
        return
    rest = MAX_ARTIKEL_PRO_TAG - len(cluster_alt)
    if rest <= 0:
        print(f"KI0 uebersprungen: Tageslimit von {MAX_ARTIKEL_PRO_TAG} Artikeln erreicht")
        return

    benutzt = {a for c in cluster_alt for a in c.get("artikel_ids", [])}
    src_dir = ROOT / "sources" / today
    kandidaten = []
    for path in sorted(src_dir.glob("*.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        if item.get("advance") and item["id"] not in benutzt:
            kandidaten.append(item)
    kandidaten.sort(key=lambda x: -(x.get("score") or 0))
    top = kandidaten[:20]
    if not top:
        print("Keine (neuen) Kandidaten — KI0 uebersprungen")
        if not out.exists():
            out.write_text("{\"cluster\": []}", encoding="utf-8")
        return

    if budget_rest() < MIN_BUDGET_NEUER_ARTIKEL:
        print(f"KI0 uebersprungen: nur noch {budget_rest()} Anfragen im Tageskontingent "
              f"(mind. {MIN_BUDGET_NEUER_ARTIKEL} fuer einen neuen Artikel noetig)")
        return

    bereits = [c.get("thema") for day in (prev_day(), today) for c in (load(day).get("cluster") or []) if c.get("thema")]
    listing = []
    for item in top:
        listing.append({
            "id": item["id"],
            "quelle": item["source_key"],
            "ressort": item.get("ressort_detected"),
            "titel": item["title"],
            "medium": item.get("medium"),
            "auszug": (item.get("volltext") or item["summary"])[:600],
            "score": item["score"],
        })
    prompt = (ROOT / "prompts" / "ki0.txt").read_text(encoding="utf-8")
    prompt = prompt.replace("{{KANDIDATEN}}", json.dumps(listing, ensure_ascii=False, indent=1))
    prompt = prompt.replace("{{BEREITS}}", json.dumps(bereits, ensure_ascii=False) if bereits else "[] (noch keine)")
    model = resolve_model("SMALL")
    print(f"KI0 laeuft mit Modell: {model} ({len(cluster_alt)} Cluster heute bereits, max. {rest} neue)")
    result = llm_json(prompt, model=model, temperature=0.2)
    # Nur Artikel-IDs aus der Kandidatenliste zulassen (keine erfundenen/benutzten IDs)
    valid_ids = {i["id"] for i in top}
    neu = []
    for c in result.get("cluster", []) if isinstance(result.get("cluster"), list) else []:
        if not isinstance(c, dict):
            continue
        ids = [a for a in c.get("artikel_ids", []) if a in valid_ids and a not in benutzt][:3]
        if ids:
            benutzt.update(ids)
            c["artikel_ids"] = ids
            neu.append(c)
    neu = neu[:min(3, rest)]
    existing["cluster"] = cluster_alt + neu
    existing.setdefault("laeufe", []).append({
        "neue_cluster": len(neu),
        "qualitaet_des_tages": result.get("qualitaet_des_tages"),
        "abgelehnt_hauptgrund": result.get("abgelehnt_hauptgrund"),
    })
    out.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"KI0: {len(neu)} neue Cluster gewaehlt (heute gesamt {len(existing['cluster'])})")


if __name__ == "__main__":
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    cmd = sys.argv[1] if len(sys.argv) > 1 else "curate"
    if cmd == "curate":
        # Wie die anderen Stufen: ein KI0-Fehler darf den Lauf nicht abbrechen —
        # offene Entwürfe von gestern/heute werden trotzdem weiter bearbeitet.
        try:
            curate()
        except QuotaExhausted as exc:
            print(f"::error::KI0: Tageslimit erreicht — keine neuen Themen heute: {exc}")
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"::error::KI0 fehlgeschlagen — keine neuen Themen in diesem Lauf: {exc}")
    else:
        raise SystemExit(f"Unbekannter Befehl: {cmd}")
