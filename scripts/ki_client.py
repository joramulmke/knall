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

v7 (früherer Modellwechsel):
- 429 (Minutenlimit/überlastet): nur EIN kurzer Wiederholversuch, dann sofort
  das nächste Modell statt 20+60+120 s zu warten.
- Leere Antwort: ein Wiederholversuch ohne Wartezeit, dann nächstes Modell.
- Ein Modell mit 429 wird für KNALL_MODELL_SPERRE_S (Default 600 s) im
  Tageszähler gesperrt und in allen folgenden Aufrufen — auch in späteren
  Pipeline-Stufen — ans Ende der Kette gestellt (nicht entfernt).

v8 (zweiter Anbieter Groq):
- Modell-IDs mit Präfix "groq:" (z. B. "groq:openai/gpt-oss-120b") laufen über
  die Groq-API (Secret GROQ_API_KEY, kostenloser Tarif ohne Zahlungsmittel).
- Ist GROQ_API_KEY gesetzt, prüft KI2/KI3 zuerst mit Groq; dahinter folgen
  Nemotron, das Schreib-Modell und die OpenRouter-Fallbacks.
- Groq hat ein eigenes Tageskontingent (KNALL_GROQ_TAGESLIMIT, Default 900)
  und eigene Zähler ("groq_*"); das OpenRouter-Budget bleibt unberührt.
- Groq-Tageslimit, 401 oder zu große Anfrage (413) → Groq überspringen,
  weiter mit OpenRouter. Kein Abbruch der Stufe.
- KI0/KI1: Groq steht direkt hinter Gemma (vor Nemotron/ERNIE/Ling, die bei
  langen Prompts oft leer antworten). Modelle: KI_GROQ_MODELS (kommagetrennt).
