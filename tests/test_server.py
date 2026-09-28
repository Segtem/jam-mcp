"""jam-mcp contra un editor FALSO: un servidor HTTP local que imita la puerta de Jam
(`POST /api/<función>` con `{"args": [...]}` → `{"resultado": "<json>"}`).

La prueba con el editor de verdad es `tools/verifica_mcp_editor.py` (necesita Unreal abierto).
"""

from __future__ import annotations

import http.server
import io
import json
import threading
import unittest

from jam_mcp import server


class _EditorFalso(http.server.BaseHTTPRequestHandler):
    llamadas: list = []
    respuestas: dict = {}

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        cuerpo = json.loads(self.rfile.read(n) or b"{}")
        funcion = self.path.removeprefix("/api/")
        self.llamadas.append((funcion, cuerpo.get("args")))
        if funcion not in self.respuestas:
            self.send_response(404); self.end_headers(); return
        datos = json.dumps({"resultado": self.respuestas[funcion]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def log_message(self, *_a):
        pass


class _Base(unittest.TestCase):
    def setUp(self):
        _EditorFalso.llamadas = []
        _EditorFalso.respuestas = {
            "leer_canvas": json.dumps({"texto": "caja = mesh_box\n", "version": "abc",
                                       "canvas_abierto": True}),
            "aplicar_texto": json.dumps({
                "ok": True, "conflicto": False, "version": "def", "errores": [],
                "canonico": "caja = mesh_box\n", "report": "línea 1 [caja·mesh_box] ✓",
                "nodes": {"caja": {"estado": "ok", "texto": "BOX ✓"}}}),
            "ayuda_texto": "curve_smooth @S → S · iterations=2",
            "confirm": "fijado ✓", "discard": "descartado ✗",
        }
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _EditorFalso)
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.02},
                         daemon=True).start()
        self.editor = server.Editor(f"http://127.0.0.1:{self.srv.server_address[1]}", timeout=5)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def sesion(self, *pedidos, editor=None):
        """Corre el servidor sobre stdio en memoria: initialize + initialized + los pedidos."""
        mensajes = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"}]
        mensajes += [{"jsonrpc": "2.0", "id": i + 1, **p} for i, p in enumerate(pedidos)]
        entrada = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in mensajes))
        salida = io.BytesIO()
        server.servir(editor or self.editor, entrada, salida)
        respuestas = [json.loads(l) for l in salida.getvalue().splitlines()]
        return {r["id"]: r for r in respuestas}

    def llamar(self, nombre, argumentos=None, editor=None):
        r = self.sesion({"method": "tools/call", "params": {"name": nombre,
                                                           "arguments": argumentos or {}}},
                        editor=editor)[1]
        return r["result"]


class Protocolo(_Base):
    def test_initialize_trae_las_instrucciones(self):
        r = self.sesion()[0]["result"]
        self.assertEqual(r["serverInfo"]["name"], "jam-mcp")
        self.assertIn("jam_read_graph", r["instructions"])

    def test_lista_las_cuatro_herramientas(self):
        r = self.sesion({"method": "tools/list", "params": {}})[1]["result"]
        self.assertEqual({h["name"] for h in r["tools"]},
                         {"jam_read_graph", "jam_apply_graph", "jam_help", "jam_preview"})

    def test_una_herramienta_desconocida_es_error_de_protocolo(self):
        r = self.sesion({"method": "tools/call", "params": {"name": "jam_borrar_todo"}})[1]
        self.assertEqual(r["error"]["code"], -32602)

    def test_json_invalido_no_tumba_el_servidor(self):
        entrada = io.BytesIO(b"{no es json\n" + json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode() + b"\n")
        salida = io.BytesIO()
        server.servir(self.editor, entrada, salida)
        codigos = [json.loads(l).get("error", {}).get("code") for l in salida.getvalue().splitlines()]
        self.assertEqual(codigos, [-32700, None])

    def test_stdout_es_solo_protocolo(self):
        entrada = io.BytesIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode() + b"\n")
        salida = io.BytesIO()
        server.servir(self.editor, entrada, salida)
        for linea in salida.getvalue().splitlines():
            self.assertEqual(json.loads(linea)["jsonrpc"], "2.0")


