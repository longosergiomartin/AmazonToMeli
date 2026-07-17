"""
Proveedor manual: cargás los productos a mano (lista) o desde un CSV.

Es la fuente por defecto del MVP: gratis y sin depender de terceros. Vos ponés
el precio y el peso que ves en Amazon, y la app hace todas las cuentas.

Formato del CSV (ver data/productos.example.csv):
  nombre,query_meli,precio_amazon_usd,peso_kg,categoria,arancel_pct,precio_meli_manual,link_amazon
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import List, Optional

from ..models import Producto
from .base import AmazonProvider


def _num(valor: str, default: float) -> float:
    valor = (valor or "").strip()
    if valor == "":
        return default
    return float(valor)


def _opt_num(valor: str) -> Optional[float]:
    valor = (valor or "").strip()
    return float(valor) if valor else None


class ManualProvider(AmazonProvider):
    def __init__(self, productos: Optional[List[Producto]] = None):
        self._productos = list(productos) if productos else []

    def cargar(self) -> List[Producto]:
        return list(self._productos)

    def agregar(self, producto: Producto) -> None:
        self._productos.append(producto)

    @classmethod
    def desde_csv(cls, ruta: str | Path) -> "ManualProvider":
        productos: List[Producto] = []
        with open(ruta, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for fila in reader:
                if not fila.get("nombre", "").strip():
                    continue  # saltar filas vacías
                productos.append(Producto(
                    nombre=fila["nombre"].strip(),
                    query_meli=fila.get("query_meli", fila["nombre"]).strip(),
                    precio_amazon_usd=_num(fila.get("precio_amazon_usd", ""), 0.0),
                    peso_kg=_num(fila.get("peso_kg", ""), 0.5),
                    categoria=fila.get("categoria", "default").strip() or "default",
                    arancel_pct=_num(fila.get("arancel_pct", ""), 0.16),
                    precio_meli_manual=_opt_num(fila.get("precio_meli_manual", "")),
                    link_amazon=(fila.get("link_amazon", "").strip() or None),
                ))
        return cls(productos)
