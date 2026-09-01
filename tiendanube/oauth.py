"""
OAuth 2.0 con Tiendanube / Nuvemshop (flujo authorization code).

Pasos:
  1. Se crea una app en el portal de Partners de Tiendanube y se obtienen
     `client_id` (que ahí figura como App ID) y `client_secret`, y se registra
     una Redirect URI.
  2. El comerciante entra a la URL de autorización (`url_autorizacion`) e
     instala la app en su tienda.
  3. Tiendanube redirige al callback con un `code`.
  4. La app cambia ese `code` por el access_token (`intercambiar_codigo`).

**Diferencia importante con MercadoLibre**: el token de Tiendanube *no vence* y
no hay refresh_token. Se pide una vez y queda. Por eso acá no existe
`refrescar()`: si algún día deja de andar, es porque el comerciante desinstaló
la app y hay que volver a autorizar, no porque haya que renovar nada.

La respuesta del token trae además el `user_id`, que **es el id de la tienda** y
va en la URL de todas las llamadas a la API. Sin él no se puede hacer nada, así
que se guarda junto al token.

Las credenciales de la app se leen de variables de entorno; nunca se hardcodean
ni se commitean.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import requests

# El comerciante instala la app desde acá. Lleva el App ID en la ruta, no como
# parámetro, que es distinto de casi todos los OAuth.
AUTH_BASE = "https://www.tiendanube.com/apps/{app_id}/authorize"
TOKEN_URL = "https://www.tiendanube.com/apps/authorize/token"


@dataclass
class TiendanubeCredenciales:
    client_id: str
    client_secret: str
    redirect_uri: str
    # Tiendanube EXIGE un User-Agent que identifique la app y un mail de
    # contacto; sin eso rechaza las llamadas. No es opcional ni cosmético.
    user_agent: str

    @classmethod
    def desde_entorno(cls) -> "TiendanubeCredenciales":
        """Lee TIENDANUBE_CLIENT_ID / _SECRET / _REDIRECT_URI / _USER_AGENT."""
        cid = os.environ.get("TIENDANUBE_CLIENT_ID", "")
        sec = os.environ.get("TIENDANUBE_CLIENT_SECRET", "")
        uri = os.environ.get("TIENDANUBE_REDIRECT_URI", "")
        ua = os.environ.get("TIENDANUBE_USER_AGENT",
                            "AmazonToMeli (sin-mail-configurado)")
        return cls(cid, sec, uri, ua)

    @property
    def configurado(self) -> bool:
        return bool(self.client_id and self.client_secret)


class TiendanubeTokenStore:
    """Guarda el token y el id de tienda vigentes (una fila) en la base."""

    def __init__(self, conn):
        self.conn = conn
        # Igual que el de MercadoLibre: por `preparar`, para que el esquema se
        # aplique al abrir la conexión y no al arrancar la app. Con la base
        # dormida en la nube, tocarla en el arranque lo cuelga.
        self.conn.preparar("""
            CREATE TABLE IF NOT EXISTS tiendanube_token (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                access_token TEXT, store_id TEXT, scope TEXT, actualizado TEXT
            );""")
        self.conn.commit()

    def guardar(self, data: dict) -> None:
        self.conn.execute(
            """INSERT INTO tiendanube_token (id, access_token, store_id, scope,
                                             actualizado)
               VALUES (1, ?, ?, ?, datetime('now'))
               ON CONFLICT(id) DO UPDATE SET
                 access_token=excluded.access_token,
                 store_id=excluded.store_id,
                 scope=excluded.scope,
                 actualizado=excluded.actualizado""",
            (data.get("access_token"),
             # Viene como `user_id` pero es el id de la tienda; se guarda con el
             # nombre que describe para qué se usa.
             str(data.get("user_id") or ""),
             data.get("scope") or ""),
        )
        self.conn.commit()

    def leer(self) -> Optional[dict]:
        return self.conn.execute(
            "SELECT * FROM tiendanube_token WHERE id = 1").fetchone()

    def hay_sesion(self) -> bool:
        fila = self.leer()
        # Sin store_id el token no sirve para nada: no hay URL a la que pegarle.
        return bool(fila and fila["access_token"] and fila["store_id"])

    def borrar(self) -> None:
        self.conn.execute("DELETE FROM tiendanube_token WHERE id = 1")
        self.conn.commit()


class TiendanubeOAuth:
    def __init__(self, cred: TiendanubeCredenciales, store: TiendanubeTokenStore):
        self.cred = cred
        self.store = store

    def url_autorizacion(self, state: str = "arbitraje") -> str:
        base = AUTH_BASE.format(app_id=self.cred.client_id)
        params = {"state": state}
        if self.cred.redirect_uri:
            params["redirect_uri"] = self.cred.redirect_uri
        return f"{base}?{urlencode(params)}"

    def intercambiar_codigo(self, code: str) -> dict:
        """Cambia el `code` del callback por el token y lo guarda."""
        resp = requests.post(
            TOKEN_URL,
            json={
                "client_id": self.cred.client_id,
                "client_secret": self.cred.client_secret,
                "grant_type": "authorization_code",
                "code": code,
            },
            headers={"Content-Type": "application/json",
                     "User-Agent": self.cred.user_agent},
            timeout=20,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Tiendanube OAuth error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        if not data.get("access_token") or not data.get("user_id"):
            # Pasa si la app no tiene los permisos pedidos. Se muestra crudo:
            # una lectura mía puede errarle, el texto de Tiendanube no.
            raise RuntimeError(
                "Tiendanube no devolvió access_token y user_id. Respondió: "
                f"{str(data)[:300]}")
        self.store.guardar(data)
        return data

    def access_token_valido(self) -> str:
        """El token guardado. No vence, así que no hay nada que renovar."""
        fila = self.store.leer()
        if not fila or not fila["access_token"]:
            raise RuntimeError("No hay sesión de Tiendanube. Autorizá primero.")
        return fila["access_token"]

    def store_id(self) -> str:
        fila = self.store.leer()
        if not fila or not fila["store_id"]:
            raise RuntimeError("No hay id de tienda de Tiendanube. Autorizá primero.")
        return fila["store_id"]
