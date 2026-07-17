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

@dataclass
class ComisionCategoria:
    comision_pct: float
    costo_fijo: float


def _comisiones_default() -> Dict[str, ComisionCategoria]:
    # Tarifas Premium publicadas por MeLi — VERIFICAR en tu cuenta de vendedor,
    # cambian cada pocos meses.
    return {
        "electronica": ComisionCategoria(0.1714, 2810),
        "computacion": ComisionCategoria(0.1500, 2810),
        "hogar":       ComisionCategoria(0.1400, 2300),
        "default":     ComisionCategoria(0.1500, 2300),
    }


@dataclass
class MeliConfig:
    comisiones: Dict[str, ComisionCategoria] = field(default_factory=_comisiones_default)
    iva_sobre_comision: float = 0.21
    iibb_pct: float = 0.03                 # sobre precio de venta — AJUSTAR por jurisdicción
    ganancias_pct: float = 0.06            # retención si sos RI — AJUSTAR
    costo_envio_estimado_ars: float = 6000 # si ofrecés envío gratis (Premium)
    umbral_costo_fijo_ars: float = 33000   # debajo de este precio se cobra costo fijo
    # Las retenciones de IIBB/Ganancias son a cuenta de impuestos anuales
    # (recuperables), no un costo puro. Se restan igual para ver el margen de caja.
    site: str = "MLA"                      # MLA = Argentina


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
        comisiones = None
        if "comisiones" in meli_data:
            comisiones = {
                k: ComisionCategoria(**v) if isinstance(v, dict) else v
                for k, v in meli_data.pop("comisiones").items()
            }
        meli = replace(base.meli, **meli_data)
        if comisiones is not None:
            meli.comisiones = comisiones

        top = {k: v for k, v in data.items()
               if k in {"tipo_cambio_oficial", "recargo_tarjeta_pct",
                        "umbral_margen_bueno_pct"}}
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
