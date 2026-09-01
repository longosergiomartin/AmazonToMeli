"""
Integración con la API de Tiendanube / Nuvemshop.

  - oauth.py   : flujo OAuth 2.0 (authorization code) + guardado del token y
                 del id de tienda. El token **no vence**: no hay refresh.
  - client.py  : cliente de la API (crear, actualizar, publicar/despublicar y
                 borrar productos; precio y stock por variante).
  - listing.py : arma el JSON del producto a partir del catálogo.

Nada se publica solo: publicar es siempre un paso explícito del usuario, igual
que con MercadoLibre.
"""

from .oauth import (TiendanubeOAuth, TiendanubeCredenciales,
                    TiendanubeTokenStore)
from .client import TiendanubeClient, TiendanubeAPIError, describir_error
from .listing import (construir_producto, vista_previa,
                      faltantes_para_publicar, precio_para_tiendanube,
                      titulo_para_tiendanube)

__all__ = ["TiendanubeOAuth", "TiendanubeCredenciales", "TiendanubeTokenStore",
           "TiendanubeClient", "TiendanubeAPIError", "describir_error",
           "construir_producto", "vista_previa", "faltantes_para_publicar",
           "precio_para_tiendanube", "titulo_para_tiendanube"]
