"""El panel es JavaScript suelto dentro del HTML: nadie lo compila.

Un error de sintaxis ahí no rompe ningún test de Python y sin embargo deja la
interfaz entera sin funcionar —la tabla no carga, los botones no responden—,
que es la falla más cara y la más fácil de no notar al editar el archivo.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PANEL = Path(__file__).resolve().parent.parent / "web" / "panel.html"


def _js() -> str:
    html = PANEL.read_text(encoding="utf-8")
    bloques = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
    assert bloques, "el panel no tiene ningún bloque <script>"
    return "\n;\n".join(bloques)


@pytest.mark.skipif(not shutil.which("node"), reason="node no está instalado")
def test_el_javascript_del_panel_compila(tmp_path):
    ruta = tmp_path / "panel.js"
    ruta.write_text(_js(), encoding="utf-8")
    r = subprocess.run(["node", "--check", str(ruta)],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"error de sintaxis en el JS del panel:\n{r.stderr}"


def test_los_botones_del_panel_tienen_su_handler():
    """Un id que quedó sin `onclick` es un botón muerto: se ve, se aprieta y no
    pasa nada. Pasó con el diagnóstico de códigos."""
    html = PANEL.read_text(encoding="utf-8")
    js = _js()
    ids = set(re.findall(r'<button[^>]*\bid="([^"]+)"', html))
    # Se cablean de las dos formas: `$("x").onclick = …` y
    # `$("x").addEventListener("click", …)`. Basta con que el id se use.
    sin_handler = [i for i in ids if f'"{i}"' not in js]
    assert not sin_handler, f"botones sin acción: {sorted(sin_handler)}"


def test_aplicar_precios_manda_los_mismos_parametros_que_simular():
    """Armar el cuerpo a mano ya se comió las dos cotizaciones una vez: se
    simulaba con dólar 1600/3200 y se aplicaba sin ellas, así que a
    MercadoLibre le llegaba el precio que la publicación ya tenía."""
    js = _js()
    aplicar = js[js.index("precios/aplicar"):]
    antes = js[:js.index("precios/aplicar")]
    # El cuerpo del aplicar tiene que salir de la misma función que el del
    # simular, no de un objeto escrito a mano al lado de la llamada.
    assert "cuerpo = prCuerpo()" in antes or "cuerpo=prCuerpo()" in antes, \
        "el aplicar no reutiliza prCuerpo(): puede volver a perder campos"
    assert "margen_pct:" not in aplicar[:200], \
        "el aplicar arma el cuerpo a mano otra vez"


def test_las_llamadas_tienen_tope_de_tiempo():
    """`fetch` no vence solo. Sin un tope, un servidor colgado deja el panel con
    el cartel de «Actualizando…» para siempre y no hay forma de distinguirlo de
    que siga trabajando —que es exactamente lo que se vio con los precios."""
    js = _js()
    assert "AbortController" in js and "ctrl.signal" in js, \
        "el panel llama sin tope de tiempo: un cuelgue queda esperando para siempre"


def test_el_aplicar_precios_muestra_que_sigue_vivo():
    """El cartel tiene que moverse mientras se espera. Un número quieto no dice
    si el servidor trabaja o se frenó."""
    js = _js()
    aplicar = js[js.index('$("pr-aplicar").onclick'):js.index('$("lote-borrar")')]
    assert "setInterval" in aplicar, \
        "el aplicar no refresca el cartel: no se ve si sigue vivo"
    assert "pr-frenar" in aplicar, "el aplicar no se puede frenar"
