"""Tests de los endpoints del catálogo y las guardas de publicación (sin red)."""

import pytest
from fastapi.testclient import TestClient

from api.server import crear_app


@pytest.fixture()
def client(tmp_path):
    return TestClient(crear_app(db_path=str(tmp_path / "t.db")))


def _alta(client, **kw):
    body = dict(asin="B0TEST", marca="HISEA", modelo="Waders", precio_usd=118.99,
                peso_kg=3.0, costo_envio_usd=84.35, margen_deseado=0.35, stock=5)
    body.update(kw)
    return client.post("/api/catalogo", json=body)


def test_alta_y_listado(client):
    r = _alta(client)
    assert r.status_code == 200
    p = r.json()
    assert p["id"] and p["costo_total_ars"] > 0 and p["precio_sugerido_ars"] > 0
    assert client.get("/api/catalogo").json()[0]["asin"] == "B0TEST"


def test_borrador_devuelve_preview_y_faltantes(client):
    pid = _alta(client).json()["id"]
    r = client.post(f"/api/catalogo/{pid}/borrador", json={"pictures": []})
    assert r.status_code == 200
    data = r.json()
    assert "preview" in data
    # Sin categoría ni fotos, debe faltar algo.
    assert any("foto" in f for f in data["faltantes"])


def test_publicar_bloqueado_si_no_esta_aprobado(client):
    pid = _alta(client, ml_category_id="MLA1", titulo_ml="Waders").json()["id"]
    r = client.post(f"/api/catalogo/{pid}/publicar",
                    json={"pictures": ["http://img/1.jpg"]})
    assert r.status_code == 409  # falta aprobación explícita


def test_publicar_bloqueado_si_faltan_datos(client):
    pid = _alta(client).json()["id"]
    client.post(f"/api/catalogo/{pid}/aprobar")
    r = client.post(f"/api/catalogo/{pid}/publicar", json={"pictures": []})
    assert r.status_code == 422  # faltan categoría/fotos


def test_publicar_sin_sesion_ml_da_401(client):
    pid = _alta(client, ml_category_id="MLA1", titulo_ml="Waders").json()["id"]
    client.post(f"/api/catalogo/{pid}/aprobar")
    r = client.post(f"/api/catalogo/{pid}/publicar",
                    json={"pictures": ["http://img/1.jpg"]})
    # Datos completos y aprobado, pero sin credenciales/sesión de MercadoLibre.
    assert r.status_code in (400, 401)


def test_precio_y_stock_actualizan(client):
    pid = _alta(client).json()["id"]
    assert client.patch(f"/api/catalogo/{pid}/precio", json={"precio": 400000}).json()["precio_publicado_ars"] == 400000
    assert client.patch(f"/api/catalogo/{pid}/stock", json={"stock": 9}).json()["stock"] == 9
    tipos = [h["tipo"] for h in client.get(f"/api/catalogo/{pid}/historial").json()]
    assert "precio" in tipos and "stock" in tipos


def test_margen_insuficiente_en_respuesta(client):
    pid = _alta(client).json()["id"]
    p = client.patch(f"/api/catalogo/{pid}/precio", json={"precio": 250000}).json()
    assert "margen_insuficiente" in p


def test_borrar_producto(client):
    pid = _alta(client).json()["id"]
    assert client.delete(f"/api/catalogo/{pid}").status_code == 200
    assert client.get(f"/api/catalogo/{pid}").status_code == 404


def test_cambiar_regimen_recalcula_costo(client):
    # Alta courier vs cambio a landed: landed usa el total sin re-sumar impuesto.
    pid = _alta(client, regimen="courier", precio_usd=839.97, costo_envio_usd=530.36).json()["id"]
    costo_courier = client.get(f"/api/catalogo/{pid}").json()["costo_total_ars"]
    p = client.patch(f"/api/catalogo/{pid}/regimen", json={"regimen": "landed"}).json()
    assert p["regimen"] == "landed"
    assert p["costo_total_ars"] < costo_courier


def test_cambiar_regimen_invalido(client):
    pid = _alta(client).json()["id"]
    assert client.patch(f"/api/catalogo/{pid}/regimen", json={"regimen": "x"}).status_code == 400


def test_precio_competitivo_muestra_margen(client):
    # Precio por debajo del sugerido: la app igual calcula el margen (aunque sea bajo).
    pid = _alta(client, regimen="landed", precio_usd=839.97, costo_envio_usd=530.36).json()["id"]
    p = client.patch(f"/api/catalogo/{pid}/precio", json={"precio": 3000000}).json()
    assert p["precio_publicado_ars"] == 3000000
    assert "margen_pct" in p and "margen_insuficiente" in p


def test_competencia_sin_sesion_ml_da_error_claro(client):
    pid = _alta(client, titulo_ml="Lego Simba").json()["id"]
    r = client.get(f"/api/catalogo/{pid}/competencia")
    assert r.status_code in (400, 401)  # sin credenciales/sesión de ML


def test_competencia_bloqueada_devuelve_link_manual(client, monkeypatch, tmp_path):
    """Si MercadoLibre bloquea la búsqueda (403), la pantalla no se rompe:
    devuelve el link para comparar a mano."""
    import api.catalogo_routes as rutas
    from mercadolibre.client import MeliAPIError

    pid = _alta(client, titulo_ml="Lego Simba 43243").json()["id"]

    class _CliFalso:
        def buscar_listados(self, *a, **k):
            raise MeliAPIError("MercadoLibre GET /sites/MLA/search → 403")

    # Inyectamos un cliente que simula el bloqueo de ML.
    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: _CliFalso())
    monkeypatch.setattr(rutas.MeliCredenciales, "configurado", property(lambda self: True))
    monkeypatch.setattr(rutas.TokenStore, "hay_sesion", lambda self: True)

    app = crear_app(db_path=str(tmp_path / "t2.db"))
    c2 = TestClient(app)
    pid2 = _alta(c2, titulo_ml="Lego Simba 43243").json()["id"]
    r = c2.get(f"/api/catalogo/{pid2}/competencia")
    assert r.status_code == 200
    data = r.json()
    assert data["items"] == [] and data["error"]
    assert "listado.mercadolibre.com.ar" in data["link_manual"]


def test_competencia_sin_titulo_da_400(client):
    pid = _alta(client, modelo="", asin="", titulo_ml="").json()["id"]
    assert client.get(f"/api/catalogo/{pid}/competencia").status_code == 400


def test_desglose_devuelve_ambas_variantes(client):
    # Como Responsable Inscripto, para que haya IVA y Ganancias en el desglose.
    client.patch("/api/fiscal", json={"condicion_fiscal": "responsable_inscripto"})
    pid = _alta(client, regimen="landed", precio_usd=126.0,
                costo_envio_usd=34.36).json()["id"]
    d = client.get(f"/api/catalogo/{pid}/desglose", params={"precio": 590000}).json()
    det = d["detalle"]
    # El neto estilo ML no descuenta impuestos argentinos; el conservador sí.
    assert d["neto_estilo_ml"] == pytest.approx(
        d["neto_conservador"] + det["impuestos_total"], abs=0.5)
    assert d["estilo_ml"]["margen_ars"] > d["conservador"]["margen_ars"]
    # El margen es neto - costo.
    assert d["conservador"]["margen_ars"] == pytest.approx(
        d["neto_conservador"] - d["costo_puesto_ars"], abs=0.5)
    for k in ("costos_ml", "iva", "ganancias", "iibb"):
        assert det[k] > 0
    assert det["costos_ml_pct"] == pytest.approx(16.0, abs=2)


def test_publicar_reintenta_con_title_si_ml_pide_family_name(tmp_path, monkeypatch):
    """Si la categoría rechaza `family_name`, se reintenta con `title`."""
    import api.catalogo_routes as rutas
    from mercadolibre.client import MeliAPIError

    enviados = []

    class _CliFalso:
        def ficha_de_catalogo(self, *a, **k):
            return {}

        def atributos_obligatorios(self, cat_id):
            return []

        def valores_permitidos(self, cat_id):
            return {}

        def publicar(self, item):
            enviados.append(item)
            if "family_name" in item:
                raise MeliAPIError("rechazo", status=400,
                                   cuerpo={"error": "The fields [family_name] are invalid"})
            return {"id": "MLA999", "permalink": "http://ml/x"}

        def poner_descripcion(self, item_id, texto):
            return {}

    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: _CliFalso())
    monkeypatch.setattr(rutas.MeliCredenciales, "configurado", property(lambda self: True))
    monkeypatch.setattr(rutas.TokenStore, "hay_sesion", lambda self: True)

    c = TestClient(crear_app(db_path=str(tmp_path / "t.db")))
    pid = _alta(c, titulo_ml="LEGO Star Wars", ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://img/1.jpg"]})
    c.post(f"/api/catalogo/{pid}/aprobar")
    r = c.post(f"/api/catalogo/{pid}/publicar", json={})

    assert r.status_code == 200 and r.json()["ml_item_id"] == "MLA999"
    assert len(enviados) == 2                     # primer intento + reintento
    assert "family_name" in enviados[0] and "title" not in enviados[0]
    assert "title" in enviados[1] and "family_name" not in enviados[1]


def test_publicar_manda_la_marca_limpia_con_value_id(tmp_path, monkeypatch):
    """El caso real: el producto quedó guardado con "Visit the LEGO Store" y ML
    lo rechazaba. Ahora se resuelve contra los valores de la categoría."""
    import api.catalogo_routes as rutas

    enviados = []

    class _CliFalso:
        def ficha_de_catalogo(self, *a, **k):
            return {}

        def atributos_obligatorios(self, cat_id):
            return [{"id": "BRAND", "name": "Marca"}]

        def valores_permitidos(self, cat_id):
            return {"BRAND": [{"id": "9155", "name": "LEGO"}]}

        def publicar(self, item):
            enviados.append(item)
            return {"id": "MLA123", "permalink": "http://ml/x"}

        def poner_descripcion(self, item_id, texto):
            return {}

    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: _CliFalso())
    monkeypatch.setattr(rutas.MeliCredenciales, "configurado", property(lambda self: True))
    monkeypatch.setattr(rutas.TokenStore, "hay_sesion", lambda self: True)

    c = TestClient(crear_app(db_path=str(tmp_path / "marca.db")))
    pid = _alta(c, marca="Visit the LEGO Store", titulo_ml="LEGO Icons ECTO-1 10274",
                ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://img/1.jpg"]})
    c.post(f"/api/catalogo/{pid}/aprobar")
    r = c.post(f"/api/catalogo/{pid}/publicar", json={})

    assert r.status_code == 200, r.text
    brand = next(a for a in enviados[0]["attributes"] if a["id"] == "BRAND")
    assert brand == {"id": "BRAND", "value_id": "9155"}


def test_publicar_usa_la_marca_del_titulo_si_amazon_no_la_trajo(tmp_path, monkeypatch):
    """Caso real: la cola importó el producto sin marca (Amazon no respondió el
    byline) y ML no lista valores para BRAND. Antes se mandaba el atributo vacío
    y ML rechazaba con "The attributes [BRAND] are required"."""
    import api.catalogo_routes as rutas

    enviados = []

    class _CliFalso:
        def ficha_de_catalogo(self, *a, **k):
            return {}

        def atributos_obligatorios(self, cat_id):
            return [{"id": "BRAND", "name": "Marca"}]

        def valores_permitidos(self, cat_id):
            return {}  # ML no devuelve valores para BRAND en esta categoría

        def publicar(self, item):
            enviados.append(item)
            return {"id": "MLA456", "permalink": "http://ml/x"}

        def poner_descripcion(self, item_id, texto):
            return {}

    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: _CliFalso())
    monkeypatch.setattr(rutas.MeliCredenciales, "configurado", property(lambda self: True))
    monkeypatch.setattr(rutas.TokenStore, "hay_sesion", lambda self: True)

    c = TestClient(crear_app(db_path=str(tmp_path / "sinmarca.db")))
    pid = _alta(c, marca="", titulo_ml="LEGO Technic Ferrari Daytona SP3 42143",
                ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://img/1.jpg"]})
    c.post(f"/api/catalogo/{pid}/aprobar")
    r = c.post(f"/api/catalogo/{pid}/publicar", json={})

    assert r.status_code == 200, r.text
    brand = next(a for a in enviados[0]["attributes"] if a["id"] == "BRAND")
    assert brand["value_name"] == "LEGO"


def test_publicar_sin_marca_resoluble_avisa_antes_de_llamar_a_ml(tmp_path, monkeypatch):
    import api.catalogo_routes as rutas

    class _CliFalso:
        def ficha_de_catalogo(self, *a, **k):
            return {}

        def atributos_obligatorios(self, cat_id):
            return []

        def valores_permitidos(self, cat_id):
            return {}

        def publicar(self, item):
            raise AssertionError("no debería llegar a MercadoLibre")

    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: _CliFalso())
    monkeypatch.setattr(rutas.MeliCredenciales, "configurado", property(lambda self: True))
    monkeypatch.setattr(rutas.TokenStore, "hay_sesion", lambda self: True)

    c = TestClient(crear_app(db_path=str(tmp_path / "nomarca.db")))
    pid = _alta(c, marca="", titulo_ml="Set de bloques", modelo="",
                ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://img/1.jpg"]})
    c.post(f"/api/catalogo/{pid}/aprobar")
    r = c.post(f"/api/catalogo/{pid}/publicar", json={})

    assert r.status_code == 422
    assert "Marca" in str(r.json())


def test_editar_marca_y_modelo_desde_el_editor(client):
    pid = _alta(client, marca="Visit the LEGO Store", modelo="viejo").json()["id"]
    r = client.patch(f"/api/catalogo/{pid}/publicacion",
                     json={"marca": "LEGO", "modelo": "Icons ECTO-1 10274"})
    assert r.status_code == 200
    p = client.get(f"/api/catalogo/{pid}").json()
    assert p["marca"] == "LEGO" and p["modelo"] == "Icons ECTO-1 10274"


def test_payload_muestra_lo_que_se_le_manda_a_ml(client):
    """Endpoint de diagnóstico: qué viaja realmente, sin publicar."""
    pid = _alta(client, marca="Visit the LEGO Store",
                titulo_ml="LEGO Icons ECTO-1", ml_category_id="MLA1157").json()["id"]
    d = client.get(f"/api/catalogo/{pid}/payload").json()
    assert d["marca_guardada"] in ("Visit the LEGO Store", "LEGO")
    assert d["marca_resuelta"] == "LEGO"
    assert d["item"]["family_name"] == "LEGO Icons ECTO-1"
    assert any(a["id"] == "BRAND" for a in d["item"]["attributes"])


def test_vista_previa_muestra_la_marca_resuelta(client):
    pid = _alta(client, marca="Visit the LEGO Store", titulo_ml="LEGO Icons").json()["id"]
    v = client.post(f"/api/catalogo/{pid}/borrador", json={}).json()["preview"]
    assert v["marca"] == "LEGO"


def test_listar_catalogo_repara_las_marcas_viejas(tmp_path):
    """Al abrir el panel se corrigen las marcas que quedaron sucias, sin que el
    usuario tenga que hacer nada."""
    c = TestClient(crear_app(db_path=str(tmp_path / "rep.db")))
    _alta(c, marca="Visit the LEGO Store")
    assert c.get("/api/catalogo").json()[0]["marca"] == "LEGO"


def test_describir_error_separa_lo_que_bloquea_de_las_advertencias():
    from mercadolibre.client import describir_error
    cuerpo = {
        "cause": [
            {"type": "warning", "code": "item.attributes.value_name.invalid",
             "references": ["BRAND"], "message": "Attribute BRAND has an invalid value name."},
            {"type": "error", "code": "item.attributes.missing_required",
             "references": ["item.attributes"],
             "message": "The attributes [BRAND] are required for category MLA1157"},
            {"type": "warning", "code": "item.shipping.mandatory_free_shipping",
             "references": [], "message": "Mandatory free shipping added"},
        ],
        "message": "Validation error", "error": "validation_error", "status": 400,
    }
    texto = describir_error(cuerpo)
    lineas = texto.splitlines()
    assert lineas[0].startswith("The attributes [BRAND] are required")  # lo que bloquea
    assert "Advertencias (no bloquean)" in lineas[1]
    assert "Mandatory free shipping" in lineas[1]


def test_describir_error_sin_causas_no_se_rompe():
    from mercadolibre.client import describir_error
    assert "invalid" in describir_error(
        {"cause": [], "error": "The fields [title] are invalid.", "status": 400})
    assert describir_error("texto pelado") == "texto pelado"


def test_almacenamiento_avisa_si_no_es_persistente(client):
    a = client.get("/api/almacenamiento").json()
    # En tests corre sobre SQLite: debe avisar que no es persistente.
    assert a["persistente"] is False and "SQLite" in a["detalle"]


def test_dolar_costo_default_tarjeta(client):
    d = client.get("/api/dolar-costo").json()
    assert d["dolar_costo"] == "tarjeta"


def test_cambiar_a_dolar_oficial_baja_el_costo(client):
    # 135 USD + 26% = 170,10 USD. Al oficial el costo puesto es menor.
    pid = _alta(client, regimen="landed", precio_usd=135.0,
                costo_envio_usd=0.0).json()["id"]
    costo_tarjeta = client.get(f"/api/catalogo/{pid}").json()["costo_total_ars"]

    d = client.patch("/api/dolar-costo", json={"dolar_costo": "oficial"}).json()
    assert d["dolar_costo"] == "oficial"
    p = client.get(f"/api/catalogo/{pid}").json()
    assert p["costo_total_ars"] < costo_tarjeta
    # El costo al oficial coincide con la fila "oficial" de la comparación.
    assert p["costo_total_ars"] == pytest.approx(
        p["comparacion"]["oficial"]["costo_ars"], abs=1)
    # Y la comparación sigue mostrando ambas puntas distintas.
    assert p["comparacion"]["tarjeta"]["costo_ars"] > p["comparacion"]["oficial"]["costo_ars"]


def test_dolar_costo_invalido_da_400(client):
    assert client.patch("/api/dolar-costo", json={"dolar_costo": "blue"}).status_code == 400


def test_fiscal_default_monotributo(client):
    f = client.get("/api/fiscal").json()
    assert f["condicion_fiscal"] == "monotributo"
    assert f["iva_pct"] == 0 and f["ganancias_pct"] == 0


def test_cambiar_condicion_fiscal_recalcula_margenes(client):
    pid = _alta(client, regimen="landed", precio_usd=126.0).json()["id"]
    margen_mono = client.get(f"/api/catalogo/{pid}").json()["margen_pct"]

    f = client.patch("/api/fiscal", json={"condicion_fiscal": "responsable_inscripto"}).json()
    assert f["condicion_fiscal"] == "responsable_inscripto" and f["iva_pct"] == 21.0
    # Al RI el precio sugerido sube (tiene que cubrir IVA y Ganancias).
    p = client.get(f"/api/catalogo/{pid}").json()
    assert p["precio_sugerido_ars"] > 0
    # El margen al mismo precio publicado baja para el RI.
    client.patch(f"/api/catalogo/{pid}/precio", json={"precio": 600000})
    margen_ri = client.get(f"/api/catalogo/{pid}").json()["margen_pct"]
    client.patch("/api/fiscal", json={"condicion_fiscal": "monotributo"})
    margen_mono2 = client.get(f"/api/catalogo/{pid}").json()["margen_pct"]
    assert margen_mono2 > margen_ri


def test_condicion_fiscal_invalida_da_400(client):
    assert client.patch("/api/fiscal", json={"condicion_fiscal": "x"}).status_code == 400


def test_oauth_status_sin_credenciales(client):
    s = client.get("/oauth/status").json()
    assert s["configurado"] is False and s["conectado"] is False


def test_oauth_code_sin_code_da_400(client):
    assert client.post("/oauth/code", json={}).status_code == 400


def test_oauth_code_sin_credenciales_da_400(client):
    # Con code pero sin MELI_CLIENT_ID/SECRET configurados.
    r = client.post("/oauth/code", json={"url": "https://127.0.0.1:8321/oauth/callback?code=ABC123"})
    assert r.status_code == 400


def test_pausar_sin_publicar_cambia_estado_local(client):
    pid = _alta(client).json()["id"]
    r = client.post(f"/api/catalogo/{pid}/pausar")
    assert r.status_code == 200 and r.json()["estado"] == "pausado"


def test_editar_publicacion_completa_faltantes(client):
    pid = _alta(client, titulo_ml="Waders HISEA").json()["id"]
    # Faltan categoría, foto y atributos.
    b0 = client.post(f"/api/catalogo/{pid}/borrador", json={}).json()
    assert b0["faltantes"]
    # Cargamos categoría, atributos y foto.
    r = client.patch(f"/api/catalogo/{pid}/publicacion", json={
        "ml_category_id": "MLA66238",
        "ml_attributes": {"GENDER": "Hombre", "COLOR": "Camuflado", "SIZE": "44"},
        "pictures": ["https://img/1.jpg"],
    })
    assert r.status_code == 200
    p = client.get(f"/api/catalogo/{pid}").json()
    assert p["ml_category_id"] == "MLA66238" and p["pictures"] == ["https://img/1.jpg"]
    # Sin sesión ML no hay atributos obligatorios remotos: los básicos ya están.
    b1 = client.post(f"/api/catalogo/{pid}/borrador", json={}).json()
    assert not b1["faltantes"]


def test_pictures_persisten_para_publicar(client):
    pid = _alta(client, titulo_ml="Waders", ml_category_id="MLA1").json()["id"]
    client.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["https://img/1.jpg"]})
    client.post(f"/api/catalogo/{pid}/aprobar")
    # Publicar sin pasar pictures en el body: usa las guardadas → llega a pedir sesión ML.
    r = client.post(f"/api/catalogo/{pid}/publicar", json={})
    assert r.status_code in (400, 401)  # faltaría solo la sesión de ML, no las fotos


