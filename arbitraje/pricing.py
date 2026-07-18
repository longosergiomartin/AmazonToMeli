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
    com = m.comisiones.get(categoria, m.comisiones["default"])
    objetivo_neto = costo_ars * (1 + margen_deseado)

    # Pendiente del neto respecto del precio (parte proporcional).
    k = 1 - com.comision_pct * (1 + m.iva_sobre_comision) - m.iibb_pct - m.ganancias_pct
    if k <= 0:
        raise ValueError("Las comisiones/impuestos superan el 100% del precio; "
                         "no hay precio que deje margen con esta configuración.")

    # Caso A: precio por encima del umbral → sin costo fijo.
    p_sin_fijo = (objetivo_neto + m.costo_envio_estimado_ars) / k
    if p_sin_fijo >= m.umbral_costo_fijo_ars:
        return round(p_sin_fijo, 2)

    # Caso B: precio por debajo del umbral → se agrega el costo fijo.
    costo_fijo_total = com.costo_fijo * (1 + m.iva_sobre_comision)
    p_con_fijo = (objetivo_neto + m.costo_envio_estimado_ars + costo_fijo_total) / k
    return round(p_con_fijo, 2)


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
