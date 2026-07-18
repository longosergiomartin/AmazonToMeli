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


def test_oauth_status_sin_credenciales(client):
    s = client.get("/oauth/status").json()
    assert s["configurado"] is False and s["conectado"] is False


def test_pausar_sin_publicar_cambia_estado_local(client):
    pid = _alta(client).json()["id"]
    r = client.post(f"/api/catalogo/{pid}/pausar")
    assert r.status_code == 200 and r.json()["estado"] == "pausado"
