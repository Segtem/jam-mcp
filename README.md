# jam-mcp

Servidor MCP de [Jam](https://github.com/Segtem/jam): un agente lee, escribe y corre un grafo
**como texto**, en Unreal, Godot o Unity. Sin dependencias de Python adicionales.

```
caja = mesh_box size_x=120
ver = mesh_preview @caja name=caja
```

Una línea por nodo: `nombre = verbo [posicional] @entrada… clave=valor… +bypass`. `@x` es un cable;
los parámetros omitidos valen su default. `jam_help` devuelve la sintaxis; la ayuda de cada verbo
indica si está **NO DISPONIBLE** en el motor elegido.

## Cómo funciona

```text
agente ──stdio──▶ jam-mcp --motor unreal (por defecto)
                      └──HTTP 127.0.0.1:8790──▶ Jam en el editor Unreal

agente ──stdio──▶ jam-mcp --motor godot|unity + núcleo Python de Jam
                      └──TCP 127.0.0.1:8792 (Godot) / :8793 (Unity)──▶ plugin
```

**Unreal mantiene el funcionamiento de 0.1.0.** El grafo abierto es el canvas del editor: el humano
ve lo aplicado y el agente lee sus ediciones. La puerta HTTP de Jam se abre al arrancar el editor
(`JAM_WEB=0` la apaga).

**Godot y Unity no tienen canvas.** El grafo abierto es el último texto aplicado en ese proceso de
jam-mcp; empieza vacío y se pierde al cerrar la sesión. El núcleo corre dentro de jam-mcp, importado
desde `--jam` (por defecto `~/Dev/jam/Content/Python`), con `sys.modules["unreal"] = None`.
Los adaptadores de Jam calculan la base común y envían las primitivas al plugin por TCP.
No se modifica el repositorio del núcleo ni se escribe bytecode allí.

`stdout` contiene únicamente JSON-RPC, un mensaje por línea; los diagnósticos van a `stderr`.

## Herramientas

Los tres motores tienen las mismas cuatro herramientas y el mismo formato de salida:

| herramienta | qué hace |
|---|---|
| `jam_read_graph` | devuelve `text`, `version`, `canvas_open`. En Godot/Unity, `canvas_open` indica si el plugin contesta con un contrato compatible; si no, devuelve `false` y conserva el texto de sesión |
| `jam_apply_graph` | reemplaza el grafo por `text` (ENTERO). Sin `run`, sólo valida con Compile; con `run=true`, ejecuta y muestra el Preview. Devuelve `ok`, `conflict`, `version`, `canonical`, `errors`; al ejecutar agrega `report` y `nodes` con `state`/`text` |
| `jam_help` | sin `query`: sintaxis y categorías; con un verbo: firma y disponibilidad; con otra palabra: coincidencias |
| `jam_preview` | `action="bake"` fija el Preview (`fijar` del plugin); `action="discard"` lo descarta (`descartar`) |

Los errores incluyen `line`, `column`, `node` y `message`. Un error de sintaxis conserva el grafo
anterior. Un texto legible queda abierto incluso si Compile lo rechaza; ese grafo no se ejecuta.
Compile juzga contra las capacidades del adaptador elegido, no contra las de Unreal.

**Versiones.** `version` son los primeros 16 caracteres hexadecimales de SHA-256 del texto canónico,
igual que `jam.api.leer_canvas`. Cambiar espacios o escribir defaults no cambia la versión.
Pasala a `jam_apply_graph`: si quedó vieja, devuelve `conflict=true`, `text` y la versión actuales,
sin reemplazar ni ejecutar nada. Sin versión, se reemplaza sin comprobar conflictos.
En Godot/Unity la versión sólo cubre el grafo de esa sesión, no las ediciones de escena ni otras
sesiones MCP. `discard` no borra el texto abierto.

## Instalar y conectar

```bash
uv tool install -e ~/Dev/jam-mcp

claude mcp add jam -- jam-mcp                       # Unreal, por defecto
claude mcp add jam-godot -- jam-mcp --motor godot
claude mcp add jam-unity -- jam-mcp --motor unity
```

Codex (`~/.codex/config.toml`), elegí los servidores que uses:

```toml
[mcp_servers.jam]
command = "jam-mcp"

[mcp_servers.jam-godot]
command = "jam-mcp"
args = ["--motor", "godot"]

[mcp_servers.jam-unity]
command = "jam-mcp"
args = ["--motor", "unity"]
```

El editor debe estar abierto con el plugin Jam activo. Para otra ubicación del núcleo:

```bash
jam-mcp --motor godot --jam /ruta/a/jam/Content/Python
```

`--timeout 600` es el plazo por llamada en segundos. `--url http://127.0.0.1:8790` sólo afecta
Unreal. `--puerto` permite cambiar el puerto TCP de Godot/Unity (el plugin debe escuchar allí).
Unreal no necesita un checkout local del núcleo.

## Pruebas

```bash
python -m unittest discover -s tests -t .
```

Los tests originales usan un editor HTTP falso. Los de Godot/Unity lanzan jam-mcp por stdio, usan
el núcleo real sin Unreal y un plugin **falso por TCP** en un puerto libre. Necesitan el checkout de
Jam en la ruta por defecto; `JAM_PYTHON=/ruta/a/Content/Python` permite elegir otro.
Cubren Compile sin ejecutar, Run, canonicalización, conflictos, ayuda, errores por línea,
fijar/descartar, desconexión, contrato incompatible y recuperación de respuestas inválidas.

Pruebas de punta a punta con los motores **reales**, desde este checkout:

```bash
python tools/verifica_mcp_editor.py               # Unreal (abre JamPlayground)
python tools/verifica_mcp_motor.py --motor godot
python tools/verifica_mcp_motor.py --motor unity
```

La nueva sonda lanza Godot con `godot --headless --editor --path ~/Dev/games/JamGodot`, o Unity
6000.3.24f1 con `-batchmode -nographics -executeMethod Jam.JamServidor.Lote` sobre
`~/Dev/games/JamUnity`. Acepta `--editor`, `--proyecto`, `--jam` y `--plazo` (arranque, 240 s).
Los proyectos deben tener instalado el plugin; la sonda rechaza un puerto ya ocupado.

Actúa como un agente por stdio: ayuda → leer → aplicar y correr caja + Preview → releer la misma
versión → conflicto viejo → `pick` rechazado en línea 2 → discard. Además consulta los hechos
del plugin para comprobar la caja de 12 triángulos y el Preview vacío después de descartar.
Usá proyectos de prueba: `discard` elimina el Preview activo completo. No ejecuta `bake` ni guarda
la escena. Cierra sólo el proceso que lanzó, por PID o mediante `salir` en Unity; deja logs en `/tmp`.
El resultado es JSON con `veredicto: VERDE|ROJO` y código de salida 0|1.

Verificado el 2026-09-28: **34 tests OK; Godot VERDE; Unity 6000.3.24f1 VERDE**, con los dos nodos en
`ok`, relectura y conflicto correctos, error de disponibilidad en línea 2 y descarte comprobado.
