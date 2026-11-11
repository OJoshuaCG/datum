# API v47 — Aprobación de versiones destructivas para producción

Addendum de [v46](api-reference-v46.md). Suma **una superficie nueva** (listar, aprobar y revocar aprobaciones de
versiones destructivas) y **extiende tres existentes** (aplicar a una base, aplicar a todas y revertir). Cómo se usa,
en [`features/environments.md`](features/environments.md) y [`features/model-migrations.md`](features/model-migrations.md);
el porqué de cada decisión, en `docs/development/decisiones-e-incidentes.md`.

Una versión **destructiva** (`DROP`, `TRUNCATE`, `DELETE` sin `WHERE`, `ALTER ... DROP COLUMN`) ya no queda
bloqueada para siempre en un entorno con `blocks_destructive_migrations`: se ejecuta si **otra persona** la aprobó
antes. La aprobación solo escribe metadatos del gateway; ejecutar es el `apply` / `rollback` de siempre.

## Capacidades

| Capacidad | Eje | Roles | Step-up | Qué protege |
|---|---|---|:-:|---|
| `blueprints.approve_destructive` | entorno | owner (sensible) | ✓ (salvo revocar) | Aprobar y revocar aprobaciones |
| `blueprints.apply_destructive` | entorno | owner (sensible) | ✓ | Ejecutar una versión destructiva ya aprobada en un entorno protegido |

Ninguna la tiene un token de integración ni el MCP (`agent_allowed=false`). El conjunto sensible pasa de 17 a 19.
Aprobar y ejecutar son capacidades separadas: quien ejecuta puede ser el autor, o la misma persona que aprobó.

## Endpoints

| Método y ruta | Capacidad | Qué hace |
|---|---|---|
| `GET /database-models/{id}/destructive-versions` | `blueprints.read` | Versiones destructivas del blueprint, una fila por dirección. Query `managed_database_id` opcional |
| `POST /database-models/{id}/migrations/{version}/destructive-approvals` | `approve_destructive` + step-up | Aprueba para **todo el blueprint** (alcance `blueprint`). `201` |
| `POST /managed-databases/{db_id}/migrations/{version}/destructive-approvals` | `approve_destructive` + step-up | Aprueba para **una base** (alcance `database`). `201` |
| `POST /database-models/{id}/destructive-approvals/batch` | `approve_destructive` + step-up | Aprueba **varias versiones** con alcance `blueprint`. `200`, resultado parcial por ítem |
| `POST /managed-databases/{db_id}/destructive-approvals/batch` | `approve_destructive` + step-up | Lo mismo con alcance `database`. `200` |
| `POST /destructive-approvals/{approval_id}/revoke` | `approve_destructive`, **sin step-up** | Revoca. Idempotente |

`{version}` cumple `^\d{4,10}$`. Todas responden con `ApiResponse[T]`.

### Listar (`GET .../destructive-versions`)

Devuelve `list[DestructiveVersionOut]`: `migration_id`, `version`, `name`, `direction` (`up` | `down`), `sql_hash`,
`reasons_by_engine`, `author`, `last_sql_editor`, `author_unknown`, `approval_status`, `effective_approval_id` y
`approvals[]` (historial completo, la más reciente primero).

- `sql_hash` es el hash **actual** del SQL de esa dirección y es el `expected_sql_hash` que hay que enviar al aprobar.
- `approval_status` es `approved`, `unapproved`, `revoked` o `stale_hash`. Con `managed_database_id` se evalúa para
  esa base (sus aprobaciones y las del blueprint); sin él, solo cuentan las de alcance `blueprint`.
- `stale_hash`: hay una aprobación vigente, pero de otro SQL (se editó después) o con otra `hash_basis`. **Nunca se
  toma por aprobada**: hay que re-aprobar.
- Una dirección `down` solo aparece si hay `down_sql`.

### Aprobar

Body (igual en las dos rutas): `{direction, expected_sql_hash, reason}`.

| Campo | Regla |
|---|---|
| `direction` | `up` (aplicar) o `down` (revertir). Una **no cubre** a la otra: el `down_sql` es otro SQL con otro hash |
| `expected_sql_hash` | 64 hex. Debe coincidir con el SQL actual; si no, `409 destructive_approval.sql_changed` |
| `reason` | 10 a 500 caracteres, recortado antes de medir |

Reglas:

- **Cuatro ojos.** El aprobador debe ser una persona distinta del **creador** y del **último editor del SQL**.
  Si no: `409 access.self_approval_forbidden`, con `public_context.rule` = `creator` o `last_editor`. Para un token
  de API, la persona es quien lo emitió.
- **Autoría desconocida.** Las versiones anteriores a esta función no tienen último editor. Se permite aprobar y la
  aprobación queda con `author_unknown: true` (y la auditoría lo dice).
- **Alcance `blueprint`** cubre las bases actuales y las que se creen después, pero solo las de entornos protegidos.
  Exige la capacidad en **cada** entorno que bloquea lo destructivo (activo o no): un owner acotado a `staging` no
  puede pre-aprobar bases futuras de producción.
