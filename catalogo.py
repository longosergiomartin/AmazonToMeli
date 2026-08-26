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
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from arbitraje.config import Config, CONFIG_DEFAULT, PRESETS_FISCALES
from arbitraje.importacion import calcular_costo
from arbitraje.models import Producto as ProductoArbitraje
from arbitraje.pricing import precio_sugerido, margen_real_al_precio

ESTADOS = ("borrador", "aprobado", "publicado", "pausado")
# Con qué tipo de cambio se valúa la compra en Amazon.
DOLARES_COSTO = ("tarjeta", "oficial")


def id_de_youtube(valor: str) -> str:
    """El id del video a partir de lo que haya pegado el usuario.

    MercadoLibre quiere el id pelado (`dQw4w9WgXcQ`), pero lo que se copia del
    navegador es la URL entera. Se aceptan las dos cosas, y las formas de link
    que usa YouTube: watch, youtu.be, embed y shorts. Lo que no se entiende
    devuelve vacío, así no se manda basura a la publicación.
    """
    import re

    v = (valor or "").strip()
    if not v:
        return ""
    patrones = (r"[?&]v=([A-Za-z0-9_-]{11})",
                r"youtu\.be/([A-Za-z0-9_-]{11})",
                r"/(?:embed|shorts|v)/([A-Za-z0-9_-]{11})")
    for patron in patrones:
        m = re.search(patron, v)
        if m:
            return m.group(1)
    return v if re.fullmatch(r"[A-Za-z0-9_-]{11}", v) else ""