class Herramientas(_Base):
    def test_leer(self):
        r = self.llamar("jam_read_graph")
        self.assertEqual(r["structuredContent"],
                         {"text": "caja = mesh_box\n", "version": "abc", "canvas_open": True})

    def test_aplicar_pasa_texto_version_y_run_en_orden(self):
        r = self.llamar("jam_apply_graph", {"text": "caja = mesh_box\n", "version": "abc",
                                            "run": True})
        self.assertIn(("aplicar_texto", ["caja = mesh_box\n", "abc", "true"]), _EditorFalso.llamadas)
        c = r["structuredContent"]
        self.assertTrue(c["ok"])
        self.assertEqual(c["nodes"], {"caja": {"state": "ok", "text": "BOX ✓"}})
        self.assertEqual(c["version"], "def")

    def test_aplicar_sin_run_no_corre(self):
        self.llamar("jam_apply_graph", {"text": "caja = mesh_box\n"})
        self.assertIn(("aplicar_texto", ["caja = mesh_box\n", "", "false"]), _EditorFalso.llamadas)

    def test_los_errores_llegan_con_su_linea(self):
        _EditorFalso.respuestas["aplicar_texto"] = json.dumps({
            "ok": False, "conflicto": False, "version": "abc", "canonico": "",
            "errores": [{"linea": 2, "columna": 18, "nodo": "", "mensaje": "«@z» no es un nodo"}]})
        c = self.llamar("jam_apply_graph", {"text": "a = mesh_box\nb = mesh_normals @z\n"})[
            "structuredContent"]
        self.assertFalse(c["ok"])
        self.assertEqual(c["errors"], [{"line": 2, "column": 18, "node": "",
                                        "message": "«@z» no es un nodo"}])

    def test_un_conflicto_devuelve_el_texto_actual(self):
        _EditorFalso.respuestas["aplicar_texto"] = json.dumps({
            "ok": False, "conflicto": True, "version": "nueva", "texto": "caja = mesh_sphere\n",
            "errores": []})
        c = self.llamar("jam_apply_graph", {"text": "x = mesh_box\n", "version": "vieja"})[
            "structuredContent"]
        self.assertTrue(c["conflict"])
        self.assertEqual((c["text"], c["version"]), ("caja = mesh_sphere\n", "nueva"))

    def test_aplicar_sin_texto_es_error_de_herramienta(self):
        r = self.llamar("jam_apply_graph", {"text": "  "})
        self.assertTrue(r["isError"])
        self.assertEqual(_EditorFalso.llamadas, [])

    def test_ayuda_devuelve_texto_plano(self):
        r = self.llamar("jam_help", {"query": "curve_smooth"})
        self.assertEqual(r["content"][0]["text"], "curve_smooth @S → S · iterations=2")
        self.assertIn(("ayuda_texto", ["curve_smooth"]), _EditorFalso.llamadas)

    def test_preview_bake_es_confirm(self):
        self.assertEqual(self.llamar("jam_preview", {"action": "bake"})["structuredContent"],
                         {"result": "fijado ✓"})
        self.assertIn(("confirm", []), _EditorFalso.llamadas)

    def test_sin_editor_el_error_dice_que_abrir(self):
        r = self.llamar("jam_read_graph", editor=server.Editor("http://127.0.0.1:9", timeout=1))
        self.assertTrue(r["isError"])
        self.assertIn("Abrí el proyecto en el editor", r["content"][0]["text"])

    def test_un_editor_que_falla_no_se_lee_como_exito(self):
        _EditorFalso.respuestas["leer_canvas"] = "[error] KeyError: 'texto'"
        self.assertTrue(self.llamar("jam_read_graph")["isError"])

    def test_un_editor_viejo_sin_la_funcion(self):
        del _EditorFalso.respuestas["leer_canvas"]
        r = self.llamar("jam_read_graph")
        self.assertTrue(r["isError"])
        self.assertIn("Actualizá el plugin", r["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
