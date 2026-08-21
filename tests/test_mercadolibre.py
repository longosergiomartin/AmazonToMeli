"""Tests del cliente de MercadoLibre y OAuth con una sesión HTTP falsa."""

import time

import pytest

from db import conectar

from mercadolibre.client import MeliClient, MeliAPIError
from mercadolibre.oauth import TokenStore


class _FakeResp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


class _FakeSession:
    """Sesión que registra las llamadas y devuelve respuestas programadas."""
    def __init__(self, respuestas):
        self.respuestas = respuestas   # lista de (status, data)
        self.llamadas = []

    def request(self, metodo, url, **kw):
        self.llamadas.append((metodo, url, kw))
        status, data = self.respuestas.pop(0)
        return _FakeResp(status, data)


def _client(respuestas):
    ses = _FakeSession(respuestas)
    c = MeliClient(token_provider=lambda: "TOKEN", session=ses)
    return c, ses


def test_publicar_manda_post_con_token():
    c, ses = _client([(201, {"id": "MLA999", "permalink": "http://ml/x"})])
    item = {"title": "Test", "price": 1000}
    r = c.publicar(item)
    assert r["id"] == "MLA999"
    metodo, url, kw = ses.llamadas[0]
    assert metodo == "POST" and url.endswith("/items")
    assert kw["headers"]["Authorization"] == "Bearer TOKEN"
    assert kw["json"] == item


def test_pausar_usa_put_status():
    c, ses = _client([(200, {"id": "MLA1", "status": "paused"})])
    c.pausar("MLA1")
    metodo, url, kw = ses.llamadas[0]
    assert metodo == "PUT" and url.endswith("/items/MLA1")
    assert kw["json"] == {"status": "paused"}


def test_actualizar_precio_y_stock():
    c, ses = _client([(200, {}), (200, {})])
    c.actualizar_precio("MLA1", 5000)
    c.actualizar_stock("MLA1", 3)
    assert ses.llamadas[0][2]["json"] == {"price": 5000}
    assert ses.llamadas[1][2]["json"] == {"available_quantity": 3}


def test_atributos_obligatorios_filtra_requeridos():
    attrs = [
        {"id": "BRAND", "name": "Marca", "value_type": "string",
         "tags": {"required": True}, "values": []},
        {"id": "COLOR", "name": "Color", "value_type": "string",
         "tags": {}, "values": []},
    ]
    c, ses = _client([(200, attrs)])
    req = c.atributos_obligatorios("MLA1")
    assert len(req) == 1 and req[0]["id"] == "BRAND"


def test_buscar_listados_usa_catalogo_primero():
    catalogo = {"results": [{"id": "MLA123", "name": "Lego Simba 43243"}]}
    ofertas = {"results": [
        {"title": "Lego Simba", "price": 590000, "permalink": "http://ml/1",
         "shipping": {"free_shipping": True}, "sold_quantity": 3},
        {"title": "Lego Simba otro", "price": 400000, "permalink": "http://ml/2",
         "shipping": {}},
        {"title": "Sin precio", "permalink": "http://ml/3"},
    ]}
    c, ses = _client([(200, catalogo), (200, ofertas)])
    res = c.buscar_listados("lego simba")
    assert res["via"] == "catalogo" and res["producto"] == "Lego Simba 43243"
    assert len(res["items"]) == 2  # descarta el que no tiene precio
    assert res["items"][0]["precio"] == 590000
    assert "/products/search" in ses.llamadas[0][1]
    assert "/products/MLA123/items" in ses.llamadas[1][1]


def test_buscar_listados_cae_a_busqueda_si_catalogo_falla():
    busqueda = {"results": [{"title": "Lego", "price": 500000,
                             "permalink": "http://ml/9", "shipping": {}}]}
    # catálogo 403 → intenta /sites/MLA/search
    c, ses = _client([(403, {"message": "forbidden"}), (200, busqueda)])
    res = c.buscar_listados("lego simba")
    assert res["via"] == "busqueda" and res["items"][0]["precio"] == 500000


