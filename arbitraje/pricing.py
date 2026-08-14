"""
Precio de venta sugerido en MercadoLibre a partir de un margen deseado.

`calcular_neto_venta_meli` va en un sentido (precio → neto). Acá hacemos el
inverso: dado el costo puesto en Argentina y el margen que querés ganar sobre
ese costo, calculamos a qué precio tenés que publicar para lograrlo, despejando
comisión + IVA sobre comisión + IIBB + Ganancias + costo fijo + envío.

Definición de margen: `margen_deseado` es la fracción sobre el costo puesto.
Ej: 0.35 = querés que te queden $35 netos por cada $100 de costo.
"""

from __future__ import annotations

from .config import Config, CONFIG_DEFAULT
from .meli import calcular_neto_venta_meli


def precio_sugerido(costo_ars: float, margen_deseado: float,
                    categoria: str = "default",
                    cfg: Config = CONFIG_DEFAULT) -> float:
    """Precio (ARS) al que hay que publicar para obtener `margen_deseado` sobre
    el costo. Resuelve la ecuación lineal del neto de venta.

    neto(P) = P·(1 − comPct·(1+iva) − iibb − gan) − fijo·(1+iva) − envio
    Queremos neto(P) = costo·(1 + margen_deseado).
    """
    m = cfg.meli
    objetivo_neto = costo_ars * (1 + margen_deseado)

    # Todos los descuentos son proporcionales al precio:
    #   neto = P · (1 − costos_ml − iva − ganancias − iibb)
    k = 1 - m.costos_ml_pct(categoria) - m.iva_pct - m.ganancias_pct - m.iibb_pct
    if k <= 0:
        raise ValueError("Los costos e impuestos superan el 100% del precio; "
                         "no hay precio que deje margen con esta configuración.")
    return round(objetivo_neto / k, 2)


def margen_real_al_precio(costo_ars: float, precio_ars: float,
                          categoria: str = "default",
                          cfg: Config = CONFIG_DEFAULT) -> dict:
    """Devuelve el margen real (ARS y %) que deja un precio dado, reutilizando
    el cálculo de neto de venta. Sirve para verificar el precio sugerido y para
    detectar márgenes insuficientes."""
    venta = calcular_neto_venta_meli(precio_ars, categoria, cfg)
    margen = venta.neto_ars - costo_ars
    pct = (margen / costo_ars * 100) if costo_ars else 0.0
    return {
        "neto_ars": round(venta.neto_ars, 2),
        "margen_ars": round(margen, 2),
        "margen_pct": round(pct, 1),
    }
