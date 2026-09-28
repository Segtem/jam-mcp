"""Núcleo real en otro proceso, Unreal bloqueado y plugins FALSOS por TCP."""

import hashlib
import os
from pathlib import Path
import socketserver
import threading
import unittest

from tools.verifica_mcp_motor import Cliente, GRAFO, JAM


class PluginFalso(socketserver.StreamRequestHandler):
    def handle(self):
        import json
        for linea in self.rfile:
            p = json.loads(linea)
            self.server.pedidos.append(p)
            op = p["op"]
            if self.server.malformado:
                self.wfile.write(b"no json\n")
                return
            if op == "hola":
                r = {"ok": True, "motor": self.server.motor, "contrato": self.server.contrato,
                     "version": "falso"}
            elif op == "mostrar_malla":
                r = ({"ok": False, "error": "fallo de prueba al mostrar"} if self.server.falla else
                     {"ok": True, "nodo": p["nombre"],
                      "hechos": {"triangulos": len(p["malla"]["triangulos"]), "posiciones": 8}})
            elif op in ("fijar", "descartar"):
                r = {"ok": True, "fijados" if op == "fijar" else "descartados": 1}
            else:
                r = {"ok": False, "error": "operación desconocida"}
            self.wfile.write((json.dumps(r) + "\n").encode())


