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
from typing import Callable, Optional, Sequence

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
    # ¿Amazon manda este producto a Argentina con envío internacional gratis?
    # Cambia el costo de forma brutal: con envío gratis los cargos de
    # importación rondan el 26% del precio, sin envío gratis el flete lo empuja
    # al 70%. Tres estados: True = tiene envío gratis, False = no lo tiene,
    # None = todavía no se miró. Para la cuenta, None se trata como False: la
    # estimación cara es la conservadora, y equivocarse para abajo es publicar
    # perdiendo plata.
    envio_gratis_amazon: Optional[bool] = None
    disponibilidad: str = "in_stock"   # in_stock | out_of_stock
    # --- parámetros de importación / venta ---
    regimen: str = "courier"           # courier | general
    arancel_pct: float = 0.16
    categoria: str = "default"         # categoría de comisión de MeLi
    margen_deseado: float = 0.30       # fracción sobre el costo
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
    revisado_en: str = ""              # última revisión de precio/stock en Amazon
    # Costo real en pesos, puesto a mano. Si está, gana sobre todo el cálculo:
    # el tipo de cambio, el % de envío y el régimen son estimaciones para
    # cuando no se sabe, y acá se sabe. 0 o None = se calcula.
    costo_manual_ars: Optional[float] = None
    # Precio del producto en pesos, puesto a mano, SIN el envío internacional.
    # Es el otro número que se conoce: lo que sale el producto, no lo que sale
    # traerlo. La herramienta le suma el envío + importación que corresponda
    # según `envio_gratis_amazon`, así el mismo producto cuesta distinto —y se
    # vende a distinto precio— según Amazon lo mande gratis o no.
    # `costo_manual_ars` (el total ya puesto acá) gana sobre este: es un dato
    # más completo. Cargar uno borra el otro, para que haya una sola verdad.
    costo_producto_manual_ars: Optional[float] = None
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
        # Preferencias leídas: se llenan al primer uso y las invalida su setter.
        self._cache_pref: dict[str, Optional[str]] = {}
        self._crear_tablas()

    def _cfg_efectivo(self, tc: Optional[float] = None,
                      envio: Optional[float] = None) -> Config:
        """Config con el tipo de cambio en vivo, el dólar elegido para el costo,
        el envío que pagás y la condición fiscal.

        `tc` fuerza un tipo de cambio puesto a mano: gana sobre la cotización en
        vivo y sobre el dólar elegido, porque es el número que el usuario
        escribió y no una estimación. `envio` funciona igual.
        """
        cfg = self.cfg
        tc = tc or self.tc_manual
        if tc:
            cfg = replace(cfg, tipo_cambio_oficial=float(tc),
                          recargo_tarjeta_pct=0.0)
        else:
            if self.cotizacion is None and self._proveedor_cotizacion is not None:
                try:
                    self.cotizacion = self._proveedor_cotizacion() or {}
                except Exception:  # noqa: BLE001 - sin cotización, la de config
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
        envio = self.envio_manual if envio is None else envio
        if envio is not None:
            cfg = replace(cfg, meli=replace(cfg.meli, envio_gratis_ars=float(envio)))
        pct = self.envio_import_pct
        if pct != cfg.envio_import_pct:
            cfg = replace(cfg, envio_import_pct=pct)
        piso = self.ganancia_minima
        if piso != cfg.ganancia_minima_ars:
            cfg = replace(cfg, ganancia_minima_ars=piso)
        condicion = self.condicion_fiscal
        if condicion and condicion != cfg.meli.condicion_fiscal:
            cfg = replace(cfg, meli=cfg.meli.con_condicion_fiscal(condicion))
        return cfg

    # ---- preferencias -----------------------------------------------------

    def _pref(self, clave: str, default: str) -> str:
        """Una preferencia, con la lectura cacheada en memoria.

        Sin la caché, recalcular el catálogo entero eran ~1.000 consultas para
        leer cuatro valores que no cambian en toda la operación: el 90% de los
        viajes a la base, y con la base por red eso solo ya se comía el minuto y
        medio. Las preferencias únicamente cambian por los setters de acá, así
        que alcanza con invalidar en `_set_pref`.
        """
        if clave not in self._cache_pref:
            row = self.conn.execute(
                "SELECT valor FROM preferencias WHERE clave = ?", (clave,)).fetchone()
            # Se cachea lo que hay en la base, no el default: dos llamadas
            # podrían pedir la misma clave con defaults distintos y la primera
            # le fijaría el suyo a la segunda.
            self._cache_pref[clave] = row["valor"] if row else None
        guardado = self._cache_pref[clave]
        return default if guardado is None else guardado

    def _set_pref(self, clave: str, valor: str) -> None:
        self.conn.execute(
            """INSERT INTO preferencias (clave, valor) VALUES (?, ?)
               ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor""",
            (clave, valor))
        self.conn.commit()
        self._cache_pref[clave] = valor

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
    def publicar_en_catalogo(self) -> bool:
        """Si se intenta primero publicar contra el catálogo de MercadoLibre.

        Apagado por defecto. En el catálogo todos los vendedores comparten la
        misma ficha, así que lo único que distingue una oferta de otra es el
        precio y el tiempo de entrega: las dos peores cartas de quien importa
        contra alguien con stock local. Con publicación propia se compite en la
        búsqueda, que es donde tener sets que nadie más tiene sí es una ventaja.

        Encendido, vuelve al comportamiento anterior. Apagado, el catálogo sigue
        usándose como salida de emergencia cuando la publicación propia no sale.
        """
        return self._pref("publicar_en_catalogo", "0") == "1"

    @publicar_en_catalogo.setter
    def publicar_en_catalogo(self, valor) -> None:
        self._set_pref("publicar_en_catalogo", "1" if valor else "0")

    @property
    def ganancia_minima(self) -> float:
        """Cuánto tiene que dejar como mínimo cada venta, en pesos.

        Los imprevistos de importar —que el precio suba entre publicar y
        vender, que se agote y haya que conseguirlo más caro, un reclamo— salen
        un monto fijo, no un porcentaje del producto. Un 30% sobre un set barato
        no banca ninguno. 0 = sin piso.
        """
        try:
            return float(self._pref("ganancia_minima", "0") or 0)
        except (TypeError, ValueError):
            return 0.0

    @ganancia_minima.setter
    def ganancia_minima(self, valor) -> None:
        try:
            v = float(valor or 0)
        except (TypeError, ValueError):
            raise ValueError("La ganancia mínima tiene que ser un número.")
        if v < 0:
            raise ValueError("La ganancia mínima no puede ser negativa.")
        self._set_pref("ganancia_minima", str(v))

    @property
    def envio_import_pct(self) -> float:
        """Envío internacional + cargos de importación, como % del precio de
        Amazon, cuando no se cargó el Total real del checkout.

        El 26% de fábrica supone que el envío entra en la promoción de envío
        gratis de Amazon. Comprando de a un producto no entra, y el costo real
        es bastante más: subestimarlo es vender por debajo del costo sin
        enterarse. Es el número más importante de toda la cuenta, así que tiene
        que poder ajustarse contra un checkout de verdad.
        """
        try:
            v = float(self._pref("envio_import_pct", "") or 0)
        except (TypeError, ValueError):
            v = 0.0
        return v if v > 0 else self.cfg.envio_import_pct

    @envio_import_pct.setter
    def envio_import_pct(self, valor) -> None:
        try:
            v = float(valor or 0)
        except (TypeError, ValueError):
            raise ValueError("El porcentaje tiene que ser un número.")
        if v < 0 or v > 3:
            raise ValueError("El porcentaje va entre 0 y 3 (0% a 300%).")
        self._set_pref("envio_import_pct", str(v))

    @property
    def envio_import_sin_gratis_pct(self) -> float:
        """El mismo porcentaje, para los productos que NO tienen envío gratis.

        Cuando Amazon no cubre el envío internacional, al precio del producto se
        le suma el flete a Argentina y el total se va a ~70% del precio, casi
        tres veces el 26% del caso con envío gratis. Aplicarle el 26% a un
        producto sin envío gratis es publicar perdiendo plata sin enterarse:
        por eso son dos números separados y no uno solo promediado.
        """
        try:
            v = float(self._pref("envio_import_sin_gratis_pct", "") or 0)
        except (TypeError, ValueError):
            v = 0.0
        return v if v > 0 else self.cfg.envio_import_sin_gratis_pct

    @envio_import_sin_gratis_pct.setter
    def envio_import_sin_gratis_pct(self, valor) -> None:
        try:
            v = float(valor or 0)
        except (TypeError, ValueError):
            raise ValueError("El porcentaje tiene que ser un número.")
        if v < 0 or v > 3:
            raise ValueError("El porcentaje va entre 0 y 3 (0% a 300%).")
        self._set_pref("envio_import_sin_gratis_pct", str(v))

    def pct_envio(self, p: ProductoCatalogo) -> float:
        """Qué porcentaje de envío + importación le toca a este producto.

        `envio_gratis_amazon` sin definir cae del lado caro a propósito: hasta
        que alguien mire el checkout no hay motivo para suponer que Amazon
        regala el envío, y el error barato es sobreestimar el costo.
        """
        return (self.envio_import_pct if p.envio_gratis_amazon
                else self.envio_import_sin_gratis_pct)

    def pcts_envio(self) -> tuple[float, ...]:
        """Los porcentajes que la herramienta puede haber estimado."""
        return (self.envio_import_pct, self.envio_import_sin_gratis_pct)

    def _envio_a_mano(self, p: ProductoCatalogo,
                      pcts: Sequence[float]) -> bool:
        """Si el envío guardado lo cargó una persona y no la estimación.

        Se reconoce por descarte: si no coincide con ninguno de los porcentajes
        que la herramienta pudo haber aplicado, salió de un checkout real. Ese
        dato es mejor que cualquier porcentaje y pisarlo sería perder la única
        medición de verdad que hay.
        """
        if not p.costo_envio_usd or not p.precio_usd:
            return False
        return all(abs(p.costo_envio_usd - round(p.precio_usd * pct, 2)) > 0.02
                   for pct in pcts)

    @property
    def revisar_con_proxy(self) -> bool:
        """Si la revisión de precio y stock pasa por ScraperAPI.

        Apagado por defecto: cada página son 5 créditos de los 1.000 del mes y
        esta tarea recorre el catálogo entero. Directo, desde un servidor,
        Amazon casi siempre rechaza; el camino que sí anda sin gastar créditos
        es leer desde el navegador del usuario.
        """
        return self._pref("revisar_con_proxy", "0") == "1"

    @revisar_con_proxy.setter
    def revisar_con_proxy(self, valor) -> None:
        self._set_pref("revisar_con_proxy", "1" if valor else "0")

    @property
    def tipo_producto(self) -> str:
        """Palabra con la que arranca el título ("Set", "Kit", "Muñeco"…).

        MercadoLibre recomienda *producto + marca + modelo*: el tipo adelante es
        lo que engancha las búsquedas genéricas ("set lego minecraft"). Vacío
        para no anteponer nada.
        """
        return self._pref("tipo_producto", "Set")

    @tipo_producto.setter
    def tipo_producto(self, valor) -> None:
        self._set_pref("tipo_producto", (valor or "").strip()[:20])

    @property
    def envio_manual(self) -> Optional[float]:
        """Lo que pagás de envío gratis, fijado a mano.

        Depende del peso del producto y de tu reputación como vendedor, así que
        no hay forma de deducirlo: sale de mirar la publicación. Como el costo
        del dólar, queda guardado para que la tabla y la simulación muestren el
        mismo margen. Vacío = el valor de la configuración.
        """
        crudo = self._pref("envio_manual", "")
        try:
            return float(crudo) or None
        except (TypeError, ValueError):
            return None

    @envio_manual.setter
    def envio_manual(self, valor) -> None:
        if valor in (None, ""):
            self._set_pref("envio_manual", "")
            return
        try:
            v = float(valor)
        except (TypeError, ValueError):
            raise ValueError("El costo de envío tiene que ser un número.")
        if v < 0:
            raise ValueError("El costo de envío no puede ser negativo.")
        self._set_pref("envio_manual", str(v))

    @property
    def filtro(self) -> dict:
        """Qué productos entran al catálogo. Configurable porque la herramienta
        sirve para cualquier rubro, no solo para sets de construcción."""
        return {
            "marca": self._pref("filtro_marca", ""),
            "descartar_accesorios": self._pref("filtro_accesorios", "1") == "1",
            "precio_min_usd": float(self._pref("filtro_precio_min", "25") or 0),
            # Descartar lo que Amazon dice explícitamente que no manda al
            # exterior. Solo eso: lo que no se pudo determinar entra igual.
            "exigir_envio": self._pref("filtro_envio", "1") == "1",
            # Desde qué país se lee la página de Amazon. Con "ar" Amazon
            # contesta si el producto llega acá; con "us" muestra la entrega en
            # EE.UU. y casi nunca lo dice.
            "pais_lectura": self._pref("filtro_pais", "us"),
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
        if "exigir_envio" in valores:
            self._set_pref("filtro_envio", "1" if valores["exigir_envio"] else "0")
        if "pais_lectura" in valores:
            pais = (valores["pais_lectura"] or "us").strip().lower()[:2]
            if pais not in ("us", "ar"):
                raise ValueError("El país de lectura tiene que ser 'us' o 'ar'.")
            self._set_pref("filtro_pais", pais)

    def recalcular_todos(self) -> None:
        """Recalcula costo/precio/margen de todo el catálogo (por ejemplo tras
        cambiar la condición fiscal o el dólar).

        Corre dentro del pedido web, así que el tiempo importa: la config se
        arma una sola vez para todos y se hace un solo commit al final. Con un
        commit por producto eran 114 viajes de ida y vuelta a la base.
        """
        cfg = self._cfg_efectivo()
        for p in self.todos():
            self._calcular(p, cfg)
            self._guardar(p, commit=False)
        self.conn.commit()

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

    def titulo_armado(self, p: ProductoCatalogo) -> str:
        """El título con el que conviene publicar este producto.

        Se arma siempre, no solo cuando el de Amazon viene sucio: el título
        crudo recortado a 60 caracteres pierde el número de set y queda cortado
        al medio, que es el peor de los dos mundos.
        """
        import re
        from titulos import titulo_para_ml, numero_de_set, piezas_del_titulo
        origen = p.modelo or p.titulo_ml or ""
        set_id = p.modelo_fabricante if re.fullmatch(
            r"\d{4,6}", p.modelo_fabricante or "") else numero_de_set(origen)
        piezas = (p.ml_attributes or {}).get("PIECES_NUMBER") or \
            piezas_del_titulo(origen)
        return titulo_para_ml(p.marca, origen, set_id,
                              piezas=str(piezas or ""), tipo=self.tipo_producto)

    @property
    def texto_compra(self) -> str:
        """El bloque de condiciones de compra que va en cada descripción.

        Vacío usa el texto por defecto. Lo que dice ahí es un compromiso
        comercial —plazos, originalidad, garantía—, así que tiene que poder
        escribirlo el vendedor y no quedar enterrado en el código.
        """
        return self._pref("texto_compra", "")

    @texto_compra.setter
    def texto_compra(self, valor) -> None:
        self._set_pref("texto_compra", (valor or "").strip()[:3000])

    def descripcion_armada(self, p: ProductoCatalogo) -> str:
        """La descripción con la que conviene publicar este producto.

        Se arma al publicar y no se guarda: así el bloque de la compra siempre
        sale con los días de preparación vigentes, y lo que el usuario editó en
        el campo Descripción sigue siendo el detalle del producto, sin que se lo
        pisemos con texto nuestro.
        """
        import re
        from titulos import numero_de_set, piezas_del_titulo
        from descripcion import armar
        origen = p.modelo or p.titulo_ml or ""
        set_id = p.modelo_fabricante if re.fullmatch(
            r"\d{4,6}", p.modelo_fabricante or "") else numero_de_set(origen)
        piezas = (p.ml_attributes or {}).get("PIECES_NUMBER") or \
            piezas_del_titulo(origen)
        return armar(titulo=p.titulo_ml or p.modelo,
                     detalle=p.descripcion or "",
                     marca=p.marca, numero_set=set_id, piezas=str(piezas or ""),
                     dias=p.dias_preparacion, compra=self.texto_compra)

    def limpiar_titulos(self, solo_sucios: bool = False) -> int:
        """Rearma los títulos de todo el catálogo con la estrategia de publicación.

        Con `solo_sucios` se limita a los que traen basura evidente: códigos
        internos de Amazon de 7 dígitos ("...La Catrina 21372 6589589"), que no
        identifican nada, o restos de puntuación ("Set # – 1 103").
        """
        import re
        arreglados = 0
        for p in self.todos():
            if solo_sucios:
                sucio = (re.search(r"\b\d{7,}\b", p.titulo_ml or "")
                         or "#" in (p.titulo_ml or "")
                         or re.search(r"\d\s+\d{3}\s", p.titulo_ml or ""))
                if not sucio:
                    continue
            nuevo = self.titulo_armado(p)
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
                              ("video_youtube", "TEXT"),
                              # Cuándo se miró por última vez el precio y el
                              # stock en Amazon. Sirve para ir rotando: revisar
                              # todo el catálogo de una gasta casi el mes
                              # entero de créditos de ScraperAPI.
                              ("revisado_en", "TEXT"),
                              # Costo puesto a mano, en pesos. Cuando el
                              # usuario conoce el costo real —del checkout de
                              # Amazon, del resumen de la tarjeta— ese dato es
                              # mejor que cualquier estimación.
                              ("costo_manual_ars", "REAL"),
                              # Si Amazon lo manda gratis a Argentina. NULL en
                              # todo lo que ya estaba cargado: nadie lo miró
                              # todavía, y se paga como si no lo tuviera.
                              ("envio_gratis_amazon", "INTEGER"),
                              # Precio del producto en pesos puesto a mano,
                              # sin el envío: la herramienta le suma el que
                              # corresponda según tenga o no envío gratis.
                              ("costo_producto_manual_ars", "REAL")):
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

    def costo_a_mano(self, p: ProductoCatalogo) -> Optional[float]:
        """El costo en pesos que cargó el usuario, si cargó alguno.

        Hay dos formas de cargarlo, según qué número se tenga a mano, y una
        precedencia clara entre ellas:

          - `costo_manual_ars`: el total ya puesto en Argentina. Es el dato más
            completo —sale del checkout o del resumen de la tarjeta—, no
            depende de ninguna estimación, y por eso gana.
          - `costo_producto_manual_ars`: lo que sale el producto, sin el envío
            internacional. La herramienta le suma el porcentaje que le
            corresponda según tenga o no envío gratis de Amazon, así el mismo
            producto cuesta —y se vende a— distinto precio según Amazon lo
            mande gratis o no.

        `None` = no hay nada cargado y el costo se estima entero.
        """
        if p.costo_manual_ars:
            return float(p.costo_manual_ars)
        if p.costo_producto_manual_ars:
            return round(float(p.costo_producto_manual_ars)
                         * (1 + self.pct_envio(p)), 2)
        return None

    def _calcular(self, p: ProductoCatalogo,
                  cfg_base: Optional[Config] = None) -> None:
        """Completa costo_total_ars, precio_sugerido_ars y margen_pct.

        `cfg_base` permite armar la config una sola vez cuando se recalcula todo
        el catálogo: es la misma para los 114 productos.
        """
        # Si no se cargó el envío+importación, se estima como % del precio de
        # Amazon: ~26% si el producto tiene envío internacional gratis, ~70% si
        # no. Cargando el Total real del checkout el número es exacto.
        if not p.costo_envio_usd and p.precio_usd:
            p.costo_envio_usd = round(p.precio_usd * self.pct_envio(p), 2)
        base_usd = p.precio_usd + p.costo_envio_usd
        pa = ProductoArbitraje(
            nombre=p.modelo or p.asin or "producto", query_meli=p.modelo or "",
            precio_amazon_usd=base_usd, peso_kg=p.peso_kg, arancel_pct=p.arancel_pct,
        )
        cfg_base = self._cfg_efectivo() if cfg_base is None else cfg_base
        cfg = cfg_base
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
        # El costo puesto a mano gana: el resto de la cuenta —tipo de cambio,
        # porcentaje de envío, régimen— son estimaciones para cuando no se
        # conoce el costo real. Si se conoce, estimarlo es peor.
        a_mano = self.costo_a_mano(p)
        p.costo_total_ars = costo.total_ars if a_mano is None else a_mano
        p.precio_sugerido_ars = precio_sugerido(
            p.costo_total_ars, p.margen_deseado, p.categoria, cfg_base)
        # margen real al precio que efectivamente se usará (publicado o sugerido)
        precio_ref = p.precio_publicado_ars or p.precio_sugerido_ars
        p.margen_pct = margen_real_al_precio(
            p.costo_total_ars, precio_ref, p.categoria, cfg_base)["margen_pct"]

    def _costo_ars(self, p: ProductoCatalogo, tc: Optional[float] = None) -> float:
        """El costo puesto en Argentina, valuado al tipo de cambio que se pida."""
        copia = replace(p)
        if not copia.costo_envio_usd and copia.precio_usd:
            copia.costo_envio_usd = round(copia.precio_usd * self.pct_envio(copia), 2)
        base_usd = copia.precio_usd + copia.costo_envio_usd
        pa = ProductoArbitraje(
            nombre=copia.modelo or copia.asin or "producto",
            query_meli=copia.modelo or "", precio_amazon_usd=base_usd,
            peso_kg=copia.peso_kg, arancel_pct=copia.arancel_pct)
        # El costo cargado a mano ya está en pesos: no lo mueve el dólar que se
        # pida para la simulación.
        a_mano = self.costo_a_mano(copia)
        if a_mano is not None:
            return a_mano
        cfg = self._cfg_efectivo(tc)
        if copia.regimen == "landed":
            pa.precio_landed_usd = base_usd
        elif copia.regimen == "courier":
            cfg = replace(cfg, courier=replace(cfg.courier, flete_usd_por_kg=0.0))
        return calcular_costo(pa, regimen=copia.regimen, cfg=cfg).total_ars

    def simular(self, p: ProductoCatalogo, tc_costo: Optional[float] = None,
                margen: Optional[float] = None,
                envio: Optional[float] = None) -> dict:
        """Costo y precio de venta con el dólar y el envío puestos a mano.

        El costo en USD se valúa a `tc_costo` —el dólar oficial que estimás para
        cuando compres— y el precio sale del margen que querés que te quede
        limpio, ya descontados la comisión de MercadoLibre, el envío gratis, el
        IIBB y la percepción de IVA si el precio la alcanza.
        """
        cfg = self._cfg_efectivo(tc_costo, envio)
        costo = self._costo_ars(p, tc_costo)
        m = p.margen_deseado if margen is None else margen
        precio = precio_sugerido(costo, m, p.categoria, cfg)
        real = margen_real_al_precio(costo, precio, p.categoria, cfg)
        return {"costo_ars": round(costo, 2), "precio_ars": round(precio, 2),
                "margen_pct": round(real["margen_pct"], 1),
                "margen_ars": round(real["margen_ars"], 2)}

    def a_revisar(self, limite: int = 10) -> list[ProductoCatalogo]:
        """Las publicaciones que hace más tiempo que no se miran en Amazon.

        Revisar el catálogo entero de una no se puede: cada producto son 5
        créditos de ScraperAPI y el plan gratis trae 1.000 por mes, así que 126
        productos son dos tercios del mes en una sola pasada. Se rota: primero
        las que nunca se revisaron, después las más viejas.
        """
        vivos = [p for p in self.todos()
                 if p.estado in ("publicado", "pausado")
                 and (p.amazon_link or p.asin)]
        vivos.sort(key=lambda p: p.revisado_en or "")
        return vivos[:max(0, int(limite))]

    def marcar_revisado(self, pid: int, precio_usd: Optional[float] = None,
                        disponible: Optional[bool] = None) -> ProductoCatalogo:
        """Guarda lo que se vio en Amazon y recalcula costo, precio y margen.

        `disponible=None` significa que no se pudo determinar: no se toca la
        disponibilidad guardada. Marcar como agotado algo que solo no se pudo
        leer sacaría de venta un producto que sí está.
        """
        p = self.obtener(pid)
        if not p:
            raise ValueError("No existe ese producto.")
        p.revisado_en = _ahora()
        if precio_usd is not None and precio_usd > 0 and precio_usd != p.precio_usd:
            anterior = p.precio_usd
            p.precio_usd = float(precio_usd)
            # El envío se había estimado como % del precio viejo: se recalcula,
            # salvo que el usuario haya cargado el total real del checkout.
            self._log(p.id, "precio_amazon", "precio_usd", anterior, p.precio_usd)
        if disponible is not None:
            nueva = "in_stock" if disponible else "out_of_stock"
            if nueva != p.disponibilidad:
                self._log(p.id, "stock_amazon", "disponibilidad",
                          p.disponibilidad, nueva)
                p.disponibilidad = nueva
        self._calcular(p)
        self._guardar(p)
        return p

    def reestimar_envios(self, pct_anterior=None,
                         solo: Optional[Sequence[int]] = None) -> int:
        """Vuelve a estimar el envío de los productos donde estaba estimado.

        Hace falta porque `costo_envio_usd` se guarda: cambiar el porcentaje no
        mueve solo a los productos que ya están cargados, y ahí el número nuevo
        no serviría para nada. A cada producto se le aplica el porcentaje que le
        corresponde según tenga o no envío gratis de Amazon.

        **Los cargados a mano no se tocan.** Se reconocen porque su valor no
        coincide con ninguna de las estimaciones anteriores: si alguien puso el
        Total real del checkout, ese dato es mejor que cualquier porcentaje y
        pisarlo sería perder la única medición de verdad que hay.

        `pct_anterior` son los porcentajes que estaban vigentes antes del
        cambio —uno solo o varios—, para poder distinguir estimación de dato
        cargado. `solo` limita el barrido a ciertos ids.
        """
        if pct_anterior is None:
            viejos = None
        elif isinstance(pct_anterior, (int, float)):
            viejos = (float(pct_anterior),)
        else:
            viejos = tuple(float(x) for x in pct_anterior)
        ids = None if solo is None else {int(i) for i in solo}
        cambiados = 0
        for p in self.todos():
            if ids is not None and p.id not in ids:
                continue
            if p.costo_manual_ars:
                continue      # el total puesto a mano no depende del porcentaje
            nuevo = None
            if p.precio_usd and not (viejos is not None
                                     and self._envio_a_mano(p, viejos)):
                candidato = round(p.precio_usd * self.pct_envio(p), 2)
                if abs((p.costo_envio_usd or 0) - candidato) >= 0.01:
                    nuevo = candidato
            # Al precio del producto cargado a mano el porcentaje se le suma
            # arriba: cambia su costo total aunque el envío en dólares no se
            # mueva, así que hay que recalcularlo igual.
            if nuevo is None and not p.costo_producto_manual_ars:
                continue
            anterior = p.costo_envio_usd
            if nuevo is not None:
                p.costo_envio_usd = nuevo
            self._calcular(p)
            self._guardar(p, commit=False)
            if nuevo is not None:
                self._log(p.id, "envio", "costo_envio_usd", anterior, nuevo)
            cambiados += 1
        self.conn.commit()
        return cambiados

    def marcar_envio_gratis(self, pid: int,
                            valor: Optional[bool]) -> ProductoCatalogo:
        """Marca si Amazon manda este producto gratis a Argentina y recalcula.

        No alcanza con guardar la marca: `costo_envio_usd` está guardado, así
        que hay que volver a estimarlo con el porcentaje que ahora corresponde.
        Si el envío lo cargó una persona desde un checkout real, se respeta —la
        marca queda igual, para saber qué producto es, pero el costo no se pisa.
        """
        p = self.obtener(pid)
        if not p:
            raise ValueError("No existe ese producto.")
        anterior = p.envio_gratis_amazon
        nuevo = None if valor is None else bool(valor)
        # Los porcentajes de antes se calculan con la marca vieja puesta: si ya
        # cambiamos la marca, `pct_envio` devolvería el porcentaje nuevo y todo
        # envío estimado parecería cargado a mano.
        respeta_mano = self._envio_a_mano(p, self.pcts_envio())
        p.envio_gratis_amazon = nuevo
        if p.precio_usd and not p.costo_manual_ars and not respeta_mano:
            p.costo_envio_usd = round(p.precio_usd * self.pct_envio(p), 2)
        self._calcular(p)
        self._guardar(p)
        self._log(p.id, "envio", "envio_gratis_amazon", anterior, nuevo)
        return p

    @staticmethod
    def _monto_o_nada(valor) -> Optional[float]:
        """Un monto en pesos, o `None` para volver a estimarlo."""
        if valor in (None, "", 0):
            return None
        try:
            monto = float(valor)
        except (TypeError, ValueError):
            raise ValueError("El costo tiene que ser un número.")
        if monto <= 0:
            raise ValueError("El costo tiene que ser mayor que cero.")
        return monto

    def actualizar_costo_manual(self, pid: int,
                                costo_ars: Optional[float]) -> ProductoCatalogo:
        """Fija el costo TOTAL en pesos a mano, o lo saca para volver a estimarlo.

        Es el costo ya puesto en Argentina: producto + envío + importación, al
        dólar que se pagó. No lo mueve ni el tipo de cambio ni el porcentaje de
        envío, porque no es una estimación de nada.

        `None` o 0 vuelve al costo calculado. Recalcula el precio sugerido y el
        margen, que es para lo que sirve: saber a cuánto hay que vender con el
        costo de verdad.
        """
        p = self.obtener(pid)
        if not p:
            raise ValueError("No existe ese producto.")
        nuevo = self._monto_o_nada(costo_ars)
        anterior = p.costo_manual_ars
        p.costo_manual_ars = nuevo
        if nuevo is not None:
            # Una sola verdad: cargar el total deja sin sentido al precio del
            # producto suelto, que existe justamente para deducir ese total.
            p.costo_producto_manual_ars = None
        self._calcular(p)
        self._guardar(p)
        self._log(p.id, "costo", "costo_manual_ars", anterior, nuevo)
        return p

    def actualizar_costo_producto(self, pid: int,
                                  costo_ars: Optional[float]) -> ProductoCatalogo:
        """Fija el precio del producto en pesos, SIN el envío internacional.

        La diferencia con `actualizar_costo_manual` es lo que la herramienta
        hace después: acá le suma el envío + importación que corresponda según
        el producto tenga o no envío gratis de Amazon. El mismo número escrito
        sobre un producto tildado y sobre uno sin tildar da dos costos y dos
        precios sugeridos distintos, que es justamente lo que hay que ver.

        `None` o 0 lo saca y vuelve a estimarse todo.
        """
        p = self.obtener(pid)
        if not p:
            raise ValueError("No existe ese producto.")
        nuevo = self._monto_o_nada(costo_ars)
        anterior = p.costo_producto_manual_ars
        p.costo_producto_manual_ars = nuevo
        if nuevo is not None:
            p.costo_manual_ars = None
        self._calcular(p)
        self._guardar(p)
        self._log(p.id, "costo", "costo_producto_manual_ars", anterior, nuevo)
        return p

    def envio_efectivo(self, envio: Optional[float] = None) -> float:
        """Cuánto se está descontando por envío gratis: lo pedido, lo guardado a
        mano o lo de la configuración, en ese orden."""
        return self._cfg_efectivo(envio=envio).meli.envio_gratis_ars

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
               "ml_permalink", "video_youtube", "revisado_en",
               "costo_manual_ars", "envio_gratis_amazon",
               "costo_producto_manual_ars"]

    def _valores(self, p: ProductoCatalogo) -> list:
        """Los campos de `p` listos para el motor SQL.

        Los booleanos de tres estados viajan como 0/1/NULL: la columna es
        INTEGER y PostgreSQL no acepta un bool ahí.
        """
        vals = []
        for c in self._CAMPOS:
            v = getattr(p, c)
            vals.append(int(v) if isinstance(v, bool) else v)
        return vals

    def _fila_a_producto(self, row) -> ProductoCatalogo:
        d = dict(row)
        attrs = json.loads(d.pop("ml_attributes") or "{}")
        pics = json.loads(d.pop("pictures") or "[]")
        vids = json.loads(d.pop("videos", None) or "[]")
        # Las filas anteriores a la migración traen NULL en las columnas nuevas,
        # y el campo está declarado como texto: sin esto entra un None donde el
        # resto del código espera un str.
        d["video_youtube"] = d.get("video_youtube") or ""
        d["revisado_en"] = d.get("revisado_en") or ""
        # 0/1/NULL en la base, tres estados acá. NULL tiene que seguir siendo
        # None: "no lo miré" no es lo mismo que "no tiene envío gratis" aunque
        # los dos paguen igual.
        eg = d.get("envio_gratis_amazon")
        d["envio_gratis_amazon"] = None if eg is None else bool(eg)
        d.pop("creado", None); d.pop("actualizado", None)
        return ProductoCatalogo(ml_attributes=attrs, pictures=pics, videos=vids,
                                **{k: d[k] for k in d})

    def agregar(self, p: ProductoCatalogo) -> ProductoCatalogo:
        self._calcular(p)
        vals = self._valores(p)
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

    def _guardar(self, p: ProductoCatalogo, commit: bool = True) -> None:
        sets = ", ".join(f"{c} = ?" for c in self._CAMPOS)
        vals = self._valores(p)
        self.conn.execute(
            f"UPDATE catalogo SET actualizado = ?, ml_attributes = ?, pictures = ?, "
            f"videos = ?, {sets} WHERE id = ?",
            [_ahora(), json.dumps(p.ml_attributes), json.dumps(p.pictures),
             json.dumps(p.videos)] + vals + [p.id],
        )
        # `commit=False` es para guardar muchos productos en una sola
        # transacción; el que llama tiene que cerrarla.
        if commit:
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