- **Alcance `database`** exige la capacidad en esa base. Gana sobre una aprobación `blueprint` cuando existen las dos.
- **Re-aprobar tras editar el SQL** reemplaza la aprobación anterior en la misma transacción
  (`revoke_reason: "superseded"`). Aprobar el mismo hash dos veces: `409 destructive_approval.duplicate`.
- **Sin vencimiento.** Una aprobación vale hasta que se revoca o se edita el SQL.
- Un actor máquina (integración, MCP) no aprueba: `403 destructive_approval.machine_actor`.

Respuesta `DestructiveApprovalOut`: `id`, `model_migration_id`, `database_model_id`, `version`, `direction`, `scope`,
`managed_database_id`, `sql_hash`, `hash_basis` (`raw_source_v1`), `destructive_summary`, `author`,
`last_sql_editor`, `author_unknown`, `reason`, `approved_by`, `approved_at`, `is_live`, `revoked_at`, `revoked_by`,
`revoke_reason`, `last_executed_at`, `last_executed_by`, `execution_count`.

### Aprobar en lote

Body (igual en las dos rutas): `{reason, items: [{version, direction, expected_sql_hash}]}`. El `reason` es único
para todo el lote y queda registrado en cada aprobación. Entre 1 y 100 ítems; repetir `(version, direction)` es `422`.

Cada ítem sigue **exactamente** las reglas de «Aprobar» (hash, cuatro ojos, alcance, reemplazo, duplicado): el lote no
tiene ningún atajo. El resultado es **parcial**: responde `200` aunque algún ítem falle, y los ítems que pasan quedan
aprobados (cada uno en su transacción, con su entrada de auditoría). Si el cliente necesita todo-o-nada, tiene que
revocar lo aprobado.

Respuesta: `{total, approved_count, failed_count, results[]}`. Cada resultado trae `version`, `direction`, `status`
(`approved` | `failed`), `approval` (un `DestructiveApprovalOut`, solo si `approved`) y `error`
(`{code, message, status_code, rule}`, solo si `failed`). Los `code` son los de la aprobación individual; un error
inesperado de un ítem sale como `destructive_approval.batch_item_failed` (500) y el detalle va al log.

Lo que no depende del ítem corta **toda la petición** antes de aprobar nada: `403 access.forbidden` (en el alcance
`blueprint`, falta la capacidad en algún entorno protegido), `403 destructive_approval.machine_actor` y `403
access.step_up_required`.

### Revocar

Body: `{reason}` (10 a 500). Pueden revocar: quien aprobó, **o** quien tiene `approve_destructive` en el alcance de
la aprobación, o un owner. **`access.admin` no tiene camino**: no es operativa por diseño del catálogo.

- Afecta solo ejecuciones futuras. No toca lo ya aplicado.
- **Idempotente**: revocar una aprobación ya revocada o reemplazada responde `200` con la fila tal cual (mismo
  revocador y fecha). No existe `already_revoked`.
- No exige step-up: frenar una operación destructiva no puede costar más que lanzarla.
- Un `approval_id` que no existe, o que el actor no puede ver, responde `404` o `403`.

## Extensiones: el flujo dry-run, token, re-tipeo, step-up

Se aplica igual a **apply**, **rollback** y **apply-all** cuando el entorno de la base bloquea lo destructivo y hay
versiones destructivas en juego. Una base sin entorno, o en un entorno que no bloquea, **no cambia**.

1. **Dry-run.** Devuelve el plan. Solo si todas las versiones están aprobadas **y** quien consulta es una persona con
   `apply_destructive` en la base, trae un `confirm_token`.
2. **Ejecución.** Se envía el `confirm_token` y el re-tipeo (`confirm_name` o `confirm_phrase`). Todo bajo step-up.
3. **Re-chequeo.** El servidor vuelve a resolver las aprobaciones en una sesión nueva al ejecutar: una revocación
   entre el plan y la ejecución se rechaza.

El token vale **300 s** y ata el conjunto exacto de aprobaciones. El re-tipeo lo valida el **servidor**: el token da
frescura, no intención. `force` **no** interviene en nada de esto.

### Aplicar a una base — `POST /managed-databases/{id}/migrations/apply`

Query nuevos: `confirm_token` (≤200) y `confirm_name` (≤128, el nombre exacto de la base).

El dry-run trae `blocked_by` (versiones sin aprobación vigente) y `destructive_plan`:
`{direction, requires_confirmation, executor_allowed, all_approved, versions[], confirm_token, expires_at,
confirm_name_expected}`. Cada elemento de `versions[]`: `version`, `migration_id`, `sql_hash`, `reasons`,
`approval_status`, `approval_id`, `approval_scope`.

### Revertir — `POST /managed-databases/{id}/migrations/rollback`

**Cambio de comportamiento.** El rollback antes no miraba el entorno. Ahora, si el `down_sql` de alguna versión del
camino es destructivo en un entorno protegido, exige una aprobación de dirección `down` (la `up` no alcanza),
`apply_destructive`, token y re-tipeo.

