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