def _cli_lote(enviados, con_categoria=True, gtin_catalogo=None):
    class _Cli:
        def ficha_de_catalogo(self, query, debe_contener="", limit=5,
                              parecido_a="", minimo_parecido=0.5, marca=""):
            return dict(gtin_catalogo) if gtin_catalogo else {}

        def gtin_de_catalogo(self, query, debe_contener="", limit=5):
            f = self.ficha_de_catalogo(query, debe_contener, limit)
            return f if f.get("gtin") else {}

        def predecir_categoria(self, titulo):
            return [{"category_id": "MLA1157", "category_name": "Sets"}] if con_categoria else []

        def atributos_obligatorios(self, cat_id):
            return [{"id": "IVA", "name": "IVA", "values": ["0 %", "21 %"]},
                    {"id": "BRAND", "name": "Marca"},
                    {"id": "GTIN", "name": "Código universal de producto"}]

        def atributos(self, cat_id):
            return self.atributos_obligatorios(cat_id) + [
                {"id": "EMPTY_GTIN_REASON", "name": "Motivo de GTIN vacío",
                 "values": ["Es un kit", "Otra razón"]}]

        def valores_permitidos(self, cat_id):
            return {}

        def publicar(self, item):
            enviados.append(item)
            return {"id": "MLA" + str(len(enviados)), "permalink": "http://ml/x"}

        def poner_descripcion(self, item_id, texto):
            return {}
    return _Cli()


def _con_ml(monkeypatch, cli):
    import api.catalogo_routes as rutas
    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: cli)
    monkeypatch.setattr(rutas.MeliCredenciales, "configurado", property(lambda self: True))
    monkeypatch.setattr(rutas.TokenStore, "hay_sesion", lambda self: True)


def test_lote_preparar_completa_marca_categoria_y_atributos(tmp_path, monkeypatch):
    """Lo que evita cargar producto por producto: se deduce todo lo deducible."""
    _con_ml(monkeypatch, _cli_lote([]))
    monkeypatch.setattr("gtin_lookup.buscar_gtin",
                        lambda asin: {"ok": True, "gtin": "5702016914498", "candidatos": []})

    c = TestClient(crear_app(db_path=str(tmp_path / "lote.db")))
    ids = [_alta(c, marca="Visit the LEGO Store", modelo="LEGO Icons ECTO-1 10274",
                 titulo_ml="", asin="B0TEST1").json()["id"],
           _alta(c, marca="", modelo="LEGO Technic Ferrari 42143", asin="B0TEST2").json()["id"]]

    r = c.post("/api/catalogo/lote/preparar", json={"ids": ids})
    assert r.status_code == 200
    assert all(x["ok"] for x in r.json()["resultados"])

    for pid in ids:
        p = c.get(f"/api/catalogo/{pid}").json()
        assert p["marca"] == "LEGO"
        assert p["ml_category_id"] == "MLA1157"
        assert p["titulo_ml"]                                  # se completó del modelo
        assert p["ml_attributes"]["GTIN"] == "5702016914498"
        assert p["ml_attributes"]["IVA"] == "21 %"             # default administrativo


def test_lote_publicar_publica_los_seleccionados(tmp_path, monkeypatch):
    """El flujo de los dos botones: Preparar completa los datos, Publicar sube."""
    enviados = []
    _con_ml(monkeypatch, _cli_lote(enviados))
    monkeypatch.setattr("gtin_lookup.buscar_gtin", lambda asin: {"ok": False, "gtin": ""})

    c = TestClient(crear_app(db_path=str(tmp_path / "lote2.db")))
    ids = []
    for n in (1, 2):
        pid = _alta(c, marca="LEGO", titulo_ml=f"LEGO Set {n}",
                    ml_category_id="MLA1157").json()["id"]
        c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://img/1.jpg"]})
        ids.append(pid)

    c.post("/api/catalogo/lote/preparar", json={"ids": ids})
    r = c.post("/api/catalogo/lote/publicar", json={"ids": ids})
    assert all(x["ok"] for x in r.json()["resultados"]), r.text
    assert len(enviados) == 2
    for pid in ids:
        assert c.get(f"/api/catalogo/{pid}").json()["estado"] == "publicado"


