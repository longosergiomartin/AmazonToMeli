"""
OAuth 2.0 con MercadoLibre (flujo authorization code).

Pasos:
  1. El usuario crea una aplicación en https://developers.mercadolibre.com.ar/
     y obtiene APP_ID (client_id) y SECRET_KEY (client_secret), y configura una
     Redirect URI (ej: http://localhost:8321/oauth/callback).
  2. La app manda al usuario a la URL de autorización (`url_autorizacion`).
  3. MercadoLibre redirige al callback con un `code`.
  4. La app cambia ese `code` por access_token + refresh_token
     (`intercambiar_codigo`).
  5. Cuando el access_token vence (~6 h), se renueva con el refresh_token
     (`refrescar`).

Los tokens se guardan en la base (TokenStore): con Postgres configurado
sobreviven a los reinicios y la sesión queda enganchada sin reconectar. Las credenciales de la app
(client_id/secret) se leen de variables de entorno o se pasan explícitas —
nunca se hardcodean ni se commitean.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests

AUTH_BASE = "https://auth.mercadolibre.com.ar/authorization"
TOKEN_URL = "https://api.mercadolibre.com/oauth/token"


@dataclass
class MeliCredenciales:
    client_id: str
    client_secret: str
    redirect_uri: str

    @classmethod
    def desde_entorno(cls) -> "MeliCredenciales":
        """Lee MELI_CLIENT_ID / MELI_CLIENT_SECRET / MELI_REDIRECT_URI."""
        cid = os.environ.get("MELI_CLIENT_ID", "")
        sec = os.environ.get("MELI_CLIENT_SECRET", "")
        # MercadoLibre exige HTTPS y no acepta "localhost" ni, en la práctica,
        # IPs como 127.0.0.1 en el authorize. Para desarrollo local usamos una
        # URL https pública de callback (Postman) donde se lee el ?code=... y se
        # completa con "Pegar código". Debe coincidir EXACTO con la Redirect URI
        # registrada en la app. En producción, poné tu propio dominio https.
        uri = os.environ.get("MELI_REDIRECT_URI",
                             "https://oauth.pstmn.io/v1/callback")
        return cls(cid, sec, uri)

    @property
    def configurado(self) -> bool:
        return bool(self.client_id and self.client_secret)


class TokenStore:
    """Guarda el token vigente (una fila) en la base."""

    def __init__(self, conn):
        self.conn = conn
        # Va por executescript para que el esquema se reaplique solo si la
        # conexión se cae y se reconecta (base dormida en la nube).
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS meli_token (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                access_token TEXT, refresh_token TEXT,
                user_id INTEGER, expires_at REAL, actualizado TEXT
            );""")
        self.conn.commit()

    def guardar(self, data: dict) -> None:
        expires_at = time.time() + float(data.get("expires_in", 0))
        self.conn.execute(
            """INSERT INTO meli_token (id, access_token, refresh_token, user_id,
                                       expires_at, actualizado)
               VALUES (1, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(id) DO UPDATE SET
                 access_token=excluded.access_token,
                 refresh_token=excluded.refresh_token,
                 user_id=excluded.user_id,
                 expires_at=excluded.expires_at,
                 actualizado=excluded.actualizado""",
            (data.get("access_token"), data.get("refresh_token"),
             data.get("user_id"), expires_at),
        )
        self.conn.commit()

    def leer(self) -> Optional[dict]:
        return self.conn.execute("SELECT * FROM meli_token WHERE id = 1").fetchone()

    def hay_sesion(self) -> bool:
        return self.leer() is not None

    def borrar(self) -> None:
        self.conn.execute("DELETE FROM meli_token WHERE id = 1")
        self.conn.commit()


class MeliOAuth:
    def __init__(self, cred: MeliCredenciales, store: TokenStore):
        self.cred = cred
        self.store = store

    def url_autorizacion(self, state: str = "arbitraje") -> str:
        params = {
            "response_type": "code",
            "client_id": self.cred.client_id,
            "redirect_uri": self.cred.redirect_uri,
            "state": state,
        }
        return f"{AUTH_BASE}?{urlencode(params)}"

    def intercambiar_codigo(self, code: str) -> dict:
        """Cambia el `code` del callback por tokens y los guarda."""
        data = self._post_token({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.cred.redirect_uri,
        })
        self.store.guardar(data)
        return data

    def refrescar(self) -> dict:
        row = self.store.leer()
        if not row or not row["refresh_token"]:
            raise RuntimeError("No hay refresh_token; el usuario debe reautorizar.")
        data = self._post_token({
            "grant_type": "refresh_token",
            "refresh_token": row["refresh_token"],
        })
        self.store.guardar(data)
        return data

    def access_token_valido(self, margen_seg: int = 120) -> str:
        """Devuelve un access_token vigente, renovándolo si está por vencer."""
        row = self.store.leer()
        if not row:
            raise RuntimeError("No hay sesión de MercadoLibre. Autorizá primero.")
        if row["expires_at"] and row["expires_at"] - time.time() < margen_seg:
            self.refrescar()
            row = self.store.leer()
        return row["access_token"]

    def _post_token(self, extra: dict) -> dict:
        payload = {
            "client_id": self.cred.client_id,
            "client_secret": self.cred.client_secret,
        }
        payload.update(extra)
        resp = requests.post(
            TOKEN_URL, data=payload,
            headers={"Accept": "application/json"}, timeout=15,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"MercadoLibre OAuth error {resp.status_code}: {resp.text[:300]}")
        return resp.json()
