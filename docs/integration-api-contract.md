# API de integración — guía para integrar otro proyecto

Contrato de la API REST con bearer (`/api/v1/integration/*`) para automatizar, desde el proyecto web de una
persona, un conjunto **cerrado** de operaciones sobre las bases que administra el gateway. Esta guía es para quien
**consume** la API. Para operarla y habilitarla, ver [`features/integration-api-tokens.md`](features/integration-api-tokens.md);
para el detalle interno, [`api-reference.md` §22](api-reference.md) y, para los canales de liberación,
[`api-reference.md` §21](api-reference.md).

> Los ejemplos de respuesta se armaron a partir de los schemas y no se capturaron contra un servidor real; los ids,
> nombres y versiones son ilustrativos. El contrato sale del código.

## 1. Cómo obtener un token

Un token lo emite **una persona** con sesión en el gateway (`POST /api/v1/integration-tokens`, requiere step-up).
El bearer completo se muestra **una sola vez**; guárdalo en un secret store. No hay forma de volver a leerlo.

Formato: `datumint.<public_id>.<secret>`

Un token no tiene poder propio: lo que puede hacer es `scopes del token ∩ vocabulario cerrado ∩ lo que su emisor tiene
hoy`, calculado en cada llamada. Si al emisor le bajan el rol, el token pierde ese poder en la siguiente llamada, sin
reemitirlo.

### Emitir un token (lo hace una persona, no el proyecto integrador)

`POST /api/v1/integration-tokens` (sesión + CSRF). Cuerpo (`extra="forbid"`):

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `name` | string (3 a 128) | sí | Proyecto o pipeline que lo usa. Un token por consumidor |
| `scopes` | string[] (≥ 1) | sí | Ver la tabla de scopes. Solo los que el emisor tiene hoy |
| `server_ids` | int[] | no (default `[]`) | Allowlist de servidores. Obligatoria para los scopes de operación |
| `blueprint_ids` | int[] | no (default `[]`) | Vacía = sin restricción de blueprint. Obligatoria (≥ 1) con `migrations.rollback`/`migrations.stamp` |
| `expires_in_days` | int ≥ 1 | no | Por defecto y como tope: 90 días solo lectura, 30 con escritura, 7 con destructivos |
| `never_expires` | bool | no (default `false`) | Excluyente con `expires_in_days`. Solo si el despliegue lo habilita; nunca con destructivos |
| `note` | string | no | Texto libre |

La respuesta `201` trae `id`, `token_id`, `name`, `scopes`, `suspended_scopes`, `server_ids`, `blueprint_ids`,
`expires_at` (`null` si no vence), `last_used_at`, `revoked_at`, `note`, `active`, `created_at` y **`token`** (el
bearer, única vez). Editar (`PATCH`, todos los campos opcionales y cada uno **reemplaza** al guardado) y revocar
(`DELETE`) también los hace la persona.

## 2. Convenciones

- **Base**: `https://<host>/api/v1/integration`. Todas las rutas de abajo son relativas a esa base.
- **Auth**: `Authorization: Bearer datumint.<public_id>.<secret>` en cada llamada. La cookie de sesión no autentica
  estas rutas, y el bearer no autentica nada fuera de ellas.
- **Content-Type**: `application/json` en los cuerpos.
- **Cuerpos cerrados**: un campo que el endpoint no define (`force`, `password`, `model_id` al crear una base, etc.)
  responde `422`. Nunca se ignora en silencio.
- **Apagado por defecto**: si el despliegue no lo habilitó, todo responde `503 integration.disabled`.

### Respuesta exitosa

```json
{ "data": { }, "message": "texto para humanos (opcional)" }
```

Las listas paginadas agregan `pagination`:

```json
{
  "data": [ ],
  "pagination": { "page": 1, "size": 20, "total": 3, "pages": 1, "has_next": false, "has_prev": false }
}
```

Paginación por query: `?page=1` (≥ 1) y `?size=20` (≥ 1; el máximo lo fija el despliegue). Dentro de `data`, los
campos opcionales pueden venir `null`.

### Error

```json
{
  "detail": {
    "msg": "Mensaje legible",
    "type": "AppHttpException",
    "public_context": { "code": "integration.server_not_allowed" }
  }
}
```

Programa tu lógica sobre **`detail.public_context.code`** (estable), no sobre `msg` (texto para humanos, puede
cambiar). Los errores de validación (`422`) traen `detail.msg` = `Error de validación en: <campos>`; solo en
desarrollo traen además `detail.context` con `{field, msg}` por campo.

