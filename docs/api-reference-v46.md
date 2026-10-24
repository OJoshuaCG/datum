# API v46 — Canales de liberación de blueprints

Addendum de [v45](api-reference-v45.md). Suma **dos superficies nuevas** (el catálogo global de canales y el
puntero de cada blueprint) y **extiende cuatro existentes** (alta de base, aplicar a todas, API de integración y
plan de borrado de una versión). Cómo se usa, en [`features/release-channels.md`](features/release-channels.md); el
porqué de cada decisión, en `docs/development/decisiones-e-incidentes.md`.

Un canal (`stable`, `development`…) es un nombre global al que cada blueprint apunta con un puntero a **una** de
sus versiones. Mover un puntero **no toca ningún motor destino**: es configuración del gateway.

## Capacidades

| Capacidad | Eje | Roles | Step-up | Qué protege |
|---|---|---|:-:|---|
| `release_channels.read` | global | viewer, operator, owner | — | Listar y leer el catálogo |
| `release_channels.write` | global | `security_officer` | ✓ | Crear, editar y borrar canales |
| `blueprints.promote` | entorno | owner (sensible) | ✓ | Promover y retroceder el puntero de un blueprint |

`blueprints.promote` se evalúa en el entorno más protegido entre las bases del blueprint (mismo guard de dos
capas que el borrado de una versión). Con ella el conjunto sensible pasa de 16 a 17.

## Catálogo (`/api/v1/release-channels`)

| Método y ruta | Capacidad | Qué hace |
|---|---|---|
| `GET /release-channels` | `release_channels.read` | Lista paginada. Cada canal trae `pointer_count` |
| `POST /release-channels` | `release_channels.write` | Crea. `201`. Body `{name, slug, resolution_mode, environment_id?}` |
| `GET /release-channels/{id}` | `release_channels.read` | Un canal |
| `PATCH /release-channels/{id}` | `release_channels.write` | Edita `name`, `resolution_mode`, `environment_id`. **El `slug` es inmutable** |
| `DELETE /release-channels/{id}` | `release_channels.write` | Borra. Con punteros exige `?confirm_slug=<slug>` |

`resolution_mode` es un vocabulario cerrado: `manual` (el puntero lo mueve una persona), `derived` (la versión
**mínima** que hoy corre en las bases vivas del entorno enlazado) y `latest` (la punta del blueprint). `derived`
exige `environment_id`.

El sembrado de arranque solo corre con la tabla vacía: `stable` (`manual`, enlazado a `production` si existe) y
`development` (`latest`, sin entorno).

## Puntero de cada blueprint (`/api/v1/database-models/{id}/channels`)

| Método y ruta | Capacidad | Qué hace |
|---|---|---|
| `GET .../channels` | `blueprints.read` | Cada canal con la versión que sirve para este blueprint. **No paginado** |
| `GET .../channels/{slug}/resolve` | `blueprints.read` | Lectura **en vivo y tolerante**. `20/min` |
| `POST .../channels/{slug}/promote` | `blueprints.promote` + step-up | Mueve el puntero de un canal manual |
| `GET .../channels/{slug}/history` | `blueprints.read` | Bitácora del puntero, paginada, la más reciente primero |

**`ChannelResolutionOut`** (listado y `resolve`): `channel_slug`, `channel_name`, `resolution_mode`,
`environment_slug`, `resolved`, `code`, `version`, `model_migration_id`, `source`, `databases[]` y
`derived_reference`.

- `source` es `pointer` (puntero guardado), `tip` (punta del blueprint), `live` (mínimo leído del motor) o
  `inventory_cache` (mínimo calculado con la versión que el gateway tiene anotada para cada base).
- **Un canal que no resuelve no es un error HTTP**: el listado y `resolve` responden `200` con `resolved: false` y
  el motivo en `code`. Solo los consumidores (alta de base, aplicar a todas, integración) lo convierten en `409`.
- `derived_reference` trae el mínimo del entorno **junto** al puntero de un canal manual o `latest` con entorno.
  Es una referencia para la persona y no decide nada.
- El listado calcula los canales `derived` con la **caché del inventario** (`inventory_cache`) para no abrir una
  conexión por base en un `GET`. Solo `resolve` lee el motor. Ningún consumidor usa la caché.
- `databases[]` explica el cálculo `derived`: por base `current_version`, `readable`, `counted` e
  `ignored_reason` (`quarantined`, `database_missing`, `at_base`).

**`POST .../promote`**: body `{version, note?}` (o `model_migration_id`, exactamente uno de los dos). Reglas:

