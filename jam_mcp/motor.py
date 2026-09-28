"""Grafo de sesión y núcleo de Jam fuera del motor; plugins TCP de Godot y Unity."""

from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
import hashlib
import importlib
from pathlib import Path
import re
import sys

JAM_DEFECTO = str(Path.home() / "Dev/jam/Content/Python")


class ErrorMotor(Exception):
    """El núcleo o el plugin no pudo atender el pedido."""


class EditorMotor:
    """La misma interfaz que la puerta HTTP, con el grafo guardado en esta sesión."""

    def __init__(self, motor: str, jam: str = JAM_DEFECTO, timeout: float = 600,
                 puerto: int | None = None):
        self.motor, self.timeout, self.puerto = motor, timeout, puerto
        self.texto_abierto = ""
        raiz = Path(jam).expanduser().resolve()
        if not (raiz / "jam/__init__.py").is_file():
            raise ErrorMotor(f"no se encontró el núcleo Jam en {raiz}; indicá Content/Python con --jam")
        sys.modules["unreal"] = None
        # Jam se consume sólo en lectura, incluso cuando está en otro repositorio.
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(raiz))
        try:
            with redirect_stdout(sys.stderr):
                self.modulo = importlib.import_module(f"jam.adaptador_{motor}")
                self.texto = importlib.import_module("jam.texto")
                self.graph = importlib.import_module("jam.graph")
                self.registro = importlib.import_module("jam.registro")
        except ImportError as e:
            raise ErrorMotor(f"no se pudo cargar Jam desde {raiz}: {e}") from None
        self.clase = getattr(self.modulo, "AdaptadorGodot" if motor == "godot" else "AdaptadorUnity")

    @property
    def version(self):
        # Mismo contrato que api._huella / leer_canvas.
        return hashlib.sha256(self.texto_abierto.encode("utf-8")).hexdigest()[:16]

    @contextmanager
    def _conectar(self):
        cliente = None
        try:
            kw = {"plazo": self.timeout}
            if self.puerto is not None:
                kw["puerto"] = self.puerto
            cliente = self.modulo.Cliente(**kw)
            yield self.clase(cliente)
        except (self.modulo.ErrorAdaptador, OSError, ValueError) as e:
            raise ErrorMotor(f"{self.motor}: {e}") from None
        finally:
            if cliente is not None:
                cliente.cerrar()

    def llamar_json(self, funcion: str, *args: str) -> dict:
        with redirect_stdout(sys.stderr):
            if funcion == "leer_canvas":
                try:
                    with self._conectar():
                        abierto = True
                except ErrorMotor:
                    abierto = False
                return {"texto": self.texto_abierto, "version": self.version,
                        "canvas_abierto": abierto}
            if funcion == "aplicar_texto":
                return self._aplicar(*args)
        raise ErrorMotor(f"función desconocida: {funcion}")

    def _aplicar(self, fuente: str, version: str, correr: str) -> dict:
        if version and version != self.version:
            return {"ok": False, "conflicto": True, "texto": self.texto_abierto,
                    "version": self.version, "errores": [{"linea": 0, "columna": 0, "nodo": "",
                    "mensaje": "el grafo cambió desde que lo leíste: partí del texto de esta respuesta"}]}
        try:
            g = self.texto.leer(fuente)
        except self.texto.ErrorTexto as e:
            r = {"ok": False, "canonico": "", "version": self.version,
                 "errores": [{"linea": e.linea, "columna": e.columna,
                              "nodo": "", "mensaje": e.mensaje}]}
            if correr == "true":
                r.update(report=str(e), nodes={})
            return r
        canonico = self.texto.imprimir(g)
        lineas = self.texto.lineas(fuente)
        with self._conectar() as adaptador:
            # Como en api.aplicar_texto: un grafo legible queda abierto incluso si Compile falla.
            self.texto_abierto = canonico
            if correr == "true":
                r = self.modulo.correr_texto(fuente, adaptador)
                # El adaptador informa fallos de ejecución por nodo; agregamos su línea al envelope.
                r["errores"] += [{"linea": lineas.get(n, 0), "columna": 0, "nodo": n,
                                  "mensaje": v.get("texto", "")}
                                 for n, v in r.get("nodes", {}).items() if v.get("estado") == "error"]
                r["report"] = re.sub(
                    r"^\[([A-Za-z_][A-Za-z0-9_]*)·",
                    lambda m: f"línea {lineas.get(m[1], 0)} [{m[1]}·",
                    r.get("report", ""), flags=re.M)
            else:
                try:
                    self.graph.compilar(g, registro=self.registro.REGISTRO, motor=self.motor,
                                        implementados=adaptador.capacidades())
                    r = {"ok": True, "errores": []}
                except self.graph.GraphValidationError as e:
                    r = {"ok": False, "errores": [
                        {"linea": lineas.get(n, 0), "columna": 0, "nodo": n,
                         "mensaje": " · ".join(m)} for n, m in e.diagnostics.items()]}
        return {**r, "canonico": canonico, "version": self.version, "conflicto": False}

    def llamar(self, funcion: str, *args: str) -> str:
        with redirect_stdout(sys.stderr), self._conectar() as adaptador:
            if funcion == "ayuda_texto":
                return self._ayuda(args[0] if args else "", adaptador.capacidades())
            if funcion in ("confirm", "discard"):
                fijar = funcion == "confirm"
                r = adaptador.cliente.pedir("fijar" if fijar else "descartar")
                cantidad = r.get("fijados" if fijar else "descartados", 0)
                return f"{'fijado ✓' if fijar else 'descartado ✗'} — {cantidad}"
        raise ErrorMotor(f"función desconocida: {funcion}")

    def _ayuda(self, filtro: str, implementados) -> str:
        vocab = self.texto.vocabulario()
        registro = self.registro.REGISTRO

        def linea(verbo):
            esta, porque = self.registro.disponible(verbo, self.motor, implementados)
            return self.texto.ayuda(verbo, vocab) + (
                "" if esta else f"  [NO DISPONIBLE en {self.motor}: {porque}]")

        f = filtro.strip()
        if f in vocab:
            return linea(f)
        if not f:
            cats = {}
            for info in registro.values():
                cat = info.get("cat", "?")
                cats[cat] = cats.get(cat, 0) + 1
            fuera = sum(not self.registro.disponible(v, self.motor, implementados)[0] for v in registro)
            return (self.texto.SINTAXIS + f"\nMotor conectado: {self.motor} ({fuera} verbos NO DISPONIBLES; "
                    "pedí su ayuda para ver por qué).\nEjemplo disponible en este motor:\n"
                    "    caja = mesh_box\n    ver = mesh_preview @caja name=caja\n"
                    "Categorías (pedí ayuda con una para ver sus verbos): "
                    + ", ".join(f"{c} ({n})" for c, n in sorted(cats.items())))
        fl = f.lower()
        hits = [v for v, info in registro.items()
                if fl in v.lower() or fl in str(info.get("label", "")).lower()
                or fl == str(info.get("cat", "")).lower() or fl in str(info.get("doc", "")).lower()]
        if not hits:
            return f"nada coincide con «{f}». ayuda sin filtro lista las categorías."
        return "\n".join(linea(v) for v in hits[:60]) + (
            f"\n(… y {len(hits) - 60} más: afiná la búsqueda)" if len(hits) > 60 else "")