def test_lote_sigue_con_los_demas_si_uno_falla(tmp_path, monkeypatch):
    """Un producto incompleto no puede frenar la tanda entera."""
    enviados = []
    _con_ml(monkeypatch, _cli_lote(enviados))
    monkeypatch.setattr("gtin_lookup.buscar_gtin", lambda asin: {"ok": False, "gtin": ""})

    c = TestClient(crear_app(db_path=str(tmp_path / "lote3.db")))
    bueno = _alta(c, marca="LEGO", titulo_ml="LEGO Set OK",
                  ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{bueno}/publicacion", json={"pictures": ["http://img/1.jpg"]})
    malo = _alta(c, marca="LEGO", titulo_ml="Sin foto").json()["id"]

    c.post("/api/catalogo/lote/preparar", json={"ids": [malo, bueno]})
    res = c.post("/api/catalogo/lote/publicar", json={"ids": [malo, bueno]}).json()["resultados"]
    por_id = {r["id"]: r for r in res}
    assert por_id[malo]["ok"] is False and "falta" in por_id[malo]["error"]
    assert por_id[bueno]["ok"] is True
    assert len(enviados) == 1


def test_lote_borrar(tmp_path):
    c = TestClient(crear_app(db_path=str(tmp_path / "lote4.db")))
    ids = [_alta(c).json()["id"] for _ in range(3)]
    c.post("/api/catalogo/lote/borrar", json={"ids": ids[:2]})
    assert [p["id"] for p in c.get("/api/catalogo").json()] == [ids[2]]


def test_lote_preparar_funciona_sin_sesion_de_ml(tmp_path, monkeypatch):
    """Sin conexión a ML igual se limpia la marca y se arma el título."""
    monkeypatch.setattr("gtin_lookup.buscar_gtin", lambda asin: {"ok": False, "gtin": ""})
    c = TestClient(crear_app(db_path=str(tmp_path / "lote5.db")))
    pid = _alta(c, marca="Visit the LEGO Store", modelo="LEGO Star Wars 75355",
                titulo_ml="").json()["id"]
    r = c.post("/api/catalogo/lote/preparar", json={"ids": [pid]})
    assert r.json()["resultados"][0]["ok"] is True
    p = c.get(f"/api/catalogo/{pid}").json()
    # El agente arma el título con la estrategia de publicación: tipo de
    # producto adelante, marca, nombre y número de set al final.
    assert p["marca"] == "LEGO" and p["titulo_ml"] == "Set LEGO Star Wars 75355"


def test_lote_prepara_los_casos_que_fallaban_en_produccion(tmp_path, monkeypatch):
    """Los dos motivos por los que los 72 productos quedaron "con problemas":
    la marca no estaba primera en el título, y el GTIN no se conseguía."""
    _con_ml(monkeypatch, _cli_lote([]))
    # El buscador de GTIN no encuentra nada, como pasó con los sets reales.
    monkeypatch.setattr("gtin_lookup.buscar_gtin",
                        lambda asin: {"ok": False, "gtin": "", "candidatos": []})

    c = TestClient(crear_app(db_path=str(tmp_path / "prod.db")))
    titulos = [
        "Set de construcción Star Wars de LEGO, Darth Vader, talla única",
        "Juguete para armar Star Wars 75050 B-Wing LEGO",
        "LEGO Star Wars: El ascenso de Skywalker Nave de Kylo Ren 752",
    ]
    ids = [_alta(c, marca="", modelo=t, titulo_ml=t, asin=f"B0T{i}").json()["id"]
           for i, t in enumerate(titulos)]

    r = c.post("/api/catalogo/lote/preparar", json={"ids": ids})
    assert all(x["ok"] for x in r.json()["resultados"]), r.text

    for pid in ids:
        p = c.get(f"/api/catalogo/{pid}").json()
        assert p["marca"] == "LEGO"
        # Sin código de barras se declara el motivo, que es la vía que acepta ML.
        assert p["ml_attributes"]["EMPTY_GTIN_REASON"] == "Otra razón"

    # Y con eso ya no falta nada: el borrador queda listo para publicar.
    for pid in ids:
        c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://img/1.jpg"]})
    d = c.post(f"/api/catalogo/{ids[0]}/borrador", json={}).json()
    assert d["faltantes"] == [], d["faltantes"]


def test_lote_saca_el_gtin_del_catalogo_de_mercadolibre(tmp_path, monkeypatch):
    """El caso que dejó 61 de 63 sin publicar: MercadoLibre exige GTIN en
    MLA1157 y no le alcanza el motivo de GTIN vacío. El código sale del propio
    catálogo de ML, que ya tiene los sets cargados."""
    _con_ml(monkeypatch, _cli_lote([], gtin_catalogo={
        "gtin": "5702017155326", "product_id": "MLA123", "nombre": "LEGO 75339"}))
    # La búsqueda web por ASIN no encuentra nada, como en producción.
    monkeypatch.setattr("gtin_lookup.buscar_gtin",
                        lambda asin: {"ok": False, "gtin": "", "candidatos": []})

    c = TestClient(crear_app(db_path=str(tmp_path / "gtin.db")))
    titulo = ("LEGO Star Wars Death Star - Compactador de basura Diorama 75339 "
              "Kit de construcción (802 piezas)")
    pid = _alta(c, marca="", modelo=titulo, titulo_ml=titulo, asin="B0TEST9").json()["id"]

    r = c.post("/api/catalogo/lote/preparar", json={"ids": [pid]})
    assert all(x["ok"] for x in r.json()["resultados"]), r.text

    attrs = c.get(f"/api/catalogo/{pid}").json()["ml_attributes"]
    assert attrs["GTIN"] == "5702017155326"
    # Con GTIN de verdad, el motivo de GTIN vacío no viaja (son contradictorios).
    assert "EMPTY_GTIN_REASON" not in attrs
    # Y la cantidad de piezas sale del propio título.
    assert attrs["PIECES_NUMBER"] == "802"


def test_lote_no_pone_gtin_si_el_catalogo_no_lo_tiene(tmp_path, monkeypatch):
    """Sin código verificado no se inventa nada: mejor que falte a publicar
    el código de otro producto."""
    _con_ml(monkeypatch, _cli_lote([]))          # el catálogo no devuelve GTIN
    monkeypatch.setattr("gtin_lookup.buscar_gtin",
                        lambda asin: {"ok": False, "gtin": "", "candidatos": []})

    c = TestClient(crear_app(db_path=str(tmp_path / "singtin.db")))
    pid = _alta(c, titulo_ml="LEGO Star Wars 75339", asin="B0TESTA").json()["id"]
    c.post("/api/catalogo/lote/preparar", json={"ids": [pid]})
    assert not (c.get(f"/api/catalogo/{pid}").json()["ml_attributes"].get("GTIN"))


def test_motivo_gtin_vacio_no_se_inventa_si_ml_no_lo_ofrece():
    """Un valor inventado ML lo descarta en silencio y después reclama el GTIN
    como si nunca se hubiera mandado: es peor que no mandarlo."""
    from mercadolibre.listing import valor_por_defecto
    assert valor_por_defecto({"id": "EMPTY_GTIN_REASON", "values": []}) == ""
    assert valor_por_defecto({"id": "EMPTY_GTIN_REASON",
                              "values": ["Es un kit", "Otra razón"]}) == "Otra razón"


def test_los_datos_se_leen_del_titulo_completo_no_del_recortado(tmp_path, monkeypatch):
    """El título de ML está recortado a 60 caracteres y ahí se pierde el final:
    el número de set queda cortado ("...Kylo Ren 752" por 75256) y la cantidad
    de piezas desaparece. Los datos se leen del título completo de Amazon."""
    consultas = []

    cli = _cli_lote([], gtin_catalogo={"gtin": "5702016909937",
                                       "product_id": "MLA77", "nombre": "LEGO 75256"})
    original = cli.ficha_de_catalogo

    def _espiar(query, debe_contener="", limit=5, **k):
        consultas.append((query, debe_contener))
        return original(query, debe_contener, limit, **k)

    cli.ficha_de_catalogo = _espiar
    _con_ml(monkeypatch, cli)
    monkeypatch.setattr("gtin_lookup.buscar_gtin",
                        lambda asin: {"ok": False, "gtin": "", "candidatos": []})

    completo = ("LEGO Star Wars: El ascenso de Skywalker Nave de Kylo Ren 75256 "
                "Kit de construcción (1005 piezas)")
    c = TestClient(crear_app(db_path=str(tmp_path / "trunc.db")))
    pid = _alta(c, marca="", modelo=completo, titulo_ml=completo[:60],
                asin="B0TRUNC").json()["id"]

    c.post("/api/catalogo/lote/preparar", json={"ids": [pid]})

    assert ("LEGO 75256", "75256") in consultas   # no "752" del título cortado
    attrs = c.get(f"/api/catalogo/{pid}").json()["ml_attributes"]
    assert attrs["GTIN"] == "5702016909937"
    assert attrs["PIECES_NUMBER"] == "1005"


def test_publica_contra_el_catalogo_cuando_encuentra_el_producto(tmp_path, monkeypatch):
    """La vía por la que ML no pide GTIN: publicar contra su producto de
    catálogo, del que toma los atributos de su propia ficha."""
    enviados = []
    _con_ml(monkeypatch, _cli_lote(enviados, gtin_catalogo={
        "gtin": "", "product_id": "MLA77", "nombre": "LEGO 75429"}))

    c = TestClient(crear_app(db_path=str(tmp_path / "cat.db")))
    completo = "LEGO Casco de conductor AT-AT de Star Wars 75429"
    pid = _alta(c, marca="LEGO", modelo=completo, titulo_ml=completo,
                ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://img/1.jpg"]})
    c.post(f"/api/catalogo/{pid}/aprobar")
    r = c.post(f"/api/catalogo/{pid}/publicar", json={})

    assert r.status_code == 200, r.text
    assert enviados[0]["catalog_product_id"] == "MLA77"
    assert enviados[0]["catalog_listing"] is True
    # Por esta vía no se mandan atributos: los pone MercadoLibre.
    assert "attributes" not in enviados[0]


def test_si_el_catalogo_rechaza_se_publica_por_la_via_normal(tmp_path, monkeypatch):
    """Un producto que no se puede publicar contra el catálogo (ya lo tenés
    publicado, no admite catalogación) no debe quedar trabado."""
    import api.catalogo_routes as rutas
    from mercadolibre.client import MeliAPIError

    enviados = []
    cli = _cli_lote(enviados, gtin_catalogo={"gtin": "", "product_id": "MLA77",
                                             "nombre": "LEGO 75429"})

    def _publicar(item):
        enviados.append(item)
        if item.get("catalog_listing"):
            raise MeliAPIError("no", status=400, cuerpo={"error": "not catalogable"})
        return {"id": "MLA5", "permalink": "http://ml/x"}

    cli.publicar = _publicar
    _con_ml(monkeypatch, cli)

    c = TestClient(crear_app(db_path=str(tmp_path / "fb.db")))
    completo = "LEGO Casco de conductor AT-AT de Star Wars 75429"
    pid = _alta(c, marca="LEGO", modelo=completo, titulo_ml=completo,
                ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion",
            json={"pictures": ["http://img/1.jpg"],
                  "ml_attributes": {"GTIN": "5702017424101", "IVA": "21 %"}})
    c.post(f"/api/catalogo/{pid}/aprobar")
    r = c.post(f"/api/catalogo/{pid}/publicar", json={})

    assert r.status_code == 200, r.text
    assert r.json()["ml_item_id"] == "MLA5"
    # Publicación propia primero: en el catálogo se compite solo por precio y
    # tiempo de entrega, que es donde un dropshipper que importa siempre pierde.
    assert "family_name" in enviados[0]
    assert not any(i.get("catalog_listing") for i in enviados), \
        "se fue al catálogo teniendo la publicación propia disponible"


def test_el_catalogo_es_la_salida_de_emergencia_si_falta_el_codigo(tmp_path, monkeypatch):
    """Sin código de barras la publicación propia no sale, y ahí sí conviene el
    catálogo: ML pone el GTIN de su ficha. Mejor esa publicación que ninguna."""
    enviados = []
    cli = _cli_lote(enviados, gtin_catalogo={"gtin": "", "product_id": "MLA77",
                                             "nombre": "LEGO 75429"})
    cli.atributos_obligatorios = lambda cid: [
        {"id": "GTIN", "name": "Código universal de producto"}]
    _con_ml(monkeypatch, cli)

    c = TestClient(crear_app(db_path=str(tmp_path / "emerg.db")))
    completo = "LEGO Casco de conductor AT-AT de Star Wars 75429"
    pid = _alta(c, marca="LEGO", modelo=completo, titulo_ml=completo,
                ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion",
            json={"pictures": ["http://img/1.jpg"], "ml_attributes": {}})
    c.post(f"/api/catalogo/{pid}/aprobar")
    r = c.post(f"/api/catalogo/{pid}/publicar", json={})

    assert r.status_code == 200, r.text
    assert enviados and enviados[-1].get("catalog_listing") is True


def test_si_la_publicacion_propia_es_rechazada_se_prueba_el_catalogo(tmp_path, monkeypatch):
    """Antes de dar la publicación por perdida se prueba la ficha del catálogo,
    que exige muchos menos datos."""
    from mercadolibre.client import MeliAPIError
    enviados = []
    cli = _cli_lote(enviados, gtin_catalogo={"gtin": "", "product_id": "MLA77",
                                             "nombre": "LEGO 75429"})

    def _publicar(item):
        enviados.append(item)
        if item.get("catalog_listing"):
            return {"id": "MLA9", "permalink": "http://ml/cat"}
        raise MeliAPIError("no", status=400, cuerpo={"error": "invalid"})

    cli.publicar = _publicar
    _con_ml(monkeypatch, cli)

    c = TestClient(crear_app(db_path=str(tmp_path / "rechazo.db")))
    completo = "LEGO Casco de conductor AT-AT de Star Wars 75429"
    pid = _alta(c, marca="LEGO", modelo=completo, titulo_ml=completo,
                ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion",
            json={"pictures": ["http://img/1.jpg"],
                  "ml_attributes": {"GTIN": "5702017424101", "IVA": "21 %"}})
    c.post(f"/api/catalogo/{pid}/aprobar")
    r = c.post(f"/api/catalogo/{pid}/publicar", json={})

    assert r.status_code == 200, r.text
    assert r.json()["ml_item_id"] == "MLA9"
    assert "family_name" in enviados[0]                    # se intentó propia
    assert enviados[-1].get("catalog_listing") is True     # y cayó al catálogo


def test_diagnostico_explica_donde_se_corta(client):
    completo = ("LEGO Star Wars: El ascenso de Skywalker Nave de Kylo Ren 75256 "
                "Kit de construcción (1005 piezas)")
    pid = _alta(client, modelo=completo, titulo_ml=completo[:60]).json()["id"]
    d = client.get(f"/api/catalogo/{pid}/diagnostico").json()
    assert d["numero_de_set"] == "75256"     # del completo, no del recortado
    assert d["piezas"] == "1005"
    assert d["consulta"] == "LEGO 75256"
    assert d["error"]                        # sin sesión de ML, lo dice


def test_vaciar_deja_encolar_los_mismos_asin_de_nuevo(tmp_path):
    """Lo que hace falta para empezar de cero: si se borra el catálogo pero
    queda la cola, `encolar` rebota los mismos ASIN como duplicados porque ya
    figuran ahí como procesados."""
    c = TestClient(crear_app(db_path=str(tmp_path / "v.db")))
    _alta(c, asin="B0VIEJO111", titulo_ml="LEGO viejo")
    c.post("/api/importar/encolar", json={"entradas": "B0VIEJO111\nB0VIEJO222"})

    r = c.post("/api/catalogo/vaciar", json={"confirmar": True})
    assert r.status_code == 200
    assert c.get("/api/catalogo").json() == []
    assert c.get("/api/importar/estado").json()["pendientes"] == 0

    # Y ahora los mismos ASIN entran de nuevo, que es el punto.
    r2 = c.post("/api/importar/encolar", json={"entradas": "B0VIEJO111\nB0VIEJO222"})
    assert r2.json()["nuevos"] == 2 and r2.json()["duplicados"] == 0


def test_vaciar_exige_confirmacion(client):
    _alta(client)
    assert client.post("/api/catalogo/vaciar", json={}).status_code == 400
    assert len(client.get("/api/catalogo").json()) == 1


def test_vaciar_conserva_lo_publicado_en_mercadolibre(tmp_path):
    """Borrarlos de acá no los baja de ML: solo se les pierde el rastro y ya no
    se les puede tocar precio ni pausarlos desde el panel."""
    c = TestClient(crear_app(db_path=str(tmp_path / "v2.db")))
    vivo = _alta(c, asin="B0PUB00001", titulo_ml="Publicado").json()["id"]
    _alta(c, asin="B0BORR0001", titulo_ml="Borrador")
    # Simulamos que ya está publicado en MercadoLibre.
    from db import conectar
    conn = conectar(str(tmp_path / "v2.db"))
    conn.execute("UPDATE catalogo SET ml_item_id = 'MLA123', estado = 'publicado' "
                 "WHERE id = ?", (vivo,))
    conn.commit()

    r = c.post("/api/catalogo/vaciar", json={"confirmar": True}).json()
    assert r["conservados_publicados"] == 1
    quedan = c.get("/api/catalogo").json()
    assert [p["ml_item_id"] for p in quedan] == ["MLA123"]


def test_vaciar_incluyendo_publicados_borra_todo(tmp_path):
    c = TestClient(crear_app(db_path=str(tmp_path / "v3.db")))
    pid = _alta(c, asin="B0PUB00002", titulo_ml="Publicado").json()["id"]
    from db import conectar
    conn = conectar(str(tmp_path / "v3.db"))
    conn.execute("UPDATE catalogo SET ml_item_id = 'MLA9' WHERE id = ?", (pid,))
    conn.commit()

    r = c.post("/api/catalogo/vaciar",
               json={"confirmar": True, "incluir_publicados": True}).json()
    assert r["conservados_publicados"] == 0
    assert c.get("/api/catalogo").json() == []


def test_filtro_por_defecto_no_esta_atado_a_una_marca(client):
    """La herramienta sirve para cualquier rubro: sin marca configurada entra
    todo lo que no sea accesorio."""
    f = client.get("/api/filtro").json()
    assert f["marca"] == ""
    assert f["descartar_accesorios"] is True


def test_configurar_el_filtro_persiste(client):
    r = client.patch("/api/filtro", json={"marca": "Bosch", "precio_min_usd": 50,
                                          "descartar_accesorios": False})
    assert r.json() == {"marca": "Bosch", "precio_min_usd": 50.0,
                        "descartar_accesorios": False,
                        # Se descarta lo que Amazon dice que no manda afuera.
                        "exigir_envio": True,
                        # Y por defecto se lee desde EE.UU., como siempre.
                        "pais_lectura": "us"}
    assert client.get("/api/filtro").json()["marca"] == "Bosch"


def test_filtro_con_precio_invalido_da_400(client):
    assert client.patch("/api/filtro", json={"precio_min_usd": "mucho"}).status_code == 400


def test_pagina_del_conversor_se_sirve(client):
    r = client.get("/codigos")
    assert r.status_code == 200 and "Conversor ASIN" in r.text


def test_endpoint_de_codigos_convierte_desde_el_catalogo(client):
    """Lo que ya está cargado se resuelve sin salir a la red."""
    pid = _alta(client, asin="B0CONV0001", titulo_ml="LEGO X-Wing").json()["id"]
    client.patch(f"/api/catalogo/{pid}/publicacion",
                 json={"ml_attributes": {"GTIN": "5702016914498"}})

    r = client.post("/api/codigos", json={"entradas": "B0CONV0001"})
    assert r.status_code == 200
    d = r.json()
    assert d["convertidos"] == 1
    res = d["resultados"][0]
    assert res["gtin"] == "5702016914498" and res["fuente"] == "tu catálogo"


def test_endpoint_de_codigos_avisa_lo_que_no_reconoce(client):
    d = client.post("/api/codigos", json={"entradas": "esto no es un codigo"}).json()
    assert d["convertidos"] == 0
    assert "no parece" in d["resultados"][0]["mensaje"].lower()


def test_lote_codigos_guarda_el_gtin_en_el_producto(tmp_path, monkeypatch):
    """El circuito que faltaba: el conversor aplicado al catálogo, dejando el
    código guardado y listo para publicar."""
    _con_ml(monkeypatch, _cli_lote([], gtin_catalogo={
        "gtin": "5702017155326", "product_id": "MLA1", "nombre": "LEGO 75339"}))

    c = TestClient(crear_app(db_path=str(tmp_path / "cod.db")))
    titulo = "LEGO Star Wars Death Star Diorama 75339 Kit de construcción"
    pid = _alta(c, marca="LEGO", modelo=titulo, titulo_ml=titulo,
                asin="B0COD00001").json()["id"]

    r = c.post("/api/catalogo/lote/codigos", json={"ids": [pid]})
    assert r.status_code == 200
    d = r.json()
    assert d["encontrados"] == 1
    assert d["resultados"][0]["fuente"] == "catálogo de MercadoLibre"
    # Y quedó guardado en el producto, no solo mostrado.
    attrs = c.get(f"/api/catalogo/{pid}").json()["ml_attributes"]
    assert attrs["GTIN"] == "5702017155326"


def test_lote_codigos_no_repite_los_que_ya_tienen(tmp_path, monkeypatch):
    consultas = []
    cli = _cli_lote([])
    cli.ficha_de_catalogo = lambda *a, **k: consultas.append(1) or {}
    _con_ml(monkeypatch, cli)

    c = TestClient(crear_app(db_path=str(tmp_path / "cod2.db")))
    pid = _alta(c, asin="B0COD00002", titulo_ml="Ya tiene").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion",
            json={"ml_attributes": {"GTIN": "5702016914498"}})

    d = c.post("/api/catalogo/lote/codigos", json={"ids": [pid]}).json()
    assert d["resultados"][0]["fuente"] == "ya lo tenía"
    assert not consultas          # no se consultó nada afuera


def test_lote_codigos_frena_si_amazon_nos_limita(tmp_path, monkeypatch):
    """Misma disciplina de siempre: ante un bloqueo se frena y se avisa."""
    cli = _cli_lote([])
    cli.ficha_de_catalogo = lambda *a, **k: {}
    _con_ml(monkeypatch, cli)
    monkeypatch.setattr("gtin_lookup.buscar_gtin",
                        lambda asin: {"ok": False, "gtin": "", "bloqueado": True,
                                      "candidatos": []})

    c = TestClient(crear_app(db_path=str(tmp_path / "cod3.db")))
    ids = [_alta(c, asin=f"B0COD0000{i}", titulo_ml=f"Producto {i}").json()["id"]
           for i in (3, 4, 5)]

    d = c.post("/api/catalogo/lote/codigos", json={"ids": ids}).json()
    assert d["detenido"] is True
    assert d["total"] == 1 and d["pendientes"] == 2


def test_lote_codigos_saca_el_motivo_de_gtin_vacio(tmp_path, monkeypatch):
    """Con GTIN de verdad, el motivo de GTIN vacío sobra: mandarlos juntos es
    contradictorio y MercadoLibre lo rechaza."""
    _con_ml(monkeypatch, _cli_lote([], gtin_catalogo={
        "gtin": "5702017155326", "product_id": "MLA1", "nombre": "LEGO 75339"}))

    c = TestClient(crear_app(db_path=str(tmp_path / "cod4.db")))
    titulo = "LEGO Star Wars Death Star Diorama 75339"
    pid = _alta(c, marca="LEGO", modelo=titulo, titulo_ml=titulo,
                asin="B0COD00006").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion",
            json={"ml_attributes": {"EMPTY_GTIN_REASON": "Otra razón"}})

    c.post("/api/catalogo/lote/codigos", json={"ids": [pid]})
    attrs = c.get(f"/api/catalogo/{pid}").json()["ml_attributes"]
    assert attrs["GTIN"] == "5702017155326"
    assert "EMPTY_GTIN_REASON" not in attrs