@dataclass
class ProductoCatalogo:
    # --- datos de Amazon (carga manual) ---
    amazon_link: str = ""
    asin: str = ""
    marca: str = ""
    modelo: str = ""
    # Número de modelo que declara el fabricante en la ficha de Amazon. En LEGO
    # es el número de set (75304): el identificador con el que MercadoLibre
    # tiene cargado el producto en su catálogo.
    modelo_fabricante: str = ""
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
    # Días de preparación/disponibilidad que se muestran en la entrega de la
    # publicación (ML los suma a la fecha estimada). Clave para dropshipping.
    dias_preparacion: int = 25
    # --- MercadoLibre ---
    titulo_ml: str = ""
    descripcion: str = ""              # descripción de la publicación (de Amazon)
    ml_category_id: str = ""
    ml_attributes: dict = field(default_factory=dict)
    pictures: list = field(default_factory=list)
    # Videos del producto en Amazon. Son de su CDN (.mp4/.m3u8) y **no se
    # pueden publicar en MercadoLibre**, que solo acepta YouTube: se guardan
    # para poder verlos sin volver a Amazon.
    videos: list = field(default_factory=list)
    # El que sí va a la publicación: id de un video de YouTube.
    video_youtube: str = ""
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

    def __init__(self, conn, cfg: Config = CONFIG_DEFAULT,
                 cotizacion: Optional[dict] = None,
                 proveedor_cotizacion: Optional[Callable[[], dict]] = None):
        self.conn = conn
        self.cfg = cfg
        # Cotización en vivo {oficial, tarjeta}. Si está, manda sobre la config.
        self.cotizacion = cotizacion
        # Si no se pasa hecha, se busca en el primer uso: pedirla al arrancar
        # deja el arranque a merced de que la API del dólar responda.
        self._proveedor_cotizacion = proveedor_cotizacion
        self._crear_tablas()

    def _cfg_efectivo(self, tc: Optional[float] = None) -> Config:
        """Config con el tipo de cambio en vivo, el dólar elegido para el costo
        y la condición fiscal.

        `tc` fuerza un tipo de cambio puesto a mano: gana sobre la cotización en
        vivo y sobre el dólar elegido, porque es el número que el usuario
        escribió y no una estimación.
        """
        cfg = self.cfg
        tc = tc or self.tc_manual
        if tc:
            cfg = replace(cfg, tipo_cambio_oficial=float(tc),
                          recargo_tarjeta_pct=0.0)
            condicion = self.condicion_fiscal
            if condicion and condicion != cfg.meli.condicion_fiscal:
                cfg = replace(cfg, meli=cfg.meli.con_condicion_fiscal(condicion))
            return cfg
        if self.cotizacion is None and self._proveedor_cotizacion is not None:
            try:
                self.cotizacion = self._proveedor_cotizacion() or {}
            except Exception:  # noqa: BLE001 - sin cotización se usa la de config
                self.cotizacion = {}
        c = self.cotizacion
        if c and c.get("oficial") and c.get("tarjeta"):
            recargo = c["tarjeta"] / c["oficial"] - 1
            cfg = replace(cfg, tipo_cambio_oficial=c["oficial"],
                          recargo_tarjeta_pct=recargo)
        # Con qué dólar se valúa la compra en Amazon: "tarjeta" (lo que
        # realmente debita la tarjeta) u "oficial" (si comprás con dólares
        # propios o recuperás las percepciones).
        if self.dolar_costo == "oficial":
            cfg = replace(cfg, recargo_tarjeta_pct=0.0)
        condicion = self.condicion_fiscal
        if condicion and condicion != cfg.meli.condicion_fiscal:
            cfg = replace(cfg, meli=cfg.meli.con_condicion_fiscal(condicion))
        return cfg

    # ---- preferencias -----------------------------------------------------

    def _pref(self, clave: str, default: str) -> str:
        row = self.conn.execute(
            "SELECT valor FROM preferencias WHERE clave = ?", (clave,)).fetchone()
        return row["valor"] if row else default

    def _set_pref(self, clave: str, valor: str) -> None:
        self.conn.execute(
            """INSERT INTO preferencias (clave, valor) VALUES (?, ?)
               ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor""",
            (clave, valor))
        self.conn.commit()

    @property
    def condicion_fiscal(self) -> str:
        return self._pref("condicion_fiscal", self.cfg.meli.condicion_fiscal)

    @condicion_fiscal.setter
    def condicion_fiscal(self, valor: str) -> None:
        if valor not in PRESETS_FISCALES:
            raise ValueError(f"Condición fiscal desconocida: {valor!r}")
        self._set_pref("condicion_fiscal", valor)

    @property
    def dolar_costo(self) -> str:
        return self._pref("dolar_costo", "tarjeta")

    @dolar_costo.setter
    def dolar_costo(self, valor: str) -> None:
        if valor not in DOLARES_COSTO:
            raise ValueError(f"Dólar inválido: {valor!r} (usá tarjeta u oficial)")
        self._set_pref("dolar_costo", valor)

    @property
    def tc_manual(self) -> Optional[float]:
        """Tipo de cambio fijado a mano para valuar el costo.

        Cuando se decide "compro a dólar 1600", ese pasa a ser **el** dólar del
        catálogo hasta que se cambie: si no, la tabla mostraría el costo a la
        cotización del mercado y el margen no coincidiría con el que se vio al
        decidir el precio. Vacío = se usa la cotización en vivo.
        """
        crudo = self._pref("tc_manual", "")
        try:
            return float(crudo) or None
        except (TypeError, ValueError):
            return None

    @tc_manual.setter
    def tc_manual(self, valor) -> None:
        if valor in (None, "", 0):
            self._set_pref("tc_manual", "")
            return
        try:
            v = float(valor)
        except (TypeError, ValueError):
            raise ValueError("El tipo de cambio tiene que ser un número.")
        if v <= 0:
            raise ValueError("El tipo de cambio tiene que ser mayor que cero.")
        self._set_pref("tc_manual", str(v))

    @property
    def filtro(self) -> dict:
        """Qué productos entran al catálogo. Configurable porque la herramienta
        sirve para cualquier rubro, no solo para sets de construcción."""
        return {
            "marca": self._pref("filtro_marca", ""),
            "descartar_accesorios": self._pref("filtro_accesorios", "1") == "1",
            "precio_min_usd": float(self._pref("filtro_precio_min", "25") or 0),
        }

    @filtro.setter
    def filtro(self, valores: dict) -> None:
        if "marca" in valores:
            self._set_pref("filtro_marca", (valores["marca"] or "").strip()[:40])
        if "descartar_accesorios" in valores:
            self._set_pref("filtro_accesorios",
                           "1" if valores["descartar_accesorios"] else "0")
        if "precio_min_usd" in valores:
            try:
                minimo = max(0.0, float(valores["precio_min_usd"]))
            except (TypeError, ValueError):
                raise ValueError("El precio mínimo tiene que ser un número.")
            self._set_pref("filtro_precio_min", str(minimo))

    def recalcular_todos(self) -> None:
        """Recalcula costo/precio/margen de todo el catálogo (por ejemplo tras
        cambiar la condición fiscal)."""
        for p in self.todos():
            self._calcular(p)
            self._guardar(p)

    def limpiar_marcas(self) -> int:
        """Arregla las marcas que quedaron con el texto del byline de Amazon
        ("Visit the LEGO Store" en vez de "LEGO"). Los productos importados
        antes de la corrección tienen ese valor guardado y MercadoLibre lo
        rechaza. Devuelve cuántos se corrigieron."""
        from marcas import elegir_marca
        arreglados = 0
        for p in self.todos():
            # Si lo guardado no sirve (texto del byline, HTML del scraping), se
            # recurre al título, que en Amazon arranca con la marca.
            limpia = elegir_marca(p.marca, p.titulo_ml or p.modelo or "")
            if limpia and limpia != p.marca:
                anterior, p.marca = p.marca, limpia
                self._guardar(p)
                self._log(p.id, "marca", "marca", anterior, limpia)
                arreglados += 1
        return arreglados

    def limpiar_titulos(self) -> int:
        """Saca de los títulos el código interno de Amazon que se coló al final.

        Los productos cargados antes del arreglo quedaron con un número de 7
        dígitos pegado ("...La Catrina 21372 6589589"): Amazon lo declara como
        número de modelo, pero no identifica nada y ensucia tanto el título como
        la búsqueda en el catálogo de MercadoLibre.
        """
        import re
        from titulos import titulo_para_ml
        arreglados = 0
        for p in self.todos():
            # Restos de versiones anteriores: códigos internos de 7+ dígitos, y
            # títulos que quedaron con basura de puntuación ("Set # – 1 103").
            sucio = (re.search(r"\b\d{7,}\b", p.titulo_ml or "")
                     or "#" in (p.titulo_ml or "")
                     or re.search(r"\d\s+\d{3}\s", p.titulo_ml or ""))
            if not sucio:
                continue
            set_id = p.modelo_fabricante if re.fullmatch(
                r"\d{4,6}", p.modelo_fabricante or "") else ""
            nuevo = titulo_para_ml(p.marca, p.modelo or p.titulo_ml, set_id)
            if nuevo and nuevo != p.titulo_ml:
                anterior, p.titulo_ml = p.titulo_ml, nuevo
                self._guardar(p)
                self._log(p.id, "titulo", "titulo_ml", anterior, nuevo)
                arreglados += 1
        return arreglados

    def vaciar(self, incluir_publicados: bool = False) -> dict:
        """Borra el catálogo para empezar de cero.

        Los productos **publicados** se conservan por defecto: borrarlos de acá
        no los baja de MercadoLibre, solo hace que la herramienta les pierda el
        rastro y ya no se les pueda cambiar precio ni pausarlos desde el panel.
        """
        publicados = [p for p in self.todos() if p.ml_item_id]
        if incluir_publicados or not publicados:
            self.conn.execute("DELETE FROM catalogo_historial")
            self.conn.execute("DELETE FROM catalogo")
            conservados = 0
        else:
            ids = [p.id for p in publicados]
            marcas = ",".join("?" * len(ids))
            self.conn.execute(
                f"DELETE FROM catalogo_historial WHERE producto_id NOT IN ({marcas})", ids)
            self.conn.execute(f"DELETE FROM catalogo WHERE id NOT IN ({marcas})", ids)
            conservados = len(ids)
        self.conn.commit()
        return {"conservados_publicados": conservados,
                "quedan": len(self.todos())}

    @staticmethod
    def _migrar(conn) -> None:
        """Columnas agregadas después de la primera versión. Corre al abrir la
        conexión, no al arrancar la app."""
        cols = conn.columnas("catalogo")
        for columna, tipo in (("modelo_fabricante", "TEXT"),
                              ("pictures", "TEXT"),
                              ("dias_preparacion", "INTEGER DEFAULT 25"),
                              ("descripcion", "TEXT"),
                              ("videos", "TEXT"),
                              ("video_youtube", "TEXT")):
            if columna not in cols:
                conn.execute(f"ALTER TABLE catalogo ADD COLUMN {columna} {tipo}")

    def _crear_tablas(self) -> None:
        self.conn.preparar("""
            CREATE TABLE IF NOT EXISTS catalogo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creado TEXT NOT NULL, actualizado TEXT NOT NULL,
                amazon_link TEXT, asin TEXT, marca TEXT, modelo TEXT,
                modelo_fabricante TEXT,
                precio_usd REAL, peso_kg REAL, costo_envio_usd REAL,
                disponibilidad TEXT, regimen TEXT, arancel_pct REAL,
                categoria TEXT, margen_deseado REAL, stock INTEGER,
                dias_preparacion INTEGER,
                titulo_ml TEXT, descripcion TEXT,
                ml_category_id TEXT, ml_attributes TEXT,
                pictures TEXT, videos TEXT, video_youtube TEXT,
                costo_total_ars REAL, precio_sugerido_ars REAL,
                precio_publicado_ars REAL, margen_pct REAL,
                estado TEXT, ml_item_id TEXT, ml_permalink TEXT
            );
            CREATE TABLE IF NOT EXISTS preferencias (
                clave TEXT PRIMARY KEY, valor TEXT
            );
            CREATE TABLE IF NOT EXISTS catalogo_historial (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                producto_id INTEGER NOT NULL, ts TEXT NOT NULL,
                tipo TEXT NOT NULL, campo TEXT,
                valor_anterior TEXT, valor_nuevo TEXT, nota TEXT
            );
        """, migracion=self._migrar)

    # ---- cálculo (reutiliza el motor arbitraje) --------------------------

    def _calcular(self, p: ProductoCatalogo) -> None:
        """Completa costo_total_ars, precio_sugerido_ars y margen_pct."""
        # Si no se cargó el envío+importación, se estima como % del precio de
        # Amazon (envio_import_pct, ~26%). Cargando el Total real del checkout
        # el número es exacto.
        if not p.costo_envio_usd and p.precio_usd:
            p.costo_envio_usd = round(p.precio_usd * self.cfg.envio_import_pct, 2)
        base_usd = p.precio_usd + p.costo_envio_usd
        pa = ProductoArbitraje(
            nombre=p.modelo or p.asin or "producto", query_meli=p.modelo or "",
            precio_amazon_usd=base_usd, peso_kg=p.peso_kg, arancel_pct=p.arancel_pct,
        )
        cfg = self._cfg_efectivo()
        regimen = p.regimen
        if regimen == "landed":
            # Amazon ya informó el Total puesto en Argentina (producto + envío +
            # importación): se usa directo, sin estimar aduana ni sumar impuestos.
            pa.precio_landed_usd = base_usd
        elif regimen == "courier":
            # El costo de envío ya lo cargó el usuario: anulamos el flete estimado
            # para no contarlo dos veces.
            cfg = replace(cfg, courier=replace(cfg.courier, flete_usd_por_kg=0.0))
        costo = calcular_costo(pa, regimen=regimen, cfg=cfg)
        p.costo_total_ars = costo.total_ars
        p.precio_sugerido_ars = precio_sugerido(
            costo.total_ars, p.margen_deseado, p.categoria, self._cfg_efectivo())
        # margen real al precio que efectivamente se usará (publicado o sugerido)
        precio_ref = p.precio_publicado_ars or p.precio_sugerido_ars
        p.margen_pct = margen_real_al_precio(
            costo.total_ars, precio_ref, p.categoria, self._cfg_efectivo())["margen_pct"]

    def _costo_ars(self, p: ProductoCatalogo, tc: Optional[float] = None) -> float:
        """El costo puesto en Argentina, valuado al tipo de cambio que se pida."""
        copia = replace(p)
        if not copia.costo_envio_usd and copia.precio_usd:
            copia.costo_envio_usd = round(copia.precio_usd * self.cfg.envio_import_pct, 2)
        base_usd = copia.precio_usd + copia.costo_envio_usd
        pa = ProductoArbitraje(
            nombre=copia.modelo or copia.asin or "producto",
            query_meli=copia.modelo or "", precio_amazon_usd=base_usd,
            peso_kg=copia.peso_kg, arancel_pct=copia.arancel_pct)
        cfg = self._cfg_efectivo(tc)
        if copia.regimen == "landed":
            pa.precio_landed_usd = base_usd
        elif copia.regimen == "courier":
            cfg = replace(cfg, courier=replace(cfg.courier, flete_usd_por_kg=0.0))
        return calcular_costo(pa, regimen=copia.regimen, cfg=cfg).total_ars

    def simular(self, p: ProductoCatalogo, tc_costo: Optional[float] = None,
                tc_venta: Optional[float] = None,
                margen: Optional[float] = None) -> dict:
        """Costo y precio de venta bajo tipos de cambio puestos a mano.

        Es la forma en que se piensa el negocio de verdad: *compro a dólar
        1600 y vendo a dólar 3200*. El precio de venta sale de valuar el mismo
        costo en dólares al tipo de cambio de venta, así que el "margen" queda
        expresado como una cotización y no como un porcentaje.

        Si no se da `tc_venta`, el precio sale del margen (el de `margen` o el
        del producto), que es el modo de siempre.
        """
        costo = self._costo_ars(p, tc_costo)
        if tc_venta:
            precio = self._costo_ars(p, tc_venta)
        else:
            m = p.margen_deseado if margen is None else margen
            precio = precio_sugerido(costo, m, p.categoria, self._cfg_efectivo(tc_costo))
        real = margen_real_al_precio(costo, precio, p.categoria,
                                     self._cfg_efectivo(tc_costo))
        return {"costo_ars": round(costo, 2), "precio_ars": round(precio, 2),
                "margen_pct": round(real["margen_pct"], 1),
                "margen_ars": round(real["margen_ars"], 2)}

    def margen_insuficiente(self, p: ProductoCatalogo) -> bool:
        return p.margen_pct < self.cfg.umbral_margen_bueno_pct

    def comparacion_dolar(self, p: ProductoCatalogo) -> dict:
        """Costo y margen del producto bajo dólar oficial y dólar tarjeta, para
        comparar. El costo escala lineal con el tipo de cambio."""
        cfg_ef = self._cfg_efectivo()
        oficial = cfg_ef.tipo_cambio_oficial
        # El tarjeta sale de la cotización en vivo (no de cfg_ef, que puede
        # tener el recargo anulado si se eligió valuar al oficial).
        c = self.cotizacion or {}
        tarjeta = c.get("tarjeta") or self.cfg.tc_compra()
        # El costo guardado se calculó con el TC efectivamente elegido.
        tc_usado = cfg_ef.tc_compra()
        total_usd = (p.costo_total_ars / tc_usado) if tc_usado else 0.0
        precio = p.precio_publicado_ars or p.precio_sugerido_ars
        out = {}
        for nombre, tc in (("oficial", oficial), ("tarjeta", tarjeta)):
            costo = round(total_usd * tc, 2)
            m = margen_real_al_precio(costo, precio, p.categoria, cfg_ef)
            out[nombre] = {"tc": round(tc, 2), "costo_ars": costo,
                           "margen_ars": m["margen_ars"], "margen_pct": m["margen_pct"]}
        return out

    # ---- persistencia ----------------------------------------------------

    _CAMPOS = ["amazon_link", "asin", "marca", "modelo", "modelo_fabricante",
               "precio_usd", "peso_kg",
               "costo_envio_usd", "disponibilidad", "regimen", "arancel_pct",
               "categoria", "margen_deseado", "stock", "dias_preparacion",
               "titulo_ml", "descripcion",
               "ml_category_id", "costo_total_ars", "precio_sugerido_ars",
               "precio_publicado_ars", "margen_pct", "estado", "ml_item_id",
               "ml_permalink", "video_youtube"]

    def _fila_a_producto(self, row) -> ProductoCatalogo:
        d = dict(row)
        attrs = json.loads(d.pop("ml_attributes") or "{}")
        pics = json.loads(d.pop("pictures") or "[]")
        vids = json.loads(d.pop("videos", None) or "[]")
        # Las filas anteriores a la migración traen NULL en las columnas nuevas,
        # y el campo está declarado como texto: sin esto entra un None donde el
        # resto del código espera un str.
        d["video_youtube"] = d.get("video_youtube") or ""
        d.pop("creado", None); d.pop("actualizado", None)
        return ProductoCatalogo(ml_attributes=attrs, pictures=pics, videos=vids,
                                **{k: d[k] for k in d})

    def agregar(self, p: ProductoCatalogo) -> ProductoCatalogo:
        self._calcular(p)
        vals = [getattr(p, c) for c in self._CAMPOS]
        p.id = self.conn.insertar(
            f"""INSERT INTO catalogo (creado, actualizado, ml_attributes, pictures, videos, {','.join(self._CAMPOS)})
                VALUES (?, ?, ?, ?, ?, {','.join('?' * len(self._CAMPOS))})""",
            [_ahora(), _ahora(), json.dumps(p.ml_attributes),
             json.dumps(p.pictures), json.dumps(p.videos)] + vals,
        )
        self.conn.commit()
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
            f"UPDATE catalogo SET actualizado = ?, ml_attributes = ?, pictures = ?, "
            f"videos = ?, {sets} WHERE id = ?",
            [_ahora(), json.dumps(p.ml_attributes), json.dumps(p.pictures),
             json.dumps(p.videos)] + vals + [p.id],
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
                               ml_attributes=None, pictures=None,
                               dias_preparacion=None, descripcion=None,
                               marca=None, modelo=None,
                               modelo_fabricante=None, videos=None,
                               video_youtube=None) -> ProductoCatalogo:
        """Completa/edita los datos necesarios para publicar: título, categoría
        de MercadoLibre, marca, modelo, atributos obligatorios, fotos y días de
        preparación."""
        p = self.obtener(pid)
        if not p:
            raise KeyError(pid)
        if titulo_ml is not None:
            p.titulo_ml = titulo_ml
        if marca is not None:
            p.marca = marca
        if modelo is not None:
            p.modelo = modelo
        if modelo_fabricante is not None:
            p.modelo_fabricante = modelo_fabricante
        if ml_category_id is not None:
            p.ml_category_id = ml_category_id
        if ml_attributes is not None:
            p.ml_attributes = ml_attributes
        if pictures is not None:
            p.pictures = pictures
        if dias_preparacion is not None:
            p.dias_preparacion = int(dias_preparacion)
        if descripcion is not None:
            p.descripcion = descripcion
        if videos is not None:
            p.videos = videos
        if video_youtube is not None:
            p.video_youtube = id_de_youtube(video_youtube)
        self._guardar(p)
        self._log(pid, "publicacion", nota="Datos de publicación actualizados "
                  f"(cat {p.ml_category_id or '—'}, {len(p.pictures)} foto/s)")
        return p

    def eliminar(self, pid: int) -> None:
        self.conn.execute("DELETE FROM catalogo WHERE id = ?", (pid,))
        self.conn.execute("DELETE FROM catalogo_historial WHERE producto_id = ?", (pid,))
        self.conn.commit()

    def cambiar_regimen(self, pid: int, regimen: str) -> ProductoCatalogo:
        p = self.obtener(pid)
        if not p:
            raise KeyError(pid)
        anterior = p.regimen
        p.regimen = regimen
        self._calcular(p)
        self._guardar(p)
        self._log(pid, "regimen", "regimen", anterior, regimen)
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

    def actualizar_margen(self, pid: int, margen: float) -> ProductoCatalogo:
        """Cambia el margen deseado y recalcula el precio sugerido."""
        p = self.obtener(pid)
        if not p:
            raise KeyError(pid)
        anterior = p.margen_deseado
        p.margen_deseado = float(margen)
        self._calcular(p)
        self._guardar(p)
        self._log(pid, "margen", "margen_deseado", anterior, p.margen_deseado)
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
                              permalink: str = "", estado_ml: str = "") -> ProductoCatalogo:
        """Marca el producto como publicado con el id de MercadoLibre.

        Exige el id: sin él no hay publicación que valga: marcarlo igual es
        decirle al usuario que vendió algo que no existe.

        `estado_ml` es el estado que devolvió MercadoLibre:

          - `active`: a la venta. Queda **publicado**.
          - `paused`: la publicación **existe** y tiene su link; MercadoLibre la
            deja así mientras revisa las fotos y los datos, y después la activa
            sola. Queda **pausado**, que es un estado real del catálogo y trae su
            botón de reactivar. Darla por fallada era peor que inútil: el ítem
            seguía vivo en MercadoLibre y acá figuraba sin publicar, así que el
            siguiente intento creaba un duplicado.
          - el resto (`payment_required`, `inactive`): el ítem se creó pero no se
            muestra y hace falta hacer algo a mano, así que se avisa con un error.
            El id y el link se guardan igual, para poder encontrarlo.
        """
        p = self.obtener(pid)
        if not p:
            raise KeyError(pid)
        if not (ml_item_id or "").strip():
            raise ValueError("MercadoLibre no devolvió el id de la publicación: "
                             "no se puede dar por publicada.")
        if estado_ml and estado_ml not in ("active", "paused"):
            p.ml_item_id = ml_item_id
            p.ml_permalink = permalink
            self._guardar(p)
            self._log(pid, "publicado", "ml_item_id", None, ml_item_id,
                      f"MercadoLibre creó el ítem pero quedó en «{estado_ml}», "
                      "no a la venta")
            raise ValueError(f"MercadoLibre creó el ítem {ml_item_id} pero quedó "
                             f"en estado «{estado_ml}», no publicado.")
        pausado = estado_ml == "paused"
        p.ml_item_id = ml_item_id
        p.ml_permalink = permalink
        p.estado = "pausado" if pausado else "publicado"
        if p.precio_publicado_ars is None:
            p.precio_publicado_ars = p.precio_sugerido_ars
        self._guardar(p)
        self._log(pid, p.estado, "ml_item_id", None, ml_item_id,
                  (f"Creado en MercadoLibre, en pausa mientras lo revisan "
                   f"({permalink})") if pausado else
                  f"Publicado en MercadoLibre ({permalink})")
        return p