def test_buscar_listados_error_si_todo_bloqueado():
    c, ses = _client([(403, {"message": "forbidden"}), (403, {"message": "forbidden"})])
    with pytest.raises(MeliAPIError):
        c.buscar_listados("lego simba")


def test_error_http_se_convierte_en_excepcion():
    c, ses = _client([(400, {"message": "invalid", "cause": []})])
    with pytest.raises(MeliAPIError) as e:
        c.publicar({})
    assert e.value.status == 400


def test_token_store_guarda_y_expira():
    conn = conectar(":memory:")
    store = TokenStore(conn)
    assert store.hay_sesion() is False
    store.guardar({"access_token": "A", "refresh_token": "R",
                   "user_id": 1, "expires_in": 21600})
    row = store.leer()
    assert row["access_token"] == "A"
    assert row["expires_at"] > time.time() + 21000
    store.borrar()
    assert store.hay_sesion() is False


class _ReqFalso:
    """Cliente con las respuestas del catálogo de MercadoLibre a mano."""

    def __init__(self, productos, fichas):
        self.productos, self.fichas = productos, fichas
        self.pedidos = []

    def __call__(self, metodo, path, **kw):
        self.pedidos.append(path)
        if path == "/products/search":
            return {"results": self.productos}
        return self.fichas.get(path, {})


def _cli_con(productos, fichas):
    from mercadolibre.client import MeliClient
    cli = MeliClient(token_provider=lambda: "t", site="MLA")
    cli._req = _ReqFalso(productos, fichas)
    return cli


def test_gtin_del_catalogo_de_mercadolibre():
    """La fuente confiable del código de barras: los sets ya están en el
    catálogo de ML con su GTIN, no hay que salir a adivinarlo por la web."""
    cli = _cli_con(
        [{"id": "MLA123", "name": "LEGO Star Wars 75339 Death Star Trash Compactor"}],
        {"/products/MLA123": {"attributes": [
            {"id": "BRAND", "value_name": "LEGO"},
            {"id": "GTIN", "value_name": "5702017155326"}]}})
    r = cli.gtin_de_catalogo("LEGO 75339", debe_contener="75339")
    assert r["gtin"] == "5702017155326" and r["product_id"] == "MLA123"


def test_gtin_del_catalogo_descarta_el_producto_equivocado():
    """Publicar el código de otro set es peor que no publicar ninguno: si el
    número de set no está en el nombre del candidato, se descarta."""
    cli = _cli_con(
        [{"id": "MLA999", "name": "LEGO Star Wars 75192 Millennium Falcon"}],
        {"/products/MLA999": {"attributes": [
            {"id": "GTIN", "value_name": "5702016109818"}]}})
    assert cli.gtin_de_catalogo("LEGO 75339", debe_contener="75339") == {}


def test_gtin_del_catalogo_sin_resultados():
    cli = _cli_con([], {})
    assert cli.gtin_de_catalogo("LEGO 99999", debe_contener="99999") == {}


def test_gtin_del_catalogo_producto_sin_ese_atributo():
    cli = _cli_con(
        [{"id": "MLA1", "name": "LEGO 75339 set"}],
        {"/products/MLA1": {"attributes": [{"id": "BRAND", "value_name": "LEGO"}]}})
    assert cli.gtin_de_catalogo("LEGO 75339", debe_contener="75339") == {}


def test_ficha_de_catalogo_por_nombre_cuando_no_hay_numero():
    """La vía que faltaba: si el producto no tiene número de modelo, se busca
    por nombre con un mínimo de parecido."""
    cli = _cli_con(
        [{"id": "MLA55", "name": "Bosch Taladro Percutor Profesional 600W"}],
        {"/products/MLA55": {"attributes": [
            {"id": "GTIN", "value_name": "3165140857710"}]}})
    r = cli.ficha_de_catalogo("Bosch taladro percutor",
                              parecido_a="Bosch Taladro percutor profesional")
    assert r["gtin"] == "3165140857710"