def test_la_cascada_llega_a_la_busqueda_por_nombre(tmp_path, monkeypatch):
    """El caso de la captura: el número de set existe pero MercadoLibre no lo
    tiene cargado con ese número. Antes se caía a Amazon —que bloquea a los
    servidores de la nube— y quedaba sin código. Ahora prueba por nombre."""
    consultas = []
    cli = _cli_lote([])

    def _ficha(query, debe_contener="", limit=5, parecido_a="",
               minimo_parecido=0.5, marca=""):
        consultas.append((query, debe_contener, bool(parecido_a)))
        if debe_contener:
            return {}                      # por número no lo encuentra
        return {"gtin": "5702017425627", "product_id": "MLA9",
                "nombre": "Lego Ideas Magic Of Disney 21352"}

    cli.ficha_de_catalogo = _ficha
    _con_ml(monkeypatch, cli)
    # Amazon bloqueado, como pasa desde Render.
    monkeypatch.setattr("gtin_lookup.buscar_gtin",
                        lambda asin: {"ok": False, "gtin": "", "bloqueado": True,
                                      "candidatos": []})

    c = TestClient(crear_app(db_path=str(tmp_path / "casc.db")))
    titulo = "LEGO Ideas Magic of Disney Set #21352 – 1,103 piezas, minifigura"
    pid = _alta(c, marca="LEGO", modelo=titulo, titulo_ml=titulo[:60],
                asin="B0CASC0001").json()["id"]

    d = c.post("/api/catalogo/lote/codigos", json={"ids": [pid]}).json()

    assert d["encontrados"] == 1
    assert d["detenido"] is False          # no hizo falta llegar a Amazon
    # Probó por número y después por nombre.
    assert any(q[1] for q in consultas) and any(q[2] for q in consultas)
    attrs = c.get(f"/api/catalogo/{pid}").json()["ml_attributes"]
    assert attrs["GTIN"] == "5702017425627"


def test_brickset_gana_sobre_todo_lo_demas(tmp_path, monkeypatch):
    """Para LEGO, Brickset es la fuente autoritativa: el dato sale de la caja.
    Si responde, no hace falta consultar a nadie más."""
    import fuentes_gtin
    otras = []
    cli = _cli_lote([])
    cli.ficha_de_catalogo = lambda *a, **k: otras.append("ml") or {}
    _con_ml(monkeypatch, cli)
    monkeypatch.setattr(fuentes_gtin, "gtin_de_brickset",
                        lambda *a, **k: {"gtin": "5702017155326",
                                         "nombre": "Death Star", "fuente": "Brickset"})
    monkeypatch.setattr(fuentes_gtin, "gtin_de_upcitemdb",
                        lambda *a, **k: otras.append("upc") or {})
    monkeypatch.setattr("gtin_lookup.buscar_gtin",
                        lambda a: otras.append("amazon") or {"ok": False, "gtin": ""})

    c = TestClient(crear_app(db_path=str(tmp_path / "bs.db")))
    titulo = "LEGO Star Wars Death Star Diorama 75339"
    pid = _alta(c, marca="LEGO", modelo=titulo, titulo_ml=titulo,
                asin="B0BS000001").json()["id"]

    d = c.post("/api/catalogo/lote/codigos", json={"ids": [pid]}).json()
    assert d["resultados"][0]["fuente"] == "Brickset"
    assert otras == []          # ninguna otra fuente se consultó
    assert c.get(f"/api/catalogo/{pid}").json()["ml_attributes"]["GTIN"] == "5702017155326"


def test_upcitemdb_cubre_los_rubros_que_no_son_lego(tmp_path, monkeypatch):
    """Brickset solo sirve para LEGO; para el resto está la base genérica."""
    import fuentes_gtin
    cli = _cli_lote([])
    cli.ficha_de_catalogo = lambda *a, **k: {}
    _con_ml(monkeypatch, cli)
    monkeypatch.setattr(fuentes_gtin, "gtin_de_upcitemdb",
                        lambda *a, **k: {"gtin": "3165140857710",
                                         "nombre": "Bosch", "fuente": "UPCitemdb"})
    monkeypatch.setattr("gtin_lookup.buscar_gtin",
                        lambda a: {"ok": False, "gtin": "", "bloqueado": True})

    c = TestClient(crear_app(db_path=str(tmp_path / "upc.db")))
    titulo = "Bosch Professional GSB 13 RE Taladro percutor 600W"
    pid = _alta(c, marca="Bosch", modelo=titulo, titulo_ml=titulo,
                asin="B0UPC00001").json()["id"]

    d = c.post("/api/catalogo/lote/codigos", json={"ids": [pid]}).json()
    assert d["resultados"][0]["fuente"] == "UPCitemdb"
    assert d["detenido"] is False       # no llegó a Amazon


def test_cargar_codigos_a_mano_por_numero_de_set(tmp_path):
    """La salida garantizada: pegar los códigos cuando ninguna fuente los tiene."""
    c = TestClient(crear_app(db_path=str(tmp_path / "man.db")))
    titulo = "LEGO Star Wars Death Star Diorama 75339 Kit"
    pid = _alta(c, marca="LEGO", modelo=titulo, titulo_ml=titulo,
                asin="B0MAN00001").json()["id"]

    d = c.post("/api/catalogo/codigos/cargar",
               json={"lineas": "75339;5702017155326"}).json()
    assert d["total"] == 1 and d["aplicados"][0]["id"] == pid
    assert c.get(f"/api/catalogo/{pid}").json()["ml_attributes"]["GTIN"] == "5702017155326"


def test_cargar_codigos_a_mano_por_asin_y_varios_separadores(tmp_path):
    c = TestClient(crear_app(db_path=str(tmp_path / "man2.db")))
    a = _alta(c, asin="B0MAN00002", titulo_ml="Uno").json()["id"]
    b = _alta(c, asin="B0MAN00003", titulo_ml="Dos").json()["id"]

    d = c.post("/api/catalogo/codigos/cargar", json={
        "lineas": "B0MAN00002;5702017155326\nB0MAN00003, 673419281423"}).json()
    assert d["total"] == 2
    for pid, gtin in ((a, "5702017155326"), (b, "673419281423")):
        assert c.get(f"/api/catalogo/{pid}").json()["ml_attributes"]["GTIN"] == gtin


def test_cargar_codigos_a_mano_avisa_lo_que_no_pudo(tmp_path):
    c = TestClient(crear_app(db_path=str(tmp_path / "man3.db")))
    _alta(c, asin="B0MAN00004", titulo_ml="Existe")

    d = c.post("/api/catalogo/codigos/cargar", json={
        "lineas": ("B0MAN00004;1234567890123\n"      # código inválido
                   "B0NOEXISTE;5702017155326\n"      # no está en el catálogo
                   "una linea suelta")}).json()
    assert d["total"] == 0
    assert d["sin_producto"] == ["B0NOEXISTE"]
    assert len(d["invalidos"]) == 2


def test_fuentes_de_codigos_reporta_lo_disponible(client, monkeypatch):
    monkeypatch.delenv("BRICKSET_API_KEY", raising=False)
    f = client.get("/api/codigos/fuentes").json()
    assert f["brickset"] is False and f["upcitemdb"] is True
    monkeypatch.setenv("BRICKSET_API_KEY", "abc")
    assert client.get("/api/codigos/fuentes").json()["brickset"] is True


def test_codigos_pendientes_lista_los_que_faltan(tmp_path):
    c = TestClient(crear_app(db_path=str(tmp_path / "pend.db")))
    con = _alta(c, asin="B0PEND0001", titulo_ml="Ya tiene").json()["id"]
    _alta(c, asin="B0PEND0002", titulo_ml="Le falta")
    c.patch(f"/api/catalogo/{con}/publicacion",
            json={"ml_attributes": {"GTIN": "5702016914498"}})

    d = c.get("/api/codigos/pendientes").json()
    assert d["total"] == 1
    assert d["items"][0]["asin"] == "B0PEND0002"


def test_recibir_codigos_del_navegador(tmp_path):
    """El botón lee las fichas de Amazon desde el navegador del usuario —cuya IP
    Amazon sí atiende— y devuelve los pares ASIN:código por la URL."""
    c = TestClient(crear_app(db_path=str(tmp_path / "recib.db")))
    a = _alta(c, asin="B0RECI0001", titulo_ml="Uno").json()["id"]
    b = _alta(c, asin="B0RECI0002", titulo_ml="Dos").json()["id"]

    r = c.get("/codigos/recibir",
              params={"datos": "B0RECI0001:5702017155326,B0RECI0002:673419281423"})
    assert r.status_code == 200 and "2 código(s) guardado(s)" in r.text
    assert c.get(f"/api/catalogo/{a}").json()["ml_attributes"]["GTIN"] == "5702017155326"
    assert c.get(f"/api/catalogo/{b}").json()["ml_attributes"]["GTIN"] == "673419281423"


def test_recibir_codigos_valida_lo_que_llega_por_la_url(tmp_path):
    """Lo que viene por la URL no es de fiar aunque el botón ya haya validado."""
    c = TestClient(crear_app(db_path=str(tmp_path / "recib2.db")))
    pid = _alta(c, asin="B0RECI0003", titulo_ml="Uno").json()["id"]

    r = c.get("/codigos/recibir", params={
        "datos": "B0RECI0003:1234567890123,B0NOEXISTE:5702017155326"})
    assert "0 código(s) guardado(s)" in r.text
    assert not c.get(f"/api/catalogo/{pid}").json()["ml_attributes"].get("GTIN")


def test_recibir_codigos_saca_el_motivo_de_gtin_vacio(tmp_path):
    c = TestClient(crear_app(db_path=str(tmp_path / "recib3.db")))
    pid = _alta(c, asin="B0RECI0004", titulo_ml="Uno").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion",
            json={"ml_attributes": {"EMPTY_GTIN_REASON": "Otra razón"}})

    c.get("/codigos/recibir", params={"datos": "B0RECI0004:5702017155326"})
    attrs = c.get(f"/api/catalogo/{pid}").json()["ml_attributes"]
    assert attrs["GTIN"] == "5702017155326" and "EMPTY_GTIN_REASON" not in attrs


def test_recibir_codigos_avisa_si_amazon_corto(tmp_path):
    c = TestClient(crear_app(db_path=str(tmp_path / "recib4.db")))
    _alta(c, asin="B0RECI0005", titulo_ml="Uno")
    r = c.get("/codigos/recibir",
              params={"datos": "B0RECI0005:5702017155326", "corto": "1"})
    assert "verificación" in r.text


def test_pagina_asistida_arma_el_boton_con_los_asin_pendientes(tmp_path):
    c = TestClient(crear_app(db_path=str(tmp_path / "asist.db")))
    _alta(c, asin="B0ASIS0001", titulo_ml="Sin código")

    r = c.get("/codigos/asistido")
    assert r.status_code == 200
    assert "B0ASIS0001" in r.text          # el ASIN va embebido en el botón
    assert "1 producto(s)" in r.text
    # El botón corre en el navegador y manda el resultado de vuelta.
    assert "javascript:" in r.text and "/codigos/recibir" in r.text


def test_agente_arranca_apagado_y_no_publica_solo(client):
    """Publicar mueve plata: el agente no lo hace hasta que lo habilitás."""
    e = client.get("/api/agente").json()
    assert e["encendido"] is False and e["publicar"] is False
    assert client.post("/api/agente/tick", json={}).json()["accion"] == "apagado"


def test_agente_recorre_el_catalogo_de_punta_a_punta(tmp_path, monkeypatch):
    """El circuito completo: preparar, conseguir el código y publicar."""
    enviados = []
    cli = _cli_lote(enviados, gtin_catalogo={
        "gtin": "5702017155326", "product_id": "MLA5", "nombre": "LEGO 75339"})
    _con_ml(monkeypatch, cli)

    c = TestClient(crear_app(db_path=str(tmp_path / "ag.db")))
    titulo = "LEGO Star Wars Death Star Diorama 75339"
    pid = _alta(c, marca="LEGO", modelo=titulo, titulo_ml=titulo,
                asin="B0AGENT001").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://i/1.jpg"]})
    c.patch("/api/agente", json={"encendido": True, "publicar": True,
                                 "margen_minimo": 0, "max_publicaciones": 5})

    acciones = []
    for _ in range(6):
        r = c.post("/api/agente/tick", json={}).json()
        acciones.append(r["accion"])
        if r["accion"] in ("sin_trabajo", "apagado"):
            break

    assert "publicar" in acciones, acciones
    p = c.get(f"/api/catalogo/{pid}").json()
    assert p["estado"] == "publicado" and p["ml_item_id"]


def test_agente_sin_fotos_deja_el_producto_trabado_con_el_motivo(tmp_path, monkeypatch):
    _con_ml(monkeypatch, _cli_lote([]))
    c = TestClient(crear_app(db_path=str(tmp_path / "ag2.db")))
    _alta(c, marca="LEGO", titulo_ml="LEGO sin fotos", asin="B0AGENT002")
    c.patch("/api/agente", json={"encendido": True})

    r = c.post("/api/agente/tick", json={}).json()
    assert r["accion"] == "error" and "fotos" in r["detalle"]


def test_agente_config_invalida_da_400(client):
    assert client.patch("/api/agente", json={"margen_minimo": "mucho"}).status_code == 400


def test_publicar_falla_si_ml_no_devuelve_id(tmp_path, monkeypatch):
    """Antes se marcaba como publicado igual, y el panel mostraba un éxito que
    no existía en MercadoLibre."""
    cli = _cli_lote([])
    cli.ficha_de_catalogo = lambda *a, **k: {}
    cli.publicar = lambda item: {"permalink": "http://ml/x"}   # sin id
    _con_ml(monkeypatch, cli)

    c = TestClient(crear_app(db_path=str(tmp_path / "noid.db")))
    pid = _alta(c, marca="LEGO", titulo_ml="LEGO X", ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion",
            json={"pictures": ["http://i/1.jpg"],
                  "ml_attributes": {"GTIN": "5702017155326", "IVA": "21 %"}})
    c.post(f"/api/catalogo/{pid}/aprobar")

    r = c.post(f"/api/catalogo/{pid}/publicar", json={})
    assert r.status_code == 502 and "sin id" in r.text
    assert c.get(f"/api/catalogo/{pid}").json()["estado"] == "aprobado"