## 3. Scopes

Cada ruta exige **un** scope. Un token puede tener cualquier combinación de lectura, escritura y destructivos.

| Scope | Nivel | Ruta |
|---|---|---|
| `servers.list` | lectura | `GET /servers` |
| `databases.list` | lectura | `GET /databases` |
| `blueprint.read_assigned` | lectura | `GET /databases/{db_id}/blueprint` |
| `blueprint.read_channel` | lectura | `GET /databases/{db_id}/channels/{channel_slug}` y `GET /blueprints/{model_id}/channels/{channel_slug}` |
| `migrations.read_version` | lectura | `GET /databases/{db_id}/migrations/version` |
| `databases.create` | escritura | `POST /databases` |
| `engine_users.create` | escritura | `POST /engine-users` |
| `engine_users.assign_profile` | escritura | `POST /engine-users/{user_id}/profiles/{profile_id}` |
| `engine_users.assign_database` | escritura | `POST /engine-users/{user_id}/databases/{db_id}` |
| `databases.assign_blueprint` | escritura | `PUT /databases/{db_id}/blueprint` |
| `migrations.apply_forward` | escritura | `POST /databases/{db_id}/migrations/apply` |
| `migrations.rollback` | **destructivo** | `POST /databases/{db_id}/migrations/rollback` |
| `migrations.stamp` | **destructivo** | `POST /databases/{db_id}/migrations/stamp` |

Además del scope, cada llamada se valida contra: la allowlist de servidores del token, la allowlist de blueprints
(si la tiene) y el rol real del emisor sobre el servidor de destino.

## 4. Endpoints

### 4.1 `GET /servers` — listar servidores

Scope `servers.list`. Query: `page`, `size`. Devuelve solo los servidores de la allowlist que el emisor puede leer.
Sin host, puerto ni credenciales.

`data[]`: `id` (int), `name` (string), `engine` (string, p. ej. `mysql`, `postgres`).

```bash
curl -s "https://gateway.example.com/api/v1/integration/servers?page=1&size=20" \
  -H "Authorization: Bearer $DATUM_TOKEN"
```

```json
{
  "data": [{ "id": 12, "name": "prod-mysql-01", "engine": "mysql" }],
  "pagination": { "page": 1, "size": 20, "total": 1, "pages": 1, "has_next": false, "has_prev": false }
}
```

### 4.2 `GET /databases` — listar bases de un servidor

Scope `databases.list`.

| Parámetro | En | Tipo | Req. | Notas |
|---|---|---|---|---|
| `server_id` | query | int ≥ 1 | **sí** | Debe estar en la allowlist |
| `page`, `size` | query | int | no | Paginación |

`data[]` (`IntegrationDatabaseOut`): `id`, `name`, `server_id`, `status` (string) siempre presentes; opcionales o
`null`: `model_id` (blueprint asignado), `model_version`, `environment_id`, `charset`, `collation`.

Se omiten las bases que el emisor no puede leer y las que tienen un blueprint fuera de la allowlist del token
(una base **sin** blueprint sí se lista, para poder asignarle uno).

```bash
curl -s "https://gateway.example.com/api/v1/integration/databases?server_id=12" \
  -H "Authorization: Bearer $DATUM_TOKEN"
```

### 4.3 `GET /databases/{db_id}/blueprint` — blueprint asignado

Scope `blueprint.read_assigned`. Path: `db_id` (int). Respuesta `data`: `model_id` (int), `name` (string), `slug`
(string), `model_version` (string o `null`). Si la base no tiene blueprint: `404 integration.blueprint_not_assigned`.

### 4.3 bis `GET /databases/{db_id}/channels/{channel_slug}` — versión que sirve un canal

Scope `blueprint.read_channel`. Responde qué versión sirve un canal de liberación (por ejemplo `stable`) para el
blueprint **asignado a la base** `db_id`. El blueprint sale de la base y nunca del llamador: por eso rigen la
allowlist de servidores y la de blueprints del token. Es de solo lectura y no mueve ningún puntero; la llamada queda auditada como `integration.call`, igual que las demás lecturas.

**Requisitos del token** (los fija la persona que lo emite; ver §1):

- `scopes` incluye `blueprint.read_channel`. Los tokens ya emitidos no lo tienen: hay que emitir uno nuevo o
  editar el `scopes` del existente (el `PATCH` reemplaza la lista completa).
