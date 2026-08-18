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
