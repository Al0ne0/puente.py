import http.server
import inspect
import json
import os
import socket
import threading
import traceback
import urllib.parse
import webbrowser

JS_PUENTE = """
<script>
let _pyVars = [];

function _syncDOM(vars) {
    _pyVars = Object.keys(vars);
    for (const [name, val] of Object.entries(vars)) {
        const el = document.getElementById(name);
        if (!el) continue;

        if (el.tagName === "INPUT" && el.type === "checkbox") {
            el.checked = Boolean(val);
        } else if (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) {
            if (document.activeElement !== el) el.value = val ?? "";
        } else {
            const contenido = (typeof val === "object" && val !== null)
                ? JSON.stringify(val)
                : (val ?? "");
            el.innerHTML = contenido;
        }
    }
}

function _leerInputs() {
    const datos = {};
    _pyVars.forEach(name => {
        const el = document.getElementById(name);
        if (!el) return;
        if (el.tagName === "INPUT" && el.type === "checkbox") {
            datos[name] = el.checked;
        } else if (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) {
            datos[name] = el.value;
        }
    });
    return datos;
}

document.addEventListener("DOMContentLoaded", async () => {
    const res = await fetch("/_estado");
    const data = await res.json();
    _syncDOM(data.vars);

    data.funcs.forEach(fn => {
        window[fn] = async (...args) => {
            const r = await fetch("/_llamar", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    func: fn,
                    args: args,
                    vars: _leerInputs()
                })
            });
            const respuesta = await r.json();
            if (respuesta.error) {
                console.error(`[puente] Error en ${fn}():\\n` + respuesta.error);
            }
            if (respuesta.vars) {
                _syncDOM(respuesta.vars);
            }
        };
    });
});
</script>
"""


class _ServidorReutilizable(http.server.HTTPServer):
    allow_reuse_address = True


def _buscar_puerto_libre(puerto_inicial):
    for puerto in range(puerto_inicial, puerto_inicial + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("localhost", puerto))
                return puerto
            except OSError:
                continue
    raise RuntimeError("No se encontró un puerto libre disponible.")


def _es_serializable(valor):
    try:
        json.dumps(valor)
        return True
    except (TypeError, ValueError):
        return False


def iniciar(archivo_html="index.html", contexto=None, puerto=8000, abrir_navegador=True):
    """Levanta el servidor local y conecta las variables/funciones de Python con el HTML."""
    if contexto is None:
        contexto = inspect.currentframe().f_back.f_globals

    tipos_validos = (int, float, str, bool, list, dict, type(None))
    modulo_origen = contexto.get("__name__")

    def obtener_estado():
        variables = {
            k: v for k, v in contexto.items()
            if not k.startswith("_") and isinstance(v, tipos_validos) and _es_serializable(v)
        }
        funciones = [
            k for k, v in contexto.items()
            if inspect.isfunction(v) and v.__module__ == modulo_origen and not k.startswith("_")
        ]
        return {"vars": variables, "funcs": funciones}

    class Manejador(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            ruta = urllib.parse.urlparse(self.path).path

            if ruta == "/_estado":
                self.send_response(200)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(obtener_estado()).encode("utf-8"))
                return

            # Inyecta el puente en index.html o en cualquier otra página .html
            if ruta == "/" or ruta.endswith(".html"):
                archivo = archivo_html if ruta == "/" else ruta.lstrip("/")
                if not os.path.isfile(archivo):
                    self.send_error(404, f"No se encontró el archivo: {archivo}")
                    return

                with open(archivo, "r", encoding="utf-8") as f:
                    html = f.read()

                if "</body>" in html:
                    html = html.replace("</body>", JS_PUENTE + "\n</body>")
                else:
                    html += JS_PUENTE

                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
                return

            # Cualquier otro archivo (CSS, imágenes, fuentes, JS)
            super().do_GET()

        def do_POST(self):
            if self.path != "/_llamar":
                self.send_error(404)
                return

            largo = int(self.headers.get("Content-Length", 0))
            datos = json.loads(self.rfile.read(largo).decode("utf-8"))

            for k, v in datos.get("vars", {}).items():
                contexto[k] = v

            error = None
            nombre_fn = datos.get("func")
            args = datos.get("args", [])
            func = contexto.get(nombre_fn)

            if callable(func):
                try:
                    func(*args)
                except Exception:
                    error = traceback.format_exc()
                    print(f"\n[puente] Error ejecutando {nombre_fn}():\n{error}")

            estado = obtener_estado()
            respuesta = {"vars": estado["vars"], "error": error}

            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(respuesta).encode("utf-8"))

        def log_message(self, format, *args):
            pass

    puerto_real = _buscar_puerto_libre(puerto)
    url = f"http://localhost:{puerto_real}"
    servidor = _ServidorReutilizable(("localhost", puerto_real), Manejador)

    print(f"Puente activo en {url} (Presiona Ctrl+C para detener)")
    if abrir_navegador:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido correctamente.")
    finally:
        servidor.server_close()