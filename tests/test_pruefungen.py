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


# --- KI3-Nachbesserung: tolerantes Finden der Stelle ------------------------

def test_ki3_ersatz_toleriert_typografie():
    from router import apply_replacements
    text = "Die Klinik in Beeskow steht vor dem „Aus“ – sagt der Landrat. Danach mehr."
    # anderes Anführungszeichen, anderer Strich, umschließende Anführungszeichen
    risiken = [{"urteil": "grenzwertig", "stelle": '"Die Klinik in Beeskow steht vor dem "Aus" - sagt der Landrat."',
                "ersatzformulierung": "Der Landrat sieht die Klinik in Beeskow in Gefahr."}]
    neu, fehl = apply_replacements(text, risiken)
    assert fehl == []
    assert neu == "Der Landrat sieht die Klinik in Beeskow in Gefahr. Danach mehr."


def test_ki3_ersatz_verlangt_gleiche_woerter():
    from router import apply_replacements
    text = "Die Klinik in Beeskow steht vor dem Aus."
    risiken = [{"urteil": "grenzwertig", "stelle": "Das Krankenhaus in Beeskow steht vor dem Aus.",
                "ersatzformulierung": "x"}]
    neu, fehl = apply_replacements(text, risiken)
    assert fehl and neu == text


def test_groq_json_modus_nur_in_llm_json(monkeypatch, tmp_path):
    import json as _json
    ki, _, _ = _ki_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.delenv("KI_MODEL_CHECK", raising=False)
    monkeypatch.setattr(ki, "available_groq_models", lambda: None)
    bodies = []
    monkeypatch.setattr(ki, "_senden", lambda url, key, payload, model: bodies.append(_json.loads(payload)) or '{"a": 1}')
    assert ki.llm_json("p", model="CHECK") == {"a": 1}
    ki.llm_call("p", model="CHECK")
    assert bodies[0].get("response_format") == {"type": "json_object"}
    assert "response_format" not in bodies[1]


def test_gerichtsfeeds_alter_nach_feed_datum():
    from common import zu_alt
    heute = "2026-11-25"
    urteil = {"datum_original": "2026-09-01", "published_iso": "2026-11-24T10:00:00+00:00", "datum_vom_feed": True}
    assert not zu_alt(urteil, heute)
    urteil["datum_vom_feed"] = False
    assert zu_alt(urteil, heute)


def test_medienname_ohne_slogan():
    from common import medium_kurz
    assert medium_kurz("Haufe. Intelligenz, die bewegt.") == "Haufe"
    assert medium_kurz("taz | die tageszeitung") == "taz"
    assert medium_kurz("rbb24") == "rbb24"
    assert medium_kurz("Deutscher Bundestag") == "Deutscher Bundestag"
    assert medium_kurz("test.de") == "test.de"


def test_alte_quelle_wird_in_den_ki_stufen_verworfen(tmp_path, monkeypatch):
    import json as _json
    import router
    monkeypatch.setattr(router, "ROOT", tmp_path)
    d = tmp_path / "sources" / "2026-11-25"
    d.mkdir(parents=True)
    alt = {"title": "BGH: Mieter", "volltext": "x" * 900, "url_original": "https://e.de/a",
           "medium": "Haufe. Intelligenz, die bewegt.", "datum_original": "2016-03-01"}
    neu = dict(alt, url_original="https://e.de/b", datum_original="2026-11-24")
    (d / "a.json").write_text(_json.dumps(alt))
    (d / "b.json").write_text(_json.dumps(neu))
    text, dropped, meta = router.sources_from_cluster({"artikel_ids": ["a", "b"]}, "2026-11-25")
    assert dropped == ["a"] and [m["id"] for m in meta] == ["b"]
    assert meta[0]["medium"] == "Haufe"
    text, dropped, meta = router.sources_from_cluster({"artikel_ids": ["a"]}, "2026-11-25", alt_verwerfen=False)
    assert dropped == []


@pytest.mark.parametrize("wert,sek", [
    ("7.66s", 7.66), ("1m2.5s", 62.5), ("350ms", 0.35), ("", None), ("quatsch", None),
])
def test_groq_dauer(wert, sek):
    import ki_client
    assert ki_client._dauer_s(wert) == sek


def test_groq_wartet_vorab_bei_knappem_minutenfenster(monkeypatch, tmp_path):
    ki, schlaf, _ = _ki_setup(monkeypatch, tmp_path)
    m = "groq:openai/gpt-oss-120b"
    ki._merke_groq_fenster(m, {"x-ratelimit-remaining-tokens": "500",
                               "x-ratelimit-reset-tokens": "20s"})
    jetzt = ki.time.time()
    assert 15 < ki.groq_wartezeit(m, "x" * 3000, jetzt) <= 22   # Bedarf > 500 frei
    assert ki.groq_wartezeit(m, "x" * 3000, jetzt + 30) == 0     # Fenster erneuert
    ki._merke_groq_fenster(m, {"x-ratelimit-remaining-tokens": "7000",
                               "x-ratelimit-reset-tokens": "5s"})
    assert ki.groq_wartezeit(m, "x" * 3000, jetzt) == 0          # genug frei
    assert ki.groq_wartezeit("groq:andere", "x", jetzt) == 0      # unbekanntes Modell


