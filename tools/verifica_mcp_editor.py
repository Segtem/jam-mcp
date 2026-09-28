"""jam-mcp contra el editor de VERDAD, de punta a punta, desde afuera.

    python tools/verifica_mcp_editor.py [--proyecto ~/Dev/games/JamPlayground/JamPlayground.uproject]

Abre el editor (sin ventana) con el Graph abierto, espera la puerta de Jam, y habla con `jam-mcp`
como un agente: ayuda → leer → aplicar y correr → releer (lo que el canvas publicó) → conflicto con
una versión vieja → descartar el Preview. Cierra el editor al final. Sale 0 si todo dio lo esperado.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
EDITOR = Path.home() / "Dev/engines/UnrealEngine_5.8/Engine/Binaries/Linux/UnrealEditor"
GRAFO = ("caja = mesh_box size_x=120\n"
         "suave = mesh_normals @caja\n"
         "hornear = mesh_to_static @suave name=SM_JamMcpSonda\n"
         "colocar = place @hornear view=true\n")

ESPERA = """
import os, time, unreal
_T0 = time.time()
def _tick(_dt):
    if os.path.exists({bandera!r}) or time.time() - _T0 > 600:
        unreal.unregister_slate_post_tick_callback(_H)
        unreal.SystemLibrary.quit_editor()
_H = unreal.register_slate_post_tick_callback(_tick)
"""


def esperar_puerta(url: str, plazo: float) -> None:
    fin = time.time() + plazo
    while time.time() < fin:
        try:
            pedido = urllib.request.Request(url + "/api/leer_canvas", data=b'{"args": []}', method="POST")
            with urllib.request.urlopen(pedido, timeout=3) as r:
                if json.loads(r.read())["resultado"].startswith("{"):
                    return
        except OSError:
            pass
        time.sleep(2)
    raise SystemExit(f"la puerta de Jam no contestó en {plazo:.0f} s")


class Cliente:
    def __init__(self):
        self.p = subprocess.Popen([sys.executable, "-m", "jam_mcp.server"], cwd=RAIZ,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.n = 0
        self.pedir("initialize", {})
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")

    def pedir(self, metodo, params):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": metodo,
                                       "params": params}) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())["result"]

    def herramienta(self, nombre, **args):
        return self.pedir("tools/call", {"name": nombre, "arguments": args})

    def cerrar(self):
        self.p.stdin.close()
        self.p.wait(10)


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--proyecto", default=str(Path.home() / "Dev/games/JamPlayground/JamPlayground.uproject"))
    a.add_argument("--url", default="http://127.0.0.1:8790")
    args = a.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="jam-mcp-"))
    bandera, espera = tmp / "listo", tmp / "espera.py"
    espera.write_text(ESPERA.format(bandera=str(bandera)))
    editor = subprocess.Popen([str(EDITOR), args.proyecto, "-RenderOffScreen", "-unattended", "-nosplash",
                               f"-ExecCmds=Jam.AbrirGraph,py {espera}"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fallas, r = [], {}
    try:
        esperar_puerta(args.url, 240)
        c = Cliente()
        r["ayuda"] = c.herramienta("jam_help")["content"][0]["text"][:80]
        if "nombre = verbo" not in c.herramienta("jam_help")["content"][0]["text"]:
            fallas.append("la ayuda no trae la sintaxis")
        leido = c.herramienta("jam_read_graph")["structuredContent"]
        v0 = leido["version"]
        ap = c.herramienta("jam_apply_graph", text=GRAFO, version=v0, run=True)["structuredContent"]
        r["estados"] = {n: e["state"] for n, e in ap.get("nodes", {}).items()}
        if not ap["ok"] or set(r["estados"].values()) != {"ok"}:
            fallas.append(f"aplicar y correr no dio verde: {ap.get('errors')} {r['estados']}")
        time.sleep(2.0)   # el Graph sondea el buzón cada 0,5 s y republica
        releido = c.herramienta("jam_read_graph")["structuredContent"]
        r["releido"] = releido["text"]
        if releido["text"] != GRAFO or releido["version"] != ap["version"]:
            fallas.append("lo que el canvas publicó no es lo que se aplicó")
        conflicto = c.herramienta("jam_apply_graph", text="x = mesh_box\n", version=v0)["structuredContent"]
        if not conflicto["conflict"] or conflicto["text"] != GRAFO:
            fallas.append("una versión vieja no dio conflicto")
        r["descartar"] = c.herramienta("jam_preview", action="discard")["structuredContent"]["result"]
        if "descartado" not in r["descartar"]:
            fallas.append("discard no descartó")
        c.cerrar()
    finally:
        bandera.touch()
        try:
            editor.wait(120)
        except subprocess.TimeoutExpired:
            editor.kill()
    print(json.dumps({"veredicto": "VERDE" if not fallas else "ROJO", **r, "fallas": fallas},
                     ensure_ascii=False, indent=2))
    return 0 if not fallas else 1


if __name__ == "__main__":
    sys.exit(main())
