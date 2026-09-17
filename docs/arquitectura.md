# Arquitectura

## Decisiones de diseño

**`sponser_api` nunca importa `sponser_etl` como librería.** Son dos
proyectos con ciclos de vida distintos: el pipeline de datos puede
evolucionar (nuevas features, reentrenar el modelo) sin arriesgar romper el
backend, y viceversa. El acoplamiento se limita a dos superficies:

1. **Lectura de archivos**: `sponser_api/app/services/data_access.py` lee
   los CSV de `sponser_etl/data/processed/` y los artefactos de
   `sponser_etl/modeling/versions/`.
2. **Ejecución como subproceso**: `sponser_api/app/services/pipeline_runner.py`
   dispara `sponser_etl/pipeline.py`, `modeling/forecast.py` y
   `replenishment/run.py` con `subprocess.run([sys.executable, script], ...)`,
   nunca con `import`. Esto también aísla fallas: si el pipeline lanza una
   excepción, el proceso del backend no se cae -- el subproceso termina con
   código de error, que `pipeline_runner.py` captura y registra en la
   bitácora (`corridas_pipeline`).

Esta separación es la razón por la que ambas carpetas deben vivir como
hermanas bajo la misma raíz: `sponser_api/app/config.py` resuelve
`sponser_etl/` como `../sponser_etl` relativo a sí mismo
(`PROJECT_ROOT = os.path.dirname(API_ROOT)`).

## Capas de consulta del backend

`data_access.py` separa dos niveles de caché, para no volver a calcular lo
mismo en cada request sin arriesgar servir datos desactualizados:

- **Capa 1 -- lectura de CSV**: cachea el `DataFrame` crudo, invalidado por
  `mtime` del archivo en disco (si el pipeline reescribe `ventas_detalle.csv`,
  la siguiente lectura lo detecta y recarga).
- **Capa 2 -- cálculos agregados**: cachea el resultado de agregar/cruzar
  esos DataFrames (rankings, Pareto, KPIs), invalidado explícitamente por
  `invalidar_cache()`, que se llama una sola vez, al terminar una corrida
  exitosa del pipeline -- no en cada request.

Así, abrir una pestaña del dashboard o cambiar un filtro nunca dispara un
recálculo de todo lo agregado; sólo lee lo ya cacheado, a menos que haya
datos nuevos.

## Frontend: por qué sin build step

`sponser_api/frontend_src/Sponser Analitica v2 Ilustrada.dc.html` es HTML +
JavaScript plano sobre un framework de plantillas minimalista
(`support.js`, con bindings `{{ }}` y directivas `<sc-if>`/`<sc-for>`). No
hay `npm install` ni bundler: se sirve tal cual con cualquier servidor
estático. La contrapartida es que React/ReactDOM/Babel se cargan desde
`unpkg.com` en tiempo de ejecución en vez de estar empaquetados (ver
[limitaciones en el README](../README.md#limitaciones-conocidas-y-trabajo-futuro)).

## Seguridad

- **Autenticación**: correo + contraseña, con bloqueo tras 5 intentos
  fallidos (`Usuario.intentos_fallidos`/`bloqueado_hasta`).
- **Roles a nivel de endpoint, no solo de interfaz**: cada ruta de escritura
  (`POST`/`PUT`/`DELETE`) depende de `security.requiere_admin`, que se
  ejecuta en el backend independientemente de lo que la interfaz permita
  hacer clic. Un usuario `viewer` que intente llamar la API directamente
  (sin pasar por la pantalla) recibe 403 igual.
- **Contraseñas de invitación**: cuando un administrador invita a un
  colaborador, la cuenta se crea con un hash de una contraseña aleatoria que
  nadie conoce -- el colaborador entra por primera vez con un enlace de un
  solo uso (mismo mecanismo que "olvidé mi contraseña") y define su propia
  contraseña. Ninguna contraseña temporal viaja por correo.
