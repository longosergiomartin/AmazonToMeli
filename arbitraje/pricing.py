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


def _k(cfg: Config, categoria: str, con_percepcion: bool) -> float:
    """Fracción del precio que sobrevive a los descuentos proporcionales."""
    m = cfg.meli
    k = 1 - m.costos_ml_pct(categoria) - m.iva_pct - m.ganancias_pct - m.iibb_pct
    if con_percepcion:
        k -= m.percepcion_iva_pct
    if k <= 0:
        raise ValueError("Los costos e impuestos superan el 100% del precio; "
                         "no hay precio que deje margen con esta configuración.")
    return k


def precio_sugerido(costo_ars: float, margen_deseado: float,
                    categoria: str = "default",
                    cfg: Config = CONFIG_DEFAULT) -> float:
    """Precio (ARS) al que hay que publicar para obtener `margen_deseado` sobre
    el costo.

        neto(P) = P·(1 − comisión − iva − ganancias − iibb − percepción) − envío
        queremos neto(P) = costo·(1 + margen_deseado)
        =>  P = (costo·(1 + margen) + envío) / k

    El envío va sumado al objetivo porque es un monto fijo en pesos: no escala
    con el precio, así que no puede entrar en `k`.

    Además del porcentaje hay un **piso en pesos** (`ganancia_minima_ars`). Los
    imprevistos de importar —que el precio suba entre que se publica y que se
    vende, que se agote y haya que conseguirlo más caro, un reclamo, una
    devolución— cuestan un monto fijo, no un porcentaje del producto. Un 30%
    sobre un set barato no alcanza para bancar ninguno de esos. Se toma el
    objetivo más exigente de los dos.

    La percepción de IVA es un escalón: recién se paga por encima del tope de
    ARCA. Eso parte la ecuación en dos y abre un hueco —el precio que da el
    margen deseado puede caer justo arriba del tope, donde pagar la percepción
    obliga a subir otro ~9%—. Cuando pasa eso conviene publicar pegado abajo del
    tope y resignar unos puntos de margen, que es lo que hace esta función
    mientras el margen no baje de `cfg.margen_piso_pct` **ni del piso en pesos**:
    ese piso existe justamente para no quedar sin colchón.
    """
    m = cfg.meli
    envio = m.envio_gratis_ars
    piso = max(0.0, float(cfg.ganancia_minima_ars or 0.0))
    # El objetivo es lo que tiene que quedar limpio: el mayor entre el margen
    # porcentual y el piso en pesos.
    objetivo_neto = costo_ars + max(costo_ars * margen_deseado, piso)
    tope = m.percepcion_iva_desde_ars

    p = (objetivo_neto + envio) / _k(cfg, categoria, con_percepcion=False)
    if p <= tope or not m.percepcion_iva_pct:
        return round(p, 2)

    # Se pasó del tope: o se publica justo debajo (sin percepción, con menos
    # margen) o se salta al precio que sí banca la percepción.
    pegado = round(tope - 0.01, 2)
    if costo_ars > 0:
        neto_pegado = margen_real_al_precio(costo_ars, pegado, categoria, cfg)
        if (neto_pegado["margen_pct"] >= cfg.margen_piso_pct * 100
                and neto_pegado["margen_ars"] >= piso):
            return pegado
    return round((objetivo_neto + envio) / _k(cfg, categoria, con_percepcion=True), 2)


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
