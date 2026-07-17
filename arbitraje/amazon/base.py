"""Interfaz común para las fuentes de datos de Amazon."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from ..models import Producto


class AmazonProvider(ABC):
    """Fuente de productos de Amazon. Toda implementación devuelve `Producto`."""

    @abstractmethod
    def cargar(self) -> List[Producto]:
        """Devuelve la lista de productos a evaluar."""
        raise NotImplementedError

    def buscar(self, query: str) -> List[Producto]:
        """Busca productos por término (opcional; las fuentes que no soportan
        búsqueda automática pueden dejar el default que devuelve todo lo cargado
        filtrando por nombre)."""
        q = query.lower()
        return [p for p in self.cargar() if q in p.nombre.lower()]
