# Arbitraje Amazon → MercadoLibre Argentina

Detecta productos que se pueden **traer desde Amazon (EEUU) y revender en
MercadoLibre Argentina con margen**, considerando *todos* los costos: importación
(régimen courier o general) y costos de venta en MeLi (comisiones, IVA sobre
comisión, IIBB, Ganancias, envío). Te devuelve el **margen neto en pesos y en %**
y rankea las oportunidades.

> ⚠️ **Aviso.** Las alícuotas, comisiones y reglas de aduana cambian seguido y
> dependen de tu situación fiscal y jurisdicción. Los valores por defecto son
> estimaciones razonables marcadas con `VERIFICAR` en el código. Chequealos
> antes de decidir con plata real. Esto es una herramienta de análisis, no
> asesoramiento impositivo ni aduanero.

## Cómo funciona

```
Amazon (precio, peso)                 MercadoLibre (precio de venta)
        │                                       │
        ▼                                       ▼
 costo puesto en Argentina          neto de la venta (comisiones + impuestos)
 (courier o general)                        │
        └──────────────► MARGEN = neto_venta − costo_puesto ◄─────────┘
                                    rankeado por margen
```

El dato de Amazon entra por un **proveedor enchufable**:

- **ManualProvider** (default): cargás precio y peso a mano o desde un CSV.
  Gratis, sin fricción legal, ideal para validar la idea.
- **RainforestProvider** (stub): API paga que trae precios de Amazon
  automáticamente. Se activa con una API key (`RAINFOREST_API_KEY`).

Amazon no ofrece una API pública y gratuita de búsqueda, por eso el MVP arranca
con carga manual y deja la puerta abierta a automatizar con una API paga.

## Instalación

```bash
pip install -r requirements.txt
```

## Uso rápido (CLI)

```bash
# Evaluar los productos del CSV bajo régimen courier
python -m arbitraje.cli --csv data/productos.example.csv

# Comparar courier vs general y exportar el ranking
python -m arbitraje.cli --csv data/productos.example.csv \
    --regimen courier general --export resultados.csv

# Sin tocar la API de MeLi (usa la columna precio_meli_manual del CSV)
python -m arbitraje.cli --csv data/productos.example.csv --sin-api
```

### Formato del CSV

| columna | qué es |
|---|---|
| `nombre` | nombre del producto |
| `query_meli` | término de búsqueda en MercadoLibre |
| `precio_amazon_usd` | precio en Amazon (sin envío) |
| `peso_kg` | peso, para estimar el flete courier |
| `categoria` | categoría de comisión MeLi (`electronica`, `computacion`, `hogar`, `default`) |
| `arancel_pct` | arancel NCM (solo aplica en régimen general) |
| `precio_meli_manual` | *(opcional)* precio de venta fijado a mano; tiene prioridad sobre la API |
| `precio_landed_usd` | *(opcional)* costo total puesto en Argentina en USD según el checkout de Amazon (el "Total" con envío + importación). Si está, se usa directo y se saltea la estimación de aduana — es el dato más preciso. |
| `cantidad` | *(opcional)* unidades que comprás juntas (default 1); reparte el envío entre todas |
| `precio_landed_lote_usd` | *(opcional)* `Total` de Amazon al pedir `cantidad` unidades; el costo por unidad = este total / cantidad |
| `link_amazon` | *(opcional)* link de referencia |

### Dólar tarjeta

Cuando comprás en Amazon con una tarjeta argentina no pagás el dólar oficial,
sino **oficial + percepciones** ("dólar tarjeta"). Ese recargo se aplica solo a
la compra (la venta en MeLi es en pesos). Se configura con `recargo_tarjeta_pct`
(default `0.30`) o desde el CLI:

```bash
python -m arbitraje.cli --csv data/productos.example.csv --sin-api --recargo-tarjeta 0.30
```

### Costo puesto que informa Amazon (landed)

Si comprás por **AmazonGlobal**, el checkout te muestra el `Total` con envío e
importación ya incluidos. Cargá ese número en `precio_landed_usd` y la app lo
usa directo (modo `landed`), sin estimar aduana: es lo más preciso.

### Compra por lote (amortizar el envío)

Comprar varias unidades en un mismo envío reparte el costo fijo de envío entre
todas y **baja el costo por unidad**. Se modela con dos columnas:

- `cantidad`: cuántas unidades comprás juntas.
- `precio_landed_lote_usd`: el `Total` que muestra Amazon al pedir esa cantidad
  (con envío e importación del lote entero). El costo por unidad =
  `precio_landed_lote_usd / cantidad`.

