"""
Proveedores de datos de Amazon.

Amazon no tiene una API pública y gratuita de búsqueda de productos, así que
abstraemos la fuente detrás de una interfaz común (`AmazonProvider`). Hoy:

  - ManualProvider    : cargás los productos a mano o desde un CSV. Gratis, sin
                        fricción legal. Ideal para validar la idea.
  - RainforestProvider: stub para una API paga (Rainforest/Keepa) que devuelve
                        precios automáticamente. Se activa con una API key.

Cambiar de fuente no toca el resto del proyecto: todos devuelven `Producto`.
"""

from .base import AmazonProvider
from .manual import ManualProvider
from .rainforest import RainforestProvider

__all__ = ["AmazonProvider", "ManualProvider", "RainforestProvider"]
