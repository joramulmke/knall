"""Signatur der Fediverse-Zustellung: mit openssl erzeugt, mit openssl geprüft."""
import base64
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fediverse  # noqa: E402


def test_signier_text_format():
    t = fediverse.signier_text("POST", "https://beispiel.social/inbox?x=1", "D", "SHA-256=abc")
    assert t == ("(request-target): post /inbox?x=1\nhost: beispiel.social\n"
                 "date: D\ndigest: SHA-256=abc")


def test_signatur_pruefbar(tmp_path):
    key = tmp_path / "k.pem"
    pub = tmp_path / "k.pub"
    subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                    "-out", str(key)], check=True, capture_output=True)
    subprocess.run(["openssl", "pkey", "-in", str(key), "-pubout", "-out", str(pub)], check=True)
    text = fediverse.signier_text("POST", "https://beispiel.social/inbox", "D", "SHA-256=abc")
    sig = tmp_path / "sig"
    sig.write_bytes(base64.b64decode(fediverse.signiere(str(key), text)))
    data = tmp_path / "data"
    data.write_text(text)
    r = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(pub), "-signature", str(sig), str(data)],
                       capture_output=True)
    assert r.returncode == 0


def test_ohne_einrichtung_still(monkeypatch, capsys):
    for k in ("FEDI_ENDPOINT", "FEDI_TOKEN", "FEDI_PRIVATE_KEY"):
        monkeypatch.delenv(k, raising=False)
    fediverse.main()
    assert "übersprungen" in capsys.readouterr().out
