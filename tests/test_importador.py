"""Tests de la cola de importación por lote (sin red: el importador es falso)."""

import pytest
from fastapi.testclient import TestClient

from api.server import crear_app
from arbitraje.config import Config
from catalogo import Catalogo
from db import conectar
from importador import ColaImportacion, BLOQUEADO, PENDIENTE


@pytest.fixture()
def cola():
    conn = conectar(":memory:")
    cat = Catalogo(conn, cfg=Config())
    return ColaImportacion(conn, cat)


def _ficha(asin, precio=100.0, bloqueado=False, mensaje=""):
    """Simula lo que devuelve el lector de fichas de Amazon."""
    return {"asin": asin, "amazon_link": f"https://www.amazon.com/dp/{asin}",
            "ok": not bloqueado, "marca": "LEGO", "modelo": f"Set {asin}",
            "precio_usd": None if bloqueado else precio, "peso_kg": 1.2,
            "descripcion": "Bloques", "imagenes": ["http://img/1.jpg"],
            "mensaje": mensaje, "status": 403 if bloqueado else 200,
            "bloqueado": bloqueado}


# ---- encolar -------------------------------------------------------------

def test_encolar_links_y_asins(cola):
    r = cola.encolar(["https://www.amazon.com/dp/B075SDMMMV", "B0BBHHT8LY", "  "])
    assert r["nuevos"] == 2 and r["pendientes"] == 2


def test_no_encola_duplicados(cola):
    cola.encolar(["B075SDMMMV"])
    r = cola.encolar(["B075SDMMMV", "https://www.amazon.com/dp/B075SDMMMV"])
    assert r["nuevos"] == 0 and r["duplicados"] == 2


def test_no_encola_lo_que_ya_esta_en_el_catalogo(cola):
    from catalogo import ProductoCatalogo
    cola.cat.agregar(ProductoCatalogo(asin="B075SDMMMV", modelo="Ya cargado"))
    r = cola.encolar(["B075SDMMMV"])
    assert r["nuevos"] == 0 and r["duplicados"] == 1


def test_descarta_entradas_invalidas(cola):
    r = cola.encolar(["no-es-un-asin", "https://example.com/algo"])
    assert r["nuevos"] == 0 and r["invalidos"] == 2


# ---- procesar ------------------------------------------------------------

def test_procesar_crea_borradores_con_datos(cola):
    cola.encolar(["B075SDMMMV"])
    r = cola.procesar_uno(importador=lambda url: _ficha("B075SDMMMV", 135.0))
    assert r["hecho"] is True and r["detener"] is False
    p = cola.cat.obtener(r["producto_id"])
    assert p.marca == "LEGO" and p.precio_usd == 135.0
    assert p.estado == "borrador"          # nada se publica solo
    assert p.pictures and p.descripcion    # trae fotos y descripción
    assert p.costo_total_ars > 0           # ya calculó el costo
    assert cola.estado()["listos"] == 1


def test_lote_procesa_varios(cola):
    cola.encolar(["B0000000A1", "B0000000A2", "B0000000A3"])
    r = cola.procesar_lote(maximo=3, importador=lambda url: _ficha("B0000000A1"),
                           dormir=lambda s: None)
    assert r["listos"] == 3 and r["pendientes"] == 0


def test_se_detiene_cuando_amazon_bloquea(cola):
    """Lo importante: ante un bloqueo se frena y NO se pierde nada."""
    cola.encolar(["B0000000A1", "B0000000A2", "B0000000A3"])
    r = cola.procesar_lote(maximo=3, dormir=lambda s: None,
                           importador=lambda url: _ficha("B0000000A1", bloqueado=True,
                                                         mensaje="429"))
    assert r["detener"] is True and r["motivo"] == "bloqueado"
    assert r["bloqueados"] == 1
    assert r["pendientes"] == 2            # los otros siguen en cola, intactos
    assert r["listos"] == 0
    assert len(r["procesados"]) == 1       # cortó en el primero, no insistió


def test_reactivar_bloqueados_para_continuar_otro_dia(cola):
    cola.encolar(["B0000000A1"])
    cola.procesar_lote(maximo=1, dormir=lambda s: None,
                       importador=lambda url: _ficha("B0000000A1", bloqueado=True))
    assert cola.estado()["bloqueados"] == 1
    e = cola.reactivar_bloqueados()
    assert e["bloqueados"] == 0 and e["pendientes"] == 1


def test_sin_precio_queda_como_error_pero_la_cola_sigue(cola):
    cola.encolar(["B0000000A1", "B0000000A2"])
    r = cola.procesar_lote(maximo=2, dormir=lambda s: None,
                           importador=lambda url: {**_ficha("B0000000A1"),
                                                   "precio_usd": None})
    assert r["detener"] is False           # no frena: es un problema del producto
    assert r["errores"] == 2


def test_descarta_lo_que_no_es_set_lego(cola):
    """Un accesorio de terceros no debe entrar al catálogo, y la cola sigue."""
    cola.encolar(["B0000000A1", "B0000000A2"])
    accesorio = {**_ficha("B0000000A1", 25.99),
                 "marca": "BRIKSMAX",
                 "modelo": "Juego de luces LED compatibles con Lego Ferrari"}
    r = cola.procesar_uno(importador=lambda url: accesorio)
    assert r["hecho"] is False and r["motivo"] == "descartado"
    assert r["detener"] is False          # sigue con el resto
    assert cola.estado()["descartados"] == 1
    assert cola.cat.todos() == []         # no se creó producto


def test_acepta_set_lego_de_verdad(cola):
    cola.encolar(["B0000000A1"])
    set_lego = {**_ficha("B0000000A1", 199.99),
                "marca": "LEGO", "modelo": "LEGO Icons Ghostbusters ECTO-1 10274"}
    r = cola.procesar_uno(importador=lambda url: set_lego)
    assert r["hecho"] is True
    assert cola.cat.obtener(r["producto_id"]).marca == "LEGO"


def test_filtro_se_puede_desactivar(cola):
    cola.solo_lego = False
    cola.encolar(["B0000000A1"])
    accesorio = {**_ficha("B0000000A1", 25.99),
                 "modelo": "Luces LED compatibles con Lego"}
    assert cola.procesar_uno(importador=lambda url: accesorio)["hecho"] is True


def test_limpiar_terminados(cola):
    cola.encolar(["B0000000A1"])
    cola.procesar_lote(maximo=1, dormir=lambda s: None,
                       importador=lambda url: _ficha("B0000000A1"))
    assert cola.limpiar_terminados()["listos"] == 0


# ---- endpoints -----------------------------------------------------------

def test_endpoints_de_la_cola(tmp_path):
    c = TestClient(crear_app(db_path=str(tmp_path / "t.db")))
    r = c.post("/api/importar/encolar",
               json={"entradas": "https://www.amazon.com/dp/B075SDMMMV\nB0BBHHT8LY"}).json()
    assert r["nuevos"] == 2
    assert c.get("/api/importar/estado").json()["pendientes"] == 2
    assert c.post("/api/importar/reactivar").status_code == 200
    assert c.post("/api/importar/limpiar").status_code == 200


def test_bookmarklet_de_pagina_encola(tmp_path):
    c = TestClient(crear_app(db_path=str(tmp_path / "t.db")))
    r = c.get("/importar/capturar", params={"asins": "B0000000A1,B0000000A2"})
    assert r.status_code == 200 and "2 producto(s) encolado(s)" in r.text
    assert c.get("/api/importar/estado").json()["pendientes"] == 2
