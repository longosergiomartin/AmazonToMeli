"""Tests del cliente de MercadoLibre y OAuth con una sesión HTTP falsa."""

import time

import pytest

from db import conectar

from mercadolibre.client import MeliClient, MeliAPIError
from mercadolibre.oauth import TokenStore


class _FakeResp:
    def __init__(self, status, data, headers=None):
        self.status_code = status
        self._data = data
        self.text = str(data)
        self.headers = headers or {}

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
    # MercadoLibre contesta con el ítem actualizado, y ahí viene el precio.
    c, ses = _client([(200, {"id": "MLA1", "price": 5000}), (200, {})])
    c.actualizar_precio("MLA1", 5000)
    c.actualizar_stock("MLA1", 3)
    assert ses.llamadas[0][2]["json"] == {"price": 5000}
    assert ses.llamadas[1][2]["json"] == {"available_quantity": 3}


def test_si_el_precio_no_queda_no_se_da_por_hecho():
    """MercadoLibre puede contestar 200 y dejar el precio como estaba. Darlo
    por bueno es cómo se informan "114 publicaciones actualizadas" con todo
    igual del otro lado."""
    c, ses = _client([(200, {"id": "MLA1", "price": 9999})])
    with pytest.raises(MeliAPIError) as e:
        c.actualizar_precio("MLA1", 5000)
    assert "9999" in str(e.value) and "5000" in str(e.value)


def test_si_la_respuesta_no_trae_precio_se_relee_el_item():
    c, ses = _client([(200, {"id": "MLA1"}),                    # PUT sin precio
                      (200, {"id": "MLA1", "price": 5000})])    # GET del ítem
    c.actualizar_precio("MLA1", 5000)
    assert ses.llamadas[1][0] == "GET"


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


# ---- multiget: una llamada cada 20 ítems, no una por ítem ---------------

class _MultiFalso:
    """Responde el multiget de MercadoLibre y anota cuántas llamadas hubo."""

    def __init__(self, existen):
        self.existen, self.llamadas = set(existen), []

    def __call__(self, metodo, path, **kw):
        ids = (kw.get("params") or {}).get("ids", "").split(",")
        self.llamadas.append(ids)
        return [{"code": 200, "body": {"id": i, "status": "active"}}
                if i in self.existen else {"code": 404, "body": {}}
                for i in ids]


def _cli_multi(existen):
    cli = MeliClient(token_provider=lambda: "t", site="MLA")
    cli._req = _MultiFalso(existen)
    return cli


def test_obtener_varios_agrupa_de_a_veinte():
    """Una llamada HTTPS por producto hacía que la verificación se cortara
    antes de terminar. El multiget acepta 20 ids por vez."""
    ids = [f"MLA{n}" for n in range(45)]
    cli = _cli_multi(ids)
    r = cli.obtener_varios(ids)

    assert len(r) == 45
    assert len(cli._req.llamadas) == 3          # 20 + 20 + 5, no 45
    assert [len(l) for l in cli._req.llamadas] == [20, 20, 5]


def test_obtener_varios_deja_afuera_lo_que_no_existe():
    """Así el que llama distingue el ítem que no está del que sí."""
    cli = _cli_multi(["MLA1"])
    r = cli.obtener_varios(["MLA1", "MLA_FANTASMA"])
    assert "MLA1" in r and "MLA_FANTASMA" not in r


def test_obtener_varios_sin_ids_no_llama():
    cli = _cli_multi([])
    assert cli.obtener_varios(["", None]) == {}
    assert cli._req.llamadas == []


def test_obtener_varios_aguanta_respuestas_con_otra_forma():
    """La forma exacta del multiget no se puede probar sin la API de verdad.
    Si viene distinta, tiene que devolver lo que entienda —o nada— pero nunca
    reventar: una excepción acá es un 500 y el panel muestra un error vacío."""
    from mercadolibre.client import MeliClient

    for respuesta in ({"error": "algo"},           # dict en vez de lista
                      [None, "basura", 42],        # elementos no-dict
                      [{"code": 404, "body": {}}], # nada existe
                      []):
        cli = MeliClient(token_provider=lambda: "t", site="MLA")
        cli._req = lambda m, p, **kw: respuesta
        assert cli.obtener_varios(["MLA1"]) == {}

    # El ítem pelado, sin el envoltorio {code, body}.
    cli = MeliClient(token_provider=lambda: "t", site="MLA")
    cli._req = lambda m, p, **kw: [{"id": "MLA1", "status": "active"}]
    assert cli.obtener_varios(["MLA1"])["MLA1"]["status"] == "active"