La app muestra el margen **por unidad** y el **margen total del lote**. Ejemplo:
un kit que solo daba USD 48/unidad comprando de a 1, comprando 6 baja a
USD 27/unidad.

> ⚠️ Verificá siempre que el producto de Amazon y el de MercadoLibre sean
> **exactamente el mismo** (misma versión, mismo contenido). Comparar productos
> distintos — p. ej. un accesorio barato contra el producto completo — da
> márgenes falsos. Es el error más común del arbitraje.

## API local — tu propio "Rainforest" personal

Servicio self-hosted que junta datos de productos con **fuentes gratuitas y
legítimas** y arma tu propio histórico de precios. En vez de scrapear Amazon
(viola sus términos y requiere infraestructura carísima), automatiza la
**captura**: vos navegás como siempre y un clic guarda lo que estás viendo.

```bash
python -m api.server     # abre http://localhost:8321
```

1. Entrá a `http://localhost:8321` y arrastrá el botón **"➜ Capturar producto"**
   a tu barra de favoritos (una sola vez).
2. Navegando **Amazon**, en la página de un producto tocá el botón: captura
   ASIN, título y precio, y te pregunta el total puesto en Argentina (opcional).
3. Navegando **MercadoLibre**, en la publicación equivalente tocá el botón:
   captura el precio y te pregunta a qué ASIN corresponde.
4. Cada captura queda fechada en SQLite → se arma solo tu **histórico de precios**.
5. Exportá y evaluá todo con el motor de arbitraje:

```bash
curl http://localhost:8321/export.csv -o export.csv
python -m arbitraje.cli --csv export.csv --sin-api
```

Endpoints: `/productos`, `/search?q=`, `/product/{asin}`, `/history/{asin}`,
`/export.csv`. Mismo espíritu que Rainforest API, pero corriendo en tu PC,
gratis y sin scraping.

## Dashboard web (opcional)

```bash
pip install streamlit pandas
streamlit run app/dashboard.py
```

Interfaz visual para cargar productos, elegir régimen y ver el ranking con
colores. Usa el mismo motor de cálculo que el CLI.

## Regímenes de importación

- **Courier / Puerta a Puerta** (default): régimen simplificado. Hasta
  USD 3.000 por envío, primeros USD 400/año exentos, impuesto único del 50%
  sobre el excedente. Es lo realista para productos sueltos.
- **General / importador registrado**: aranceles NCM + despachante + IVA +
  percepciones. Conviene para volumen; encarece un producto suelto.

La app puede calcular y **comparar ambos** (`--regimen courier general`).

## Configuración

Los defaults viven en `arbitraje/config.py`. Para usar tus propios números sin
tocar código, creá un JSON y pasalo con `--config`:

```json
{
  "tipo_cambio_oficial": 1350.0,
  "recargo_tarjeta_pct": 0.30,
  "courier": { "flete_usd_por_kg": 60.0, "franquicia_anual_usd": 400.0 },
  "meli": { "iibb_pct": 0.035, "costo_envio_estimado_ars": 7000 }
}
```

```bash
python -m arbitraje.cli --csv data/productos.example.csv --config mi_config.json
```

## Estructura del proyecto

```
arbitraje/
  config.py        parámetros ajustables (TC, alícuotas, comisiones)
  models.py        estructuras de datos
  importacion.py   costo puesto en Argentina (courier / general)
  meli.py          cliente API MercadoLibre + costos de venta
  amazon/          proveedores de datos de Amazon (manual / Rainforest)
  evaluador.py     orquesta el pipeline y rankea oportunidades
  cli.py           interfaz de línea de comandos
app/dashboard.py   dashboard web opcional (Streamlit)
data/              CSV de ejemplo
tests/             tests del motor de cálculo (no tocan la red)
scripts/           script original preservado
```

## Tests

```bash
pytest
```

Los tests cubren los cálculos de importación y de venta sin depender de la red.

## Roadmap

1. **MVP (hecho):** motor de cálculo + CLI + dashboard, carga manual/CSV,
   régimen courier y general, ranking por margen.
2. **Automatizar Amazon:** activar `RainforestProvider` (o Keepa) para buscar
   productos y precios solos.
3. **Matching automático** Amazon ↔ MercadoLibre (hoy la referencia de MeLi es
   la mediana de la búsqueda; conviene confirmar el match real).
4. **Agente periódico:** correr solo, guardar histórico y avisar oportunidades
   nuevas de buen margen.
