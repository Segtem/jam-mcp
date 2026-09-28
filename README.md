# jam-mcp

Servidor MCP de [Jam](https://github.com/Segtem/jam): un agente lee, escribe y corre el grafo
abierto en Unreal **como texto**, y el humano ve cada cambio en el canvas de nodos — y al revés.

```
caja = mesh_box size_x=120
suave = mesh_normals @caja
hornear = mesh_to_static @suave name=SM_Caja
colocar = place @hornear view=true
```

Una línea por nodo: `nombre = verbo [posicional] @entrada… clave=valor… +bypass`. El nombre es el
que se ve en la ficha del nodo; `@x` es un cable; los parámetros que no se escriben valen su default.
La sintaxis entera la devuelve `jam_help` sin argumentos.

## Cómo funciona

```
agente (Claude Code, Codex…) ──stdio──▶ jam-mcp ──HTTP 127.0.0.1:8790──▶ editor de Unreal con Jam
                                                     POST /api/<función>      (jam.web, game thread)
```

`jam-mcp` corre **fuera** del editor y no tiene dependencias. Jam abre su puerta sola al arrancar el
editor (`jam.web`, sólo en `127.0.0.1`; `JAM_WEB=0` la apaga) y deja llamar una lista blanca corta
de funciones: leer y aplicar texto, la ayuda, y fijar o descartar el Preview. Nada que escriba
archivos.

## Herramientas

| herramienta | qué hace |
|---|---|
| `jam_read_graph` | el grafo abierto como texto canónico, y su `version` |
| `jam_apply_graph` | `text` (el grafo ENTERO) pasa a ser el grafo abierto y aparece en el canvas; con `run` además corre, y devuelve el estado de cada nodo. Los errores vienen con su línea |
| `jam_help` | sin `query`: la sintaxis y las categorías; con un verbo, su firma; con otra palabra, los verbos que coinciden |
| `jam_preview` | `bake` fija lo que dejó el último Run; `discard` lo borra |

**Concurrencia.** La `version` es una huella del texto canónico. Si el humano editó el grafo desde
que el agente lo leyó, `jam_apply_graph` no pisa nada: devuelve `conflict` con el texto y la versión
actuales, y el agente rehace su cambio sobre ellos. Mover nodos en el canvas no cambia la versión.

## Instalar y conectar

```bash
uv tool install -e ~/Dev/jam-mcp        # deja `jam-mcp` en el PATH
claude mcp add jam -- jam-mcp           # Claude Code
```

Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.jam]
command = "jam-mcp"
```

`jam-mcp --url http://127.0.0.1:8790 --timeout 600` son los valores por defecto. El editor tiene que
estar abierto con el plugin Jam; si no, cada herramienta devuelve un error que lo dice.

## Pruebas

```bash
python -m unittest discover -s tests -t .      # contra un editor falso, sin Unreal
python tools/verifica_mcp_editor.py            # contra el editor de verdad (abre JamPlayground)
```