def test_el_aviso_de_mercadolibre_llega_al_mensaje():
    """MercadoLibre explica en `warnings` por qué no aplicó el precio. Es la
    diferencia entre saber que no se aplicó y saber por qué."""
    c, ses = _client([(200, {"id": "MLA1", "price": 9999, "warnings": [
        {"code": "price_not_updated",
         "message": "El precio no se actualizó: usá la API de precios."}]})])
    with pytest.raises(MeliAPIError) as e:
        c.actualizar_precio("MLA1", 5000)
    assert "API de precios" in str(e.value)


def test_sin_aviso_el_mensaje_sigue_siendo_claro():
    c, ses = _client([(200, {"id": "MLA1", "price": 9999})])
    with pytest.raises(MeliAPIError) as e:
        c.actualizar_precio("MLA1", 5000)
    assert "9999" in str(e.value)


# ---- editar publicaciones vivas ------------------------------------------

def test_cambiar_el_titulo_usa_title_y_una_sola_llamada():
    """`family_name` solo existe al crear la publicación. Reintentar con él en
    una que ya existe gastaba una segunda llamada condenada por producto —el
    doble de pedidos, que es lo que hacía saltar el límite de MercadoLibre— y
    encima tapaba el error de `title`, que es el que explica qué pasó."""
    c, ses = _client([(200, {"id": "MLA1", "title": "Set LEGO 21181"})])
    c.actualizar_titulo("MLA1", "Set LEGO 21181")

    assert len(ses.llamadas) == 1
    metodo, url, kw = ses.llamadas[0]
    assert metodo == "PUT" and kw["json"] == {"title": "Set LEGO 21181"}


def test_el_error_del_titulo_se_explica_en_castellano():
    c, ses = _client([(400, {"message": "Item is a catalog_listing",
                             "cause": []})])
    with pytest.raises(MeliAPIError) as e:
        c.actualizar_titulo("MLA1", "Set LEGO 21181")
    assert "catálogo" in str(e.value) and "republicarla" in str(e.value)


def test_un_titulo_que_ml_acepta_pero_no_aplica_no_cuenta_como_exito():
    """El PUT puede volver 200 con el título anterior: dar eso por bueno es
    cómo se informan 115 publicaciones actualizadas sin haber cambiado nada."""
    c, ses = _client([(200, {"id": "MLA1", "title": "El viejo"})])
    with pytest.raises(MeliAPIError) as e:
        c.actualizar_titulo("MLA1", "Set LEGO 21181")
    assert "El viejo" in str(e.value)


def test_la_descripcion_reintenta_con_text_si_rechaza_plain_text():
    """ML discontinuó `plain_text` en parte de su catálogo y contesta
    DESCRIPTION_PLAIN_TEXT_NOT_ALLOWED."""
    c, ses = _client([
        (400, {"message": "DESCRIPTION_PLAIN_TEXT_NOT_ALLOWED", "cause": []}),
        (200, {"text": "ok"}),
    ])
    c.poner_descripcion("MLA1", "Hola")

    assert [k["json"] for _, _, k in ses.llamadas] == [
        {"plain_text": "Hola"}, {"text": "Hola"}]


def test_la_descripcion_pasa_a_put_si_el_post_no_es_por_el_formato():
    """Si ya tiene descripción, el POST rebota por existir, no por el texto:
    ahí probar otro campo no arregla nada, hay que reemplazarla con PUT."""
    c, ses = _client([
        (400, {"message": "Body already exists", "cause": []}),
        (200, {"plain_text": "ok"}),
    ])
    c.poner_descripcion("MLA1", "Hola")

    assert [m for m, _, _ in ses.llamadas] == ["POST", "PUT"]


def test_espera_y_reintenta_cuando_ml_limita_el_ritmo(monkeypatch):
    """Sin esto, cada producto que cae en el límite se pierde y hay que
    descubrir cuáles a mano."""
    dormido = []
    monkeypatch.setattr("mercadolibre.client.time.sleep", dormido.append)
    c, ses = _client([(429, {"message": "too_many_requests"}),
                      (200, {"id": "MLA1", "title": "Set LEGO 21181"})])

    c.actualizar_titulo("MLA1", "Set LEGO 21181")
    assert len(ses.llamadas) == 2
    assert dormido and dormido[0] >= 2.0


def test_el_limite_de_ritmo_no_reintenta_para_siempre(monkeypatch):
    monkeypatch.setattr("mercadolibre.client.time.sleep", lambda s: None)
    from mercadolibre.client import ESPERAS_429
    c, ses = _client([(429, {"message": "too_many_requests"})] * 10)
    with pytest.raises(MeliAPIError):
        c.actualizar_titulo("MLA1", "Set LEGO 21181")
    assert len(ses.llamadas) == len(ESPERAS_429) + 1
