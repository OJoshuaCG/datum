# 14 — Aplicar migraciones sin cola global y `apply-all` en background

Hoy aplicar una migración a la BD A puede hacer esperar a la BD B. Si alguien aplica un `ALTER` de
10 minutos, todas las demás aplicaciones, rollbacks y `stamp` del servicio esperan esos 10 minutos.
Este plan quita esa cola global (Fase A) y saca `apply-all` del request a un job con progreso por
base (Fase B). Es el "fan-out" que el plan 06 dejó pendiente.

## Resumen

| Fase | Qué resuelve | Riesgo | Depende de |
|------|--------------|--------|------------|
| A | Aplicaciones a bases distintas dejan de esperarse entre sí | Alto: toca la tabla de versión dentro de bases de terceros | — |
| B | `apply-all` deja de bloquear un request durante minutos u horas | Medio: cambia un contrato que consume el frontend | A (sin A, el job corre igual de serializado) |

Paliativo disponible hoy, sin código: `WORKERS>1` reparte las aplicaciones entre procesos. Exige
`RATE_LIMIT_REDIS_ENABLED=True` (`environments.py`) y rompe el barrido `interrupted` de los jobs de
clon/export/collation, que asume un solo proceso (ver `clone_batch_runner.py`). Por eso **no** se
recomienda como solución.

## El problema, con evidencia

- `migrations.py` serializa todo `command.upgrade/downgrade/stamp` con un `threading.Lock` de
  módulo (`_ALEMBIC_LOCK`). El motivo es real: `alembic.context` es un proxy GLOBAL de módulo y no
  es thread-safe.
- `WORKERS=1` es el default (`environments.py`, `entrypoint.sh`), así que el lock frena a todo el
  servicio, no a un worker.
- El lock se toma **durante** la ejecución del DDL, no solo para preparar.
- Cada request que espera ocupa un hilo del threadpool de FastAPI, compartido con todo endpoint
  síncrono. Con varias aplicaciones encoladas el resto de la API también se degrada (inferido, no
  medido).
- `apply-all` (`managed_migration_controller.py`) recorre hasta 100 bases en serie dentro del
  request.

## Fase A — Ejecutar las revisiones sin `alembic.context`

### Decisión propuesta

Reemplazar `command.upgrade/downgrade/stamp` por un ejecutor propio que corre sobre la conexión que
`_prepared` ya abre. Es viable porque las revisiones generadas no usan nada de Alembic salvo
`op.get_bind().exec_driver_sql(...)` (más `record_statement` y `capture_statement`, que son
funciones nuestras). El ejecutor no tiene estado global, así que el lock deja de hacer falta. La
serialización POR BASE la sigue dando el advisory lock del motor (`GET_LOCK` /
`pg_advisory_lock`), que ya existe.

| Alternativa | Por qué no |
|-------------|------------|
| `command.*` en un subproceso (`ProcessPoolExecutor`) | Conserva Alembic, pero la conexión y el advisory lock tienen que vivir en el subproceso: se mueve `_prepared` entero, las capturas bufferizadas de PostgreSQL y el checkpoint cruzan procesos. Más piezas móviles que el ejecutor propio. |
| Un `EnvironmentContext` por hilo | `alembic.context` y `alembic.op` son proxies de módulo; parchearlos por hilo depende de detalles internos de Alembic que pueden cambiar en cualquier versión. |
| Subir `WORKERS` | Ver el paliativo de arriba. |

### Lo que el ejecutor tiene que replicar EXACTAMENTE

La tabla de versión vive dentro de bases de terceros que ya escribió Alembic. El ejecutor nuevo
tiene que dejarla byte a byte igual:

- [ ] Una fila, columna `version_num`, en la tabla que resuelve `modernize_accounting`.
- [ ] Primera migración: `INSERT`. Siguientes: `UPDATE`. Downgrade a base: `DELETE`.
- [ ] `transaction_per_migration=True`: en PostgreSQL, el DDL de una migración y su cambio de
      versión confirman juntos; en MySQL/MariaDB el DDL hace commit implícito igual que hoy.
- [ ] `stamp` sin ejecutar DDL.
- [ ] El orden numérico de versiones (`version_sort_key`), no el lexicográfico.

### Pasos

1. Escribir el ejecutor detrás de un flag (`MIGRATION_EXECUTOR=alembic|native`, default `alembic`).
2. Test de paridad: el mismo blueprint aplicado con cada ejecutor contra SQLite y contra los motores
   reales de CI. Compara la tabla de versión, el historial y el checkpoint. Cubre upgrade parcial,
   downgrade, stamp y un fallo a mitad de migración.
3. Activar `native` en un entorno no productivo; después default.
4. Borrar `_ALEMBIC_LOCK`, los `rev_*.py` temporales y el `env.py` compartido cuando `alembic` deje
   de usarse.

### Riesgos

- **Contabilidad de versión divergente** (item de alto riesgo 1 del protocolo): una base marcada en
  una versión que no corresponde a su esquema. Mitigación: el test de paridad del paso 2 es el gate,
  y el flag permite volver atrás sin desplegar.
- **Dos aplicaciones a la misma base desde dos procesos**: ya lo cubre el advisory lock por base; el
  ejecutor tiene que seguir tomándolo en la MISMA conexión.

## Fase B — `apply-all` como job en background

### Decisión propuesta

Seguir el esqueleto de los jobs que ya existen (`clone_batch_runner.py`): tabla de lote + tabla de
ítems, `ThreadPoolExecutor` en proceso, barrido a `interrupted` en el `lifespan`, endpoint de
estado. El preflight destructivo todo-o-nada sigue corriendo **en el request**, antes de crear el
job: si falla, no se crea nada.

| Tema | Decisión |
|------|----------|
| Paralelismo | `MIGRATION_APPLY_MAX_WORKERS` (default 1 = serie, como hoy). Subirlo solo tiene efecto con la Fase A. |
| Contrato | `POST .../apply-all` devuelve `202` con el id del job; `GET .../apply-all-jobs/{id}` devuelve el progreso por base con el mismo ítem que hoy. Actualizar `docs/api-reference.md` en el mismo commit. |
| Durabilidad | Igual que la familia: no es cola durable; un reinicio deja el lote `interrupted` y se relanza a mano. |
| Deuda | Sería la QUINTA copia del esqueleto de runner (ticket `P-23`). Conviene hacer `P-23` antes o junto con esta fase. |

### Riesgos

- **Contrato que consume el frontend** (item de alto riesgo 3): el `200` síncrono pasa a `202`. Hace
  falta coordinar con la SPA o mantener el modo síncrono detrás de un parámetro durante la
  transición.

## Verificación

- [ ] Fase A: test de paridad verde en los tres motores, y una prueba de carga con dos aplicaciones
      a bases distintas que muestre que ya no se esperan.
- [ ] Fase B: `apply-all` de N bases responde en menos de un segundo, y el job reporta el mismo
      resultado por base que el endpoint síncrono de hoy.

## Fuera de alcance

- Paralelizar sentencias DENTRO de una migración: el orden es parte de su semántica.
- Pool de conexiones a los destinos: el `NullPool` es deliberado (`remote_engine.py`).