- `server_ids` con el servidor de la base (la allowlist es obligatoria).
- Si `blueprint_ids` no está vacío, el blueprint asignado a la base tiene que estar en esa lista.
- El emisor debe tener hoy la capacidad `blueprints.read` en la base. Si le bajan el rol, el scope queda en
  `suspended_scopes` y la llamada responde `403 integration.scope_missing`.

**Parámetros de ruta**

| Parámetro | Tipo | Validación |
|---|---|---|
| `db_id` | int | Id de una base gestionada. Una base inexistente o de un servidor fuera de la allowlist responde igual (`403 integration.server_not_allowed`) |
| `channel_slug` | string | 1 a 60 caracteres. Se recorta y se pasa a minúsculas antes de buscar (`Stable` = `stable`). Un slug que no existe es `404` |

No lleva cuerpo ni query. Las rutas de lectura no tienen el cupo por IP global; rige el cupo por token (§6).

**Respuesta `200`** — `data`:

| Campo | Tipo | Descripción |
|---|---|---|
| `slug` | string | Slug del canal consultado |
| `mode` | string | `manual`, `latest` o `derived` (cómo se calcula la versión) |
| `version` | string | Versión que sirve el canal para el blueprint. Nunca `null`: si no hay, es un error |
| `source` | string | Siempre `pointer`: todos los modos se sirven desde el puntero |

```json
{ "data": { "slug": "stable", "mode": "manual", "version": "0004", "source": "pointer" } }
```

**Variante por blueprint: `GET /blueprints/{model_id}/channels/{channel_slug}`**

Mismo scope, misma respuesta y mismos errores `release_channel.*`, pero se consulta **sin necesitar una base**: es lo que se
lee antes de crear nada. Cambia solo cómo se autoriza el destino:

- Si el token tiene `blueprint_ids`, `model_id` tiene que estar en esa lista; si no, `403 integration.blueprint_not_allowed`.
  En ese caso no se exige que el blueprint tenga bases ni se mira la allowlist de servidores (el blueprint es la restricción).
- Si el token **no** tiene `blueprint_ids`, rige la regla estricta: el blueprint debe tener bases y todas en servidores de la
  allowlist; un blueprint sin bases responde `403 integration.server_not_allowed`. **Para aprovisionar, emití el token con
  `blueprint_ids`.**
- Un `model_id` inexistente responde igual que uno no permitido (403), para no revelar qué ids existen.

**Qué significa `version` según el modo**

| `mode` | `version` es… | Notas |
|---|---|---|
| `manual` | La versión a la que apunta el puntero del canal para ese blueprint | La mueve una persona con `blueprints.promote`. El puntero solo apunta a versiones **revisadas** |
| `latest` | La versión **revisada** más nueva del blueprint | El gateway mueve el puntero solo al crear o aprobar versiones. Una punta sin revisar no se sirve |
| `derived` | La versión **mínima** que registra el inventario en las bases del blueprint, dentro del entorno enlazado al canal | El gateway reubica el puntero cuando una base cambia de versión, y solo sobre versiones revisadas. No abre conexiones al responder. **No es la versión de la base `db_id`** |

Los canales sembrados por defecto son `stable` (manual, enlazado a `production`) y `development` (latest); un
operador puede crear otros. Para saber cuál es el de producción, pregunta a quien administra el gateway: el nombre
del canal no está garantizado.

**Errores específicos de esta ruta**

| Caso | HTTP | `public_context.code` |
|---|---|---|
| Canal inexistente | 404 | `release_channel.not_found` |
| Base sin blueprint asignado | 404 | `integration.blueprint_not_assigned` |
| Puntero sin valor para ese blueprint (manual sin promover; `latest`/`derived` sin versión revisada que sirva todavía) | 409 | `release_channel.no_released_version` |
| Puntero apuntando a una versión que no es del blueprint | 409 | `release_channel.pointer_inconsistent` |
| Blueprint fuera de la allowlist del token | 403 | `integration.blueprint_not_allowed` |
| Servidor fuera de la allowlist, o base inexistente | 403 | `integration.server_not_allowed` |
| Token sin el scope | 403 | `integration.scope_missing` |

Un canal `derived` sin entorno enlazado, sin bases en una versión o con alguna base ilegible deja el puntero donde estaba (no calcula un mínimo parcial); si nunca tuvo valor, responde `no_released_version`. Los códigos `derived_*` ya no salen de esta ruta: solo describen la referencia del listado.