def test_publicar_falla_si_ml_lo_deja_en_revision(tmp_path, monkeypatch):
    cli = _cli_lote([])
    cli.ficha_de_catalogo = lambda *a, **k: {}
    cli.publicar = lambda item: {"id": "MLA55", "status": "under_review",
                                 "permalink": "http://ml/x"}
    _con_ml(monkeypatch, cli)

    c = TestClient(crear_app(db_path=str(tmp_path / "rev.db")))
    pid = _alta(c, marca="LEGO", titulo_ml="LEGO X", ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion",
            json={"pictures": ["http://i/1.jpg"],
                  "ml_attributes": {"GTIN": "5702017155326", "IVA": "21 %"}})
    c.post(f"/api/catalogo/{pid}/aprobar")

    r = c.post(f"/api/catalogo/{pid}/publicar", json={})
    assert r.status_code == 502 and "under_review" in r.text
    assert c.get(f"/api/catalogo/{pid}").json()["estado"] != "publicado"


def test_verificar_corrige_lo_que_no_esta_publicado_de_verdad(tmp_path, monkeypatch):
    """El caso real: el panel decía 18 publicados y en MercadoLibre no había
    ninguno."""
    import api.catalogo_routes as rutas
    from mercadolibre.client import MeliAPIError

    class _Cli:
        def mis_items(self, limit=200):
            return ["MLA_VIVO"]

        def obtener_varios(self, ids):
            # El multiget deja afuera lo que no existe: así se entera el que
            # llama de que el ítem fantasma no está.
            return {"MLA_VIVO": {"id": "MLA_VIVO", "status": "active",
                                 "permalink": "http://ml/vivo"}}

    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: _Cli())
    monkeypatch.setattr(rutas.MeliCredenciales, "configurado", property(lambda self: True))
    monkeypatch.setattr(rutas.TokenStore, "hay_sesion", lambda self: True)

    c = TestClient(crear_app(db_path=str(tmp_path / "ver.db")))
    vivo = _alta(c, asin="B0VER00001", titulo_ml="Vivo").json()["id"]
    fantasma = _alta(c, asin="B0VER00002", titulo_ml="Fantasma").json()["id"]
    from db import conectar
    conn = conectar(str(tmp_path / "ver.db"))
    conn.execute("UPDATE catalogo SET ml_item_id='MLA_VIVO', estado='publicado' "
                 "WHERE id = ?", (vivo,))
    conn.execute("UPDATE catalogo SET ml_item_id='MLA_FANTASMA', estado='publicado' "
                 "WHERE id = ?", (fantasma,))
    conn.commit()

    d = c.post("/api/catalogo/verificar", json={}).json()
    assert d["publicaciones_en_la_cuenta"] == 1
    assert d["corregidos"] == 1
    # El que no existe deja de figurar como publicado.
    assert c.get(f"/api/catalogo/{fantasma}").json()["estado"] == "borrador"
    assert c.get(f"/api/catalogo/{vivo}").json()["estado"] == "publicado"


def test_verificar_guarda_el_link_de_la_publicacion(tmp_path, monkeypatch):
    """El link es lo que permite abrir la publicación y comprobarla de un clic,
    en vez de buscarla a mano en el panel de MercadoLibre."""
    import api.catalogo_routes as rutas

    class _Cli:
        def mis_items(self, limit=200):
            return ["MLA_VIVO"]

        def obtener_varios(self, ids):
            return {i: {"id": i, "status": "active",
                        "permalink": "https://articulo.mercadolibre.com.ar/MLA-VIVO"}
                    for i in ids}

    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: _Cli())
    monkeypatch.setattr(rutas.MeliCredenciales, "configurado", property(lambda self: True))
    monkeypatch.setattr(rutas.TokenStore, "hay_sesion", lambda self: True)

    c = TestClient(crear_app(db_path=str(tmp_path / "link.db")))
    pid = _alta(c, asin="B0LINK0001", titulo_ml="Publicado").json()["id"]
    from db import conectar
    conn = conectar(str(tmp_path / "link.db"))
    conn.execute("UPDATE catalogo SET ml_item_id='MLA_VIVO', estado='publicado', "
                 "ml_permalink='' WHERE id = ?", (pid,))
    conn.commit()

    d = c.post("/api/catalogo/verificar", json={}).json()
    assert d["corregidos"] == 0
    assert d["revisados"][0]["permalink"].endswith("MLA-VIVO")
    assert c.get(f"/api/catalogo/{pid}").json()["ml_permalink"].endswith("MLA-VIVO")


def test_publicar_que_queda_pausado_se_activa_solo(tmp_path, monkeypatch):
    """MercadoLibre crea algunas publicaciones de catálogo en `paused`. El ítem
    existe: hay que activarlo, no darlo por fallado."""
    import api.catalogo_routes as rutas

    activados = []

    class _CliFalso:
        def ficha_de_catalogo(self, *a, **k):
            return {}

        def atributos_obligatorios(self, cat_id):
            return []

        def valores_permitidos(self, cat_id):
            return {}

        def publicar(self, item):
            return {"id": "MLA777", "permalink": "http://ml/p", "status": "paused"}

        def reactivar(self, item_id):
            activados.append(item_id)
            return {}

        def obtener(self, item_id):
            return {"status": "active", "permalink": "http://ml/p"}

        def poner_descripcion(self, item_id, texto):
            return {}

    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: _CliFalso())
    monkeypatch.setattr(rutas.MeliCredenciales, "configurado", property(lambda self: True))
    monkeypatch.setattr(rutas.TokenStore, "hay_sesion", lambda self: True)

    c = TestClient(crear_app(db_path=str(tmp_path / "pausado.db")))
    pid = _alta(c, marca="LEGO", titulo_ml="LEGO Ninjago Máscara de Lobo 71721",
                ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://img/1.jpg"]})
    c.post(f"/api/catalogo/{pid}/aprobar")
    r = c.post(f"/api/catalogo/{pid}/publicar", json={})

    assert r.status_code == 200, r.text
    assert activados == ["MLA777"]
    assert c.get(f"/api/catalogo/{pid}").json()["estado"] == "publicado"


def test_no_busca_por_el_codigo_interno_de_amazon(tmp_path, monkeypatch):
    """Amazon declara 6332955 como "modelo" del set 10282. Buscar por ese
    número no encuentra nada; el que sirve está en el título."""
    consultas = []
    cli = _cli_lote([])

    def _ficha(query, debe_contener="", limit=5, parecido_a="",
               minimo_parecido=0.5, marca=""):
        consultas.append(debe_contener)
        return {}

    cli.ficha_de_catalogo = _ficha
    _con_ml(monkeypatch, cli)
    monkeypatch.setattr("gtin_lookup.buscar_gtin",
                        lambda asin: {"ok": False, "gtin": "", "bloqueado": True,
                                      "candidatos": []})

    titulo = "LEGO Adidas Originals Superstar 10282 Kit de construcción"
    c = TestClient(crear_app(db_path=str(tmp_path / "interno.db")))
    pid = _alta(c, marca="LEGO", modelo=titulo, titulo_ml=titulo[:60],
                asin="B0INTERN01").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"modelo_fabricante": "6332955"})
    c.post("/api/catalogo/lote/codigos", json={"ids": [pid]})

    assert "10282" in consultas, consultas
    assert "6332955" not in consultas, "buscó por el código interno de Amazon"


def test_busca_mas_alla_de_los_primeros_resultados(tmp_path, monkeypatch):
    """Buscando "LEGO 21042" los primeros lugares se los llevan repuestos de
    auto que comparten el número. Con 5 resultados el set queda tapado."""
    limites = []
    cli = _cli_lote([])

    def _ficha(query, debe_contener="", limit=5, parecido_a="",
               minimo_parecido=0.5, marca=""):
        if debe_contener:
            limites.append(limit)
        return {}

    cli.ficha_de_catalogo = _ficha
    _con_ml(monkeypatch, cli)
    monkeypatch.setattr("gtin_lookup.buscar_gtin",
                        lambda asin: {"ok": False, "gtin": "", "bloqueado": True,
                                      "candidatos": []})

    titulo = "LEGO Architecture Set de Construcción de la Estatua 21042"
    c = TestClient(crear_app(db_path=str(tmp_path / "tapado.db")))
    pid = _alta(c, marca="LEGO", modelo=titulo, titulo_ml=titulo[:60],
                asin="B0TAPADO01").json()["id"]
    c.post("/api/catalogo/lote/codigos", json={"ids": [pid]})

    assert limites and min(limites) >= 20, limites


def _cli_publica(status, activa_a=None):
    """Cliente falso que publica devolviendo `status`; `activa_a` es el estado
    que informa después de reactivar (None = MercadoLibre lo deja en pausa)."""
    class _Cli:
        def __init__(self):
            self.publicados, self.reactivados = [], []

        def ficha_de_catalogo(self, *a, **k):
            return {}

        def atributos_obligatorios(self, cat_id):
            return []

        def valores_permitidos(self, cat_id):
            return {}

        def publicar(self, item):
            self.publicados.append(item)
            return {"id": "MLA3861899038", "permalink": "http://ml/z",
                    "status": status}

        def reactivar(self, item_id):
            self.reactivados.append(item_id)
            return {}

        def obtener(self, item_id):
            return {"status": activa_a or status, "permalink": "http://ml/z"}

        def poner_descripcion(self, item_id, texto):
            return {}
    return _Cli()