def test_themengruppen_vor_ki0():
    import ki_client as ki
    items = {
        "a": {"title": "Charlottenburg-Wilmersdorf: Weil Eigentümer nicht zahlen, 80 Wohnhäuser ohne Heizung",
              "summary": "Im Bezirk Charlottenburg-Wilmersdorf sitzen Mieter in 80 Wohnhäusern ohne Heizung. "
                         "Die Eigentümer zahlen die Rechnungen des Versorgers nicht, das Bezirksamt prüft."},
        "b": {"title": "Weihnachten im Kerzenschein",
              "summary": "Leipzig stimmt sich auf die Adventszeit ein. Auf dem Weihnachtsmarkt leuchten "
                         "Kerzen, Familien besuchen die Buden."},
        "c": {"title": "Mieter in Charlottenburg frieren: Versorger stellt Heizung ab",
              "summary": "Weil Eigentümer offene Rechnungen nicht begleichen, bleiben 80 Wohnhäuser "
                         "in Charlottenburg-Wilmersdorf kalt. Das Bezirksamt sucht eine Lösung."},
    }
    liste = [dict(v, id=k) for k, v in items.items()]
    gruppe = ki.gruppieren(liste)
    assert gruppe["a"] == gruppe["c"] != gruppe["b"]
    # KI0 mischt trotzdem → maschinell wieder getrennt
    assert ki.cluster_auf_gruppe(["a", "b", "c"], gruppe) == ["a", "c"]
    assert ki.cluster_auf_gruppe(["b"], gruppe) == ["b"]      # Einzelquelle bleibt


def _rot_prot(titel="ok", sys_fehler=None):
    return {"ampel": "rot", "titel_befund": titel, "systemfehler": sys_fehler,
            "saetze": [{"satz_nr": 0, "text": "Titel", "befund": titel, "verstoesse": []},
                       {"satz_nr": 1, "text": "Erfunden.", "befund": "rot",
                        "verstoesse": [{"pruefposten": "P7", "erklaerung": "nicht in Quelle"}]}]}


def test_rot_hinweis_nur_bei_sauberem_protokoll():
    from router import rot_hinweis
    h = rot_hinweis(_rot_prot(titel="rot"), [])
    assert "Satz 1" in h and "P7" in h and "Titel" in h
    assert rot_hinweis(_rot_prot(), ["Satz 3 fehlt im Protokoll"]) is None
    assert rot_hinweis(_rot_prot(sys_fehler="kaputt"), []) is None


def _evaluate_setup(monkeypatch, ampeln):
    import router
    import ki_client
    import special_checks
    draft = {"stage": "geschrieben", "text": "Alt", "cluster": {"thema": "T"}}
    path = pathlib.Path("2026-01-01-draft-0.json")
    folge = iter(ampeln)
    aufrufe = {"ki1": 0, "abgelehnt": None}

    def ki2(text, quellen):
        a = next(folge)
        return a, (_rot_prot() if a == "rot" else {"saetze": []}), []

    def ki1(prompt, model=None, **kw):
        aufrufe["ki1"] += 1
        assert "beanstandet" in prompt and "Erfunden." in prompt
        return "Neu"

    def ki1_satzweise(prompt, model=None, **kw):
        # Artikel "Alt" enthält den roten Satz nicht → Rückfall auf Gesamtkorrektur
        aufrufe["ki1_json"] = aufrufe.get("ki1_json", 0) + 1
        return {"ersetzungen": [{"nr": 1, "neu": "Belegt."}]}

    monkeypatch.setattr(router, "load_drafts", lambda: [("2026-01-01", path, draft)])
    monkeypatch.setattr(router, "sources_from_cluster", lambda c, t: ("Q", [], []))
    monkeypatch.setattr(router, "ki2_check", ki2)
    monkeypatch.setattr(router, "regex_runden", lambda t, *a, **k: (t, [], 0))
    monkeypatch.setattr(router, "write_json", lambda p, o: None)
    monkeypatch.setattr(router, "reject", lambda t, n, d, g: aufrufe.update(abgelehnt=g))
    monkeypatch.setattr(ki_client, "llm_call", ki1)
    monkeypatch.setattr(ki_client, "llm_json", ki1_satzweise)
    monkeypatch.setattr(special_checks, "run_all", lambda t, q: {"bestaende": True})
    router.evaluate_stage()
    return draft, aufrufe


def test_rot_bekommt_eine_ki1_korrektur_dann_gruen(monkeypatch):
    draft, aufrufe = _evaluate_setup(monkeypatch, ["rot", "gruen"])
    assert aufrufe["ki1"] == 1 and draft["stage"] == "gruen" and draft["text"] == "Neu"
    assert draft["ki2_pruefrunden"] == 2 and aufrufe["abgelehnt"] is None


