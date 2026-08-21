"""
Agente: completa y publica los productos del catálogo sin intervención.

Trabaja de a un producto por vez, siempre en el mismo orden de pasos, y deja
registrado qué hizo y por qué. Ese "de a uno" es a propósito: permite frenarlo
en cualquier momento, ver el avance en vivo y que un producto trabado no arrastre
al resto.

Los pasos, para cada producto:

  1. **Preparar**: marca, título, categoría, atributos administrativos, fotos.
  2. **Código de barras**: sin GTIN, MercadoLibre rechaza varias categorías.
  3. **Publicar**: solo si no falta nada y el margen alcanza.

Guardas, porque publica con plata de por medio:

  - Publicar está **apagado por defecto**. Encendido, hay un tope de
    publicaciones por corrida.
  - No publica con margen por debajo del mínimo configurado.
  - Un producto que falla queda marcado con el motivo y **no se reintenta en
    la misma corrida**, así el agente no se queda en loop contra el mismo error.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

# Qué le falta a un producto para poder publicarse.
PREPARAR = "preparar"
CODIGO = "codigo"
PUBLICAR = "publicar"
NADA = "nada"


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Agente:
    """Orquesta los pasos sobre el catálogo. La lógica de cada paso vive en el
    servicio: acá solo se decide **qué producto toca y qué paso le falta**."""

    def __init__(self, cat, preparar: Callable, buscar_codigo: Callable,
                 publicar: Callable, faltantes: Callable):
        self.cat = cat
        self._preparar = preparar
        self._buscar_codigo = buscar_codigo
        self._publicar = publicar
        self._faltantes = faltantes
        # Productos que fallaron en esta corrida, con su motivo. Se limpian al
        # apagar y encender el agente.
        self.trabados: dict[int, str] = {}
        # Productos a los que ya se les buscó el código sin éxito: no se vuelve
        # a buscar, pero se los deja intentar publicar por si están en el
        # catálogo de MercadoLibre (esa vía no necesita código).
        self.sin_codigo: set[int] = set()

    # ---- configuración (persistida en preferencias) ----------------------

    @property
    def config(self) -> dict:
        return {
            "encendido": self.cat._pref("agente_encendido", "0") == "1",
            "publicar": self.cat._pref("agente_publicar", "0") == "1",
            "max_publicaciones": int(self.cat._pref("agente_max_publicaciones", "5")),
            "margen_minimo": float(self.cat._pref("agente_margen_minimo", "20")),
        }

    @config.setter
    def config(self, valores: dict) -> None:
        if "encendido" in valores:
            self.cat._set_pref("agente_encendido", "1" if valores["encendido"] else "0")
            if valores["encendido"]:
                self.trabados.clear()   # arrancar de nuevo reintenta todo
                self.sin_codigo.clear()
        if "publicar" in valores:
            self.cat._set_pref("agente_publicar", "1" if valores["publicar"] else "0")
        if "max_publicaciones" in valores:
            n = max(0, min(int(valores["max_publicaciones"]), 50))
            self.cat._set_pref("agente_max_publicaciones", str(n))
        if "margen_minimo" in valores:
            self.cat._set_pref("agente_margen_minimo",
                               str(max(0.0, float(valores["margen_minimo"]))))

    # ---- qué le falta a cada producto ------------------------------------

    def paso_pendiente(self, p) -> str:
        """El próximo paso que necesita este producto, o NADA si está listo."""
        if p.estado in ("publicado", "pausado"):
            return NADA
        if not p.ml_category_id or not p.marca or not p.pictures:
            return PREPARAR
        # El código se busca una sola vez. Si no aparece **igual se intenta
        # publicar**: cuando el producto está en el catálogo de MercadoLibre, se
        # publica contra su ficha y ML pone el código de la suya, así que exigirlo
        # antes de intentar era bloquearse solo.
        if not (p.ml_attributes or {}).get("GTIN") and p.id not in self.sin_codigo:
            return CODIGO
        if self._faltantes(p):
            return PREPARAR
        return PUBLICAR

    def estado(self) -> dict:
        """Resumen para mostrar en el panel."""
        conteo = {PREPARAR: 0, CODIGO: 0, PUBLICAR: 0, NADA: 0}
        for p in self.cat.todos():
            conteo[self.paso_pendiente(p)] += 1
        publicados = sum(1 for p in self.cat.todos() if p.estado == "publicado")
        sin_codigo_a_intentar = len(self.sin_codigo)
        return {**self.config,
                "por_preparar": conteo[PREPARAR],
                "sin_codigo": conteo[CODIGO],
                "listos_para_publicar": conteo[PUBLICAR],
                "publicados": publicados,
                "trabados": len(self.trabados),
                "sin_codigo_a_intentar": sin_codigo_a_intentar,
                "detalle_trabados": [
                    {"id": pid, "motivo": m} for pid, m in list(self.trabados.items())[:10]],
                "pendientes": conteo[PREPARAR] + conteo[CODIGO] + conteo[PUBLICAR]}

    # ---- una unidad de trabajo -------------------------------------------

    def _siguiente(self):
        """El próximo producto con trabajo pendiente que no esté trabado."""
        for p in self.cat.todos():
            if p.id in self.trabados:
                continue
            if self.paso_pendiente(p) != NADA:
                return p
        return None

    def tick(self) -> dict:
        """Avanza **un paso sobre un producto**. Devuelve qué hizo."""
        cfg = self.config
        if not cfg["encendido"]:
            return {"accion": "apagado", **self.estado()}

        p = self._siguiente()
        if p is None:
            return {"accion": "sin_trabajo", **self.estado()}

        paso = self.paso_pendiente(p)
        nombre = p.titulo_ml or p.modelo or p.asin

        try:
            if paso == PREPARAR:
                self._preparar(p)
                return {"accion": PREPARAR, "id": p.id, "nombre": nombre,
                        "detalle": "datos completados", **self.estado()}

            if paso == CODIGO:
                r = self._buscar_codigo(p)
                if r.get("gtin"):
                    return {"accion": CODIGO, "id": p.id, "nombre": nombre,
                            "detalle": f"código {r['gtin']} ({r.get('fuente','')})",
                            **self.estado()}
                # Sin código no se abandona: se intenta publicar igual, porque
                # la vía del catálogo de MercadoLibre no lo necesita.
                self.sin_codigo.add(p.id)
                return {"accion": "sin_codigo", "id": p.id, "nombre": nombre,
                        "detalle": "no encontré el código; pruebo publicar "
                                   "contra el catálogo de MercadoLibre",
                        "bloqueado": bool(r.get("bloqueado")), **self.estado()}

            # PUBLICAR
            if not cfg["publicar"]:
                self.trabados[p.id] = "listo, esperando que habilites publicar"
                return {"accion": "listo", "id": p.id, "nombre": nombre,
                        "detalle": "completo; falta habilitar la publicación",
                        **self.estado()}
            if cfg["max_publicaciones"] <= 0:
                self.trabados[p.id] = "se alcanzó el tope de publicaciones"
                return {"accion": "tope", "id": p.id, "nombre": nombre,
                        "detalle": "tope de publicaciones alcanzado", **self.estado()}
            if p.margen_pct < cfg["margen_minimo"]:
                motivo = (f"margen {p.margen_pct:.0f}% por debajo del mínimo "
                          f"({cfg['margen_minimo']:.0f}%)")
                self.trabados[p.id] = motivo
                return {"accion": "trabado", "id": p.id, "nombre": nombre,
                        "detalle": motivo, **self.estado()}

            item = self._publicar(p)
            self.config = {"max_publicaciones": cfg["max_publicaciones"] - 1}
            return {"accion": PUBLICAR, "id": p.id, "nombre": nombre,
                    "detalle": f"publicado como {item.get('ml_item_id','')}",
                    **self.estado()}

        except Exception as e:  # noqa: BLE001 - un producto no frena al agente
            motivo = str(e)[:200]
            self.trabados[p.id] = motivo
            return {"accion": "error", "id": p.id, "nombre": nombre,
                    "detalle": motivo, **self.estado()}
