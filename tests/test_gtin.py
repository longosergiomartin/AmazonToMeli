"""Tests de la búsqueda automática de GTIN (sin red real)."""

import pytest
from fastapi.testclient import TestClient

import gtin_lookup
from api.server import crear_app
from gtin_lookup import validar_gtin, buscar_gtin


# ---- validación matemática (dígito verificador) --------------------------

def test_validar_gtin_codigos_reales():
    assert validar_gtin("673419281423") is True    # UPC-12 (Lego 75192)
    assert validar_gtin("4006381333931") is True   # EAN-13 conocido
    assert validar_gtin("673419281424") is False   # verificador alterado
    assert validar_gtin("1234567890123") is False  # inventado


def test_validar_gtin_rechaza_asin_y_formatos_raros():
    assert validar_gtin("B07PX3X5WL") is False     # un ASIN no es GTIN
    assert validar_gtin("123") is False
    assert validar_gtin("") is False


# ---- búsqueda con fuentes mockeadas --------------------------------------

class _Resp:
    def __init__(self, texto, status=200):
        self.text = texto
        self.status_code = status


def test_buscar_gtin_encuentra_y_elige_mas_repetido(monkeypatch):
    # Amazon menciona el EAN; el buscador lo repite y agrega ruido.
    def _get(url, **kw):
        if "amazon.com" in url:
            return _Resp("Detalles ... EAN: 673419281423 ...")
        return _Resp("resultado 673419281423 ... otro 4006381333931 ... 673419281423")
    monkeypatch.setattr(gtin_lookup.requests, "get", _get)
    r = buscar_gtin("B075SDMMMV")
    assert r["ok"] is True
    assert r["gtin"] == "673419281423"
    assert "673419281423" in r["candidatos"]


def test_buscar_gtin_descarta_numeros_invalidos(monkeypatch):
    # Solo hay números que NO pasan la validación → no inventa nada.
    monkeypatch.setattr(gtin_lookup.requests, "get",
                        lambda url, **kw: _Resp("ruido 1234567890123 999999999999"))
    r = buscar_gtin("B075SDMMMV")
    assert r["ok"] is False and r["gtin"] == ""


def test_buscar_gtin_sin_red(monkeypatch):
    def _fallar(*a, **kw):
        raise gtin_lookup.requests.RequestException("bloqueado")
    monkeypatch.setattr(gtin_lookup.requests, "get", _fallar)
    r = buscar_gtin("B075SDMMMV")
    assert r["ok"] is False and "mano" in r["mensaje"].lower() or "manual" in r["mensaje"].lower()


def test_buscar_gtin_asin_invalido():
    r = buscar_gtin("no-es-asin")
    assert r["ok"] is False and "inválido" in r["mensaje"].lower()


# ---- endpoint ------------------------------------------------------------

def test_endpoint_gtin(tmp_path, monkeypatch):
    monkeypatch.setattr(gtin_lookup.requests, "get",
                        lambda url, **kw: _Resp("EAN: 673419281423"))
    c = TestClient(crear_app(db_path=str(tmp_path / "t.db")))
    r = c.post("/api/gtin", json={"asin": "B075SDMMMV"}).json()
    assert r["ok"] is True and r["gtin"] == "673419281423"


def _resp(status=200, text=""):
    class _R:
        status_code, text = status, ""
    _R.status_code, _R.text = status, text
    return _R()


def test_buscar_asin_encuentra_el_producto(monkeypatch):
    import gtin_lookup
    html = ('<div data-component-type="s-search-result" data-asin="B075SDMMMV">'
            '<h2><span>LEGO Star Wars X-Wing 75301</span></h2></div>')
    monkeypatch.setattr(gtin_lookup.requests, "get", lambda *a, **k: _resp(200, html))
    r = gtin_lookup.buscar_asin("5702016914498")
    assert r["ok"] and r["asin"] == "B075SDMMMV"
    assert "X-Wing" in r["titulo"]


def test_buscar_asin_rechaza_codigo_invalido(monkeypatch):
    import gtin_lookup
    llamadas = []
    monkeypatch.setattr(gtin_lookup.requests, "get",
                        lambda *a, **k: llamadas.append(1) or _resp())
    r = gtin_lookup.buscar_asin("1234567890123")   # no pasa el verificador
    assert r["ok"] is False and not llamadas
    assert "verificador" in r["mensaje"]


def test_buscar_asin_detecta_el_bloqueo(monkeypatch):
    import gtin_lookup
    monkeypatch.setattr(gtin_lookup.requests, "get", lambda *a, **k: _resp(503))
    r = gtin_lookup.buscar_asin("5702016914498")
    assert r["ok"] is False and r["bloqueado"] is True


def test_buscar_asin_detecta_el_captcha(monkeypatch):
    import gtin_lookup
    monkeypatch.setattr(gtin_lookup.requests, "get",
                        lambda *a, **k: _resp(200, "Enter the characters captcha"))
    assert gtin_lookup.buscar_asin("5702016914498")["bloqueado"] is True


def test_buscar_asin_sin_resultados(monkeypatch):
    import gtin_lookup
    monkeypatch.setattr(gtin_lookup.requests, "get",
                        lambda *a, **k: _resp(200, "<div>sin resultados</div>"))
    r = gtin_lookup.buscar_asin("5702016914498")
    assert r["ok"] is False and r["bloqueado"] is False