Los `409 release_channel.*` son **estrictos a propósito**: un canal sin versión inequívoca nunca devuelve un valor
vacío ni cae a "la última". No lo trates como "versión 0": no hay versión liberada todavía.

**Ejemplo**

```bash
curl -H "Authorization: Bearer datumint.<public_id>.<secret>" \
  https://<host>/api/v1/integration/databases/301/channels/stable
```

### 4.4 `GET /databases/{db_id}/migrations/version` — versión de migraciones

Scope `migrations.read_version`. Respuesta `data`: `current_version` (string o `null`), `latest_version` (string o
`null`), `pending` (string[]).

```json
{ "data": { "current_version": "0003", "latest_version": "0005", "pending": ["0004", "0005"] } }
```

### 4.5 `POST /databases` — crear una base **vacía**

Scope `databases.create`. Crea y aprovisiona la base en el motor. No asigna blueprint ni migra (eso lo hacen sus
propios scopes). Responde `201`.

| Campo | Tipo | Req. | Validación |
|---|---|---|---|
| `name` | string | sí | `^[A-Za-z_][A-Za-z0-9_]{0,62}$` |
| `server_id` | int ≥ 1 | sí | En la allowlist |
| `owner_id` | int ≥ 1 | sí | Usuario del motor del **mismo** servidor |
| `environment_id` | int ≥ 1 | no | Clasifica la base (producción, etc.) |
| `charset` | string | no | `^[A-Za-z0-9_]{1,64}$` |
| `collation` | string | no | `^[A-Za-z][A-Za-z0-9_.@\-]{0,127}$` |
| `notes` | string | no | Texto libre |

Rechazados con `422`: `model_id`, `model_version`, `apply_migrations`, `target_version`.

```bash
curl -s -X POST "https://gateway.example.com/api/v1/integration/databases" \
  -H "Authorization: Bearer $DATUM_TOKEN" -H "Content-Type: application/json" \
  -d '{ "name": "cliente_acme", "server_id": 12, "owner_id": 40, "environment_id": 2, "charset": "utf8mb4" }'
```

Respuesta: mismo `IntegrationDatabaseOut` de 4.2.

### 4.6 `POST /engine-users` — crear un usuario del motor

Scope `engine_users.create`. Responde `201`. **No existe campo `password`**: el gateway genera la contraseña y la
devuelve **solo en esta respuesta**. No se puede leer después; si la pierdes, crea otro usuario.

| Campo | Tipo | Req. | Validación |
|---|---|---|---|
| `server_id` | int ≥ 1 | sí | En la allowlist |
| `username` | string | sí | `^[A-Za-z_][A-Za-z0-9_]{0,62}$` |
| `host` | string | no (default `%`) | `^[A-Za-z0-9_.%:\-]{1,255}$`. Solo MySQL/MariaDB; PostgreSQL lo ignora |
| `notes` | string | no | Texto libre |

`data`: `id`, `server_id`, `username`, `host`, `is_active` (bool), `notes`, `has_password` (bool), `created_at`,
`updated_at` (ISO 8601, UTC) y `password` (string, única vez).

```bash
curl -s -X POST "https://gateway.example.com/api/v1/integration/engine-users" \
  -H "Authorization: Bearer $DATUM_TOKEN" -H "Content-Type: application/json" \
  -d '{ "server_id": 12, "username": "app_acme", "host": "%" }'
```

No registres el cuerpo de esta respuesta en tus logs: lleva una credencial.

### 4.7 `POST /engine-users/{user_id}/profiles/{profile_id}` — aplicar un perfil con mapeo explícito

Scope `engine_users.assign_profile`. Path: `user_id`, `profile_id` (int). Cuerpo:

| Campo | Tipo | Req. | Notas |
|---|---|---|---|
| `object_mappings` | array (≥ 1) de `{level, object_ref}` | sí | Un mapeo por nivel del perfil que quieras aplicar |

Cada mapeo:

- `level`: `database` \| `schema` \| `table` \| `column` \| `sequence` \| `routine`. **`global` se rechaza.**
- `object_ref`: `database` (string, **obligatorio**), y según el nivel `schema` (solo PostgreSQL, default `public`),
  `table`, `columns` (string[]), `sequence`, `routine`.
