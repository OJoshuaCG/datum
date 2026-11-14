# API Reference — Datum

> Referencia completa para integrar **Datum** en tu desarrollo.
> Documenta cada endpoint, sus parámetros, tipos y valores permitidos, ejemplos de
> uso (`curl` + JSON) y el orden en que deben consumirse para cumplir cada propósito.
>
> Este es el **único** documento de referencia de la API. Los addendums `api-reference-vN.md`
> (v2 a v48) se eliminaron: cada uno describía una feature ya aplicada en el frontend, y su texto
> completo sigue en el historial de git (último commit que los contiene: `b61def1`; el v48 nunca se
> commiteó y vive en la §21). Para recuperar uno: `git show b61def1:docs/api-reference-vN.md`.
> Las secciones 21 a 24 (canales, integración, aprobaciones destructivas y MCP) están escritas
> contra el código; el resto de la API se documenta en las secciones 1 a 20 y en `docs/features/`.
>
> ⚠️ **Los addendums de v6 en adelante NO están consolidados acá.** Siguen sueltos, y
> cada uno cubre una feature distinta — el número **no** indica antigüedad del contrato
> ni "la versión vigente", solo el orden en que se escribieron. Buscalos por título:
>
> | | Feature |
> |---|---|
> | `v6` | Consola SQL (SQL ad-hoc con el usuario del motor que elijas) |
> | `v7` | Catálogo de charsets/collations |
> | `v8` | Conversión de collation de una BD completa |
> | `v9` | Captura de resultados de `SELECT` dentro de una migración |
> | `v10` | Exportación de bases de datos |
> | `v11` | Validación de migraciones y collation de referencia del blueprint |
> | `v12` | Re-aprovisionamiento de BDs gestionadas |
> | `v13` | La captura de `SELECT` pasa de tres llaves a dos |
> | `v14` | Una versión de blueprint se descongela al revertirla |
> | `v15` | Editar una versión de blueprint que ya está aplicada |
> | `v16` | Proyectos: agrupación de blueprints |
> | `v17` | Conversión de collation en lote, versión de contabilidad y deriva |
> | `v18` | Eliminar una versión intermedia de un blueprint |
> | `v19` | Clonar N bases de un servidor a otro en un gesto (lotes de clonado) |
> | `v20` | Los dos relojes de una fila de lote de clonado |
> | `v21` | El alta de una BD gestionada ejecuta las migraciones en vez de declararlas |
> | `v22` | Orden del catálogo de versiones y punta autoritativa |
> | `v23` | **Autorización**: capacidades, `/auth/me`, el 403, alcance por destino y tokens de agente (parcialmente histórico: ver su banner) |
> | `v24` | **Identidades**: `/gateway-users`, tokens de agente y confirmaciones (parcialmente histórico: ver su banner) |
> | `v25` | Slug de blueprint renombrable, contabilidad de versiones huérfana e historial con actor |
> | `v26` | Búsqueda de texto en el SQL de las versiones de un blueprint |
> | `v27` | Cuántas BDs tienen aplicada cada versión del blueprint |
> | `v28` | Autor de una versión del blueprint |
> | `v29` | **Separación de deberes**: `access.admin`/`policy.admin`, `sod_override`, segundo aprobador, ventana de arranque, lectura de auditoría |
>
> El modelo de autorización **vigente**, de punta a punta, está resumido en
> [`features/authorization.md`](features/authorization.md); la §20 de este documento dice dónde
> está el contrato de cada endpoint de autorización.
>
> Si consolidás alguno acá, sacalo de esta tabla en el mismo cambio: una tabla que
> miente sobre qué falta leer es peor que no tenerla.