def test_rot_nach_zwei_korrekturen_wird_abgelehnt(monkeypatch):
    draft, aufrufe = _evaluate_setup(monkeypatch, ["rot", "rot", "rot"])
    assert aufrufe["ki1"] == 2 and aufrufe["abgelehnt"] == "KI2 rot/gelb-erschoepft"
    assert [k["modus"] for k in draft["rot_korrekturen"]] == ["gesamt", "gesamt"]


ARTIKEL_ROT = (
    "Amazon lügt Beschäftigte an\n"
    "Bei Amazon streiken 500 Beschäftigte. Die Gewerkschaft fordert einen Tarifvertrag.\n\n"
    "Der Konzern lehnt Gespräche ab. Solange Amazon lügt, drohen weitere Angriffe."
)


def test_rot_stellen_aus_protokoll():
    from router import rot_stellen
    prot = {"ampel": "rot", "titel_befund": "rot", "systemfehler": None, "saetze": [
        {"satz_nr": 0, "text": "Amazon lügt Beschäftigte an", "befund": "rot",
         "verstoesse": [{"pruefposten": "P9", "erklaerung": "Lüge nicht belegt"}]},
        {"satz_nr": 1, "text": "x", "befund": "ok", "verstoesse": []},
        {"satz_nr": 4, "text": "Solange Amazon lügt, drohen weitere Angriffe.", "befund": "rot",
         "verstoesse": [{"pruefposten": "P6", "erklaerung": "Prognose"}]}]}
    st = rot_stellen(prot, [])
    assert [s["nr"] for s in st] == [0, 4] and "P9" in st[0]["gruende"]
    assert rot_stellen({"systemfehler": "kaputt", "saetze": []}, []) is None
    assert rot_stellen(prot, ["Protokoll unvollständig"]) is None


def test_satzweise_ersetzen_aendert_nur_beanstandetes():
    from router import satzweise_ersetzen
    stellen = [{"nr": 0, "text": "Amazon lügt Beschäftigte an", "gruende": "P9"},
               {"nr": 4, "text": "Solange Amazon lügt, drohen weitere Angriffe.", "gruende": "P6"}]
    neu = satzweise_ersetzen(ARTIKEL_ROT, stellen, [
        {"nr": 0, "neu": "500 Amazon-Beschäftigte streiken für Tarifvertrag"},
        {"nr": 4, "neu": ""}])
    assert neu == ("500 Amazon-Beschäftigte streiken für Tarifvertrag\n"
                   "Bei Amazon streiken 500 Beschäftigte. Die Gewerkschaft fordert einen Tarifvertrag.\n\n"
                   "Der Konzern lehnt Gespräche ab.")


def test_satzweise_ersetzen_typografie_tolerant_und_streichen_mittendrin():
    from router import satzweise_ersetzen
    text = "Titel\nErster Satz. Der „Konzern“ droht allen. Letzter Satz."
    neu = satzweise_ersetzen(text, [{"nr": 2, "text": 'Der "Konzern" droht allen.', "gruende": "P7"}],
                             [{"nr": 2, "neu": ""}])
    assert neu == "Titel\nErster Satz. Letzter Satz."


@pytest.mark.parametrize("ersetzungen", [
    [],                                         # Stelle fehlt in der Antwort
    [{"nr": 0, "neu": ""}],                     # Titel darf nicht gestrichen werden
])
def test_satzweise_ersetzen_unanwendbar_gibt_none(ersetzungen):
    from router import satzweise_ersetzen
    stellen = [{"nr": 0, "text": "Amazon lügt Beschäftigte an", "gruende": "P9"}]
    assert satzweise_ersetzen(ARTIKEL_ROT, stellen, ersetzungen) is None


def test_satzweise_ersetzen_unbekannter_satz_gibt_none():
    from router import satzweise_ersetzen
    stellen = [{"nr": 2, "text": "Diesen Satz gibt es nicht im Artikel.", "gruende": "P7"}]
    assert satzweise_ersetzen(ARTIKEL_ROT, stellen, [{"nr": 2, "neu": "Neu."}]) is None


def test_rot_korrektur_faellt_auf_gesamt_zurueck(monkeypatch):
    import ki_client
    import router
    aufrufe = []
    monkeypatch.setattr(ki_client, "llm_json", lambda p, **kw: aufrufe.append(p) or {"ersetzungen": []})
    monkeypatch.setattr(ki_client, "llm_call", lambda p, **kw: aufrufe.append(p) or "Neuer Titel\nText.")
    stellen = [{"nr": 4, "text": "Solange Amazon lügt, drohen weitere Angriffe.", "gruende": "P6"}]
    neu, modus = router.rot_korrektur(ARTIKEL_ROT, stellen, "<quelle>Q</quelle>")
    assert modus == "gesamt" and neu == "Neuer Titel\nText." and len(aufrufe) == 2
    # Korrektur-Prompt ohne Boulevard-Zuspitzung aus ki1.txt
    assert "Härtestes Verb" not in aufrufe[0] and "nie schärfer" in aufrufe[0]
