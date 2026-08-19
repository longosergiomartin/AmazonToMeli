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

### Si no podés acceder a localhost

1. Verificá que el servidor esté corriendo: la consola debe decir
   `Uvicorn running on http://127.0.0.1:8321`.
2. Probá `http://127.0.0.1:8321` en vez de `localhost` (los proxies
   corporativos a veces resuelven mal el nombre `localhost`).
3. Probá un puerto estándar: `python -m api.server --puerto 8080` y entrá a
   `http://127.0.0.1:8080`. El bookmarklet se adapta solo al host/puerto con
   el que entraste.
4. Si estás en una **computadora del trabajo** y el bloqueo viene del firewall
   o antivirus corporativo, no intentes desactivarlos: pedile la excepción al
   área de IT, o usá la app en tu PC personal.

## Panel de publicación en MercadoLibre

Convierte los productos identificados en Amazon en **publicaciones de tu cuenta
de MercadoLibre**, con costo en pesos, precio sugerido por margen deseado,
vista previa y **publicación solo tras tu aprobación manual**.

```bash
python -m api.server        # abrí http://localhost:8321/panel
```

Qué hace:

- **Importar muchos de una vez**: buscás lo que te interesa en Amazon (ej.
  "LEGO Star Wars"), tocás el botón **"Encolar toda la página"** de tu barra de
  favoritos y se encolan todos los productos de esa página. La cola los procesa
  **de a uno y despacio**, autocompletando lo que se puede leer, y los deja como
  **borradores** para que los revises.
  - Si Amazon empieza a limitar, la cola **se detiene y guarda el progreso**;
    se continúa después con *Reintentar bloqueados*. No se insiste ni se
    intenta esquivar el bloqueo: además de corresponder, protege tu cuenta de
    comprador, que es la que necesitás para comprar.
  - También podés pegar una lista de links o ASIN a mano.
- **Registrar** un producto de Amazon: link, ASIN, marca, modelo, precio USD,
  peso, costo de envío, disponibilidad.
- **Traer datos desde el link de Amazon**: con un botón se autocompletan ASIN,
  título, marca, precio, peso, **descripción y fotos** (mejor desde una PC
  hogareña; el "Total landed" con envío+importación se agrega del checkout).
  Queda casi todo listo para revisar y publicar.
- **Costo total en pesos** automático (tipo de cambio + dólar tarjeta + envío +
  importación) reutilizando el motor de arbitraje.
- **Precio sugerido** a partir de tu *margen deseado* (despeja comisión, IVA,
  IIBB, Ganancias y envío de MeLi).
- **Borrador local + vista previa** con categoría y atributos obligatorios; se
  **publica recién cuando lo aprobás** (nunca en un solo paso).
- **Editar precio y stock**, **pausar/reactivar**, **alerta de margen
  insuficiente** e **historial de cambios** por producto.
- **Días de preparación** (default **25**): se publican como tiempo de
  disponibilidad (`MANUFACTURING_TIME`), y MercadoLibre los suma a la fecha de
  entrega ("el vendedor necesita N días para tener listo el producto"). Ideal
  para dropshipping. Editable por producto.

> La primera versión **no compra en Amazon** ni **publica sin aprobación**.

### Conectar tu cuenta (OAuth)

1. Creá una aplicación en https://developers.mercadolibre.com.ar/ y anotá el
   **App ID** (client id) y la **Secret Key**.
2. Configurá la app así:
   - **Redirect URI**: `https://oauth.pstmn.io/v1/callback`
     (MercadoLibre exige HTTPS y no acepta `localhost` ni IPs locales en el
     authorize. Esta URL pública de callback solo sirve para leer el `?code=`;
     después completás con **Pegar código**. En producción usá tu dominio.)
   - **Flujos OAuth**: tildá **Authorization Code** y **Refresh Token**.
   - **Negocios**: tildá **Mercado Libre**.
   - **Permisos**: *Usuarios* y *Publicación y sincronización* → **Lectura y
     escritura**. El resto podés dejarlo en *Sin acceso*.
3. Antes de levantar el servidor, exportá tus credenciales (no se commitean):

   ```bash
   export MELI_CLIENT_ID="tu_app_id"
   export MELI_CLIENT_SECRET="tu_secret_key"
   export MELI_REDIRECT_URI="https://127.0.0.1:8321/oauth/callback"
   python -m api.server
   ```
4. En el panel, tocá **Conectar** → autorizás en MercadoLibre. Como el redirect
   es HTTPS y en local no hay servidor HTTPS, el navegador va a mostrar un
   error al volver — **es normal**: copiá la URL completa de la barra de
   direcciones (la que tiene `?code=...`), tocá **Pegar código** en el panel y
   pegala. Listo, el token queda guardado y se renueva solo.