def test_ficha_de_catalogo_por_nombre_descarta_lo_distinto():
    cli = _cli_con(
        [{"id": "MLA66", "name": "Cafetera Oster express"}],
        {"/products/MLA66": {"attributes": [
            {"id": "GTIN", "value_name": "3165140857710"}]}})
    assert cli.ficha_de_catalogo("Bosch taladro",
                                 parecido_a="Bosch Taladro percutor") == {}


def test_ficha_de_catalogo_por_nombre_no_agarra_otro_set():
    cli = _cli_con(
        [{"id": "MLA77", "name": "Lego Ideas Magic Of Disney 43222"}],
        {"/products/MLA77": {"attributes": [
            {"id": "GTIN", "value_name": "5702016914498"}]}})
    r = cli.ficha_de_catalogo("LEGO Ideas Magic of Disney",
                              parecido_a="LEGO Ideas Magic of Disney 21352")
    assert r == {}


# ---- casos reales del diagnóstico contra el catálogo de MLA --------------
# Los nombres son los que devolvió MercadoLibre de verdad, copiados de la
# pantalla de diagnóstico. Sin la marca como guarda, el primer resultado que
# contiene el número gana, y suele no ser el producto.

def test_no_agarra_un_babero_como_ficha_del_set_de_lego():
    """Buscando "LEGO 75551" el catálogo devuelve primero un babero que trae
    ese número. El set de Minions viene segundo: hay que llegar hasta él."""
    cli = _cli_con(
        [{"id": "MLA1", "name": "Babero Para Bebés Little Treasure 75551"},
         {"id": "MLA2", "name": "Lego Minions Gru 75551 Brick-build Minions "
                                "And Their Lair Cantidad De Piezas 876"}],
        {"/products/MLA2": {"attributes": [
            {"id": "GTIN", "value_name": "5702016913354"}]}})
    r = cli.ficha_de_catalogo("LEGO 75551", debe_contener="75551", marca="LEGO")
    assert r["product_id"] == "MLA2"
    assert r["gtin"] == "5702016913354"


def test_descarta_repuestos_que_solo_comparten_el_numero():
    """Para el set 21042 el catálogo devuelve cinco repuestos IMC con ese
    número. Ninguno es LEGO: no hay ficha, y está bien que no la haya."""
    cli = _cli_con(
        [{"id": "MLA1", "name": "IMC 499 21042 401"},
         {"id": "MLA2", "name": "IMC 499 21042 534"},
         {"id": "MLA3", "name": "IMC 800 21042 065"}],
        {})
    assert cli.ficha_de_catalogo("LEGO 21042", debe_contener="21042",
                                 marca="LEGO") == {}


def test_encuentra_la_ficha_aunque_el_nombre_este_en_castellano():
    """El nombre en ML no se parece al de Amazon —está en castellano y con
    otro orden— pero es el producto: marca y número alcanzan."""
    cli = _cli_con(
        [{"id": "MLA23181154",
          "name": "Set De Construcción Lego Ideas 21352 1103 Piezas En Caja"}],
        {"/products/MLA23181154": {"attributes": [
            {"id": "GTIN", "value_name": "5702017424101"}]}})
    r = cli.ficha_de_catalogo("LEGO 21352", debe_contener="21352", marca="LEGO")
    assert r["product_id"] == "MLA23181154"


def test_el_numero_tiene_que_ser_palabra_suelta():
    """Como subcadena, "1103 Piezas" contiene "110" y cualquier set de tres
    dígitos engancharía fichas ajenas."""
    cli = _cli_con(
        [{"id": "MLA1", "name": "Lego Ideas 21352 1103 Piezas"}], {})
    assert cli.ficha_de_catalogo("LEGO 110", debe_contener="110",
                                 marca="LEGO") == {}


def test_marca_de_varias_palabras_no_matchea_con_media():
    cli = _cli_con(
        [{"id": "MLA1", "name": "Fisher Juguete 12345"}], {})
    assert cli.ficha_de_catalogo("Fisher Price 12345", debe_contener="12345",
                                 marca="Fisher Price") == {}
