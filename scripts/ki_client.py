"""
ki_client.py — Einheitliche LLM-Aufrufe über OpenRouter, JSON-Zwang.

v2: Nutzt ausschließlich :free-Modelle, damit der Pipeline-Betrieb ohne
API-Kosten bleibt. Modellwahl via Repo-Variablen KI_MODEL_SMALL / KI_MODEL_LARGE
(optional — Defaults greifen bei leer/fehlend).

Modell-Auflösung: Env-Variablen KI_MODEL_SMALL / KI_MODEL_LARGE.
Leere oder fehlende Variablen fallen auf die Defaults zurück.
"""
import os
import json
import sys
import time
import pathlib
import urllib.request
import urllib.error

ROOT = pathlib.Path(__file__).resolve().parent.parent

# :free-Modelle (Stand 2026-11-25, via openrouter.ai/api/v1/models verifiziert)
DEFAULT_SMALL = "google/gemma-4-26b-a4b-it:free"
DEFAULT_LARGE = "google/gemma-4-31b-it:free"


def env_or(name, default):
    """Env-Variable, aber leerer String zaehlt als nicht gesetzt."""
    val = (os.environ.get(name) or "").strip()
    return val if val else default


def resolve_model(model):
    """'LARGE' oder None/'' -> konkrete Modell-ID; leere Strings -> Defaults."""
    if model == "LARGE":
        return env_or("KI_MODEL_LARGE", DEFAULT_LARGE)
    if not model or not str(model).strip():
        return env_or("KI_MODEL_SMALL", DEFAULT_SMALL)
    return str(model).strip()


def llm_call(prompt, model=None, max_tokens=2000, retries=2):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY fehlt")
    model = resolve_model(model)
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    last_error = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            # 429 (Rate-Limit: free-Tier ist limitiert) -> laenger warten
            wait = 30 if exc.code == 429 else 5
            # 4xx (ausser 429): nicht wiederholen, Fehler ist deterministisch
            if 400 <= exc.code < 500 and exc.code != 429:
                raise RuntimeError(
                    f"OpenRouter HTTP {exc.code} fuer Modell {model!r}: {body}"
                ) from exc
            last_error = RuntimeError(f"OpenRouter HTTP {exc.code} fuer Modell {model!r}: {body}")
        except Exception as exc:
            last_error = exc
        if attempt < retries:
            time.sleep(wait)
    raise RuntimeError(f"LLM-Aufruf endgueltig gescheitert: {last_error}")


def llm_json(prompt, model=None, max_tokens=2500):
    """Ruft LLM auf und erzaengt valides JSON; invalid = Retry, dann Crash."""
    current = prompt
    for attempt in range(3):
        raw = llm_call(current, model=model, max_tokens=max_tokens)
        try:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1:
                return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass
        current = prompt + "\n\n(Antworte NUR mit validem JSON, ohne Text davor oder danach.)"
    raise RuntimeError("LLM hat 3x kein valides JSON geliefert")


def curate():
    import datetime
    today = datetime.date.today().isoformat()
    src_dir = ROOT / "sources" / today
    kandidaten = []
    for path in sorted(src_dir.glob("*.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        if item.get("advance"):
            kandidaten.append(item)
    kandidaten.sort(key=lambda x: -(x.get("score") or 0))
    top = kandidaten[:20]
    if not top:
        print("Keine Kandidaten — KI0 uebersprungen")
        (ROOT / "drafts").mkdir(exist_ok=True)
        (ROOT / "drafts" / f"{today}-curation-empty.json").write_text("{}", encoding="utf-8")
        # Leere Curation ablegen, damit nachfolgende Stages sauber durchlaufen
        (ROOT / "drafts" / f"{today}-curation.json").write_text("{\"cluster\": []}", encoding="utf-8")
        return
    listing = []
    for item in top:
        listing.append({
            "id": item["id"],
            "quelle": item["source_key"],
            "ressort": item.get("ressort_detected"),
            "titel": item["title"],
            "auszug": item["summary"][:300],
            "score": item["score"],
        })
    prompt_file = ROOT / "prompts" / "ki0.txt"
    prompt = prompt_file.read_text(encoding="utf-8").replace(
        "{{KANDIDATEN}}", json.dumps(listing, ensure_ascii=False, indent=1)
    )
    model = env_or("KI_MODEL_SMALL", DEFAULT_SMALL)
    print(f"KI0 laeuft mit Modell: {model}")
    result = llm_json(prompt, model=model)
    out = ROOT / "drafts" / f"{today}-curation.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"KI0: {len(result.get('cluster', []))} Cluster gewaehlt")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "curate"
    if cmd == "curate":
        curate()
    else:
        raise SystemExit(f"Unbekannter Befehl: {cmd}")