- Cada base nombrada debe ser una base **gestionada** del servidor del usuario (si no, `409 integration.server_mismatch`)
  y su blueprint, si lo tiene, estar permitido por el token.

```bash
curl -s -X POST "https://gateway.example.com/api/v1/integration/engine-users/77/profiles/5" \
  -H "Authorization: Bearer $DATUM_TOKEN" -H "Content-Type: application/json" \
  -d '{ "object_mappings": [
        { "level": "database", "object_ref": { "database": "cliente_acme" } },
        { "level": "table",    "object_ref": { "database": "cliente_acme", "table": "orders" } }
      ] }'
```

Respuesta `data` (`ApplyProfileResult`): `profile_id`, `profile_name`, `engine`, `grants_applied` (int),
`skipped_levels` (string[]), `errors` (string[]). Un perfil con algún privilegio que delegue poder (`ALL PRIVILEGES`,
`GRANT OPTION`) se rechaza **antes** de aplicar nada: `403 integration.profile_requires_grant_admin`.

### 4.8 `POST /engine-users/{user_id}/databases/{db_id}` — aplicar un perfil a una base

Scope `engine_users.assign_database`. Versión simple de 4.7: el gateway arma el mapeo a nivel `database`; los ítems
de otros niveles se reportan como `skipped_levels`. Cuerpo: `{ "profile_id": <int ≥ 1> }` (único campo, requerido).
Respuesta: `ApplyProfileResult`. Usuario inexistente o de otro servidor: el mismo `409 integration.server_mismatch`.

```bash
curl -s -X POST "https://gateway.example.com/api/v1/integration/engine-users/77/databases/301" \
  -H "Authorization: Bearer $DATUM_TOKEN" -H "Content-Type: application/json" \
  -d '{ "profile_id": 5 }'
```

### 4.9 `PUT /databases/{db_id}/blueprint` — asignar un blueprint

Scope `databases.assign_blueprint`. Cuerpo: `{ "model_id": <int ≥ 1> }` (único campo, requerido). Solo asigna a una base
que no tiene uno. Repetir el mismo es un éxito sin cambios. Reemplazar por otro: `409 integration.blueprint_already_assigned`.
Si el token tiene allowlist de blueprints, `model_id` debe estar en ella (`403 integration.blueprint_not_allowed`).
Respuesta: `IntegrationDatabaseOut`.

```bash
curl -s -X PUT "https://gateway.example.com/api/v1/integration/databases/301/blueprint" \
  -H "Authorization: Bearer $DATUM_TOKEN" -H "Content-Type: application/json" \
  -d '{ "model_id": 9 }'
```

### 4.10 `POST /databases/{db_id}/migrations/apply` — aplicar migraciones (solo hacia adelante)

Scope `migrations.apply_forward`. Cuerpo (ambos opcionales; puede enviarse `{}`):

| Campo | Tipo | Default | Notas |
|---|---|---|---|
| `version` | string `^\d{4,10}$` | última disponible | Versión objetivo, inclusiva |
| `dry_run` | bool | `false` | Devuelve el plan sin aplicar nada |

`force` y `on_failure` **no existen** (`422`): el gateway los fija. Una `version` menor a la actual es
`422 integration.migration_target_not_forward`; la actual es un no-op exitoso.

Campos principales de `data` (`MigrationApplyOut`): `managed_database_id`, `database_name`, `server_id`,
`from_version`, `to_version`, `target_version`, `applied_count` (int), `failed` (bool), `quarantined` (bool),
`no_op` (bool), `dry_run` (bool), `pending_versions` (string[]), `blocked_by` (string[], solo en `dry_run`: versiones
que el entorno bloquearía por destructivas) y `results[]` con `{migration_id, version, status: "applied"|"failed",
error, error_code, execution_ms}`. Hay campos adicionales de diagnóstico que una automatización normalmente puede
ignorar.

Distingue los resultados por los flags: plan (`dry_run`), nada que hacer (`no_op`), aplicado, o `failed: true`
(revisa el estado de la base antes de reintentar).

```bash
# 1) ver el plan
curl -s -X POST "https://gateway.example.com/api/v1/integration/databases/301/migrations/apply" \
  -H "Authorization: Bearer $DATUM_TOKEN" -H "Content-Type: application/json" \
  -d '{ "dry_run": true }'

# 2) aplicar hasta una versión
curl -s -X POST "https://gateway.example.com/api/v1/integration/databases/301/migrations/apply" \
  -H "Authorization: Bearer $DATUM_TOKEN" -H "Content-Type: application/json" \
  -d '{ "version": "0004" }'
```

