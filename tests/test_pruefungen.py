"""Maschinelle Prüfungen — laufen vor jedem Pipeline-Lauf (fail-closed:
schlägt ein Test fehl, wird an diesem Tag nichts geschrieben)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import pytest  # noqa: E402
from special_checks import check_numbers, check_entities, zahlwort  # noqa: E402
from regex_gate import check  # noqa: E402
from build_site import render  # noqa: E402
from safety import ki2_verdict  # noqa: E402

QUELLE = (
    "Die Klinik in Riesa streicht zwei Stationen. Am 26.11.2026 sagte der Landrat "
    "Klaus Becker, 2,5 Millionen Euro fehlen. 4.000 Pflegekräfte sind betroffen. "
    "Das Klinikum Riesa gehört dem Kreis Meißen. 12 Prozent der Betten fallen weg."
)


@pytest.mark.parametrize("wort,wert", [
    ("zwei", 2), ("zwölf", 12), ("dreiundzwanzig", 23), ("hundert", 100),
    ("zweihundertfünfzig", 250), ("viertausendfünfhundert", 4500), ("einhundert", 100),
    ("achtung", None), ("elfmeter", None), ("ein", None), ("sieben", 7),
])
def test_zahlwort(wort, wert):
    assert zahlwort(wort) == wert


@pytest.mark.parametrize("artikel,fehler", [
    ("2 Stationen", []),                       # Ziffer ↔ Zahlwort in der Quelle
    ("zwei Stationen", []),
    ("3 Stationen", ["3"]),
    ("vier Stationen", ["vier"]),              # Zahlwort im Artikel wird geprüft
    ("2,5 Mio. Euro", []),                     # Größenordnung + Abkürzung
    ("2.500.000 Euro", []),
    ("zweieinhalb", []),                       # kein Zahlwort → nicht erfasst
    ("2 Millionen Euro", ["2 Millionen"]),     # Wert ≠ 2,5 Mio.
    ("am 26.11.2026", []),
    ("am 27.11.2026", ["27.11.2026"]),         # jeder Datumsteil muss gedeckt sein
    ("4000 Pflegekräfte", []),
    ("4 000 Pflegekräfte", []),
    ("12 Prozent", []),
    ("eine Station", []),                      # Artikel "eine" ist keine Zahl
    ("eine Million Euro", ["eine Million"]),
])
def test_zahlen(artikel, fehler):
    assert check_numbers(artikel, QUELLE) == fehler


def test_entities_keine_fehlalarme_bei_nomen():
    art = ("Das Klinikum Riesa macht zwei Stationen dicht. Kranke Menschen fahren weiter. "
           "Pflegekräfte Sachsens bangen. Landrat Klaus Becker schweigt.")
    assert check_entities(art, QUELLE) == []


def test_entities_erfundene_namen():
    art = "Bürgermeister Hans Wurst lacht. Der Vorstand von Fresenius Helios kassiert."
    fehler = check_entities(art, QUELLE)
    assert "Hans Wurst" in fehler and "Fresenius Helios" in fehler


def test_regex_gate():
    regeln = {f["regel"] for f in check("Die Firma würde kündigen. Es wurde gekündigt. Wir zahlen.", "")}
    assert {"konjunktiv", "passiv", "ansprache"} <= regeln


def test_site_escaped_und_ohne_markdown():
    html = render({"quellen": [{"titel": "<x>", "url": "javascript:alert(1)"}]},
                  "**Titel <script>**\n\nText **fett**")
    assert "<script>" not in html and "**" not in html and "javascript:" not in html


def test_ki2_ok_mit_leeren_verstoessen_ist_gruen():
    prot = {"ampel": "gruen", "titel_befund": "ok", "systemfehler": None,
            "saetze": [{"satz_nr": 0, "befund": "ok", "verstoesse": []},
                       {"satz_nr": 1, "befund": "ok", "verstoesse": []}]}
    assert ki2_verdict(prot, "Titel hier.\nEin Satz hier.")[0] == "gruen"


def test_oeffentlicher_befund_ohne_text():
    """Abgelehnte Texte, Fassungen und Quelltexte bleiben privat (Archiv)."""
    from router import redact
    draft = {"text": "Geheimer Text", "verlauf": [{"stufe": "ki1-entwurf", "text": "Geheimer Text"}],
             "quellen_text": "Quelle", "text_vor_ki3": "Geheimer Text",
             "cluster": {"thema": "T"}, "ablehnungsgruende": ["x"]}
    assert "Geheimer Text" not in str(redact(draft)) and "Quelle" not in str(redact(draft))


def test_volltext_gedaechtnis():
    from fulltext import blockade, quote
    assert blockade("HTTP 403") and blockade("zu wenig Text (300 Zeichen, Paywall?)")
    assert blockade("robots.txt verbietet Abruf") and not blockade("ok") and not blockade("HTTP 500")
    ged = {"gut": {"2026-11-25": {"ok": 6, "fehl": 0}}, "blocker": {"2026-11-25": {"ok": 0, "fehl": 7}}}
    assert quote(ged, "gut") > quote(ged, "neu") > quote(ged, "blocker")


# --- Früherer Modellwechsel bei 429 -----------------------------------------

def _ki_setup(monkeypatch, tmp_path):
    import io
    import urllib.error
    import ki_client
    monkeypatch.setattr(ki_client, "_zaehler_pfad", lambda: tmp_path / "anfragen.json")
    monkeypatch.setattr(ki_client, "available_free_models", lambda: None)
    monkeypatch.setattr(ki_client.time, "sleep", lambda s: schlaf.append(s))
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.setenv("KI_FALLBACK_MODELS", "b/zwei:free,c/drei:free")
    schlaf = []

    def err429(model):
        return urllib.error.HTTPError("u", 429, "rate", {}, io.BytesIO(b"rate-limited upstream"))
    return ki_client, schlaf, err429


def test_429_wechselt_nach_einem_kurzen_versuch(monkeypatch, tmp_path):
    ki, schlaf, err429 = _ki_setup(monkeypatch, tmp_path)
    aufrufe = []

    def post(model, *a):
        aufrufe.append(model)
        if model == "a/eins:free":
            raise err429(model)
        return "ok"
    monkeypatch.setattr(ki, "_post_once", post)
    assert ki.llm_call("p", model="a/eins:free") == "ok"
    assert aufrufe == ["a/eins:free", "a/eins:free", "b/zwei:free"]
    assert schlaf == [15]
    # gesperrtes Modell steht beim nächsten Aufruf hinten
    assert ki.model_chain("a/eins:free")[0] == "b/zwei:free"


def test_leere_antwort_sofort_wiederholen(monkeypatch, tmp_path):
    ki, schlaf, _ = _ki_setup(monkeypatch, tmp_path)
    aufrufe = []

    def post(model, *a):
        aufrufe.append(model)
        if model == "a/eins:free":
            raise ki.EmptyResponse("leer")
        return "ok"
    monkeypatch.setattr(ki, "_post_once", post)
    assert ki.llm_call("p", model="a/eins:free") == "ok"
    assert aufrufe == ["a/eins:free", "a/eins:free", "b/zwei:free"]
    assert schlaf == []


# --- Groq als zweiter Anbieter für die Prüferin -----------------------------

def test_groq_pruefer_kette_und_eigene_zaehler(monkeypatch, tmp_path):
    ki, schlaf, _ = _ki_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.delenv("KI_MODEL_CHECK", raising=False)
    monkeypatch.setattr(ki, "available_groq_models", lambda: None)
    kette = ki.model_chain(ki.resolve_model("CHECK"), [ki.DEFAULT_CHECK, ki.resolve_model("LARGE")])
    assert kette[:3] == ["groq:openai/gpt-oss-120b", ki.DEFAULT_CHECK, ki.DEFAULT_LARGE]

    gesendet = []
    monkeypatch.setattr(ki, "_senden", lambda url, key, payload, model: gesendet.append((url, model)) or "ok")
    assert ki.llm_call("p", model="CHECK") == "ok"
    assert "api.groq.com" in gesendet[0][0]
    z = ki.zaehler()
    assert z["groq_anfragen"] == 1 and z["groq_ok"] == 1 and z["anfragen"] == 0


def test_groq_tageslimit_faellt_auf_openrouter_zurueck(monkeypatch, tmp_path):
    import io
    import urllib.error
    ki, schlaf, _ = _ki_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.delenv("KI_MODEL_CHECK", raising=False)
    monkeypatch.setattr(ki, "available_groq_models", lambda: None)
    aufrufe = []

    def post(model, *a):
        aufrufe.append(model)
        if ki.ist_groq(model):
            raise urllib.error.HTTPError("u", 429, "rate", {}, io.BytesIO(
                b"Rate limit reached on requests per day (RPD): Limit 1000"))
        return "ok"
    monkeypatch.setattr(ki, "_post_once", post)
    assert ki.llm_call("p", model="CHECK") == "ok"      # kein QuotaExhausted
    assert aufrufe == ["groq:openai/gpt-oss-120b", "groq:openai/gpt-oss-20b", ki.DEFAULT_CHECK]
    assert schlaf == []
    assert ki.gesperrt("groq:openai/gpt-oss-120b")


def test_ohne_groq_key_bleibt_nemotron_pruefer(monkeypatch, tmp_path):
    ki, _, _ = _ki_setup(monkeypatch, tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("KI_MODEL_CHECK", raising=False)
    assert ki.resolve_model("CHECK") == ki.DEFAULT_CHECK
    assert not any(ki.ist_groq(m) for m in ki.model_chain("groq:x", None))


def test_ki1_kette_gemma_dann_groq(monkeypatch, tmp_path):
    ki, _, err429 = _ki_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.delenv("KI_MODEL_LARGE", raising=False)
    monkeypatch.setattr(ki, "available_groq_models", lambda: None)
    aufrufe = []

    def post(model, *a):
        aufrufe.append(model)
        if model == ki.DEFAULT_LARGE:
            raise err429(model)
        return "text"
    monkeypatch.setattr(ki, "_post_once", post)
    assert ki.llm_call("p", model="LARGE") == "text"
    assert aufrufe == [ki.DEFAULT_LARGE, ki.DEFAULT_LARGE, "groq:openai/gpt-oss-120b"]


def test_openrouter_tageslimit_weiter_mit_groq(monkeypatch, tmp_path):
    import io
    import urllib.error
    ki, _, _ = _ki_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.delenv("KI_MODEL_LARGE", raising=False)
    monkeypatch.setattr(ki, "available_groq_models", lambda: None)
    aufrufe = []

    def post(model, *a):
        aufrufe.append(model)
        if not ki.ist_groq(model):
            raise urllib.error.HTTPError("u", 429, "q", {}, io.BytesIO(b"free-models-per-day exceeded"))
        return "text"
    monkeypatch.setattr(ki, "_post_once", post)
    assert ki.llm_call("p", model="LARGE") == "text"
    assert aufrufe == [ki.DEFAULT_LARGE, "groq:openai/gpt-oss-120b"]


def test_eigener_zaehler_voll_aber_groq_frei(monkeypatch, tmp_path):
    ki, _, _ = _ki_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setattr(ki, "available_groq_models", lambda: None)
    monkeypatch.setattr(ki, "budget_rest", lambda: 0)
    monkeypatch.setattr(ki, "_senden", lambda url, key, payload, model: "text")
    assert ki.budget_verfuegbar() >= ki.MIN_BUDGET_NEUER_ARTIKEL
    assert ki.llm_call("p", model="LARGE") == "text"


def test_ohne_groq_bleibt_tageslimit_abbruch(monkeypatch, tmp_path):
    ki, _, _ = _ki_setup(monkeypatch, tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(ki, "budget_rest", lambda: 0)
    with pytest.raises(ki.QuotaExhausted):
        ki.llm_call("p", model="LARGE")


def test_eigene_user_agent_kennung(monkeypatch, tmp_path):
    """Groq/Cloudflare sperrt 'Python-urllib' (HTTP 403, error code 1010)."""
    import io
    import json as _json
    ki, _, _ = _ki_setup(monkeypatch, tmp_path)
    gesehen = {}

    class Antwort(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=None):
        gesehen["ua"] = req.get_header("User-agent")
        return Antwort(_json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode())
    monkeypatch.setattr(ki.urllib.request, "urlopen", urlopen)
    assert ki._senden("https://api.groq.com/x", "k", b"{}", "groq:m") == "ok"
    assert gesehen["ua"] and "python" not in gesehen["ua"].lower()


def test_groq_minutenlimit_wartet_genannte_zeit(monkeypatch, tmp_path):
    import io
    import urllib.error
    ki, schlaf, _ = _ki_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.delenv("KI_MODEL_CHECK", raising=False)
    monkeypatch.setattr(ki, "available_groq_models", lambda: None)
    n = {"x": 0}

    def post(model, *a):
        n["x"] += 1
        if n["x"] <= 2:
            raise urllib.error.HTTPError("u", 429, "tpm", {}, io.BytesIO(
                b"Rate limit reached on tokens per minute (TPM): Limit 8000. Please try again in 7.4175s."))
        return "ok"
    monkeypatch.setattr(ki, "_post_once", post)
    assert ki.llm_call("p", model="CHECK") == "ok"
    assert schlaf == [9, 9]                     # 7.4 s → 7 + 2 Puffer, gleiches Modell


# --- Aktualität -------------------------------------------------------------

def test_alte_meldungen_werden_aussortiert():
    from common import alter_tage, zu_alt
    heute = "2026-11-25"
    assert alter_tage({"published_iso": "2026-11-24T08:00:00+00:00"}, heute) == 1
    assert zu_alt({"datum_original": "2026-03-10"}, heute)
    # Datum aus dem Artikel schlägt Feed-Datum
    assert zu_alt({"datum_original": "2026-03-10", "published_iso": "2026-11-25T08:00:00+00:00"}, heute)
    assert not zu_alt({}, heute)                # unbekannt → nicht aussortieren


def test_ki1_schreibt_nie_mit_kleinem_groq_modell(monkeypatch, tmp_path):
    ki, _, _ = _ki_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "g")
    assert "groq:openai/gpt-oss-20b" not in ki.groq_modelle(schreiben=True)
    assert "groq:openai/gpt-oss-20b" in ki.groq_modelle()


def test_groq_minutenlimit_sperrt_nur_kurz(monkeypatch, tmp_path):
    import io
    import time as _t
    import urllib.error
    ki, _, _ = _ki_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setattr(ki, "available_groq_models", lambda: None)

    def post(model, *a):
        if model == "groq:openai/gpt-oss-120b":
            raise urllib.error.HTTPError("u", 429, "tpm", {}, io.BytesIO(b"TPM. Please try again in 3s."))
        return "ok"
    monkeypatch.setattr(ki, "_post_once", post)
    ki.llm_call("p", model="CHECK")
    bis = ki.zaehler()["gesperrt_bis"]["groq:openai/gpt-oss-120b"]
    assert bis - _t.time() <= 95
