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
  - **Filtro "solo sets LEGO"**: las búsquedas de Amazon devuelven además
    patrocinados, accesorios de terceros ("luces LED *compatibles con* Lego",
    vitrinas, organizadores) y productos de otras marcas. El filtro los
    descarta en dos momentos: en el botón (para no gastar pedidos a Amazon en
    basura) y al procesar, con el título y la marca reales de la ficha. También
    descarta lo muy barato (llaveros, polybags) según un precio mínimo
    (USD 25 por defecto). Ver `filtros.py`.

### Procesar la cola desde tu PC (importante)

**Amazon bloquea las IPs de los servidores en la nube**: desde Render la
lectura de fichas falla siempre (403), aunque el encolado funcione bien. Desde
una conexión hogareña, en cambio, anda.

Como la base es compartida, encolás desde donde quieras y **procesás desde tu
casa**. En la carpeta del proyecto:

```bash
set DATABASE_URL=postgresql://...     # el mismo valor que pusiste en Render
py procesar_cola.py                   # procesa hasta 25, con 3 s entre cada uno
py procesar_cola.py --maximo 40 --pausa 4
py procesar_cola.py --reintentar      # retomar los que quedaron frenados
```

Los productos cargados aparecen en el panel de la nube al instante. El script
frena solo si Amazon empieza a limitar.
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
   - `BRICKSET_API_KEY` = tu clave de [brickset.com/api](https://brickset.com/api/)
     (**opcional pero muy recomendada si vendés LEGO**, ver abajo).
   - `YOUTUBE_API_KEY` = clave de YouTube Data API v3 (*opcional*, para buscar
     el video del producto, ver abajo).
   - `SCRAPER_API_KEY` = clave de [scraperapi.com](https://www.scraperapi.com/)
     (*opcional*, para que la carga en lote funcione desde el servidor, ver
     abajo).
4. **Create Web Service** → esperá a que termine el build.
5. Render te da una URL `https://arbitraje-meli.onrender.com`. Abrila desde
   donde quieras, ingresás la contraseña y usás el panel. Para conectar
   MercadoLibre, el flujo es el mismo (**Conectar → Pegar código**).

### El código de barras (GTIN) y por qué importa

MercadoLibre exige el código de barras en varias categorías: sin él, la
publicación se rechaza. La herramienta lo busca sola, probando de la fuente más
confiable a la menos:

| # | Fuente | Cómo funciona | ¿Sirve desde la nube? |
|---|--------|---------------|------------------------|
| 1 | Tu catálogo | Otro producto tuyo con el mismo ASIN ya lo tiene | Sí, instantáneo |
| 2 | Catálogo de MercadoLibre | Su ficha del producto, por número o por nombre | **Sí** |
| 3 | UPCitemdb | Base genérica de códigos, por nombre | Sí (límite diario) |
| 4 | Amazon / buscador web | Lee la página del producto | **No**: bloquea servidores |

La última fila es el problema: **Amazon rechaza las IP de datacenter**, así que
desde Render esa fuente casi nunca responde.

#### El botón que lo resuelve para cualquier rubro

`/codigos/asistido` arma un bookmarklet que **lee las fichas de Amazon desde tu
navegador**, con tu conexión hogareña. Amazon te responde normal a vos aunque
rechace al servidor. Lo abrís desde cualquier página de amazon.com, va de a una
ficha con pausa entre cada una, corta solo si Amazon pide verificación y guarda
lo que consiguió.

Sirve para **cualquier producto**, porque lee el código de la propia ficha: LEGO,
herramientas, electrónica, lo que sea. Es el mismo principio que los otros
botones de la herramienta: leer lo que vos ya podés ver.

#### Últimos recursos

- **Cargar códigos de barras a mano** (en el panel): se pegan líneas
  `número de set;código` o `ASIN;código` y se aplican en lote. Acepta pegar
  desde una planilla.
- **`BRICKSET_API_KEY`** *(opcional, solo LEGO)*: si tenés una API key de
  [brickset.com/api](https://brickset.com/api/), se usa como fuente extra. No
  hace falta configurarla.

### Actualizar los precios de lo ya publicado

El dólar se mueve y los costos quedan viejos. **💲 Actualizar precios de lo
publicado** recalcula el costo con la cotización de hoy y vuelve a fijar el
precio en MercadoLibre, en lote.

Va en dos pasos, y es a propósito:

1. **Ver qué cambiaría** — muestra, publicación por publicación, el precio
   actual, el nuevo, la variación y el margen que queda. **No toca nada**: ni
   guarda ni llama a MercadoLibre.
2. **Aplicar en MercadoLibre** — recién ahí cambia los precios, con una
   confirmación de por medio.

Cambiar el precio de una publicación viva no se deshace con un botón: los
compradores ya la están viendo.

El campo de margen es opcional. Vacío, cada producto conserva el suyo y solo se
actualiza por el dólar. Con un número, ese margen pasa a ser el de todos los que
se actualicen.

Si MercadoLibre rechaza un precio, **no se guarda en el catálogo**: no puede
figurar acá un precio que la publicación no tiene. El error se muestra con el
nombre del producto.

### Cargar en lote sin que Amazon frene (`SCRAPER_API_KEY`)

**La API oficial de Amazon no es una opción.** PA-API 5.0 dejó de aceptar
clientes nuevos y se discontinúa en mayo de 2026; además exigía ser Amazon
Associate **con ventas de afiliado hechas**. Comprar en Amazon para revender no
califica. La SP-API es para vendedores *de* Amazon, tampoco aplica.

Lo que sí funciona es leer la página a través de un proxy que ponga IP
residencial. Con `SCRAPER_API_KEY` configurada, la importación va por
[ScraperAPI](https://www.scraperapi.com/) en vez de ir directo, y **la cola deja
de frenarse desde el servidor**. Sin la clave, todo sigue igual que antes: se
lee directo, que es lo que sirve corriendo la herramienta en tu PC.

El plan gratuito da **1.000 créditos por mes sin tarjeta**, y cada producto de
Amazon gasta 5: **unos 200 productos mensuales gratis**.

Yendo por proxy, la pausa entre productos baja a 0,2 s: existía para no golpear
a Amazon, y con proxy eso es trabajo del proxy. El lote termina mucho más
rápido.

Los errores se explican distinto según de dónde vengan: `401` es la clave mal
puesta, `403` es haberse quedado sin créditos del mes. No es lo mismo que
Amazon nos bloquee.

### El video de la publicación

MercadoLibre acepta **un video de YouTube** por publicación (campo `video_id`).
No acepta archivos: los videos que trae Amazon están en su propio CDN, así que
**no se pueden publicar**. Se guardan igual y se ven como enlaces en el editor,
pero el que va a la publicación es otro.

Con `YOUTUBE_API_KEY` configurada, la herramienta busca sola el video oficial:
**🎬 Buscar videos** en la barra de lote, o el botón dentro del editor. También
lo completa el agente al preparar cada producto.

El filtro es deliberadamente estricto: **solo acepta videos del canal de la
marca**. Pedir la marca en el título no alcanzaría, porque una reseña de un
tercero también dice "LEGO 21042" ahí, y ese es justo el video que no queremos
en la ficha. Por eso encuentra pocos, y está bien: quedarse sin video es gratis,
publicar el video de otro producto no.

#### Canales de terceros elegidos a mano

Cuando el fabricante no tiene video del producto, se aceptan **canales de
terceros elegidos para esa marca**. Para LEGO vienen dos: **AustrianBrickFan**
y **Brick Studio Architect**.

Es una segunda opción, no un empate: si el canal oficial tiene el video, gana
el oficial aunque YouTube lo devuelva más abajo. Y en un canal de terceros se
exige **siempre el número de set**, aunque el producto no lo tenga en la ficha:
esos canales suben de todas las marcas y sin número no hay forma de saber de
cuál es el video. El panel avisa cuando el video no es del canal oficial, para
poder mirarlo antes de publicar.

Los canales van **atados a la marca** a propósito: un video de AustrianBrickFan
que mencione "12345" no dice nada sobre un Fisher-Price con ese número.

Se pueden agregar más sin tocar el código, con `CANALES_VIDEO_CONFIABLES` en
formato `marca=canal|otro canal;otra marca=canal`. Por ejemplo:

```
LEGO=Tercer Canal;Playmobil=Canal De Playmobil
```

Para sacar la clave: [consola de Google Cloud](https://console.cloud.google.com/)
→ crear un proyecto → habilitar **YouTube Data API v3** → Credenciales → Crear
credenciales → Clave de API. Es autoservicio y gratis; no hay que pedir permiso
ni explicar para qué. El nivel gratuito da 10.000 unidades por día y cada
búsqueda cuesta 100, o sea ~100 búsquedas diarias.

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