Sin credenciales, el panel igual sirve para registrar productos, calcular
costos, precios y márgenes y armar borradores; solo la publicación real y la
predicción de categoría requieren la sesión de MercadoLibre.

## Acceder al panel desde otra máquina (ej: el trabajo)

El panel es un servidor: corre en tu PC de casa y desde otra máquina se accede
por una **URL pública** vía túnel. **Antes de exponerlo a internet, ponele
contraseña** (controla tu cuenta de MercadoLibre):

```bash
set PANEL_PASSWORD=una_clave_larga_y_secreta   # Windows (cmd)
# export PANEL_PASSWORD=...                     # Mac/Linux
python -m api.server
```

Con eso, el panel pide usuario/contraseña (cualquier usuario; la clave es la de
`PANEL_PASSWORD`).

### Túnel con Cloudflare (gratis, sin abrir puertos)

1. Descargá `cloudflared` (https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/).
2. Con el server corriendo, en otra terminal:
   ```bash
   cloudflared tunnel --url http://localhost:8321
   ```
3. Te da una URL `https://algo.trycloudflare.com`. Abrila desde el trabajo,
   ingresás la contraseña y usás el panel normalmente.

> Requisitos: tu PC de casa tiene que quedar **encendida y con el server (y el
> túnel) corriendo**. La URL es pública: mantené la contraseña fuerte y privada.
> Tu Secret Key de MercadoLibre nunca sale de la PC que corre el server.
> Si tu red laboral filtra dominios, puede bloquear también el túnel; en ese
> caso conviene un deploy en la nube (Render/Railway) con la misma contraseña.

## Usar el panel desde la web (deploy en la nube)

Si querés usar el panel **desde cualquier navegador sin instalar nada** (ej: una
PC del trabajo sin Python), subilo a la nube. Todo el deploy se hace **desde el
navegador** en [Render](https://render.com) (plan gratis para probar).

1. Entrá a https://render.com y creá una cuenta (podés usar tu cuenta de GitHub).
2. **New → Web Service** → conectá el repo `longosergiomartin/AmazonToMeli`.
   (El repo ya trae `render.yaml`, así que Render detecta la configuración.)
3. En **Environment / Variables**, cargá:
   - `PANEL_PASSWORD` = una clave fuerte (**obligatoria**: sin ella el panel
     queda abierto a cualquiera).
   - `MELI_CLIENT_ID` = tu App ID.
   - `MELI_CLIENT_SECRET` = tu Secret Key.
   - `MELI_REDIRECT_URI` = `https://oauth.pstmn.io/v1/callback` (ya viene puesta).
4. **Create Web Service** → esperá a que termine el build.
5. Render te da una URL `https://arbitraje-meli.onrender.com`. Abrila desde
   donde quieras, ingresás la contraseña y usás el panel. Para conectar
   MercadoLibre, el flujo es el mismo (**Conectar → Pegar código**).

> Notas del plan gratis:
> - El servicio **se duerme** tras un rato sin uso; la primera carga puede
>   tardar ~30-60 s en despertar.
> - Tu Secret Key queda guardada como variable en Render, no en el código.

### Que no se pierda la sesión (base de datos permanente)

El disco de Render en plan gratis es **efímero**: cada vez que el servicio se
duerme o se redeploya, se borra el archivo SQLite y con él **la sesión de
MercadoLibre, el catálogo y el historial**. Por eso hay que reconectar y pegar
el código una y otra vez.

La solución es guardar los datos en un **Postgres externo** (gratis y
permanente). Una vez configurado, **la conexión con MercadoLibre queda
enganchada sola**: el token se renueva solo y no hay que pegar más el código.

1. Creá una base gratuita en [Neon](https://neon.tech) (o Supabase). Te dan una
   *connection string* así:
   `postgresql://usuario:clave@host.neon.tech/basedatos?sslmode=require`
2. En Render → tu servicio → **Environment** → agregá la variable:
   - `DATABASE_URL` = esa connection string.
3. **Save** → Render redeploya. Entrá al panel, conectá MercadoLibre **una
   última vez**, y listo: queda conectado para siempre.

El panel muestra un aviso amarillo cuando los datos **no** están guardados de
forma permanente, y lo oculta cuando `DATABASE_URL` está bien configurada.
Localmente (sin `DATABASE_URL`) sigue usando SQLite, que en tu PC ya es
permanente.

Alternativa sin nube: dejar el panel en tu PC y exponerlo por un **túnel**
(ver la sección anterior).

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