"""
import os
import re
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
DEFAULT_CHECK_GROQ = "groq:openai/gpt-oss-120b"
# Groq-Ersatz für KI0/KI1 und zweite Groq-Prüferin. Nicht (mehr) angebotene
# Modelle fallen über die Groq-Modellliste automatisch heraus.
# Prüfen/Auswählen (KI0, KI2, KI3): großes Modell, kleines als Reserve.
DEFAULT_GROQ_MODELS = ["groq:openai/gpt-oss-120b", "groq:openai/gpt-oss-20b"]
# Schreiben (KI1): nur große Modelle — das kleine schrieb im Test ungenau.
DEFAULT_GROQ_SCHREIBER = ["groq:openai/gpt-oss-120b", "groq:qwen/qwen3.8-27b"]
GROQ_TPM_VERSUCHE = 3            # Token-pro-Minute-Limit: so oft die genannte Wartezeit abwarten
GROQ_TPM_MAX_WARTEN = 65
GROQ = "groq:"
GROQ_TAGESLIMIT = int(os.environ.get("KNALL_GROQ_TAGESLIMIT") or 900)
# Eigene Kennung: Groq (Cloudflare) sperrt die Python-Standardkennung
# "Python-urllib" mit HTTP 403 / "error code: 1010".
USER_AGENT = "knall-pipeline/1.0"
GROQ_MAX_TOKENS = 3500           # Groq-Gratis: kleines Token-pro-Minute-Limit; 2500 schnitt KI2-JSON ab
_JSON_MODUS = False              # von llm_json gesetzt → Groq liefert garantiert ein JSON-Objekt

# Fallback-Kette bei persistentem 429, leeren Antworten oder fehlendem Modell.
DEFAULT_FALLBACK_CHAIN = [
    "nvidia/nemotron-3.5-lightning:free",
    "baidu/ernie-5.5-flash-preview:free",
    "inclusionai/ling-3.2-flash:free",
]

BACKOFF = [20, 60, 120]          # Netzwerk-/Serverfehler: Wartezeit nach Versuch 1, 2, 3
BACKOFF_429 = [15]               # 429: ein kurzer Wiederholversuch, dann nächstes Modell
BACKOFF_LEER = [0]               # leere Antwort: einmal sofort wiederholen
MODELL_SPERRE_S = int(os.environ.get("KNALL_MODELL_SPERRE_S") or 600)
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


def ist_groq(model):
    return str(model).startswith(GROQ)


def require_free(model):
    if ist_groq(model):          # Groq: kostenloser Tarif, Konto ohne Zahlungsmittel
        return model
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
        z.setdefault("groq_" + k, 0)
    z.setdefault("modelle", {})
    z["limit"] = TAGESLIMIT
    z["groq_limit"] = GROQ_TAGESLIMIT
    return z


def _zaehle(art, model=None, groq=False):
    """Zählt für OpenRouter ("anfragen", …) oder Groq ("groq_anfragen", …)."""
    z = zaehler()
    if groq:
        art = "groq_" + art
    z[art] = z.get(art, 0) + 1
    if art.endswith("anfragen") and model:
        z["modelle"][model] = z["modelle"].get(model, 0) + 1
    f = _zaehler_pfad()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(z, ensure_ascii=False, indent=2), encoding="utf-8")


def sperre_modell(model, sekunden=None):
    """Modell nach 429 für eine Weile hinten anstellen (gilt über Stufen hinweg)."""
    z = zaehler()
    z.setdefault("gesperrt_bis", {})[model] = int(time.time()) + (sekunden or MODELL_SPERRE_S)
    f = _zaehler_pfad()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(z, ensure_ascii=False, indent=2), encoding="utf-8")


def gesperrt(model):
    return zaehler().get("gesperrt_bis", {}).get(model, 0) > time.time()


def budget_rest():
    """Wie viele Anfragen heute noch ins Kontingent passen."""
    return max(0, TAGESLIMIT - zaehler()["anfragen"])


def groq_rest():
    return max(0, GROQ_TAGESLIMIT - zaehler()["groq_anfragen"])


def budget_verfuegbar():
    """Anfragen, die heute insgesamt noch gehen (OpenRouter + Groq, falls Key)."""
    return budget_rest() + (groq_rest() if os.environ.get("GROQ_API_KEY") else 0)


def resolve_model(model):
    if model == "CHECK":
        default = DEFAULT_CHECK_GROQ if os.environ.get("GROQ_API_KEY") else DEFAULT_CHECK
        return require_free(env_or("KI_MODEL_CHECK", default))
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


_GROQ_AVAILABLE = None


def groq_modelle(schreiben=False):
    if not os.environ.get("GROQ_API_KEY"):
        return []
    raw = os.environ.get("KI_GROQ_SCHREIBER" if schreiben else "KI_GROQ_MODELS", "")
    eigene = [m.strip() for m in raw.split(",") if m.strip()]
    default = DEFAULT_GROQ_SCHREIBER if schreiben else DEFAULT_GROQ_MODELS
    return [m if ist_groq(m) else GROQ + m for m in eigene] or list(default)


def available_groq_models():
    """Set der Groq-Modelle (ohne Präfix) oder None, wenn nicht abrufbar."""
    global _GROQ_AVAILABLE
    if _GROQ_AVAILABLE is not None:
        return _GROQ_AVAILABLE or None
    try:
        req = urllib.request.Request("https://api.groq.com/openai/v1/models",
                                     headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
                                              "User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        _GROQ_AVAILABLE = {m["id"] for m in data.get("data", [])}
        print(f"  Groq-Modelle verfuegbar: {', '.join(sorted(_GROQ_AVAILABLE))}")
    except Exception as exc:
        print(f"  Groq-Modell-Liste nicht abrufbar ({exc}) — nutze Groq ungeprueft")
        _GROQ_AVAILABLE = set()
    return _GROQ_AVAILABLE or None


def model_chain(primary, backup=None):
    """[primary, backup(s) (z. B. Schreib-Modell für die Prüferin), Fallbacks…]"""
    raw = os.environ.get("KI_FALLBACK_MODELS", "")
    fallbacks = [m.strip() for m in raw.split(",") if m.strip()] or DEFAULT_FALLBACK_CHAIN
    chain = []
    backups = backup if isinstance(backup, list) else ([backup] if backup else [])
    for m in [primary] + backups + fallbacks:
        if m in chain:
            continue
        try:
            require_free(m)
        except ValueError as exc:
            print(f"  ueberspringe {m}: {exc}")
            continue
        chain.append(m)
    if any(ist_groq(m) for m in chain):
        if not os.environ.get("GROQ_API_KEY"):
            chain = [m for m in chain if not ist_groq(m)]
        elif groq_rest() <= 0:
            print("  Groq-Tageskontingent aufgebraucht — nur OpenRouter")
            chain = [m for m in chain if not ist_groq(m)]
        else:
            gavail = available_groq_models()
            if gavail:
                for m in [m for m in chain if ist_groq(m) and m[len(GROQ):] not in gavail]:
                    print(f"  Groq-Modell {m} nicht (mehr) verfuegbar — uebersprungen")
                chain = [m for m in chain if not ist_groq(m) or m[len(GROQ):] in gavail]
    avail = available_free_models()
    if avail:
        missing = [m for m in chain if not ist_groq(m) and m not in avail]
        for m in missing:
            print(f"  Modell {m} ist bei OpenRouter nicht (mehr) gelistet — uebersprungen")
        chain = [m for m in chain if ist_groq(m) or m in avail]
    if not chain:
        raise RuntimeError("Keine gueltigen :free-Modelle in der Kette")
    hinten = [m for m in chain if gesperrt(m)]
    if hinten and len(hinten) < len(chain):
        print(f"  zuletzt 429, ans Ende gestellt: {', '.join(hinten)}")
        chain = [m for m in chain if m not in hinten] + hinten
    return chain


def _post_once(model, prompt, max_tokens, temperature):
    if ist_groq(model):
        return _post_groq(model, prompt, max_tokens, temperature)
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
    return _senden("https://openrouter.ai/api/v1/chat/completions", key, payload, model)


class GroqUebersprungen(Exception):
    """Groq nicht nutzbar (Kontingent/Schlüssel) — weiter mit dem nächsten Modell."""


def _post_groq(model, prompt, max_tokens, temperature):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise GroqUebersprungen("GROQ_API_KEY fehlt")
    if groq_rest() <= 0:
        raise GroqUebersprungen(f"Groq-Tageszaehler: {GROQ_TAGESLIMIT} Anfragen erreicht")
    _zaehle("anfragen", model, groq=True)
    name = model[len(GROQ):]
    body = {
        "model": name,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": min(max_tokens, GROQ_MAX_TOKENS),
        "temperature": temperature,
    }
    if _JSON_MODUS:
        body["response_format"] = {"type": "json_object"}
    if "gpt-oss" in name:        # Reasoning-Modell: wenig denken
        body["reasoning_effort"] = "low"
    elif "qwen" in name:         # Denktext nicht in die Antwort (sonst <think>…)
        body["reasoning_format"] = "hidden"
    return _senden("https://api.groq.com/openai/v1/chat/completions", key,
                   json.dumps(body).encode(), model)


def _senden(url, key, payload, model):
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read())
    if data.get("error"):
        raise EmptyResponse(f"{model} lieferte Fehlerobjekt: {str(data['error'])[:300]}")
    choice = (data.get("choices") or [{}])[0]
    content = choice.get("message", {}).get("content")
    if not content or not str(content).strip():
        # Diagnose: Abbruchgrund, Token-Verbrauch und ob nur Reasoning kam
        usage = data.get("usage") or {}
        reasoning = choice.get("message", {}).get("reasoning") or ""
        raise EmptyResponse(
            f"{model} lieferte leeren content (finish_reason={choice.get('finish_reason')}, "
            f"prompt={usage.get('prompt_tokens')}, completion={usage.get('completion_tokens')}, "
            f"reasoning_zeichen={len(str(reasoning))})")
    return str(content)


def _is_daily_quota(body):
    b = body.lower()
    return "per-day" in b or "per day" in b or "free-models-per-day" in b


def llm_call(prompt, model=None, max_tokens=DEFAULT_MAX_TOKENS, temperature=0.3):
    """Ruft LLM auf. 429/leere Antwort: kurzes Backoff, dann nächstes :free-Modell.
    Tageslimit → QuotaExhausted sofort. 401/402/403 → sofort Abbruch."""
    primary = resolve_model(model)
    groq = groq_modelle(schreiben=(model == "LARGE"))
    if model == "CHECK":
        # Groq-Prüferin(nen) → Nemotron → Schreib-Modell; ohne Groq: Nemotron → Schreib-Modell
        backup = groq + [DEFAULT_CHECK, resolve_model("LARGE")]
    else:
        # KI0/KI1: Gemma → Groq → OpenRouter-Fallbacks
        backup = groq
    chain = model_chain(primary, backup)
    last_error = None
    waited = 0
    openrouter_aus = None        # OpenRouter-Tageslimit erreicht → nur noch Groq
    for chain_model in chain:
        if openrouter_aus and not ist_groq(chain_model):
            continue
        attempt = 0
        while True:
            attempt += 1
            plan = BACKOFF
            try:
                out = _post_once(chain_model, prompt, max_tokens, temperature)
                _zaehle("ok", groq=ist_groq(chain_model))
                if chain_model != primary:
                    print(f"  (Antwort kam via Fallback-Modell: {chain_model})")
                return out
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                groq = ist_groq(chain_model)
                _zaehle("r429" if exc.code == 429 else "fehler", groq=groq)
                if groq and (exc.code in (401, 403, 413) or (exc.code == 429 and _is_daily_quota(body))):
                    # Groq-Tageslimit, Schlüsselproblem oder Anfrage zu groß fürs Gratis-Limit:
                    # Groq überspringen, OpenRouter übernimmt.
                    print(f"  Groq HTTP {exc.code} bei {chain_model}: {body} — weiter ohne Groq")
                    if exc.code != 413 and "1010" not in body:
                        sperre_modell(chain_model, 24 * 3600)
                    last_error = RuntimeError(f"Groq HTTP {exc.code}: {body}")
                    break
                if exc.code == 429:
                    print(f"  429 bei {chain_model} (Versuch {attempt}): {body}")
                    if _is_daily_quota(body):
                        q = QuotaExhausted(f"Free-Tier-Tageslimit erreicht: {body}")
                        if not any(ist_groq(m) for m in chain[chain.index(chain_model) + 1:]):
                            raise q from exc
                        print("  OpenRouter-Tageslimit — weiter nur mit Groq")
                        openrouter_aus = q
                        break
                    last_error = RuntimeError(f"HTTP 429 fuer {chain_model!r}: {body}")
                    plan = BACKOFF_429
                    m = re.search(r"try again in ([\d.]+)\s*s", body) if groq else None
                    if m:
                        # Groq nennt die Wartezeit bis das Minutenlimit wieder frei ist
                        w = min(GROQ_TPM_MAX_WARTEN, int(float(m.group(1))) + 2)
                        plan = [w] * GROQ_TPM_VERSUCHE
                    if attempt > len(plan):
                        # Minutenlimit ist nach gut einer Minute wieder frei —
                        # das große Modell nicht 10 Minuten nach hinten stellen.
                        sperre_modell(chain_model, 90 if m else None)
                elif exc.code in (401, 402, 403):
                    raise RuntimeError(f"OpenRouter HTTP {exc.code} (Auth/Konto): {body}") from exc
                elif exc.code in (400, 404, 410, 422):
                    print(f"  HTTP {exc.code} bei {chain_model}: {body} — naechstes Modell")
                    last_error = RuntimeError(f"HTTP {exc.code} fuer {chain_model!r}: {body}")
                    break
                else:
                    print(f"  HTTP {exc.code} bei {chain_model} (Versuch {attempt}): {body}")
                    last_error = RuntimeError(f"HTTP {exc.code} fuer {chain_model!r}: {body}")
            except GroqUebersprungen as exc:
                print(f"  {exc} — weiter ohne Groq")
                last_error = exc
                break
            except EmptyResponse as exc:
                _zaehle("leer", groq=ist_groq(chain_model))
                print(f"  leere Antwort bei {chain_model} (Versuch {attempt}): {exc}")
                last_error = exc
                plan = BACKOFF_LEER
            except QuotaExhausted as exc:
                # OpenRouter ist für heute durch. Gibt es noch Groq in der Kette,
                # dort weitermachen; sonst Abbruch wie bisher.
                if not any(ist_groq(m) for m in chain[chain.index(chain_model) + 1:]):
                    raise
                print(f"  {exc} — weiter nur mit Groq")
                openrouter_aus = exc
                break
            except Exception as exc:
                _zaehle("fehler", groq=ist_groq(chain_model))
                print(f"  Netzwerkfehler bei {chain_model} (Versuch {attempt}): {exc}")
                last_error = RuntimeError(f"Netzwerkfehler bei {chain_model!r}: {exc}")
                plan = [min(w, 10) for w in BACKOFF]
            if attempt > len(plan):
                break
            wait = plan[attempt - 1]
            if waited + wait > CALL_WAIT_BUDGET_S:
                print("  Warte-Budget fuer diesen Aufruf aufgebraucht")
                raise RuntimeError(f"LLM-Aufruf: Warte-Budget erschoepft. Letzter Fehler: {last_error}")
            if wait:
                time.sleep(wait)
            waited += wait
        print(f"  {chain_model} fehlgeschlagen — naechstes Modell")
    if openrouter_aus:
        raise QuotaExhausted(f"OpenRouter-Tageslimit erreicht, Groq lieferte nicht: {last_error}")
    raise RuntimeError(f"LLM-Aufruf endgueltig gescheitert (alle Modelle probiert): {last_error}")


def llm_json(prompt, model=None, max_tokens=DEFAULT_MAX_TOKENS, temperature=0.0):
    """Ruft LLM auf und erzwingt ein JSON-Objekt; invalid = Retry, dann Crash."""
    global _JSON_MODUS
    current = prompt
    for _ in range(3):
        _JSON_MODUS = True
        try:
            raw = llm_call(current, model=model, max_tokens=max_tokens, temperature=temperature)
        finally:
            _JSON_MODUS = False
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

    if budget_verfuegbar() < MIN_BUDGET_NEUER_ARTIKEL:
        print(f"KI0 uebersprungen: nur noch {budget_verfuegbar()} Anfragen im Tageskontingent "
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
