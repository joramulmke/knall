"""
ki_client.py — Einheitliche LLM-Aufrufe über OpenRouter, JSON-Zwang.
Nutzt kleines Modell für KI0, großes für KI1–KI3.
"""
import os
import json
import sys
import time
import pathlib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent

DEFAULT_SMALL = "meta-llama/llama-3.1-8b-instruct"
DEFAULT_LARGE = "anthropic/claude-sonnet-4.5"


def llm_call(prompt, model=None, max_tokens=2000, retries=2):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY fehlt")
    model = model or os.environ.get("KI_MODEL_SMALL", DEFAULT_SMALL)
    if model == "LARGE":
        model = os.environ.get("KI_MODEL_LARGE", DEFAULT_LARGE)
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
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            if attempt == retries:
                raise
            time.sleep(5)


def llm_json(prompt, model=None, max_tokens=2500):
    """Ruft LLM auf und erzwingt valides JSON; invalid = Retry, dann Crash."""
    for attempt in range(3):
        raw = llm_call(prompt, model=model, max_tokens=max_tokens)
        try:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1:
                return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass
        prompt = prompt + "\n\n(Antworte NUR mit validem JSON, ohne Text davor oder danach.)"
    raise RuntimeError("LLM hat 3x kein valides JSON geliefert")


def curate():
    import datetime
    import yaml
    today = datetime.date.today().isoformat()
    src_dir = ROOT / "sources" / today
    kandidates = []
    for path in sorted(src_dir.glob("*.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        if item.get("advance"):
            kandidates.append(item)
    kandidates.sort(key=lambda x: -(x.get("score") or 0))
    top = kandidates[:20]
    if not top:
        print("Keine Kandidaten — KI0 übersprungen")
        (ROOT / "drafts" / f"{today}-curation-empty.json").write_text("{}", encoding="utf-8")
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
    prompt = prompt_file.read_text(encoding="utf-8").replace("{{KANDIDATEN}}", json.dumps(listing, ensure_ascii=False, indent=1))
    result = llm_json(prompt, model=os.environ.get("KI_MODEL_SMALL", DEFAULT_SMALL))
    out = ROOT / "drafts" / f"{today}-curation.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"KI0: {len(result.get('cluster', []))} Cluster gewählt")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "curate"
    if cmd == "curate":
        curate()
    else:
        raise SystemExit(f"Unbekannter Befehl: {cmd}")
