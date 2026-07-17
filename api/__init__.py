"""
API local de datos de productos — tu propio "Rainforest" personal.

Servicio FastAPI self-hosted que expone datos de productos de Amazon y precios
de MercadoLibre con la misma filosofía que Rainforest API (endpoints REST que
devuelven JSON limpio), pero con fuentes de datos legítimas y gratuitas:

  - Captura asistida por bookmarklet: navegás Amazon/MeLi como siempre y con un
    clic el producto que estás viendo se guarda en tu base local.
  - Histórico de precios propio en SQLite (cada captura queda fechada).
  - Export a CSV compatible con el CLI de arbitraje.
  - Proveedor opcional Canopy (100 requests gratis/mes) para refrescar por ASIN.

Correr:  python -m api.server   (abre http://localhost:8321)
"""

__version__ = "0.1.0"