**Versión de la API:** `v1` · **Base URL:** `https://<host>/api/v1` · **Estado:** inventario y aprovisionamiento, migraciones de blueprints, adopción/reconciliación/snapshot, usuarios del motor, comparación de esquemas, clonado, exportación, consola SQL, conversión de collation, proyectos y entornos, servidor MCP v1 parcial y autorización multiusuario (roles, capacidades, alcance por destino, separación de deberes, step-up y auditoría) implementados (ver [§15](#15-estado-del-proyecto)).

---

## Índice

1. [¿Qué es y qué problema resuelve?](#1-qué-es-y-qué-problema-resuelve)
2. [Conceptos clave](#2-conceptos-clave)
3. [Convenciones de la API](#3-convenciones-de-la-api)
4. [Tipos de datos y enums](#4-tipos-de-datos-y-enums)
5. [Autenticación (`/auth`)](#5-autenticación-auth)
6. [Servidores (`/servers`)](#6-servidores-servers)
7. [Usuarios del motor (`/server-users` y `/servers/{id}/users/*`)](#7-usuarios-del-motor-server-users-y-serversidusers)
8. [Blueprints de BD y sus migraciones (`/database-models`)](#8-blueprints-de-bd-database-models)
9. [Bases de datos gestionadas y migraciones por BD (`/managed-databases`)](#9-bases-de-datos-gestionadas-managed-databases)
10. [Comparación de esquemas entre BDs (`/schema-comparisons`)](#10-comparación-de-esquemas-entre-bds-schema-comparisons)
11. [Catálogo de privilegios (`/privileges`)](#11-catálogo-de-privilegios-privileges)
12. [Perfiles de permisos (`/permission-profiles`)](#12-perfiles-de-permisos-permission-profiles)
13. [Administración: cifrado (`/admin/crypto`)](#13-administración-cifrado-admincrypto)
14. [Health checks](#14-health-checks)
15. [Estado del proyecto](#15-estado-del-proyecto)
16. [Flujos de integración (orden de llamadas)](#16-flujos-de-integración-orden-de-llamadas)
17. [Apéndice: tabla resumen de endpoints](#17-apéndice-tabla-resumen-de-endpoints)
18. [Apéndice: variables de entorno del integrador](#18-apéndice-variables-de-entorno-del-integrador)
19. [Capacidades puntuales (`/gateway-users/{id}/capability-grants`)](#19-capacidades-puntuales-gateway-usersidcapability-grants)
20. [Autorización: dónde está cada contrato](#20-autorización-dónde-está-cada-contrato)
21. [Canales de liberación](#21-canales-de-liberación-release-channels-y-database-modelsidchannels)
22. [Tokens de integración y API `/integration`](#22-tokens-de-integración-y-api-integration)
23. [Aprobación de versiones destructivas para producción](#23-aprobación-de-versiones-destructivas-para-producción)
24. [MCP: acceso de agentes (credenciales, tokens y tools)](#24-mcp-acceso-de-agentes-credenciales-tokens-y-tools)

---

## 1. ¿Qué es y qué problema resuelve?

**Datum** es un controlador central que permite a un administrador
gestionar **múltiples servidores remotos de bases de datos** (MySQL, MariaDB,
PostgreSQL) a través de una única API HTTP, **sin exponer nunca las credenciales
pseudo-root** de esos servidores.

Resuelve tres problemas:

1. **Segregación de credenciales.** Las contraseñas pseudo-root se almacenan cifradas
   (Fernet) en la base de datos de metadatos del propio gateway. Nunca se serializan en
   respuestas ni se escriben en logs: las respuestas solo informan un booleano
   `has_root_password` / `has_password`.
2. **Inventario + aprovisionamiento.** Mantiene un catálogo de servidores, usuarios,
   bases de datos y privilegios, y puede orquestar su creación/eliminación en el motor
   destino vía DDL/DCL (`CREATE USER`, `CREATE DATABASE`, `GRANT`, `DROP`, …).
3. **Introspección segura de estructura.** Permite inspeccionar bases de datos, tablas y
   esquemas de columnas **sin leer datos de las filas**, validando todos los
   identificadores para evitar inyección SQL.

```
                ┌────────────────────────────────────┐
  Admin  ─────▶ │   Datum                             │
 (cookie)       │   FastAPI + BD de metadatos (cifr.) │
                └──────────────────┬─────────────────┘
                                   │  pseudo-root cifrada (Fernet)
            ┌──────────────────────┼──────────────────────┐
            ▼                      ▼                      ▼
      MySQL / MariaDB         PostgreSQL              MySQL ...
       (servidor 1)          (servidor 2)           (servidor N)
```

### Modelo de operación: inventario vs. motor destino

Toda la API distingue dos tipos de efecto:

- **Operaciones de inventario** — solo leen/escriben en la BD de metadatos del gateway.
  Son rápidas y no dependen de que el servidor destino esté accesible.
- **Operaciones que tocan el motor destino** (marcadas con **🔌** en este documento) —
  abren una conexión al servidor remoto y ejecutan SQL real. Pueden fallar si el
  servidor no es alcanzable (`502`), agota el tiempo de espera (`504`) o el recurso
  ya existe (`409`).

Muchos endpoints de escritura aceptan un flag (`?provision=true` / `?drop_remote=true`)
que decide si la operación es solo de inventario o también toca el motor.

---

## 2. Conceptos clave

| Entidad | Descripción |
|---|---|
| **Server** | Un servidor de BD remoto registrado en el inventario (host, puerto, motor, credencial pseudo-root cifrada). |
| **ServerUser** | Un usuario/rol real del motor que el gateway gestiona. En MySQL es `'usuario'@'host'`; en PostgreSQL es un `ROLE … LOGIN`. Es el **propietario** de bases de datos. |
| **ManagedDatabase** | Una base de datos real creada/gestionada por el gateway en un servidor. Pertenece a **exactamente un** `ServerUser` (owner) del mismo servidor. |
| **DatabaseModel** | Un *blueprint*/categoría lógica (p. ej. "WhatsApp", "SMS"). Metadato del inventario; varias BDs pueden replicar el mismo modelo. Tiene una secuencia versionada de migraciones SQL ([§8](#8-blueprints-de-bd-database-models)). |
| **SchemaComparison** | Un diff estructural entre dos BDs (mismo motor, o MySQL↔MariaDB), calculado una vez y persistido con TTL. Se puede **adoptar** como nueva versión de un blueprint o **ejecutar** directamente sobre la BD destino ([§10](#10-comparación-de-esquemas-entre-bds-schema-comparisons)). |
| **Privilege** | Entrada del catálogo de privilegios soportados por cada motor (`SELECT`, `CREATE`, …). |
| **AuditLog** | Registro interno de toda operación sensible (no expuesto por la API). |

**Propiedad de una base de datos:**
- *MySQL/MariaDB*: propiedad **lógica** — el owner es el `ServerUser` con `GRANT ALL` sobre la BD.
- *PostgreSQL*: propiedad **nativa** — la BD tiene un `OWNER` (`ALTER DATABASE … OWNER TO`).

**Estados de aprovisionamiento** (`ProvisionStatus`): una BD pasa de `pending` →
`active` si el `CREATE` en el motor tiene éxito, o queda en `error` si falla. **No hay
rollback silencioso**: el registro se conserva con el detalle del error para auditoría y
reintento. `error` también se usa como **cuarentena** tras un fallo de migración a
mitad de camino (ver [§9](#9-bases-de-datos-gestionadas-managed-databases)).

**Aplicación parcial (concepto transversal, importante para la UI de migraciones):**
el DDL de MySQL/MariaDB hace **commit por sentencia** (no es transaccional). Si una
migración de N sentencias falla en la sentencia k, la BD queda **físicamente** con k
cambios aplicados, pero el campo `current_version` de esa BD no se actualiza hasta que
la migración *entera* termina — puede leerse como "sano" aunque no lo esté. El gateway
expone esto explícitamente y ofrece una vía de reconciliación automática y otra manual
(ver [§9](#9-bases-de-datos-gestionadas-managed-databases)). **En PostgreSQL esto casi
nunca ocurre**: el motor ejecuta DDL transaccional, así que una migración fallida se
deshace sola.

**Seguridad transversal:**
- Credenciales cifradas con **Fernet** (derivado de `SECRET_KEY`).
- **Anti-SSRF**: al registrar un servidor se rechazan hosts privados/loopback.
- **Anti-inyección**: identificadores validados contra whitelist y quoteados por dialecto.
- **Doble confirmación** en operaciones destructivas que tocan el motor (hay que repetir
  el nombre exacto del recurso).
- **Auditoría** best-effort de toda mutación; nunca almacena credenciales ni datos.

---

## 3. Convenciones de la API

### Base URL y versionado

La API está versionada como una sub-app montada en `/api/v1`. Los *health checks* viven
en la raíz, fuera del versionado.

```
https://<host>/api/v1/...      ← toda la API funcional
https://<host>/health          ← liveness (sin versión, sin auth)
https://<host>/health/ready    ← readiness (sin versión, sin auth)
```

Documentación interactiva (si `DOCS_ENABLED=true`): `GET /api/v1/docs` (Swagger) y
`GET /api/v1/redoc`.

### Envelope de respuesta

**Todas** las respuestas exitosas usan el envelope `ApiResponse[T]`:

```json
{
  "data": { },
  "message": "Texto opcional para el usuario",
  "pagination": { }
}
```

- `data` — el payload (objeto, lista, string…). Ausente en respuestas sin contenido.
- `message` — texto opcional. Ausente si no se proporciona.
- `pagination` — solo presente en listados paginados.

> Los campos con valor `null` **se omiten** del JSON. Si un endpoint no devuelve
> `message` ni `pagination`, esas claves simplemente no aparecen.

**Excepción al envelope**: `GET /schema-comparisons/{id}/export` ([§10](#10-comparación-de-esquemas-entre-bds-schema-comparisons))
es una descarga de archivo (`Content-Disposition: attachment`, `application/sql`); no
usa `ApiResponse`.

### Paginación

Los listados aceptan dos query params:

| Parámetro | Tipo | Default | Restricción |
|---|---|---|---|
| `page` | int | `1` | `>= 1` |
| `size` | int | `20` | `>= 1`, máximo **200** |

La respuesta incluye un bloque `pagination`:

```json
{
  "data": [ ],
  "pagination": {
    "page": 1,
    "size": 20,
    "total": 150,
    "pages": 8,
    "has_next": true,
    "has_prev": false
  }
}
```

### Errores

Los errores **no** usan el envelope; devuelven el status HTTP correspondiente y un cuerpo
con `detail`:

```json
{ "detail": "Servidor no encontrado." }
```

O, en los endpoints más nuevos, con más estructura (siempre compatible con leer solo
`detail.msg` como el mensaje para el usuario):

```json
{ "detail": { "msg": "Mensaje para el usuario.", "type": "AppHttpException", "context": { } } }
```

Algunos errores traen además **`detail.public_context`**: datos estructurados que la
UI necesita para guiar al usuario (por ejemplo, exactamente qué versiones les falta un
rollback confirmado, o qué dependencias faltan en una selección). A diferencia de
`context` (que es información de debug y **solo aparece con `APP_ENV=development`**),
**`public_context` viaja siempre, en cualquier entorno** — es el campo que hay que leer
para construir flujos guiados de recuperación de error, nunca `context`.

| Código | Significado en el gateway |
|---|---|
| `400` | Petición mal formada. |
| `401` | No autenticado / sesión inválida o usuario inactivo. |
| `404` | El recurso del inventario no existe (o, en `/schema-comparisons`, una BD cruda que no existe de verdad en el motor). |
| `409` | Conflicto: recurso duplicado, dependencias, validación cruzada (p. ej. owner de otro servidor), "ya existe en el motor destino", o un estado que exige resolución previa (cuarentena, aplicación parcial, blueprint sin revisar). |
| `410` | El recurso expiró (hoy: una `SchemaComparison` vencida — [§10](#10-comparación-de-esquemas-entre-bds-schema-comparisons)). Hay que recalcularlo. |
| `422` | Error de validación de Pydantic (tipos/patrones), falta una confirmación obligatoria (`confirm_name`, `confirm_username`, `password` al aprovisionar, `confirm_target_name`/`confirm_token`, `confirm_version`), o una selección/plan que no cierra sus dependencias. |
| `429` | Rate limit excedido. |
| `502` | No se pudo conectar al servidor de base de datos destino. 🔌 |
| `504` | La operación en el servidor destino excedió el tiempo de espera. 🔌 |

### Autenticación

El gateway usa **sesión server-side** con una cookie httpOnly firmada que lleva solo el `sid`. Es
**multiusuario**: cada persona tiene un rol (`viewer`/`operator`/`owner`, también por entorno o
servidor), puede tener las capacidades globales `access_admin` o `security_officer`, y cada endpoint
exige una capacidad. Sin ella responde `403 access.forbidden`. Modelo completo en
[`features/authorization.md`](features/authorization.md).

1. `POST /api/v1/auth/login` con usuario y contraseña → el servidor responde con
   `Set-Cookie` (sesión firmada).
2. En cada petición posterior, **envía la cookie**. Con `curl`, usa un *cookie jar*:
   `-c cookies.txt` para guardarla y `-b cookies.txt` para enviarla.
3. Todos los endpoints bajo `/api/v1` (excepto `login` y la aceptación de invitación) requieren
   la cookie; sin ella devuelven `401`. Todo método no seguro exige además el header
   `X-CSRF-Token` (ver `api-reference-v23.md`).

`POST /api/v1/auth/login` está limitado a **20 peticiones por minuto por IP**, **5 por minuto
por IP + usuario** y **20 por hora por usuario** desde cualquier IP (ver
`api-reference-v24.md` §7). La cookie de sesión no cuenta para este
límite. El resto de
endpoints comparten el rate limit global configurado en el servidor, salvo los
específicos anotados en cada sección (`grants`, `apply-all`, `apply`/`rollback`/`stamp`,
`adopt`/`execute` de schema-comparisons).

---

## 4. Tipos de datos y enums

Valores reutilizados a lo largo de la API:

| Enum | Valores permitidos | Uso |
|---|---|---|
| `EngineType` | `mysql`, `mariadb`, `postgresql` | Motor de un servidor. |
| `ServerStatus` | `active`, `inactive`, `unreachable` | Estado operativo de un servidor en el inventario. |
| `ProvisionStatus` | `pending`, `active`, `error`, `archived` | Consistencia inventario ↔ motor de una BD gestionada. `error` también marca **cuarentena** tras un fallo de migración (ver [§9](#9-bases-de-datos-gestionadas-managed-databases)). |
| `MigrationStatus` | `applied`, `failed` | Desenlace de una migración de blueprint en el historial. |
| `ssl_mode` | `disable`, `allow`, `prefer`, `require`, `verify-ca`, `verify-full` | Modo TLS hacia el servidor destino. `null` o `""` ⇒ sin TLS. Se normaliza a minúsculas. |
| `GrantLevel` | `global`, `database`, `schema`, `table`, `column`, `sequence`, `routine` | Nivel de un grant (§7/§12). `schema` y `sequence` solo PostgreSQL. |

**Tipos de grants** (usados en los endpoints de permisos, §7 y §12):

- `ObjectRef` — objeto destino de un grant; los campos dependen del nivel:
  `{ database?, schema? (solo PG, default "public"), table?, columns?: list[str], sequence?, routine?: {kind: "FUNCTION"|"PROCEDURE", name} }`.
- `GrantInfo` — privilegio efectivo (respuesta de introspección):
  `{ level: GrantLevel, object?: str, privileges: list[str], with_grant_option: bool }`.
- `privileges` — lista de tokens (`SELECT`, `INSERT`, `EXECUTE`, `ALL PRIVILEGES`, …)
  validados contra el catálogo por motor y nivel; uno no soportado da `422`.

**Patrones de identificadores** (validación *fail-fast* en la API, alineada con la
whitelist anti-inyección del motor):

| Campo | Patrón | Notas |
|---|---|---|
| `username` (ServerUser) | `^[A-Za-z_][A-Za-z0-9_]{0,62}$` | Empieza por letra o `_`; hasta 63 chars. |
| `host` (ServerUser) | `^[A-Za-z0-9_.%:\-]{1,255}$` | Solo MySQL/MariaDB; `%` = wildcard (**es un host real**, no significa "todos los hosts" — ver §7). |
| `name` (ManagedDatabase) | `^[A-Za-z_][A-Za-z0-9_]{0,62}$` | Nombre de BD. |
| `charset` / `collation` | `^[A-Za-z0-9_]{1,64}$` | Solo MySQL/MariaDB. |
| `slug` (DatabaseModel) | `^[a-z0-9]+(?:[-_][a-z0-9]+)*$` | kebab/snake en minúsculas. |

Las marcas de tiempo (`created_at`, `updated_at`) son `datetime` en formato ISO 8601.

> **Regla de orden transversal (importante, aplica a `/schema-comparisons` y —
> internamente— a la generación de migraciones): cuando un objeto expone tanto `seq`
> como `phase`, `seq` es la ÚNICA fuente de verdad del orden de ejecución.** `phase` es
> una etiqueta gruesa (1..9) útil para agrupar/filtrar visualmente, pero el orden real
> de ejecución puede **cruzar fases** (una FK puede depender de una `PRIMARY KEY`
> creada en una fase posterior). Ordenar/ejecutar por `phase` produce una secuencia que
> el motor puede rechazar. Ver el detalle completo en [§10](#10-comparación-de-esquemas-entre-bds-schema-comparisons).

---

## 5. Autenticación (`/auth`)

Gestiona la sesión de cada usuario del gateway. Es el punto de entrada obligatorio: sin
una sesión válida, el resto de la API responde `401`.

### `POST /api/v1/auth/login`

Inicia sesión y emite la cookie de sesión. **Rate limit: 20/min por IP · 5/min por IP + usuario ·
20/hora por usuario** (el usuario se compara normalizado: `Admin` y `admin` gastan el mismo cupo;
el límite corre antes de verificar la password).

**Body** (`LoginIn`):

| Campo | Tipo | Requerido | Validación |
|---|---|---|---|
| `username` | string | sí | 1–128 caracteres |
| `password` | string | sí | mínimo 1 carácter |

**Respuesta** `200` — `ApiResponse[AdminOut]` (`AdminOut` = `{id, username}`).

```bash
curl -X POST https://<host>/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -c cookies.txt \
  -d '{"username": "admin", "password": "s3cr3t"}'
```

```json
{ "data": { "id": 1, "username": "admin" }, "message": "Sesión iniciada." }
```

> Errores: `401` credenciales inválidas · `429` demasiados intentos.

### `POST /api/v1/auth/logout`

Cierra la sesión actual. **Requiere sesión.**

**Respuesta** `200` — `ApiResponse[None]`.

```bash
curl -X POST https://<host>/api/v1/auth/logout -b cookies.txt
```

```json
{ "message": "Sesión cerrada." }
```

### `GET /api/v1/auth/me`

Devuelve el usuario autenticado, sus capacidades efectivas y el estado de sus controles. Útil
para validar la sesión y decidir la UI (es una pista: **decide el servidor**). **Requiere sesión.**

**Respuesta** `200` — `ApiResponse[MeOut]` (identidad, capacidades efectivas y estado del
step-up). Los campos de autorización están en `api-reference-v23.md` §1, las capacidades puntuales
en la [§19](#19-capacidades-puntuales-gateway-usersidcapability-grants), `sod_warnings` en
`api-reference-v29.md` §8.5 y `bootstrap_window` en `v29` §10.4. Campos del step-up:

| Campo | Tipo | Significado |
|---|---|---|
| `step_up_capabilities` | string[] | Capacidades del actor que exigen step-up. |
| `step_up_enforced` | bool | `false` si el servidor tiene `STEP_UP_ENFORCED=False`: la SPA no debe pedir contraseña. |
| `step_up_expires_at` | datetime \| null | Fin (UTC) de la ventana de step-up de **esta** sesión. Puede estar en el pasado; `null` = sin ventana. |

```bash
curl https://<host>/api/v1/auth/me -b cookies.txt
```

```jsonc
{
  "data": {
    "id": 4,
    "username": "mlopez",
    "role": "owner",                         // efectivo: máximo sobre los alcances
    "base_role": "operator",                 // rige donde ningún scope_role aplica
    "capabilities": ["databases.read", "databases.drop", "..."],
    "global_capabilities": [],               // access_admin | security_officer
    "scope_roles": [{ "scope_type": "environment", "scope_id": 1, "role": "owner" }],
    "step_up_capabilities": ["databases.drop", "..."],
    "step_up_enforced": true,
    "step_up_expires_at": "2026-10-02T12:05:00",
    "capability_grants": [],                 // las vivas (pending|active) de esta persona
    "previous_login_at": "2026-10-01T09:41:03",
    "last_failed_at": null,
    "catalog_version": "9f2c1a…",
    "sod_warnings": [],
    "bootstrap_window": null                 // solo para quien tiene access.admin
  }
}
```

La SPA hace `safeParse` del envelope completo: todo campo nuevo se declara `.nullish()`.

### `POST /api/v1/auth/password`

Cambia la contraseña **del propio usuario**. No hay parámetro para cambiar la de otro.
**Requiere sesión por cookie** (capacidad `self.read`) y, como todo método no seguro con
cookie, el header `X-CSRF-Token` y un `Origin` permitido. Un `Authorization: Bearer` de agente
no autentica en `/api/v1`: responde `401`. **Rate limit: 5/min por usuario + IP**, contando
todos los intentos y antes de verificar la contraseña actual (la clave es el usuario ya
autenticado, nunca el `sid`: abrir otra sesión no da cupo nuevo).

**Body** (`PasswordChangeIn`):

| Campo | Tipo | Requerido | Validación |
|---|---|---|---|
| `current_password` | string | sí | mínimo 1 carácter |
| `new_password` | string | sí | 1–200 caracteres en el schema; el mínimo real (`PASSWORD_MIN_LENGTH` = 12) lo aplica la política compartida con la invitación y responde con código estable |

**Respuesta** `200` — `ApiResponse[PasswordChangeOut]` (`PasswordChangeOut` = `{revoked_sessions}`:
cuántas sesiones **otras** que la actual se cerraron).

```bash
curl -X POST https://<host>/api/v1/auth/password \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: <token de la cookie gw_csrf>" \
  -b cookies.txt -c cookies.txt \
  -d '{"current_password": "s3cr3t-actual-12", "new_password": "otra-password-larga"}'
```

```json
{ "data": { "revoked_sessions": 2 }, "message": "Contraseña cambiada. Se cerraron las demás sesiones." }
```

**Efecto sobre las sesiones.** Al éxito se cierran **todas** las sesiones del usuario con motivo
`password_change`, **incluida la actual**, y la misma respuesta trae la cookie de una sesión
nueva (el `sid` rota, como en el login). Consecuencias para el cliente:

- La sesión desde la que se cambió **sigue funcionando** sin volver a loguearse, pero el token
  CSRF cambió: la respuesta re-emite la cookie `gw_csrf` (`__Host-gw_csrf` con TLS) y el
  cliente tiene que **releerla** antes del próximo método no seguro; con el token viejo responde
  `403 auth.csrf_invalid`.
- Las demás sesiones reciben `401` con `public_context.code = "auth.session_password_change"`
  en su próximo request.

**Errores** (código en `detail.public_context.code`):

| Status | Código | Cuándo |
|---|---|---|
| `401` | `auth.session_missing` / `auth.session_*` | Sin sesión por cookie (incluye un request solo con bearer de agente). |
| `403` | `auth.csrf_missing` · `auth.csrf_invalid` · `auth.origin_rejected` | Falta o no valida el token CSRF, o el `Origin` es ajeno. |
| `422` | `auth.invalid_current_password` | La contraseña actual no es correcta. No cierra la sesión (no es un `401`) y queda auditado como `auth.password_change_failed`. |
| `422` | `gateway_user.weak_password` | `new_password` más corta que el mínimo. Trae `public_context.min_length`. Es el mismo código que la aceptación de invitación. |
| `422` | `auth.password_unchanged` | `new_password` es igual a la actual. |
| `422` | *(sin código)* | Error de validación de Pydantic (campo faltante, `new_password` > 200). |
| `429` | — | Más de 5 intentos por minuto para el mismo usuario e IP. |

Se audita como `auth.password_changed` (y los fallos de contraseña actual como
`auth.password_change_failed`). Ninguna fila lleva la contraseña, ni la vieja ni la nueva.

### `POST /api/v1/auth/step-up`

Confirma la contraseña **del propio usuario** y abre la ventana de step-up ("sudo mode") de la
sesión actual por `STEP_UP_TTL_SECONDS` (300 s). El login ya abre una. **Requiere sesión por
cookie** (`self.read`) + `X-CSRF-Token`. **Rate limit: 5/min por usuario + IP.** El `sid` **no**
rota: el token CSRF sigue siendo el mismo.

Las operaciones cuya capacidad exige step-up (`/auth/me` → `step_up_capabilities`) responden,
con la ventana cerrada y si el método no es seguro o la capacidad divulga:

```json
{ "detail": { "public_context": { "code": "access.step_up_required", "step_up_ttl_seconds": 300 } } }
```

con status `403`. El cliente llama a este endpoint y **reintenta una vez** el request original:
el 403 sale antes de cualquier efecto. A quien le falta la capacidad le llega `403
access.forbidden`, nunca este código. Las cinco cancelaciones (`POST .../cancel` de clonados, lotes de clonado,
conversiones de collation, sus lotes y `/access-requests/{id}/cancel`) **no** piden step-up
(`STEP_UP_EXEMPT` en `scripts/check_route_capabilities.py`).

**Body** (`StepUpIn`): `{ "password": "<contraseña>" }` (1–200 caracteres).

**Respuesta** `200` — `ApiResponse[StepUpOut]`:

```json
{ "data": { "step_up_expires_at": "2026-10-02T12:05:00", "step_up_ttl_seconds": 300 }, "message": "Contraseña confirmada." }
```

**Errores** (código en `detail.public_context.code`):

| Status | Código | Cuándo |
|---|---|---|
| `400` | `auth.step_up_failed` | Contraseña incorrecta. Trae `attempts_remaining`. **No** es `401`: la sesión sigue viva. Auditado como `auth.step_up_failed`. |
| `401` | `auth.session_step_up_failed` | Quinto fallo seguido: la sesión se revocó. Cualquier request posterior con esa cookie recibe el mismo código. |
| `401` | `auth.session_*` | Sin sesión, o sesión vencida/revocada. |
| `403` | `auth.csrf_missing` · `auth.csrf_invalid` · `auth.origin_rejected` | CSRF u `Origin`. |
| `429` | — | Más de 5 intentos por minuto para el mismo usuario e IP. |

Se audita como `auth.step_up`. `STEP_UP_ENFORCED=False` apaga la exigencia (ver
[authentication](features/authentication.md#step-up-sudo-mode)).

---

## 6. Servidores (`/servers`)

Gestiona el inventario de servidores destino y ofrece operaciones de introspección en
vivo. **Todos los endpoints requieren sesión.** La credencial pseudo-root entra en texto
plano al crear/actualizar, se cifra antes de persistir y **nunca** se devuelve.

### Schema `ServerCreate` (body de creación)

| Campo | Tipo | Requerido | Validación / valores |
|---|---|---|---|
| `name` | string | sí | 1–100 caracteres |
| `host` | string | sí | 1–255 caracteres (rechazado si es privado/loopback — anti-SSRF) |
| `port` | int | sí | 1–65535 |
| `engine` | `EngineType` | sí | `mysql` \| `mariadb` \| `postgresql` |
| `root_username` | string | sí | 1–128 caracteres |
| `root_password` | string | sí | mínimo 1 (se cifra; nunca se devuelve) |
| `ssl_mode` | string \| null | no | uno de los `ssl_mode` válidos; `null`/`""` ⇒ sin TLS |
| `notes` | string \| null | no | — |
| `is_active` | bool | no | default `true` |

`ServerUpdate` (para `PATCH`) tiene **los mismos campos, todos opcionales**. Solo se
actualizan los enviados; `root_password` omitido ⇒ no cambia.

### Schema `ServerOut` (respuesta)

```json
{
  "id": 42,
  "name": "mysql-prod",
  "host": "db.example.com",
  "port": 3306,
  "engine": "mysql",
  "root_username": "gateway_root",
  "ssl_mode": "require",
  "status": "active",
  "is_active": true,
  "notes": "Producción",
  "has_root_password": true,
  "created_at": "2026-06-23T10:00:00Z",
  "updated_at": "2026-06-23T10:00:00Z"
}
```

### Endpoints CRUD (inventario)

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/v1/servers` | Lista paginada de servidores. |
| `POST` | `/api/v1/servers` | Registra un servidor (`201`). |
| `GET` | `/api/v1/servers/{server_id}` | Detalle de un servidor. |
| `PATCH` | `/api/v1/servers/{server_id}` | Actualiza parcialmente. |
| `DELETE` | `/api/v1/servers/{server_id}` | Elimina del inventario. `409 access.scope_has_grants` si todavía hay accesos sobre ese servidor. |

**Crear un servidor:**

```bash
curl -X POST https://<host>/api/v1/servers -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{
        "name": "mysql-prod",
        "host": "db.example.com",
        "port": 3306,
        "engine": "mysql",
        "root_username": "gateway_root",
        "root_password": "super-secreta",
        "ssl_mode": "require"
      }'
```

```json
{ "data": { "id": 42, "name": "mysql-prod", "...": "...", "has_root_password": true },
  "message": "Servidor registrado exitosamente." }
```

> `GET /api/v1/servers/{id}` devuelve `404` si no existe. `POST` puede devolver `409`
> (host:puerto duplicado) o `422` (anti-SSRF / validación).

### Operaciones contra el destino 🔌

Estas requieren que el servidor sea alcanzable; pueden devolver `502`/`504`.

> **Seguridad (anti-SSRF):** si `REMOTE_SSRF_GUARD_ENABLED=True`, el host se revalida **al
> conectar** (no solo al registrar el servidor): un destino que resuelva a loopback,
> link-local/metadata (169.254.169.254), multicast o reservado se rechaza con `422` en cada
> operación contra el motor (cierra el DNS-rebinding).

#### `POST /api/v1/servers/{server_id}/test-connection` 🔌

Verifica conectividad y actualiza el `status` del servidor. Respuesta `ConnectionInfo`:

| Campo | Tipo |
|---|---|
| `ok` | bool |
| `dialect` | string |
| `server_version` | string \| null |

```bash
curl -X POST https://<host>/api/v1/servers/42/test-connection -b cookies.txt
```

```json
{ "data": { "ok": true, "dialect": "mysql", "server_version": "8.0.36" } }
```

#### `GET /api/v1/servers/{server_id}/databases` 🔌

Lista los nombres de las bases de datos del servidor (excluye las del sistema).
Respuesta: `ApiResponse[list[str]]`.

```json
{ "data": ["app_prod", "analytics", "billing"] }
```

#### `GET /api/v1/servers/{server_id}/users` 🔌

Lista los usuarios/roles del motor, **plano** (un `user@host` por cuenta). Respuesta:
`ApiResponse[list[EngineUserInfo]]` (`EngineUserInfo` = `{username, host?}`; `host` solo
en MySQL/MariaDB).

```json
{ "data": [ { "username": "app_user", "host": "%" }, { "username": "readonly" } ] }
```

> Este listado es redundante cuando un username tiene varios hosts (MySQL/MariaDB).
> Para la pantalla principal de gestión de usuarios del motor, usá la **vista agrupada**
> [`GET /servers/{server_id}/users/grouped`](#7-usuarios-del-motor-server-users-y-serversidusers)
> en su lugar — este endpoint sigue existiendo por compatibilidad.

#### `GET /api/v1/servers/{server_id}/databases/{database}/tables` 🔌

Lista las tablas de una base de datos. Respuesta: `ApiResponse[list[str]]`.

#### `GET /api/v1/servers/{server_id}/databases/{database}/tables/{table}/schema` 🔌

Devuelve la estructura de una tabla (**nunca filas**). Respuesta `TableSchema`:

| Campo | Tipo | Detalle |
|---|---|---|
| `database` | string | — |
| `table` | string | — |
| `columns` | list[`ColumnInfo`] | `{name, type, nullable, default?, primary_key, autoincrement, comment?}` |
| `primary_key` | list[string] | columnas que forman la PK |
| `foreign_keys` | list[`ForeignKeyInfo`] | `{name?, columns[], referred_table, referred_columns[]}` |
| `indexes` | list[`IndexInfo`] | `{name, columns[], unique}` |

```bash
curl https://<host>/api/v1/servers/42/databases/app_prod/tables/users/schema -b cookies.txt
```

```json
{
  "data": {
    "database": "app_prod",
    "table": "users",
    "columns": [
      { "name": "id", "type": "INTEGER", "nullable": false, "primary_key": true, "autoincrement": true },
      { "name": "email", "type": "VARCHAR(255)", "nullable": false, "primary_key": false, "autoincrement": false }
    ],
    "primary_key": ["id"],
    "foreign_keys": [],
    "indexes": [ { "name": "ix_users_email", "columns": ["email"], "unique": true } ]
  }
}
```

#### `POST /api/v1/servers/{server_id}/grantable` 🔌

Comprueba **antes** de intentar un grant si la credencial pseudo-root del gateway puede
delegar ciertos privilegios (`WITH GRANT OPTION`). No modifica nada.

**Body** (`GrantableRequest`): `{ level: GrantLevel, object_ref: ObjectRef, privileges: list[str] }`.

**Respuesta** `200` — `ApiResponse[GrantableResult]` (`{can_grant, level, privileges}`).

```bash
curl -X POST https://<host>/api/v1/servers/42/grantable -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{ "level": "database", "object_ref": { "database": "app_prod" }, "privileges": ["SELECT","INSERT"] }'
```

```json
{ "data": { "can_grant": true, "level": "database", "privileges": ["SELECT","INSERT"] } }
```

#### `GET /api/v1/servers/{server_id}/reconcile` 🔌 *(Plan 09)*

Cruza el **plano en vivo** (lo que existe en el motor) con el **inventario** (lo que
gestiona el gateway) y clasifica cada BD y usuario. Read-only. Respuesta `ReconcileResult`:

| Campo | Tipo | Detalle |
|---|---|---|
| `server_id` | int | — |
| `databases` | list | `{name, state, managed_id?, owner_id?, status?}` |
| `users` | list | `{username, host?, state, managed_id?}` |

`state` ∈ `managed` (en motor **y** en inventario) · `unmanaged` (solo en el motor →
**adoptable**) · `orphan` (solo en el inventario → se borró por fuera).

```json
{
  "data": {
    "server_id": 42,
    "databases": [
      { "name": "app_prod",  "state": "managed",   "managed_id": 7, "owner_id": 3, "status": "active" },
      { "name": "legacy_crm","state": "unmanaged" },
      { "name": "ventas_old","state": "orphan",    "managed_id": 9 }
    ],
    "users": [ { "username": "app_user", "host": "%", "state": "managed", "managed_id": 4 } ]
  }
}
```

#### `GET /api/v1/servers/{server_id}/databases/{database}/snapshot` 🔌 *(Plan 09)*

Snapshot **estructural** de una BD (tablas, vistas, rutinas, triggers, y según motor
secuencias/tipos/extensiones/events). **Solo estructura, nunca filas.** Es la *preview*
(no persiste). Respuesta `StructureDump`:

| Campo | Tipo | Detalle |
|---|---|---|
| `database` | string | — |
| `source_engine` | string | `mysql` \| `mariadb` \| `postgresql` |
| `statements` | list | `{object_type, name, ddl}` en orden de dependencia |
| `has_non_portable` | bool | `true` si incluye objetos procedurales (rutinas/triggers/events) |

```json
{
  "data": {
    "database": "legacy_crm", "source_engine": "mysql", "has_non_portable": true,
    "statements": [
      { "object_type": "table", "name": "clientes", "ddl": "CREATE TABLE `clientes` (...)" },
      { "object_type": "view",  "name": "v_top",    "ddl": "CREATE VIEW `v_top` AS ..." }
    ]
  }
}
```

---

## 7. Usuarios del motor (`/server-users` y `/servers/{id}/users/*`)

Gestiona los usuarios/roles reales del motor. Hay **dos formas** de trabajar con ellos,
que conviven:

- **`/server-users`** — recurso de **inventario** (nivel superior, no anidado bajo
  `/servers`; se filtra con `?server_id=`). Opera por `id` de inventario y solo
  funciona con usuarios **adoptados**.
- **`/servers/{id}/users/*`** — opera por **identidad física** (`server_id` + `username`
  + `host`) directamente sobre el motor, **funcionen o no adoptados** en el inventario.
  Incluye la **vista agrupada** (recomendada como pantalla principal) y endpoints
  **batch** que operan sobre un username completo (todos sus hosts) de una sola
  llamada.

**Todos requieren sesión.** El password se cifra y nunca se devuelve.

**Capacidad: elegir una contraseña es `engine_users.credentials`, no `write`.** Quien elige la
credencial de una cuenta del motor la conoce, así que divulga igual que revelarla: solo `owner`
(o una capacidad puntual sensible), con step-up (`403 access.step_up_required`). El gateway
**nunca genera** contraseñas: toda contraseña que guarda la tipeó el actor.

| Operación | Capacidad |
|---|---|
| `POST /server-users/provision`, `POST /servers/{id}/users`, `PATCH /servers/{id}/users/password`, `PATCH /servers/{id}/users/password-all-hosts`, `POST /servers/{id}/users/define-password` | `engine_users.credentials` siempre |
| `POST /server-users`, `PATCH /server-users/{id}` | `engine_users.write`; con `password` en el body, además `engine_users.credentials` |
| `POST /servers/{id}/users/adopt-all-hosts` | `engine_users.write`; con `known_password`, además `engine_users.credentials` |
| `POST /servers/{id}/users/add-host` | `engine_users.write`; con `reuse_password: false`, además `engine_users.credentials` |
| `POST /server-users/adopt`, grants, perfiles | `engine_users.write` |
| `POST /servers/{id}/users/reveal-password` | `engine_users.secrets` |
| `DELETE` con `drop_remote=true` / `DELETE /servers/{id}/users` | `engine_users.drop` |

### 7.1 Por qué existen dos formas (leer antes de diseñar la UI)

En **MySQL/MariaDB** un usuario **no es una entidad única**: `'alice'@'localhost'` y
`'alice'@'%'` son **cuentas separadas**, cada una con su propia contraseña y sus propios
grants. El listado plano (`GET /servers/{id}/users`, [§6](#6-servidores-servers))
devuelve un `user@host` por cuenta, así que un mismo nombre aparece repetido N veces.

En **PostgreSQL** un `ROLE` **no tiene host** (el acceso por host se controla en
`pg_hba.conf`, fuera del alcance SQL). Un usuario = un rol.

**Asimetría por motor (la UI DEBE respetarla):**

| Concepto | MySQL / MariaDB | PostgreSQL |
|---|---|---|
| Identidad de usuario | `'user'@'host'` (varias por nombre) | un `ROLE` (una por nombre) |
| `supports_hosts` (en la respuesta agrupada) | `true` | `false` |
| Columna "host" en la UI | mostrar | **ocultar** |
| Botón "Agregar host" | mostrar | **ocultar** (el endpoint da `422`) |

La respuesta de la vista agrupada trae `supports_hosts`: leela y adaptá la UI —
si es `false`, ocultá la columna host y el botón "Agregar host"; cada usuario tendrá
una sola identidad con `host: null`.

**Por qué los endpoints batch (por username completo):** como cada host es una cuenta
separada en MySQL/MariaDB, gestionar "el usuario alice" como concepto humano obligaba
antes a repetir la misma operación host por host desde el cliente. El admin razona en
términos de "el usuario alice", no de "cada una de sus cuentas": los endpoints batch
cierran esa brecha con **una intención = una llamada**, iterando por dentro sobre el
plano en vivo (no hay sintaxis nativa del motor para "todos los hosts a la vez").

> 🚫 **Trampa a evitar en la UI**: el alcance (un host vs. todos) **NUNCA** debe
> inferirse del valor del campo `host`. En MySQL, `"%"` **es un host real** (comodín de
> conexión al motor), **no** significa "todos los hosts" para el gateway. El alcance
> siempre es una elección **explícita**: un toggle "Este host / Todos los hosts", o el
> campo `scope` en `define-password`.

### 7.2 Endpoints de inventario (`/server-users`, por `id`)

`ServerUserCreate` (body de creación):

| Campo | Tipo | Requerido | Validación / valores |
|---|---|---|---|
| `server_id` | int | sí | `>= 1` |
| `username` | string | sí | patrón `^[A-Za-z_][A-Za-z0-9_]{0,62}$` |
| `host` | string | no | default `"%"`; patrón de host; solo MySQL/MariaDB |
| `password` | string \| null | condicional | mínimo 1; **obligatorio si `?provision=true`** |
| `notes` | string \| null | no | — |
| `is_active` | bool | no | default `true` |

`ServerUserUpdate` (body de `PATCH`): `password?`, `is_active?`, `notes?`. El
`username`/`host`/`server_id` son **inmutables**.

`ServerUserOut` (respuesta):

```json
{
  "id": 7, "server_id": 42, "username": "app_user", "host": "%",
  "is_active": true, "notes": null, "has_password": true,
  "created_at": "2026-06-23T10:00:00Z", "updated_at": "2026-06-23T10:00:00Z"
}
```

| Método | Ruta | Query | Descripción |
|---|---|---|---|
| `GET` | `/api/v1/server-users` | `page`, `size`, `server_id?` | Lista paginada; filtra por servidor. |
| `POST` | `/api/v1/server-users` | `provision=false` | Crea el usuario (`201`). Con `provision=true` 🔌 ejecuta `CREATE USER`. |
| `GET` | `/api/v1/server-users/{user_id}` | — | Detalle. |
| `PATCH` | `/api/v1/server-users/{user_id}` | `provision=false` | Actualiza. Con `provision=true` 🔌 ejecuta `ALTER USER` solo si se envía nuevo `password`. |
| `DELETE` | `/api/v1/server-users/{user_id}` | `drop_remote=false`, `confirm_username?` | Elimina del inventario. Con `drop_remote=true` 🔌 ejecuta `DROP USER`. |
| `GET` | `/api/v1/server-users/{user_id}/databases` | — | Lista las BDs cuyo owner es este usuario. |
| `GET` | `/api/v1/server-users/{user_id}/grants` | `database?` (oblig. en PG) | 🔌 Permisos efectivos del usuario (introspección del motor). |
| `POST` | `/api/v1/server-users/{user_id}/grants` | — | 🔌 Otorga privilegios a un nivel/objeto. |
| `DELETE` | `/api/v1/server-users/{user_id}/grants` | `confirm_grantee?` | 🔌 Revoca privilegios (cuerpo en el `DELETE`; `cascade?` solo PG). |
| `POST` | `/api/v1/server-users/{user_id}/apply-profile/{profile_id}` | — | 🔌 Aplica un [perfil de permisos](#12-perfiles-de-permisos-permission-profiles). |
| `POST` | `/api/v1/server-users/provision` | — | 🔌 Crea + aprovisiona el usuario + aplica grants iniciales (`201`). |
| `POST` | `/api/v1/server-users/adopt` | — | 🔌 **(Plan 09)** Adopta un usuario que **ya existe** en el motor (sin `CREATE USER`, sin password). `404` si no existe; `409` si ya está. Para adoptar **todos los hosts** de un username de una vez, ver `POST /servers/{id}/users/adopt-all-hosts` (§7.4). |

**Crear y aprovisionar un usuario en el motor** 🔌:

```bash
curl -X POST "https://<host>/api/v1/server-users?provision=true" -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{ "server_id": 42, "username": "app_user", "host": "%", "password": "p@ss" }'
```

```json
{ "data": { "id": 7, "server_id": 42, "username": "app_user", "host": "%", "has_password": true },
  "message": "Usuario creado y aprovisionado en el motor." }
```

> Si `provision=true` y no se envía `password` ⇒ `422`. Sin `provision`, el mensaje es
> `"Usuario creado en el inventario."`.

**Eliminar usuario y borrarlo del motor** (doble confirmación) 🔌:

```bash
curl -X DELETE "https://<host>/api/v1/server-users/7?drop_remote=true&confirm_username=app_user" \
  -b cookies.txt
```

```json
{ "message": "Usuario eliminado." }
```

> `drop_remote=true` exige que `confirm_username` coincida **exactamente** con el
> username (si no, `422`). Un usuario que posee BDs no puede eliminarse hasta reasignar o
> borrar esas BDs (regla `RESTRICT` ⇒ `409`).

**Grants granulares 🔌** — otorgan/revocan/consultan privilegios del usuario a
cualquier nivel (`database`, `table`, `column`, …). Diferencias por motor: MySQL/MariaDB
usan `global/database/table/column/routine` y el `host` del usuario; PostgreSQL añade
`schema`/`sequence`, ignora el `host`, y **requiere `?database=`** al consultar grants
de objeto.

#### `GET /api/v1/server-users/{user_id}/grants`

Lee los permisos efectivos del usuario del motor real. **Query:** `database?` (obligatorio
en PostgreSQL para tablas/columnas/secuencias/rutinas). Respuesta `ApiResponse[list[GrantInfo]]`.

```bash
curl -b cookies.txt https://<host>/api/v1/server-users/7/grants
```
```json
{ "data": [
  { "level": "database", "object": "app_prod", "privileges": ["DELETE","INSERT","SELECT","UPDATE"], "with_grant_option": false },
  { "level": "table", "object": "app_prod.items", "privileges": ["SELECT"], "with_grant_option": false }
] }
```

#### `POST /api/v1/server-users/{user_id}/grants`

Otorga privilegios. Pre-chequea `can_grant` (→ `403` si la credencial del gateway no puede
delegar). **Body** (`GrantRequest`): `{ level, object_ref, privileges: list[str], with_grant_option?: bool }`.

```bash
curl -b cookies.txt -X POST https://<host>/api/v1/server-users/7/grants \
  -H "Content-Type: application/json" \
  -d '{ "level": "database", "object_ref": { "database": "app_prod" },
        "privileges": ["SELECT","INSERT","UPDATE","DELETE"] }'
```
```json
{ "data": { "granted": true, "level": "database",
            "privileges": ["SELECT","INSERT","UPDATE","DELETE"], "with_grant_option": false },
  "message": "Privilegio(s) otorgado(s): SELECT, INSERT, UPDATE, DELETE a nivel database." }
```

#### `DELETE /api/v1/server-users/{user_id}/grants`

Revoca privilegios. **Body** (`RevokeRequest`): `{ level, object_ref, privileges: list[str], cascade?: bool }`.
**Query**: `confirm_grantee` (str) — obligatorio si `cascade=true`: repetir el username del grantee.
Respuesta `ApiResponse[None]`.

- `409` si el `grantee` es la propia credencial del gateway (anti auto-lockout).
- `cascade=true` solo en PostgreSQL (revoca privilegios re-delegados); en MySQL/MariaDB → `422`.
- Sin `confirm_grantee` cuando `cascade=true` → `422`.

```bash
# REVOKE simple
curl -b cookies.txt -X DELETE https://<host>/api/v1/server-users/7/grants \
  -H "Content-Type: application/json" \
  -d '{ "level": "table", "object_ref": { "database": "app_prod", "table": "items" }, "privileges": ["DELETE"] }'

# REVOKE ... CASCADE (PostgreSQL) — exige confirmación
curl -b cookies.txt -X DELETE "https://<host>/api/v1/server-users/7/grants?confirm_grantee=analista" \
  -H "Content-Type: application/json" \
  -d '{ "level": "table", "object_ref": { "database": "app_prod", "schema": "public", "table": "items" }, "privileges": ["SELECT"], "cascade": true }'
```

#### `POST /api/v1/server-users/{user_id}/apply-profile/{profile_id}`

Aplica un [perfil de permisos](#12-perfiles-de-permisos-permission-profiles) al usuario.
**Body** (`ApplyProfileRequest`): `{ object_mappings: [{ level, object_ref }] }` (un mapeo
por cada nivel del perfil que quieras aplicar; los sin mapeo se omiten). Best-effort: un
grant que falle no aborta los demás. Respuesta `ApiResponse[ApplyProfileResult]`
(`{profile_id, profile_name, engine, grants_applied, skipped_levels[], errors[]}`).

#### `POST /api/v1/server-users/provision`

Endpoint unificado: crea el usuario, lo aprovisiona (`CREATE USER`) y aplica
`initial_grants`, todo en una llamada (`201`). **Body** (`ServerUserFullCreate`): campos de
`ServerUserCreate` + `initial_grants: [{ level, object_ref, privileges[], with_grant_option? }]`.
Respuesta `ApiResponse[ServerUserFullOut]` (`{user, grants_applied, grant_results[]}`).

```bash
curl -b cookies.txt -X POST https://<host>/api/v1/server-users/provision \
  -H "Content-Type: application/json" \
  -d '{ "server_id": 42, "username": "app_user", "host": "%", "password": "p@ss",
        "initial_grants": [ { "level": "database", "object_ref": { "database": "app_prod" },
          "privileges": ["SELECT","INSERT","UPDATE","DELETE"] } ] }'
```
```json
{ "data": {
    "user": { "id": 7, "server_id": 42, "username": "app_user", "host": "%", "has_password": true },
    "grants_applied": 1,
    "grant_results": [ { "level": "database", "object": "app_prod",
      "privileges": ["SELECT","INSERT","UPDATE","DELETE"], "success": true, "error": null } ] },
  "message": "Usuario 'app_user' aprovisionado. 1 grant(s) aplicado(s)." }
```

### 7.3 Vista agrupada y CRUD por identidad (`/servers/{id}/users/*`)

Esta es la pantalla **recomendada** para la gestión principal de usuarios del motor: una
fila por username, expandible a sus identidades (hosts), cruzando el plano en vivo con
el inventario — **funcionen o no adoptados**.

#### `GET /api/v1/servers/{server_id}/users/grouped`

Vista principal. Cruza el motor real con el inventario del gateway y agrupa por
username. Respuesta `GroupedEngineUsersOut`:

```jsonc
{
  "data": {
    "dialect": "mysql",
    "supports_hosts": true,
    "users": [
      {
        "username": "alice",
        "identity_count": 3,
        "identities": [
          { "host": "localhost", "status": "adopted",   "server_user_id": 12,
            "has_password": true,  "is_active": true, "notes": null },
          { "host": "%",         "status": "unmanaged", "server_user_id": null,
            "has_password": false, "is_active": null, "notes": null },
          { "host": "10.0.0.5",  "status": "orphan",    "server_user_id": 33,
            "has_password": true,  "is_active": true, "notes": "temporal" }
        ]
      }
    ]
  },
  "message": "..."
}
```

**Estados de cada identidad** (`status`): `adopted` (en motor **y** inventario) ·
`unmanaged` (solo en el motor → adoptable) · `orphan` (solo en el inventario → drift,
se borró por fuera del gateway).

**PostgreSQL**: `supports_hosts: false`; cada usuario tiene **una sola** identidad con
`host: null`.

Campos clave por identidad: `server_user_id` (llave para navegar a los grants
`/server-users/{id}/grants`, presente si `status != unmanaged`) y `has_password`
(habilita el botón "Revelar contraseña"). No paginado (trae todo el servidor).

#### `POST /api/v1/servers/{server_id}/users` — crear en el motor

Ejecuta `CREATE USER`. Con `adopt=true` además registra en el inventario guardando la
contraseña **cifrada** (habilita revelarla luego).

**Body** — `EngineUserCreateIn`: `{ username, host? (default "%"), password, adopt? (default false), notes? }`.

**Respuesta 201**: `{ username, host, adopted: bool, server_user_id: number | null }`.

**Errores**: `401` · `404` servidor inexistente · `409` credencial pseudo-root · `422` validación.

#### `PATCH /api/v1/servers/{server_id}/users/password` — cambiar contraseña (un host)

Ejecuta `ALTER USER/ROLE`. Si ya hay fila de inventario, la **sincroniza** (queda
revelable). El flag `adopt` solo aplica si **no** había fila previa.

**Body** — `EnginePasswordChangeIn`: `{ username, host? (default "%"), new_password, adopt? }`.

**Respuesta 200**: `{ username, host, adopted: bool, server_user_id: number | null }`.

**Errores**: `401` · `404` · `409` credencial pseudo-root · `422`.

#### `DELETE /api/v1/servers/{server_id}/users` — eliminar (DROP)

Ejecuta `DROP USER/ROLE` y elimina la fila de inventario si existe. **Destructivo e
irreversible.**

**Query**: `username` (requerido), `host` (opcional, default `%`), `confirm_username`
(requerido, debe **repetir exactamente** el username — doble intención).

```bash
curl -X DELETE "https://<host>/api/v1/servers/42/users?username=alice&host=%25&confirm_username=alice" \
  -b cookies.txt
```

**Errores**: `401` · `404` · `409` el usuario posee BDs gestionadas o es la credencial
pseudo-root · `422` `confirm_username` no coincide.

#### `POST /api/v1/servers/{server_id}/users/add-host` — clonar cuenta a un nuevo host

Clona `'user'@'source_host'` a `'user'@'new_host'` (`CREATE USER`). **Solo MySQL/MariaDB**
→ `422` en PostgreSQL.

**Body** — `AddHostIn`: `{ username, source_host? (default "%"), new_host, reuse_password? (default true), new_password? (requerido si reuse_password=false), copy_grants? (default false), adopt? , notes? }`.

- `reuse_password: true` — copia el **hash** (misma contraseña; el gateway no la descubre en claro).
- `copy_grants: true` — replica permisos del origen; **best-effort** (`grants_error` si falla parcialmente).

**Respuesta 201**: `{ username, new_host, password_mode: "reused"|"new", grants_copied: number, grants_error: string|null, adopted: bool, server_user_id: number|null }`.

> **Advertencia si `copy_grants=true`**: se replican fielmente privilegios **globales**
> y `WITH GRANT OPTION` del origen — avisá del riesgo de sobre-aprovisionamiento.

**Errores**: `401` · `404` · `409` credencial pseudo-root · `422` en PostgreSQL, o
`reuse_password=false` sin `new_password`.

#### `POST /api/v1/servers/{server_id}/users/reveal-password` — revelar contraseña

Devuelve la contraseña **en claro**, solo cuando el gateway la conoce. **Acción auditada.**

**Body**: `{ username, host? }`. **Respuesta 200**: `{ username, host, password }`.

**Límite criptográfico**: el motor solo guarda un **hash irreversible** — una
contraseña que el gateway **nunca conoció** es irrecuperable; el gateway solo puede
revelar una que **él mismo fijó** (create, rotación, o `define-password`, ver abajo).

| Situación | Código |
|---|---|
| Usuario no en el inventario | `404` |
| Adoptado, pero el gateway no conoce la contraseña | `409` |
| Contraseña fijada por el gateway | `200` |

> **UX**: habilitá "Revelar" solo si `has_password: true`. Tratá la contraseña como
> secreto efímero (no la persistas en el cliente).

### 7.4 Operaciones batch (username completo, todos los hosts)

Tres endpoints que operan sobre **todas las identidades en vivo** de un username de una
sola llamada, sin iterar host por host desde el cliente. Todos son **fail-tolerant por
host** (un host que falla no aborta los demás) y devuelven **200/201 con un array
`results[]`** — el desenlace real vive ahí, **no** en el código HTTP.

> ⚠️ **Distinción crítica: DEFINIR vs. ROTAR/CAMBIAR contraseña.** Son operaciones
> **conceptualmente distintas** y la UI **no debe mezclarlas en un mismo flujo**:
>
> | | **Definir** (`define-password`) | **Rotar/Cambiar** (`password`, `password-all-hosts`) |
> |---|---|---|
> | ¿Toca el motor? | **No** — nunca ejecuta `ALTER USER/ROLE` | **Sí** |
> | ¿Qué hace? | Cifra y guarda una contraseña que el admin **ya conoce** | Cambia la contraseña **real** vigente |
> | Riesgo | El gateway **no puede verificar** que sea la real | Ninguno — la real pasa a ser la enviada |
>
> `define-password` resuelve un hueco real: antes, adoptar sin contraseña dejaba
> `has_password: false` **para siempre**, sin forma de rellenarlo salvo rotando de
> verdad (lo que puede romper apps que usan la clave actual). Con `define-password`, el
> admin humano que **sabe** cuál es la contraseña vigente se la "dicta" al gateway sin
> tocar el motor.

#### `POST /api/v1/servers/{server_id}/users/adopt-all-hosts`

Adopta en una sola operación **todas** las identidades en vivo de un username. Nunca
ejecuta `CREATE USER` — solo registra lo que ya existe (`origin='adopted'`).

**Body** — `AdoptAllHostsIn`: `{ username, known_password? (si se envía, se cifra y guarda en TODAS las filas SIN ejecutar ALTER USER), notes? }`.

**Respuesta 201** — `BatchAdoptOut`:
```jsonc
{ "username": "alice", "dialect": "mysql", "total_hosts": 3, "adopted": 2,
  "results": [
    { "host": "localhost", "status": "adopted",        "server_user_id": 41 },
    { "host": "%",         "status": "already_adopted", "server_user_id": 12 },
    { "host": "10.0.0.5",  "status": "adopted",         "server_user_id": 42 }
  ] }
```
`results[].status`: `adopted` | `already_adopted` (no es error — un mix de ambos es un
éxito normal). En PostgreSQL, `results[].host` es `null`.

**Errores**: `401` · `404` el username no existe en vivo (nada que adoptar) · `409`
credencial pseudo-root · `422`.

#### `POST /api/v1/servers/{server_id}/users/define-password`

Cifra y guarda una contraseña ya conocida por el admin, **sin tocar el motor**.

**Body** — `DefineKnownPasswordIn`: `{ username, scope: "host"|"all_hosts", host? (solo si scope="host"; "%" es un host REAL), known_password, adopt_if_missing? (crea la fila para hosts en vivo sin ella), overwrite? (OBLIGATORIO true para sobrescribir una identidad que ya tenía contraseña guardada) }`.

**Respuesta 200** — `KnownPasswordSetOut`:
```jsonc
{ "username": "alice", "scope": "all_hosts", "total_hosts": 2, "updated": 1,
  "results": [
    { "host": "%",         "status": "updated",                  "server_user_id": 12 },
    { "host": "localhost", "status": "conflict_needs_overwrite", "server_user_id": 41 }
  ] }
```
`results[].status`: `updated` | `adopted` | `skipped_not_found` | `conflict_needs_overwrite`
(no es error — reenviar con `overwrite=true` si el admin confirma).

> ⚠️ **Aviso obligatorio en la UI**: el gateway **no verifica** que `known_password` sea
> la real. Si el admin se equivoca, `reveal-password` devolverá luego un valor
> incorrecto sin que nadie lo detecte.

**Errores**: `401` · `404` (`scope="all_hosts"` y el username no existe en vivo) · `409`
credencial pseudo-root · `422`.

#### `PATCH /api/v1/servers/{server_id}/users/password-all-hosts`

Ejecuta `ALTER USER/ROLE` para cambiar la contraseña **real** en **todos** los hosts en
vivo de un username. Es la versión batch de `PATCH /users/password`. **Sí toca el
motor**, fail-tolerant.

**Body** — `EnginePasswordChangeAllHostsIn`: `{ username, new_password, confirm_username (OBLIGATORIO, debe coincidir EXACTO — doble intención), adopt_if_missing? }`.

**Respuesta 200** — `PasswordChangeBatchOut`:
```jsonc
{ "username": "alice", "total_hosts": 2, "updated": 1,
  "results": [
    { "host": "%",         "status": "rotated", "server_user_id": 12,   "adopted": false, "error": null },
    { "host": "localhost", "status": "error",   "server_user_id": null, "adopted": false, "error": "motor caído" }
  ] }
```

> ⚠️ **Fail-tolerant → estado divergente posible**: un host con `status: "error"`
> **conserva la contraseña ANTERIOR** en el motor real. Renderizá `results` por host y
> **nunca** un "contraseña cambiada" genérico que oculte un fallo parcial.

**Errores**: `401` · `404` username no existe en vivo · `409` credencial pseudo-root ·
`422` `confirm_username` no coincide.

### 7.5 Semántica de errores y flujo (resumen de la sección)

| Código | Causa |
|---|---|
| `401` | Sin sesión admin. |
| `404` | Servidor/usuario inexistente; en los batch, el username no existe **en vivo** en el motor. |
| `409` | Credencial **pseudo-root** del gateway (guard anti-lockout); eliminar un usuario con BDs gestionadas; revelar sin que el gateway conozca la contraseña. |
| `422` | Whitelist de `username`/`host`; `add-host` en PostgreSQL; confirmación (`confirm_username`) incorrecta; `reuse_password=false` sin `new_password`. |

**Acciones según `status` de una identidad** (vista agrupada):

| `status` | Qué ofrecer |
|---|---|
| `adopted` | Revelar (si `has_password`) · Rotar · Eliminar · Ver grants (`server_user_id`) |
| `unmanaged` | Adoptar (rotar/crear con `adopt=true`, o el endpoint singular `/server-users/adopt`) · Rotar · Eliminar · Agregar host |
| `orphan` | Resolver **drift**: recrear en el motor (`create`) o limpiar la fila huérfana (`DELETE /server-users/{id}`) |

**Flujo recomendado**: `GET /users/grouped` → leer `supports_hosts` una vez → por
identidad expandida, ofrecer acciones según `status` → por username, ofrecer las
acciones **batch** (adoptar todos, definir/rotar contraseña de todos) → tras cualquier
escritura, recargar la vista agrupada o actualizar el estado local afectado.

---

## 8. Blueprints de BD (`/database-models`)

Gestiona *blueprints*/categorías lógicas de bases de datos. **CRUD puro de inventario; no
toca ningún motor.** Requiere sesión.

### Schemas

`DatabaseModelCreate`:

| Campo | Tipo | Requerido | Validación |
|---|---|---|---|
| `name` | string | sí | 1–100 caracteres |
| `slug` | string | sí | 1–120, patrón `^[a-z0-9]+(?:[-_][a-z0-9]+)*$` |
| `description` | string \| null | no | — |
| `current_version` | string | no | default `"0.0.0"`, máx 50 |
| `is_active` | bool | no | default `true` |

`DatabaseModelUpdate`: mismos campos, **todos opcionales**.

`DatabaseModelOut`: `{id, name, slug, description?, current_version, is_active, created_at, updated_at}`.

### Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/v1/database-models` | Lista paginada. |
| `POST` | `/api/v1/database-models` | Crea (`201`). |
| `GET` | `/api/v1/database-models/{model_id}` | Detalle. |
| `PATCH` | `/api/v1/database-models/{model_id}` | Actualiza. |
| `DELETE` | `/api/v1/database-models/{model_id}` | Elimina. |
| `GET` | `/api/v1/database-models/{model_id}/databases` | BDs que replican este blueprint, **con su estado de despliegue** (`pending_count`, `pending_versions`, `has_partial_application`). El refresco 🔌 vive en `POST .../databases/refresh` (10/min). Ver `api-reference-v11.md` §4. |
| `POST` | `/api/v1/database-models/from-snapshot` 🔌 | **(Plan 09)** Crea un blueprint cuyo baseline (`0001`) es el snapshot estructural de una BD existente. Rate limit **10/min**. Nace `is_baseline=true` y `reviewed=false` (ver el gate R1 más abajo). |

```bash
curl -X POST https://<host>/api/v1/database-models -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{ "name": "WhatsApp", "slug": "whatsapp", "current_version": "1.2.0" }'
```

```json
{ "data": { "id": 3, "name": "WhatsApp", "slug": "whatsapp", "current_version": "1.2.0", "is_active": true },
  "message": "Blueprint creado." }
```

### Migraciones del blueprint (versionado de esquema) 🔧

Un blueprint puede tener una **secuencia versionada de migraciones SQL** (deltas) que el
gateway aplica a las BDs que lo replican. Estos endpoints son **CRUD de inventario** (no
tocan ningún motor); la aplicación real sobre cada BD vive en [§9](#9-bases-de-datos-gestionadas-managed-databases).
El SQL se escribe en estilo **MySQL de referencia** y el gateway lo **auto-traduce** a
PostgreSQL con `sqlglot` (campo calculado `translated`); puedes sobrescribir la traducción
con overrides manuales. Detalle conceptual: [feature doc](features/model-migrations.md).

#### Schemas

`ModelMigrationCreate` (body de creación):

| Campo | Tipo | Requerido | Validación / valores |
|---|---|---|---|
| `version` | string \| null | **no** | patrón `^\d{4,10}$`. **Si se omite, el gateway autoasigna la siguiente secuencial** (`max+1`). Pásala solo para fijarla a mano. Se ordena NUMÉRICAMENTE. |
| `name` | string | sí | 1–200 caracteres |
| `up_sql` | string | sí | delta SQL base (estilo MySQL); 1–262144 chars (256 KB) |
| `up_sql_mysql` | string \| null | no | override manual MySQL/MariaDB; ≤256 KB |
| `up_sql_postgresql` | string \| null | no | override manual PostgreSQL; ≤256 KB |
| `down_sql` | string \| null | no | rollback **confirmado**; ≤256 KB. Sin él, el rollback responde `409` |

> **Versión autoasignada (recomendado).** Como la numeración es entera y secuencial, **`version`
> es opcional**: si se omite, el gateway calcula y asigna **la siguiente** (`max(versión)+1`,
> p. ej. `0007`) de forma autónoma. Pensado para equipos: con varios colaboradores nadie necesita
> consultar antes "cuál fue la última". La asignación es **segura ante concurrencia** (reintenta
> ante colisión con el `UNIQUE` por blueprint). Si pasas `version` manualmente y ya existe → `409`.

`ModelMigrationPatch` (body de `PATCH`): `name?`, `down_sql?`, `up_sql_mysql?`,
`up_sql_postgresql?`, `reviewed?` (aprueba un baseline de snapshot — gate R1, ver abajo).
**No** se puede modificar el SQL de una migración ya aplicada **exitosamente** en alguna
BD (`409`) — un intento que solo falló no congela el SQL.

`ModelMigrationOut` (detalle): `{ id, model_id, version, name, up_sql, up_sql_mysql?,
up_sql_postgresql?, down_sql?, down_sql_suggested?, translated: {mysql, postgresql}, checksum,
source_engine?, is_baseline, has_non_portable, reviewed, created_by_admin_id?,
created_by_username?, created_by_actor_type?, created_at, updated_at }`.
`down_sql_suggested` es un rollback **auto-generado** para operaciones aditivas (revísalo y
confírmalo con `PATCH`).

`ModelMigrationSummary` (item de listado): `{ id, model_id, version, name,
has_mysql_override, has_postgresql_override, has_rollback, checksum, is_baseline, reviewed,
applied_database_count, created_by_admin_id?, created_by_username?, created_by_actor_type?,
created_at }`. `applied_database_count` (cuántas BDs tienen la versión aplicada hoy) está en
`api-reference-v27.md`; los `created_by_*` (autor de la versión, `null` = desconocido), en
`api-reference-v28.md`.

#### Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/v1/database-models/{model_id}/migrations` | Lista paginada (resúmenes). |
| `POST` | `/api/v1/database-models/{model_id}/migrations` | Crea una migración (`201`). **`version` es opcional**: si se omite, el gateway autoasigna la siguiente secuencial (max+1). Devuelve `translated` + `down_sql_suggested`. |
| `GET` | `/api/v1/database-models/{model_id}/migrations/{version}` | Detalle completo. |
| `PATCH` | `/api/v1/database-models/{model_id}/migrations/{version}` | Confirma `down_sql` / añade overrides / **aprueba un baseline de snapshot** (`reviewed: true`, gate R1). |
| `DELETE` | `/api/v1/database-models/{model_id}/migrations/{version}` | Elimina — **solo la última versión** (la punta), **solo si ninguna BD la aplicó con éxito** y **solo si no hay una aplicación parcial sin resolver**; si no, `409`. Un intento que solo **falló** ya **no** bloquea el borrado (antes sí, y como no existe purga de historial la versión quedaba imborrable para siempre): se descarta su historial y queda constancia en auditoría de qué BDs se vieron afectadas. Mismo criterio que el `PATCH` del SQL. |
| `POST` | `/api/v1/database-models/{model_id}/migrations/apply-all` 🔌 | Aplica a las BDs del blueprint. Con `database_ids` se acota a **destinos concretos** (un id ajeno al blueprint → `422` con la lista); sin él, a todas hasta `max_databases`. Acepta `on_failure`. Rate limit **3/min**. |
| `POST` | `/api/v1/database-models/{model_id}/migrations/validate` | Analiza el SQL **antes** de aplicarlo: parseo, traducción a PostgreSQL, siembra de datos, `COLLATE` forzados, sentencias destructivas y tablas internas del gateway. Con `managed_database_id` **🔌** comprueba además contra el catálogo de esa BD que las tablas referenciadas existan. Rate limit **20/min**. Ver `api-reference-v11.md`. |

**Banderas de política (en `ModelMigrationSummary` y en `ModelMigrationOut`).** El gateway publica
la **decisión** de sus propias reglas, no los insumos para recalcularlas: así el cliente puede
bloquear un campo *antes* de que se escriba, en vez de rechazarlo al guardar, y no hay dos copias
de la misma política que puedan desincronizarse.

| Campo | Tipo | Significado |
|---|---|---|
| `sql_frozen` | bool | El SQL ya no se puede modificar: alguna BD la aplicó con éxito, o hay una aplicación parcial a medias. Es la misma condición que dispara el `409` del `PATCH`. |
| `deletable` | bool | El `DELETE` pasaría hoy: es la punta, nadie la aplicó con éxito y no hay parcial sin resolver. |
| `block_reason` | `"applied"` \| `"partial"` \| `"not_tip"` \| `null` | Por qué está restringida. **`not_tip` solo impide borrarla**: editarla sigue permitido. |

**Crear una migración:**

```bash
curl -X POST https://<host>/api/v1/database-models/3/migrations -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{ "version": "0001", "name": "Esquema inicial",
        "up_sql": "CREATE TABLE orders (id INT AUTO_INCREMENT PRIMARY KEY, total INT)" }'
```

```json
{ "data": {
    "version": "0001", "name": "Esquema inicial",
    "up_sql": "CREATE TABLE orders (id INT AUTO_INCREMENT PRIMARY KEY, total INT)",
    "down_sql": null,
    "down_sql_suggested": "DROP TABLE IF EXISTS orders;",
    "translated": {
      "mysql": "CREATE TABLE orders (id INT AUTO_INCREMENT PRIMARY KEY, total INT)",
      "postgresql": "CREATE TABLE orders (id INT GENERATED BY DEFAULT AS IDENTITY NOT NULL PRIMARY KEY, total INT)"
    },
    "checksum": "…" },
  "message": "Migración creada." }
```

**Crear SIN pasar versión (autoasignada — recomendado para equipos):** omite el campo `version`
y el gateway le pone la siguiente secuencial. Si el blueprint ya tiene `0001`, esta queda `0002`:

```bash
curl -X POST https://<host>/api/v1/database-models/3/migrations -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{ "name": "Add status", "up_sql": "ALTER TABLE orders ADD COLUMN status VARCHAR(20)" }'
```

```json
{ "data": { "version": "0002", "name": "Add status", "reviewed": true, "is_baseline": false,
            "down_sql_suggested": "ALTER TABLE orders DROP COLUMN status;", "checksum": "…" },
  "message": "Migración creada." }
```

> **Concurrencia:** si dos colaboradores crean a la vez sin `version`, el gateway resuelve el
> empate (reintenta con el siguiente número); ninguno tiene que consultar la última versión.
> Pasar `version` manualmente sigue siendo válido; una versión explícita duplicada da `409`.

> Cuándo escribir `up_sql_postgresql` manual: `ENUM(...)` inline, `ON UPDATE CURRENT_TIMESTAMP`,
> `UNSIGNED/ZEROFILL`, `ALTER … MODIFY … AUTO_INCREMENT`/`DROP PRIMARY KEY` (PostgreSQL usa
> `ALTER COLUMN … TYPE`/`DROP CONSTRAINT`, que no se derivan automáticamente) y rutinas
> `BEGIN…END` con `;` internos no se traducen de forma fiable. `AUTO_INCREMENT`, backticks y
> `DATETIME` sí. Si aplicás a PostgreSQL una migración con este tipo de DDL sin override, el
> `apply` responde **422** con el detalle de qué construcción no se pudo traducir — antes de
> tocar el motor, nunca ejecuta SQL roto.

**Confirmar el rollback sugerido:**

```bash
curl -X PATCH https://<host>/api/v1/database-models/3/migrations/0001 -b cookies.txt \
  -H "Content-Type: application/json" -d '{ "down_sql": "DROP TABLE IF EXISTS orders" }'
```

#### El gate `reviewed` (R1) — por qué un `apply` puede bloquearse aunque tu versión esté bien

Cuando creás un blueprint **desde un snapshot** (`POST /database-models/from-snapshot`),
su migración baseline (`0001`) contiene **DDL capturado del motor real** — estructura
que el gateway no generó y trata como **potencialmente no confiable** (puede traer
vistas/rutinas/triggers con lógica arbitraria). Por eso ese baseline nace **sin
aprobar** (`reviewed: false`) y el gateway **bloquea cualquier `apply`** de ese
blueprint hasta que un admin lo revise y apruebe.

**El detalle que confunde:** el bloqueo es **a nivel de blueprint**, no de versión.
Aunque crees una **nueva versión** (`0002`, escrita a mano y ya `reviewed: true`) e
intentes actualizar la BD a ella, el `apply` **sigue bloqueado** porque el baseline
`0001` —base de todo el esquema— continúa sin revisar:

```
409 Conflict
{ "detail": { "msg": "El blueprint tiene un baseline de snapshot SIN revisar (0001).
   Contiene DDL capturado del motor: revísalo y apruébalo (PATCH reviewed=true en esa
   versión) antes de aplicar." } }
```

Resolución: `GET .../migrations/{version}` la versión que el mensaje señala → revisar
su `up_sql` → `PATCH .../migrations/{version}` `{ "reviewed": true }` → reintentar el
`apply`. `reviewed` **no** afecta a `stamp` (que no ejecuta SQL).

**Aplicación masiva (a todas las BDs del blueprint)** 🔌:

```bash
curl -X POST "https://<host>/api/v1/database-models/3/migrations/apply-all?max_databases=10" \
  -b cookies.txt
```

| Query (apply-all) | Tipo | Default | Detalle |
|---|---|---|---|
| `max_databases` | int | `10` | `1..100`. Cota de BDs a procesar por llamada (síncrono). |
| `force` | bool | `false` | Override de cuarentena en cada BD (ver §9). |
| `dry_run` | bool | `false` | No aplica: devuelve el plan (pendientes) por BD. |
| `on_failure` | string | `"auto"` | `auto`\|`reconcile`\|`leave` — qué hacer si una migración falla a mitad en alguna BD (ver §9). |

```json
{ "data": { "model_id": 3, "total_databases": 12, "processed": 10,
    "results": [ { "managed_database_id": 5, "database_name": "app_a", "server_id": 1,
                   "ok": true, "applied": [ { "version": "0001", "status": "applied", "execution_ms": 42 } ] } ] },
  "message": "Aplicación masiva ejecutada." }
```

> Continúa con las demás BDs aunque una falle (cada `result` trae `ok`/`error`). El
> fan-out asíncrono real (jobs) es del Plan 06; hoy es síncrono y acotado por `max_databases`.

---

## 9. Bases de datos gestionadas (`/managed-databases`)

Gestiona bases de datos reales en los servidores destino. Cada BD pertenece a un
`ServerUser` (owner) del **mismo servidor**. Requiere sesión.

### Schemas

`ManagedDatabaseCreate` (body de creación):

| Campo | Tipo | Requerido | Validación / valores |
|---|---|---|---|
| `name` | string | sí | patrón `^[A-Za-z_][A-Za-z0-9_]{0,62}$` |
| `server_id` | int | sí | `>= 1` |
| `owner_id` | int | sí | `>= 1`; debe ser un `ServerUser` del mismo `server_id` |
| `model_id` | int \| null | no | `>= 1` (blueprint) |
| `model_version` | string \| null | no | máx 50 |
| `charset` | string \| null | no | patrón charset; MySQL/MariaDB |
| `collation` | string \| null | no | patrón charset; MySQL/MariaDB |
| `notes` | string \| null | no | — |

`ManagedDatabaseUpdate` (body de `PATCH`): `model_id?`, `model_version?`, `charset?`,
`collation?`, `notes?`. El `name`/`server_id`/`owner_id` **no** se editan aquí (para owner
usa `reassign-owner`).

`ReassignOwnerIn`: `{ "owner_id": int }` (nuevo propietario, mismo servidor).

`ManagedDatabaseOut` (respuesta):

```json
{
  "id": 11, "name": "app_prod", "server_id": 42, "owner_id": 7,
  "model_id": 3, "model_version": "1.2.0",
  "charset": "utf8mb4", "collation": "utf8mb4_unicode_ci",
  "status": "active", "notes": null, "origin": "provisioned",
  "created_at": "2026-06-23T10:00:00Z", "updated_at": "2026-06-23T10:00:00Z"
}
```

> `origin` *(Plan 09)*: `provisioned` (creada por el gateway) | `adopted` (preexistente,
> registrada con `POST /managed-databases/adopt`).

### Endpoints

| Método | Ruta | Query | Descripción |
|---|---|---|---|
| `GET` | `/api/v1/managed-databases` | `page`, `size`, `server_id?`, `owner_id?`, `model_id?`, `status?` | Lista paginada con filtros (`status` ∈ `ProvisionStatus`). |
| `POST` | `/api/v1/managed-databases` | `provision=false` | Registra (`201`, status `pending`). Con `provision=true` 🔌 ejecuta `CREATE DATABASE` + `GRANT` al owner. |
| `GET` | `/api/v1/managed-databases/{db_id}` | — | Detalle. |
| `PATCH` | `/api/v1/managed-databases/{db_id}` | — | Actualiza metadata. |
| `DELETE` | `/api/v1/managed-databases/{db_id}` | `drop_remote=false`, `confirm_name?` | Elimina del inventario. Con `drop_remote=true` 🔌 ejecuta `DROP DATABASE`. |
| `POST` | `/api/v1/managed-databases/{db_id}/reassign-owner` | `provision=false` | Cambia el owner (`databases.write`). Con `provision=true` 🔌 revoca/otorga (o `ALTER OWNER` en PG) y exige además `databases.drop` en la BD (v23 §4). |
| `POST` | `/api/v1/managed-databases/adopt` | — | 🔌 **(Plan 09)** Adopta una BD que **ya existe** en el motor (sin `CREATE DATABASE`; status `active`, `origin=adopted`). `404` si no existe; `409` si ya está. Ver **stamp-on-adopt** abajo. |

**Crear y aprovisionar una BD** 🔌:

```bash
curl -X POST "https://<host>/api/v1/managed-databases?provision=true" -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{
        "name": "app_prod",
        "server_id": 42,
        "owner_id": 7,
        "model_id": 3,
        "charset": "utf8mb4",
        "collation": "utf8mb4_unicode_ci"
      }'
```

```json
{ "data": { "id": 11, "name": "app_prod", "status": "active", "owner_id": 7 },
  "message": "Base de datos creada y aprovisionada en el motor." }
```

> Si `owner_id` no pertenece al `server_id` indicado ⇒ `409`. Si el `CREATE` en el motor
> falla, la BD queda con `status: "error"` y el detalle en `notes` (sin rollback). Sin
> `provision`, la BD queda en `status: "pending"` y el mensaje es
> `"Base de datos registrada en el inventario."`.

**Eliminar BD del motor** (doble confirmación) 🔌:

```bash
curl -X DELETE "https://<host>/api/v1/managed-databases/11?drop_remote=true&confirm_name=app_prod" \
  -b cookies.txt
```

```json
{ "message": "Base de datos eliminada." }
```

> `drop_remote=true` exige que `confirm_name` coincida **exactamente** con el nombre de la
> BD (si no, `422`).

**Reasignar propietario** 🔌:

```bash
curl -X POST "https://<host>/api/v1/managed-databases/11/reassign-owner?provision=true" \
  -b cookies.txt -H "Content-Type: application/json" \
  -d '{ "owner_id": 9 }'
```

```json
{ "data": { "id": 11, "owner_id": 9, "...": "..." }, "message": "Propietario reasignado." }
```

**Adoptar una BD preexistente, con "stamp-on-adopt"** 🔌 *(Plan 09)*:

```
POST /api/v1/managed-databases/adopt
Body: { server_id, name, owner_id, model_id?, model_version? }
```

Si pasás `model_id` + `model_version`, el gateway hace **stamp** de esa versión en el
motor destino como parte de la adopción — así un `apply` posterior no reintenta crear
objetos que ya existen físicamente en la BD adoptada. `model_version` requiere
`model_id` y se valida que exista **antes** de insertar (`422` si no). No inyecta
`IF NOT EXISTS`: resuelve el conflicto de "ya existe" sin enmascarar drift.

```bash
curl -X POST https://<host>/api/v1/managed-databases/adopt -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{ "server_id": 5, "name": "legacy_crm", "owner_id": 7, "model_id": 9, "model_version": "0003" }'
```

### Migraciones sobre la BD gestionada 🔌

Aplica/revierte/consulta las migraciones del [blueprint](#8-blueprints-de-bd-database-models)
asignado (`model_id`) **sobre esta BD real**. La versión actual de cada BD la mantiene el
gateway dentro de la propia BD destino (tabla `_gw_v_{slug}`, gestionada con Alembic
embebido); el historial queda en el gateway. Requieren que la BD tenga un blueprint
asignado (`422` si no). Rate limit **10/min** en `apply`/`rollback`/`stamp`/`reconcile-partial`.

| Método | Ruta | Query | Descripción |
|---|---|---|---|
| `GET` | `/api/v1/managed-databases/{db_id}/migrations/status` | — | Versión actual vs. pendientes, y si hay una **aplicación parcial** pendiente de resolver. |
| `POST` | `/api/v1/managed-databases/{db_id}/migrations/apply` | `version?`, `force?`, `dry_run?`, `on_failure?` | **Una sola llamada** aplica secuencialmente, en orden, **todas** las pendientes hasta `version` (o hasta la **última** si se omite). Forward-only (`version` ≤ actual → no-op). `422` si `version` no existe, o si el SQL no se puede traducir con certeza al motor destino; `409` si hay un baseline de snapshot sin revisar (gate R1, §8). Respuesta `MigrationApplyOut` con `from_version`→`to_version` y (si aplica) `reconciliation`. |
| `POST` | `/api/v1/managed-databases/{db_id}/migrations/rollback` | `confirm_version` (**obligatorio**), `target_version?` | **Una sola llamada** revierte secuencialmente hasta `target_version` (anterior a la actual); sin él, revierte solo la última. `409` si alguna versión del camino no tiene `down_sql` confirmado, **o si la BD tiene una aplicación parcial sin resolver** (ver abajo). Respuesta `MigrationRollbackOut` con `from_version`→`to_version`. |
| `POST` | `/api/v1/managed-databases/{db_id}/migrations/stamp` | `version` (**obligatorio**), `force?` | Marca una versión **sin ejecutar SQL** (BDs pre-existentes). ⚠️ Ver la advertencia sobre el anti-patrón más abajo antes de usarlo para "arreglar" un fallo de `apply`. |
| `POST` | `/api/v1/managed-databases/{db_id}/migrations/reconcile-partial` | `confirm_version` (**obligatorio**), `dry_run?`, `force?` | Deshace las sentencias que **sí** se aplicaron de una migración que falló a mitad. Ver el detalle completo más abajo. |
| `GET` | `/api/v1/managed-databases/{db_id}/migrations/history` | `page`, `size` | Historial paginado de aplicaciones. |

**Query params de `apply`:**

| Parámetro | Tipo | Default | Detalle |
|---|---|---|---|
| `version` | string \| null | — | Patrón `^\d{4,10}$`. Versión objetivo: en **una llamada** aplica todas las pendientes hasta ella (o hasta la última si se omite). Forward-only; `422` si no existe. |
| `force` | bool | `false` | Reintenta una BD en **cuarentena** (tras inspección). |
| `dry_run` | bool | `false` | No aplica: devuelve el plan (`from_version` + `pending_versions`). |
| `on_failure` | string | `"auto"` | `auto`\|`reconcile`\|`leave` — ver "Aplicación parcial" abajo. Solo relevante en MySQL/MariaDB. |

**Estado (`status`)** — `MigrationStatusOut`:

```bash
curl https://<host>/api/v1/managed-databases/5/migrations/status -b cookies.txt
```
```jsonc
{ "data": { "managed_database_id": 5, "model_id": 3, "slug": "whatsapp",
            "current_version": null, "latest_available": "0002",
            "pending_count": 2, "pending_versions": ["0001","0002"],
            "has_partial_application": false,   // true = hay algo a medias, ver abajo
            "partial_application": [] } }
```

**Previsualizar (dry-run):**

```bash
curl -X POST "https://<host>/api/v1/managed-databases/5/migrations/apply?dry_run=true" -b cookies.txt
```
```json
{ "data": { "managed_database_id": 5, "database_name": "app_prod", "server_id": 42,
            "dry_run": true, "from_version": null, "to_version": "0002", "target_version": null,
            "no_op": false, "pending_versions": ["0001","0002"] } }
```

**Aplicar:** respuesta `MigrationApplyOut` con el salto `from_version`→`to_version`.

```bash
curl -X POST https://<host>/api/v1/managed-databases/5/migrations/apply -b cookies.txt
```
```json
{ "data": { "managed_database_id": 5, "database_name": "app_prod", "server_id": 42,
            "from_version": null, "to_version": "0002", "target_version": null,
            "applied_count": 2, "failed": false, "quarantined": false, "no_op": false,
            "pending_versions": ["0001","0002"],
            "results": [ { "migration_id": 1, "version": "0001", "status": "applied", "error": null, "execution_ms": 42,
                           "resumed": false, "resumed_from_statement": null, "statement_total": 4, "failed_at_statement_index": null },
                         { "migration_id": 2, "version": "0002", "status": "applied", "error": null, "execution_ms": 31,
                           "resumed": false, "resumed_from_statement": null, "statement_total": 2, "failed_at_statement_index": null } ],
            "reconciliation": null },
  "message": "Aplicadas 2 migración(es): ∅ → 0002." }
```

- `results[].resumed: true` — este intento **retomó automáticamente** desde un fallo
  parcial previo (checkpoint de sentencia); `resumed_from_statement` indica desde cuál.
- `results[].failed_at_statement_index` — si `status: "failed"`, la sentencia (1-based)
  en la que murió, de `statement_total`. Puede ser `null` si la migración no es elegible
  para checkpoint (datos-semilla, objetos no portables, o sentencias con estado de
  sesión) — ahí el fallo sigue siendo todo-o-nada. **Nunca** se expone el SQL de la
  sentencia (puede tener secretos), solo el índice.
- Estos mismos campos aplican en `results[]` de `MigrationRollbackOut`
  (`POST .../migrations/rollback`), con la misma semántica.

> `422` si la `version` objetivo no existe en el blueprint · `409` si hay un **baseline
> de snapshot sin revisar** (gate R1, ver [§8](#8-blueprints-de-bd-database-models)) ·
> `422` si el SQL tiene construcciones de MySQL que no se traducen con certeza a
> PostgreSQL (ver la nota de `up_sql_postgresql` en §8) — se detecta **antes** de tocar
> el motor.

#### Aplicación parcial — qué pasa cuando un `apply` falla a mitad de camino

El DDL de MySQL/MariaDB hace **commit por sentencia** (no es transaccional). Si una
migración de N sentencias falla en la k, la BD queda **físicamente** con k cambios
aplicados, pero `current_version` no se actualiza hasta que la migración *entera*
termina — puede leerse como sano sin estarlo. **En PostgreSQL esto casi nunca ocurre**:
el motor ejecuta DDL transaccional y deshace la migración fallida por sí solo.

El query param **`on_failure`** de `apply` decide qué hacer (solo relevante en
MySQL/MariaDB):

| Valor | Comportamiento |
|---|---|
| `auto` (default) | Si falla a mitad, el backend deshace automáticamente lo aplicado — **pero solo si puede deshacerlo TODO**. Si lo logra, la BD queda limpia en su versión anterior y **NO entra en cuarentena**. |
| `reconcile` | Igual que `auto`, pero si hay sentencias sin reverso las saltea y las reporta (en vez de no intentar nada). |
| `leave` | No deshace nada — comportamiento histórico (cuarentena + checkpoint, requiere intervención manual). |

`MigrationApplyOut.reconciliation` (`null` si no hizo falta) informa qué hizo el
sistema:

```jsonc
// apply?on_failure=auto, la migración 0008 falló en la sentencia 6 de 12
{
  "managed_database_id": 1, "from_version": "0007", "to_version": "0007",
  "applied_count": 0, "failed": true, "quarantined": false,
  "reconciliation": {
    "version": "0008", "attempted": true,
    "undone_count": 5, "statements_to_undo": 5,
    "fully_reconciled": true,
    "unconfirmed_reverses": [], "unreversible_statements": [],
    "error": null
  }
}
```

**Mensaje sugerido para la UI** cuando `reconciliation.fully_reconciled: true`: *"La
migración 0008 falló, pero el sistema deshizo automáticamente los cambios que había
aplicado. La base de datos volvió a la versión anterior (0007) sin intervención
necesaria. Corregí la migración y reintentá."* — esto reemplaza cualquier mensaje de
"la BD quedó en cuarentena, contactá a un DBA" para el caso feliz (la mayoría).

⚠️ **`reconciliation: null` con `failed: true` NO significa "no pasó nada".** Puede ser:
(1) la migración falló en su **primera** sentencia — no había nada que reconciliar
(caso benigno); (2) se usó `on_failure=leave` explícito; (3) `on_failure=auto` pero
había sentencias sin reverso seguro — el sistema **deliberadamente no intentó** una
reconciliación parcial. La única forma confiable de distinguirlos es consultar
`GET .../migrations/status`: si `has_partial_application: true`, es el caso 2 o 3.

**`GET .../migrations/status`** informa el detalle si algo quedó a medias:

```jsonc
{ "has_partial_application": true,
  "partial_application": [
    { "version": "0008", "model_migration_id": 20,
      "applied_statements": 6, "total_statements": 12,
      "reconcilable": true, "reconcilable_with_force": false,
      "reason": null, "statements_to_undo": 6 }
  ] }
```

Puede haber **más de una entrada** en `partial_application[]` (migraciones distintas
que quedaron a medias en momentos distintos). `reconcile-partial` solo resuelve **la de
versión más alta** por llamada — si hay varias, hace falta iterar de la más alta a la
más baja.

**Son TRES estados, no dos.** Tratarlos como dos deja al operador sin salida visible:

| `reconcilable` | `reconcilable_with_force` | Qué ofrecer |
|---|---|---|
| `true` | `false` | «Reconciliar» normal: se deshace todo lo aplicado. |
| `false` | `true` | «Reconciliar» **exigiendo `force`**: hay reversos para parte de lo aplicado y alguna sentencia no tiene ninguno, así que esos cambios quedan en la BD. El endpoint lo acepta con `force=true` — la UI **tiene** que ofrecerlo. |
| `false` | `false` | **No hay vía automática.** Mostrar `reason` y mandar a las dos salidas reales: reintentar `apply` (retoma del checkpoint) o arreglar el esquema a mano y declarar la versión con `stamp?force=true`. |

`reason` viene poblado siempre que `reconcilable` sea `false`, **incluido el caso del
medio**: es lo único que explica por qué la vía normal no está disponible y qué va a
quedar sin deshacer.

⚠️ **Con los dos flags en `false`, el `force` del `stamp` NO se puede deshabilitar en la
UI**: es la única salida que queda. Deshabilitarlo "porque hay una aplicación parcial"
—y a la vez sugerir «Reconciliar», que en ese estado no existe— cierra el círculo y deja
la BD sin forma de salir del estado desde la interfaz.

#### `POST .../migrations/reconcile-partial` — deshacer a mano una aplicación parcial

```
POST /api/v1/managed-databases/{db_id}/migrations/reconcile-partial?confirm_version=0008&dry_run=true
```

| Param | Requerido | Descripción |
|---|---|---|
| `confirm_version` | sí | Debe ser la versión que aparece en `partial_application[].version` (doble intención). |
| `dry_run` | no | `true` devuelve los reversos exactos SIN ejecutar nada. Recomendado llamarlo primero **siempre** — ⚠️ salvo que haya sentencias sin reverso: ver nota abajo. |
| `force` | no | Procede aunque alguna sentencia ya aplicada no tenga reverso — la saltea y la reporta (`409` sin esto). |

```jsonc
// dry_run=true
{ "data": {
    "managed_database_id": 1, "database_name": "db1", "server_id": 2,
    "version": "0008", "applied_statements": 6, "total_statements": 12,
    "statements_to_undo": 6, "unreversible_statements": [], "unconfirmed_reverses": [],
    "dry_run": true,
    "statements": [
      { "seq": 6, "sql": "DROP INDEX \"ix_z\"" },
      { "seq": 5, "sql": "ALTER TABLE \"t\" DROP CONSTRAINT \"fk_x\"" }
    ] } }
```

Con `dry_run=false` (default): mismo shape con `undone_count`, `failed`,
`fully_reconciled`, `remaining_applied_statements` y `results[]` por sentencia.
`unconfirmed_reverses` lista reversos que **sí se ejecutaron** pero no son
demostrablemente seguros (p. ej. recrear una tabla borrada devuelve la estructura, no
las filas) — mostrarlos como aviso, no como error.

⚠️ **`dry_run=true` NO garantiza previsualizar si hay sentencias sin reverso.** El
backend valida `force` **antes** de mirar `dry_run`: si el plan tiene sentencias sin
`down_sql` y no se pasó `force=true`, la llamada responde **409 incluso en modo
dry-run**. Si el primer intento con `dry_run=true` da 409 con
`public_context.unreversible_statements`, reintentá con `dry_run=true&force=true` para
ver el plan completo antes de decidir si se ejecuta de verdad.

**NO es un `downgrade` de Alembic**: la versión parcial nunca se registró, así que no
se toca la tabla de versión — es una **compensación** que devuelve el plano físico al
estado que el ledger ya afirma.

**Requiere que la versión tenga manifiesto de sentencias** (lo tienen las versiones
adoptadas desde un diff estructural — [§10](#10-comparación-de-esquemas-entre-bds-schema-comparisons)).
Sin él, `reconcilable: false` **y** `reconcilable_with_force: false`, con un `reason`
explicando por qué, y este endpoint responde `409`: no hay ningún reverso que ejecutar.
Las salidas son reintentar `apply` (retoma del checkpoint) o `stamp?force=true` tras
reconciliar a mano.

El manifiesto no se puede regenerar: solo lo escribe la adopción por diff estructural, y
un `PATCH` que cambie el SQL lo **borra** a propósito (un manifiesto desalineado haría
que la reconciliación deshiciera la sentencia equivocada). Así que una migración escrita
a mano, o editada después de crearse, **nunca** va a ser reconciliable automáticamente —
para esas versiones la salida manual no es un caso de borde, es el camino normal.

#### ⚠️ El anti-patrón que la UI nunca debe sugerir

La reacción intuitiva ante un `apply` que falla a mitad es `stamp --force` a la versión
que falló y después `rollback`. **Esto EMPEORA el problema**: el `stamp` **afirma** que
la migración se aplicó completa, así que el `rollback` posterior ejecuta **todos** los
reversos contra una BD que solo tiene una fracción de los cambios físicos — los
reversos de lo que nunca corrió fallan ("no existe") y el rollback muere a mitad,
dejando un **tercer** estado inconsistente. Además, `stamp --force` descarta el
checkpoint, que era la única prueba de dónde había quedado.

Las vías correctas, en orden de preferencia: (1) `on_failure=auto`/`reconcile` en el
propio `apply` (automático); (2) `reconcile-partial` (explícito); (3) reintentar
`apply` sin más (retoma del checkpoint). `stamp --force` es solo para "ya reconcilié el
estado físico a mano" — su `409` lo explica.

**Rollback (DESTRUCTIVO — doble confirmación), target-based y secuencial:** `confirm_version` debe
igualar la versión actual; `target_version` (opcional, anterior a la actual) revierte
secuencialmente hasta ahí en **una llamada** (sin él, revierte solo la última).

```bash
# Estando en 0010, revertir hasta 0007 (deshace 0010, 0009, 0008):
curl -X POST "https://<host>/api/v1/managed-databases/5/migrations/rollback?confirm_version=0010&target_version=0007" -b cookies.txt
```
```json
{ "data": { "managed_database_id": 5, "from_version": "0010", "to_version": "0007",
            "target_version": "0007", "reverted_count": 3,
            "reverted_versions": ["0010","0009","0008"], "failed": false, "no_op": false },
  "message": "Revertidas 3 migración(es): 0010 → 0007." }
```

> `422` si `confirm_version` no coincide con la actual o `target_version` no es anterior/no existe ·
> `409` si **alguna** versión del camino no tiene `down_sql` confirmado, o si
> `has_partial_application: true` (resolvé primero con `reconcile-partial` o
> reintentando `apply`).

El cuerpo del `409` por `down_sql` faltante trae siempre (en cualquier entorno)
`detail.public_context.missing_down_sql` con la lista exacta de versiones — es el
campo a leer para armar un flujo guiado (listar esas versiones y enlazar a
`PATCH .../migrations/{version}` de cada una), **no** `detail.context` (dev-only).

**Historial:**

```json
{ "data": [ { "id": 10, "managed_database_id": 5, "model_migration_id": 2, "version": "0002",
              "applied_at": "2026-06-26T10:00:00Z", "status": "applied", "error": null, "execution_ms": 31 } ],
  "pagination": { "page": 1, "size": 20, "total": 2, "...": "..." } }
```

> **Cuarentena:** una migración multi-sentencia que falla a mitad (con `on_failure=leave`,
> o si `auto`/`reconcile` no pudieron reconciliar del todo) deja la BD en
> `status: "error"` (con detalle en `notes`); el siguiente `apply` responde `409` hasta
> que inspecciones y reintentes con `?force=true`. Con `on_failure=auto` y sin
> sentencias irreversibles, este estado no debería acumularse — es la recuperación de
> último recurso, no el flujo esperado. El gateway re-valida el `checksum` antes de
> aplicar: si la migración fue alterada en la BD del gateway, responde `409`.

---

## 10. Comparación de esquemas entre BDs (`/schema-comparisons`)

Compara la **estructura** de dos bases de datos gestionadas (mismo motor, o
MySQL↔MariaDB) y, a partir del diff, permite **adoptar** el resultado como una nueva
versión de un blueprint (Opción A, integrándose con [§8](#8-blueprints-de-bd-database-models))
o **ejecutarlo directamente** sobre la BD destino (Opción B, para BDs sin blueprint).
Ambos lados aceptan una BD ya en el inventario **o** una BD cruda de cualquier servidor
dado de alta (sin necesidad de registrarla primero).

### 10.1 Entidades

`SchemaComparisonSummaryOut` (`data` de crear y de `GET /{id}`):

```
- id:                    number
- source_server_id:      number   (SIEMPRE poblado, sea la BD adoptada o cruda)
- source_database_name:  string   (SIEMPRE poblado)
- target_server_id:      number   (SIEMPRE poblado)
- target_database_name:  string   (SIEMPRE poblado)
- source_database_id:    number | null   (managed_database_id SI está en el inventario; null = BD cruda)
- target_database_id:    number | null   (ídem — null = la Opción A NO está disponible para esta comparación)
- source_engine / target_engine: enum [mysql, mariadb, postgresql]
- cross_flavor_warning:  boolean  (true si es MySQL↔MariaDB — ruido esperable)
- scope_note:            string | null  (p. ej. en PostgreSQL: "solo schema public")
- item_count:            number   (nº de SENTENCIAS del diff — ver nota abajo)
- counts:                object   (mapa object_type → {new?, modified?, dropped?} — cuenta OBJETOS)
- has_destructive:       boolean
- expired:               boolean  (true si venció el TTL → recalcular)
- created_at / expires_at: date
```

> **`item_count` cuenta SENTENCIAS, `counts` cuenta OBJETOS.** Un mismo objeto lógico
> puede generar varias sentencias (p. ej. en PostgreSQL, modificar una columna puede
> rendir 2-3 `ALTER COLUMN`). La selección/ejecución es por `id` de sentencia
> individual, pero conviene **agrupar visualmente por `object_name`** — y, más
> importante, por `op_group` (ver abajo).

`SchemaComparisonItemOut` (cada elemento de `GET /{id}/items`):

```
- id:               number   (clave para selección/ejecución)
- comparison_id:     number
- seq:               number   (orden de EJECUCIÓN — ver §10.2, ÚNICA fuente de verdad)
- object_type:       enum [table, column, primary_key, foreign_key, unique_constraint,
                     check_constraint, index, view, materialized_view, routine,
                     trigger, event, sequence, enum_type, extension]
- object_name:       string   (p. ej. "productos.descripcion")
- change_type:       enum [new, modified, dropped]
- phase:             number   (etiqueta INFORMATIVA de agrupación, 1..9 — NO ordena, ver §10.2)
- sql:               string   (DDL EXACTO a correr en el TARGET)
- risk_flags:        object RiskFlags
- down_sql:          string | null   (rollback inferido; null si no reversible con certeza)
- down_confirmed:    boolean
- op_group:          string | null   (grupo ATÓMICO del cambio — ver §10.2. Puede ser null
                     en comparaciones creadas ANTES de esta funcionalidad: tratalo como
                     "ítem suelto, sin grupo ni dependencias conocidas")
- depends_on:        array<string>   (op_group que deben ejecutarse ANTES — ver §10.2)
- execution_status:  enum | null [applied, failed, skipped]
- execution_error:   string | null
- executed_at:       date | null
```

```
Entidad: RiskFlags (embebido en cada ítem)
- destructive:                boolean   (potencialmente destructivo)
- lock_heavy:                 boolean   (puede tomar locks pesados)
- data_conversion:            boolean   (implica conversión de datos)
- needs_review:               boolean
- requires_individual_review: boolean   (objeto procedural: NUNCA en modos automáticos;
                              obliga selección manual + ver el cuerpo completo)
- cross_flavor_warning:       boolean   (ítem afectado por comparación MySQL↔MariaDB)
- possible_rename_of:         string | null   (heurística advisory: si NO es null, este
                              DROP/CREATE probablemente sea un RENAME de ese objeto)
```

### 10.2 `seq` es el orden; `phase` es solo una etiqueta

**Ordená y ejecutá siempre por `seq`, nunca por `phase`.** `phase` (1..9) es una
etiqueta gruesa del pipeline conceptual (prerrequisitos → tablas → aditivos → cuerpos →
destructivos → …), útil para un filtro o badge de color en la UI, pero el orden
**real** de ejecución puede cruzar fases: una FK (fase 3) puede depender de una
`PRIMARY KEY` que se agrega en fase 4 sobre la tabla referida, y en ese caso la FK se
ejecuta **después**, aunque su `phase` sea menor. `seq` ya viene con eso resuelto — es
el resultado de un ordenador topológico, no de un simple orden por fase.

### 10.3 Grupos atómicos (`op_group`) y dependencias (`depends_on`)

**`op_group` — la unidad de selección es el CAMBIO, no la fila.** Un
índice/UNIQUE/CHECK/FK *redefinido* (mismo nombre, definición distinta) se renderiza
como **dos filas**: un `DROP` del viejo y un `CREATE`/`ADD` del nuevo. Ambas comparten
`op_group`. Si la UI permite tildar checkboxes por fila suelta, hay que agruparlas: **si
el usuario marca una fila de un `op_group`, se marcan todas las de ese grupo** (o se
deshabilita el checkbox individual y se muestra un solo control por grupo). Enviar solo
la mitad de un grupo en `selected_item_ids` se **rechaza con 422**.

**`depends_on` — lista de `op_group` que deben ir ANTES.** Solo lista dependencias que
hay que *crear en esta misma comparación*: algo que ya existe en el destino no aparece.
Úsalo para deshabilitar/advertir si el usuario desmarca un ítem del que otros dependen,
o para un tooltip explicativo. Alternativa más simple: usar `resolve-selection` (§10.6),
que resuelve esto por vos.

### 10.4 Selector de BDs por motor (reusado de otros módulos)

Para armar el selector de las dos BDs a comparar, usá `GET /managed-databases` (filtrado
por `?server_id=` si querés acotar a un servidor, y comparando el campo `engine` del
`ServerOut` correspondiente) para elegir BDs **ya adoptadas**. Para una BD **cruda** de
cualquier servidor (sin adoptar), usá `GET /servers/{server_id}/databases` (§6) para
listar los nombres reales y pasá `{lado}_server_id` + `{lado}_database_name` al crear la
comparación en vez de `{lado}_database_id`.

### 10.5 Endpoints

| Método | Ruta | Rate limit | Descripción |
|---|---|---|---|
| `POST` | `/api/v1/schema-comparisons` | 10/min | Crea la comparación: snapshotea ambas BDs, corre el diff, renderiza el DDL para el motor del target, persiste y devuelve el resumen. |
| `GET` | `/api/v1/schema-comparisons/{id}` | — | Resumen de una comparación ya calculada (sin recomputar). |
| `GET` | `/api/v1/schema-comparisons/{id}/items` | — | Detalle paginado de sentencias (`?page=&size=&object_type=&change_type=`). |
| `GET` | `/api/v1/schema-comparisons/{id}/export` | — | Descarga el diff como archivo `.sql` (ver §10.9). |
| `POST` | `/api/v1/schema-comparisons/{id}/resolve-selection` | — | Cierra una selección parcial (agrega lo que falta) SIN adoptar ni ejecutar. |
| `POST` | `/api/v1/schema-comparisons/{id}/adopt` | 3/min | **Opción A**: crea una nueva versión del blueprint del target con el DDL seleccionado. |
| `POST` | `/api/v1/schema-comparisons/{id}/execute-preview` | — | Resuelve un modo/selección de Opción B **sin ejecutar**: sentencias exactas + `confirm_token`. |
| `POST` | `/api/v1/schema-comparisons/{id}/execute` | 3/min | **Opción B**: ejecuta el DDL directamente sobre el target (sin blueprint). |

Cada BD, en cualquiera de los dos lados, se referencia de una de estas dos formas
(nunca ambas, nunca ninguna): `{lado}_database_id` (BD en el inventario) **o**
`{lado}_server_id` + `{lado}_database_name` (BD cruda de cualquier servidor dado de
alta). Si una referencia cruda coincide con una `ManagedDatabase` ya existente, el
backend la **auto-resuelve** transparentemente (mismo comportamiento que si se hubiera
pasado el id directamente).

#### `POST /api/v1/schema-comparisons` — crear

```bash
curl -X POST "https://<host>/api/v1/schema-comparisons" -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{ "source_database_id": 7, "target_database_id": 12 }'
```
```json
{
  "data": {
    "id": 42,
    "source_server_id": 3, "source_database_name": "productos_ref",
    "target_server_id": 3, "target_database_name": "productos_db",
    "source_database_id": 7, "target_database_id": 12,
    "source_engine": "mysql", "target_engine": "mysql",
    "cross_flavor_warning": false, "scope_note": null,
    "item_count": 18,
    "counts": { "table": { "new": 1, "modified": 1 }, "column": { "new": 2, "modified": 1, "dropped": 1 },
                "index": { "new": 1 }, "routine": { "new": 1 } },
    "has_destructive": true, "expired": false,
    "created_at": "2026-07-13T10:00:00", "expires_at": "2026-07-14T10:00:00"
  },
  "message": "Comparación creada."
}
```

Con un lado **crudo** (sin adoptar): `{ "source_database_id": 7, "target_server_id": 5, "target_database_name": "legacy_db_09" }`
→ si `legacy_db_09` no está en el inventario, la respuesta trae `target_database_id: null`
(la Opción A queda deshabilitada para esta comparación: ocultá ese botón en la UI en vez
de esperar el 422).

**Errores**: `401` · `404` BD inexistente (por id) o BD cruda inexistente en el motor
(por `server_id`+nombre) · `422` un lado manda ambas representaciones o ninguna, source
y target son la misma BD física, motores incompatibles, o el diff excede el máximo de
sentencias/bytes configurado · `429` (10/min) · `502`/`504` motor inalcanzable.

#### `GET /api/v1/schema-comparisons/{id}` — resumen

Mismo shape que la creación, sin recomputar. **Errores**: `404` · `410` expiró (recalculá) · `401`.

#### `GET /api/v1/schema-comparisons/{id}/items` — detalle paginado

```bash
curl "https://<host>/api/v1/schema-comparisons/42/items?page=1&size=20" -b cookies.txt
```
```json
{
  "data": [
    { "id": 501, "comparison_id": 42, "seq": 3, "object_type": "column",
      "object_name": "productos.descripcion", "change_type": "new", "phase": 3,
      "sql": "ALTER TABLE `productos` ADD COLUMN `descripcion` TEXT NULL",
      "risk_flags": { "destructive": false, "requires_individual_review": false },
      "down_sql": "ALTER TABLE `productos` DROP COLUMN `descripcion`", "down_confirmed": true,
      "op_group": "column|productos.descripcion|new", "depends_on": [],
      "execution_status": null, "execution_error": null, "executed_at": null },
    { "id": 507, "comparison_id": 42, "seq": 9, "object_type": "column",
      "object_name": "clientes.rfc", "change_type": "dropped", "phase": 7,
      "sql": "ALTER TABLE `clientes` DROP COLUMN `rfc`",
      "risk_flags": { "destructive": true, "needs_review": true, "possible_rename_of": "clientes.rfc_nuevo" },
      "down_sql": null, "down_confirmed": false,
      "op_group": "column|clientes.rfc|dropped", "depends_on": [],
      "execution_status": null, "execution_error": null, "executed_at": null }
  ],
  "pagination": { "page": 1, "size": 20, "total": 18, "pages": 1 }
}
```

> **Lecturas para la UI**: el ítem `501` es aditivo seguro; el `507` es un `DROP`
> marcado `possible_rename_of` → **advertencia fuerte**, probablemente sea un rename y
> no una eliminación real. Un ítem con `risk_flags.requires_individual_review: true`
> (rutinas/triggers/eventos) **nunca** entra en modos automáticos y su cuerpo debe
> mostrarse completo antes de confirmar.

**Errores**: `404` · `410` expiró · `401`.

### 10.6 Cerrar una selección: `POST .../resolve-selection`

Endpoint nuevo, **solo lectura** (no adopta ni ejecuta nada):

```bash
curl -X POST "https://<host>/api/v1/schema-comparisons/42/resolve-selection" -b cookies.txt \
  -H "Content-Type: application/json" -d '{ "selected_item_ids": [507, 512] }'
```
```json
{
  "data": {
    "comparison_id": 42,
    "requested_item_ids": [507, 512],
    "resolved_item_ids": [501, 507, 512],
    "added_item_ids": [501],
    "added_reasons": { "view|v_reporte|new": ["table|clientes|new"] },
    "added": [ { "item_id": 501, "object_type": "table", "object_name": "clientes",
                 "change_type": "new", "sql": "CREATE TABLE `clientes` (...)" } ],
    "total": 3
  }
}
```

`resolved_item_ids` viene **en orden de ejecución** (no en el orden enviado). **Flujo
recomendado**: el usuario tilda ítems libremente (checkboxes por `op_group`) → al pasar
a confirmación, llamar a `resolve-selection` → si `added_item_ids` no está vacío,
mostrar un aviso ("se agregaron N sentencia(s) porque las que elegiste dependen de
ellas") con el detalle de `added` → usar `resolved_item_ids` como el `selected_item_ids`
real que se manda a `adopt`/`execute`. Reemplaza el ciclo "intento → 422 → agrego a mano
→ reintento".

⚠️ **No valida si la comparación expiró** (a diferencia de `adopt`/`execute`, que sí lo
hacen) — no lo uses como sustituto de refrescar la comparación si pasó mucho tiempo.

### 10.7 Opción A — Adoptar como versión del blueprint

**Cuándo**: el target está en el inventario **y** tiene `model_id` (es decir,
`SchemaComparisonSummaryOut.target_database_id` no es `null` — la UI debe ocultar esta
opción si vino `null`, sin necesidad de llamar al endpoint para saberlo).

```
POST /api/v1/schema-comparisons/{id}/adopt
Body: { "selected_item_ids": number[], "name": string, "description"?: string,
        "execute_immediately"?: boolean (default false),
        "auto_resolve_dependencies"?: boolean (default false) }
```

- **`auto_resolve_dependencies: false` (default, fail-closed):** si la selección no
  cierra, **422** con `public_context.missing_dependencies` y
  `public_context.suggested_item_ids` (el resultado que hubiera dado
  `resolve-selection`). Si tu pantalla ya llama a `resolve-selection` antes de
  confirmar, este 422 no debería aparecer en producción.
- **`true`:** el backend cierra la selección por su cuenta y la incluye; la respuesta
  trae `added_item_ids` (sin el detalle de `added_reasons` — para transparencia total,
  preferí el flujo de §10.6).

```json
{
  "data": {
    "comparison_id": 42, "model_id": 9, "version": "0007",
    "statements": 3, "executed": false,
    "migration": { "id": 88, "version": "0007", "is_baseline": true, "reviewed": false,
                   "has_non_portable": true, "up_sql": "...", "down_sql_suggested": "...", "...": "..." },
    "apply_result": null,
    "added_item_ids": [],
    "plan_warnings": []
  },
  "message": "Versión adoptada al blueprint (pendiente de revisión)."
}
```

`plan_warnings` (`[{code, message, op_group}]`) son avisos **no bloqueantes** del linter
de plan — no cambian lo que se ejecuta, pero vale mostrarlos:

| `code` | Significado |
|---|---|
| `create_and_drop_same_object` | El plan crea y borra el mismo objeto — casi siempre un rename no auto-detectado. |
| `destructive_without_rollback` | Hay cambios destructivos sin `down_sql`: la versión no podrá revertirse automáticamente. |

Con `execute_immediately: true`, la versión nace `reviewed: true` y `apply_result` trae
el resultado del `apply` normal (mismo shape que [§9](#9-bases-de-datos-gestionadas-managed-databases));
si es `false`, nace `reviewed: false` (gate R1, [§8](#8-blueprints-de-bd-database-models))
y hay que aprobarla + aplicarla luego.

**Errores**: `401` · `410` expiró · `422` el target no está en el inventario (usar
Opción B), el target está en el inventario pero sin blueprint (usar Opción B, mensaje
distinto, mismo código), o la selección no cierra sus dependencias · `409` anti-TOCTOU
(el esquema del target cambió desde que se calculó — recalculá) · `429` (3/min).

> ⚠️ **Limitación conocida**: adoptar por Opción A una **rutina/trigger de
> MySQL/MariaDB con cuerpo `BEGIN...END`** puede fallar al aplicarse si el `up_sql` se
> editó a mano rompiendo el cuerpo. Para objetos procedurales, la Opción B
> (`mode=custom`) no tiene este riesgo porque no re-parte el SQL ya renderizado.

### 10.8 Opción B — Ejecutar directamente sobre el target

**Cuándo**: el target **no** tiene `model_id`. Bloqueada (`409`) si sí lo tiene → usar
Opción A.

**Paso 1 — preview obligatorio**, `POST .../execute-preview` (sin rate limit, solo
lectura): resuelve el modo/selección y devuelve las sentencias exactas + el
`confirm_token` a reenviar. **El cliente NO puede calcular este token por su cuenta**
(requeriría replicar el filtro de riesgo sobre todos los ítems y el formato exacto de
serialización del servidor) — llamalo siempre antes de habilitar el botón de confirmar,
cada vez que cambie el modo o la selección.

```bash
curl -X POST "https://<host>/api/v1/schema-comparisons/42/execute-preview" -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{ "mode": "all_except_destructive", "selected_item_ids": null }'
```
```json
{
  "data": {
    "comparison_id": 42, "target_database_id": 12, "mode": "all_except_destructive",
    "statements": [ { "item_id": 501, "object_type": "column", "object_name": "productos.descripcion",
                       "sql": "ALTER TABLE `productos` ADD COLUMN `descripcion` TEXT NULL",
                       "risk_flags": { "destructive": false } } ],
    "excluded_by_dependency": [],
    "plan_warnings": [],
    "confirm_token": "3f9a1c...<sha256, reenviar tal cual en execute>"
  }
}
```

`excluded_by_dependency` (lista de `op_group`) — en los modos automáticos (`all`,
`all_except_destructive`) el filtro por riesgo puede dejar fuera una dependencia sin
sacar a sus dependientes (p. ej. una tabla marcada `possible_rename_of` queda excluida
por destructiva, pero sus índices no) — el backend **poda transitivamente** esos
huérfanos en vez de fallar, y los reporta acá. **Mostralo en el preview**, antes de
confirmar: cambia lo que realmente va a ejecutarse.

**Paso 2 — ejecutar**, `POST .../execute` (rate limit 3/min):

```
Body: { "mode": "all"|"all_except_destructive"|"custom",
        "selected_item_ids"?: number[] (requerido si mode=custom),
        "confirm_target_name": string, "confirm_token": string }
Query: ?force=true (override de cuarentena)
```

```bash
curl -X POST "https://<host>/api/v1/schema-comparisons/42/execute" -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{ "mode": "all_except_destructive", "selected_item_ids": null,
        "confirm_target_name": "productos_db", "confirm_token": "3f9a1c...<sha256>" }'
```
```json
{
  "data": {
    "comparison_id": 42, "target_database_id": 12, "mode": "all_except_destructive",
    "total": 8, "applied_count": 8, "failed": false,
    "statements": [ { "item_id": 501, "object_type": "column", "object_name": "productos.descripcion",
                       "status": "applied", "error": null, "execution_ms": 12 } ],
    "excluded_by_dependency": [], "plan_warnings": []
  },
  "message": "Ejecutadas 8 sentencia(s); 0 fallidas."
}
```

Con `mode=custom`: la selección se valida contra el cierre de dependencias igual que en
`adopt` (sin flag `auto_resolve_dependencies` en este endpoint — llamá a
`resolve-selection` primero si necesitás cerrarla).

**Errores**: `401` · `410` expiró · `409` el target tiene blueprint (usar Opción A),
cuarentena sin `force`, o anti-TOCTOU · `422` `confirm_target_name`/`confirm_token` no
coinciden, `mode=custom` sin `selected_item_ids`, o selección que no cierra · `429`
(3/min).

### 10.9 Export a `.sql`

```
GET /api/v1/schema-comparisons/{id}/export
```

**No usa el envelope `ApiResponse`** — es una descarga de archivo
(`Content-Disposition: attachment`, `application/sql`, nombre
`schema-diff-{id}-{target}.sql`).

| Query | Descripción |
|---|---|
| `item_ids` | Repetible (`?item_ids=1&item_ids=2`). Por defecto exporta **todas** las entidades. |
| `object_type`, `change_type` | Mismos filtros que `/items`, combinables con `item_ids`. |
| `include_rollback` | `true` anexa al final el `down_sql` sugerido (orden inverso), **comentado** (nunca ejecutable por accidente). |

Los objetos con cuerpo (rutinas/triggers/eventos) en MySQL/MariaDB se envuelven con
`DELIMITER $$ ... $$ DELIMITER ;`. Funciona igual para BDs adoptadas o crudas (solo lee
ítems ya calculados, no toca el motor ni valida fingerprint). El archivo incluye un
bloque de comentarios con los metadatos (source/target/motores) y marca `[EXPIRADA]` si
la comparación ya venció.

### 10.10 Matriz de errores (resumen de la sección)

| Situación | Código | CTA sugerido |
|---|---|---|
| source == target | `422` | "Elegí dos bases de datos distintas." |
| Motores incompatibles | `422` | Mostrar `detail.msg` tal cual |
| Comparación expiró (`GET`/`items`/`adopt`/`execute`) | `410` | Banner + botón **"Recalcular"** |
| `adopt` con target sin inventario o sin blueprint | `422` | `detail.msg` + CTA "Ejecutar directo (Opción B)" |
| `execute` con target con blueprint | `409` | `detail.msg` + CTA "Adoptar como versión (Opción A)" |
| `adopt`/`execute` — esquema del target cambió (anti-TOCTOU) | `409` | Banner + CTA **"Recalcular"** |
| `execute` — cuarentena sin `force` | `409` | Banner cuarentena + CTA "Reintentar con force" |
| `execute` — `confirm_target_name` no coincide | `422` | Error inline en el campo |
| `execute` — `confirm_token` no coincide | `422` | "El conjunto a ejecutar cambió; recalculá el preview." |
| Selección no cierra dependencias | `422` | Leer `public_context.missing_dependencies`/`suggested_item_ids` → CTA "Resolver automáticamente" |
| Rate limit | `429` | "Demasiadas solicitudes; esperá un momento." |

---

## 11. Catálogo de privilegios (`/privileges`)

Consulta y activa/desactiva los privilegios que la plataforma controla por motor.
Requiere sesión. **No toca ningún motor** (es un catálogo del gateway).

### Endpoints

#### `GET /api/v1/privileges`

| Query | Tipo | Descripción |
|---|---|---|
| `engine` | string \| null | `mysql` \| `mariadb` \| `postgresql` |
| `active` | bool \| null | `true` = solo los privilegios que la plataforma controla |

Respuesta `ApiResponse[list[PrivilegeOut]]` (**no paginada**). `PrivilegeOut`:
`{id, engine, name, category, context?, description, is_sensitive, is_active, created_at, updated_at}`.

```bash
curl "https://<host>/api/v1/privileges?engine=mysql&active=true" -b cookies.txt
```

```json
{ "data": [
  { "id": 1, "engine": "mysql", "name": "SELECT", "category": "object",
    "description": "Leer filas", "is_sensitive": false, "is_active": true,
    "created_at": "…", "updated_at": "…" }
] }
```

#### `PATCH /api/v1/privileges/{privilege_id}`

**Body** (`PrivilegeUpdate`): `{ "is_active": bool }`.

```bash
curl -X PATCH https://<host>/api/v1/privileges/1 -b cookies.txt \
  -H "Content-Type: application/json" -d '{ "is_active": false }'
```

```json
{ "data": { "id": 1, "is_active": false, "...": "..." }, "message": "Privilegio desactivado." }
```

---

## 12. Perfiles de permisos (`/permission-profiles`)

Plantillas de privilegios **por motor**, reutilizables para aplicar a usuarios con
[`apply-profile`](#7-usuarios-del-motor-server-users-y-serversidusers). CRUD puro de
inventario; **no toca ningún motor**. Requiere sesión.

### Schemas

`PermissionProfileCreate`:

| Campo | Tipo | Requerido | Validación |
|---|---|---|---|
| `name` | string | sí | 1–100 caracteres |
| `engine` | `EngineType` | sí | `mysql` \| `mariadb` \| `postgresql` |
| `description` | string \| null | no | máx 255 |
| `items` | list | sí | mínimo 1; cada item: `{ level: GrantLevel, privileges: list[str] }` |

`PermissionProfileUpdate`: `name?`, `description?`, `is_active?`, `items?`. El `engine` es
**inmutable**; si envías `items`, **reemplazan** por completo los anteriores.

`PermissionProfileOut`: `{ id, name, engine, description?, is_active, items[], created_at, updated_at }`
donde cada item de salida es `{ level, privileges[], requires_confirmation }`.

### Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/v1/permission-profiles` | Lista (filtros `?engine=`, `?active=`). **No paginada.** |
| `POST` | `/api/v1/permission-profiles` | Crea (`201`). |
| `GET` | `/api/v1/permission-profiles/{profile_id}` | Detalle. |
| `PATCH` | `/api/v1/permission-profiles/{profile_id}` | Actualiza (items reemplazan). |
| `DELETE` | `/api/v1/permission-profiles/{profile_id}` | Elimina. |

```bash
curl -b cookies.txt -X POST https://<host>/api/v1/permission-profiles \
  -H "Content-Type: application/json" \
  -d '{ "name": "app-readwrite", "engine": "mysql",
        "items": [ { "level": "database", "privileges": ["SELECT","INSERT","UPDATE","DELETE"] } ] }'
```

```json
{ "data": { "id": 3, "name": "app-readwrite", "engine": "mysql", "is_active": true,
            "items": [ { "level": "database",
              "privileges": ["SELECT","INSERT","UPDATE","DELETE"], "requires_confirmation": false } ] },
  "message": "Perfil de permisos creado." }
```

---

## 13. Administración: cifrado (`/admin/crypto`)

Operaciones de administración del cifrado de credenciales. Requiere `crypto.rotate` (solo la global
`security_officer`; antes `policy.admin`, ver `api-reference-v41.md`) y step-up. No toca los motores destino (opera sobre la BD de metadatos).

### `POST /api/v1/admin/crypto/rotate`

Rota la **clave de datos (DEK)** y **re-cifra todas las credenciales** almacenadas
(servidores y usuarios), **sin cambiar `SECRET_KEY` ni reiniciar** la aplicación.

**Body:** ninguno. **Respuesta** `200` — `ApiResponse[CryptoRotationOut]`
(`{servers_reencrypted, server_users_reencrypted}`).

```bash
curl -b cookies.txt -X POST https://<host>/api/v1/admin/crypto/rotate
```

```json
{ "data": { "servers_reencrypted": 12, "server_users_reencrypted": 30 },
  "message": "Clave de cifrado rotada; credenciales re-cifradas." }
```

---

## 14. Health checks

No versionados, **sin autenticación**, sin envelope `ApiResponse`. Pensados para
*probes* de Docker/Kubernetes.

### `GET /health` — liveness

Confirma que el proceso responde. No comprueba dependencias. Siempre `200`.

```json
{ "status": "ok", "service": "<APP_NAME>", "environment": "production" }
```

### `GET /health/ready` — readiness

Comprueba que la BD de metadatos es alcanzable (`SELECT 1`). Devuelve `200` si está lista
o `503` si no.

```json
{ "status": "ready", "service": "<APP_NAME>", "environment": "production" }
```

```json
// 503 Service Unavailable
{ "status": "unavailable", "service": "<APP_NAME>", "environment": "production",
  "detail": "metadata database unreachable" }
```

---

## 15. Estado del proyecto

| Iteración | Estado | Alcance |
|---|---|---|
| **1** | ✅ Completada | Infra FastAPI, modelo `Server` + CRUD, cifrado Fernet, conexión remota, introspección, anti-SSRF, anti-inyección, rate limiting, auditoría base. |
| **2** | ✅ Completada | `ServerUser`, `DatabaseModel`, `ManagedDatabase`, `Privilege`; aprovisionamiento `CREATE/DROP` USER y DATABASE, `GRANT/REVOKE`, reasignación de owner, doble confirmación, catálogo de privilegios. |
| **2+** | ✅ Completada | Gestión granular de permisos (grants por nivel, introspección de permisos efectivos, perfiles de permisos reutilizables, creación unificada usuario+grants) y rotación de cifrado (DEK). |
| **3 (Plan 02)** | ✅ Completada | **Migraciones de blueprints**: deltas SQL versionados por blueprint, aplicación/rollback/stamp/historial por BD, dry-run, apply-all, auto-traducción cross-engine, cuarentena y checkpoint/resume por sentencia. Verificado e2e en MySQL 8 / MariaDB 11 / PostgreSQL 16. |
| **Plan 09** | ✅ Completada | **Adopción, reconciliación y snapshot**: `reconcile` (plano en vivo vs. inventario), adoptar BDs/usuarios preexistentes (con stamp-on-adopt), snapshot estructural, blueprint baseline desde snapshot (gate R1 de revisión). |
| **Gestión de usuarios del motor** | ✅ Completada | Vista agrupada por username (`adopted`/`unmanaged`/`orphan`, `supports_hosts`), CRUD por identidad física (adoptados y no), agregar host (MySQL/MariaDB), revelar/definir contraseña, endpoints batch por username completo. |
| **Comparación de esquemas** | ✅ Completada | Diff estructural entre dos BDs (mismo motor o MySQL↔MariaDB), adopción como versión de blueprint u ejecución directa, orden topológico de ejecución, cierre de dependencias de una selección parcial, linter de invariantes del plan. |
| **Reconciliación de aplicaciones parciales** | ✅ Completada | DDL transaccional en PostgreSQL (elimina el estado parcial de raíz), auto-reconciliación configurable al fallar un `apply` en MySQL/MariaDB, endpoint dedicado de reconciliación manual. |
| **Clonado de bases de datos** | ✅ Completada | Clonado de estructura y datos entre servidores/motores, y lotes de N bases (`api-reference-v19.md`). Ver `docs/features/database-clone.md`. |
| **Exportación** | ✅ Completada | Estructura y/o datos, multiformato, descarga en dos pasos (`api-reference-v10.md`). e2e contra motores reales pendiente (`TODO.md` P-01). |
| **Consola SQL** | ✅ Completada | SQL ad-hoc con el usuario del motor elegido, historial con literales enmascarados para quien no puede ejecutar (`api-reference-v6.md`). |
| **Collation** | ✅ Completada | Catálogo de charsets, conversión de una BD y en lote (`v7`, `v8`, `v17`). |
| **Proyectos y entornos** | ✅ Completada | Agrupación de blueprints (`v16`) y entornos con política (`docs/features/environments.md`). |
| **Servidor MCP** | 🟡 Parcial (v1) | Tokens de agente con techo de solo lectura; faltan las tools que leen el catálogo del motor (`api-reference-v23.md` §9). |
| **Autorización multiusuario** | ✅ Completada | Roles y 32 capacidades, alcance por destino, capacidades puntuales, separación de deberes, segundo aprobador, ventana de arranque, step-up, lectura de auditoría y revocación de sesiones. Ver [§20](#20-autorización-dónde-está-cada-contrato) y `docs/features/authorization.md`. |
| **Siguiente** | ⏳ Pendiente | Aprovisionamiento de servidores (Terraform/SSH), 2FA y SSO, cola durable de jobs, verificación e2e contra motores reales, *production readiness*. Ver `docs/plans/` y `TODO.md`. |

> Los endpoints `/api/v1/test/*` que pueda exponer la app son **ejemplos de demostración
> del template** y no forman parte de la API funcional del gateway; no están documentados
> aquí.

---

## 16. Flujos de integración (orden de llamadas)

Cada flujo lista la secuencia de endpoints y sus dependencias. Recuerda enviar la cookie
de sesión en cada llamada (paso A).

### A. Autenticarse (siempre primero)

```
POST /api/v1/auth/login        → guarda la cookie (Set-Cookie)
GET  /api/v1/auth/me           → (opcional) valida la sesión
…                              → usa la cookie en todas las llamadas
POST /api/v1/auth/step-up      → ante un 403 access.step_up_required; reintentar una vez
POST /api/v1/auth/logout       → al terminar
```

### B. Registrar y validar un servidor

```
1. POST /api/v1/servers                         → crea el servidor (cifra la pseudo-root)
2. POST /api/v1/servers/{id}/test-connection 🔌 → verifica conectividad; fija status
```

Depende de: sesión activa. Salida: un `server_id` utilizable por los demás recursos.

### C. Inspeccionar la estructura de un servidor (solo lectura)

```
1. GET /api/v1/servers/{id}/databases 🔌                              → elige una BD
2. GET /api/v1/servers/{id}/databases/{db}/tables 🔌                  → elige una tabla
3. GET /api/v1/servers/{id}/databases/{db}/tables/{t}/schema 🔌       → columnas/PK/FK/índices
   (GET /api/v1/servers/{id}/users/grouped 🔌 lista los usuarios del motor, agrupados)
```

Depende de: un servidor alcanzable (paso B exitoso).

### D. Aprovisionar un usuario y una base de datos (flujo principal)

```
1. POST /api/v1/server-users?provision=true 🔌
      body: { server_id, username, host?, password }      → crea el usuario (owner)
2. POST /api/v1/managed-databases?provision=true 🔌
      body: { name, server_id, owner_id, charset?, collation?, model_id? }
                                                           → CREATE DATABASE (SIN GRANT)
```

Dependencias y reglas:
- El `owner_id` del paso 2 es el `id` devuelto en el paso 1, y **debe** pertenecer al
  mismo `server_id` (si no, `409`).
- Con `provision=true`, `password` es obligatorio en el paso 1 (`422` si falta).
- Si el `CREATE` del paso 2 falla, la BD queda en `status: "error"` (revisa `notes`).
- **No hay GRANT automático**: el owner no recibe ningún privilegio sobre la BD (en
  PostgreSQL queda como `OWNER` nativo, que es propiedad y no un grant). Los privilegios se
  otorgan explícitamente con `POST /server-users/{id}/grants`.
- Para registrar sin tocar el motor todavía, usa `provision=false`: la BD queda en
  `pending`. Para aprovisionarla más adelante **sobre la misma fila** (sin perder notas,
  entorno, blueprint ni historial) está `POST /managed-databases/{id}/provision`.

### E. Reasignar el propietario de una BD

```
POST /api/v1/managed-databases/{db_id}/reassign-owner?provision=true 🔌
     body: { owner_id: <nuevo_owner> }   → revoca al anterior y otorga al nuevo (o ALTER OWNER en PG)
```

El nuevo owner debe ser un `ServerUser` del mismo servidor (`409` si no).

### F. Borrado seguro

```
# Para borrar un usuario que posee BDs, primero libéralas:
1. (reasignar con E)  o  (borrar las BDs con paso 2)
2. DELETE /api/v1/managed-databases/{db_id}?drop_remote=true&confirm_name=<name> 🔌
3. DELETE /api/v1/server-users/{user_id}?drop_remote=true&confirm_username=<username> 🔌
```

Reglas:
- Un `ServerUser` con BDs no se puede borrar (`RESTRICT` ⇒ `409`): reasigna o borra sus
  BDs primero.
- `drop_remote=true` exige repetir el nombre exacto (`confirm_name` / `confirm_username`),
  de lo contrario `422`.
- Sin `drop_remote`, solo se borra del inventario y el objeto sigue existiendo en el motor.

### G. Blueprints y catálogo de privilegios (auxiliares, solo inventario)

```
POST /api/v1/database-models                 → define un blueprint reutilizable
GET  /api/v1/database-models/{id}/databases  → BDs que lo replican
GET  /api/v1/privileges?engine=…&active=true → privilegios que la plataforma controla
PATCH /api/v1/privileges/{id}                → activa/desactiva un privilegio
```

Puedes referenciar `model_id` al crear una BD (paso D) para asociarla a un blueprint.

### H. Gestión de permisos de un usuario

```
# Rápido: usuario + grants en una sola llamada
POST /api/v1/server-users/provision 🔌
     body: { server_id, username, password, initial_grants:[{level, object_ref, privileges}] }

# Paso a paso:
1. (opcional) POST /api/v1/servers/{id}/grantable 🔌      → ¿puedo delegar estos privilegios?
2. POST   /api/v1/server-users/{user_id}/grants 🔌        → otorga
3. GET    /api/v1/server-users/{user_id}/grants 🔌        → verifica permisos efectivos (PG: ?database=)
4. DELETE /api/v1/server-users/{user_id}/grants 🔌        → revoca

# Con perfiles reutilizables:
A. POST /api/v1/permission-profiles                       → plantilla por motor
B. POST /api/v1/server-users/{user_id}/apply-profile/{profile_id} 🔌
        body: { object_mappings:[{level, object_ref}] }   → aplica la plantilla
```

Reglas: los grants operan sobre un `ServerUser` ya registrado (créalo primero o usa
`/provision`); en PostgreSQL pasa `?database=` para grants de objeto; el motor del perfil
debe coincidir con el del servidor (`422`); `grantable`/`grants POST` devuelven `403` si la
credencial pseudo-root no puede delegar (`WITH GRANT OPTION`).

### I. Versionar un blueprint y aplicarlo a sus BDs (migraciones)

Permite definir el esquema de un blueprint como una secuencia de deltas SQL y aplicarlo a
las BDs que lo replican. Depende de: un blueprint (paso G) y BDs creadas con ese `model_id`
(paso D con `model_id`).

```
# 1) Definir las migraciones del blueprint (inventario; no toca motores)
POST  /api/v1/database-models/{model_id}/migrations
      body: { version:"0001", name, up_sql }          → devuelve translated + down_sql_suggested
PATCH /api/v1/database-models/{model_id}/migrations/0001
      body: { down_sql: "<rollback confirmado>" }      → (opcional) habilita el rollback

# 2) Previsualizar y aplicar sobre UNA BD que use el blueprint
GET   /api/v1/managed-databases/{db_id}/migrations/status            → current vs pendientes
POST  /api/v1/managed-databases/{db_id}/migrations/apply?dry_run=true → plan sin ejecutar
POST  /api/v1/managed-databases/{db_id}/migrations/apply 🔌          → aplica las pendientes
GET   /api/v1/managed-databases/{db_id}/migrations/history           → auditoría del resultado

# 3) (opcional) Revertir, reconciliar un fallo parcial, o aplicar a TODAS las BDs
POST  /api/v1/managed-databases/{db_id}/migrations/rollback?confirm_version=0002 🔌
POST  /api/v1/managed-databases/{db_id}/migrations/reconcile-partial?confirm_version=0008&dry_run=true 🔌
POST  /api/v1/database-models/{model_id}/migrations/apply-all 🔌     → fan-out a N BDs
```

Dependencias y reglas:
- La BD debe tener `model_id` asignado (al crearla en el paso D, o vía `PATCH`); si no, los
  endpoints de migración responden `422`.
- El orden de versiones es **numérico**: `apply` recorre las pendientes en orden ascendente
  y se detiene en la primera que falle.
- `rollback` es destructivo: exige `?confirm_version=` = versión actual (`422` si no) y que
  esa versión tenga `down_sql` confirmado (`409` si no) — y que la BD **no** tenga una
  aplicación parcial pendiente (`409`, ver [§9](#9-bases-de-datos-gestionadas-managed-databases)).
- **Recuperación de fallo (MySQL/MariaDB):** con el default `on_failure=auto`, un `apply`
  que falla a mitad se auto-resuelve en la misma llamada (campo `reconciliation`). Si
  usaste `on_failure=leave` o quedó algo sin reconciliar del todo, usá
  `reconcile-partial` (nunca `stamp --force` + `rollback` — ver la advertencia en §9).
  En **PostgreSQL** esto casi no ocurre: el motor deshace la migración fallida solo.
- `stamp` marca una BD pre-existente en una versión **sin ejecutar SQL** (cuando el esquema
  ya existe pero el gateway aún no lo registra) — o para descartar (`force=true`) un
  checkpoint parcial ya reconciliado a mano.

Escenario real (frontend): un wizard de "publicar nueva versión de esquema" llamaría
`POST …/migrations` (subir el delta) → mostrar `translated`/`down_sql_suggested` para
revisión → `PATCH` (confirmar rollback) → por cada BD afectada, `…/migrations/apply?dry_run=true`
(preview) → `…/migrations/apply` (confirmar) → `…/migrations/history` (resultado). Para
desplegar a toda una familia de BDs, `…/migrations/apply-all`.

### J. Comparar dos BDs y sincronizarlas (schema-comparisons)

Depende de: dos BDs alcanzables (mismo motor, o MySQL↔MariaDB) — adoptadas o crudas.

```
1. POST /api/v1/schema-comparisons 🔌
        body: { source_database_id | source_server_id+source_database_name,
                target_database_id | target_server_id+target_database_name }
2. GET  /api/v1/schema-comparisons/{id}                → resumen (¿hay destructivos?)
3. GET  /api/v1/schema-comparisons/{id}/items           → DDL exacto por ítem (ordenar por seq)
4. POST /api/v1/schema-comparisons/{id}/resolve-selection
        body: { selected_item_ids }                     → cierra la selección del usuario

# Según si el target tiene blueprint:
5a. POST /api/v1/schema-comparisons/{id}/adopt 🔌       → Opción A (crea versión en el blueprint)
5b. POST /api/v1/schema-comparisons/{id}/execute-preview → Opción B, paso 1 (obtiene confirm_token)
    POST /api/v1/schema-comparisons/{id}/execute 🔌     → Opción B, paso 2 (ejecuta)
```

Reglas: la dirección `source`/`target` siempre es explícita, nunca inferida. Ordená y
ejecutá siempre por `seq`, nunca por `phase`. Agrupá la selección por `op_group`. Una
comparación tiene TTL — `410` si expiró, hay que recalcularla (volver al paso 1).

### K. Reconciliar una aplicación parcial (recuperación fina, MySQL/MariaDB)

Depende de: una BD gestionada con blueprint cuyo `GET .../migrations/status` informa
`has_partial_application: true`.

```
1. GET  /api/v1/managed-databases/{db_id}/migrations/status
        → lee partial_application[] (la de versión más alta primero)
2. POST /api/v1/managed-databases/{db_id}/migrations/reconcile-partial
        ?confirm_version=<version>&dry_run=true            → preview de los reversos exactos
3. POST /api/v1/managed-databases/{db_id}/migrations/reconcile-partial 🔌
        ?confirm_version=<version>                          → ejecuta (agregar &force=true si
                                                                hay sentencias sin reverso)
4. GET  /api/v1/managed-databases/{db_id}/migrations/status → confirmar has_partial_application=false
```

Si hay **más de una** entrada en `partial_application[]`, repetir desde el paso 2 con la
siguiente versión (de mayor a menor) — cada llamada solo resuelve la de versión más
alta pendiente.

---

## 17. Apéndice: tabla resumen de endpoints

> 🔌 = toca el servidor de BD destino · 🔒 = requiere sesión

| # | Método | Ruta | Auth | Motor |
|---|---|---|---|---|
| 1 | GET | `/health` | — | — |
| 2 | GET | `/health/ready` | — | — |
| 3 | POST | `/api/v1/auth/login` | — | — |
| 4 | POST | `/api/v1/auth/logout` | 🔒 | — |
| 5 | GET | `/api/v1/auth/me` | 🔒 | — |
| 6 | GET | `/api/v1/servers` | 🔒 | — |
| 7 | POST | `/api/v1/servers` | 🔒 | — |
| 8 | GET | `/api/v1/servers/{server_id}` | 🔒 | — |
| 9 | PATCH | `/api/v1/servers/{server_id}` | 🔒 | — |
| 10 | DELETE | `/api/v1/servers/{server_id}` | 🔒 | — |
| 11 | POST | `/api/v1/servers/{server_id}/test-connection` | 🔒 | 🔌 |
| 12 | GET | `/api/v1/servers/{server_id}/databases` | 🔒 | 🔌 |
| 13 | GET | `/api/v1/servers/{server_id}/users` | 🔒 | 🔌 |
| 14 | GET | `/api/v1/servers/{server_id}/databases/{database}/tables` | 🔒 | 🔌 |
| 15 | GET | `/api/v1/servers/{server_id}/databases/{database}/tables/{table}/schema` | 🔒 | 🔌 |
| 16 | POST | `/api/v1/servers/{server_id}/grantable` | 🔒 | 🔌 |
| 17 | GET | `/api/v1/server-users` | 🔒 | — |
| 18 | POST | `/api/v1/server-users` (`?provision`) | 🔒 | 🔌* |
| 19 | GET | `/api/v1/server-users/{user_id}` | 🔒 | — |
| 20 | PATCH | `/api/v1/server-users/{user_id}` (`?provision`) | 🔒 | 🔌* |
| 21 | DELETE | `/api/v1/server-users/{user_id}` (`?drop_remote`) | 🔒 | 🔌* |
| 22 | GET | `/api/v1/server-users/{user_id}/databases` | 🔒 | — |
| 23 | GET | `/api/v1/server-users/{user_id}/grants` | 🔒 | 🔌 |
| 24 | POST | `/api/v1/server-users/{user_id}/grants` | 🔒 | 🔌 |
| 25 | DELETE | `/api/v1/server-users/{user_id}/grants` | 🔒 | 🔌 |
| 26 | POST | `/api/v1/server-users/{user_id}/apply-profile/{profile_id}` | 🔒 | 🔌 |
| 27 | POST | `/api/v1/server-users/provision` | 🔒 | 🔌 |
| 28 | GET | `/api/v1/database-models` | 🔒 | — |
| 29 | POST | `/api/v1/database-models` | 🔒 | — |
| 30 | GET | `/api/v1/database-models/{model_id}` | 🔒 | — |
| 31 | PATCH | `/api/v1/database-models/{model_id}` | 🔒 | — |
| 32 | DELETE | `/api/v1/database-models/{model_id}` | 🔒 | — |
| 33 | GET | `/api/v1/database-models/{model_id}/databases` | 🔒 | — |
| 34 | GET | `/api/v1/managed-databases` | 🔒 | — |
| 35 | POST | `/api/v1/managed-databases` (`?provision`) | 🔒 | 🔌* |
| 36 | GET | `/api/v1/managed-databases/{db_id}` | 🔒 | — |
| 37 | PATCH | `/api/v1/managed-databases/{db_id}` | 🔒 | — |
| 38 | DELETE | `/api/v1/managed-databases/{db_id}` (`?drop_remote`) | 🔒 | 🔌* |
| 39 | POST | `/api/v1/managed-databases/{db_id}/reassign-owner` (`?provision`) | 🔒 | 🔌* |
| 40 | GET | `/api/v1/privileges` | 🔒 | — |
| 41 | PATCH | `/api/v1/privileges/{privilege_id}` | 🔒 | — |
| 42 | GET | `/api/v1/permission-profiles` | 🔒 | — |
| 43 | POST | `/api/v1/permission-profiles` | 🔒 | — |
| 44 | GET | `/api/v1/permission-profiles/{profile_id}` | 🔒 | — |
| 45 | PATCH | `/api/v1/permission-profiles/{profile_id}` | 🔒 | — |
| 46 | DELETE | `/api/v1/permission-profiles/{profile_id}` | 🔒 | — |
| 47 | POST | `/api/v1/admin/crypto/rotate` | 🔒 | — |
| 48 | GET | `/api/v1/database-models/{model_id}/migrations` | 🔒 | — |
| 49 | POST | `/api/v1/database-models/{model_id}/migrations` | 🔒 | — |
| 50 | GET | `/api/v1/database-models/{model_id}/migrations/{version}` | 🔒 | — |
| 51 | PATCH | `/api/v1/database-models/{model_id}/migrations/{version}` | 🔒 | — |
| 52 | DELETE | `/api/v1/database-models/{model_id}/migrations/{version}` | 🔒 | — |
| 53 | POST | `/api/v1/database-models/{model_id}/migrations/apply-all` | 🔒 | 🔌 |
| 54 | GET | `/api/v1/managed-databases/{db_id}/migrations/status` | 🔒 | 🔌 |
| 55 | POST | `/api/v1/managed-databases/{db_id}/migrations/apply` | 🔒 | 🔌 |
| 56 | POST | `/api/v1/managed-databases/{db_id}/migrations/rollback` | 🔒 | 🔌 |
| 57 | POST | `/api/v1/managed-databases/{db_id}/migrations/stamp` | 🔒 | 🔌 |
| 58 | GET | `/api/v1/managed-databases/{db_id}/migrations/history` | 🔒 | 🔌 |
| 59 | GET | `/api/v1/servers/{server_id}/reconcile` | 🔒 | 🔌 |
| 60 | GET | `/api/v1/servers/{server_id}/databases/{database}/snapshot` | 🔒 | 🔌 |
| 61 | POST | `/api/v1/server-users/adopt` | 🔒 | 🔌 |
| 62 | POST | `/api/v1/managed-databases/adopt` | 🔒 | 🔌 |
| 63 | POST | `/api/v1/database-models/from-snapshot` | 🔒 | 🔌 |
| 64 | GET | `/api/v1/servers/{server_id}/users/grouped` | 🔒 | 🔌 |
| 65 | POST | `/api/v1/servers/{server_id}/users` | 🔒 | 🔌 |
| 66 | PATCH | `/api/v1/servers/{server_id}/users/password` | 🔒 | 🔌 |
| 67 | DELETE | `/api/v1/servers/{server_id}/users` | 🔒 | 🔌 |
| 68 | POST | `/api/v1/servers/{server_id}/users/add-host` | 🔒 | 🔌 |
| 69 | POST | `/api/v1/servers/{server_id}/users/reveal-password` | 🔒 | 🔌 |
| 70 | POST | `/api/v1/servers/{server_id}/users/adopt-all-hosts` | 🔒 | 🔌 |
| 71 | POST | `/api/v1/servers/{server_id}/users/define-password` | 🔒 | — (nunca toca el motor) |
| 72 | PATCH | `/api/v1/servers/{server_id}/users/password-all-hosts` | 🔒 | 🔌 |
| 73 | POST | `/api/v1/schema-comparisons` | 🔒 | 🔌 (solo lectura de ambos motores) |
| 74 | GET | `/api/v1/schema-comparisons/{id}` | 🔒 | — |
| 75 | GET | `/api/v1/schema-comparisons/{id}/items` | 🔒 | — |
| 76 | GET | `/api/v1/schema-comparisons/{id}/export` | 🔒 | — |
| 77 | POST | `/api/v1/schema-comparisons/{id}/resolve-selection` | 🔒 | — |
| 78 | POST | `/api/v1/schema-comparisons/{id}/adopt` | 🔒 | 🔌* |
| 79 | POST | `/api/v1/schema-comparisons/{id}/execute-preview` | 🔒 | — |
| 80 | POST | `/api/v1/schema-comparisons/{id}/execute` | 🔒 | 🔌 |
| 81 | POST | `/api/v1/managed-databases/{db_id}/migrations/reconcile-partial` | 🔒 | 🔌 |
| 82 | GET | `/api/v1/gateway-users/{user_id}/capability-grants` | 🔒 `access_admin` | — |
| 83 | POST | `/api/v1/gateway-users/{user_id}/capability-grants` | 🔒 `access_admin` | — |
| 84 | DELETE | `/api/v1/gateway-users/{user_id}/capability-grants/{grant_id}` | 🔒 `access_admin` | — |
| 85 | GET | `/api/v1/capability-grants/pending` | 🔒 `access_admin` | — |
| 86 | POST | `/api/v1/capability-grants/{grant_id}/approve` | 🔒 `access_admin` | — |
| 87 | POST | `/api/v1/capability-grants/{grant_id}/reject` | 🔒 `access_admin` | — |
| 88 | GET | `/api/v1/gateway-users/{user_id}/effective-access` | 🔒 `access_admin` | — |

\* Toca el motor solo cuando el flag (`provision` / `drop_remote` / `execute_immediately`)
es `true`. Los grants y `provision`/`apply-profile` tocan el motor siempre. Los
endpoints **48–58** son el módulo de migraciones de blueprints (Plan 02); los `48–52`
son CRUD de inventario, el resto tocan el motor. Los endpoints **59–63** son el módulo
de adopción/reconciliación/snapshot (Plan 09); todos leen el motor (solo lectura), salvo
que crean metadata en el gateway. Los endpoints **64–72** son la gestión agrupada de
usuarios del motor por identidad física ([§7](#7-usuarios-del-motor-server-users-y-serversidusers)) —
`71` es el único que nunca toca el motor (cifra y guarda, nada más). Los endpoints
**73–80** son el módulo de comparación de esquemas ([§10](#10-comparación-de-esquemas-entre-bds-schema-comparisons)):
`73` snapshotea ambos motores (solo lectura); `74/75/76/77/79` son puro inventario; `78`
toca el motor solo si `execute_immediately=true`; `80` siempre toca el motor. El `81` es
la reconciliación manual de una aplicación parcial ([§9](#9-bases-de-datos-gestionadas-managed-databases)).

---

## 18. Apéndice: variables de entorno del integrador

Relevantes para quien despliega o consume el gateway (la lista completa está en
`.env.example` y `app/core/environments.py`):

| Variable | Propósito |
|---|---|
| `CORS_ORIGINS` | Orígenes permitidos para el frontend que consume la API (coma-separados). |
| `DOCS_ENABLED` | Habilita `/api/v1/docs` y `/api/v1/redoc`. |
| `RATE_LIMIT_DEFAULT` | Límite global por IP (p. ej. `100/minute`). El login tiene límites propios (ver §5). |
| `LOGIN_USERNAME_RATE_LIMIT` | Tope de intentos de login por cuenta desde cualquier IP (default `20/hour`; vacío lo desactiva). |
| `MCP_AUTH_FAILURE_RATE_LIMIT` | Tope de credenciales rechazadas por IP en `/mcp` (default `30/minute`). |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Credenciales del administrador de accesos sembrado en una instalación vacía (`viewer` + `access_admin`; ver `api-reference-v29.md` §10). |
| `ACCESS_BOOTSTRAP_WINDOW_HOURS` | Horas de la ventana de arranque en que el único `access_admin` eleva sin segundo aprobador (default `72`). |
| `ADMIN_RECOVERY` | `1` solo para recuperar el administrador de accesos al arrancar (F-24); quitarlo después. |
| `SECRET_KEY` | Deriva la clave Fernet y firma la sesión. Obligatorio en producción. |
| `REMOTE_CONNECT_TIMEOUT` | Segundos para abrir la conexión a un servidor destino. |
| `REMOTE_STATEMENT_TIMEOUT_MS` | Milisegundos máximos por sentencia en el destino. |
| `REMOTE_SSL_MODE` | `ssl_mode` por defecto si un servidor no define el suyo. |

---

## 19. Capacidades puntuales (`/gateway-users/{id}/capability-grants`)

Una **capacidad puntual** le suma a una persona del gateway UNA capacidad sobre UN entorno o
servidor, sin cambiarle el rol. Suma al rol del alcance (nunca resta) y no toca `PUT /access`.
Incluye el alta, el listado, la revocación, la aprobación/rechazo de las sensibles, la vista de
la propia persona (`/auth/me`) y el acceso efectivo con procedencia.

**Quién actúa.** Solo quien tiene la global `access_admin`: las rutas declaran `access.admin`,
que `security_officer` no tiene (`403`; ver `api-reference-v29.md`). Todo es CSRF + sesión, como el resto de `/gateway-users`.
Nadie se otorga ni se revoca capacidades a sí mismo. Las capacidades **globales** (`servers.admin`,
`catalogs.*`, `environments.*`, `access.admin`, `audit.read`, `crypto.rotate`, `self.read`) no se otorgan nunca.

**Asignación (C3).** Lo que cuenta es la función: `access_admin` asigna cualquier capacidad
otorgable, tenga o no la capacidad él mismo. Reemplaza al techo por tenencia
(`access.grant_ceiling_exceeded`, retirado); ver `api-reference-v29.md` §9. Otorgar
escribir/ejecutar trae implícita la lectura de su módulo (campo `implies`).

**Sensibles (segundo aprobador).** Las 11 exclusivas de `owner` (owner − operator, otorgables):
`engine_users.secrets`, `engine_users.credentials`, `blueprints.captures`, `blueprints.apply`,
`clones.execute`, `collation.execute`, `exports.download`, `schema_diff.execute`,
`sql_console.execute`, `engine_users.drop` y `databases.drop` nacen `pending` (vencen a los 7
días, sin ningún efecto hasta que otro `access_admin` las apruebe). El resto nace `active`. Una
alta con `sod_override` también nace `pending` (el override se aplica al aprobarla y viaja en el
campo `sod_override` de `CapabilityGrantOut`). Con `ACCESS_FOUR_EYES=False` todas nacen `active`
y se audita `access.elevation_unapproved`.

### Objeto `CapabilityGrantOut`

```json
{
  "id": 7, "user_id": 2, "username": "destino",
  "capability": "databases.write", "scope_type": "environment", "scope_id": 1,
  "scope_name": "Desarrollo", "status": "active", "sensitive": false,
  "requested_by": { "id": 1, "username": "admin" }, "requested_at": "2026-10-01T18:00:00",
  "decided_by": null, "decided_at": null, "expires_at": null,
  "request_reason": "release del viernes", "decision_reason": null,
  "implies": ["databases.read"]
}
```

`status`: `pending | active | rejected | expired | cancelled | revoked`. `scope_type`:
`environment | server`. `requested_by` / `decided_by` son `null` si el usuario se borró.

### `GET /api/v1/gateway-users/{user_id}/capability-grants`

Todas las capacidades de la persona, historial incluido, de la más nueva a la más vieja. Filtro
opcional `?status=<estado>` (valor inválido: `422`). **Respuesta** `200` — `ApiResponse[CapabilityGrantOut[]]`
(sin paginar).

### `POST /api/v1/gateway-users/{user_id}/capability-grants`

Body: `{ "capability": "databases.write", "scope_type": "environment", "scope_id": 1, "reason": "…" }`
(`reason` opcional, máx. 500; `scope_id` ≥ 1; `scope_type` distinto de `environment|server`: `422`).
**Respuesta** `201` — `ApiResponse[CapabilityGrantOut]` (`status` `active` o `pending`).

### `DELETE /api/v1/gateway-users/{user_id}/capability-grants/{grant_id}`

Sin body. Un solo `access_admin` alcanza, sin techo y con efecto inmediato. `active` pasa a
`revoked`, `pending` pasa a `cancelled`; la fila queda como historial. **Respuesta** `200` —
`ApiResponse[CapabilityGrantOut]`.

### Aprobación de sensibles (`/capability-grants`)

Aprueba **otro** `access_admin`: ni quien la pidió ni la persona destino. A quien aprueba le basta
su función (no hace falta que tenga la capacidad); del solicitante solo se
exige que siga siendo `access_admin` activo (si no, la solicitud se cancela y el approve da `409
access.grant_not_pending`). La decisión es atómica (compare-and-set sobre `pending` y no vencida):
con dos aprobadores simultáneos gana uno y el otro recibe `access.grant_not_pending`. Al aprobar
se borra `expires_at` (la capacidad activa no vence). Las inactivas de una persona desactivada
se conservan.

**Vencimiento.** Una pendiente vence a los 7 días (`pending` → `expired`, `live_key` liberado). El
barrido es perezoso: corre en el listado por usuario, en la bandeja, en approve/reject y una vez
al arrancar el gateway (un fallo del barrido de arranque no impide arrancar).

**Cancelación.** Si el solicitante pierde `access_admin` (`PUT /gateway-users/{id}/access`) o es
desactivado (`PATCH is_active=false`), sus solicitudes `pending` pasan a `cancelled`. Lo ya
`active` no cambia.

#### `GET /api/v1/capability-grants/pending`

Pendientes vigentes de todas las personas (de la más vieja a la más nueva). **Respuesta** `200` —
`ApiResponse[PendingCapabilityGrantOut[]]`: `CapabilityGrantOut` + `can_decide: bool` y
`blocked_reason: string | null` (código `access.*`: `access.self_approval_forbidden`,
`access.self_modification_forbidden`, `access.grant_user_inactive`,
`access.not_assignable`, `access.sod_conflict`, `access.grant_scope_not_found` o
`access.grant_not_pending` si el solicitante ya no es access_admin). Para un actor sin `access_admin`: `403`.

#### `POST /api/v1/capability-grants/{grant_id}/approve`

Body opcional: `{ "reason": "…" }` (máx. 500). **Respuesta** `200` — `ApiResponse[CapabilityGrantOut]`
con `status: "active"`, `decided_by`, `decided_at`, `expires_at: null`.

#### `POST /api/v1/capability-grants/{grant_id}/reject`

Body opcional: `{ "reason": "…" }`. Sin segundo aprobador (rechazar nunca da acceso).
**Respuesta** `200` — `ApiResponse[CapabilityGrantOut]` con `status: "rejected"`.

Errores de approve/reject: `403 access.forbidden`, `404 access.grant_not_found`, `409
access.grant_not_pending` (ya decidida, vencida o cancelada); solo approve: `409
access.self_approval_forbidden`, `409 access.self_modification_forbidden`, `409
access.grant_user_inactive`, `409 access.not_assignable`, `409 access.sod_conflict`, `404
access.grant_scope_not_found`.

### Vista propia: `GET /api/v1/auth/me` (campo nuevo, aditivo)

`MeOut` suma `capability_grants`: las capacidades puntuales **vivas** (`pending` | `active`) de
**quien pregunta**, y solo las suyas (nadie ve las de otra persona por acá). Es una lista que
puede venir vacía; el resto de los campos no cambia. Las `active` ya están sumadas en
`capabilities` (mismo predicado que `require()`); las `pending` no conceden nada todavía.

```json
"capability_grants": [
  { "id": 7, "capability": "databases.write", "scope_type": "environment", "scope_id": 1,
    "scope_name": "Desarrollo", "status": "active", "expires_at": null },
  { "id": 9, "capability": "exports.download", "scope_type": "server", "scope_id": 3,
    "scope_name": "pg-prod", "status": "pending", "expires_at": "2026-10-08T18:00:00" }
]
```

### `GET /api/v1/gateway-users/{user_id}/effective-access`

Acceso efectivo de la persona **con procedencia**, para decidir «por rol» vs «puntual». Solo
`access_admin` (`403 access.forbidden` opaco para el resto, incluido `security_officer`; la propia
persona usa `/auth/me`). `404 gateway_user.not_found` si no existe. Lo calcula el MISMO resolvedor
que hace cumplir `require()` (`explain`), sobre el mismo contexto que acuña el `Actor`.

```json
{
  "user_id": 2, "username": "destino", "active": true, "base_role": "viewer",
  "scope_roles": [
    { "scope_type": "environment", "scope_id": 2, "scope_name": "Producción", "role": "viewer" }
  ],
  "global_capabilities": [],
  "capabilities": [
    { "capability": "self.read", "source": "role", "scope_type": null, "scope_id": null,
      "scope_name": null, "grant_id": null, "implied_by": null, "inert": false },
    { "capability": "databases.read", "source": "scoped_role", "scope_type": "environment",
      "scope_id": 2, "scope_name": "Producción", "grant_id": null, "implied_by": null,
      "inert": false },
    { "capability": "blueprints.apply", "source": "capability_grant", "scope_type": "environment",
      "scope_id": 2, "scope_name": "Producción", "grant_id": 7, "implied_by": null,
      "inert": false },
    { "capability": "blueprints.read", "source": "capability_grant", "scope_type": "environment",
      "scope_id": 2, "scope_name": "Producción", "grant_id": 7, "implied_by": "blueprints.apply",
      "inert": false }
  ],
  "catalog_version": "ab12cd34ef56"
}
```

- `source`: `role` (rol base) | `scoped_role` (rol por alcance) | `global` (`access_admin` /
  `security_officer`) | `capability_grant` (puntual; trae `grant_id`).
- Una capacidad con varias fuentes aparece **una vez por fuente y alcance**. La lectura implícita
  de una puntual sale con `implied_by` = la capacidad puntual que la trae.
- Solo cuentan las puntuales `active`; las pendientes no aparecen. Si la persona está
  desactivada (`active: false`) sus puntuales siguen listadas con `inert: true` (retenidas, sin
  efecto: reactivarla las restaura) y NO cuentan como acceso. `inert` es `false` en el resto.
- El conjunto de `capability` de las entradas no inertes es exactamente `Actor.capabilities`.
- `scope_name` es `null` si el entorno o servidor ya no existe.

### Errores (`detail.public_context.code`)

| Código | HTTP | Cuándo |
|---|---|---|
| `access.self_approval_forbidden` | 409 | Quien pidió la sensible intenta aprobarla (approve). |
| `access.forbidden` | 403 | El actor no es `access_admin` (cuerpo opaco, no dice qué falta). |
| `gateway_user.not_found` | 404 | La persona no existe. |
| `access.self_modification_forbidden` | 409 | El actor es la persona destino (alta o revocación). |
| `access.grant_user_inactive` | 409 | La persona está desactivada (alta). |
| `access.capability_not_grantable` | 422 | Capacidad global o desconocida. |
| `access.grant_scope_not_found` | 404 | El entorno o servidor no existe. En `PUT /gateway-users/{id}/access` sale como **422** (el alcance es un campo del payload) con `missing_scopes: [{scope_type, scope_id}]`, y no se escribe nada. |
| `access.scope_has_grants` | 409 | `DELETE /environments/{id}` o `DELETE /servers/{id}` con accesos que todavía apuntan a ese destino. `public_context` trae `access_grant_count` (roles por alcance) y `capability_grant_count` (puntuales `pending`/`active`). Quitarlos (`PUT /access`) o revocarlos y reintentar. Las puntuales ya terminales no bloquean. |
| `access.last_admin_protected` | 409 | `PUT /gateway-users/{id}/access` o `PATCH /gateway-users/{id}` (`is_active: false`) dejaría el gateway sin ningún `access_admin` activo. Se evalúa dentro de la transacción de la escritura, con las filas bloqueadas: dos cambios concurrentes no pueden dejarlo en cero. |
| `access.not_assignable` | 409 | La función del actor no asigna esa capacidad (hoy: no es `access_admin`; en la práctica lo frena antes la ruta). Reemplaza a `access.grant_ceiling_exceeded`, retirado en C3. |
| `access.grant_duplicate` | 409 | Ya hay una viva (`pending` o `active`) para esa persona, capacidad y alcance. |
| `access.grant_not_found` | 404 | La capacidad no existe o es de otra persona (revocación). |
| `access.grant_not_pending` | 409 | La capacidad ya es terminal (revocación). |

Cada alta, decisión y revocación escribe `audit_log`: `capability_grant.created` (no sensibles),
`capability_grant.requested` (sensibles), `capability_grant.approved`, `capability_grant.rejected`,
`capability_grant.revoked`, `capability_grant.cancelled` y `capability_grant.expired`. Vencida y
cancelada por pérdida de rol del solicitante se auditan con `actor_type="system"` (sin persona).
Los rechazos por auto-otorgamiento, asignación y approve bloqueado quedan con `status` `failure`.

---

## 20. Autorización: dónde está cada contrato

Esta sección **no repite** los contratos: dice dónde está cada uno. El modelo (roles, capacidades,
capas, separación de deberes, step-up) está explicado en
[`features/authorization.md`](features/authorization.md), que es el punto de partida.

| Endpoints | Capacidad | Contrato |
|---|---|---|
| `GET /auth/me` | `self.read` | [§5](#get-apiv1authme); campos de autorización en `api-reference-v23.md` §1; `sod_warnings` en `api-reference-v29.md` §8.5; `bootstrap_window` en `v29` §10.4 |
| `POST /auth/step-up`, `POST /auth/password` | `self.read` | [§5](#5-autenticación-auth) |
| `GET /auth/sessions`, `POST /auth/sessions/revoke-others` | `self.read` | `api-reference-v23.md` §7.4 |
| `GET /authz/catalog` | `self.read` | `api-reference-v23.md` §2 (campos `grantable`, `sensitive`, `implies` en la [§19](#19-capacidades-puntuales-gateway-usersidcapability-grants)) |
| `GET /authz/scope-readiness` | `access.admin` | `api-reference-v23.md` §8 |
| `GET /authz/sod-report` | `access.admin` | `api-reference-v29.md` §8.6 |
| `/gateway-users` (listar, crear, detalle, `PATCH`, `PUT /{id}/access`, reinvitar) | `access.admin` | `api-reference-v24.md` §2; respuesta `202 access.elevation_pending` en `api-reference-v29.md` §9.3 |
| `POST /gateway-users/invite/accept` | público | `api-reference-v24.md` §2.4 |
| `GET /gateway-users/{id}/effective-access` | `access.admin` | [§19](#19-capacidades-puntuales-gateway-usersidcapability-grants) |
| `GET /gateway-users/{id}/sessions`, `POST /gateway-users/{id}/sessions/revoke` | `access.admin` | `api-reference-v29.md` §11.5–§11.6 |
| `/gateway-users/{id}/capability-grants`, `/capability-grants/pending`, `…/approve`, `…/reject` | `access.admin` | [§19](#19-capacidades-puntuales-gateway-usersidcapability-grants) |
| `/access-requests/pending`, `/{id}`, `/{id}/approve`, `/{id}/reject`, `/{id}/cancel` | `access.admin` | `api-reference-v29.md` §9.4 |
| `/api-tokens` (listar, crear, revocar) | `access.admin` | `api-reference-v24.md` §3 (y techo de agente en `api-reference-v23.md` §9) |
| `GET /audit-log`, `GET /audit-log/{id}` | `audit.read` | `api-reference-v29.md` §11.3–§11.4 |
| `POST /admin/crypto/rotate` | `crypto.rotate` | [§13](#13-administración-cifrado-admincrypto) |

Todas las rutas de esta tabla que no son `GET` piden step-up (salvo `…/cancel` y las de
`self.read`, cuya capacidad no lo exige). `v23` y `v24` tienen pasajes **históricos** marcados en
línea: ante una contradicción con `v29` o con `features/authorization.md`, mandan estos.

---

---

## 21. Canales de liberación (`/release-channels` y `/database-models/{id}/channels`)

Un canal (`stable`, `development`…) es un nombre global al que cada blueprint apunta con un **puntero** a una de
sus versiones. **Todo canal sirve siempre lo que dice su puntero**, sea cual sea su modo. Mover un puntero **no
toca ningún motor destino**: es configuración del gateway. Cómo se usa, en
[`features/release-channels.md`](features/release-channels.md); el porqué, en `development/decisiones-e-incidentes.md`
(«El puntero es la fuente única de todos los modos de canal»).

### 21.1 Capacidades

| Capacidad | Eje | Roles | Step-up | Qué protege |
|---|---|---|:-:|---|
| `release_channels.read` | global | viewer, operator, owner | — | Listar y leer el catálogo |
| `release_channels.write` | global | `security_officer` | ✓ | Crear, editar y borrar canales |
| `blueprints.promote` | entorno | owner (sensible) | ✓ | Promover y retroceder el puntero de un blueprint |

`blueprints.promote` se evalúa en el entorno más protegido entre las bases del blueprint (mismo guard de dos capas
que el borrado de una versión).

### 21.2 Catálogo (`/api/v1/release-channels`)

| Método y ruta | Capacidad | Qué hace |
|---|---|---|
| `GET /release-channels` | `release_channels.read` | Lista paginada. Cada canal trae `pointer_count` |
| `POST /release-channels` | `release_channels.write` | Crea. `201`. Body `{name, slug, resolution_mode, environment_id?}` |
| `GET /release-channels/{id}` | `release_channels.read` | Un canal |
| `PATCH /release-channels/{id}` | `release_channels.write` | Edita `name`, `resolution_mode`, `environment_id`. **El `slug` es inmutable** |
| `DELETE /release-channels/{id}` | `release_channels.write` | Borra. Con punteros exige `?confirm_slug=<slug>` |

`resolution_mode` es un vocabulario cerrado: `manual`, `latest` o `derived`. `derived` exige `environment_id`. El
modo decide **quién mueve el puntero**, no de dónde sale lo que se sirve (siempre del puntero).

El sembrado de arranque solo corre con la tabla vacía: `stable` (`manual`, enlazado a `production` si existe) y
`development` (`latest`, sin entorno).

### 21.3 Cómo se mueve el puntero

| Modo | Quién lo mueve | A qué versión |
|---|---|---|
| `manual` | Una persona, con `POST .../promote` | La que elige (solo revisadas) |
| `latest` | El gateway | La versión **revisada** más nueva del blueprint |
| `derived` | El gateway | El **mínimo** de `ManagedDatabase.model_version` de las bases del blueprint en el entorno del canal |

- **Solo versiones revisadas.** Si la nueva punta (o el nuevo mínimo) no está revisada, el puntero **se queda donde
  estaba**; al aprobarla, el gateway recalcula y se mueve.
- **Sin datos parciales.** Un canal `derived` sin entorno enlazado, o con alguna base de versión ilegible, no se
  mueve. Las bases en cuarentena no cuentan; una base en la versión base (`at_base`) tampoco.
- **Si nunca hubo valor**, el canal responde `409 release_channel.no_released_version` hasta el primer movimiento
  válido.
- **Idempotente:** si el destino calculado ya es el puntero, no se escribe nada ni queda evento.
- **Cuándo se recalcula** (en la misma transacción del cambio que lo dispara): crear o importar una versión,
  aprobar o revocar su revisión, aplicar migraciones a una base, dar de alta o adoptar una base (incluye el clon),
  cambiar el blueprint o el entorno de una base, y borrar una base. Además, **al arrancar** el gateway hace una
  pasada sobre todos los blueprints.
- **Huecos conocidos** (el puntero puede quedar atrasado hasta el próximo gancho o reinicio): el cambio de estado
  de una base (por ejemplo, a cuarentena), el borrado de una base por sincronización con el motor y el borrado de
  un servidor. Un puntero atrasado nunca apunta a una versión sin revisar ni de otro blueprint.

### 21.4 Puntero de cada blueprint (`/api/v1/database-models/{id}/channels`)

| Método y ruta | Capacidad | Qué hace |
|---|---|---|
| `GET .../channels` | `blueprints.read` | Cada canal con la versión que sirve para este blueprint. **No paginado** |
| `GET .../channels/{slug}/resolve` | `blueprints.read` | Lectura **tolerante** para pantalla. `20/min` |
| `POST .../channels/{slug}/promote` | `blueprints.promote` + step-up | Mueve el puntero de un canal manual |
| `GET .../channels/{slug}/history` | `blueprints.read` | Bitácora del puntero, paginada, la más reciente primero |

**`ChannelResolutionOut`** (listado y `resolve`): `channel_slug`, `channel_name`, `resolution_mode`,
`environment_slug`, `resolved`, `code`, `version`, `model_migration_id`, `source`, `databases[]` y
`derived_reference`.

- `source` es siempre `pointer`. `derived_reference.source` es `live` (mínimo leído del motor, solo en `resolve`) o
  `inventory_cache` (mínimo calculado con la versión anotada en el inventario, en el listado).
- **Un canal que no resuelve no es un error HTTP** en estas dos rutas: responden `200` con `resolved: false` y el
  motivo en `code`. Solo los consumidores (alta de base, aplicar a todas, integración) lo convierten en `409`.
- `derived_reference` trae el mínimo del entorno **junto** al puntero de todo canal con entorno enlazado. Es una
  referencia para la persona y **no decide nada**. Sus códigos `release_channel.derived_unreadable` y
  `release_channel.derived_no_scope` solo describen `derived_reference.code`; no salen de ningún consumidor.
- `databases[]` explica el cálculo `derived`: por base `current_version`, `readable`, `counted` e `ignored_reason`
  (`quarantined`, `database_missing`, `at_base`).

**`POST .../promote`**: body `{version, note?}` o `{model_migration_id, note?}` (exactamente uno de los dos).

- Solo canales `manual`: otro modo es `409 release_channel.not_manual`.
- Solo versiones **revisadas**: `409 release_channel.version_unreviewed`.
- Un **retroceso** exige `?confirm_slug=<slug>`; sin él, `409 release_channel.promote_backward_requires_confirm`.
- Misma versión que la actual: `200` con `changed: false`, sin escritura y sin evento.
- Versión inexistente: `422 release_channel.version_not_found`; de otro blueprint:
  `422 release_channel.version_not_in_blueprint`.

Respuesta: `{channel_slug, changed, event_type, from_version, to_version, model_migration_id}`.

**Bitácora** (`ChannelPointerEventOut`): `event_type` es `promote`, `demote` o `version_deleted` (vocabulario
cerrado). Un movimiento automático se reconoce por `actor_type: "system"`, `actor_username: "sistema"`,
`actor_admin_id: null` y `note: "automático (latest)"` o `"automático (derived)"`. Borrar la versión apuntada deja
`version_deleted` también en canales automáticos.

### 21.5 Superficies que consumen canales

El destino se pide por versión **o** por canal, nunca por los dos (`422 release_channel.target_conflict`).

| Superficie | Campo | Nota |
|---|---|---|
| `POST /managed-databases` | `channel` | Excluyente con `target_version`; exige `apply_migrations`. La respuesta trae `resolved_channel {slug, mode, version, source}` |
| `POST /database-models/{id}/migrations/apply-all` | query `channel` o `target_version` (`^\d{4,10}$`) | Las bases por encima del tope no retroceden (`pending_versions: []`) |
| `POST /integration/databases/{db_id}/migrations/apply` | body `channel` | Excluyente con `version`. Solo hacia adelante (`422 integration.migration_target_not_forward`) |
| `GET .../migrations/{version}/delete-plan` y `DELETE .../migrations/{version}` | respuesta `channel_moves[]` | `{release_channel_id, channel_slug, from_version, to_version, to_migration_id}` |
| `DELETE /environments/{id}` | — | `409 environment.has_release_channels` (con `channel_count`) si algún canal lo enlaza |

**`channel_moves` y el borrado de una versión.** Si la versión a borrar es la apuntada por un canal, el puntero se
mueve a la versión revisada inmediatamente anterior (o queda sin versión, `to_version: null`) en la misma
transacción local del renumerado, con un evento `version_deleted`. El borrado **nunca se bloquea** por un canal.
`requires_confirmation` es `stamp_plan` **o** `channel_moves`: borrar una versión apuntada por un canal manual
exige el `confirm_token` del plan aunque ninguna base se toque.

### 21.6 Lectura de canales por la API de integración (bearer)

Base `/api/v1/integration`. Scope **`blueprint.read_channel`** (capacidad evaluada `blueprints.read`). Solo
lectura, sin cuerpo; quedan auditadas como `integration.call`.

| Ruta | Para qué |
|---|---|
| `GET /databases/{db_id}/channels/{channel_slug}` | Versión que sirve el canal para el blueprint **asignado a la base** |
| `GET /blueprints/{model_id}/channels/{channel_slug}` | Lo mismo **sin necesitar una base**: lo que se lee antes de crear nada |

`channel_slug`: 1 a 60 caracteres, se recorta y pasa a minúsculas. Respuesta `200` (`ResolvedChannelOut`), idéntica
en las dos rutas, y **estricta**: o trae `version`, o es un `409 release_channel.*`; nunca `null` ni «la última».

```json
{ "data": { "slug": "stable", "mode": "manual", "version": "0004", "source": "pointer" } }
```

`mode` es informativo y no cambia cómo se interpreta `version`; `source` es siempre `pointer`. No se abre ninguna
conexión a las bases al responder.

**Autorización del destino**

| Ruta | Regla |
|---|---|
| Por base | El blueprint sale de la base, nunca del llamador. Rigen la allowlist de servidores y, si el token la tiene, la de blueprints. Base inexistente o de un servidor fuera de la allowlist: el mismo `403 integration.server_not_allowed` |
| Por blueprint, token **con** `blueprint_ids` | `model_id` debe estar en la lista (`403 integration.blueprint_not_allowed`). No se exige que tenga bases ni se mira la allowlist de servidores |
| Por blueprint, token **sin** `blueprint_ids` | El blueprint debe tener bases y todas en servidores de la allowlist; si no, `403 integration.server_not_allowed` |
| `model_id` inexistente | Responde igual que uno no permitido (`403`), para no revelar qué ids existen |

Para aprovisionar, el token debe emitirse **con `blueprint_ids`**: antes de la primera base la ruta por base no
sirve, y la ruta por blueprint sin esa lista se niega. Un token anterior a esta función no trae
`blueprint.read_channel`: hay que emitir uno nuevo o hacer `PATCH` con la lista completa de `scopes`. Si al emisor
le bajan `blueprints.read`, el scope queda suspendido (`403 integration.scope_missing`).

**Flujo recomendado para generar la base de un cliente** (token con `servers.list`, `blueprint.read_channel`,
`engine_users.create`, `databases.create`, `databases.assign_blueprint`, `migrations.apply_forward`,
`engine_users.assign_database`, `migrations.read_version`):

1. `GET /blueprints/{model_id}/channels/{channel}` → versión vigente. Si es `409 release_channel.no_released_version`,
   **detener**.
2. `GET /servers` → elegir servidor.
3. `POST /engine-users` → guardar la `password` devuelta (única vez).
4. `POST /databases` → base vacía.
5. `PUT /databases/{db_id}/blueprint` → asignar `model_id`.
6. `POST /databases/{db_id}/migrations/apply` con `{"channel": "<canal>", "dry_run": true}`, revisar `blocked_by`,
   repetir sin `dry_run`.
7. `POST /engine-users/{user_id}/databases/{db_id}` → dar el perfil.
8. `GET /databases/{db_id}/migrations/version` → confirmar `pending: []`.

Si el canal es automático, el puntero puede moverse entre el paso 1 y el 6. Con `channel` en el paso 6 se aplica
«lo vigente al aplicar»; con `version` se aplica exactamente lo leído en el paso 1.

### 21.7 Códigos de error

Todos viajan en `detail.public_context.code`. El modo inválido no tiene código propio: es el `422` estándar de
Pydantic.

| Código | HTTP | Cuándo |
|---|:-:|---|
| `release_channel.not_found` | 404 / 422 | El canal no existe (404 en la ruta, 422 si llega en un body o query) |
| `release_channel.name_taken` / `release_channel.slug_taken` | 409 | Nombre o slug repetido |
| `release_channel.environment_required` | 422 | `derived` sin entorno |
| `release_channel.confirmation_required` | 422 | Borrar un canal con punteros sin `confirm_slug` correcto |
| `release_channel.not_manual` | 409 | Promover un canal que no es manual |
| `release_channel.version_not_found` / `release_channel.version_not_in_blueprint` | 422 | Versión inexistente / de otro blueprint |
| `release_channel.version_unreviewed` | 409 | Promover una versión sin revisar |
| `release_channel.promote_backward_requires_confirm` | 409 | Retroceso sin `confirm_slug` |
| `release_channel.no_released_version` | 409 | El puntero no tiene valor para el blueprint (solo en consumidores) |
| `release_channel.pointer_inconsistent` | 409 | El puntero apunta a una versión de otro blueprint |
| `release_channel.target_conflict` | 422 | `channel` y `target_version`/`version` a la vez |
| `environment.has_release_channels` | 409 | Borrar un entorno enlazado por canales |
| `integration.blueprint_not_assigned` | 404 | Ruta por base: la base no tiene blueprint |
| `integration.blueprint_not_allowed` / `integration.server_not_allowed` / `integration.scope_missing` | 403 | Ver autorización del destino |
| `integration.token_invalid` / `integration.disabled` | 401 / 503 | Bearer inválido / integración apagada |

Los `409 release_channel.*` de los consumidores son estrictos a propósito: no se tratan como «versión 0» ni se cae a
«la última».

### 21.8 Qué NO se verificó

No hubo llamadas con un token real ni contra un servidor real; la pasada de arranque que rellena punteros no se
probó con datos reales; los ejemplos salen de los schemas, no de una captura.

---

## 22. Tokens de integración y API `/integration`

Dos superficies: la gestión de tokens (`/api/v1/integration-tokens`, con sesión, para personas) y la API de
integración (`/api/v1/integration/...`, con bearer, para el backend de un proyecto web). Cómo habilitarla y
operarla, en [`features/integration-api-tokens.md`](features/integration-api-tokens.md); guía para quien integra,
en [`integration-api-contract.md`](integration-api-contract.md); el porqué, en `development/decisiones-e-incidentes.md`.

Nace **apagada** (`INTEGRATION_API_ENABLED=false`). Apagada, `/integration/*` responde `503 integration.disabled` y
la gestión no crea ni edita (listar y revocar siguen andando). El contrato tiene **13 scopes**: 5 de lectura,
6 de escritura y 2 destructivos (`migrations.rollback`, `migrations.stamp`). Las rutas de lectura de canales
(`blueprint.read_channel`) están detalladas en la §21.6.
### 22.1 El bearer

`Authorization: Bearer datumint.<public_id>.<secret>`. Solo el HMAC del secreto se guarda; el valor completo se
muestra **una vez**, en la respuesta de creación. La cookie de sesión **no** autentica `/integration/*` y el
bearer **no** autentica el resto de la API ni `/mcp/`.

### 22.2 Gestión de tokens (`/api/v1/integration-tokens`, sesión + CSRF)

Guard: `access.admin` **o** `integration_tokens.own` (los tres roles base). Con `integration_tokens.own` se
administran solo los tokens propios; `access.admin` lista y revoca los de todos, no los edita.

| Método y ruta | Qué hace | Notas |
|---|---|---|
| `GET /integration-tokens` | Lista (paginada) | `suspended_scopes` solo en los tokens propios |
| `GET /integration-tokens/ceiling` | Scopes que quien llama puede otorgar hoy, `enabled`, `max_ttl_days`, `max_write_ttl_days` | Un scope que el rol no tiene **no aparece** (ni como deshabilitado) |
| `POST /integration-tokens` | Emite un token; `201` con `token` (el bearer) | 10/min; `503` con el kill switch apagado |
| `PATCH /integration-tokens/{token_pk}` | Reemplaza `name`, `scopes`, `server_ids`, `blueprint_ids`, `note` (lo enviado reemplaza) | El secreto, el TTL y el emisor no cambian |
| `DELETE /integration-tokens/{token_pk}` | Revoca | `409` si ya estaba revocado |

Todo método no seguro (alta, edición y revocación) exige **step-up fresco** de la persona, por el guard de la
ruta; la máquina que usa el token nunca lo responde, así que la contraseña de quien emite se paga al otorgar el
scope.

`POST` acepta `{name (3..128), scopes[≥1], server_ids[], blueprint_ids[], expires_in_days?, note?}` con
`extra="forbid"`. No hay campo de secreto.

#### Reglas de emisión

- **Vocabulario cerrado**: cada scope pertenece a la lista de abajo; cualquier otro valor (incluidas capacidades
  reales como `access.admin`, `data.read` o `blueprints.apply`) es `422 integration_token.unknown_scope`.
- **Techo del emisor**: un scope que quien emite no tiene hoy es `403 integration_token.scope_not_allowed`.
- **Allowlist de servidores obligatoria** (`422 integration_token.server_allowlist_required`).
- **TTL**: 90 días con solo lectura, 30 con cualquier scope de escritura (`INTEGRATION_TOKEN_MAX_TTL_DAYS`,
  `INTEGRATION_WRITE_TOKEN_MAX_TTL_DAYS`). Excederlo es `422 integration_token.ttl_too_long` con `max_days`; una
  edición que lo excede deja el token intacto.
- **Sin expiración**: `never_expires: true` en la emisión (excluyente con `expires_in_days`) deja `expires_at: null`.
  Solo con `INTEGRATION_ALLOW_NON_EXPIRING_TOKENS=true`, y nunca con scopes destructivos (tampoco se pueden agregar
  después). `GET /integration-tokens/ceiling` informa `allow_non_expiring`.
- **Scopes suspendidos**: lo efectivo es `guardado ∩ vocabulario ∩ lo que el emisor tiene hoy`. Si el emisor
  pierde una capacidad, el scope queda suspendido en el acto (se informa en `suspended_scopes`) y vuelve si
  la recupera; no se puede volver a agregar mientras esté suspendido, sí quitar.

### 22.3 Scopes

| Scope | Nivel | Capacidad evaluada | Ruta |
|---|---|---|---|
| `servers.list` | lectura | `servers.read` | `GET /integration/servers` |
| `databases.list` | lectura | `databases.read` | `GET /integration/databases?server_id=` |
| `blueprint.read_assigned` | lectura | `blueprints.read` | `GET /integration/databases/{db_id}/blueprint` |
| `blueprint.read_channel` | lectura | `blueprints.read` | `GET /integration/databases/{db_id}/channels/{channel_slug}` · `GET /integration/blueprints/{model_id}/channels/{channel_slug}` |
| `migrations.read_version` | lectura | `blueprints.read` | `GET /integration/databases/{db_id}/migrations/version` |
| `databases.create` | escritura | `databases.write` | `POST /integration/databases` |
| `engine_users.create` | escritura | `engine_users.write` | `POST /integration/engine-users` |
| `engine_users.assign_profile` | escritura | `engine_users.write` | `POST /integration/engine-users/{user_id}/profiles/{profile_id}` |
| `engine_users.assign_database` | escritura | `engine_users.write` | `POST /integration/engine-users/{user_id}/databases/{db_id}` |
| `databases.assign_blueprint` | escritura | `databases.write` | `PUT /integration/databases/{db_id}/blueprint` |
| `migrations.apply_forward` | escritura | `blueprints.apply` | `POST /integration/databases/{db_id}/migrations/apply` |
| `migrations.rollback` | **destructivo** | `blueprints.apply` | `POST /integration/databases/{db_id}/migrations/rollback` |
| `migrations.stamp` | **destructivo** | `blueprints.apply` | `POST /integration/databases/{db_id}/migrations/stamp` |

Ningún scope se publica en `/authz/catalog`: son un vocabulario propio, que **mapea** a capacidades existentes.

### 22.4 Operaciones

Todas las respuestas usan `ApiResponse[T]`. Los cuerpos rechazan campos desconocidos (`422`): `force`,
`password`, `model_id` al crear una base, etc. no se ignoran en silencio.

| Operación | Cuerpo | Respuesta |
|---|---|---|
| `GET /servers` | — | `[{id, name, engine}]`: solo los de la allowlist que el emisor puede leer; sin host ni puerto |
| `GET /databases` | `server_id` (query, obligatorio) | `[{id, name, server_id, model_id, model_version, environment_id, status, charset, collation}]` |
| `GET .../blueprint` | — | `{model_id, name, slug, model_version}`; `404 integration.blueprint_not_assigned` si no hay |
| `GET .../channels/{channel_slug}` | — | `{slug, mode, version, source}` (resolver estricto: `409 release_channel.*` si no resuelve) |
| `GET .../migrations/version` | — | `{current_version, latest_version, pending[]}` |
| `POST /databases` | `{name, server_id, owner_id, environment_id?, charset?, collation?, notes?}` | `201`, base **vacía** (el blueprint se asigna y aplica con sus scopes) |
| `POST /engine-users` | `{server_id, username, host?, notes?}` | `201` con `password` generada por el gateway; **única vez**, no se puede releer |
| `POST .../profiles/{profile_id}` | `{object_mappings[≥1]}` (nivel ≠ `global`, `object_ref.database` obligatorio) | `ApplyProfileResult` |
| `POST .../databases/{db_id}` | `{profile_id}` (el mapeo lo arma el gateway) | `ApplyProfileResult` |
| `PUT .../blueprint` | `{model_id}` | Base actualizada; repetir el mismo es éxito sin cambios |
| `POST .../migrations/apply` | `{version?, dry_run?}` | `MigrationApplyOut`; `force=False` y `on_failure="auto"` fijos |
| `POST .../migrations/rollback` | `{from_version, to_version}` (ambas obligatorias) | `MigrationRollbackOut`; sin `dry_run`, `force` ni `purge` (`422`) |
| `POST .../migrations/stamp` | `{expected_current_version, version}` (la clave es obligatoria; puede ser `null`) | `MigrationStatusOut`; sin `force` ni `purge` (`422`) |

Reglas que cambian el resultado:

- **Servidor desconocido o fuera de la allowlist**: un mismo `403 integration.server_not_allowed` (no se pueden
  enumerar ids). Un blueprint fuera de la allowlist del token: `403 integration.blueprint_not_allowed`.
- **Perfiles**: un perfil con cualquier ítem que delega poder (`ALL PRIVILEGES`, `GRANT OPTION`) es
  `403 integration.profile_requires_grant_admin` **antes** de aplicar un solo grant. Un usuario de otro servidor
  o inexistente, o una base no gestionada en su servidor, es `409 integration.server_mismatch`.
- **Blueprint**: reemplazar uno ya asignado es `409 integration.blueprint_already_assigned`.
- **Migraciones**: una `version` menor a la actual es `422 integration.migration_target_not_forward`; la actual
  es un no-op exitoso. La guarda de migraciones destructivas de los entornos protegidos rige como para una persona.

#### Nivel destructivo (`migrations.rollback`, `migrations.stamp`)

Sobre lo anterior, y siempre antes de tocar nada (ninguna de estas negativas deja una fila `attempt`):

- **Allowlist de blueprints obligatoria** en el token (`422 integration_token.blueprint_allowlist_required` al
  emitir o editar). En la llamada, una base sin blueprint, o fuera de la allowlist, o un token con la allowlist
  vaciada, es `403 integration.blueprint_not_allowed` (falla cerrado).
- **Entorno**: un entorno que bloquea migraciones destructivas es `409 integration.environment_blocks_destructive`; una
  base **sin entorno** es `409 integration.environment_unclassified` (el flujo humano la deja pasar; una máquina no).
- **Cuarentena**: base en `error` es `409 integration.database_quarantined`.
- **Rollback**: compare-and-set en ambos extremos (`from_version` debe ser la versión viva, lo valida el
  controlador con `422`; `to_version` debe ser anterior). No hay "un paso atrás" ni "volver a la base". **Prueba de
  historial**: cada versión que se desharía tiene que constar, en su última fila de `database_migration_history`,
  como `up` exitoso con el `checksum` que el blueprint tiene hoy; si no, `409 integration.rollback_unapplied_version`
  (con `versions[]`). Una versión solo marcada con `stamp`, editada después de correr, o con fila legada sin
  dirección, no tiene prueba.
- **Stamp**: `expected_current_version` distinto de la versión viva es `409 integration.stamp_version_conflict`;
  contabilidad huérfana es `409 integration.stamp_orphan_accounting`; marcar la versión en la que ya está es un
  `200` sin cambios, auditado y sin llegar al controlador. Nunca limpia una cuarentena.
- **Step-up al emitir**: agregar o editar un scope destructivo exige un step-up de `blueprints.apply` **de la persona
  que emite** (en tiempo de ejecución el bearer no lo pide, como en el resto). Con `STEP_UP_ENFORCED=false` se
  respeta el interruptor global. TTL máximo `INTEGRATION_DESTRUCTIVE_TOKEN_MAX_TTL_DAYS` (7 días).
- **Auditoría**: `audit.record_intent` (falla cerrado, `500` y no se ejecuta nada) con acción
  `integration.migration.rollback` / `integration.migration.stamp`, antes de delegar en el controlador humano.
- **Cupo**: `INTEGRATION_DESTRUCTIVE_RATE_LIMIT` (`5/minute`) por token, además de los de lectura y escritura.

Los errores propios del controlador (versión de confirmación distinta `422`, falta `down_sql` `409`, captura sin revisar
`409`, checkpoint parcial `409`, versión desconocida `422`) llegan sin cambios.

### 22.5 Códigos de error (`public_context.code`)

| Código | HTTP | Cuándo |
|---|---|---|
| `integration.disabled` | 503 | Kill switch apagado |
| `integration.token_invalid` | 401 | Bearer ausente, malformado, desconocido, revocado, vencido o con emisor inactivo (misma respuesta byte a byte) |
| `integration.scope_missing` | 403 | El token no tiene el scope efectivo de la ruta |
| `integration.server_not_allowed` | 403 | Servidor fuera de la allowlist o inexistente |
| `integration.blueprint_not_allowed` | 403 | Blueprint fuera de la allowlist del token |
| `integration.profile_requires_grant_admin` | 403 | El perfil delega poder |
| `integration.blueprint_already_assigned` | 409 | La base ya tiene otro blueprint |
| `integration.server_mismatch` | 409 | Usuario o base de otro servidor |
| `integration.blueprint_not_assigned` | 404 | La base no tiene blueprint |
| `integration.migration_target_not_forward` | 422 | Versión objetivo anterior a la actual |
| `integration.environment_blocks_destructive` | 409 | El entorno de la base bloquea operaciones destructivas |
| `integration.environment_unclassified` | 409 | La base no tiene entorno asignado |
| `integration.database_quarantined` | 409 | La base está en cuarentena |
| `integration.rollback_unapplied_version` | 409 | Una versión a revertir no consta como aplicada por el gateway con su definición actual |
| `integration.stamp_version_conflict` | 409 | `expected_current_version` no coincide con la versión viva |
| `integration.stamp_orphan_accounting` | 409 | La base tiene contabilidad de migraciones huérfana |
| `integration_token.blueprint_allowlist_required` | 422 | Un scope destructivo exige allowlist de blueprints |
| `integration_token.not_found` | 404 | Token ajeno o inexistente |
| `integration_token.ttl_too_long` | 422 | TTL por encima del tope del nivel (`max_days`); también `never_expires` con un scope destructivo |
| `integration_token.non_expiring_not_allowed` | 422 | `never_expires: true` con `INTEGRATION_ALLOW_NON_EXPIRING_TOKENS` apagado |
| `integration_token.scope_not_allowed` | 403 | El emisor no tiene la capacidad del scope |
| `integration_token.unknown_scope` | 422 | Scope fuera del vocabulario |
| `integration_token.server_allowlist_required` | 422 | Falta la allowlist de servidores |
| `integration_token.already_revoked` | 409 | El token ya estaba revocado |
| `integration_token.server_not_found` | 422 | Un `server_ids` no existe |
| `integration_token.blueprint_not_found` | 422 | Un `blueprint_ids` no existe |

Además, `429` con `Retry-After` al superar el cupo por token (`INTEGRATION_RATE_LIMIT`, y el de escritura
`INTEGRATION_WRITE_RATE_LIMIT` en los de escritura, `INTEGRATION_DESTRUCTIVE_RATE_LIMIT` en los destructivos) y `429` por IP al superar los rechazos de credencial
(`INTEGRATION_AUTH_FAILURE_RATE_LIMIT`).

### 22.6 Auditoría

Cada llamada autenticada deja `integration.call` con `actor_type = "integration"`, `integration_token_id` = PK del
token, `admin_id` = el emisor y `admin_username = "integration:<public_id>"`. `GET /audit-log?actor_type=integration`
filtra por ellas. La gestión deja `integration_token.create|update|revoke` con la persona como actor. Ninguna fila
lleva el secreto ni la contraseña generada.

### 22.7 Variables de entorno

`INTEGRATION_API_ENABLED` (`false`), `INTEGRATION_TOKEN_MAX_TTL_DAYS` (`90`), `INTEGRATION_WRITE_TOKEN_MAX_TTL_DAYS`
(`30`, no puede superar la anterior), `INTEGRATION_RATE_LIMIT` (`120/minute`), `INTEGRATION_WRITE_RATE_LIMIT`
(`20/minute`), `INTEGRATION_DESTRUCTIVE_TOKEN_MAX_TTL_DAYS` (`7`, no puede superar el de escritura: con
`INTEGRATION_WRITE_TOKEN_MAX_TTL_DAYS` menor a 7 hay que bajar también este), `INTEGRATION_DESTRUCTIVE_RATE_LIMIT`
(`5/minute`), `INTEGRATION_AUTH_FAILURE_RATE_LIMIT` (`30/minute`). Se leen una vez al importar
`app/core/environments.py`: cambiarlas exige reinicio.

---

## 23. Aprobación de versiones destructivas para producción

Una versión **destructiva** (`DROP`, `TRUNCATE`, `DELETE` sin `WHERE`, `ALTER ... DROP COLUMN`) no queda bloqueada
para siempre en un entorno con `blocks_destructive_migrations`: se ejecuta si **otra persona** la aprobó antes. La
aprobación solo escribe metadatos del gateway; ejecutar es el `apply` / `rollback` de siempre. Cómo se usa, en
[`features/environments.md`](features/environments.md) y [`features/model-migrations.md`](features/model-migrations.md);
el porqué, en `development/decisiones-e-incidentes.md`.
### 23.1 Capacidades

| Capacidad | Eje | Roles | Step-up | Qué protege |
|---|---|---|:-:|---|
| `blueprints.approve_destructive` | entorno | owner (sensible) | ✓ (salvo revocar) | Aprobar y revocar aprobaciones |
| `blueprints.apply_destructive` | entorno | owner (sensible) | ✓ | Ejecutar una versión destructiva ya aprobada en un entorno protegido |

Ninguna la tiene un token de integración ni el MCP (`agent_allowed=false`). El conjunto sensible pasa de 17 a 19.
Aprobar y ejecutar son capacidades separadas: quien ejecuta puede ser el autor, o la misma persona que aprobó.

### 23.2 Endpoints

| Método y ruta | Capacidad | Qué hace |
|---|---|---|
| `GET /database-models/{id}/destructive-versions` | `blueprints.read` | Versiones destructivas del blueprint, una fila por dirección. Query `managed_database_id` opcional |
| `POST /database-models/{id}/migrations/{version}/destructive-approvals` | `approve_destructive` + step-up | Aprueba para **todo el blueprint** (alcance `blueprint`). `201` |
| `POST /managed-databases/{db_id}/migrations/{version}/destructive-approvals` | `approve_destructive` + step-up | Aprueba para **una base** (alcance `database`). `201` |
| `POST /database-models/{id}/destructive-approvals/batch` | `approve_destructive` + step-up | Aprueba **varias versiones** con alcance `blueprint`. `200`, resultado parcial por ítem |
| `POST /managed-databases/{db_id}/destructive-approvals/batch` | `approve_destructive` + step-up | Lo mismo con alcance `database`. `200` |
| `POST /destructive-approvals/{approval_id}/revoke` | `approve_destructive`, **sin step-up** | Revoca. Idempotente |

`{version}` cumple `^\d{4,10}$`. Todas responden con `ApiResponse[T]`.

#### Listar (`GET .../destructive-versions`)

Devuelve `list[DestructiveVersionOut]`: `migration_id`, `version`, `name`, `direction` (`up` | `down`), `sql_hash`,
`reasons_by_engine`, `author`, `last_sql_editor`, `author_unknown`, `approval_status`, `effective_approval_id` y
`approvals[]` (historial completo, la más reciente primero).

- `sql_hash` es el hash **actual** del SQL de esa dirección y es el `expected_sql_hash` que hay que enviar al aprobar.
- `approval_status` es `approved`, `unapproved`, `revoked` o `stale_hash`. Con `managed_database_id` se evalúa para
  esa base (sus aprobaciones y las del blueprint); sin él, solo cuentan las de alcance `blueprint`.
- `stale_hash`: hay una aprobación vigente, pero de otro SQL (se editó después) o con otra `hash_basis`. **Nunca se
  toma por aprobada**: hay que re-aprobar.
- Una dirección `down` solo aparece si hay `down_sql`.

#### Aprobar

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

#### Aprobar en lote

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

#### Revocar

Body: `{reason}` (10 a 500). Pueden revocar: quien aprobó, **o** quien tiene `approve_destructive` en el alcance de
la aprobación, o un owner. **`access.admin` no tiene camino**: no es operativa por diseño del catálogo.

- Afecta solo ejecuciones futuras. No toca lo ya aplicado.
- **Idempotente**: revocar una aprobación ya revocada o reemplazada responde `200` con la fila tal cual (mismo
  revocador y fecha). No existe `already_revoked`.
- No exige step-up: frenar una operación destructiva no puede costar más que lanzarla.
- Un `approval_id` que no existe, o que el actor no puede ver, responde `404` o `403`.

### 23.3 Extensiones: el flujo dry-run, token, re-tipeo, step-up

Se aplica igual a **apply**, **rollback** y **apply-all** cuando el entorno de la base bloquea lo destructivo y hay
versiones destructivas en juego. Una base sin entorno, o en un entorno que no bloquea, **no cambia**.

1. **Dry-run.** Devuelve el plan. Solo si todas las versiones están aprobadas **y** quien consulta es una persona con
   `apply_destructive` en la base, trae un `confirm_token`.
2. **Ejecución.** Se envía el `confirm_token` y el re-tipeo (`confirm_name` o `confirm_phrase`). Todo bajo step-up.
3. **Re-chequeo.** El servidor vuelve a resolver las aprobaciones en una sesión nueva al ejecutar: una revocación
   entre el plan y la ejecución se rechaza.

El token vale **300 s** y ata el conjunto exacto de aprobaciones. El re-tipeo lo valida el **servidor**: el token da
frescura, no intención. `force` **no** interviene en nada de esto.

#### Aplicar a una base — `POST /managed-databases/{id}/migrations/apply`

Query nuevos: `confirm_token` (≤200) y `confirm_name` (≤128, el nombre exacto de la base).

El dry-run trae `blocked_by` (versiones sin aprobación vigente) y `destructive_plan`:
`{direction, requires_confirmation, executor_allowed, all_approved, versions[], confirm_token, expires_at,
confirm_name_expected}`. Cada elemento de `versions[]`: `version`, `migration_id`, `sql_hash`, `reasons`,
`approval_status`, `approval_id`, `approval_scope`.

#### Revertir — `POST /managed-databases/{id}/migrations/rollback`

**Cambio de comportamiento.** El rollback antes no miraba el entorno. Ahora, si el `down_sql` de alguna versión del
camino es destructivo en un entorno protegido, exige una aprobación de dirección `down` (la `up` no alcanza),
`apply_destructive`, token y re-tipeo.

Query nuevos: `dry_run`, `confirm_token`, `confirm_name`. `confirm_version` pasa a ser **opcional solo con
`dry_run=true`**; sin dry-run y sin él responde `422`. El dry-run no escribe en el motor y devuelve
`rollback_path_versions`, `blocked_by` y `destructive_plan` con `direction: "down"`.

#### Aplicar a todas — `POST /database-models/{id}/migrations/apply-all`

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

### 23.4 Códigos de error

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

### 23.5 Cosas que sorprenden

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

---

## 24. MCP: acceso de agentes (credenciales, tokens y tools)

El MCP (`POST /mcp/`) deja que un agente lea el catálogo y, opcionalmente, datos de las bases del gateway. **Invariante
vigente:** el MCP ejecuta únicamente `SELECT` únicos validados, dentro de una transacción `READ ONLY` y bajo una
credencial por base con `SELECT` solamente; ninguna otra tool ejecuta SQL ni procedimientos, triggers, eventos o vistas.
Orden de lectura: 24.1 credencial de solo lectura del servidor, 24.2 aprovisionarla, 24.3 estado de acceso por base, 24.4 credencial de datos, 24.5 scopes de datos y opt-in, 24.6 `search_schema`, 24.7 `draft_query`, 24.8 a 24.9 tools de datos, 24.10 definiciones y estadísticas, 24.11 tools de blueprints, 24.12 versión del motor. El transporte y las tools base del MCP (`list_databases`,
`list_objects`, `get_schema`, `check_freshness`, `diff_schemas`, `list_environments`, `list_exports`, `list_clones`,
`list_catalogs`) están descritos en `api-reference-v23.md` §9, eliminado (ver historial de git). Guía para colaboradores:
[`features/mcp-para-colaboradores.md`](features/mcp-para-colaboradores.md).

### 24.1 Credencial de solo lectura del servidor (manual)

Relacionado con la credencial de **solo lectura** que el MCP usa para leer el catálogo de un motor
(plan 12 §5.2). Lo consume la pantalla de detalle de servidor del frontend.

#### Resumen para el frontend

| Cambio | Ruta | Capacidad |
|---|---|---|
| Campos nuevos en `ServerOut` | todas las que devuelven un servidor | — |
| Nuevo | `PUT /api/v1/servers/{server_id}/readonly-credential` | `servers.admin` + step-up |
| Nuevo | `DELETE /api/v1/servers/{server_id}/readonly-credential` | `servers.admin` + step-up |
| Parámetro nuevo | `POST /api/v1/servers/{server_id}/test-connection?credential=readonly` | `servers.read`, y escala a `servers.admin` + step-up con `credential=readonly` |
| Campo nuevo en `ConnectionInfo` | respuesta de `test-connection` | — |

Ningún contrato existente cambia de forma: todo es aditivo.

#### `ServerOut`: dos campos nuevos

```jsonc
{
  "has_readonly_credential": true,          // hay usuario y contraseña de solo lectura
  "readonly_verified_at": "2026-10-02T15:04:05" // null = sin verificar (el MCP no lo lee)
}
```

**Nunca** sale el usuario ni la contraseña de solo lectura, cifrados o en claro.

`readonly_verified_at` vuelve a `null` cuando:

- se registra o reemplaza la credencial (`PUT`);
- la sonda negativa falla;
- se quita la credencial (`DELETE`).

Además, un `PATCH /servers/{id}` que **re-apunta** el servidor (cambia `host`, `port` o `engine`, o
debilita un TLS `require` o más fuerte) **descarta la credencial de solo lectura** entera:
`has_readonly_credential` vuelve a `false`. El `audit_log` lo registra como «credencial de solo
lectura descartada por re-apuntado».

#### `PUT /servers/{server_id}/readonly-credential`

```json
{ "username": "mcp_ro", "password": "…" }
```

- `extra="forbid"`: un campo de más es `422`.
- Responde `ServerOut`, con `readonly_verified_at: null`. El mensaje recuerda que falta verificarla.
- Auditado como `server.readonly_credential.set`, sin usuario ni contraseña.

#### `DELETE /servers/{server_id}/readonly-credential`

- Idempotente: quitarla dos veces no es un error.
- Responde `ServerOut` con `has_readonly_credential: false`.
- Auditado como `server.readonly_credential.clear`.

#### `POST /servers/{server_id}/test-connection?credential=readonly`

`credential` admite `root` (default, comportamiento de siempre) o `readonly`.

Con `readonly` corre la **sonda negativa**: conecta con la credencial de solo lectura y exige que
el motor observe que no puede escribir. Si pasa, fija `readonly_verified_at` y lo devuelve en
`ConnectionInfo`:

```jsonc
{ "ok": true, "dialect": "mysql", "server_version": "8.0.36",
  "readonly_verified_at": "2026-10-02T15:04:05" }
```

No cambia el `status` del servidor, que describe la conexión con la pseudo-root.

Errores con `public_context.code`:

| Código | Status | Cuándo |
|---|---|---|
| `server.readonly_credential_missing` | 409 | El servidor no tiene credencial de solo lectura registrada |
| `server.readonly_probe_failed` | 422 | La credencial puede escribir. `public_context.violations` lista los motivos (`privilege:insert`, `global_privilege:select`, `select_on_mysql_schema`, `grant_option`, `unrecognized_grant`, `role_attribute:rolsuper`, `default_transaction_read_only_off`, `create_on_database`, `create_on_schema_public`, `member_of:pg_write_all_data`, `table_write_privileges`, `write_attempt_succeeded`) |
| `access.step_up_required` | 403 | Falta la confirmación de contraseña (`servers.admin` la exige) |

Los errores de conexión del motor son los mismos que en la prueba con `root`.

**Sugerencia de UI:** un bloque «Acceso de agentes (MCP)» en el detalle del servidor, con el estado
(sin credencial / sin verificar / verificada el …, vencida a los `MCP_READONLY_MAX_AGE_DAYS` días),
un formulario de alta o reemplazo, el botón «Verificar» y el botón «Quitar». Cuando
`server.readonly_probe_failed`, listar las `violations` como texto para el DBA.

#### Scopes nuevos para tokens de agente

`POST /api/v1/api-tokens` acepta ahora, además de `blueprints.read`, `databases.read` y
`schema_diff.read` (ya existían en el techo), estos cuatro: `environments.read`, `exports.read`,
`clones.read` y `catalogs.read`. El catálogo (`GET /authz/catalog`) los publica con
`agent_allowed: true`. Un selector de scopes en el alta de tokens tiene que ofrecer los siete.

#### Editar los scopes de un token (`PATCH /api-tokens/{id}`)

Antes, para darle más scopes a un token había que emitir otro y cambiar `GATEWAY_MCP_TOKEN`. Los
scopes viven en la fila (`api_tokens.scopes`) y no dentro del bearer, así que ahora se reemplazan
sin tocar el secreto: el agente los ve **desde su llamada siguiente** (no hay caché), con el mismo
bearer y sin abrir una terminal nueva.

`PATCH /api/v1/api-tokens/{id}` — `id` es el `id` numérico del listado, no el `token_id`.
Requiere `access.admin`, CSRF y step-up (todo método no seguro lo exige).

```json
{ "scopes": ["blueprints.read", "databases.read"] }
```

- **Solo `scopes`**, y la lista es el **reemplazo completo** (no suma ni resta). Cualquier otro
  campo (`name`, `project_id`, …) es 422.
- Respuesta 200: `ApiResponse[ApiTokenOut]` (el token con sus scopes nuevos, **sin secreto**).
- Se valida contra el **techo de agente**, igual que el alta.
- Auditoría: `api_token.update` con `scopes=[antes]->[después]`.

| Código | Status | Cuándo |
|---|---|---|
| `api_token.scope_not_allowed` | 422 | Scope desconocido o fuera del techo de agente (`public_context.allowed` lo lista cuando es lo segundo) |
| (validación de Pydantic) | 422 | Lista vacía (un token sin permisos se revoca) o campo extra |
| `api_token.not_found` | 404 | El token no existe |
| `api_token.already_revoked` | 409 | El token está revocado; no se edita (un token vencido sí) |
| `access.step_up_required` | 403 | Falta la confirmación de contraseña |

**Sugerencia de UI:** un botón de edición en las filas activas, junto a «Revocar», con el mismo
selector de scopes del alta. Avisar que ampliar un token ya repartido amplía lo que puede hacer
quien lo tenga.

### 24.2 Aprovisionar la credencial de solo lectura (un click)

Relacionado con 24.1: un solo click reemplaza el trío «el DBA crea la cuenta → `PUT` → `test-connection`».
Lo consume el bloque «Acceso de agentes (MCP)» del detalle de servidor.

#### Resumen para el frontend

| Cambio | Ruta | Capacidad |
|---|---|---|
| Nuevo | `POST /api/v1/servers/{server_id}/readonly-credential/provision` | `servers.admin` + step-up |

Aditivo: ningún contrato existente cambia. `PUT`/`DELETE .../readonly-credential` y
`test-connection?credential=readonly` siguen siendo el camino manual.

#### `POST /servers/{server_id}/readonly-credential/provision`

- **Sin cuerpo.** Ni usuario, ni host, ni contraseña, ni grants: los fija el servidor
  (`MCP_READONLY_ACCOUNT_USERNAME`, `MCP_READONLY_ACCOUNT_HOST` y una lista fija de privilegios).
  Un cuerpo enviado se ignora.
- **Respuesta 200:** `ApiResponse[ServerOut]` con `has_readonly_credential: true` y
  `readonly_verified_at` cargado. **Nunca** devuelve usuario ni contraseña.
- **Límite de tasa:** 3/minuto.
- **Idempotente solo para cuentas propias:** si la cuenta existe y este servidor ya guarda una
  credencial de solo lectura con ese usuario, rota la contraseña y re-aplica los grants; si existe y
  no es propia, 409 `readonly_account.already_exists` sin cambios. Aprovisionar de nuevo invalida la
  contraseña anterior (el gateway guarda la nueva, sin verificar, antes de que el motor cambie).
  `SHOW_ROUTINE` solo se otorga en MySQL >= 8.0.20.
- **Es una operación sobre el motor de un tercero** con la pseudo-root, y la credencial es **por
  servidor**: alcanza todas sus bases no internas, no solo las de un proyecto. La UI tiene que
  decirlo en la confirmación.
- Auditoría: `server.readonly_credential.provision` (intención `attempt` y resultado
  `success`/`error`), más los `set` y `verify` que ya existían. Sin usuario ni contraseña.
- No existe una tool equivalente en el MCP, a propósito.

Errores con `public_context.code`:

| Código | Status | Cuándo |
|---|---|---|
| `server.readonly_probe_failed` | 422 | La cuenta creada puede escribir; `public_context.violations` lista los motivos. El servidor queda **sin verificar** y la credencial registrada, para corregir grants y reintentar |
| `readonly_account.already_exists` | 409 | En el motor ya existe una cuenta con el usuario configurado y el gateway no la tiene guardada como credencial de solo lectura de este servidor. No se cambió nada: registrarla a mano (`PUT`) o cambiar `MCP_READONLY_ACCOUNT_USERNAME` |
| `readonly_account.has_roles` | 409 | La cuenta propia (MySQL/MariaDB) tiene roles u otros grants que el aprovisionamiento no puede quitar. No se cambió nada: revocarlos en el motor y reintentar |
| `readonly_provision.in_progress` | 409 | Ya hay un aprovisionamiento de este servidor en curso (lock por proceso). No se cambió nada: reintentar al terminar |
| `engine_user.protected_account` | 409 | El nombre configurado es una cuenta reservada, la pseudo-root, o un rol PostgreSQL con privilegios de administración |
| `access.step_up_required` | 403 | Falta la confirmación de contraseña |
| (sin código) | 404 | El servidor no existe |
| (sin código) | 4xx/5xx | Fallo del motor al crear la cuenta; los mismos mapeos que el resto de operaciones del motor. Es reintentable y deja `readonly_verified_at: null` |

Los tres códigos `readonly_account.*` / `readonly_provision.*` viven en `app/services/server_catalog.py`. La UI debería mapearlos a un mensaje claro para el operador (mismas indicaciones que la columna «Cuándo»).

**Sugerencia de UI:** un botón «Aprovisionar credencial» junto a «Registrar manualmente», con
confirmación que diga que se usará la pseudo-root y que el alcance es todo el servidor. Si la base
nueva no aparece en el MCP, la respuesta es repetir el aprovisionamiento (no cubre bases creadas
después).

### 24.3 Estado de acceso de agentes en `ManagedDatabaseOut`

Cierra el pendiente de `api-reference-v24.md` §10.1: el estado de acceso de
agentes (MCP) de cada base gestionada, que se escribía con `PUT /managed-databases/{id}/agent-access`
pero no se devolvía en ninguna respuesta.

#### Resumen para el frontend

| Cambio | Dónde | Capacidad |
|---|---|---|
| Dos campos nuevos en `ManagedDatabaseOut` | toda ruta que devuelve una base | `databases.read` |

Es un **cambio de forma del envelope**, aditivo: no hay rutas nuevas y ningún campo existente
cambia. Ver la advertencia de la cabecera de `api-reference-v24.md` sobre cambios de forma.

#### `ManagedDatabaseOut`: dos campos nuevos

```jsonc
{
  "agent_access_allowed": false,  // opt-in por base; default false
  "agent_access_blocked": false   // veto de emergencia; default false
}
```

- `agent_access_allowed`: el opt-in. Es el eje que **decide** el alcance: sin esto en `true`,
  ningún agente ve la base aunque su entorno los permita.
- `agent_access_blocked`: el veto de emergencia. **Gana** sobre `allowed` y no tiene override. Con
  `allowed: true, blocked: true` la base está cerrada; el campo crudo muestra igual los dos ejes.
- Los dos tienen default `false`: una base recién creada está cerrada a agentes y un cliente que
  no los conoce los ignora sin romperse. Un cliente nuevo debe leer un campo ausente como `false`
  (falla cerrado), nunca como «abierta».

Aparecen en: `POST`/`GET`/`PATCH` de `/managed-databases`, la lista, las bases de un usuario de
servidor, las bases de un modelo (`ModelDatabaseStatusOut`) y la respuesta del propio
`PUT .../agent-access`, que ahora confirma el estado resultante.

#### Quién los ve

Todo el que tenga `databases.read`. No hay filtro por `environments.write`: el incremento de
información es chico (el entorno ya expone `allows_agent_access` y `list_databases` del MCP
revela las abiertas). **Escribirlos** sigue siendo `environments.write` (`security_officer`) con
step-up.

#### Habilitar un entorno no abre ninguna base

`PATCH /environments/{id}` con `allows_agent_access: true` (que exige `?confirm_slug=`) solo
habilita la **posibilidad**. El default-deny es por base: cada una necesita su propio
`agent_access_allowed = true`. Una pantalla debe decirlo, porque `allows_agent_access` en `true`
con todas las bases en `false` significa que ningún agente ve nada.

#### Auditoría

`managed_database.agent_access_open` y `managed_database.agent_access_close` incluyen ahora en el
`detail` el estado de acceso previo (`antes`). La semántica no cambia: abrir sigue siendo
fail-closed.

#### Orden de despliegue

**Backend primero, frontend después.** El frontend define los campos con default `false`, así que
contra un backend viejo todo se lee como cerrado; el orden inverso mostraría «cerrada» sobre
bases que quizá estén abiertas.

### 24.4 Credencial de DATOS por base gestionada

Relacionado con 24.1 y 24.2 (credencial de solo lectura **por servidor**, que sirve a la
estructura). Esta es la hermana **por base**: una cuenta del motor con `SELECT` sobre UNA base y nada
más. Es la credencial que usan las tools de datos (`sample_rows`,
`distinct_values`, `count_rows`, `run_select`). La sonda `POST .../verify` comprueba que sirve.

#### Resumen para el frontend

| Cambio | Ruta | Capacidad |
|---|---|---|
| Nuevo | `POST /api/v1/managed-databases/{db_id}/data-credential/provision` | `servers.admin` + step-up |
| Nuevo | `DELETE /api/v1/managed-databases/{db_id}/data-credential` | `servers.admin` + step-up |
| Nuevo | `POST /api/v1/managed-databases/{db_id}/data-credential/verify` | `servers.admin` + step-up |

Aditivo. Nueva tabla `managed_database_data_credentials` (migración `e4a6c8f0b2d5`, vacía al
desplegar) y nuevas variables `MCP_DATA_ACCOUNT_PREFIX` (default `mcp_d_`) y
`MCP_DATA_CREDENTIAL_MAX_AGE_DAYS` (default `7`). No cambia ningún contrato
existente ni `ManagedDatabaseOut`.

#### `POST /managed-databases/{db_id}/data-credential/provision`

- **Sin cuerpo.** Usuario (`MCP_DATA_ACCOUNT_PREFIX` + id de la base, p. ej. `mcp_d_42`), host
  (`MCP_READONLY_ACCOUNT_HOST`), grants y contraseña los fija el servidor. Un cuerpo enviado se
  ignora.
- **Alcance POR BASE:** MySQL/MariaDB `GRANT SELECT ON <base>.*` y nada más (sin `SHOW VIEW`,
  `TRIGGER`, `EVENT` ni privilegios globales), con `MAX_USER_CONNECTIONS 6` (cinco consultas más la
  conexión del vigilante que cancela; una cuenta ya creada conserva su tope hasta regenerarla) y
  `MAX_STATEMENT_TIME 30` en MariaDB. PostgreSQL: rol `LOGIN` sin atributos de administración,
  `CONNECTION LIMIT 3`,
  `default_transaction_read_only = on`, `statement_timeout = '30s'`, `CONNECT` sobre la base y
  `USAGE` + `SELECT` por cada esquema de usuario. Una tabla creada después no queda cubierta hasta
  repetir el aprovisionamiento.
- **Respuesta 200:** `ApiResponse[DataCredentialOut]`: `managed_database_id`, `has_data_credential`,
  `verified_at`, `probed_at`, `probe_violations`, `probe_warnings`, `data_access_allowed`. **Nunca**
  devuelve usuario, contraseña ni el cifrado. Tras aprovisionar, `verified_at` es `null`: la sonda es
  un paso aparte (`POST .../verify`) y hasta que pase ninguna tool de datos puede usar la credencial.
- **Límite de tasa:** 3/minuto.
- **Elegibilidad:** la base tiene que estar `active` (existir en el motor) y no ser una base de
  sistema ni la base de metadatos del propio gateway.
- **Idempotente solo para cuentas propias:** si la cuenta existe y esta base ya guarda una
  credencial de datos con ese usuario, rota la contraseña y re-aplica el grant. Si existe y no es
  propia: 409 sin cambios. El gateway guarda la contraseña nueva (cifrada, sin verificar) **antes**
  de que el motor cambie, así que un reintento tras una falla a medias converge.
- Auditoría: `managed_database.data_credential.provision` (intención `attempt`, fail-closed, y
  resultado `success`/`error`). Sin usuario ni contraseña.
- No existe una tool equivalente en el MCP, a propósito.

#### `DELETE /managed-databases/{db_id}/data-credential`

Palanca de emergencia, **idempotente**: sin credencial no hace nada. Con credencial, primero la
des-verifica y cierra el opt-in (el corte es inmediato aunque el motor no conteste), después borra
la cuenta del motor (`DROP USER` / `DROP ROLE`) y, solo si eso funcionó, la fila. Si el motor falla,
responde el error del motor y la fila queda para que el reintento termine la revocación. Nunca borra
una cuenta que el gateway no creó. **Hacerlo antes de bajar la migración**: borrar la tabla pierde
la contraseña y deja huérfanas las cuentas del motor. Auditoría: `managed_database.data_credential.clear`.

#### `POST /managed-databases/{db_id}/data-credential/verify`

Sonda NEGATIVA. Sin cuerpo; 6/minuto; mismo lock por base que aprovisionar y revocar. Conecta con la
cuenta de DATOS de esa base (nunca la pseudo-root) y exige que el motor muestre SELECT sobre
**exactamente esa base**:

- MySQL/MariaDB (`SHOW GRANTS FOR CURRENT_USER()`): sin privilegios globales salvo `USAGE`, sin ningún
  privilegio que no sea `SELECT`, sin patrones con `_`/`%` sin escapar, sin grants sobre otras bases,
  sin `GRANT OPTION`/roles/`PROXY` ni líneas que la sonda no entienda. El nombre se compara
  distinguiendo mayúsculas salvo `lower_case_table_names` 1 o 2. Tablas `FEDERATED`/`CONNECT`/`SPIDER`
  en la base **bloquean**.
- PostgreSQL: sin atributos de administración, sin pertenencia a roles, cero privilegios de escritura
  sobre relaciones de usuario (también los de `PUBLIC`), `default_transaction_read_only = on` e intento
  real de escritura rechazado, `CONNECTION LIMIT` entre 1 y 3, `statement_timeout` fijado, sin `CONNECT`
  explícito a otras bases y sin `dblink`/`postgres_fdw`/`mysql_fdw`/`file_fdw` (bloquean).

**200** (`DataCredentialOut`): `verified_at` fijado y `probe_warnings` con lo no bloqueante
(`cross_schema_view_reference`, `definer_views_present`, `public_connect_other_databases`,
`create_on_database`, `create_on_schema_public`). **422** `managed_database.data_probe_failed`:
`verified_at` queda en `null` (se BORRA la verificación anterior), `public_context.reasons` lleva
códigos públicos cerrados y `public_context.violations` los motivos cortos; nunca texto de grants ni
mensaje del motor. Si la sonda no pudo correr (motor caído), también se des-verifica y responde el error
del motor. Auditoría: `managed_database.data_credential.verify`.

| `reasons` | Motivos cortos (`violations`) |
|---|---|
| `CREDENTIAL_TOO_BROAD` | `global_privilege:*`, `extra_privilege:*`, `wildcard_database_pattern`, `select_outside_database`, `grant_option`, `unrecognized_grant`, `role_attribute:*`, `member_of:*`, `member_of_role` |
| `WRITE_PRIVILEGE_PRESENT` | `privilege:*`, `all_privileges`, `table_write_privileges`, `default_transaction_read_only_off`, `write_attempt_succeeded` |
| `FEDERATED_TABLE_PRESENT` | `foreign_engine_table`, `foreign_access_extension` |
| `PROBE_NOT_GREEN` | `missing_select_on_database`, `connection_limit`, `statement_timeout_unset`, `engine_unsupported` |

La verificación vale `MCP_DATA_CREDENTIAL_MAX_AGE_DAYS` (7) días; pasado el plazo, las tools de datos
(entregas 5 y 6) la rechazan con `PROBE_NOT_GREEN`. Re-aprovisionar borra la verificación: la
contraseña nueva no está verificada.

#### Errores con `public_context.code`

| Código | Status | Cuándo |
|---|---|---|
| `data_credential.account_already_exists` | 409 | En el motor ya existe una cuenta con ese usuario y el gateway no la creó. No se cambió nada: configurar otro `MCP_DATA_ACCOUNT_PREFIX` |
| `data_credential.provision_in_progress` | 409 | Ya hay un aprovisionamiento o una revocación de esta base en curso (lock por proceso). No se cambió nada |
| `data_credential.database_not_eligible` | 409 | La base no está `active`, es de sistema o es la base de metadatos del gateway |
| `data_credential.missing` | 409 | `verify` sobre una base sin credencial de datos |
| `managed_database.data_probe_failed` | 422 | La sonda observó que la credencial no es SELECT-only sobre esa base (ver `reasons` y `violations`) |
| `engine_user.protected_account` | 409 | El usuario resultante es una cuenta reservada o un rol PostgreSQL con privilegios de administración |
| `access.step_up_required` | 403 | Falta la confirmación de contraseña |
| (sin código) | 404 | La base no existe |
| (sin código) | 4xx/5xx | Fallo del motor; reintentable |

Los códigos `data_credential.*` y `managed_database.data_probe_failed` viven en `app/services/data_credential_catalog.py`.

### 24.5 Scopes de datos de un token y opt-in de datos por base

Relacionado con 24.4 (credencial de DATOS por base). El catálogo de scopes tiene `data.read` y `data.query`, el emisor se
re-autentica al darlos, cada base tiene un opt-in de datos con segundo aprobador y hay dos kill
switches. Las tools de datos (24.6) exigen estos scopes.

#### Resumen para el frontend

| Cambio | Ruta | Capacidad |
|---|---|---|
| Nuevo | `GET /api/v1/managed-databases/{db_id}/data-credential` | `databases.read` |
| Nuevo | `POST /api/v1/managed-databases/{db_id}/data-access/request` | `data.read` en el entorno + step-up |
| Nuevo | `POST /api/v1/managed-databases/{db_id}/data-access/approve` | `data.read` en el entorno + step-up |
| Nuevo | `DELETE /api/v1/managed-databases/{db_id}/data-access` | `data.read` en el entorno + step-up |
| Cambia | `POST /api-tokens`, `PATCH /api-tokens/{id}` | aceptan `data.read` y `data.query` (ver abajo) |
| Cambia | `GET /authz/catalog` | dos filas nuevas; `agent_allowed` + `discloses` + `requires_step_up` a la vez |

`DataCredentialOut` gana `data_access_state` (`closed` / `pending` / `open`) y
`data_access_second_approver_required` (aditivo, con default). Variables nuevas:
`MCP_DATA_READ_ENABLED=false`, `MCP_DATA_QUERY_ENABLED=false`, `MCP_DATA_TOKEN_MAX_TTL_DAYS=0`
(0 = sin tope propio de vida para los tokens de datos).

#### Catálogo: `data.read` y `data.query`

- Divulgan, exigen step-up, son del techo de agente, **no mutan**, eje `environment`, solo `owner`
  (nunca `viewer`/`operator`) y **sensibles** (otorgarlas sueltas pide segundo aprobador): el conjunto
  sensible pasa de 11 a 13.
- Son la ÚNICA excepción cerrada a "un token nunca divulga" y a "step-up implica no agente"
  (`AGENT_DATA_EXCEPTIONS`, invariante 13 del catálogo). Mutar no tiene excepción.

#### Tokens (`/api-tokens`)

- Un scope de datos exige un **step-up fresco del emisor** al crear y al editar: 403
  `access.step_up_required` si la ventana venció. El token no tiene contraseña; la da quien lo emite.
- Rastro `api_token.data_scope_grant` con `record_intent` **fail-closed** (500 y no se otorga si el
  rastro no se persiste).
- Vida de un token con scope de datos: por defecto **sin tope propio** (`MCP_DATA_TOKEN_MAX_TTL_DAYS=0`),
  o sea que rige solo el tope general `MCP_TOKEN_MAX_TTL_DAYS` (90) y siguen sin existir tokens
  perpetuos. Con `MCP_DATA_TOKEN_MAX_TTL_DAYS` >= 1 el tope propio vuelve: 422
  `api_token.ttl_too_long` con `max_days`; en el PATCH cuenta la vida **restante** del token, y sin
  `expires_in_days` el default (90) lo excede y también da 422 (hay que pedir el TTL explícito).
- Con el kill switch de la capacidad apagado el scope se **guarda y se lista** pero el token no lo
  ejerce (inerte). `GET /api-tokens` lo muestra igual para que un PATCH no lo pierda.

#### Opt-in de datos por base

Sin cuerpo en los tres. Capa 2 sobre la base: hace falta `data.read` EN su entorno (owner).

- `POST .../data-access/request`: en `production` (y en bases sin entorno) el pedido queda `pending`;
  en los demás entornos abre en el acto (`open`). Exige credencial de datos (409
  `data_credential.missing`). 409 `data_access.already_open` si ya estaba abierto. Audita
  `managed_database.data_access_request` / `_open` con `record_intent` fail-closed.
- `POST .../data-access/approve`: lo aprueba **otro** owner. 403
  `data_access.self_approval_forbidden` para el solicitante; 409 `data_access.not_pending` sin pedido.
- `DELETE .../data-access`: cierra o cancela el pedido. Inmediato e idempotente; no toca el motor ni la
  credencial (borrarla es `DELETE .../data-credential`). Audita `managed_database.data_access_close`.
- Abrir NO alcanza para leer: el gate exige además credencial con sonda verde reciente y el kill
  switch encendido.

#### Errores con `public_context.code`

| Código | Status | Cuándo |
|---|---|---|
| `data_access.self_approval_forbidden` | 403 | El solicitante intentó aprobar su propio pedido |
| `data_access.not_pending` | 409 | `approve` sin un pedido pendiente |
| `data_access.already_open` | 409 | `request` con el acceso ya abierto |
| `data_access.identity_required` | 403 | El actor no es un usuario del gateway (fail-closed) |
| `data_credential.missing` | 409 | La base no tiene credencial de datos |
| `api_token.ttl_too_long` | 422 | Token que excede `MCP_TOKEN_MAX_TTL_DAYS`, o con scope de datos que excede `MCP_DATA_TOKEN_MAX_TTL_DAYS` (solo si es >= 1) |
| `access.step_up_required` | 403 | Falta la confirmación de contraseña |

Los de `data_access.*` viven en `app/services/data_credential_catalog.py`.

### 24.6 Tool `search_schema`

Relacionado con `api-reference-v23.md` §9 (transporte y tools del MCP). Agrega una tool de solo
lectura y publica `annotations` en `tools/list`. No cambia ninguna ruta REST ni el frontend.

#### Resumen

| Cambio | Dónde | Scope |
|---|---|---|
| Tool nueva `search_schema` | `tools/list` / `tools/call` | `databases.read` |
| `annotations` en cada tool de `tools/list` | `tools/list` | — |
| Variable `MCP_SEARCH_MAX_TABLES` (default 200) | entorno del proceso | — |
| Warnings `mcp.warn.search_scan_truncated`, `mcp.warn.search_results_truncated` | respuesta | — |

Aditivo. Un token que ya tiene `databases.read` pasa a ver la tool nueva sin reemitirse.

#### Entrada

```jsonc
{ "database_id": 12,          // el que devuelve list_databases
  "query": "fecha nacimiento", // 2..100 caracteres tras recortar espacios
  "kinds": ["table", "view", "column"], // opcional; default esos tres; también routine, trigger
  "limit": 20 }                // opcional; 1..50, default 20
```

Schema cerrado: cualquier otra clave se rechaza. `query` vacía, solo espacios, sin letras ni
dígitos, fuera de rango, o un `kinds`/`limit` inválido → error de tool `mcp.invalid_argument`,
antes de abrir ninguna conexión.

#### Salida (`structuredContent`)

Mismo envelope que `get_schema` (`notice`, `untrusted_fields`, `clipped_fields`, `warnings`,
`database`). `data`:

| Campo | Significado |
|---|---|
| `query_tokens` | La consulta ya normalizada (sin acentos, sin palabras vacías) |
| `hits[]` | Resultados ordenados por `score` descendente, desempate estable |
| `count` / `total_matches` | Devueltos / coincidencias totales (puede haber más que `limit`) |
| `truncated` | `true` si CUALQUIER motivo de `truncated_reasons` recortó la búsqueda |
| `truncated_reasons` | `results_limit`, `scan_cap` (más tablas que `MCP_SEARCH_MAX_TABLES`), `time_budget` |
| `scanned_tables` / `total_tables` | Tablas cuyo detalle se leyó / tablas de la base |
| `searched_kinds` | Los tipos efectivamente buscados |
| `next_step` | Cómo seguir con `get_schema` |

Cada elemento de `hits[]`: `kind`, `name`, `table`, `column`, `data_type`, `key_flags[]`
(`primary_key` / `foreign_key` / `unique`), `references` (`tabla.columna` de una FK), `comment`
(texto de terceros, máx. 200 caracteres), `score`, `matched_on` (`name`, `name_and_table`,
`comment`, `name_and_comment`), `matched_tokens[]` y `get_schema_object` (`{kind, name}` para
`get_schema.objects`). Los nombres de tablas y vistas se buscan siempre; las columnas y los
comentarios de tabla solo en las tablas leídas (ver `scanned_tables`).

#### Errores

Idénticos a `get_schema` (mismo gate de ocho ejes, misma credencial de solo lectura): una base que
el token no alcanza responde `mcp.not_found`, igual que una inexistente; sin credencial vigente,
`mcp.readonly_credential_missing`; sesión agotada, `mcp.session_timeout`.

#### Privilegios

**No hacen falta privilegios nuevos.** La credencial de solo lectura ya tiene `SELECT`,
`SHOW VIEW`, `TRIGGER` y `EVENT` por base: alcanza para leer nombres, columnas y comentarios del
catálogo, que es todo lo que la tool consulta. Ver
[`docs/features/mcp-para-colaboradores.md`](features/mcp-para-colaboradores.md) (A.6).

### 24.7 Tool `draft_query`

Relacionado con `api-reference-v23.md` §9 (transporte y tools del MCP). Agrega una tool que
**recibe un texto SQL y devuelve solo texto**. No cambia ninguna ruta REST ni el frontend, no
agrega variables de entorno ni migraciones, y no abre ninguna conexión a un motor.

#### Resumen

| Cambio | Dónde | Scope |
|---|---|---|
| Tool nueva `draft_query` | `tools/list` / `tools/call` | `databases.read` |
| Códigos públicos de razón y de advertencia del sobre | respuesta | — |
| Invariante de v23 §9.5 (anterior): "no acepta SQL" pasa a "no **ejecuta** SQL" | documentación | — |

Aditivo. Un token que ya tiene `databases.read` pasa a ver la tool nueva sin reemitirse.

#### Entrada

```jsonc
{ "database_id": 12,   // el que devuelve list_databases
  "sql": "UPDATE t SET a = 1" } // cualquier texto; el schema no fija un máximo a propósito
```

Schema cerrado: cualquier otra clave se rechaza (error de protocolo). Un `database_id` que no sea
entero o un `sql` que no sea cadena → error de tool `MALFORMED_REQUEST`. **Todo texto** —vacío,
ilegible, enorme— devuelve un sobre; un error de protocolo por un SQL mal escrito invitaría a
reintentar en loop.

#### Salida (`structuredContent`)

Exactamente estas cinco claves, siempre:

```jsonc
{ "classification": "write",
  "reasons": ["NOT_SELECT"],
  "warnings": ["WRITE_NOT_EXECUTED"],
  "query_text": "UPDATE t SET a = 1",
  "touches_engine": false }
```

| Campo | Significado |
|---|---|
| `classification` | `read`, `write`, `ddl`, `blocked` o `invalid` (conjunto cerrado) |
| `reasons[]` | Códigos públicos cerrados que explican por qué NO es una lectura aceptable. Vacío si `read` |
| `warnings[]` | `WRITE_NOT_EXECUTED` (escritura), `DDL_NOT_EXECUTED` (DDL). Una escritura o un DDL **siempre** los traen |
| `query_text` | El texto canónico si es una lectura aceptada; si no, el texto recibido sin caracteres de control y recortado a 16 KiB |
| `touches_engine` | **Siempre `false`**: redactar nunca ejecuta, ni siquiera una lectura |

`classification`: `read` es una lectura que el validador acepta; `write` y `ddl` salen de la
política de la consola; `blocked` es lo que la consola prohíbe incluso confirmando **o** una
lectura que el perfil de agente rechaza (con sus `reasons`); `invalid` es un texto vacío, ilegible,
enorme o con varias sentencias.

##### Códigos de `reasons`

`PARSE_FAILED`, `MULTIPLE_STATEMENTS`, `NOT_SELECT`, `DML_IN_CTE`, `DML_IN_SUBQUERY`,
`SELECT_INTO`, `LOCKING_READ`, `FUNCTION_NOT_ALLOWED`, `VARIABLE_ASSIGNMENT`,
`EXECUTABLE_COMMENT`, `COMMENT_NOT_ALLOWED`, `SYSTEM_SCHEMA`, `CROSS_DATABASE`, `UNSUPPORTED_NODE`,
`LIMIT_NOT_BOUNDABLE`, `OFFSET_TOO_HIGH`, `SQL_TOO_LARGE`.

El vocabulario público completo (que incluye códigos de capas posteriores, como `DATA_DISABLED` o
`QUERY_TIMEOUT`) vive en `app/services/mcp_catalog.py`; los códigos internos del validador nunca
salen.

Lo que sqlglot 30.11 no parsea se rechaza como `PARSE_FAILED`, con el código más específico
sumado cuando se lee de los tokens: `SELECT … INTO OUTFILE` (`SELECT_INTO`) y un DML dentro de un
subselect (`DML_IN_SUBQUERY`).

#### Qué decide el validador

La decisión sale del **AST** (nunca de palabras sueltas): una lista blanca de tipos de nodo, de
argumentos de cada nodo y de funciones, sobre el árbol completo. Un texto se acepta solo si:

- es exactamente **una** sentencia (`SELECT`, `UNION`/`INTERSECT`/`EXCEPT`, con `WITH` opcional);
- no tiene **ningún comentario** (los `/*!…*/` y `/*M!…*/`, que el motor ejecuta, se rechazan sobre
  el texto crudo, incluso dentro de un literal);
- todas sus funciones están en la lista blanca (cerrada; las desconocidas y las calificadas se
  rechazan);
- no asigna ni lee variables, no toma locks (`FOR UPDATE`/`FOR SHARE`/`LOCK IN SHARE MODE`) ni
  materializa el resultado (`INTO`);
- solo nombra la base fijada por `database_id` (otra base, un nombre de tres partes en PostgreSQL y
  los esquemas del sistema se rechazan);
- su `LIMIT`/`OFFSET` son literales enteros (un `OFFSET` mayor a 10 000 se rechaza).

El render canónico del árbol vuelve a pasar por el pipeline completo y tiene que dar el mismo
conjunto de nodos; si no, se rechaza (`UNSUPPORTED_NODE`).

**Falsos positivos deliberados:** en MySQL/MariaDB un literal con barra invertida o con salto de
línea se rechaza (`PARSE_FAILED`) porque el mismo texto significaría otra cosa bajo
`NO_BACKSLASH_ESCAPES`; `a DIV 2` se rechaza (`UNSUPPORTED_NODE`) porque sqlglot lo renderiza como
otro árbol. Un `LIMIT ALL` de PostgreSQL se acepta (el parser no deja rastro y la consulta se acota
igual con un `LIMIT` empujado por el gateway).

#### Lo que el validador NO detecta

El parseo no ve: vistas con `DEFINER`, tablas `FEDERATED`/`CONNECT`/`SPIDER` (y extensiones tipo
`dblink`) que leen otras bases, ni las diferencias entre cómo sqlglot y el motor leen el mismo
texto. **La barrera real es la cuenta del motor**: `SELECT` sobre una sola base, transacción
`READ ONLY` y timeout del lado del servidor. El validador es defensa en profundidad, y esta tool
no ejecuta nada, así que hoy esos residuales no tienen un camino al motor.

#### Errores

El gate de la base es el de las demás tools: una base que el token no alcanza responde
`mcp.not_found`; sin scope, `mcp.scope_denied`; sin credencial de solo lectura vigente en el
servidor, `mcp.readonly_credential_missing` (aunque `draft_query` no la use para conectar).

### 24.8 Tools `sample_rows`, `distinct_values`, `count_rows`

Relacionado con 24.5. **No hay rutas
HTTP nuevas**: son tools del endpoint `/mcp/`. Existen solo con `MCP_DATA_READ_ENABLED=true`; scope
`data.read`. Contrato para el frontend: ninguno (la SPA ya gestiona scopes y opt-in, ver 24.5).

#### Tools

| Tool | Argumentos | Sentencia que arma el gateway |
|---|---|---|
| `sample_rows` | `database_id`, `table`, `columns?`, `limit?` | `SELECT <cols|*> FROM <t> LIMIT n+1` |
| `distinct_values` | `database_id`, `table`, `column`, `limit?` | `SELECT DISTINCT <c> FROM <t> ORDER BY <c> LIMIT n+1` |
| `count_rows` | `database_id`, `table` | `SELECT COUNT(*) FROM <t> LIMIT 2` |

Identificadores cuoteados, validados contra el catálogo y pasados por `validate_agent_select`.

#### Respuesta

`{notice, data:{columns, rows[arreglos]}, row_count, truncated, truncation_reason (row_cap|byte_budget|null),
clipped_cells, executed_sql, human_query, duration_ms, warnings, untrusted_fields, untrusted_content,
source, database}`. `data.columns` y `data.rows` son contenido no confiable de terceros.

#### Topes y códigos

Filas 100/200/500; timeout 20 s/30 s; respuesta 128 KiB (recorta filas); celda 512. Códigos de error
cerrados: `DATA_DISABLED`, `PROBE_NOT_GREEN`, `UNKNOWN_IDENTIFIER`, `MALFORMED_REQUEST`,
`QUERY_TIMEOUT`, `QUERY_FAILED`, `AUDIT_UNAVAILABLE`, `DATA_ACCOUNT_BUSY` (429: la cuenta de datos
llegó a su tope de conexiones; la credencial está bien y se reintenta en unos segundos); más los de
autorización (`mcp.scope_denied`, `mcp.not_found`, `mcp.environment_denies_agents`…). Advertencia:
`LIMIT_TOO_HIGH`.

Variables nuevas: `MCP_QUERY_DEFAULT_ROWS`, `MCP_QUERY_MAX_ROWS`, `MCP_QUERY_TIMEOUT_MS`,
`MCP_QUERY_MAX_OFFSET`, `MCP_QUERY_MAX_SQL_BYTES`, `MCP_DATA_MAX_RESULT_BYTES`.

### 24.9 Tool `run_select`

Relacionado con 24.8. **No hay
rutas HTTP nuevas**: es una tool del endpoint `/mcp/`. Existe solo con `MCP_DATA_QUERY_ENABLED=true`
(apagado por default; independiente de `MCP_DATA_READ_ENABLED`); scope `data.query`. Contrato para el
frontend: ninguno (scopes y opt-in ya están en 24.5).

#### Invariante (reemplaza al de `api-reference-v23.md` §9.5)

**El MCP ejecuta únicamente `SELECT` únicos validados, dentro de una transacción `READ ONLY` y bajo una
credencial por base con `SELECT` solamente.** Antes: "no acepta SQL del agente" (hasta `search_schema`) y "no EJECUTA
SQL del agente" (`draft_query`). Lo que no es un `SELECT` aceptable no llega al motor.

#### Tool

| Tool | Argumentos | Qué hace |
|---|---|---|
| `run_select` | `database_id`, `sql`, `limit?` | Valida el `sql` con el validador compartido; si es una lectura aceptada la ejecuta con la credencial de datos de la base; si no, devuelve el sobre del borrador |

Mismo gate que 24.8 (kill switch → scope → base y entorno → opt-in → credencial con sonda fresca), mismo
servicio y mismos topes (filas 100/200/500, timeout 20 s/30 s, respuesta 128 KiB, celda 512). `limit`
solo puede igualar o bajar el máximo; por encima se recorta y `warnings` trae `LIMIT_TOO_HIGH`.

#### Respuestas

- **Lectura aceptada**: el sobre de 24.8 (`data.rows` como arreglos, `untrusted_fields`, `truncated`,
  `human_query`, `executed_sql`, …). `executed_sql` es el render de `sqlglot` del árbol verificado, no el
  texto del agente: un `LIMIT` propio literal `<=` tope se ejecuta tal cual; uno mayor o ausente se
  reemplaza por tope + 1.
- **Todo lo demás** (escritura, DDL, bloqueado, ilegible): el sobre del borrador, **sin filas, sin
  conexión y sin intención de auditoría de ejecución**:
  `{classification: read|write|ddl|blocked|invalid, reasons[], warnings[], query_text, touches_engine: false}`.
  Un rechazo no es un error de protocolo. Códigos de `reasons`: `PARSE_FAILED`, `MULTIPLE_STATEMENTS`,
  `NOT_SELECT`, `DML_IN_CTE`, `DML_IN_SUBQUERY`, `SELECT_INTO`, `LOCKING_READ`, `FUNCTION_NOT_ALLOWED`,
  `VARIABLE_ASSIGNMENT`, `EXECUTABLE_COMMENT`, `COMMENT_NOT_ALLOWED`, `SYSTEM_SCHEMA`, `CROSS_DATABASE`,
  `UNSUPPORTED_NODE`, `OFFSET_TOO_HIGH` (literal `> MCP_QUERY_MAX_OFFSET`), `LIMIT_NOT_BOUNDABLE`,
  `SQL_TOO_LARGE`. Advertencias: `WRITE_NOT_EXECUTED`, `DDL_NOT_EXECUTED`.
- **Errores de tool** (códigos cerrados, mensaje fijo, nunca texto del motor): `DATA_DISABLED`,
  `PROBE_NOT_GREEN`, `QUERY_TIMEOUT`, `QUERY_FAILED`, `AUDIT_UNAVAILABLE`, `DATA_ACCOUNT_BUSY`
  (cuenta de datos en su tope de conexiones, 429), `MALFORMED_REQUEST` (solo
  argumentos ausentes o mal tipados) y los de autorización (`mcp.scope_denied`, `mcp.not_found`,
  `mcp.environment_denies_agents`…). El gate corre ANTES del validador: con el gate cerrado ni siquiera
  se clasifica el texto.

La fila de auditoría del despacho (`mcp.run_select`) marca `touched_engine=false` cuando la respuesta fue
un borrador (`touches_engine: false`), y `true` cuando hubo ejecución; la ejecución se audita aparte
(`mcp.agent_query`, intención antes de conectar).

#### Riesgo residual aceptado

La barrera real es la cuenta del motor (`SELECT` sobre una sola base, transacción `READ ONLY`, timeout
del lado del servidor); el validador es defensa en profundidad. Quedan: (1) **inyección de prompt por los
datos de las filas** (mitigada por el sobre, contenida porque ninguna tool escribe); (2) lo que el parseo
no ve — vistas con `DEFINER`, tablas `FEDERATED`/`CONNECT`/`SPIDER`/FDW y diferenciales entre `sqlglot` y
el motor; (3) **los datos personales no se filtran**: la lista de denegación por PII quedó diferida
(enmienda S14; `PII_BLOCKED` reservado), así que lo legible lo fija el `GRANT`. Costo deliberado: en
MySQL/MariaDB un literal con barra invertida se rechaza (`PARSE_FAILED`).

Variable nueva: `MCP_DATA_QUERY_ENABLED` (ya listada en 24.8 junto a los topes).

### 24.10 `get_definition`, `get_table_stats` y lectura de cuerpos de rutinas

Relacionado con 24.9. Las tools
(`get_definition`, `get_table_stats`) son del endpoint `/mcp/`. **La única ruta HTTP nueva** es
`PUT /servers/{id}/readonly-credential/routine-bodies` (S6, ver la sección de `routine-bodies` más abajo),
y `ServerOut` gana `readonly_proc_grant`. La SPA (S8) agrega `data.definitions` al selector de scopes
de token y el control de esa bandera en el panel de la credencial de solo lectura.

#### Invariante

**El MCP nunca ejecuta un procedimiento, una función, un trigger, un evento ni una vista, y ninguna de
estas tools acepta SQL.** Reciben identificadores (`database_id`, `kind`, `name`), leen el código con
`SHOW CREATE` o `pg_get_*` y lo entregan como texto. Lo que rige para `run_select` (24.9) no cambia:
solo `run_select` ejecuta, y solo `SELECT` validados.

#### Scope `data.definitions` y kill switch

| Aspecto | Comportamiento implementado |
|---|---|
| Capacidad | `data.definitions`, tercer miembro de la excepción cerrada `AGENT_DATA_EXCEPTIONS` (junto a `data.read` y `data.query`; invariante 13 del catálogo). Nunca se agrega una capacidad que mute |
| Quién la tiene | Solo `owner` (no `operator`: el invariante 7b prohíbe que `operator` divulgue) |
| Sensible | Sí: segundo aprobador al otorgarla suelta (el conjunto sensible pasó de 13 a 14) |
| Step-up | Lo cumple el **emisor humano** al crear o editar el token (`api_token_controller._validate_scopes`); el token no tiene contraseña que reconfirmar |
| Rastro | `api_token.data_scope_grant` con `record_intent` fail-closed, igual que los otros scopes de datos |
| TTL del token | Rige el tope propio `MCP_DATA_TOKEN_MAX_TTL_DAYS` (default `0` = desactivado, el token vive hasta `MCP_TOKEN_MAX_TTL_DAYS`; con `>= 1` se acota, error `422` con `max_days`). Se aplica porque el scope pertenece a `AGENT_DATA_EXCEPTIONS` |
| Agregar a un token ya emitido | Solo quien lo emitió o quien ya tiene hoy el permiso (`_require_editor_may_add_data_scopes`) |
| Kill switch | `MCP_SCHEMA_DEFINITIONS_ENABLED`, **default `false`**, independiente de `MCP_DATA_READ_ENABLED` y `MCP_DATA_QUERY_ENABLED` |

Con el kill switch apagado: el scope queda inerte (`parse_scopes` lo descarta), **`get_definition` no
aparece en `tools/list`** y, si igual se la invoca (el registro se arma al importar y el handler vuelve a
mirar el switch en cada llamada), responde `403` con código `mcp.definitions_disabled`. Es el primer paso:
no valida argumentos ni lee el inventario, así que no se distingue por tiempo ni por efectos de una tool
inexistente. Cambiar el switch exige reiniciar el proceso para que `tools/list` cambie.

#### `get_definition`

| Tool | Argumentos | Qué hace |
|---|---|---|
| `get_definition` | `database_id`, `objects: [{kind, name, routine_kind?}]` | Devuelve el texto de vistas, triggers, eventos y rutinas pedidos por nombre |

`kind` admite `view`, `trigger`, `event`, `routine`. `routine_kind` (`PROCEDURE` | `FUNCTION`) solo aplica a
`routine` y desambigua dos rutinas con el mismo nombre. Cada elemento admite únicamente esas tres claves.

**Topes**

- **3 objetos por llamada** (`maxItems` del schema publicado; se vuelve a exigir en el handler y en
  `read_definitions`). Más es `422 mcp.invalid_argument`. Los duplicados se colapsan.
- `name` de 1 a 128 caracteres. No se rechaza por sus caracteres: un nombre con comilla o prefijo de otra
  base no está en el índice y vuelve en `missing`, sin que se emita SQL con ese texto.
- **64 KiB por cuerpo**, medidos sobre su codificación JSON y **después de redactar**. Un cuerpo mayor
  **se rechaza, no se trunca**: vuelve `body_available=false`, `unavailable_reason=too_large` y su
  `size_bytes`. El dispatcher cuenta cada resultado dos veces (texto y `structuredContent`):
  3 x 64 KiB x 2 = 384 KiB entran en su presupuesto de 512 KiB.
- Lo que no está en el índice de la base vuelve en `missing[]` (`{kind, name, routine_kind}`) y **no se
  consulta al motor**. Nunca hay un éxito vacío: o hay objetos, o hay `missing`, o la llamada falló con su
  código.

**Orden del gate** (cada paso corta el siguiente)

1. Kill switch (`mcp.definitions_disabled`, 403).
2. Argumentos (sin conectar; `422 mcp.invalid_argument`).
3. Scope `data.definitions` del token (`mcp.scope_denied`).
4. Proyecto del token (`mcp.not_found`).
5. Credencial de **estructura** (la misma de `list_objects`) fresca, y entorno, opt-in y veto de la base.
   **No pasa por `_data_gate`**: no exige la credencial de datos por base ni su opt-in; el control de acceso
   es el scope más el kill switch.
6. Índice de objetos: lo ausente va a `missing`.
7. Auditoría de intención `mcp.get_definition` con `record_intent` **fail-closed**: si no se puede
   escribir, no se lee ningún cuerpo y el error es `AUDIT_UNAVAILABLE` (503).
8. Lectura por objeto, redacción, medición y huella. Al final, una fila de resultado con conteos por razón.

**Motivos de no disponibilidad** (`unavailable_reason`, vocabulario cerrado; `not_found` no existe a
propósito, eso es `missing`): `insufficient_privilege`, `engine_unsupported`, `scope_disabled`, `flag_off`,
`too_large`. Un cuerpo NULL, vacío o en blanco, o que el saneado deja vacío, es `insufficient_privilege`:
nunca `body_available=true` sin texto (el modelo lo valida al construirse).

**Códigos de error**: `mcp.definitions_disabled` (403), `mcp.invalid_argument` (422), `AUDIT_UNAVAILABLE`
(503), `mcp.scope_denied`, `mcp.not_found`, `mcp.environment_denies_agents` y los demás del gate de
estructura; un timeout de la sesión es el código de sesión del MCP (504, "pedí menos objetos").

##### Respuesta

Envelope habitual (`notice`, `data`, `warnings`, `untrusted_fields`, `untrusted_content`, `source`,
`database`). `data` es `{objects[], missing[]}`; cada objeto trae:

`kind`, `name`, `routine_kind`, `identity_arguments` (PostgreSQL: una entrada por sobrecarga),
`body_available`, `unavailable_reason`, `body`, `size_bytes`, `body_fingerprint`, `security`
(`definer` | `invoker`, **solo el modo; la cuenta del DEFINER nunca sale**), `check_option` (vistas),
`trigger {table, timing, events[]}`, `event {schedule, status}`, `redactions[]` y `flagged[]`
(`{category, count}`, jamás el valor).

`body_fingerprint` es SHA-256 del cuerpo redactado y normalizado con la misma `normalize_body` que usa el
diff (sin DEFINER, espacios colapsados, sin `;` final). Advertencias: las de `list_objects` (cuarentena,
estructura no atómica, etc.) más `mcp.warn.bodies_redacted` si se enmascaró algo.

**Rutinas en `missing[]` (MySQL/MariaDB).** Sin privilegio de rutina, `information_schema.ROUTINES` devuelve
cero filas **sin error**, así que una rutina que existe puede volver en `missing[]`. Si alguna rutina pedida
queda ahí en MySQL o MariaDB, la respuesta suma UN aviso `mcp.warn.routine_not_found_or_not_visible`
(vocabulario cerrado, uno por llamada aunque falten varias): la rutina no se encontró o la cuenta no tiene
privilegio para verla; si existe, regenerar la credencial de solo lectura (MariaDB >= 11.3) o habilitar la
lectura de cuerpos de rutinas (motores más antiguos). La forma de los ítems de `missing[]` no cambia. Vistas,
triggers y events conservan el `missing` llano (su ausencia sí es confiable). PostgreSQL no emite el aviso.

##### Contenido no confiable y redacción

Cada `body` va en `untrusted_fields` y bajo el `notice`: es código de un tercero y puede contener texto
que parezca una instrucción ("ignorá lo anterior…"). El control real es que ninguna tool escribe. **La
redacción de credenciales es best effort y NO es una frontera de seguridad.** Enmascara: `IDENTIFIED BY`,
URIs con usuario y clave, asignaciones con nombre de secreto, JWT, llaves de AWS, tokens de GitHub y Slack,
API keys, literales largos tipo token y bloques PEM. Emails y hosts internos solo se **cuentan**
(`flagged`), no se enmascaran. Lo que no matchea un patrón sale en claro: la frontera es el scope.

##### Auditoría

`mcp.get_definition` registra la **intención antes de leer** (fail-closed) con `token_id` y la lista
`tipo:nombre` de los objetos que **sí están en el índice** (tope de 2048 bytes). Nunca un cuerpo ni los
nombres ausentes (son texto del agente). La fila de resultado lleva solo conteos: disponibles, ausentes,
redactados y por motivo.

#### `list_objects`: `body_available` ahora dice la verdad

Antes `body_available` era siempre `false`. Ahora se calcula con el scope del token y el motor/versión, sin
leer ningún cuerpo ni emitir `SHOW CREATE`:

- Sin `data.definitions` (o con el kill switch apagado): `body_available=false`,
  `unavailable_reason=scope_disabled` en vistas, rutinas, triggers y eventos.
- Con el scope: vistas, triggers y eventos salen disponibles; las rutinas, según la matriz de abajo
  (`flag_off` o `engine_unsupported`). "Disponible" no promete un cuerpo: lo confirma `get_definition` por
  objeto.
- Tablas y secuencias no llevan los campos (`null`).
- Aviso `mcp.warn.routines_not_visible` en MySQL/MariaDB cuando las rutinas forman parte del listado (`kinds`
  omitido o que incluye `routine`) y se cumple una de dos: el motor/versión/bandera puede ocultarlas, **o** el
  índice listó **cero** rutinas ("puede que no existan o que esta cuenta no las vea"). Que no aparezcan no
  prueba que no existan; nunca se afirma certeza. El cero se mide sobre el índice completo, no sobre lo
  filtrado por `name_prefix`. PostgreSQL no cambia.
- **Nuevo tipo `event`** en `kinds` (MySQL y MariaDB; en PostgreSQL no existe y la lista es vacía).
  `get_schema` conserva sus tipos y no lee eventos ni cuerpos.
- Listar nombres de rutinas y triggers ya no hace un `SHOW CREATE` por objeto.

#### `get_table_stats`

| Tool | Argumentos | Qué hace |
|---|---|---|
| `get_table_stats` | `database_id`, `tables: [nombre]` | Estadísticas de almacenamiento de tablas pedidas por nombre |

Scope **`databases.read`**, credencial de estructura, **sin kill switch propio** y sin SQL ni lectura de
filas. Lee `information_schema.TABLES` (MySQL/MariaDB) o `pg_class` (PostgreSQL) con consultas
parametrizadas, solo para nombres que están en el índice y acotadas a la base fijada. Hasta
`MCP_MAX_OBJECTS_PER_CALL` tablas por llamada (más es `413 mcp.too_many_objects`, antes de conectar);
`missing[]` para lo que no es una tabla del índice (incluida la contabilidad `_gw_v_`/`_gw_stg_`).

Cada tabla trae `name`, `engine`, `collation`, `data_bytes`, `index_bytes`, `created_at`, `updated_at`.
En PostgreSQL `engine`, `collation` y las fechas son `null` (el motor no los guarda).

**`row_estimate` y `auto_increment` solo salen si el token también tiene `data.read` con
`MCP_DATA_READ_ENABLED` encendido**, porque aproximan `count_rows` y `AUTO_INCREMENT` delata cuántas filas
se insertaron. Para los demás **no existen como claves** (un `null` sería ambiguo con "el motor no lo
sabe"), y el adapter ni siquiera selecciona esas columnas. La respuesta lo dice con
`row_estimates_included` y `row_estimates_omitted_reason: "requires_data_read_scope"`. No exige la
credencial de datos por base: el estimado se lee del catálogo con la de estructura.

#### Credencial de estructura y matriz de motor/versión

El código de una **rutina** necesita un privilegio que depende del motor. El aprovisionamiento de la
credencial de estructura agrega, **solo en MariaDB 11.3 o superior** (versión leída en runtime), el
privilegio **`SHOW CREATE ROUTINE` a nivel de base** (MDEV-29167); la sonda lo acepta solo en MariaDB y solo
a nivel base (global o por tabla sigue siendo violación; `SELECT` sobre `mysql.*` sigue prohibido).

| Motor / versión | Cuerpo de rutinas | `unavailable_reason` si falta |
|---|---|---|
| MariaDB 11.3+ | Con `SHOW CREATE ROUTINE` por base (otorgado al aprovisionar) | `insufficient_privilege` |
| MariaDB < 11.3 | Solo con `SELECT ON mysql.proc` (alcance servidor, bandera `readonly_proc_grant`, ver S6) | `flag_off` |
| MySQL 8.0.20+ | `SHOW_ROUTINE` global (ya existente) | `insufficient_privilege` |
| MySQL 8.0.0 a 8.0.19 | No hay grant posible | `engine_unsupported` |
| MySQL 5.7 | Solo con `SELECT ON mysql.proc` (bandera `readonly_proc_grant`, S6) | `flag_off` |
| PostgreSQL | `pg_get_functiondef` y `pg_get_viewdef`, una lectura por sobrecarga; no tiene eventos | `engine_unsupported` (eventos) |

Vistas, triggers y eventos se leen con los grants por base ya existentes (`SELECT`, `SHOW VIEW`,
`TRIGGER`, `EVENT`).

**Verificado en vivo solo en MariaDB 11.8.3** (staging): al regenerar la credencial de estructura, las
rutinas pasaron a verse en `list_objects` y sus cuerpos se leyeron con `get_definition`. No se probó en
otras versiones 11.3 a 11.7 ni se inspeccionó la salida de `SHOW GRANTS` posterior. El literal vive en una
sola constante (`MARIADB_SHOW_CREATE_ROUTINE_PRIVILEGE`, `readonly_probe.py`). Una credencial ya
aprovisionada no recibe el privilegio hasta que se regenera. Los tests de esta entrega usan un motor falso.

#### `PUT /servers/{id}/readonly-credential/routine-bodies` (S6)

Enciende o apaga `SELECT ON mysql.proc` para la credencial de solo lectura del MCP en **MariaDB < 11.3 y
MySQL 5.7**, el único camino para leer el código de las rutinas ahí. **Es SERVER-WIDE**: `mysql.proc` no se
puede acotar a una base, así que la credencial lee las rutinas de TODAS las bases del servidor, también las
que están fuera del proyecto o excluidas; solo el filtrado del gateway lo contiene.

| Aspecto | Comportamiento |
|---|---|
| Autorización | `servers.admin` (la dependencia `ServersAdmin`) con **step-up** (lo exige a todo `PUT`; sin él, `403 access.step_up_required`) |
| Límite | `3/minute` por cliente (`429` al excederlo) |
| Cuerpo | `{"enabled": bool, "acknowledgement": str \| null}`; `extra="forbid"` |
| Acknowledgement | Al **habilitar** (`enabled=true`) tiene que ser igual, carácter por carácter y sin `strip`, a `READONLY_PROC_ACK_TEXT` (`app/services/server_catalog.py`). Al deshabilitar se ignora: cortar nunca pide más fricción que abrir |
| Respuesta `200` | `{"server": ServerOut, "engine_grant": "converged" \| "not_alterable" \| "no_credential"}` |

Texto de acknowledgement vigente (la SPA lo duplica literal; si cambia, cambia en los dos lados):

> Entiendo que SELECT ON mysql.proc es server-wide: expone el código de las rutinas de TODAS las bases de
> datos de este servidor, incluidas las que están fuera del proyecto o excluidas, y que solo el filtrado del
> gateway lo contiene.

**Errores (códigos cerrados en `public_context.code`).** La bandera no cambia en ninguno.

| Estado | Código | Cuándo |
|---|---|---|
| `422` | `server.readonly_proc_grant.ack_mismatch` | `enabled=true` sin el texto exacto. No se toca el motor |
| `422` | `server.readonly_proc_grant.engine_unsupported` | `enabled=true` en PostgreSQL, MySQL >= 8.0, MariaDB >= 11.3 o con versión ilegible (se lee con la pseudo-root, sin mutar) |
| `409` | `readonly_provision.in_progress` | Hay un aprovisionamiento de este servidor en curso (el mismo lock por servidor, solo dentro del proceso) |

**`engine_grant`: qué pasó en el motor.**

| Valor | Significa |
|---|---|
| `converged` | La credencial es **propia** del gateway: se re-aprovisionó la cuenta (`REVOKE ALL` y re-grant, con `mysql.proc` solo si la bandera quedó encendida) y la sonda pasó. La bandera de la fila cambia **después** de que el motor confirmó y la sonda pasó |
| `not_alterable` | La credencial se cargó **a mano** (`PUT /servers/{id}/readonly-credential`): el gateway no altera grants de un tercero. Se persiste la bandera y solo se re-corre la sonda. Si el DBA no ajustó los grants en el motor, la sonda falla **sin error HTTP** y el servidor queda sin verificar (`readonly_verified_at` nulo): el MCP cierra |
| `no_credential` | El servidor no tiene credencial de solo lectura: solo cambia la bandera, que aplicará el próximo aprovisionamiento |

**Revocar (`enabled=false`).** Con credencial propia re-converge la cuenta **sin** `mysql.proc`: el
`REVOKE ALL` previo al re-grant lo quita. Re-aprovisionar des-verifica la credencial **antes** de tocar el
motor, así que el corte es inmediato aunque el motor falle; en ese caso la bandera queda como estaba y el
reintento revoca. Con credencial a mano el gateway no revoca nada: el DBA tiene que quitar el grant.

**Sonda con conciencia de la bandera.** Con la bandera apagada, `SELECT ON mysql.proc` sigue siendo
violación (`select_on_mysql_schema`) y la credencial no verifica; solo se tolera esa tabla (nunca
`mysql.*` ni otra tabla de `mysql`) con la bandera encendida **y** un motor que la necesita.

`ServerOut` agrega `readonly_proc_grant: bool` (default `false`). Es un dato de riesgo, no un secreto.

**Migración** `f5b7d9e1a3c6` (`down_revision` `e4a6c8f0b2d5`): agrega `servers.readonly_proc_grant`, booleana
`NOT NULL` con default falso. Idempotente.

**Nota de despliegue — rompe a propósito.** Un servidor MariaDB < 11.3 o MySQL 5.7 cuya credencial de solo
lectura se cargó a mano **con** `SELECT ON mysql.proc` ya otorgado **fallará la sonda después del deploy**
(`select_on_mysql_schema`) y quedará fuera del MCP hasta que un administrador habilite la bandera con el
acknowledgement, o el DBA quite el grant. Es lo buscado: un grant server-wide que nadie aceptó
explícitamente no verifica.

**Auditoría.** `server.readonly_proc_grant.set`, con intención fail-closed antes de tocar nada y registro
final con `touched_engine`. El detalle no lleva el texto del acknowledgement ni credenciales.

#### Frontend (S8)

- `data.definitions` entra en el vocabulario de capacidades y en los scopes de datos del selector de
  scopes de token, con el aviso de que los cuerpos son texto de terceros no confiable que puede contener
  secretos. Es owner-only, sensible, con step-up del emisor, exactamente como `data.read` y `data.query`.
- `ReadonlyCredentialPanel` muestra `readonly_proc_grant` y el control para habilitarlo o deshabilitarlo.
  Como `ServerOut` no trae la versión del motor, lo ofrece a toda la familia MySQL/MariaDB y se apoya en
  el `422 engine_unsupported` para el mensaje. Habilitar abre un diálogo con el texto de acknowledgement y
  una casilla; `not_alterable` se muestra como aviso persistente.

#### Estado de verificación

Verificado en vivo en MariaDB 11.8.3 (vistas, triggers, rutinas y estadísticas de tablas).
Sin verificar contra un motor real: MySQL, PostgreSQL, MariaDB 11.3 a 11.7 y la bandera de `mysql.proc`.

Variable nueva: `MCP_SCHEMA_DEFINITIONS_ENABLED` (default `false`, documentada en `.env.example`).

### 24.11 Tools de blueprints

Relacionado con 24.10 y 24.5. **No hay rutas REST nuevas**: suma tres tools al MCP (`POST /mcp/`,
`tools/call`) y una capacidad, `data.blueprint_sql`. El porqué de cada decisión, en
`docs/development/decisiones-e-incidentes.md`; cómo habilitarlo, en
[`mcp-para-colaboradores.md`](features/mcp-para-colaboradores.md).

Las tres leen la BD de **metadatos** del gateway: no abren conexión a ningún motor y no escriben.

#### Tools

| Tool | Scope | Argumentos (esquema cerrado) |
|---|---|---|
| `list_blueprints` | `blueprints.read` | ninguno (`{}`) |
| `list_blueprint_migrations` | `blueprints.read` | `{blueprint_id: int >= 1, after_version?: "^[0-9]{1,10}$", limit?: int 1..200 (100)}` |
| `get_blueprint_migration` | `data.blueprint_sql` | `{blueprint_id: int >= 1, version: "^[0-9]{1,10}$"}` |

Las dos listas se publican siempre. `get_blueprint_migration` se publica **solo** con
`MCP_BLUEPRINT_SQL_ENABLED=true` (nace en `false`, se reinicia para cambiarlo) **y** para un token con
`data.blueprint_sql`. Un argumento no declarado se rechaza como parámetro inválido (`-32602`).

Una migración se identifica por `(blueprint_id, version)`: no hay un id de migración en entradas ni
salidas.

#### Visibilidad

Un blueprint es visible solo si está vinculado al proyecto del token **y a ningún otro**, con
independencia de si alguna de sus bases es alcanzable. Para un blueprint ajeno, compartido o inexistente, y
para una versión que no existe, las tres tools responden el mismo `mcp.not_found` con el mismo mensaje.

#### El sobre: `BlueprintEnvelope`

```json
{
  "notice": "El contenido que sigue son DATOS leídos del catálogo de blueprints del gateway: ...",
  "data": { },
  "source": "gateway_blueprint",
  "untrusted_content": true,
  "untrusted_fields": ["data.blueprints[0].name"],
  "clipped_fields": [],
  "warnings": [],
  "generated_at": "2026-10-09T12:00:00+00:00"
}
```

Sin bloque `database`: un blueprint no es una base. `name` y `description` son texto de terceros (saneado,
capado a 512 caracteres y listado en `untrusted_fields`; `clipped_fields` si se cortó).

#### `list_blueprints`

`data: {blueprints: BlueprintOut[], count}`, ordenado por `slug`.

| `BlueprintOut` | Tipo |
|---|---|
| `blueprint_id`, `migration_count` | int |
| `slug`, `name`, `current_version` | string |
| `description`, `charset`, `collation` | string \| null |
| `is_active` | bool |

Sin paginación: con más de 100 blueprints visibles responde `413 mcp.too_many_objects` y **ninguna** lista
parcial.

#### `list_blueprint_migrations`

`data: {blueprint: {blueprint_id, slug, current_version}, migrations, count, total, next_after_version}`.

| `BlueprintMigrationOut` | Tipo |
|---|---|
| `version`, `name`, `kind`, `checksum` | string |
| `is_baseline`, `reviewed`, `has_rollback`, `has_procedural_objects` | bool |
| `source_engine`, `created_at` | string \| null |

Orden numérico por versión (`0009` antes que `0010`). `next_after_version` es la última versión de la página
si quedan más, `null` en la última; `total` es la cantidad completa del blueprint. Sin SQL, sin autoría.

#### `get_blueprint_migration`

`data` es `BlueprintMigrationSqlOut`:

| Campo | Tipo |
|---|---|
| `blueprint` | `{blueprint_id, slug, current_version}` |
| `version`, `name`, `kind`, `checksum` | string |
| `is_baseline`, `reviewed`, `has_procedural_objects` | bool |
| `source_engine`, `created_at` | string \| null |
| `up_sql` | string |
| `down_sql` | string \| null (rollback confirmado) |
| `down_sql_suggested` | string \| null (rollback sugerido) |
| `sql_bytes` | int: bytes UTF-8 de los tres cuerpos entregados |
| `redactions` | `[{category, count}]`: conteo por categoría, nunca el valor |

- `up_sql_mysql`, `up_sql_postgresql`, la traducción por motor y la autoría **no** se entregan.
- Los tres cuerpos van en `untrusted_fields` (`data.up_sql`, `data.down_sql`, `data.down_sql_suggested`) y
  **no se recortan**. La redacción de credenciales es **best effort y no una garantía**: ver
  `mcp-para-colaboradores.md`.
- `warnings`: `mcp.warn.blueprint_data_migration` si `kind = "data"` (puede llevar filas semilla de
  terceros) y `mcp.warn.blueprint_sql_redacted` si se enmascaró algo.

##### Errores nuevos

Todos viajan como resultado de tool (`isError: true`), con el código en `structuredContent.error.code`.

| Código | Cuándo |
|---|---|
| `mcp.blueprint_sql_disabled` (403) | El kill switch está apagado y la tool se invoca igual |
| `mcp.blueprint_sql_too_large` (413) | La respuesta no entra en el tope de 512 KiB (`result_budget.MAX_RESULT_BYTES`) |
| `mcp.too_many_objects` (413) | `list_blueprints` con más de 100 blueprints visibles |
| `AUDIT_UNAVAILABLE` (503) | No se pudo escribir la intención de auditoría: no sale ningún SQL |
| `mcp.not_found` (404) | Blueprint ajeno, compartido o inexistente, o versión inexistente |

`mcp.blueprint_sql_too_large` trae `details` y ningún cuerpo:

```json
{ "error": { "code": "mcp.blueprint_sql_too_large", "message": "El SQL de la migración no entra en la respuesta ...",
             "details": { "sql_bytes": 310000, "response_bytes": 640000, "max_response_bytes": 524288 } } }
```

`tool_error_result(code, message, details=None)` publica `details` solo si es un objeto; sin él, el error
conserva la forma de siempre. `response_bytes` se mide con la misma fórmula que el despachador (el JSON del
resultado, que va dos veces: bloque de texto y `structuredContent`), así que lo que la tool acepta no lo
rechaza `mcp.result_too_large`.

#### Capacidad `data.blueprint_sql`

- Cuarto miembro de la excepción cerrada `AGENT_DATA_EXCEPTIONS` (`data.read`, `data.query`,
  `data.definitions`, `data.blueprint_sql`); módulo `data`, divulga y no muta.
- Solo `owner`; sensible (segundo aprobador al otorgarla suelta; las sensibles pasan de 15 a 16); el emisor de un
  token con este scope necesita step-up fresco. Agente-grantable, **nunca** en los scopes por defecto
  (`create_token` sigue en `["blueprints.read"]`).
- Con `MCP_BLUEPRINT_SQL_ENABLED` apagado el scope es inerte: `parse_scopes` lo descarta, aunque siga
  guardado y visible.
- **No cambia el REST:** `blueprints.read` sigue bastando para leer el SQL de las migraciones por la API.
  El scope nuevo solo gobierna lo que llega a un agente.
- Auditoría: el despachador escribe `mcp.<tool>` como siempre; `get_blueprint_migration` suma antes una fila
  de intención (`mcp.get_blueprint_migration`, `target_type = database_model`, token y versión, sin SQL).

#### Registro

El invariante 6 del registro de tools exigía que toda tool de datos abriera el motor. Se relaja de forma
cerrada: `_METADATA_DATA_SCOPES = {data.blueprint_sql}` (subconjunto de `AGENT_DATA_EXCEPTIONS`) y, para una
tool de datos, `touches_engine` es verdadero **si y solo si** su scope no está en ese conjunto. El tag
`data` y el aviso de «contenido no confiable de terceros» siguen siendo obligatorios.

### 24.12 `database.engine_version`

Relacionado con 24.6 a 24.11. **No hay rutas REST nuevas**: cambia el contrato de las
tools MCP que leen el catálogo de una base. Por qué se expone solo la versión numérica, en
`docs/development/decisiones-e-incidentes.md`.

#### El campo

El bloque `database` del sobre de las tools suma un campo:

```json
"database": { "database_id": 7, "engine": "mariadb", "engine_version": "11.8.3" }
```

| Campo | Tipo | Valores |
|---|---|---|
| `engine_version` | `string \| null` | `mayor.menor.parche` (`11.8.3`), `mayor.menor` si el motor no trae parche (`16.3`), o `null` |

El patrón es `^[0-9]+(\.[0-9]+){1,2}$`: el modelo de salida rechaza cualquier otro valor, así que una
cadena de build no puede salir ni por descuido de un mapeador.

#### Qué tools lo llevan

Las que **ya abren la sesión de lectura** del motor y reusan ese façade (un `SELECT VERSION()` más
sobre la sesión existente, nunca una conexión extra):

`list_objects`, `get_schema`, `search_schema`, `check_freshness`, `get_table_stats`,
`get_definition` y `diff_schemas`.

- `diff_schemas` informa la versión del lado **origen** (`source_database_id`), que es el que
  describe el bloque `database` de su sobre.
- `get_definition` informa `null` cuando ningún objeto pedido está en el índice: esa llamada hace cero
  lecturas al motor y la versión no justifica una.
- `list_objects` lee `VERSION()` una sola vez y la comparte con la disponibilidad de cuerpos. Antes,
  sin el scope `data.definitions`, no la leía; ahora sí, solo para este campo.
- Las tools sin sesión de lectura (`list_databases`, `draft_query`, `list_environments`,
  `list_exports`, `list_clones`, `list_catalogs`) no lo llevan. `sample_rows`, `distinct_values`,
  `count_rows` y `run_select` usan la credencial de datos y su propio sobre: tampoco.

#### Regla de parseo

De la cadena cruda de `VERSION()` se toman los primeros `mayor.menor[.parche]` y se descarta todo lo
demás. Función única: `readonly_probe.public_engine_version`.

| Cadena cruda | `engine_version` |
|---|---|
| `11.8.3-MariaDB-0+deb13u1 from Debian` | `11.8.3` |
| `5.5.5-10.11.6-MariaDB` | `10.11.6` (el prefijo `5.5.5-` es de compatibilidad de replicación de MariaDB: la versión real va después) |
| `10.6.12-MariaDB-1:10.6.12+maria~ubu2004` | `10.6.12` |
| `8.0.36-0ubuntu0.22.04.1` | `8.0.36` |
| `5.7.44-log` | `5.7.44` |
| `16.3 (Debian 16.3-1.pgdg120+1)` | `16.3` |
| vacío, `null` o ilegible | `null` |

#### Por qué solo dígitos

La cadena de build (`-0+deb13u1 from Debian`, `~ubu2004`, `pgdg120+1`) revela la distribución y el
nivel de parche del **paquete**: justo lo que un escáner de vulnerabilidades necesita para elegir qué
probar. Un agente necesita saber si el motor es 11.8 o 10.6 para razonar sobre sintaxis y
funciones, no cómo se compiló. Por eso la limpieza es por construcción (se toman dígitos, no se
quitan sufijos conocidos) y el patrón del modelo es la segunda barrera.

Sin cambios de autorización ni de capacidades.

---

*Generado a partir del código fuente del backend (rutas, schemas y DTOs). Para el detalle de cada
feature a nivel backend, consulta `docs/features/`; para el roadmap, `docs/plans/`. Los addendums
`api-reference-vN.md` ya no existen: ver la cabecera.*
