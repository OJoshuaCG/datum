# latencia-aplicar-migraciones

Objetivo: reducir la latencia de aplicar migraciones de blueprint sobre una BD destino (viajes al gateway, conexiones remotas, espera de metadata lock) y dejar planificado el fan-out real (lock global de Alembic + `apply-all` en background).
Rama: `main` (como las features anteriores; el usuario no pidió rama). Commits vía agente `git`. Implementa el hilo principal (sin writers).
Runner de tests: `.venv/bin/python scripts/run_tests_direct.py tests.<modulo>` (pytest está denegado en este repo).

## Specs

- S1. «Adelante con el plan de acuerdo al orden que propones, adelante con hacer el plan necesario». Orden propuesto: #4, #3, `lock_wait_timeout` de MySQL, y #1 + #5 como plan propio.
- S2. (#4) `_write_revision_files` resuelve los checkpoints de la BD con UNA consulta al gateway en vez de `get_progress` por spec y dirección. Se conserva el 409 fail-closed de `_resolve_resume_offset` cuando el checksum o el total no coinciden.
- S3. (#3a) `migration_progress.record_statement` deja el checkpoint con un upsert de una sola sentencia; mismo estado final de la fila que hoy.
- S4. (#3b) Menos conexiones remotas por apply en `_run_apply`, sin mover la lectura de versión previa al lock (es deliberada para el guard destructivo).
- S5. MySQL/MariaDB: la conexión de apply/rollback fija un `lock_wait_timeout` acotado y configurable. Supuesto: 60 s por defecto, variable de entorno nueva en `environments.py` y `.env.example`.
- S6. (#1 + #5) Documento de plan en `docs/plans/` para quitar el `_ALEMBIC_LOCK` global y llevar `apply-all` a un job en background. Solo plan, sin implementación.

## Tasks

- [x] T1 · S2 · inline · `migration_progress.load_database_progress` + `_write_revision_files` · ruff OK; GREEN (L6)
- [x] T2 · S3 · inline · `_checkpoint_upsert` (mysql/mariadb/postgresql/sqlite) · SQL compilado verificado por dialecto; test de integración GREEN (L6)
- [~] T3 · S4 · DESCARTADA · ver L3
- [x] T4 · S5 · inline · `_bound_metadata_lock_wait` en `_prepared` y en `reconcile_partial`; `MIGRATION_LOCK_WAIT_TIMEOUT_SECONDS` en environments/.env.example/docs · ruff OK; GREEN (L6)
- [x] T5 · S6 · inline · `docs/plans/14-fan-out-de-migraciones.md` + fila en `docs/plans/README.md`

## Log

- L1 (2026-11-17) «Adelante con el plan de acuerdo al orden que propones, adelante con hacer el plan necesario»
- L2 Análisis previo: `_ALEMBIC_LOCK` (`migrations.py:77`) con `WORKERS=1` por defecto serializa todo el servicio; ~2 consultas de checkpoint por spec del historial completo; 3 viajes por sentencia en `record_statement`; `apply-all` síncrono y secuencial. RDD global off.
- L3 T3 descartada: juntar la conexión del espejo (`_mirror_history`, fail-open) con la de `_sync_model_version_from_engine` (propaga) exige atravesar la auto-reconciliación que corre entre ambas en el camino de fallo. Ahorro: 1 handshake (~0,1-0,3 s) frente a un apply dominado por DDL. No compensa.
- L4 T4 se extendió a `reconcile_partial` (la auto-reconciliación tras un fallo abre su propia conexión y tenía la misma espera sin tope). Solo `lock_wait_timeout`: `max_statement_time` abortaría ALTER largos.
- L5 Verificación: pytest denegado en `.claude/settings.json`; los tests los corrió el usuario con `scripts/run_tests_direct.py`.
- L6 GREEN (corridas del usuario): `tests.test_migration_runner` 36/36, `tests.test_migration_results` 73/73, `tests.test_api_model_migrations` 65/65, `tests.test_migration_reconcile_partial` 30/30. Total 204/204. Sin verificar: suite completa y motores reales (MySQL/MariaDB/PostgreSQL) para el upsert y `lock_wait_timeout`; los cubre CI. Próximo paso: commit vía agente `git` cuando el usuario lo confirme.
