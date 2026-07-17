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
| `link_amazon` | *(opcional)* link de referencia |

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