class CasosMotor:
    def setUp(self):
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), PluginFalso)
        self.srv.daemon_threads = True
        self.srv.motor, self.srv.contrato = self.motor, 1
        self.srv.pedidos, self.srv.falla, self.srv.malformado = [], False, False
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.01},
                         daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.c = Cliente(self.motor, jam=Path(os.environ.get("JAM_PYTHON", JAM)),
                         puerto=self.srv.server_address[1], timeout=5)
        self.addCleanup(self.c.cerrar)

    def contenido(self, nombre, **args):
        return self.c.contenido(nombre, **args)

    def test_flujo_completo_version_canonica_y_conflicto(self):
        self.assertIn(self.motor, self.c.info["instructions"])
        self.assertEqual(self.c.info["serverInfo"]["version"], "0.2.0")
        leido = self.contenido("jam_read_graph")
        self.assertEqual(leido["text"], "")
        self.assertTrue(leido["canvas_open"])
        fuente = "caja=mesh_box size_x=100.0\n\nver=mesh_preview @caja name=caja\n"
        ap = self.contenido("jam_apply_graph", text=fuente, version=leido["version"])
        self.assertTrue(ap["ok"], ap)
        self.assertEqual(ap["canonical"], GRAFO)
        self.assertEqual(ap["version"], hashlib.sha256(GRAFO.encode()).hexdigest()[:16])
        self.assertNotIn("nodes", ap)
        self.assertTrue(all(p["op"] == "hola" for p in self.srv.pedidos))
        run = self.contenido("jam_apply_graph", text=GRAFO, version=ap["version"], run=True)
        self.assertTrue(run["ok"], run)
        self.assertEqual(run["version"], ap["version"])
        self.assertEqual({n: v["state"] for n, v in run["nodes"].items()},
                         {"caja": "ok", "ver": "ok"})
        self.assertIn("línea 2", run["report"])
        self.assertIn("en " + self.motor.capitalize(), run["nodes"]["ver"]["text"])
        mallas = [p for p in self.srv.pedidos if p["op"] == "mostrar_malla"]
        self.assertEqual(len(mallas), 1)
        self.assertEqual(len(mallas[0]["malla"]["triangulos"]), 12)
        releido = self.contenido("jam_read_graph")
        self.assertEqual((releido["text"], releido["version"]), (GRAFO, ap["version"]))
        antes = len(self.srv.pedidos)
        conflicto = self.contenido("jam_apply_graph", text="otra = mesh_box\n",
                                  version=leido["version"], run=True)
        self.assertTrue(conflicto["conflict"])
        self.assertEqual((conflicto["text"], conflicto["version"]), (GRAFO, ap["version"]))
        self.assertEqual(len(self.srv.pedidos), antes)

    def test_compile_sin_run_y_con_run_rechaza_no_disponible_con_linea(self):
        for run in (False, True):
            r = self.contenido("jam_apply_graph", text="caja = mesh_box\n\nelegido = pick\n", run=run)
            self.assertFalse(r["ok"])
            self.assertEqual(r["errors"][0]["line"], 3)
            self.assertEqual(r["errors"][0]["node"], "elegido")
            self.assertIn(f"no disponible en este motor ({self.motor})", r["errors"][0]["message"])
            self.assertEqual(self.contenido("jam_read_graph")["text"], r["canonical"])
        self.assertFalse(any(p["op"] == "mostrar_malla" for p in self.srv.pedidos))

    def test_error_de_sintaxis_conserva_el_grafo(self):
        ap = self.contenido("jam_apply_graph", text=GRAFO)
        r = self.contenido("jam_apply_graph", text="caja = mesh_box\nver = mesh_preview @ausente\n",
                           run=True)
        self.assertEqual(r["nodes"], {})
        self.assertIn("línea 2", r["report"])
        self.assertFalse(r["ok"])
        self.assertEqual(r["errors"][0]["line"], 2)
        self.assertGreater(r["errors"][0]["column"], 0)
        self.assertEqual(r["version"], ap["version"])
        self.assertEqual(self.contenido("jam_read_graph")["text"], GRAFO)

    def test_fallo_runtime_tiene_estado_y_linea_y_el_servidor_sigue(self):
        self.srv.falla = True
        r = self.contenido("jam_apply_graph", text=GRAFO, run=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["nodes"]["ver"]["state"], "error")
        self.assertEqual(r["errors"][0]["line"], 2)
        self.assertIn("fallo de prueba", r["errors"][0]["message"])
        self.assertEqual(self.contenido("jam_read_graph")["version"], r["version"])

    def test_ayuda_sintaxis_firma_busqueda_y_disponibilidad(self):
        general = self.contenido("jam_help")["help"]
        self.assertIn("nombre = verbo", general)
        self.assertIn("Motor conectado: " + self.motor, general)
        self.assertIn("Categorías", general)
        caja = self.contenido("jam_help", query="mesh_box")["help"]
        self.assertIn("size_x=100", caja)
        self.assertNotIn("NO DISPONIBLE", caja)
        self.assertIn("NO DISPONIBLE en " + self.motor,
                      self.contenido("jam_help", query="pick")["help"])
        self.assertIn("NO DISPONIBLE", self.contenido("jam_help", query="pick")["help"])
        self.assertIn("nada coincide", self.contenido("jam_help", query="zzzzzz")["help"])

    def test_preview_traduce_ambas_operaciones(self):
        self.assertIn("fijado", self.contenido("jam_preview", action="bake")["result"])
        self.assertIn("descartado", self.contenido("jam_preview", action="discard")["result"])
        self.assertEqual([p["op"] for p in self.srv.pedidos if p["op"] != "hola"],
                         ["fijar", "descartar"])

    def test_plugin_malformado_no_tumba_mcp_y_se_puede_reconectar(self):
        self.srv.malformado = True
        self.assertTrue(self.c.herramienta("jam_preview", action="discard")["isError"])
        self.assertFalse(self.contenido("jam_read_graph")["canvas_open"])
        self.srv.malformado = False
        self.assertTrue(self.contenido("jam_read_graph")["canvas_open"])

    def test_contrato_incompatible_no_modifica_grafo(self):
        self.srv.contrato = 2
        r = self.c.herramienta("jam_apply_graph", text=GRAFO, run=True)
        self.assertTrue(r["isError"])
        self.assertIn("contrato 2", r["content"][0]["text"])
        self.assertEqual(self.contenido("jam_read_graph")["text"], "")

    def test_sin_motor_conserva_grafo_y_read_indica_false(self):
        self.contenido("jam_apply_graph", text=GRAFO)
        self.srv.shutdown()
        self.srv.server_close()
        r = self.contenido("jam_read_graph")
        self.assertFalse(r["canvas_open"])
        self.assertEqual(r["text"], GRAFO)
        r = self.c.herramienta("jam_preview", action="discard")
        self.assertTrue(r["isError"])
        self.assertIn(self.motor.capitalize(), r["content"][0]["text"])


class Godot(CasosMotor, unittest.TestCase):
    motor = "godot"


class Unity(CasosMotor, unittest.TestCase):
    motor = "unity"
