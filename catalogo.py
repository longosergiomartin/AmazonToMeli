"""
Catálogo de productos: administración de lo identificado en Amazon para
publicar en MercadoLibre, con costo en pesos, precio sugerido, estado de
publicación e historial de cambios.

Responsabilidades:
  - Registrar manualmente un producto de Amazon (link, ASIN, marca, modelo,
    precio USD, peso, costo de envío, disponibilidad).
  - Calcular el costo total en pesos (tipo de cambio + dólar tarjeta + envío +
    importación) reutilizando el motor `arbitraje`.
  - Calcular el precio de venta sugerido a partir del margen deseado.
  - Detectar márgenes insuficientes.
  - Manejar el ciclo de vida de la publicación: borrador → (aprobación) →
    publicado → pausado, guardando historial de cada cambio.

Nada se publica en MercadoLibre automáticamente: `publicar()` es un paso
explícito que dispara el usuario tras aprobar la vista previa.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from arbitraje.config import Config, CONFIG_DEFAULT
from arbitraje.importacion import calcular_costo
from arbitraje.models import Producto as ProductoArbitraje
from arbitraje.pricing import precio_sugerido, margen_real_al_precio

ESTADOS = ("borrador", "aprobado", "publicado", "pausado")


@dataclass
class ProductoCatalogo:
    # --- datos de Amazon (carga manual) ---
    amazon_link: str = ""
    asin: str = ""
    marca: str = ""
    modelo: str = ""
    precio_usd: float = 0.0
    peso_kg: float = 0.5
    costo_envio_usd: float = 0.0
    disponibilidad: str = "in_stock"   # in_stock | out_of_stock
    # --- parámetros de importación / venta ---
    regimen: str = "courier"           # courier | general
    arancel_pct: float = 0.16
    categoria: str = "default"         # categoría de comisión de MeLi
    margen_deseado: float = 0.35       # fracción sobre el costo
    stock: int = 1
    # --- MercadoLibre ---
    titulo_ml: str = ""
    ml_category_id: str = ""
    ml_attributes: dict = field(default_factory=dict)
    pictures: list = field(default_factory=list)
    # --- calculados / estado (los llena el servicio) ---
    id: Optional[int] = None
    costo_total_ars: float = 0.0
    precio_sugerido_ars: float = 0.0
    precio_publicado_ars: Optional[float] = None
    margen_pct: float = 0.0
    estado: str = "borrador"
    ml_item_id: str = ""
    ml_permalink: str = ""


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Catalogo:
    """Almacenamiento + lógica de negocio del catálogo."""

    def __init__(self, conn: sqlite3.Connection, cfg: Config = CONFIG_DEFAULT):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.cfg = cfg
        self._crear_tablas()

    def _crear_tablas(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS catalogo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creado TEXT NOT NULL, actualizado TEXT NOT NULL,
                amazon_link TEXT, asin TEXT, marca TEXT, modelo TEXT,
                precio_usd REAL, peso_kg REAL, costo_envio_usd REAL,
                disponibilidad TEXT, regimen TEXT, arancel_pct REAL,
                categoria TEXT, margen_deseado REAL, stock INTEGER,
                titulo_ml TEXT, ml_category_id TEXT, ml_attributes TEXT,
                pictures TEXT,
                costo_total_ars REAL, precio_sugerido_ars REAL,
                precio_publicado_ars REAL, margen_pct REAL,
                estado TEXT, ml_item_id TEXT, ml_permalink TEXT
            );
            CREATE TABLE IF NOT EXISTS catalogo_historial (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                producto_id INTEGER NOT NULL, ts TEXT NOT NULL,
                tipo TEXT NOT NULL, campo TEXT,
                valor_anterior TEXT, valor_nuevo TEXT, nota TEXT
            );
        """)
        # Migración para bases creadas antes de la columna pictures.
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(catalogo)").fetchall()]
        if "pictures" not in cols:
            self.conn.execute("ALTER TABLE catalogo ADD COLUMN pictures TEXT")
        self.conn.commit()

    # ---- cálculo (reutiliza el motor arbitraje) --------------------------

    def _calcular(self, p: ProductoCatalogo) -> None:
        """Completa costo_total_ars, precio_sugerido_ars y margen_pct."""
        base_usd = p.precio_usd + p.costo_envio_usd
        pa = ProductoArbitraje(
            nombre=p.modelo or p.asin or "producto", query_meli=p.modelo or "",
            precio_amazon_usd=base_usd, peso_kg=p.peso_kg, arancel_pct=p.arancel_pct,
        )
        cfg = self.cfg
        regimen = p.regimen
        if regimen == "landed":
            # Amazon ya informó el Total puesto en Argentina (producto + envío +
            # importación): se usa directo, sin estimar aduana ni sumar impuestos.
            pa.precio_landed_usd = base_usd
        elif regimen == "courier":
            # El costo de envío ya lo cargó el usuario: anulamos el flete estimado
            # para no contarlo dos veces.
            cfg = replace(self.cfg, courier=replace(self.cfg.courier, flete_usd_por_kg=0.0))
        costo = calcular_costo(pa, regimen=regimen, cfg=cfg)
        p.costo_total_ars = costo.total_ars
        p.precio_sugerido_ars = precio_sugerido(
            costo.total_ars, p.margen_deseado, p.categoria, self.cfg)
        # margen real al precio que efectivamente se usará (publicado o sugerido)
        precio_ref = p.precio_publicado_ars or p.precio_sugerido_ars
        p.margen_pct = margen_real_al_precio(
            costo.total_ars, precio_ref, p.categoria, self.cfg)["margen_pct"]

    def margen_insuficiente(self, p: ProductoCatalogo) -> bool:
        return p.margen_pct < self.cfg.umbral_margen_bueno_pct

    # ---- persistencia ----------------------------------------------------

    _CAMPOS = ["amazon_link", "asin", "marca", "modelo", "precio_usd", "peso_kg",
               "costo_envio_usd", "disponibilidad", "regimen", "arancel_pct",
               "categoria", "margen_deseado", "stock", "titulo_ml",
               "ml_category_id", "costo_total_ars", "precio_sugerido_ars",
               "precio_publicado_ars", "margen_pct", "estado", "ml_item_id",
               "ml_permalink"]

    def _fila_a_producto(self, row: sqlite3.Row) -> ProductoCatalogo:
        d = dict(row)
        attrs = json.loads(d.pop("ml_attributes") or "{}")
        pics = json.loads(d.pop("pictures") or "[]")
        d.pop("creado", None); d.pop("actualizado", None)
        return ProductoCatalogo(ml_attributes=attrs, pictures=pics,
                                **{k: d[k] for k in d})

    def agregar(self, p: ProductoCatalogo) -> ProductoCatalogo:
        self._calcular(p)
        vals = [getattr(p, c) for c in self._CAMPOS]
        cur = self.conn.execute(
            f"""INSERT INTO catalogo (creado, actualizado, ml_attributes, pictures, {','.join(self._CAMPOS)})
                VALUES (?, ?, ?, ?, {','.join('?' * len(self._CAMPOS))})""",
            [_ahora(), _ahora(), json.dumps(p.ml_attributes),
             json.dumps(p.pictures)] + vals,
        )
        self.conn.commit()
        p.id = cur.lastrowid
        self._log(p.id, "alta", nota=f"Producto {p.modelo or p.asin} registrado")
        return p

    def obtener(self, pid: int) -> Optional[ProductoCatalogo]:
        row = self.conn.execute("SELECT * FROM catalogo WHERE id = ?", (pid,)).fetchone()
        return self._fila_a_producto(row) if row else None

    def todos(self) -> list[ProductoCatalogo]:
        rows = self.conn.execute("SELECT * FROM catalogo ORDER BY creado DESC").fetchall()
        return [self._fila_a_producto(r) for r in rows]

    def _guardar(self, p: ProductoCatalogo) -> None:
        sets = ", ".join(f"{c} = ?" for c in self._CAMPOS)
        vals = [getattr(p, c) for c in self._CAMPOS]
        self.conn.execute(
            f"UPDATE catalogo SET actualizado = ?, ml_attributes = ?, pictures = ?, {sets} WHERE id = ?",
            [_ahora(), json.dumps(p.ml_attributes), json.dumps(p.pictures)] + vals + [p.id],
        )
        self.conn.commit()

    # ---- historial -------------------------------------------------------

    def _log(self, pid: int, tipo: str, campo: str = "", anterior=None,
             nuevo=None, nota: str = "") -> None:
        self.conn.execute(
            """INSERT INTO catalogo_historial
               (producto_id, ts, tipo, campo, valor_anterior, valor_nuevo, nota)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (pid, _ahora(), tipo, campo,
             None if anterior is None else str(anterior),
             None if nuevo is None else str(nuevo), nota),
        )
        self.conn.commit()

    def historial(self, pid: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT ts, tipo, campo, valor_anterior, valor_nuevo, nota "
            "FROM catalogo_historial WHERE producto_id = ? ORDER BY ts, id", (pid,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- operaciones de negocio -----------------------------------------

    def actualizar_publicacion(self, pid: int, titulo_ml=None, ml_category_id=None,
                               ml_attributes=None, pictures=None) -> ProductoCatalogo:
        """Completa/edita los datos necesarios para publicar: título, categoría
        de MercadoLibre, atributos obligatorios y fotos."""
        p = self.obtener(pid)
        if not p:
            raise KeyError(pid)
        if titulo_ml is not None:
            p.titulo_ml = titulo_ml
        if ml_category_id is not None:
            p.ml_category_id = ml_category_id
        if ml_attributes is not None:
            p.ml_attributes = ml_attributes
        if pictures is not None:
            p.pictures = pictures
        self._guardar(p)
        self._log(pid, "publicacion", nota="Datos de publicación actualizados "
                  f"(cat {p.ml_category_id or '—'}, {len(p.pictures)} foto/s)")
        return p

    def recalcular(self, pid: int) -> ProductoCatalogo:
        p = self.obtener(pid)
        if not p:
            raise KeyError(pid)
        self._calcular(p)
        self._guardar(p)
        self._log(pid, "recalculo", nota=f"costo ${p.costo_total_ars:,.0f}, "
                                          f"sugerido ${p.precio_sugerido_ars:,.0f}")
        return p

    def actualizar_precio(self, pid: int, nuevo_precio: float) -> ProductoCatalogo:
        p = self.obtener(pid)
        if not p:
            raise KeyError(pid)
        anterior = p.precio_publicado_ars
        p.precio_publicado_ars = nuevo_precio
        self._calcular(p)
        self._guardar(p)
        self._log(pid, "precio", "precio_publicado_ars", anterior, nuevo_precio)
        return p

    def actualizar_stock(self, pid: int, nuevo_stock: int) -> ProductoCatalogo:
        p = self.obtener(pid)
        if not p:
            raise KeyError(pid)
        anterior = p.stock
        p.stock = nuevo_stock
        self._guardar(p)
        self._log(pid, "stock", "stock", anterior, nuevo_stock)
        return p

    def cambiar_estado(self, pid: int, nuevo_estado: str, nota: str = "") -> ProductoCatalogo:
        if nuevo_estado not in ESTADOS:
            raise ValueError(f"Estado inválido: {nuevo_estado}")
        p = self.obtener(pid)
        if not p:
            raise KeyError(pid)
        anterior = p.estado
        p.estado = nuevo_estado
        self._guardar(p)
        self._log(pid, "estado", "estado", anterior, nuevo_estado, nota)
        return p

    def registrar_publicacion(self, pid: int, ml_item_id: str,
                              permalink: str = "") -> ProductoCatalogo:
        """Marca el producto como publicado con el id de MercadoLibre."""
        p = self.obtener(pid)
        if not p:
            raise KeyError(pid)
        p.ml_item_id = ml_item_id
        p.ml_permalink = permalink
        p.estado = "publicado"
        if p.precio_publicado_ars is None:
            p.precio_publicado_ars = p.precio_sugerido_ars
        self._guardar(p)
        self._log(pid, "publicado", "ml_item_id", None, ml_item_id,
                  f"Publicado en MercadoLibre ({permalink})")
        return p
