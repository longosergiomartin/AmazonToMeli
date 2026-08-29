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
    cola.cat.filtro = {"marca": "", "descartar_accesorios": False,
                       "precio_min_usd": 0}
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


def _pagina_amazon(titulo, marca_byline, set_id, precio="59.99"):
    return f"""<html><span id="productTitle">{titulo}</span>
      <a id="bylineInfo">{marca_byline}</a>
      <span class="a-offscreen">${precio}</span>
      <table><tr><th>Marca</th><td>LEGO</td></tr>
             <tr><th>Número de modelo del artículo</th><td>{set_id}</td></tr></table>
      </html>"""


def test_la_cola_deja_el_titulo_y_el_set_listos(tmp_path, monkeypatch):
    """El arreglo de raíz: encolar una página de Amazon deja el borrador con la
    marca limpia, el número de set del fabricante y un título de MercadoLibre
    que conserva los dos — sin depender de que el título de Amazon los traiga."""
    import amazon_import
    from db import conectar
    from arbitraje.config import Config
    from catalogo import Catalogo
    from importador import ColaImportacion

    # Título traducido sin número de set: el caso que venía fallando.
    titulo = "Set de construcción Star Wars de LEGO, Darth Vader, talla única"

    class _Resp:
        status_code = 200
        text = _pagina_amazon(titulo, "Visit the LEGO Store", "75304")

    monkeypatch.setattr(amazon_import.requests, "get", lambda *a, **k: _Resp())

    cat = Catalogo(conectar(str(tmp_path / "c.db")), cfg=Config(),
                   cotizacion={"oficial": 1000.0, "tarjeta": 1300.0})
    cola = ColaImportacion(cat.conn, cat)
    cola.encolar(["B0TESTAAAA"])
    r = cola.procesar_uno()

    assert r["hecho"] is True, r
    p = cat.todos()[0]
    assert p.marca == "LEGO"
    assert p.modelo_fabricante == "75304"          # de la ficha, no del título
    assert p.titulo_ml.startswith("LEGO ")
    assert p.titulo_ml.endswith("75304")           # sobrevive al límite de 60
    assert len(p.titulo_ml) <= 60


def test_si_la_ficha_no_declara_el_set_se_usa_el_del_titulo(tmp_path, monkeypatch):
    import amazon_import
    from db import conectar
    from arbitraje.config import Config
    from catalogo import Catalogo
    from importador import ColaImportacion

    titulo = "LEGO Star Wars Death Star 75339 Kit de construcción (802 piezas)"

    class _Resp:
        status_code = 200
        text = _pagina_amazon(titulo, "LEGO", "")   # sin número en la ficha

    monkeypatch.setattr(amazon_import.requests, "get", lambda *a, **k: _Resp())

    cat = Catalogo(conectar(str(tmp_path / "c2.db")), cfg=Config(),
                   cotizacion={"oficial": 1000.0, "tarjeta": 1300.0})
    cola = ColaImportacion(cat.conn, cat)
    cola.encolar(["B0TESTBBBB"])
    cola.procesar_uno()

    assert cat.todos()[0].modelo_fabricante == "75339"


def test_sin_marca_configurada_entra_cualquier_rubro(cola):
    """El pedido explícito: la herramienta tiene que servir para más que LEGO."""
    cola.cat.filtro = {"marca": "", "descartar_accesorios": True, "precio_min_usd": 25}
    cola.encolar(["B0BOSCH001"])
    ficha = {**_ficha("B0BOSCH001", 129.0),
             "modelo": "Bosch Professional GSB 13 RE Taladro percutor 600W",
             "marca": "Bosch"}
    assert cola.procesar_uno(importador=lambda url: ficha)["hecho"] is True
    assert cola.cat.todos()[0].marca == "Bosch"


def test_con_marca_configurada_se_descarta_el_resto(cola):
    cola.cat.filtro = {"marca": "LEGO", "descartar_accesorios": True,
                       "precio_min_usd": 25}
    cola.encolar(["B0BOSCH002"])
    ficha = {**_ficha("B0BOSCH002", 129.0),
             "modelo": "Bosch Taladro percutor", "marca": "Bosch"}
    r = cola.procesar_uno(importador=lambda url: ficha)
    assert r["hecho"] is False and "no es LEGO" in r["mensaje"]


def test_los_accesorios_se_descartan_aunque_no_haya_marca(cola):
    cola.cat.filtro = {"marca": "", "descartar_accesorios": True, "precio_min_usd": 0}
    cola.encolar(["B0ACCES001"])
    ficha = {**_ficha("B0ACCES001", 40.0),
             "modelo": "Vitrina acrílica para exhibir tu colección", "marca": "Genérica"}
    assert cola.procesar_uno(importador=lambda url: ficha)["hecho"] is False


