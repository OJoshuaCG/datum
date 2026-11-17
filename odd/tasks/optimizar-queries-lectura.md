# optimizar-queries-lectura

Objetivo: quitar de las rutas de lectura/arranque del gateway el trabajo que se recalcula en cada request (sqlglot, filas ORM completas con LONGTEXT, N+1) y agregar solo los índices que el agente de BD confirmó.
Rama: `main` (el usuario no pidió rama). Entrega: sin commit ni push salvo que lo pida; los commits pasan por el agente `git`. Implementa el hilo principal (sin writers).
Runner de tests: `scripts/run_tests_direct.py tests.<modulo>` solo cuando el usuario lo pida; pytest está denegado en este repo.

## Specs

- S1. «Antes de proceder a arreglar todo lo que analizaste, quiero indicarte que tendras libertad, siempre y cuando consultes con el agente de bases de datos, para validar que no haga falta alguna tabla o columna nueva» → toda decisión de esquema pasa por `gateway-db-dialects`. Veredicto recibido: NINGUNA tabla ni columna nueva; solo índices.
- S2. «Adelante con proceder a arreglar todo lo necesario, optimizando queries y mejorandolas con ayuda de la modificacion de la estructura de la base de datos.»
- S3. (H1) El guard de apply/dry-run/lote analiza solo las versiones PENDIENTES. NO lee `destructive_verdicts`: la huella no cubre `sql_dialect` y la tabla es escribible, así que un veredicto viejo podría dejar pasar un DROP. Debe seguir fallando cerrado.
- S4. (H2) `postgresql_blockers` sale de `MigrationFacts`/`analyze` a una función propia cacheada; el JSON del endpoint de validación no cambia.
- S5. (H3) Tope de bytes en `_transpile_cached` (mismo criterio que `migration_facts._CACHE_MAX_SQL_BYTES`).
- S6. (H4) `channel_pointer_sync`: proyectar `(id, version, reviewed)` y ordenar en SQL con `LIMIT 1`; calcular por modelo, no por canal. Sin columna `version_number`.
- S7. (H5) Proyecciones livianas en `status()` y en el `tip` de `list_migrations`.
- S8. (H9) N+1 de `authz_controller` (~269) y `schema_comparison_controller` (~1864).
- S9. (H6) Índices `ix_qe_server_db_id` y `ix_msp_migration_direction`: migración idempotente encadenada desde `f7a9c1e3b5d6`, un solo head.
- S10. (H7) Snapshot estructural: reutilizarlo dentro del mismo request; NO persistir ni cachear entre requests.
- S11. (H8) Purga de `gateway_sessions` + índice `ix_gs_last_seen_at`: PENDIENTE de decisión de producto (retención).

## Tasks

- [x] T1 · S3 · inline · guard solo-pendientes · implementado, ruff OK, revisión independiente pedida (item 2); tests NO corridos
- [x] T2 · S4 · inline · postgresql_blockers fuera de analyze · ruff OK; tests NO corridos
- [x] T3 · S5 · inline · tope de bytes en transpile · ruff OK
- [x] T4 · S6 · inline · channel_pointer_sync · ruff OK; tests NO corridos
- [x] T5 · S7 · inline · proyecciones status/tip · ruff OK; tests NO corridos
- [x] T6 · S8 · inline · N+1 authz y comparación · ruff OK; tests NO corridos
- [x] T7 · S9 · inline · migración de índices · a8c2e4f6b1d9, head único verificado con check_migration_graph.py
- [~] T8 · S10 · DESCARTADA · los re-snapshots son anti-TOCTOU deliberados (plan→preview→execute→worker, deriva al cerrar export); cachearlos anula el guard
- [x] T9 · S11 · inline · purga de gateway_sessions a 30 días (SESSION_RETENTION_DAYS) + ix_gs_last_seen_at (b9d3f5a7c2e1); tests de purga agregados, corrida pendiente

## Log

- L1. «Antes de proceder a arreglar todo lo que analizaste, quiero indicarte que tendras libertad, siempre y cuando consultes con el agente de bases de datos, para validar que no haga falta alguna tabla o columna nueva, y validar que tan util seria, ya que realmente, creo que la mayoria se resolverian de casi la misma manera. Adelante con proceder a arreglar todo lo necesario, optimizando queries y mejorandolas con ayuda de la modificacion de la estructura de la base de datos. Recuerda apoyarte de los agentes o skills existentes»
- L2. Veredicto de `gateway-db-dialects`: H1-H5, H7, H9 solo código; H6 solo índice (2 tablas); H8 índice + job; ninguna columna/tabla nueva. Refutó la idea de que el guard lea `destructive_verdicts`.
- L3: T8 descartada tras leer export_controller ~2040-2118: el segundo snapshot compara la huella del plan contra la estructura viva a propósito.
- L4: el usuario aprobó retención de 30 días (desde last_seen_at) para gateway_sessions tras la recomendación; purga en arranque + tarea periódica cada 6 h.