- Solo canales `manual`: otro modo es `409 release_channel.not_manual`.
- Solo versiones **revisadas**: `409 release_channel.version_unreviewed`.
- Un **retroceso** (destino menor que el actual) exige `?confirm_slug=<slug>`; sin él,
  `409 release_channel.promote_backward_requires_confirm`.
- Misma versión que la actual: `200` con `changed: false`, sin escritura y sin evento.
- Versión inexistente: `422 release_channel.version_not_found`; de otro blueprint:
  `422 release_channel.version_not_in_blueprint`.

Respuesta: `{channel_slug, changed, event_type, from_version, to_version, model_migration_id}`.

## Extensiones de superficies existentes

| Superficie | Cambio |
|---|---|
| `POST /managed-databases` | Body `channel` (slug). **Excluyente** con `target_version` y exige `apply_migrations`. La respuesta trae `resolved_channel {slug, mode, version, source}` |
| `POST /database-models/{id}/migrations/apply-all` | Query `target_version` (`^\d{4,10}$`) o `channel`, excluyentes. El resultado trae `target_version` y `resolved_channel`. Las bases que ya están por encima del tope informan `pending_versions: []`: **no se retrocede nada** |
| `POST /integration/databases/{db_id}/migrations/apply` (aplicar migraciones) | Body `channel`, excluyente con `version`. Rige la misma regla de «solo hacia adelante» |
| `GET .../migrations/{version}/delete-plan` y `DELETE .../migrations/{version}` | Traen `channel_moves[]`: `{release_channel_id, channel_slug, from_version, to_version, to_migration_id}` |
| `DELETE /environments/{id}` | `409 environment.has_release_channels` (con `channel_count`) si algún canal lo enlaza |

**`channel_moves` y el borrado de una versión.** Si la versión a borrar es la apuntada por un canal, el puntero
**se mueve a la versión revisada inmediatamente anterior** (o queda sin versión, `to_version: null`) en la misma
transacción local del renumerado, y queda un evento `version_deleted` en el historial. El borrado **nunca se
bloquea** por un canal. `requires_confirmation` es ahora `stamp_plan` **o** `channel_moves`: borrar una versión
apuntada por un canal manual exige el `confirm_token` del plan aunque ninguna base se toque. Las escrituras de
`channel_moves` son locales del gateway, no escrituras al motor.

## Códigos de error

Todos viajan en `detail.public_context.code`. El modo inválido no tiene código propio: es el `422` estándar de
Pydantic, sin detalle por campo en producción.

| Código | HTTP | Cuándo |
|---|:-:|---|
| `release_channel.not_found` | 404 / 422 | El canal no existe (404 en la ruta, 422 si llega en un body o query) |
| `release_channel.name_taken` | 409 | Nombre repetido |
| `release_channel.slug_taken` | 409 | Slug repetido |
| `release_channel.environment_required` | 422 | `derived` sin entorno |
| `release_channel.confirmation_required` | 422 | Borrar un canal con punteros sin `confirm_slug` correcto |
| `release_channel.not_manual` | 409 | Promover un canal que no es manual |
| `release_channel.version_not_found` | 422 | La versión no existe |
| `release_channel.version_not_in_blueprint` | 422 | La versión es de otro blueprint |
| `release_channel.version_unreviewed` | 409 | Promover una versión sin revisar |
| `release_channel.promote_backward_requires_confirm` | 409 | Retroceso sin `confirm_slug` |
| `release_channel.no_released_version` | 409 | El canal no tiene versión para el blueprint (solo en consumidores) |
| `release_channel.derived_unreadable` | 409 | Una base que cuenta no se pudo leer, o está en una versión que el blueprint no conoce |
| `release_channel.derived_no_scope` | 409 | Ninguna base del entorno cuenta |
| `release_channel.pointer_inconsistent` | 409 | El puntero apunta a una versión de otro blueprint |
| `release_channel.target_conflict` | 422 | `channel` y `target_version`/`version` a la vez |
| `environment.has_release_channels` | 409 | Borrar un entorno enlazado por canales |

## Cosas que sorprenden

- **`latest` incluye la punta sin revisar.** Aprovisionar con `channel=development` sobre una punta sin revisar lo
  rechazan las guardas de revisión de siempre, no el resolver.
- **Desplegar esta versión invalida los `confirm_token` de borrado pendientes**: la huella del plan ahora incluye
  `channel_moves`. La persona vuelve a pedir el plan.
- **Consumir un canal es siempre estricto y en vivo**; la caché del inventario solo alimenta pantallas.
