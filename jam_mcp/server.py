"""Servidor MCP de Jam: el mismo grafo como texto en Unreal, Godot y Unity.

Unreal conserva su puerta HTTP local (jam.web, 8790) y el canvas del editor. Con Godot/Unity,
EditorMotor guarda el texto en esta sesión y ejecuta el núcleo de Jam fuera del motor, por TCP.
JSON-RPC UTF-8 por stdio, un mensaje por línea; stdout sólo protocolo, diagnósticos a stderr.
Sin dependencias adicionales.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from jam_mcp import __version__
from jam_mcp.motor import EditorMotor, ErrorMotor, JAM_DEFECTO

PROTOCOLO = "2025-11-25"
NOMBRE_SERVIDOR = "jam-mcp"
URL_DEFECTO = "http://127.0.0.1:8790"

INSTRUCCIONES = """\
Jam (plugin de Unreal) crea escenas y geometría con un grafo de nodos. El grafo abierto en el editor \
se lee y se escribe como TEXTO, una línea por nodo: `nombre = verbo @entrada clave=valor`. \
Flujo: jam_help sin argumentos (sintaxis y categorías) → jam_read_graph (texto + version) → \
editá el texto → jam_apply_graph con esa version (run=true para ejecutarlo) → leé los errores por \
línea y corregí. Lo que aplicás aparece en el canvas que ve el humano; si él editó entretanto, \
jam_apply_graph devuelve conflict con el texto nuevo: rehacé tu cambio sobre ése. Un Run deja un \
Preview en la escena: jam_preview bake lo fija, discard lo borra."""

_TEXTO = {"type": "string"}
HERRAMIENTAS = [
    {
        "name": "jam_read_graph",
        "title": "Leer el grafo abierto",
        "description": ("El grafo abierto en el Graph de Jam, como texto canónico, y su `version` "
                        "(pasala a jam_apply_graph para no pisar ediciones del humano)."),
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
                        "openWorldHint": False},
    },
    {
        "name": "jam_apply_graph",
        "title": "Aplicar texto al grafo",
        "description": ("Reemplaza el grafo abierto por `text` (el grafo ENTERO, no un parche) y lo "
                        "muestra en el canvas. Con `run`, además lo ejecuta (puede poner cosas en la "
                        "escena, como Preview). Devuelve el texto canónico, los errores con su línea "
                        "y, si corrió, el estado de cada nodo."),
        "inputSchema": {
            "type": "object", "additionalProperties": False, "required": ["text"],
            "properties": {
                "text": {"type": "string", "description": "el grafo completo, una línea por nodo"},
                "version": {"type": "string",
                            "description": "la de jam_read_graph; si el grafo cambió, hay conflicto"},
                "run": {"type": "boolean", "default": False},
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False,
                        "openWorldHint": False},
    },
    {
        "name": "jam_help",
        "title": "Ayuda de Jam",
        "description": ("Sin `query`: la sintaxis del texto y las categorías de verbos. Con un verbo: "
                        "su firma (entrada, salida, parámetros y defaults). Con otra palabra o una "
                        "categoría: los verbos que coinciden."),
        "inputSchema": {"type": "object", "additionalProperties": False,
                        "properties": {"query": _TEXTO}},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
                        "openWorldHint": False},
    },
    {
        "name": "jam_preview",
        "title": "Fijar o descartar el Preview",
        "description": ("Lo que dejó el último Run en la escena: `bake` lo fija (actores y assets "
                        "quedan), `discard` lo borra."),
        "inputSchema": {"type": "object", "additionalProperties": False, "required": ["action"],
                        "properties": {"action": {"enum": ["bake", "discard"]}}},
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False,
                        "openWorldHint": False},
    },
]
_NOMBRES = {h["name"] for h in HERRAMIENTAS}


class ErrorHerramienta(Exception):
    """Un error que el agente puede corregir o que tiene que contarle al humano."""


class Editor:
    """El editor de Unreal con Jam, del otro lado de `POST /api/<función>`."""

    def __init__(self, url: str = URL_DEFECTO, timeout: float = 600.0) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout

    def llamar(self, funcion: str, *args: str) -> str:
        pedido = urllib.request.Request(
            f"{self.url}/api/{funcion}", method="POST",
            data=json.dumps({"args": list(args)}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(pedido, timeout=self.timeout) as r:
                resultado = json.loads(r.read().decode("utf-8")).get("resultado")
        except urllib.error.HTTPError as e:
            e.close()
            raise ErrorHerramienta(
                f"el editor rechazó «{funcion}» ({e.code}): ¿Jam es anterior a jam-mcp? "
                "Actualizá el plugin.") from None
        except (urllib.error.URLError, OSError) as e:
            raise ErrorHerramienta(
                f"no hay un editor de Unreal con Jam escuchando en {self.url} ({e}). Abrí el "
                "proyecto en el editor: Jam abre esta puerta solo al arrancar.") from None
        if not isinstance(resultado, str):
            raise ErrorHerramienta(f"el editor no devolvió nada para «{funcion}»")
        if resultado.startswith("(timeout:"):
            raise ErrorHerramienta(f"el editor no respondió a tiempo: {resultado}")
        if resultado.startswith("[error]"):
            raise ErrorHerramienta(f"el editor falló en «{funcion}»: {resultado}")
        return resultado

    def llamar_json(self, funcion: str, *args: str) -> dict:
        texto = self.llamar(funcion, *args)
        try:
            return json.loads(texto)
        except ValueError:
            raise ErrorHerramienta(f"el editor devolvió algo que no es JSON en «{funcion}»: "
                                   f"{texto[:200]}") from None


def _errores(crudos) -> list[dict]:
    return [{"line": e.get("linea", 0), "column": e.get("columna", 0), "node": e.get("nodo", ""),
             "message": e.get("mensaje", "")} for e in crudos or []]


def leer(editor: Editor | EditorMotor, _args: dict) -> dict:
    r = editor.llamar_json("leer_canvas")
    return {"text": r["texto"], "version": r["version"], "canvas_open": r["canvas_abierto"]}


def aplicar(editor: Editor | EditorMotor, args: dict) -> dict:
    texto = args.get("text")
    if not isinstance(texto, str) or not texto.strip():
        raise ErrorHerramienta("falta `text`: el grafo entero, una línea por nodo")
    version = args.get("version") or ""
    if not isinstance(version, str):
        raise ErrorHerramienta("`version` es el string que devuelve jam_read_graph")
    r = editor.llamar_json("aplicar_texto", texto, version, "true" if args.get("run") else "false")
    salida = {"ok": bool(r.get("ok")), "conflict": bool(r.get("conflicto")),
              "version": r.get("version", ""), "errors": _errores(r.get("errores"))}
    if r.get("conflicto"):
        salida["text"] = r.get("texto", "")
        return salida
    salida["canonical"] = r.get("canonico", "")
    if "nodes" in r:
        salida["report"] = r.get("report", "")
        salida["nodes"] = {n: {"state": v.get("estado", ""), "text": v.get("texto", "")}
                           for n, v in (r.get("nodes") or {}).items()}
    return salida


def ayuda(editor: Editor | EditorMotor, args: dict) -> dict:
    return {"help": editor.llamar("ayuda_texto", str(args.get("query") or ""))}


def preview(editor: Editor | EditorMotor, args: dict) -> dict:
    accion = args.get("action")
    if accion not in ("bake", "discard"):
        raise ErrorHerramienta("`action` es bake o discard")
    return {"result": editor.llamar("confirm" if accion == "bake" else "discard")}


_DESPACHO = {"jam_read_graph": leer, "jam_apply_graph": aplicar, "jam_help": ayuda,
             "jam_preview": preview}


# ---------------------------------------------------------------- transporte (como oracle-mcp)

def _leer_mensaje(entrada):
    linea = entrada.readline()
    if not linea:
        return None
    if not linea.endswith(b"\n"):
        raise EOFError("mensaje MCP truncado: falta el delimitador '\\n'")
    return json.loads(linea.rstrip(b"\r\n").decode("utf-8"))


def _enviar(salida, mensaje: dict) -> None:
    salida.write(json.dumps(mensaje, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
    salida.flush()


class Servidor:
    def __init__(self, editor: Editor | EditorMotor, salida) -> None:
        self.editor = editor
        self.salida = salida
        self.estado = "nuevo"
        self.instrucciones = INSTRUCCIONES
        if isinstance(editor, EditorMotor):
            self.instrucciones = (
                f"Jam crea escenas y geometría en {editor.motor}. El grafo abierto vive en esta "
                "sesión MCP, sin canvas, y se pierde al cerrar el proceso. "
                "Flujo: jam_help → jam_read_graph → jam_apply_graph con text y version. "
                "Sin run valida con Compile; con run=true ejecuta y muestra Preview en el motor. "
                "Leé errors por línea y nodes por estado. Ante conflict, partí del texto devuelto. "
                "jam_preview bake fija el Preview y discard lo descarta. "
                "canvas_open indica si el plugin contesta.")

    def _respuesta(self, mensaje: dict, resultado) -> None:
        _enviar(self.salida, {"jsonrpc": "2.0", "id": mensaje["id"], "result": resultado})

    def _error(self, mensaje: dict, codigo: int, texto: str) -> None:
        _enviar(self.salida, {"jsonrpc": "2.0", "id": mensaje.get("id") if isinstance(mensaje, dict)
                              else None, "error": {"code": codigo, "message": texto}})

    def _herramienta(self, mensaje: dict, nombre: str, argumentos) -> None:
        if not isinstance(argumentos, dict):
            self._error(mensaje, -32602, "tools/call inválido: arguments tiene que ser un objeto")
            return
        try:
            contenido = _DESPACHO[nombre](self.editor, argumentos)
        except (ErrorHerramienta, ErrorMotor) as e:
            self._respuesta(mensaje, {"content": [{"type": "text", "text": str(e)}], "isError": True})
            return
        texto = contenido.get("help") if nombre == "jam_help" else json.dumps(
            contenido, ensure_ascii=False, separators=(",", ":"))
        self._respuesta(mensaje, {"content": [{"type": "text", "text": texto}],
                                  "structuredContent": contenido, "isError": False})

    def manejar(self, mensaje) -> bool:
        if not isinstance(mensaje, dict) or mensaje.get("jsonrpc") != "2.0" \
                or not isinstance(mensaje.get("method"), str):
            self._error(mensaje if isinstance(mensaje, dict) else {}, -32600,
                        "pedido JSON-RPC inválido: se esperaba jsonrpc '2.0' y method")
            return True
        metodo, es_pedido = mensaje["method"], "id" in mensaje
        if metodo == "initialize" and es_pedido and self.estado == "nuevo":
            self.estado = "inicializando"
            self._respuesta(mensaje, {"protocolVersion": PROTOCOLO, "capabilities": {"tools": {}},
                                      "serverInfo": {"name": NOMBRE_SERVIDOR, "version": __version__},
                                      "instructions": self.instrucciones})
        elif metodo == "notifications/initialized" and not es_pedido:
            if self.estado == "inicializando":
                self.estado = "inicializado"
        elif metodo == "ping" and es_pedido:
            self._respuesta(mensaje, {})
        elif metodo == "tools/list" and es_pedido and self.estado == "inicializado":
            herramientas = HERRAMIENTAS
            if isinstance(self.editor, EditorMotor):
                herramientas = [dict(h) for h in HERRAMIENTAS]
                herramientas[0]["description"] = (
                    "El último texto aplicado en esta sesión y su version. "
                    "canvas_open indica si el plugin del motor contesta; no hay canvas.")
                herramientas[1]["description"] = (
                    "Reemplaza el grafo de esta sesión por text (grafo ENTERO). Sin run valida "
                    "con Compile; con run=true ejecuta y muestra Preview en el motor. Devuelve "
                    "texto canónico, errores por línea y estados por nodo. version evita conflictos.")
            self._respuesta(mensaje, {"tools": herramientas})
        elif metodo == "tools/call" and es_pedido and self.estado == "inicializado":
            params = mensaje.get("params")
            if not isinstance(params, dict) or params.get("name") not in _NOMBRES:
                nombre = params.get("name") if isinstance(params, dict) else None
                self._error(mensaje, -32602, f"herramienta desconocida: {nombre}")
            else:
                self._herramienta(mensaje, params["name"], params.get("arguments", {}))
        elif es_pedido:
            self._error(mensaje, -32601, f"método no soportado en este estado: {metodo}")
        return True


def servir(editor: Editor | EditorMotor, entrada, salida) -> int:
    servidor = Servidor(editor, salida)
    while True:
        try:
            mensaje = _leer_mensaje(entrada)
        except json.JSONDecodeError as e:
            _enviar(salida, {"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": f"JSON inválido: {e}"}})
            continue
        except (EOFError, UnicodeError) as e:
            print(f"TRANSPORTE MCP INVÁLIDO — {e}", file=sys.stderr)
            return 1
        if mensaje is None:
            return 0
        servidor.manejar(mensaje)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Servidor MCP de Jam (stdio).")
    parser.add_argument("--motor", choices=("unreal", "godot", "unity"), default="unreal",
                        help="motor conectado (por defecto unreal)")
    parser.add_argument("--jam", default=JAM_DEFECTO,
                        help="Content/Python de Jam; sólo para Godot/Unity")
    parser.add_argument("--puerto", type=int,
                        help="puerto TCP del plugin (Godot 8792, Unity 8793)")
    parser.add_argument("--url", default=URL_DEFECTO,
                        help=f"la puerta de Jam en el editor (por defecto {URL_DEFECTO})")
    parser.add_argument("--timeout", type=float, default=600.0,
                        help="segundos que espera una llamada al editor (un Run puede hornear mallas)")
    args = parser.parse_args(argv)
    try:
        editor = (Editor(args.url, args.timeout) if args.motor == "unreal" else
                  EditorMotor(args.motor, args.jam, args.timeout, args.puerto))
    except ErrorMotor as e:
        print(f"JAM MCP — {e}", file=sys.stderr)
        return 1
    return servir(editor, sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    sys.exit(main())
