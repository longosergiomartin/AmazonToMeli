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
    """Costos de MercadoLibre como % del precio de venta: incluyen la comisión
    por vender Y el cargo por ofrecer envío gratis. Verificado contra el
    simulador de la Central de vendedores (~16%). AJUSTAR por categoría."""
    return {
        "electronica": 0.17,
        "computacion": 0.16,
        "hogar":       0.15,
        "default":     0.16,
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
    # % del precio que se lleva MercadoLibre (comisión + envío gratis).
    costos_ml: Dict[str, float] = field(default_factory=_costos_ml_default)
    # Condición fiscal del vendedor: define las alícuotas de abajo.
    condicion_fiscal: str = "monotributo"
    # Impuestos argentinos sobre la venta (% del precio de venta):
    iva_pct: float = 0.0         # 0 en Monotributo; 21% si sos RI
    ganancias_pct: float = 0.0   # 0 en Monotributo; retención si sos RI
    iibb_pct: float = 0.03       # según jurisdicción — VERIFICAR si te retienen
    site: str = "MLA"            # MLA = Argentina

    def costos_ml_pct(self, categoria: str = "default") -> float:
        return self.costos_ml.get(categoria, self.costos_ml["default"])

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
    # publicado del producto. Estimación verificada contra checkouts reales
    # (~26% cuando el envío entra en la promo de envío gratis). Se usa para
    # precargar el costo de envío; podés pisarlo con el Total real del checkout.
    envio_import_pct: float = 0.26
    umbral_margen_bueno_pct: float = 30.0  # a partir de acá lo marcamos como oportunidad
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
                        "umbral_margen_bueno_pct", "envio_import_pct"}}
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
