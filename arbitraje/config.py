"""
Configuración central del proyecto.

Todos los valores marcados con "VERIFICAR" cambian con el tiempo (cotización,
alícuotas, comisiones de MeLi, reglas de aduana) y conviene chequearlos antes
de tomar una decisión con plata real.

La config se define con dataclasses (valores por defecto sensatos) y se puede
sobreescribir desde un archivo JSON con `Config.desde_json("mi_config.json")`,
así no tenés que tocar el código para ajustar tus números.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path
from typing import Dict


# =======================================================================
# COSTOS DE IMPORTACIÓN
# =======================================================================

@dataclass
class CourierConfig:
    """Régimen simplificado 'Puerta a Puerta' / courier.

    Es el camino realista para traer productos sueltos desde Amazon. Reglas
    (VERIFICAR, cambian seguido): tope por envío, franquicia anual exenta y un
    impuesto único sobre el excedente que reemplaza aranceles y percepciones.
    """
    tope_por_envio_usd: float = 3000.0     # máximo permitido por envío — VERIFICAR
    franquicia_anual_usd: float = 400.0    # exento por año calendario — VERIFICAR
    tasa_impuesto: float = 0.50            # 50% sobre el excedente de la franquicia
    flete_usd_por_kg: float = 55.0         # tarifa courier puerta a puerta por kg — AJUSTAR
    flete_minimo_usd: float = 0.0          # algunos couriers cobran un mínimo por envío


@dataclass
class GeneralConfig:
    """Régimen general / importador registrado (aranceles NCM, despachante...).

    Sirve para volumen. Encarece mucho un producto suelto, por eso el default
    del proyecto es courier, pero se puede comparar contra este.
    """
    tasa_estadistica_pct: float = 0.03     # sobre CIF (histórico; VERIFICAR tope)
    iva_pct: float = 0.21
    percepcion_iva_pct: float = 0.10       # 10% si sos Responsable Inscripto — VERIFICAR
    percepcion_ganancias_pct: float = 0.06 # 6% si sos RI — VERIFICAR
    percepcion_iibb_pct: float = 0.025     # según jurisdicción — AJUSTAR
    despachante_pct: float = 0.02          # honorarios sobre CIF — AJUSTAR
    despachante_minimo_usd: float = 80.0
    flete_seguro_pct: float = 0.15         # flete internacional + seguro sobre FOB — AJUSTAR
    gastos_portuarios_usd: float = 60.0


# =======================================================================
# COSTOS DE VENTA EN MERCADOLIBRE
# =======================================================================

def _costos_ml_default() -> Dict[str, float]:
    """Comisión de MercadoLibre como % del precio de venta.

    Es SOLO la comisión por vender: el envío gratis va aparte, en
    `envio_gratis_ars`, porque es un monto fijo en pesos y no un porcentaje.
    Meterlo acá adentro sale caro en los productos baratos: un envío de $9.860
    es el 4,8% de una publicación de $206.000 pero el 1,4% de una de $710.000,
    así que ningún porcentaje único lo representa.
    """
    return {
        "electronica": 0.17,
        "computacion": 0.16,
        "hogar":       0.15,
        "lego":        0.145,
        "default":     0.145,
    }


# Alícuotas de impuestos sobre la venta según la condición fiscal del vendedor.
# VERIFICAR con tu contador: dependen de tu jurisdicción y de si estás inscripto
# en los regímenes de retención.
PRESETS_FISCALES: Dict[str, Dict[str, float]] = {
    # Monotributo: no discrimina IVA en la venta y no sufre retención de
    # Ganancias. IIBB puede retenerse igual según la provincia/CABA.
    # Contrapartida: el IVA que pagás al importar NO se recupera (es costo).
    "monotributo": {"iva_pct": 0.0, "ganancias_pct": 0.0, "iibb_pct": 0.03},
    # Responsable Inscripto: IVA débito de la venta (compensable con el crédito
    # fiscal de la importación) + retenciones de Ganancias e IIBB.
    "responsable_inscripto": {"iva_pct": 0.21, "ganancias_pct": 0.06, "iibb_pct": 0.03},
}
CONDICIONES_FISCALES = tuple(PRESETS_FISCALES)


@dataclass
class MeliConfig:
    # % del precio que se lleva MercadoLibre de comisión (sin el envío).
    costos_ml: Dict[str, float] = field(default_factory=_costos_ml_default)
    # Lo que PAGÁS de envío por ofrecer "envío gratis", en pesos y ya neto del
    # descuento por reputación. Es fijo: no depende del precio de venta, sí del
    # peso del producto y de tu reputación como vendedor. VERIFICAR en la
    # publicación ("Ofrecés envío gratis · Pagás $X").
    envio_gratis_ars: float = 9860.0
    # Condición fiscal del vendedor: define las alícuotas de abajo.
    condicion_fiscal: str = "monotributo"
    # Impuestos argentinos sobre la venta (% del precio de venta):
    iva_pct: float = 0.0         # 0 en Monotributo; 21% si sos RI
    ganancias_pct: float = 0.0   # 0 en Monotributo; retención si sos RI
    iibb_pct: float = 0.03       # según jurisdicción — VERIFICAR si te retienen
    # Percepción de IVA que ARCA le aplica al monotributista cuando el precio de
    # una venta supera el tope. Es un escalón, no una alícuota continua: por
    # debajo del tope no se paga nada.
    percepcion_iva_pct: float = 0.07
    percepcion_iva_desde_ars: float = 716840.77
    site: str = "MLA"            # MLA = Argentina

    def costos_ml_pct(self, categoria: str = "default") -> float:
        return self.costos_ml.get(categoria, self.costos_ml["default"])

    def percepcion_iva_ars(self, precio_venta_ars: float) -> float:
        """La percepción sobre este precio. Cero por debajo del tope."""
        if precio_venta_ars > self.percepcion_iva_desde_ars:
            return precio_venta_ars * self.percepcion_iva_pct
        return 0.0

    def con_condicion_fiscal(self, condicion: str) -> "MeliConfig":
        """Copia con las alícuotas del preset de esa condición fiscal."""
        if condicion not in PRESETS_FISCALES:
            raise ValueError(f"Condición fiscal desconocida: {condicion!r}")
        return replace(self, condicion_fiscal=condicion, **PRESETS_FISCALES[condicion])


# =======================================================================
# CONFIG RAÍZ
# =======================================================================

@dataclass
class Config:
    tipo_cambio_oficial: float = 1300.0    # ARS por USD — VERIFICAR cotización del día
    # Recargo del "dólar tarjeta": cuando comprás en Amazon con una tarjeta
    # argentina no pagás el dólar oficial, sino oficial + percepciones. Este
    # recargo se suma al TC solo para la COMPRA (no para la venta en MeLi, que
    # es en pesos). Es a cuenta de impuestos (recuperable) pero sale de tu
    # bolsillo al momento de comprar. VERIFICAR alícuota vigente.
    recargo_tarjeta_pct: float = 0.30
    # Envío internacional + cargos de importación de Amazon, como % del precio
    # publicado del producto. Son DOS números distintos porque Amazon cobra dos
    # cosas distintas, y confundirlos es vender por debajo del costo:
    #   - `envio_import_pct` (~26%): el producto entra en la promoción de envío
    #     internacional gratis, así que solo se pagan los cargos de importación.
    #   - `envio_import_sin_gratis_pct` (~70%): el producto NO entra, y encima
    #     del producto se paga el flete a Argentina. Es el caso más común.
    # Cuál se aplica lo decide `envio_gratis_amazon` en cada producto. Ambos son
    # estimaciones: se pisan con el Total real del checkout.
    envio_import_pct: float = 0.26
    envio_import_sin_gratis_pct: float = 0.70
    umbral_margen_bueno_pct: float = 30.0  # a partir de acá lo marcamos como oportunidad
    # Piso de margen para no cruzar el tope de la percepción de IVA. Si el
    # precio que da el margen deseado se pasa del tope, conviene publicar justo
    # por debajo y resignar unos puntos antes que saltar ~10% de precio y perder
    # competitividad. Si ni así llega a este piso, se acepta el salto.
    margen_piso_pct: float = 0.25
    # Piso de ganancia en PESOS por venta. Los imprevistos de importar —que el
    # precio suba entre publicar y vender, que se agote y haya que conseguirlo
    # más caro, un reclamo— cuestan un monto fijo, no un porcentaje: un 30%
    # sobre un set barato no banca ninguno. 0 = sin piso.
    ganancia_minima_ars: float = 0.0
    courier: CourierConfig = field(default_factory=CourierConfig)
    general: GeneralConfig = field(default_factory=GeneralConfig)
    meli: MeliConfig = field(default_factory=MeliConfig)

    def tc_compra(self) -> float:
        """Tipo de cambio efectivo al que comprás en el exterior con tarjeta."""
        return self.tipo_cambio_oficial * (1 + self.recargo_tarjeta_pct)

    # ---- persistencia ----------------------------------------------------
    def a_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def desde_dict(cls, data: dict) -> "Config":
        base = cls()
        courier = replace(base.courier, **data.get("courier", {}))
        general = replace(base.general, **data.get("general", {}))

        meli_data = dict(data.get("meli", {}))
        costos = meli_data.pop("costos_ml", None)
        meli = replace(base.meli, **meli_data)
        if costos is not None:
            meli.costos_ml = dict(costos)

        top = {k: v for k, v in data.items()
               if k in {"tipo_cambio_oficial", "recargo_tarjeta_pct",
                        "umbral_margen_bueno_pct", "envio_import_pct",
                        "envio_import_sin_gratis_pct",
                        "margen_piso_pct", "ganancia_minima_ars"}}
        return replace(base, courier=courier, general=general, meli=meli, **top)

    @classmethod
    def desde_json(cls, ruta: str | Path) -> "Config":
        with open(ruta, "r", encoding="utf-8") as f:
            return cls.desde_dict(json.load(f))

    def guardar_json(self, ruta: str | Path) -> None:
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(self.a_dict(), f, ensure_ascii=False, indent=2)


# Instancia lista para usar con los defaults.
CONFIG_DEFAULT = Config()
