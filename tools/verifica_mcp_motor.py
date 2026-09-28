#!/usr/bin/env python3
"""Agente MCP por stdio contra Godot/Unity reales. No modifica el repositorio de Jam."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import time

RAIZ = Path(__file__).resolve().parents[1]
JAM = Path.home() / "Dev/jam/Content/Python"
GRAFO = "caja = mesh_box\nver = mesh_preview @caja name=caja\n"


class Cliente:
    """Cliente MCP mínimo con plazo: stdout debe contener exclusivamente JSON-RPC."""

    def __init__(self, motor, jam=JAM, puerto=None, timeout=30):
        comando = [sys.executable, "-B", "-m", "jam_mcp.server", "--motor", motor,
                   "--jam", str(jam), "--timeout", str(timeout)]
        if puerto is not None:
            comando += ["--puerto", str(puerto)]
        self.p = subprocess.Popen(comando, cwd=RAIZ, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, bufsize=0)
        self.timeout, self.n = timeout, 0
        try:
            self.info = self.pedir("initialize", {})
            self._enviar({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except BaseException:
            self.cerrar()
            raise

    def _enviar(self, mensaje):
        self.p.stdin.write((json.dumps(mensaje) + "\n").encode())
        self.p.stdin.flush()

    def pedir(self, metodo, params):
        self.n += 1
        self._enviar({"jsonrpc": "2.0", "id": self.n, "method": metodo, "params": params})
        fin, linea = time.monotonic() + self.timeout, bytearray()
        while not linea.endswith(b"\n"):
            if not select.select([self.p.stdout], [], [], max(0, fin - time.monotonic()))[0]:
                raise TimeoutError(f"MCP no respondió a {metodo}")
            byte = os.read(self.p.stdout.fileno(), 1)
            if not byte:
                raise RuntimeError(f"MCP cerró stdout: {self.p.stderr.read().decode()}")
            linea.extend(byte)
        r = json.loads(linea)
        if r.get("jsonrpc") != "2.0" or r.get("id") != self.n or "error" in r:
            raise AssertionError(f"respuesta MCP inválida: {r}")
        return r["result"]

    def herramienta(self, nombre, **args):
        return self.pedir("tools/call", {"name": nombre, "arguments": args})

    def contenido(self, nombre, **args):
        r = self.herramienta(nombre, **args)
        if r.get("isError"):
            raise AssertionError(r)
        return r["structuredContent"]

    def cerrar(self):
        self.p.stdin.close()
        try:
            self.p.wait(5)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait()
        self.p.stdout.close()
        self.p.stderr.close()


def plugin(puerto, op, timeout=3):
    with socket.create_connection(("127.0.0.1", puerto), timeout=timeout) as s:
        s.sendall((json.dumps({"op": op}) + "\n").encode())
        with s.makefile("rb") as f:
            r = json.loads(f.readline())
    if not r.get("ok"):
        raise RuntimeError(r)
    return r


def verificar(c, puerto):
    medidas = {}
    herramientas = c.pedir("tools/list", {})["tools"]
    assert {h["name"] for h in herramientas} == {
        "jam_help", "jam_read_graph", "jam_apply_graph", "jam_preview"}
    ayuda = c.contenido("jam_help")["help"]
    assert "nombre = verbo" in ayuda
    medidas["ayuda"] = True
    leido = c.contenido("jam_read_graph")
    assert leido["canvas_open"] and leido["text"] == "", leido
    ap = c.contenido("jam_apply_graph", text=GRAFO, version=leido["version"], run=True)
    estados = {n: v["state"] for n, v in ap.get("nodes", {}).items()}
    assert ap["ok"] and estados == {"caja": "ok", "ver": "ok"}, ap
    medidas["estados"] = estados
    hechos = plugin(puerto, "hechos")
    cajas = [m for m in hechos["mallas"] if m["nodo"] == "caja"]
    assert len(cajas) == 1 and cajas[0]["hechos"]["triangulos"] == 12, hechos
    medidas["malla_en_motor"] = cajas[0]
    releido = c.contenido("jam_read_graph")
    assert releido["text"] == ap["canonical"] == GRAFO, releido
    assert releido["version"] == ap["version"], releido
    medidas["relectura_misma_version"] = True
    conflicto = c.contenido("jam_apply_graph", text="otra = mesh_box\n",
                           version=leido["version"], run=True)
    assert conflicto["conflict"] and conflicto["text"] == GRAFO, conflicto
    assert conflicto["version"] == ap["version"], conflicto
    medidas["conflicto"] = True
    error = c.contenido("jam_apply_graph", text="caja = mesh_box\nelegido = pick\n",
                       version=ap["version"], run=True)
    assert not error["ok"] and any(e["line"] == 2 and e["node"] == "elegido"
                                  and "no disponible" in e["message"] for e in error["errors"]), error
    medidas["no_disponible"] = error["errors"]
    assert "NO DISPONIBLE" in c.contenido("jam_help", query="pick")["help"]
    medidas["discard"] = c.contenido("jam_preview", action="discard")["result"]
    assert "descartado" in medidas["discard"]
    assert not plugin(puerto, "hechos")["mallas"], "discard dejó mallas en el motor"
    medidas["comprobaciones"] = 9
    return medidas


def main():
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("--motor", choices=("godot", "unity"), required=True)
    a.add_argument("--jam", type=Path, default=JAM)
    a.add_argument("--proyecto", type=Path)
    a.add_argument("--editor", help="ejecutable del motor")
    a.add_argument("--plazo", type=float, default=240)
    args = a.parse_args()
    puerto = 8792 if args.motor == "godot" else 8793
    proyecto = args.proyecto or Path.home() / "Dev/games" / (
        "JamGodot" if args.motor == "godot" else "JamUnity")
    # No reutilizar ni cerrar un editor ajeno, ni atribuirle la prueba al proceso equivocado.
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", puerto)) == 0:
            a.error(f"el puerto {puerto} está ocupado; cerrá ese editor antes de la prueba")
    carpeta = Path(tempfile.mkdtemp(prefix=f"jam-mcp-{args.motor}-"))
    log = carpeta / ("proceso.log" if args.motor == "godot" else "motor.log")
    if args.motor == "godot":
        comando = [args.editor or "godot", "--headless", "--editor", "--path", str(proyecto)]
    else:
        comando = [args.editor or str(Path.home() / "Dev/engines/unity/6000.3.24f1/Editor/Unity"),
                   "-batchmode", "-nographics", "-projectPath", str(proyecto),
                   "-executeMethod", "Jam.JamServidor.Lote", "-logFile", str(log)]
    motor = c = None
    resultado = {"motor": args.motor, "comando": comando, "log": str(log)}
    try:
        with (carpeta / "proceso.log").open("wb") as salida:
            motor = subprocess.Popen(comando, stdout=salida, stderr=subprocess.STDOUT)
        fin = time.monotonic() + args.plazo
        while True:
            if motor.poll() is not None:
                raise RuntimeError(f"el motor terminó con código {motor.returncode}; ver {carpeta}")
            try:
                hola = plugin(puerto, "hola", timeout=2)
                assert hola["motor"] == args.motor and hola["contrato"] == 1, hola
                break
            except (OSError, ValueError):
                if time.monotonic() >= fin:
                    raise TimeoutError(f"el plugin no contestó en {args.plazo} s; ver {carpeta}")
                time.sleep(0.5)
        c = Cliente(args.motor, args.jam)
        resultado.update(verificar(c, puerto), veredicto="VERDE")
    except Exception as e:
        resultado.update(veredicto="ROJO", error=str(e))
    finally:
        if c:
            c.cerrar()
        if motor and motor.poll() is None:
            if args.motor == "unity":
                try:
                    plugin(puerto, "salir")
                except (OSError, ValueError, RuntimeError):
                    motor.terminate()
            else:
                motor.terminate()
            try:
                motor.wait(15)
            except subprocess.TimeoutExpired:
                motor.kill()
                motor.wait()
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    return 0 if resultado["veredicto"] == "VERDE" else 1


if __name__ == "__main__":
    sys.exit(main())
