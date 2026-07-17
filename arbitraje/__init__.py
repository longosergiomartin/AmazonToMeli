"""
Arbitraje Amazon (EEUU) -> MercadoLibre Argentina.

Paquete que detecta oportunidades de arbitraje / dropshipping: productos que
se pueden traer desde Amazon a Argentina y revender en MercadoLibre con margen,
considerando TODOS los costos (importación + comisiones e impuestos de venta).

Módulos principales:
  - config       : parámetros ajustables (tipo de cambio, alícuotas, comisiones)
  - models       : estructuras de datos (Producto, Costo, Venta, Oportunidad)
  - importacion  : costo puesto en Argentina (régimen courier o general)
  - meli         : cliente de la API de MercadoLibre + costos de venta
  - amazon       : proveedores de datos de Amazon (manual / API paga)
  - evaluador    : orquesta todo y rankea oportunidades por margen
"""

__version__ = "0.1.0"
