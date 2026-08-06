"""Test de la protección por contraseña del panel (PANEL_PASSWORD)."""

import base64

from fastapi.testclient import TestClient

from api.server import crear_app


def _cliente(tmp_path):
    return TestClient(crear_app(db_path=str(tmp_path / "t.db")))


def test_sin_password_no_pide_login(tmp_path, monkeypatch):
    monkeypatch.delenv("PANEL_PASSWORD", raising=False)
    assert _cliente(tmp_path).get("/api/catalogo").status_code == 200


def test_con_password_bloquea_sin_credenciales(tmp_path, monkeypatch):
    monkeypatch.setenv("PANEL_PASSWORD", "secreta123")
    c = _cliente(tmp_path)
    r = c.get("/panel")
    assert r.status_code == 401
    assert "WWW-Authenticate" in r.headers


def test_con_password_permite_con_credenciales(tmp_path, monkeypatch):
    monkeypatch.setenv("PANEL_PASSWORD", "secreta123")
    c = _cliente(tmp_path)
    token = base64.b64encode(b"admin:secreta123").decode()
    r = c.get("/api/catalogo", headers={"Authorization": f"Basic {token}"})
    assert r.status_code == 200


def test_password_incorrecta_rechazada(tmp_path, monkeypatch):
    monkeypatch.setenv("PANEL_PASSWORD", "secreta123")
    c = _cliente(tmp_path)
    token = base64.b64encode(b"admin:otra").decode()
    r = c.get("/api/catalogo", headers={"Authorization": f"Basic {token}"})
    assert r.status_code == 401
