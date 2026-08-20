"""La app tiene que levantar aunque la base esté dormida.

Render devuelve **502 Bad Gateway** cuando el proceso no llega a escuchar en su
puerto. Eso pasaba cuando el arranque intentaba crear las tablas contra una base
Neon apagada: cada intento reintentaba con esperas y el arranque se colgaba.
"""

import time

from fastapi.testclient import TestClient

from api.server import crear_app

# Postgres en un puerto donde no hay nadie: simula la base caída/dormida.
BASE_CAIDA = "postgresql://u:p@127.0.0.1:59999/nada"


def test_crear_app_no_espera_a_la_base(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", BASE_CAIDA)
    t = time.time()
    crear_app()
    assert time.time() - t < 5  # antes se colgaba hasta fallar


def test_el_panel_carga_con_la_base_caida(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", BASE_CAIDA)
    c = TestClient(crear_app())
    assert c.get("/panel").status_code == 200


def test_la_api_avisa_que_la_base_despierta_en_vez_de_romperse(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", BASE_CAIDA)
    # raise_server_exceptions=False: queremos ver la respuesta que recibe el
    # navegador, no que el TestClient re-lance la excepción.
    c = TestClient(crear_app(), raise_server_exceptions=False)
    r = c.get("/api/catalogo")
    assert r.status_code == 503                      # no 500 ni caída del proceso
    assert "base de datos" in r.text.lower()


def test_arranca_aunque_la_api_del_dolar_no_responda(monkeypatch):
    """La cotización se busca en el primer uso, no al arrancar."""
    import arbitraje.cotizacion as cot

    def _explota(*a, **k):
        raise RuntimeError("la API del dólar no responde")

    monkeypatch.setattr(cot, "obtener_cotizaciones", _explota)
    monkeypatch.setattr("api.catalogo_routes.obtener_cotizaciones", _explota)
    c = TestClient(crear_app(db_path=":memory:"))
    assert c.get("/panel").status_code == 200
    # Y el catálogo sigue respondiendo, con el tipo de cambio de la config.
    assert c.get("/api/catalogo").status_code == 200
