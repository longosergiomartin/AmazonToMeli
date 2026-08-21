"""Fuentes de códigos de barras con API (no scraping)."""

import json

import pytest

import fuentes_gtin
from fuentes_gtin import brickset_configurado, gtin_de_brickset, gtin_de_upcitemdb


class _Resp:
    def __init__(self, datos, status=200):
        self._d, self.status_code = datos, status

    def json(self):
        if isinstance(self._d, Exception):
            raise self._d
        return self._d


@pytest.fixture(autouse=True)
def _sin_red(monkeypatch):
    """Ninguna de estas pruebas sale a internet."""
    monkeypatch.setattr(fuentes_gtin.requests, "get",
                        lambda *a, **k: _Resp({}, 200))


# ---- Brickset: la fuente de referencia para LEGO ------------------------

def test_brickset_devuelve_el_ean_de_la_caja(monkeypatch):
    llamadas = []

    def _get(url, params=None, timeout=None):
        llamadas.append((url, params))
        return _Resp({"status": "success", "matches": 1, "sets": [
            {"name": "Death Star Trash Compactor Diorama",
             "barcode": {"EAN": "5702017155326", "UPC": "673419376709"}}]})

    monkeypatch.setattr(fuentes_gtin.requests, "get", _get)
    r = gtin_de_brickset("75339", api_key="clave")
    assert r["gtin"] == "5702017155326" and r["fuente"] == "Brickset"
    # Brickset identifica los sets con la variante: "75339-1".
    assert json.loads(llamadas[0][1]["params"])["setNumber"] == "75339-1"


def test_brickset_usa_el_upc_si_no_hay_ean(monkeypatch):
    monkeypatch.setattr(fuentes_gtin.requests, "get", lambda *a, **k: _Resp(
        {"status": "success", "sets": [{"name": "X", "barcode": {"UPC": "673419281423"}}]}))
    assert gtin_de_brickset("75192", api_key="clave")["gtin"] == "673419281423"


def test_brickset_descarta_codigos_que_no_validan(monkeypatch):
    """Un número que no pasa el dígito verificador no es un código de barras."""
    monkeypatch.setattr(fuentes_gtin.requests, "get", lambda *a, **k: _Resp(
        {"status": "success", "sets": [{"barcode": {"EAN": "1234567890123"}}]}))
    assert gtin_de_brickset("75192", api_key="clave") == {}


def test_brickset_sin_clave_no_consulta(monkeypatch):
    llamadas = []
    monkeypatch.setattr(fuentes_gtin.requests, "get",
                        lambda *a, **k: llamadas.append(1) or _Resp({}))
    assert gtin_de_brickset("75339", api_key="") == {}
    assert not llamadas


def test_brickset_sin_resultados(monkeypatch):
    monkeypatch.setattr(fuentes_gtin.requests, "get", lambda *a, **k: _Resp(
        {"status": "success", "matches": 0, "sets": []}))
    assert gtin_de_brickset("99999", api_key="clave") == {}


def test_brickset_error_de_la_api_no_rompe(monkeypatch):
    monkeypatch.setattr(fuentes_gtin.requests, "get", lambda *a, **k: _Resp(
        {"status": "error", "message": "invalid API key"}))
    assert gtin_de_brickset("75339", api_key="clave") == {}


def test_brickset_configurado_lee_el_entorno(monkeypatch):
    monkeypatch.delenv("BRICKSET_API_KEY", raising=False)
    assert brickset_configurado() is False
    monkeypatch.setenv("BRICKSET_API_KEY", "abc")
    assert brickset_configurado() is True


# ---- UPCitemdb: base genérica, cualquier rubro -------------------------

def test_upcitemdb_encuentra_por_nombre(monkeypatch):
    monkeypatch.setattr(fuentes_gtin.requests, "get", lambda *a, **k: _Resp(
        {"items": [{"title": "Bosch Professional GSB 13 RE Taladro percutor",
                    "ean": "3165140857710"}]}))
    r = gtin_de_upcitemdb("Bosch taladro percutor",
                          parecido_a="Bosch Professional GSB 13 RE Taladro percutor")
    assert r["gtin"] == "3165140857710" and r["fuente"] == "UPCitemdb"


def test_upcitemdb_descarta_un_producto_distinto(monkeypatch):
    """La guarda contra quedarse con cualquier cosa que devuelva la búsqueda."""
    monkeypatch.setattr(fuentes_gtin.requests, "get", lambda *a, **k: _Resp(
        {"items": [{"title": "Cafetera Oster express", "ean": "3165140857710"}]}))
    assert gtin_de_upcitemdb("Bosch taladro", parecido_a="Bosch Taladro percutor") == {}


def test_upcitemdb_sin_guarda_acepta_el_primero(monkeypatch):
    monkeypatch.setattr(fuentes_gtin.requests, "get", lambda *a, **k: _Resp(
        {"items": [{"title": "Lo que sea", "upc": "673419281423"}]}))
    assert gtin_de_upcitemdb("algo que buscar")["gtin"] == "673419281423"


def test_upcitemdb_no_consulta_con_una_consulta_muy_corta(monkeypatch):
    llamadas = []
    monkeypatch.setattr(fuentes_gtin.requests, "get",
                        lambda *a, **k: llamadas.append(1) or _Resp({}))
    assert gtin_de_upcitemdb("ab") == {}
    assert not llamadas


def test_upcitemdb_respuesta_invalida_no_rompe(monkeypatch):
    monkeypatch.setattr(fuentes_gtin.requests, "get",
                        lambda *a, **k: _Resp(ValueError("no es json")))
    assert gtin_de_upcitemdb("Bosch taladro percutor") == {}


def test_upcitemdb_limite_alcanzado_no_rompe(monkeypatch):
    monkeypatch.setattr(fuentes_gtin.requests, "get",
                        lambda *a, **k: _Resp({}, status=429))
    assert gtin_de_upcitemdb("Bosch taladro percutor") == {}
