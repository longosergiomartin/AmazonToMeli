"""
Integración con la API de MercadoLibre.

  - oauth.py  : flujo OAuth 2.0 (authorization code) + almacenamiento y refresh
                de tokens.
  - client.py : cliente de la API (categorías, atributos, publicar, actualizar,
                pausar). Todas las llamadas que modifican tu cuenta requieren un
                token válido; sin token, el cliente lanza un error claro.

Nada se publica solo: publicar es siempre un paso explícito que dispara el
usuario desde la app tras ver la vista previa.
"""

from .oauth import MeliOAuth, MeliCredenciales, TokenStore
from .client import MeliClient, MeliAPIError
from .listing import construir_item, vista_previa, faltantes_para_publicar

__all__ = ["MeliOAuth", "MeliCredenciales", "TokenStore", "MeliClient",
           "MeliAPIError", "construir_item", "vista_previa",
           "faltantes_para_publicar"]
