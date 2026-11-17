# optimizar-latencia-gateway

Objetivo: corregir los puntos P1-P3 de la auditoría de latencia de los procesos propios del gateway (no de los servidores remotos), para que ninguna consulta pase de 5 s.
Rama: `main` (sin rama, igual que la feature anterior). Entrega: commits por unidad mediante el agente `git`, sin push. El hilo principal implementa todo, sin writers. Tests: agente `test-runner` con `run-tests.sh` (pytest está denegado).

## Specs

- S1. «lo que interesa son los procesos de estre proyecto, conocer que todo este "rapido" y optimo para su funcionamiento, que una query no tarde mas de 5 segundos en cargar»
- S2. «Adelante con arreglar cada punto de acuerdo a la prioridad, sin problema puedes continuar punto por punto, hasta tarminar todos desde los de la prioridad 1 hasta la 3»
- S3. Condición vigente: los cambios de esquema pasan por `gateway-db-dialects`. Veredicto (L2): no hacen falta tablas ni columnas nuevas, solo índices.
- S4. (P1-1) El dispatcher MCP no bloquea el event loop.
- S5. (P1-2) La BD de metadatos tiene timeouts:
  - en el motor: `max_statement_time` en MariaDB, aplicado por un listener que primero detecta MariaDB, y `statement_timeout` en PostgreSQL;
  - como red de seguridad: `read_timeout` y `write_timeout`;
  - `pool_timeout`.

  Antes de activarlos, las purgas deben borrar por lotes.
- S6. (P1-3) El storage Redis del limitador tiene timeouts de socket y de conexión.
- S7. (P2-4) Menos consultas por request autenticado, sin cachear autorización, revocación ni `is_active`.
- S8. (P2-5) Índices en `audit_log` para `status`, `actor_type` y `admin_username`. El conteo sin filtros se acota y el contrato de la API se mantiene.
- S9. (P2-6) El enmascarado del historial de la consola se memoiza con un LRU acotado en bytes. No se agregan columnas.
- S10. (P2-7) Escaneo de dependencias: cada cuerpo se tokeniza una sola vez y se memoiza por request.
- S11. (P2-8) `mcp_auth` actualiza `last_used_at` con un `UPDATE` condicional y cierra la sesión pronto.
- S12. (P3) Varios puntos:
  - el `LoggerMiddleware` lee el body solo si hace falta;
  - el arranque es más corto;
  - índices en `clone_jobs (created_at,id)` y en los ítems `(fk,seq)`;
  - proyecciones sin LONGTEXT;
  - N+1 menores.

## Tasks

- [x] T1 · S4 · inline · `run_in_threadpool` en `app/mcp/app_factory.py` · commit ea72f1c
- [x] T2 · S6 · inline · timeouts del limitador Redis · commit adfbeb1
- [x] T3 · S5 · inline · purgas por lotes y timeouts de la BD de metadatos · commit adfbeb1
- [x] T4 · S11 · inline · `last_used_at` de `mcp_auth` · commit 0df602d (139 tests verdes)
- [x] T5 · S7 · inline · consultas por request · commit 0df602d (170 tests verdes)
- [x] T6 · S8, S12 · inline · migración de índices y `COUNT(id)` de `audit_log` · commit 997be90 (head único c4e6a8b0d2f3) + tope `AUDIT_LOG_COUNT_CAP` en commit dcdd334 (RED 2 fallos → GREEN 143 tests)
- [x] T7 · S9 · inline · LRU del enmascarado · commit 7c4867e (68 tests verdes)
- [x] T8 · S10 · inline · escaneo de dependencias · commit 0f543b2 (139 tests verdes; diferencial regex vs tokens: 0 diferencias)
- [x] T9 · S12 · inline · resto de P3, más las recomendaciones de seguridad 1 y 2 de 0df602d · commits 72362ee y bd2b38a (290 + 140 + 49 tests verdes; seguridad sin bloqueantes)

## Log

- L1. «Adelante con arreglar cada punto de acuerdo a la prioridad, sin problema puedes continuar punto por punto, hasta tarminar todos desde los de la prioridad 1 hasta la 3, haz los ajustes necesarios y apoyate de los agentes existentes tanto a nivel local como global»
- L2. Veredicto de `gateway-db-dialects`:
  - índices aprobados en `audit_log`, `clone_jobs (created_at,id)` y los ítems `(job_id|comparison_id, seq)`, sin borrar los índices simples y sin `UNIQUE`;
  - los timeouts se ponen en el motor;
  - las purgas necesitan lotes;
  - no hay columnas nuevas;
  - el enmascarado usa un LRU acotado en bytes.
- L3 (2026-11-17): T9 descartes con motivo: purgas del arranque (ya por lotes, solo afectan el arranque y el orden de `.part` es obligatorio); `can_at` del historial (ya memoizado por base); `expire_overdue` en el GET de la bandeja (garantiza no mostrar vencidas); memo de `build_graph` por identidad del snapshot (frágil, el costo ya es lineal).
- L4 (2026-11-17): T9 verde.
  - Tests de comportamiento nuevos: polling de export vía `/preview`, resolución por servidor en scope-readiness y paridad de índices entre migración y modelo.
  - `main.py` va con waiver: su stem de cobertura es vacío, aunque lo ejercitan tests verdes.
  - Simplify: se quitó el refiltrado redundante en Python tras el `IN`.
  - Descartados en simplify:
    - borrar los índices FK simples: L2 lo prohíbe;
    - el tri-estado de `_serialize_summary`: es necesario para no cargar el LONGTEXT;
    - `find_by_ids` en la bandeja: haría falta un método de repositorio nuevo para una lista chica;
    - el conteo opt-in de `audit_log`: cambia el contrato y queda pendiente de decisión del usuario.
- L5 (2026-11-17): «ADelante con lo necesario, y sin problema con llevar a cabo los conventional commits pendientes». El conteo de `GET /audit-log` queda acotado a 10.000 (`has_next` exacto, `total` = «10.000 o más»), documentado en §11.3. Recomendación de seguridad: `_load_issuer` corre tras cerrar la sesión de lectura (commit 9cbb296), y en `record_last_used` queda documentado el caveat de fail-open. GREEN: 143 tests en 4 targets. Feature cerrada.