Query nuevos: `dry_run`, `confirm_token`, `confirm_name`. `confirm_version` pasa a ser **opcional solo con
`dry_run=true`**; sin dry-run y sin él responde `422`. El dry-run no escribe en el motor y devuelve
`rollback_path_versions`, `blocked_by` y `destructive_plan` con `direction: "down"`.

### Aplicar a todas — `POST /database-models/{id}/migrations/apply-all`

Query nuevos: `confirm_token` (≤200) y `confirm_phrase` (≤128, el **slug del entorno**, por ejemplo `production`).

El dry-run suma al resultado `destructive_confirmation`: `{required, confirm_token, expires_at, environment_slug,
environment_slugs, entry_count, executor_allowed, all_approved, unapproved_databases[]}` y, por ítem,
`destructive_plan` sin token. Un único token respalda todo el lote. No se emite si el lote toca más de un entorno
protegido.

**Cambio de contrato intencional: `apply-all` es todo o nada para las bases protegidas.** Antes, una base con una
versión destructiva sin aprobar fallaba en su ítem (`error_code`) y las demás se aplicaban, con respuesta `200`.
Ahora el preflight corre **antes de ejecutar la primera base**: si una versión no está aprobada, o falta el token, la
capacidad o el re-tipeo, **toda la petición** responde con error y **no se ejecuta ninguna base**. Un cliente que
leía `items[].error_code` para este caso debe leer el `409` de la petición. Los rechazos por ítem quedan solo como
defensa ante carreras (una revocación a mitad del lote).

Una máquina no pasa por el preflight: cada ítem se rechaza con `environment.destructive_blocked`, como antes.

## Códigos de error

Todos viajan en `detail.public_context.code`.

| Código | HTTP | Cuándo |
|---|:-:|---|
| `environment.destructive_blocked` | 409 | Versión sin aprobación vigente (sin aprobar, revocada o con SQL cambiado). Trae `environment_slug`, `blocked_versions`, `direction`, `approved_versions` y `approval_status` por versión. En `apply-all` trae `blocked_databases[]` |
| `access.self_approval_forbidden` | 409 | El aprobador es el creador o el último editor (`rule`) |
| `destructive_approval.not_found` | 404 | Versión o aprobación inexistente |
| `destructive_approval.not_destructive` | 422 | Ese SQL no es destructivo: no hay nada que aprobar |
| `destructive_approval.down_sql_missing` | 422 | Dirección `down` sin `down_sql` |
| `destructive_approval.scope_invalid` | 422 | La base no usa este blueprint |
| `destructive_approval.sql_changed` | 409 | `expected_sql_hash` distinto del SQL actual |
| `destructive_approval.duplicate` | 409 | Ya hay una aprobación vigente igual |
| `destructive_approval.machine_actor` | 403 | Una máquina intentó aprobar |
| `destructive_approval.executor_forbidden` | 403 | Falta `blueprints.apply_destructive` en la base |
| `destructive_approval.confirmation_required` | 409 | Falta el `confirm_token`, o las aprobaciones cambiaron desde el plan |
| `destructive_approval.confirmation_invalid` | 422 | Token alterado o de otro conjunto de versiones/aprobaciones |
| `destructive_approval.confirmation_expired` | 410 | Token vencido: pedir un dry-run nuevo |
| `destructive_approval.confirm_phrase_mismatch` | 422 | El re-tipeo no coincide (nombre de la base o slug del entorno) |
| `destructive_approval.batch_item_failed` | 500 | Solo dentro de un ítem de la aprobación en lote: error inesperado, el detalle está en el log |
| `destructive_approval.batch_multi_environment` | 422 | El lote toca más de un entorno protegido: filtrar por `environment_id` |

## Cosas que sorprenden

- **Los rollbacks destructivos ahora necesitan aprobación `down`.** Un `down_sql` que borra lo que creó el `up`
  (`DROP TABLE`) cuenta. Quien tenía rollbacks de producción en un runbook debe aprobar la dirección `down` antes.
- **Integración y MCP no consumen aprobaciones.** Aunque exista una aprobación vigente, `apply` por integración sigue
  respondiendo `409 environment.destructive_blocked`.
- **`POST /managed-databases` (alta con `apply_migrations`) no las consume.** El alta no lleva `confirm_token`: la
  base se crea igual (201) y la migración falla con `migration.ok = false` y `migration.error_code` en
  `destructive_approval.confirmation_required` (había aprobación) o `environment.destructive_blocked` (no la había).
  Se termina con el `apply` de la base. `adopt` solo hace `stamp` (no ejecuta SQL), así que el guard no aplica.
- **Editar el SQL invalida la aprobación**, no la borra: queda como evidencia con `stale_hash`.
- **La aprobación sigue la versión por id**, no por número: renumerar o borrar versiones no la mueve.
- **Desplegar esta versión cambia el resultado de `apply-all`** descripto arriba.