def test_por_proxy_no_pausa_entre_productos(cola, monkeypatch):
    """La pausa existe para no golpear a Amazon. Con proxy es su trabajo, y
    esperar de más hace que el lote tarde el triple sin ganar nada."""
    monkeypatch.setenv("SCRAPER_API_KEY", "clave-de-prueba")
    cola.encolar(["https://www.amazon.com/dp/B0PAUSA0001",
                  "https://www.amazon.com/dp/B0PAUSA0002"])
    esperas = []
    cola.procesar_lote(maximo=2, pausa_seg=2.0,
                       importador=lambda u: {"ok": True, "asin": "B0PAUSA0001",
                                             "modelo": "Cosa", "precio_usd": 10.0,
                                             "imagenes": [], "marca": "X"},
                       dormir=esperas.append)
    assert all(e <= 0.2 for e in esperas), esperas


def test_sin_proxy_mantiene_la_pausa(cola, monkeypatch):
    monkeypatch.delenv("SCRAPER_API_KEY", raising=False)
    cola.encolar(["https://www.amazon.com/dp/B0PAUSA0003",
                  "https://www.amazon.com/dp/B0PAUSA0004"])
    esperas = []
    cola.procesar_lote(maximo=2, pausa_seg=2.0,
                       importador=lambda u: {"ok": True, "asin": "B0PAUSA0003",
                                             "modelo": "Cosa", "precio_usd": 10.0,
                                             "imagenes": [], "marca": "X"},
                       dormir=esperas.append)
    assert esperas == [2.0]


def test_no_guarda_el_codigo_interno_de_amazon_como_numero_de_set(cola):
    """Amazon declara 6474652 como "modelo" del set 21350. Ese número no
    identifica nada ni encuentra el producto en el catálogo de MercadoLibre."""
    cola.encolar(["https://www.amazon.com/dp/B0JAWS00001"])
    cola.procesar_uno(importador=lambda u: {
        "ok": True, "asin": "B0JAWS00001", "marca": "LEGO",
        "modelo": "LEGO Ideas Jaws Set 21350 – Kit de diorama",
        "modelo_fabricante": "6474652", "precio_usd": 150.0, "imagenes": []})

    p = cola.cat.todos()[0]
    assert p.modelo_fabricante == "21350"


def test_sin_numero_en_el_titulo_tampoco_guarda_el_codigo_interno(cola):
    """Vacío es mejor que un número que no sirve: se ve que falta y se corrige."""
    cola.encolar(["https://www.amazon.com/dp/B0JAWS00002"])
    cola.procesar_uno(importador=lambda u: {
        "ok": True, "asin": "B0JAWS00002", "marca": "LEGO",
        "modelo": "LEGO Ideas Jaws Kit de diorama",
        "modelo_fabricante": "6474652", "precio_usd": 150.0, "imagenes": []})

    assert cola.cat.todos()[0].modelo_fabricante == ""


def test_un_numero_de_set_declarado_por_amazon_si_se_guarda(cola):
    cola.encolar(["https://www.amazon.com/dp/B0JAWS00003"])
    cola.procesar_uno(importador=lambda u: {
        "ok": True, "asin": "B0JAWS00003", "marca": "LEGO",
        "modelo": "LEGO Ideas Kit de diorama",
        "modelo_fabricante": "21350", "precio_usd": 150.0, "imagenes": []})

    assert cola.cat.todos()[0].modelo_fabricante == "21350"


# ---- envío a Argentina ---------------------------------------------------

def _ficha_envio(asin, envia):
    d = _ficha(asin)
    d["envia_al_exterior"] = envia
    return d


def test_no_encola_lo_que_amazon_no_manda_al_exterior(cola):
    """Pasó de verdad: entraron productos que después resultaron sin envío a
    Argentina, y se publicó algo que no se puede entregar."""
    cola.encolar(["B0NOENVIA1"])
    r = cola.procesar_uno(lambda url, **k: _ficha_envio("B0NOENVIA1", False))

    assert r["hecho"] is False and r["motivo"] == "descartado"
    assert "no lo envía" in r["mensaje"]
    assert cola.cat.todos() == []


def test_si_no_se_pudo_saber_el_envio_igual_entra(cola):
    """Leyendo desde EE.UU. el resultado normal es no saber. Descartar por eso
    dejaría afuera casi todo el catálogo."""
    cola.encolar(["B0NOSESAB1"])
    r = cola.procesar_uno(lambda url, **k: _ficha_envio("B0NOSESAB1", None))

    assert r["hecho"] is True
    assert len(cola.cat.todos()) == 1


def test_apagando_el_filtro_entra_igual_el_que_no_manda(cola):
    cola.cat.filtro = {"exigir_envio": False}
    cola.encolar(["B0NOENVIA2"])
    r = cola.procesar_uno(lambda url, **k: _ficha_envio("B0NOENVIA2", False))
    assert r["hecho"] is True


def test_el_pais_de_lectura_llega_al_importador(cola):
    """Con "ar" Amazon contesta si el producto llega acá: si el país no viaja
    hasta la descarga, el filtro nunca puede saber nada."""
    cola.cat.filtro = {"pais_lectura": "ar"}
    cola.encolar(["B0PAIS0001"])
    visto = {}

    def _lector(url, pais="us"):
        visto["pais"] = pais
        return _ficha_envio("B0PAIS0001", None)

    cola.procesar_uno(_lector)
    assert visto["pais"] == "ar"