### 4.11 `POST /databases/{db_id}/migrations/rollback` — revertir (destructivo)

Scope `migrations.rollback`. Cuerpo, ambos **requeridos**, patrón `^\d{4,10}$`:

| Campo | Notas |
|---|---|
| `from_version` | La versión en la que está la base **ahora**. Si no coincide, se rechaza |
| `to_version` | Versión a la que volver (queda aplicada). Debe ser anterior a `from_version` |

No hay "un paso atrás" ni "volver a la base". `force`, `purge` y `dry_run` no existen (`422`). Solo se revierte lo que
este gateway aplicó, con la definición que el blueprint tiene hoy. Respuesta `data` (`MigrationRollbackOut`):
`from_version`, `to_version`, `reverted_count`, `reverted_versions[]`, `failed`, `quarantined`, `no_op`, `results[]`.

```bash
curl -s -X POST "https://gateway.example.com/api/v1/integration/databases/301/migrations/rollback" \
  -H "Authorization: Bearer $DATUM_TOKEN" -H "Content-Type: application/json" \
  -d '{ "from_version": "0005", "to_version": "0003" }'
```

### 4.12 `POST /databases/{db_id}/migrations/stamp` — declarar una versión sin ejecutar SQL (destructivo)

Scope `migrations.stamp`. Cuerpo, ambos **requeridos** (`^\d{4,10}$`):

| Campo | Tipo | Notas |
|---|---|---|
| `expected_current_version` | string **o `null`** | La clave es obligatoria; `null` si la base aún no tiene versión. Debe coincidir con la viva |
| `version` | string | Versión a declarar. Debe existir en el blueprint asignado |

Omitir `expected_current_version` es `422`. Marcar la versión en la que ya está es un `200` sin cambios. Nunca limpia
una cuarentena. Respuesta `data` (`MigrationStatusOut`): `managed_database_id`, `model_id`, `slug`, `current_version`,
`latest_available`, `pending_count`, `pending_versions[]`, `cached_version`, `has_orphan_accounting`,
`has_partial_application`, entre otros.

```bash
curl -s -X POST "https://gateway.example.com/api/v1/integration/databases/301/migrations/stamp" \
  -H "Authorization: Bearer $DATUM_TOKEN" -H "Content-Type: application/json" \
  -d '{ "expected_current_version": "0003", "version": "0005" }'
```

#### Condiciones extra de los dos destructivos

Se rechazan **antes** de tocar nada, con `409` salvo indicación:

- La base debe tener un blueprint que esté en la allowlist del token (`403 integration.blueprint_not_allowed`; falla
  cerrado si la allowlist quedó vacía).
- Entorno que bloquea operaciones destructivas: `integration.environment_blocks_destructive`.
- Base sin entorno asignado: `integration.environment_unclassified`.
- Base en cuarentena: `integration.database_quarantined`.
- Rollback de una versión sin prueba de haberse aplicado por el gateway: `integration.rollback_unapplied_version`
  (trae `versions[]`).
- Stamp con versión esperada distinta de la viva: `integration.stamp_version_conflict`; con contabilidad huérfana:
  `integration.stamp_orphan_accounting`.

## 5. Códigos de error de la API de integración

| `public_context.code` | HTTP | Cuándo |
|---|---|---|
| `integration.disabled` | 503 | La API está apagada en el despliegue |
| `integration.token_invalid` | 401 | Bearer ausente, malformado, desconocido, revocado, vencido o con emisor inactivo (siempre la misma respuesta) |
| `integration.scope_missing` | 403 | El token no tiene el scope efectivo de la ruta |
| `integration.server_not_allowed` | 403 | Servidor fuera de la allowlist **o inexistente** (no se pueden enumerar ids) |
| `integration.blueprint_not_allowed` | 403 | Blueprint fuera de la allowlist del token |
| `integration.profile_requires_grant_admin` | 403 | El perfil delega poder |
| `integration.blueprint_not_assigned` | 404 | La base no tiene blueprint |
| `integration.blueprint_already_assigned` | 409 | La base ya tiene otro blueprint |
| `integration.server_mismatch` | 409 | Usuario o base de otro servidor, o base no gestionada |
| `integration.environment_blocks_destructive` | 409 | Entorno protegido |
| `integration.environment_unclassified` | 409 | Base sin entorno |
| `integration.database_quarantined` | 409 | Base en cuarentena |
| `integration.rollback_unapplied_version` | 409 | Sin prueba de aplicación |
| `integration.stamp_version_conflict` | 409 | Versión esperada ≠ viva |
| `integration.stamp_orphan_accounting` | 409 | Contabilidad de migraciones huérfana |
| `release_channel.not_found` | 404 | El canal pedido no existe |
| `release_channel.no_released_version` | 409 | El canal no tiene versión liberada para el blueprint de la base |
| `release_channel.pointer_inconsistent` | 409 | El puntero apunta a una versión ajena al blueprint |
| `integration.migration_target_not_forward` | 422 | `version` anterior a la actual |
| *(validación)* | 422 | Campo desconocido, faltante o con formato inválido |
| *(límite)* | 429 | Cupo por token o por IP superado (ver abajo) |

