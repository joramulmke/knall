"""
common.py — Gemeinsame Tages-Logik der Pipeline.

- run_day(): EIN Datum pro Lauf (Env KNALL_DAY, vom Workflow einmal gesetzt).
  Verhindert, dass ein Lauf über Mitternacht (UTC) zwischen den Schritten
  den Tag wechselt.
- work_days(): [gestern, heute] — offene Entwürfe von gestern werden vor
  den heutigen fertig gemacht.
- prune(): räumt alles auf, was älter als gestern ist. Noch offene Entwürfe
  bekommen dabei einen öffentlichen Befund "abgelaufen".

CLI: python scripts/common.py prune
"""
import datetime
import json
import os
import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
FINAL_STAGES = {"veroeffentlicht", "abgelehnt"}
MAX_ARTIKEL_PRO_TAG = int(os.environ.get("KNALL_MAX_ARTIKEL_PRO_TAG") or 6)
DAY_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def run_day():
    d = (os.environ.get("KNALL_DAY") or "").strip()
    if DAY_RX.match(d):
        return d
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def prev_day(day=None):
    d = datetime.date.fromisoformat(day or run_day())
    return (d - datetime.timedelta(days=1)).isoformat()


def work_days():
    """Reihenfolge = Priorität: erst gestern fertig machen, dann heute."""
    return [prev_day(), run_day()]


def is_open(draft):
    return draft.get("stage") not in FINAL_STAGES


def prune():
    keep = set(work_days())
    sys.path.insert(0, str(ROOT / "scripts"))
    src = ROOT / "sources"
    if src.exists():
        for d in src.iterdir():
            if d.is_dir() and DAY_RX.match(d.name) and d.name not in keep:
                shutil.rmtree(d)
                print(f"aufgeraeumt: sources/{d.name}")
    drafts = ROOT / "drafts"
    if not drafts.exists():
        return
    for f in sorted(drafts.glob("*.json")):
        day = f.name[:10]
        if not DAY_RX.match(day) or day in keep:
            continue
        if "-draft-" in f.name:
            try:
                draft = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                draft = {}
            if is_open(draft):
                from router import redact, reject_path
                rec = redact(dict(draft, ablehnungsgruende=[
                    f"abgelaufen: bis {prev_day()} nicht fertig geworden (letzter Stand: {draft.get('stage')})"
                ]))
                rec["abgelaufen"] = True
                path = reject_path(day, f.stem)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"abgelaufen: {f.stem}")
        f.unlink()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "prune":
        prune()
    elif cmd == "day":
        print(run_day())
    else:
        raise SystemExit("Befehle: prune | day")