def test_pausado_por_ml_queda_registrado_no_perdido(tmp_path, monkeypatch):
    """MercadoLibre deja la publicación en pausa mientras revisa las fotos. El
    ítem existe: darlo por fallado lo dejaba vivo en ML y sin registrar acá."""
    import api.catalogo_routes as rutas
    cli = _cli_publica("paused")            # sigue pausado tras reactivar
    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: cli)
    _con_ml(monkeypatch, cli)

    c = TestClient(crear_app(db_path=str(tmp_path / "pau.db")))
    pid = _alta(c, marca="LEGO", titulo_ml="LEGO Zelda Ocarina 77092",
                ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://i/1.jpg"]})
    c.post(f"/api/catalogo/{pid}/aprobar")
    r = c.post(f"/api/catalogo/{pid}/publicar", json={})

    assert r.status_code == 200, r.text
    p = c.get(f"/api/catalogo/{pid}").json()
    assert p["estado"] == "pausado"
    assert p["ml_item_id"] == "MLA3861899038"
    assert p["ml_permalink"] == "http://ml/z"


def test_no_republica_un_producto_que_ya_tiene_item(tmp_path, monkeypatch):
    """El duplicado que causaba el bug anterior: el ítem quedaba en ML, acá
    figuraba sin publicar, y el siguiente intento creaba una segunda
    publicación de lo mismo."""
    import api.catalogo_routes as rutas
    cli = _cli_publica("paused")
    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: cli)
    _con_ml(monkeypatch, cli)

    c = TestClient(crear_app(db_path=str(tmp_path / "dup.db")))
    pid = _alta(c, marca="LEGO", titulo_ml="LEGO Batman Forever 76273",
                ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://i/1.jpg"]})
    c.post(f"/api/catalogo/{pid}/aprobar")
    assert c.post(f"/api/catalogo/{pid}/publicar", json={}).status_code == 200

    c.post(f"/api/catalogo/{pid}/aprobar")          # forzar el reintento
    r = c.post(f"/api/catalogo/{pid}/publicar", json={})
    assert r.status_code == 409
    assert "duplicado" in str(r.json())
    assert len(cli.publicados) == 1, "creó una segunda publicación"


def test_verificar_pasa_a_publicado_cuando_ml_lo_activa(tmp_path, monkeypatch):
    """Cuando ML termina de revisar y lo activa, el catálogo tiene que
    enterarse: si no, queda pausado para siempre."""
    import api.catalogo_routes as rutas
    cli = _cli_publica("paused")
    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: cli)
    _con_ml(monkeypatch, cli)

    c = TestClient(crear_app(db_path=str(tmp_path / "act.db")))
    pid = _alta(c, marca="LEGO", titulo_ml="LEGO Ninjago 71721",
                ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://i/1.jpg"]})
    c.post(f"/api/catalogo/{pid}/aprobar")
    c.post(f"/api/catalogo/{pid}/publicar", json={})
    assert c.get(f"/api/catalogo/{pid}").json()["estado"] == "pausado"

    cli.obtener_varios = lambda ids: {
        i: {"id": i, "status": "active", "permalink": "http://ml/z"} for i in ids}
    cli.mis_items = lambda: ["MLA3861899038"]
    d = c.post("/api/catalogo/verificar", json={}).json()

    assert d["corregidos"] == 1
    assert c.get(f"/api/catalogo/{pid}").json()["estado"] == "publicado"


# ---- videos de YouTube --------------------------------------------------

def test_lote_videos_avisa_si_falta_la_clave(client, monkeypatch):
    """Sin YOUTUBE_API_KEY no se puede buscar; hay que decirlo, no fallar
    silenciosamente."""
    import api.catalogo_routes as rutas
    monkeypatch.setattr(rutas, "youtube_configurado", lambda: False)

    pid = _alta(client, marca="LEGO", titulo_ml="LEGO 10282").json()["id"]
    r = client.post("/api/catalogo/lote/videos", json={"ids": [pid]})
    assert r.status_code == 422
    assert "YOUTUBE_API_KEY" in str(r.json())


def test_lote_videos_guarda_el_que_encuentra(client, monkeypatch):
    import api.catalogo_routes as rutas
    monkeypatch.setattr(rutas, "youtube_configurado", lambda: True)
    monkeypatch.setattr(rutas, "buscar_video", lambda *a, **k: {
        "video_id": "dQw4w9WgXcQ", "titulo": "LEGO 10282 adidas", "canal": "LEGO"})

    pid = _alta(client, marca="LEGO", titulo_ml="LEGO adidas 10282").json()["id"]
    d = client.post("/api/catalogo/lote/videos", json={"ids": [pid]}).json()

    assert d["encontrados"] == 1
    assert client.get(f"/api/catalogo/{pid}").json()["video_youtube"] == "dQw4w9WgXcQ"


def test_lote_videos_sin_resultado_no_es_un_error(client, monkeypatch):
    """Que no haya video oficial es lo normal, no una falla."""
    import api.catalogo_routes as rutas
    monkeypatch.setattr(rutas, "youtube_configurado", lambda: True)
    monkeypatch.setattr(rutas, "buscar_video", lambda *a, **k: {})

    pid = _alta(client, marca="LEGO", titulo_ml="LEGO raro 99999").json()["id"]
    d = client.post("/api/catalogo/lote/videos", json={"ids": [pid]}).json()

    assert d["encontrados"] == 0 and d["total"] == 1
    assert client.get(f"/api/catalogo/{pid}").json()["video_youtube"] == ""


def test_lote_videos_no_repisa_el_que_ya_esta(client, monkeypatch):
    """El video cargado a mano gana: no se lo pisa con uno automático."""
    import api.catalogo_routes as rutas
    monkeypatch.setattr(rutas, "youtube_configurado", lambda: True)
    monkeypatch.setattr(rutas, "buscar_video", lambda *a, **k: {
        "video_id": "OTROOTRO123", "titulo": "otro", "canal": "LEGO"})

    pid = _alta(client, marca="LEGO", titulo_ml="LEGO 10282").json()["id"]
    client.patch(f"/api/catalogo/{pid}/publicacion",
                 json={"video_youtube": "dQw4w9WgXcQ"})
    client.post("/api/catalogo/lote/videos", json={"ids": [pid]})

    assert client.get(f"/api/catalogo/{pid}").json()["video_youtube"] == "dQw4w9WgXcQ"


def test_buscar_video_de_un_producto(client, monkeypatch):
    import api.catalogo_routes as rutas
    monkeypatch.setattr(rutas, "youtube_configurado", lambda: True)
    monkeypatch.setattr(rutas, "buscar_video", lambda *a, **k: {
        "video_id": "dQw4w9WgXcQ", "titulo": "LEGO 10282", "canal": "LEGO"})

    pid = _alta(client, marca="LEGO", titulo_ml="LEGO adidas 10282").json()["id"]
    r = client.post(f"/api/catalogo/{pid}/video").json()

    assert r["encontrado"] is True and r["canal"] == "LEGO"
    assert client.get(f"/api/catalogo/{pid}").json()["video_youtube"] == "dQw4w9WgXcQ"


def test_preparar_no_busca_video_sin_clave(client, monkeypatch):
    """Sin clave, preparar tiene que seguir andando igual que siempre."""
    import api.catalogo_routes as rutas
    monkeypatch.setattr(rutas, "youtube_configurado", lambda: False)
    monkeypatch.setattr(rutas, "buscar_video",
                        lambda *a, **k: pytest.fail("no debería buscar"))

    pid = _alta(client, marca="LEGO", titulo_ml="LEGO 10282",
                ml_category_id="MLA1157").json()["id"]
    assert client.post("/api/catalogo/lote/preparar",
                       json={"ids": [pid]}).status_code == 200


def test_las_fuentes_informan_si_hay_clave_de_youtube(client, monkeypatch):
    """El panel usa esto para no ofrecer un botón que no puede funcionar."""
    import api.catalogo_routes as rutas

    monkeypatch.setattr(rutas, "youtube_configurado", lambda: False)
    assert client.get("/api/codigos/fuentes").json()["youtube"] is False
    monkeypatch.setattr(rutas, "youtube_configurado", lambda: True)
    assert client.get("/api/codigos/fuentes").json()["youtube"] is True


def test_por_proxy_se_procesan_menos_por_llamada(client, monkeypatch):
    """El proxy tarda mucho más que leer directo: varios seguidos harían la
    petición eterna que este endpoint existe para evitar, y el servidor la
    corta a mitad de camino."""
    import api.catalogo_routes as rutas

    pedidos = []
    monkeypatch.setattr(rutas.ColaImportacion, "procesar_lote",
                        lambda self, maximo, pausa_seg, **k:
                        pedidos.append(maximo) or {"procesados": [],
                                                   "detener": False,
                                                   "motivo": "ok"})

    monkeypatch.setattr(rutas, "scraperapi_configurada", lambda: True)
    client.post("/api/importar/procesar", json={"maximo": 10})
    monkeypatch.setattr(rutas, "scraperapi_configurada", lambda: False)
    client.post("/api/importar/procesar", json={"maximo": 10})

    assert pedidos == [2, 10]


def test_el_lote_de_videos_marca_los_que_no_son_oficiales(client, monkeypatch):
    """Un video de un canal de confianza no es del fabricante: hay que poder
    distinguirlo para revisarlo antes de publicar."""
    import api.catalogo_routes as rutas
    monkeypatch.setattr(rutas, "youtube_configurado", lambda: True)
    monkeypatch.setattr(rutas, "buscar_video", lambda *a, **k: {
        "video_id": "dQw4w9WgXcQ", "titulo": "LEGO 21042",
        "canal": "AustrianBrickFan", "oficial": False})

    pid = _alta(client, marca="LEGO", titulo_ml="LEGO 21042").json()["id"]
    d = client.post("/api/catalogo/lote/videos", json={"ids": [pid]}).json()

    assert d["resultados"][0]["oficial"] is False
    assert d["resultados"][0]["canal"] == "AustrianBrickFan"


# ---- actualización de precios en lote -----------------------------------

def _app_con_publicado(tmp_path, monkeypatch, nombre, precios_falla=None):
    """App con un producto ya publicado y un cliente que registra los precios
    que se le mandan a MercadoLibre."""
    import api.catalogo_routes as rutas
    from mercadolibre.client import MeliAPIError

    class _Cli:
        def __init__(self):
            self.puestos = []

        def ficha_de_catalogo(self, *a, **k):
            return {}

        def atributos_obligatorios(self, cat_id):
            return []

        def valores_permitidos(self, cat_id):
            return {}

        def publicar(self, item):
            return {"id": "MLA100", "permalink": "http://ml/x", "status": "active"}

        def poner_descripcion(self, item_id, texto):
            return {}

        def actualizar_precio(self, item_id, precio):
            if precios_falla:
                raise MeliAPIError("MercadoLibre PUT /items → 400", status=400,
                                   cuerpo={"message": precios_falla})
            self.puestos.append((item_id, precio))
            return {}

    cli = _Cli()
    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: cli)
    monkeypatch.setattr(rutas.MeliCredenciales, "configurado", property(lambda self: True))
    monkeypatch.setattr(rutas.TokenStore, "hay_sesion", lambda self: True)

    c = TestClient(crear_app(db_path=str(tmp_path / nombre)))
    pid = _alta(c, asin="B0PRECIO01", marca="LEGO", titulo_ml="LEGO Set 21042",
                precio_usd=100.0, margen_deseado=0.35,
                ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://i/1.jpg"]})
    c.post(f"/api/catalogo/{pid}/aprobar")
    assert c.post(f"/api/catalogo/{pid}/publicar", json={}).status_code == 200
    return c, cli, pid


def test_simular_precios_no_toca_nada(tmp_path, monkeypatch):
    """La mitad importante: cambiar el precio de una publicación viva no se
    deshace con un botón, así que primero se mira qué cambiaría."""
    c, cli, pid = _app_con_publicado(tmp_path, monkeypatch, "sim.db")
    antes = c.get(f"/api/catalogo/{pid}").json()

    d = c.post("/api/catalogo/precios/simular", json={"margen_pct": 80}).json()
    despues = c.get(f"/api/catalogo/{pid}").json()

    assert d["total"] == 1
    fila = d["filas"][0]
    assert fila["precio_nuevo"] > fila["precio_actual"]   # 80% > 35%
    # Y nada cambió de verdad.
    assert despues["margen_deseado"] == antes["margen_deseado"]
    assert despues["precio_publicado_ars"] == antes["precio_publicado_ars"]
    assert cli.puestos == [], "no debería llamar a MercadoLibre"


def test_aplicar_precios_manda_el_nuevo_a_mercadolibre(tmp_path, monkeypatch):
    c, cli, pid = _app_con_publicado(tmp_path, monkeypatch, "apl.db")
    esperado = c.post("/api/catalogo/precios/simular",
                      json={"margen_pct": 80}).json()["filas"][0]["precio_nuevo"]

    d = c.post("/api/catalogo/precios/aplicar",
               json={"ids": [pid], "margen_pct": 80}).json()

    assert d["actualizados"] == 1
    assert cli.puestos == [("MLA100", esperado)]
    guardado = c.get(f"/api/catalogo/{pid}").json()
    assert guardado["precio_publicado_ars"] == esperado
    assert abs(guardado["margen_deseado"] - 0.8) < 1e-9


def test_si_mercadolibre_rechaza_no_se_guarda_el_precio(tmp_path, monkeypatch):
    """El catálogo no puede decir un precio que la publicación no tiene."""
    c, cli, pid = _app_con_publicado(tmp_path, monkeypatch, "rech.db",
                                     precios_falla="Price is not valid")
    antes = c.get(f"/api/catalogo/{pid}").json()["precio_publicado_ars"]

    d = c.post("/api/catalogo/precios/aplicar",
               json={"ids": [pid], "margen_pct": 80}).json()

    assert d["actualizados"] == 0
    assert "Price is not valid" in d["resultados"][0]["error"]
    assert c.get(f"/api/catalogo/{pid}").json()["precio_publicado_ars"] == antes


def test_sin_margen_se_usa_el_de_cada_producto(tmp_path, monkeypatch):
    """Actualizar solo por el dólar, sin cambiar el margen de nadie."""
    c, cli, pid = _app_con_publicado(tmp_path, monkeypatch, "sinm.db")

    c.post("/api/catalogo/precios/aplicar", json={"ids": [pid]})

    assert abs(c.get(f"/api/catalogo/{pid}").json()["margen_deseado"] - 0.35) < 1e-9


def test_un_margen_invalido_se_rechaza(tmp_path, monkeypatch):
    c, cli, pid = _app_con_publicado(tmp_path, monkeypatch, "mal.db")

    assert c.post("/api/catalogo/precios/simular",
                  json={"margen_pct": "muchísimo"}).status_code == 422
    assert c.post("/api/catalogo/precios/simular",
                  json={"margen_pct": -10}).status_code == 422


def test_solo_entran_los_que_estan_publicados(tmp_path, monkeypatch):
    """Un borrador no tiene publicación que actualizar."""
    c, cli, pid = _app_con_publicado(tmp_path, monkeypatch, "solo.db")
    _alta(c, asin="B0BORRADOR", marca="LEGO", titulo_ml="Borrador",
          precio_usd=50.0)

    d = c.post("/api/catalogo/precios/simular", json={}).json()
    assert [f["id"] for f in d["filas"]] == [pid]


def test_precio_a_dolar_1600_con_margen_y_envio_desde_el_panel(tmp_path, monkeypatch):
    """El caso completo: LEGO ya publicado, costo a dólar 1600, margen del 30%
    y el envío gratis descontado, con el precio nuevo llegando a MercadoLibre."""
    c, cli, pid = _app_con_publicado(tmp_path, monkeypatch, "dosdolares.db")

    d = c.post("/api/catalogo/precios/simular",
               json={"tc_costo": 1600, "margen_pct": 30, "envio_ars": 9860}).json()
    fila = d["filas"][0]

    assert d["tc_costo"] == 1600 and d["envio_ars"] == 9860
    # El margen pedido es el que queda después de todos los descuentos.
    assert abs(fila["margen_pct"] - 30.0) < 0.2

    c.post("/api/catalogo/precios/aplicar",
           json={"ids": [pid], "tc_costo": 1600, "margen_pct": 30,
                 "envio_ars": 9860})
    assert cli.puestos == [("MLA100", fila["precio_nuevo"])]


def test_un_dolar_invalido_se_rechaza(tmp_path, monkeypatch):
    c, cli, pid = _app_con_publicado(tmp_path, monkeypatch, "dolarmal.db")

    for cuerpo in ({"tc_costo": "mil quinientos"}, {"tc_costo": 0},
                   {"envio_ars": -100}, {"margen_pct": -5}):
        r = c.post("/api/catalogo/precios/simular", json=cuerpo)
        assert r.status_code == 422, cuerpo


def test_el_envio_en_cero_es_valido(tmp_path, monkeypatch):
    """Cero significa "no ofrezco envío gratis". Vacío significa "usá el
    configurado". No son lo mismo y el cero no puede rebotar como inválido."""
    c, cli, pid = _app_con_publicado(tmp_path, monkeypatch, "envio0.db")

    d = c.post("/api/catalogo/precios/simular",
               json={"tc_costo": 1600, "envio_ars": 0})
    assert d.status_code == 200
    assert d.json()["envio_ars"] == 0


def test_la_tabla_queda_con_los_mismos_numeros_que_se_simularon(tmp_path, monkeypatch):
    """Lo que se ve al decidir tiene que ser lo que queda. Antes el costo se
    recalculaba con la cotización del mercado y el margen de la tabla no
    coincidía con el de la simulación."""
    c, cli, pid = _app_con_publicado(tmp_path, monkeypatch, "coherente.db")

    sim = c.post("/api/catalogo/precios/simular",
                 json={"tc_costo": 1600, "margen_pct": 30}).json()["filas"][0]
    c.post("/api/catalogo/precios/aplicar",
           json={"ids": [pid], "tc_costo": 1600, "margen_pct": 30})
    guardado = c.get(f"/api/catalogo/{pid}").json()

    assert guardado["costo_total_ars"] == sim["costo_nuevo"]
    assert guardado["precio_publicado_ars"] == sim["precio_nuevo"]
    assert round(guardado["margen_pct"], 1) == sim["margen_pct"]


def test_el_dolar_elegido_queda_fijado_para_todo_el_catalogo(tmp_path, monkeypatch):
    """Si unos productos quedaran al dólar nuevo y otros al viejo, la tabla
    mezclaría dos monedas distintas."""
    c, cli, pid = _app_con_publicado(tmp_path, monkeypatch, "fijado.db")
    otro = _alta(c, asin="B0OTRO00001", marca="LEGO", titulo_ml="Otro",
                 precio_usd=100.0).json()["id"]
    antes = c.get(f"/api/catalogo/{otro}").json()["costo_total_ars"]

    c.post("/api/catalogo/precios/aplicar",
           json={"ids": [pid], "tc_costo": 1600, "margen_pct": 30})

    assert c.get("/api/dolar-costo").json()["tc_manual"] == 1600
    # El que no se tocó también quedó valuado al dólar nuevo.
    assert c.get(f"/api/catalogo/{otro}").json()["costo_total_ars"] != antes


def test_se_puede_volver_al_dolar_del_mercado(tmp_path, monkeypatch):
    c, cli, pid = _app_con_publicado(tmp_path, monkeypatch, "volver.db")
    c.post("/api/catalogo/precios/aplicar",
           json={"ids": [pid], "tc_costo": 1600, "margen_pct": 30})
    con_manual = c.get(f"/api/catalogo/{pid}").json()["costo_total_ars"]

    r = c.patch("/api/dolar-costo", json={"tc_manual": None}).json()

    assert r["tc_manual"] is None
    assert c.get(f"/api/catalogo/{pid}").json()["costo_total_ars"] != con_manual


def test_un_dolar_fijado_invalido_se_rechaza(client):
    assert client.patch("/api/dolar-costo", json={"tc_manual": -5}).status_code == 400
    assert client.patch("/api/dolar-costo", json={"tc_manual": "mil"}).status_code == 400


def test_si_el_precio_no_queda_en_ml_no_se_informa_como_actualizado(tmp_path, monkeypatch):
    """El caso real: el panel decía "114 publicaciones actualizadas" y en
    MercadoLibre no había cambiado nada. Un 200 no alcanza."""
    import api.catalogo_routes as rutas
    from mercadolibre.client import MeliAPIError

    class _Cli:
        def ficha_de_catalogo(self, *a, **k): return {}
        def atributos_obligatorios(self, cat_id): return []
        def valores_permitidos(self, cat_id): return {}
        def publicar(self, item):
            return {"id": "MLA100", "permalink": "http://ml/x", "status": "active"}
        def poner_descripcion(self, item_id, texto): return {}

        def actualizar_precio(self, item_id, precio):
            # Como el cliente real cuando el precio no quedó.
            raise MeliAPIError("MercadoLibre aceptó el pedido pero el precio "
                               f"quedó en 999 en vez de {precio}",
                               status=200, cuerpo={"id": item_id, "price": 999})

    cli = _Cli()
    monkeypatch.setattr(rutas, "MeliClient", lambda *a, **k: cli)
    monkeypatch.setattr(rutas.MeliCredenciales, "configurado", property(lambda self: True))
    monkeypatch.setattr(rutas.TokenStore, "hay_sesion", lambda self: True)

    c = TestClient(crear_app(db_path=str(tmp_path / "nopego.db")))
    pid = _alta(c, asin="B0NOPEGO01", marca="LEGO", titulo_ml="LEGO Set",
                precio_usd=100.0, ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://i/1.jpg"]})
    c.post(f"/api/catalogo/{pid}/aprobar")
    c.post(f"/api/catalogo/{pid}/publicar", json={})
    antes = c.get(f"/api/catalogo/{pid}").json()["precio_publicado_ars"]

    d = c.post("/api/catalogo/precios/aplicar",
               json={"ids": [pid], "tc_costo": 1600, "margen_pct": 30}).json()

    assert d["actualizados"] == 0
    # Y el mensaje dice qué pasó, no un diccionario crudo.
    assert "quedó en 999" in d["resultados"][0]["error"]
    assert c.get(f"/api/catalogo/{pid}").json()["precio_publicado_ars"] == antes


def test_mandar_el_precio_que_ya_estaba_no_es_una_actualizacion(tmp_path, monkeypatch):
    """El caso real: se aplicó sin las cotizaciones, se recalculó con el margen
    viejo, y a MercadoLibre le llegó el precio que la publicación ya tenía. Se
    informaron «114 actualizadas» sin haber cambiado nada."""
    c, cli, pid = _app_con_publicado(tmp_path, monkeypatch, "igual.db")
    actual = c.get(f"/api/catalogo/{pid}").json()["precio_publicado_ars"]

    # Sin cotizaciones ni margen: da el mismo precio que ya tiene.
    d = c.post("/api/catalogo/precios/aplicar", json={"ids": [pid]}).json()

    assert d["resultados"][0]["precio_nuevo"] == actual
    assert d["resultados"][0]["sin_cambios"] is True
    assert d["actualizados"] == 0 and d["sin_cambios"] == 1


def test_con_las_cotizaciones_si_cuenta_como_actualizada(tmp_path, monkeypatch):
    c, cli, pid = _app_con_publicado(tmp_path, monkeypatch, "distinto.db")

    d = c.post("/api/catalogo/precios/aplicar",
               json={"ids": [pid], "tc_costo": 1600, "margen_pct": 30}).json()

    assert d["resultados"][0]["sin_cambios"] is False
    assert d["actualizados"] == 1 and d["sin_cambios"] == 0


def test_una_tanda_lenta_devuelve_lo_pendiente_en_vez_de_morirse(tmp_path, monkeypatch):
    """Una publicación lenta puede tardar 20 segundos: cinco alcanzan para
    pasarse del tope del panel. Si la llamada se muere entera no se sabe qué se
    aplicó y los que no se tocaron se pierden, porque el panel ya los sacó de su
    cola. El servidor se corta solo y los devuelve."""
    import api.catalogo_routes as rutas
    c, cli, pid = _app_con_publicado(tmp_path, monkeypatch, "lento.db")
    otros = [_alta(c, asin=f"B0LENTO{i:04d}", marca="LEGO", titulo_ml=f"Set {i}",
                   precio_usd=100.0).json()["id"] for i in range(3)]
    for i, oid in enumerate(otros):
        c.post(f"/api/catalogo/{oid}/publicado",
               json={"ml_item_id": f"MLA90{i}", "ml_permalink": "http://x"})

    # Cada publicación consume todo el presupuesto: solo entra la primera.
    monkeypatch.setattr(rutas, "TOPE_APLICAR_SEG", 0.0)

    d = c.post("/api/catalogo/precios/aplicar",
               json={"ids": [pid] + otros, "tc_costo": 1600}).json()

    assert len(d["resultados"]) == 1, "no intentó ni una: no avanzaría nunca"
    assert d["pendientes"] == otros, "los que no se tocaron se perderían"


def test_sin_apuro_no_queda_nada_pendiente(tmp_path, monkeypatch):
    c, cli, pid = _app_con_publicado(tmp_path, monkeypatch, "sinapuro.db")
    d = c.post("/api/catalogo/precios/aplicar",
               json={"ids": [pid], "tc_costo": 1600}).json()
    assert d["pendientes"] == []
    assert len(d["resultados"]) == 1


# ---- mejorar títulos y descripciones de lo ya publicado -------------------

def _publicado_con_titulo_crudo(tmp_path, monkeypatch, nombre, cli=None):
    """Una publicación como las que ya están: título crudo de Amazon."""
    cli = cli or _cli_lote([])
    cli.publicar = lambda item: {"id": "MLA100", "permalink": "http://ml/x",
                                 "status": "active"}
    cli.atributos_obligatorios = lambda cid: []
    _con_ml(monkeypatch, cli)
    c = TestClient(crear_app(db_path=str(tmp_path / nombre)))
    crudo = ("LEGO Minecraft The Rabbit Ranch House Farm Set, 21181 Animals Toy "
             "for Kids, Boys and Girls Age 8 Plus")
    pid = _alta(c, marca="LEGO", modelo=crudo, titulo_ml=crudo[:60],
                ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://i/1.jpg"]})
    c.post(f"/api/catalogo/{pid}/aprobar")
    assert c.post(f"/api/catalogo/{pid}/publicar", json={}).status_code == 200
    return c, cli, pid


def test_simular_muestra_el_titulo_nuevo_sin_tocar_mercadolibre(tmp_path, monkeypatch):
    c, cli, pid = _publicado_con_titulo_crudo(tmp_path, monkeypatch, "tx1.db")
    d = c.post("/api/catalogo/publicaciones/simular", json={}).json()
    f = d["filas"][0]

    assert f["cambia_titulo"] is True
    assert f["titulo_nuevo"] == "Set LEGO Minecraft The Rabbit Ranch House Farm 21181"
    assert len(f["titulo_nuevo"]) <= 60
    # La descripción se rehace siempre: la de Amazon no dice lo que hace falta.
    assert "CÓMO ES LA COMPRA" in f["descripcion_nueva"]
    # Y el título local sigue intacto: simular no cambia nada.
    assert c.get(f"/api/catalogo/{pid}").json()["titulo_ml"].startswith("LEGO Minecraft")


def test_aplicar_manda_el_titulo_a_mercadolibre_y_lo_guarda(tmp_path, monkeypatch):
    puestos = []
    cli = _cli_lote([])
    cli.actualizar_titulo = lambda item_id, t: puestos.append((item_id, t)) or {}
    cli.poner_descripcion = lambda item_id, t: puestos.append((item_id, "desc")) or {}
    c, cli, pid = _publicado_con_titulo_crudo(tmp_path, monkeypatch, "tx2.db", cli)

    d = c.post("/api/catalogo/publicaciones/aplicar", json={"ids": [pid]}).json()

    esperado = "Set LEGO Minecraft The Rabbit Ranch House Farm 21181"
    assert d["titulos"] == 1 and d["descripciones"] == 1
    assert ("MLA100", esperado) in puestos
    # El catálogo local queda con el título que MercadoLibre aceptó.
    assert c.get(f"/api/catalogo/{pid}").json()["titulo_ml"] == esperado


def test_si_ml_no_deja_cambiar_el_titulo_no_se_guarda_como_cambiado(tmp_path, monkeypatch):
    """En una publicación de catálogo el título lo pone MercadoLibre. Guardarlo
    local igual sería decir que la publicación tiene un título que no tiene."""
    from mercadolibre.client import MeliAPIError
    cli = _cli_lote([])

    def _falla(item_id, t):
        raise MeliAPIError("el título quedó en «otro»", status=200, cuerpo={})

    cli.actualizar_titulo = _falla
    cli.poner_descripcion = lambda item_id, t: {}
    c, cli, pid = _publicado_con_titulo_crudo(tmp_path, monkeypatch, "tx3.db", cli)
    antes = c.get(f"/api/catalogo/{pid}").json()["titulo_ml"]

    d = c.post("/api/catalogo/publicaciones/aplicar", json={"ids": [pid]}).json()

    assert d["titulos"] == 0 and d["fallas"] == 1
    assert c.get(f"/api/catalogo/{pid}").json()["titulo_ml"] == antes
    # La descripción es independiente: que el título no salga no la frena.
    assert d["descripciones"] == 1


def test_se_puede_pedir_solo_la_descripcion(tmp_path, monkeypatch):
    puestos = []
    cli = _cli_lote([])
    cli.actualizar_titulo = lambda i, t: puestos.append("titulo") or {}
    cli.poner_descripcion = lambda i, t: puestos.append("desc") or {}
    c, cli, pid = _publicado_con_titulo_crudo(tmp_path, monkeypatch, "tx4.db", cli)

    d = c.post("/api/catalogo/publicaciones/aplicar",
               json={"ids": [pid], "titulo": False}).json()
    assert d["titulos"] == 0 and d["descripciones"] == 1
    assert "titulo" not in puestos


def test_aplicar_publicaciones_devuelve_lo_pendiente_si_se_acaba_el_tiempo(tmp_path, monkeypatch):
    import api.catalogo_routes as rutas
    cli = _cli_lote([])
    cli.actualizar_titulo = lambda i, t: {}
    cli.poner_descripcion = lambda i, t: {}
    c, cli, pid = _publicado_con_titulo_crudo(tmp_path, monkeypatch, "tx5.db", cli)
    otros = []
    for i in range(3):
        oid = _alta(c, asin=f"B0TX{i:05d}", marca="LEGO",
                    modelo=f"LEGO Minecraft Set {i} 2110{i}",
                    titulo_ml=f"LEGO Minecraft Set {i} 2110{i}",
                    ml_category_id="MLA1157").json()["id"]
        c.patch(f"/api/catalogo/{oid}/publicacion",
                json={"pictures": ["http://i/1.jpg"]})
        c.post(f"/api/catalogo/{oid}/aprobar")
        assert c.post(f"/api/catalogo/{oid}/publicar", json={}).status_code == 200
        otros.append(oid)
    monkeypatch.setattr(rutas, "TOPE_APLICAR_SEG", 0.0)

    d = c.post("/api/catalogo/publicaciones/aplicar",
               json={"ids": [pid] + otros}).json()
    assert len(d["resultados"]) == 1
    assert d["pendientes"] == otros


def test_las_condiciones_de_compra_se_guardan_y_salen_en_la_descripcion(tmp_path, monkeypatch):
    c, cli, pid = _publicado_con_titulo_crudo(tmp_path, monkeypatch, "tx6.db")
    c.post("/api/catalogo/config",
           json={"texto_compra": "ENVÍO\n• Llega en {dias} días hábiles."})

    d = c.post("/api/catalogo/publicaciones/simular", json={}).json()
    texto = d["filas"][0]["descripcion_nueva"]
    assert "Llega en 25 días hábiles." in texto
    assert "CÓMO ES LA COMPRA" not in texto


def test_el_diagnostico_dice_por_que_no_se_puede_cambiar_el_titulo(tmp_path, monkeypatch):
    """Averiguarlo intentando el cambio cuesta un pedido por producto, dispara
    el límite de ritmo de ML y mezcla el diagnóstico con los errores. Una
    consulta de solo lectura contesta lo mismo sin tocar nada."""
    escrituras = []
    cli = _cli_lote([])
    cli.actualizar = lambda *a, **k: escrituras.append(a) or {}
    cli.obtener_varios = lambda ids: {
        "MLA100": {"id": "MLA100", "title": "El de ML", "catalog_listing": True,
                   "status": "active"},
    }
    c, cli, pid = _publicado_con_titulo_crudo(tmp_path, monkeypatch, "diag.db", cli)

    d = c.get("/api/catalogo/publicaciones/diagnostico").json()

    assert d["resumen"]["catalogo"] == 1 and d["resumen"]["editable"] == 0
    assert "catálogo" in d["filas"][0]["motivo"]
    assert escrituras == [], "el diagnóstico no puede escribir nada"


def test_el_diagnostico_distingue_familia_de_editable(tmp_path, monkeypatch):
    cli = _cli_lote([])
    cli.obtener_varios = lambda ids: {
        "MLA100": {"id": "MLA100", "title": "Set", "family_name": "LEGO Minecraft",
                   "status": "active"},
    }
    c, cli, pid = _publicado_con_titulo_crudo(tmp_path, monkeypatch, "diag2.db", cli)
    d = c.get("/api/catalogo/publicaciones/diagnostico").json()
    assert d["resumen"]["familia"] == 1
    assert d["filas"][0]["family_name"] == "LEGO Minecraft"

    cli.obtener_varios = lambda ids: {"MLA100": {"id": "MLA100", "title": "Set",
                                                 "status": "active"}}
    d2 = c.get("/api/catalogo/publicaciones/diagnostico").json()
    assert d2["resumen"]["editable"] == 1


def test_el_diagnostico_no_miente_si_no_pudo_leer(tmp_path, monkeypatch):
    """Lo que no vuelve del multiget no se puede clasificar: decir que es
    editable sería inventar."""
    cli = _cli_lote([])
    cli.obtener_varios = lambda ids: {}
    c, cli, pid = _publicado_con_titulo_crudo(tmp_path, monkeypatch, "diag3.db", cli)
    d = c.get("/api/catalogo/publicaciones/diagnostico").json()
    assert d["resumen"]["no_leido"] == 1 and d["resumen"]["editable"] == 0


def test_probar_titulo_devuelve_el_cuerpo_crudo_de_mercadolibre(tmp_path, monkeypatch):
    """Sin el crudo no hay diagnóstico. Reemplazarlo por una lectura mía ya
    costó una vuelta entera: los 115 rechazos llegaron con un mensaje que
    tapaba el de ML."""
    from mercadolibre.client import MeliAPIError
    cli = _cli_lote([])
    cuerpo = {"message": "The field family name is invalid", "cause": []}

    def _falla(item_id, t):
        raise MeliAPIError("no", status=400, cuerpo=cuerpo)

    cli.actualizar_titulo = _falla
    c, cli, pid = _publicado_con_titulo_crudo(tmp_path, monkeypatch, "probar.db", cli)
    antes = c.get(f"/api/catalogo/{pid}").json()["titulo_ml"]

    d = c.post(f"/api/catalogo/{pid}/probar-titulo").json()

    assert d["ok"] is False
    assert d["crudo"] == cuerpo, "se perdió la respuesta cruda de MercadoLibre"
    assert c.get(f"/api/catalogo/{pid}").json()["titulo_ml"] == antes


def test_probar_titulo_guarda_si_mercadolibre_lo_acepta(tmp_path, monkeypatch):
    cli = _cli_lote([])
    cli.actualizar_titulo = lambda i, t: {}
    c, cli, pid = _publicado_con_titulo_crudo(tmp_path, monkeypatch, "probar2.db", cli)

    d = c.post(f"/api/catalogo/{pid}/probar-titulo").json()

    assert d["ok"] is True
    assert c.get(f"/api/catalogo/{pid}").json()["titulo_ml"] == d["titulo_probado"]


def test_probar_titulo_rebota_si_no_esta_publicado(client):
    pid = _alta(client, marca="LEGO", modelo="LEGO Set 21181").json()["id"]
    assert client.post(f"/api/catalogo/{pid}/probar-titulo").status_code == 409


def test_el_diagnostico_cuenta_pausadas_y_sin_stock(tmp_path, monkeypatch):
    """Una publicación pausada o sin stock se ve pero no se puede comprar:
    junta visitas y hasta intenciones de compra, y ninguna termina en venta.
    Es lo primero a mirar cuando el reporte de ML da visitas y cero ventas."""
    cli = _cli_lote([])
    cli.obtener_varios = lambda ids: {
        "MLA100": {"id": "MLA100", "title": "Set", "status": "paused",
                   "available_quantity": 0, "family_name": "Set"},
    }
    c, cli, pid = _publicado_con_titulo_crudo(tmp_path, monkeypatch, "salud.db", cli)

    d = c.get("/api/catalogo/publicaciones/diagnostico").json()

    assert d["salud"]["pausadas"] == 1 and d["salud"]["activas"] == 0
    assert d["salud"]["sin_stock"] == 1
    assert d["filas"][0]["stock"] == 0


def test_una_activa_con_stock_no_se_cuenta_como_problema(tmp_path, monkeypatch):
    cli = _cli_lote([])
    cli.obtener_varios = lambda ids: {
        "MLA100": {"id": "MLA100", "title": "Set", "status": "active",
                   "available_quantity": 3},
    }
    c, cli, pid = _publicado_con_titulo_crudo(tmp_path, monkeypatch, "salud2.db", cli)
    s = c.get("/api/catalogo/publicaciones/diagnostico").json()["salud"]
    assert s == {"activas": 1, "pausadas": 0, "otro_estado": 0, "sin_stock": 0}


# ---- vigilancia de precio y stock en Amazon ------------------------------

def _publicado_para_vigilar(tmp_path, monkeypatch, nombre, respuesta):
    """Un publicado real, con la lectura de Amazon interceptada."""
    import api.catalogo_routes as rutas
    cli = _cli_lote([])
    cli.publicar = lambda item: {"id": "MLA100", "permalink": "http://ml/x",
                                 "status": "active"}
    cli.atributos_obligatorios = lambda cid: []
    cli.pausar = lambda item_id: {"id": item_id, "status": "paused"}
    _con_ml(monkeypatch, cli)
    # Nunca se sale a la red: la respuesta de Amazon se programa acá.
    monkeypatch.setattr(rutas, "importar_desde_url", lambda url, **k: dict(respuesta))

    c = TestClient(crear_app(db_path=str(tmp_path / nombre)))
    pid = _alta(c, marca="LEGO", modelo="LEGO Ideas Mineral Collection 21362",
                titulo_ml="Set LEGO Ideas Mineral Collection 21362",
                precio_usd=31.69, regimen="landed",
                ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{pid}/publicacion", json={"pictures": ["http://i/1.jpg"]})
    c.post(f"/api/catalogo/{pid}/aprobar")
    assert c.post(f"/api/catalogo/{pid}/publicar", json={}).status_code == 200
    return c, cli, pid


def test_revisar_avisa_cuando_amazon_subio_el_precio(tmp_path, monkeypatch):
    """El caso real: se publicó con Amazon a US$31,69 y para cuando alguien
    compró estaba a US$53. La venta quedó en pérdida."""
    c, cli, pid = _publicado_para_vigilar(
        tmp_path, monkeypatch, "vg1.db",
        {"ok": True, "precio_usd": 53.00, "disponible": True, "mensaje": ""})

    d = c.post("/api/catalogo/vigilancia/revisar", json={"limite": 5}).json()
    f = d["filas"][0]

    assert f["precio_antes_usd"] == 31.69 and f["precio_ahora_usd"] == 53.00
    assert f["costo_ahora_ars"] > f["costo_antes_ars"]
    assert d["creditos_usados"] == 5
    # El precio publicado no se toca: informar no es cambiar.
    assert c.get(f"/api/catalogo/{pid}").json()["precio_publicado_ars"] == \
        f["precio_publicado_ars"]


def test_revisar_detecta_lo_que_se_quedo_sin_stock(tmp_path, monkeypatch):
    c, cli, pid = _publicado_para_vigilar(
        tmp_path, monkeypatch, "vg2.db",
        {"ok": True, "precio_usd": 59.99, "disponible": False, "mensaje": ""})

    d = c.post("/api/catalogo/vigilancia/revisar", json={"limite": 5}).json()

    assert d["sin_stock"] == 1
    assert d["filas"][0]["disponible"] is False
    # Y no lo pausa solo: eso es un paso aparte, con confirmación.
    assert c.get(f"/api/catalogo/{pid}").json()["estado"] == "publicado"


def test_revisar_no_pausa_lo_que_no_pudo_leer(tmp_path, monkeypatch):
    """Amazon bloquea seguido. Tomar un bloqueo por "sin stock" sacaría de
    venta productos que sí están disponibles."""
    c, cli, pid = _publicado_para_vigilar(
        tmp_path, monkeypatch, "vg3.db",
        {"ok": False, "precio_usd": None, "disponible": None,
         "bloqueado": True, "mensaje": "Amazon nos bloqueó"})

    d = c.post("/api/catalogo/vigilancia/revisar", json={"limite": 5}).json()

    assert d["sin_stock"] == 0 and d["no_leidos"] == 1
    assert d["filas"][0]["bloqueado"] is True
    assert c.get(f"/api/catalogo/{pid}").json()["disponibilidad"] == "in_stock"


def test_pausar_manda_la_pausa_a_mercadolibre(tmp_path, monkeypatch):
    pausados = []
    c, cli, pid = _publicado_para_vigilar(
        tmp_path, monkeypatch, "vg4.db",
        {"ok": True, "precio_usd": 59.99, "disponible": False, "mensaje": ""})
    cli.pausar = lambda item_id: pausados.append(item_id) or {"status": "paused"}

    d = c.post("/api/catalogo/vigilancia/pausar", json={"ids": [pid]}).json()

    assert d["pausadas"] == 1 and pausados == ["MLA100"]
    assert c.get(f"/api/catalogo/{pid}").json()["estado"] == "pausado"


def test_el_limite_de_revision_esta_acotado(tmp_path, monkeypatch):
    """Cada producto son 5 créditos de un plan de 1.000 por mes: un límite
    suelto vacía la cuenta en una llamada."""
    c, cli, pid = _publicado_para_vigilar(
        tmp_path, monkeypatch, "vg5.db",
        {"ok": True, "precio_usd": 40.0, "disponible": True, "mensaje": ""})

    d = c.post("/api/catalogo/vigilancia/revisar", json={"limite": 9999}).json()
    assert d["revisados"] <= 40


def test_la_ganancia_minima_se_guarda_y_recalcula_el_catalogo(tmp_path, monkeypatch):
    """El piso cambia el precio sugerido de todos los productos, no solo de los
    nuevos: si no se recalcula, la tabla muestra precios que ya no rigen."""
    c = TestClient(crear_app(db_path=str(tmp_path / "piso.db")))
    pid = _alta(c, marca="LEGO", modelo="LEGO Set 21181", precio_usd=100.0,
                regimen="landed", margen_deseado=0.30).json()["id"]
    antes = c.get(f"/api/catalogo/{pid}").json()["precio_sugerido_ars"]

    r = c.post("/api/catalogo/config", json={"ganancia_minima": 100000})
    assert r.json()["ganancia_minima"] == 100000.0

    despues = c.get(f"/api/catalogo/{pid}").json()["precio_sugerido_ars"]
    assert despues > antes


def test_una_ganancia_minima_negativa_se_rechaza(client):
    assert client.post("/api/catalogo/config",
                       json={"ganancia_minima": -5000}).status_code == 422


def test_el_piso_llega_al_precio_de_un_producto_nuevo(tmp_path, monkeypatch):
    """La regla es "cada vez que metamos un producto": tiene que valer al dar
    de alta, no solo al recalcular."""
    c = TestClient(crear_app(db_path=str(tmp_path / "piso2.db")))
    c.post("/api/catalogo/config", json={"ganancia_minima": 100000})

    p = _alta(c, marca="LEGO", modelo="LEGO Set chico", precio_usd=40.0,
              regimen="landed", margen_deseado=0.30).json()
    # 40 USD puestos son un costo bajo: el 30% no llega a 100.000, manda el piso.
    assert p["precio_sugerido_ars"] > p["costo_total_ars"] + 100000


# ---- botón rápido de pausar / reactivar ----------------------------------

def test_pausar_en_lote_manda_la_pausa_a_mercadolibre(tmp_path, monkeypatch):
    pausados = []
    c, cli, pid = _publicado_para_vigilar(
        tmp_path, monkeypatch, "lp1.db",
        {"ok": True, "precio_usd": 40.0, "disponible": True, "mensaje": ""})
    cli.pausar = lambda item_id: pausados.append(item_id) or {"status": "paused"}

    d = c.post("/api/catalogo/lote/pausar", json={"ids": [pid]}).json()

    assert all(r["ok"] for r in d["resultados"])
    assert pausados == ["MLA100"]
    assert c.get(f"/api/catalogo/{pid}").json()["estado"] == "pausado"


def test_reactivar_en_lote_vuelve_a_ponerla_a_la_venta(tmp_path, monkeypatch):
    reactivados = []
    c, cli, pid = _publicado_para_vigilar(
        tmp_path, monkeypatch, "lp2.db",
        {"ok": True, "precio_usd": 40.0, "disponible": True, "mensaje": ""})
    cli.pausar = lambda item_id: {"status": "paused"}
    cli.reactivar = lambda item_id: reactivados.append(item_id) or {"status": "active"}
    c.post("/api/catalogo/lote/pausar", json={"ids": [pid]})

    d = c.post("/api/catalogo/lote/reactivar", json={"ids": [pid]}).json()

    assert all(r["ok"] for r in d["resultados"])
    assert reactivados == ["MLA100"]
    assert c.get(f"/api/catalogo/{pid}").json()["estado"] == "publicado"


def test_el_error_de_uno_no_frena_el_lote(tmp_path, monkeypatch):
    """Un producto que MercadoLibre rechaza no puede dejar sin pausar al resto,
    ni pasar en silencio."""
    from mercadolibre.client import MeliAPIError
    c, cli, pid = _publicado_para_vigilar(
        tmp_path, monkeypatch, "lp3.db",
        {"ok": True, "precio_usd": 40.0, "disponible": True, "mensaje": ""})
    otro = _alta(c, asin="B0OTRO00099", marca="LEGO", titulo_ml="Otro",
                 ml_category_id="MLA1157").json()["id"]
    c.patch(f"/api/catalogo/{otro}/publicacion", json={"pictures": ["http://i/1.jpg"]})
    c.post(f"/api/catalogo/{otro}/aprobar")
    c.post(f"/api/catalogo/{otro}/publicar", json={})

    llamadas = []

    def _pausar(item_id):
        llamadas.append(item_id)
        if len(llamadas) == 1:
            raise MeliAPIError("no se puede", status=400, cuerpo={})
        return {"status": "paused"}

    cli.pausar = _pausar
    d = c.post("/api/catalogo/lote/pausar", json={"ids": [pid, otro]}).json()

    assert len(llamadas) == 2, "el error del primero frenó al segundo"
    assert sum(1 for r in d["resultados"] if r["ok"]) == 1
    assert sum(1 for r in d["resultados"] if not r["ok"]) == 1


def test_el_agente_de_revision_tiene_su_propio_estado(tmp_path, monkeypatch):
    c, cli, pid = _publicado_para_vigilar(
        tmp_path, monkeypatch, "rev1.db",
        {"ok": True, "precio_usd": 40.0, "disponible": True, "mensaje": ""})

    e = c.get("/api/revision").json()
    assert e["por_revisar"] == 1 and e["revisados"] == 0
    # Por defecto NO usa ScraperAPI: recorre el catálogo entero y son 5
    # créditos por producto.
    assert e["con_proxy"] is False

    r = c.post("/api/revision/tick", json={}).json()
    assert r["accion"] in ("revisar", "margen_bajo")
    assert c.get("/api/revision").json()["revisados"] == 1


def test_el_navegador_puede_reportar_lo_que_leyo(tmp_path, monkeypatch):
    """El camino que no gasta créditos: la ficha la lee la PC del usuario."""
    pausados = []
    c, cli, pid = _publicado_para_vigilar(
        tmp_path, monkeypatch, "rev2.db",
        {"ok": True, "precio_usd": 40.0, "disponible": True, "mensaje": ""})
    cli.pausar = lambda item_id: pausados.append(item_id) or {"status": "paused"}

    d = c.post("/api/revision/reportar", json={"productos": [
        {"id": pid, "precio_usd": 53.0, "disponible": False}]}).json()

    assert d["revisados"] == 1 and d["pausadas"] == 1
    assert pausados == ["MLA100"]
    assert c.get(f"/api/catalogo/{pid}").json()["precio_usd"] == 53.0


def test_lo_que_el_navegador_no_pudo_leer_no_se_toca(tmp_path, monkeypatch):
    c, cli, pid = _publicado_para_vigilar(
        tmp_path, monkeypatch, "rev3.db",
        {"ok": True, "precio_usd": 40.0, "disponible": True, "mensaje": ""})
    antes = c.get(f"/api/catalogo/{pid}").json()["precio_usd"]

    d = c.post("/api/revision/reportar", json={"productos": [
        {"id": pid, "precio_usd": None, "disponible": None}]}).json()

    assert d["revisados"] == 0 and d["no_leidos"] == 1
    assert c.get(f"/api/catalogo/{pid}").json()["precio_usd"] == antes
    assert c.get(f"/api/catalogo/{pid}").json()["estado"] == "publicado"


def test_pausar_todo_saca_de_venta_todo_lo_publicado(tmp_path, monkeypatch):
    """Freno de mano: cuando el catálogo está mal calculado, seguir vendiendo
    es vender a pérdida o no poder entregar."""
    pausados = []
    c, cli, pid = _publicado_para_vigilar(
        tmp_path, monkeypatch, "todo1.db",
        {"ok": True, "precio_usd": 40.0, "disponible": True, "mensaje": ""})
    cli.pausar = lambda item_id: pausados.append(item_id) or {"status": "paused"}

    d = c.post("/api/catalogo/pausar-todo", json={}).json()

    assert d["pausadas"] == 1 and d["quedan"] == 0
    assert pausados == ["MLA100"]
    assert c.get(f"/api/catalogo/{pid}").json()["estado"] == "pausado"


def test_pausar_todo_no_toca_borradores(tmp_path, monkeypatch):
    c, cli, pid = _publicado_para_vigilar(
        tmp_path, monkeypatch, "todo2.db",
        {"ok": True, "precio_usd": 40.0, "disponible": True, "mensaje": ""})
    borrador = _alta(c, asin="B0BORRA0001", marca="LEGO",
                     modelo="Sin publicar").json()["id"]
    cli.pausar = lambda item_id: {"status": "paused"}

    c.post("/api/catalogo/pausar-todo", json={})

    assert c.get(f"/api/catalogo/{borrador}").json()["estado"] == "borrador"


def test_pausar_todo_devuelve_lo_que_falta_si_se_acaba_el_tiempo(tmp_path, monkeypatch):
    """Con 126 publicaciones no entra en un solo pedido: hay que poder seguir."""
    import api.catalogo_routes as rutas
    c, cli, pid = _publicado_para_vigilar(
        tmp_path, monkeypatch, "todo3.db",
        {"ok": True, "precio_usd": 40.0, "disponible": True, "mensaje": ""})
    for i in range(2):
        oid = _alta(c, asin=f"B0TODO{i:05d}", marca="LEGO",
                    titulo_ml=f"Otro {i}", ml_category_id="MLA1157").json()["id"]
        c.patch(f"/api/catalogo/{oid}/publicacion",
                json={"pictures": ["http://i/1.jpg"]})
        c.post(f"/api/catalogo/{oid}/aprobar")
        c.post(f"/api/catalogo/{oid}/publicar", json={})
    cli.pausar = lambda item_id: {"status": "paused"}
    monkeypatch.setattr(rutas, "TOPE_APLICAR_SEG", 0.0)

    d = c.post("/api/catalogo/pausar-todo", json={}).json()
    assert d["pausadas"] == 1 and d["quedan"] == 2


def test_cambiar_el_envio_por_ciento_reestima_todo_el_catalogo(tmp_path, monkeypatch):
    """El costo mal estimado es lo que hace vender a pérdida. Cambiar el
    porcentaje tiene que mover a los productos que ya están cargados."""
    c = TestClient(crear_app(db_path=str(tmp_path / "envpct.db")))
    pid = _alta(c, marca="LEGO", modelo="LEGO Set", precio_usd=100.0,
                costo_envio_usd=0, regimen="landed").json()["id"]
    antes = c.get(f"/api/catalogo/{pid}").json()
    assert antes["costo_envio_usd"] == pytest.approx(26.0, abs=0.01)

    r = c.post("/api/catalogo/config", json={"envio_import_pct": 60})
    assert r.json()["envio_import_pct"] == 60.0

    despues = c.get(f"/api/catalogo/{pid}").json()
    assert despues["costo_envio_usd"] == pytest.approx(60.0, abs=0.01)
    assert despues["costo_total_ars"] > antes["costo_total_ars"]


def test_el_costo_a_mano_se_edita_desde_el_panel(client):
    pid = _alta(client, marca="LEGO", modelo="LEGO Set", precio_usd=100.0,
                costo_envio_usd=0, regimen="landed").json()["id"]
    antes = client.get(f"/api/catalogo/{pid}").json()

    r = client.patch(f"/api/catalogo/{pid}/costo", json={"costo_ars": 250000})

    assert r.status_code == 200
    d = r.json()
    assert d["costo_total_ars"] == 250000
    assert d["precio_sugerido_ars"] > antes["precio_sugerido_ars"]


def test_vaciar_el_costo_vuelve_a_la_estimacion(client):
    pid = _alta(client, marca="LEGO", modelo="LEGO Set 2", precio_usd=100.0,
                costo_envio_usd=0, regimen="landed").json()["id"]
    estimado = client.get(f"/api/catalogo/{pid}").json()["costo_total_ars"]
    client.patch(f"/api/catalogo/{pid}/costo", json={"costo_ars": 999999})

    d = client.patch(f"/api/catalogo/{pid}/costo", json={"costo_ars": None}).json()

    assert d["costo_total_ars"] == pytest.approx(estimado, abs=1)


def test_un_costo_negativo_se_rechaza(client):
    pid = _alta(client, marca="LEGO", modelo="LEGO Set 3").json()["id"]
    assert client.patch(f"/api/catalogo/{pid}/costo",
                        json={"costo_ars": -5}).status_code == 422
