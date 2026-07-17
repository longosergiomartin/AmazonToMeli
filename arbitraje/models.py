"""
Estructuras de datos del proyecto.

Se usan dataclasses simples para que los resultados sean fáciles de inspeccionar,
serializar (a CSV/JSON) y testear.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, Optional


@dataclass
class Producto:
    """Un candidato a evaluar: lo que sabemos del lado de Amazon + cómo buscarlo
    en MercadoLibre."""
    nombre: str
    query_meli: str                 # término de búsqueda en MeLi
    precio_amazon_usd: float        # precio del producto en Amazon (sin envío)
    peso_kg: float = 0.5            # peso para estimar flete courier
    categoria: str = "default"      # categoría de comisión en MeLi
    arancel_pct: float = 0.16       # NCM específico (solo aplica en régimen general)
    precio_meli_manual: Optional[float] = None  # si querés fijar el precio de venta a mano
    link_amazon: Optional[str] = None

    def a_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResultadoImportacion:
    """Costo puesto en Argentina de importar el producto."""
    regimen: str                    # "courier" | "general"
    total_usd: float
    total_ars: float
    detalle_usd: Dict[str, float] = field(default_factory=dict)


@dataclass
class ResultadoVentaMeli:
    """Cuánto queda neto de una venta en MercadoLibre (en ARS)."""
    precio_venta_ars: float
    neto_ars: float
    detalle_ars: Dict[str, float] = field(default_factory=dict)


@dataclass
class ResultadoMeliBusqueda:
    """Un resultado de búsqueda en la API de MercadoLibre."""
    titulo: str
    precio: float
    link: str


@dataclass
class Oportunidad:
    """Resultado final de evaluar un producto bajo un régimen dado."""
    producto: Producto
    regimen: str
    costo: ResultadoImportacion
    venta: ResultadoVentaMeli
    precio_venta_ars: float
    margen_ars: float
    margen_pct: float               # margen sobre el costo puesto en Argentina
    resultados_meli: list = field(default_factory=list)

    @property
    def es_oportunidad(self) -> bool:
        return self.margen_ars > 0

    def veredicto(self, umbral_bueno_pct: float = 30.0) -> str:
        if self.margen_pct >= umbral_bueno_pct:
            return "OPORTUNIDAD"
        if self.margen_pct >= 0:
            return "AJUSTADO"
        return "NO CONVIENE"

    def fila_resumen(self) -> dict:
        """Fila plana para exportar a CSV / mostrar en tabla."""
        return {
            "producto": self.producto.nombre,
            "categoria": self.producto.categoria,
            "regimen": self.regimen,
            "precio_amazon_usd": round(self.producto.precio_amazon_usd, 2),
            "costo_puesto_ars": round(self.costo.total_ars, 2),
            "precio_venta_meli_ars": round(self.precio_venta_ars, 2),
            "neto_venta_ars": round(self.venta.neto_ars, 2),
            "margen_ars": round(self.margen_ars, 2),
            "margen_pct": round(self.margen_pct, 1),
            "veredicto": self.veredicto(),
        }