Los errores propios del flujo de migraciones (versión desconocida, falta `down_sql`, captura sin revisar, checkpoint
parcial, etc.) llegan con su propio `public_context.code` y estado; trata cualquier código desconocido por su clase HTTP.

## 6. Límites y reintentos

- **Cupos por token** (valores por defecto; el despliegue puede cambiarlos): lectura `120/minute`, escritura
  `20/minute` adicional, destructivos `5/minute` adicional.
- Al superarlos: `429` con header **`Retry-After`** (segundos). Espera ese tiempo antes de reintentar.
- Credenciales rechazadas: cupo por IP (`30/minute` por defecto). Superado, esa IP recibe `429` sin llegar a la BD.
  No reintentes en bucle un `401`: no se va a arreglar solo.
- **Idempotencia**: `PUT .../blueprint` repetido con el mismo valor, `apply` sin pendientes y `stamp` a la versión
  actual son éxitos sin cambios. `POST /databases` y `POST /engine-users` **no** son idempotentes: un reintento tras un
  timeout puede dar `409` por duplicado; consulta antes de reintentar.

## 7. Qué NO existe (a propósito)

Borrar cualquier cosa, editar servidores, leer contraseñas, aplicar a toda la flota, `force`/`purge`/`dry_run` en el
nivel destructivo, acceso a datos de las bases y gestión de tokens por bearer.

## 8. Flujo típico: alta de un cliente

1. `GET /servers` → elegir el servidor.
2. `POST /engine-users` → guardar la `password` devuelta en tu secret store.
3. `POST /databases` (con el `owner_id` del paso 2) → base vacía.
4. `PUT /databases/{db_id}/blueprint` → asignar el blueprint.
5. `POST /databases/{db_id}/migrations/apply` con `{"dry_run": true}`, revisar `blocked_by`, y luego sin `dry_run`.
6. `POST /engine-users/{user_id}/databases/{db_id}` → darle al usuario su perfil sobre la base.
7. `GET /databases/{db_id}/migrations/version` → confirmar `pending: []`.

### Ejemplo mínimo en Python

```python
import os
import time
import requests

BASE_URL = "https://gateway.example.com/api/v1/integration"
HEADERS = {"Authorization": f"Bearer {os.environ['DATUM_TOKEN']}"}


def call(method: str, path: str, **kwargs) -> dict:
    response = requests.request(method, f"{BASE_URL}{path}", headers=HEADERS, timeout=30, **kwargs)
    if response.status_code == 429:
        retry_after_seconds = int(response.headers.get("Retry-After", "1"))
        time.sleep(retry_after_seconds)
        response = requests.request(method, f"{BASE_URL}{path}", headers=HEADERS, timeout=30, **kwargs)
    if not response.ok:
        error_code = response.json().get("detail", {}).get("public_context", {}).get("code")
        raise RuntimeError(f"{response.status_code} {error_code}")
    return response.json()


servers = call("GET", "/servers")["data"]
version = call("GET", "/databases/301/migrations/version")["data"]
print(servers, version["pending"])
```

## 9. Seguridad para quien integra

- Guarda el bearer en un secret store, nunca en el repositorio ni en el front-end del navegador: esta API es para
  servidores o pipelines.
- Un token por proyecto o pipeline, con los scopes mínimos y la allowlist de servidores más corta posible.
- Si sospechas una filtración, pide que revoquen el token: corta en la siguiente llamada.
- No registres cuerpos de respuesta de `POST /engine-users` (contienen una contraseña).
