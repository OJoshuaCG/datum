# Canales de liberación de blueprints

Un **canal** (`stable`, `development`…) es un nombre global que decide qué versión de un blueprint consumen las
integraciones, el alta de bases y la aplicación en lote. Cada blueprint tiene, por canal, un **puntero** a una de
sus versiones. Contrato completo en [`api-reference.md` §21](../api-reference.md).

Un canal **no toca ningún motor destino**: mover un puntero cambia qué versión recibirá la *próxima* base o la
*próxima* aplicación que pida ese canal. Las bases ya migradas no se mueven solas.

## Los tres modos

| Modo | Qué versión sirve | Quién la decide |
|---|---|---|
| `manual` | La del puntero de cada blueprint | Una persona con `blueprints.promote` |
| `derived` | La **mínima** que registra el inventario en las bases del entorno enlazado | El estado real de las bases |
| `latest` | La versión **revisada** más nueva del blueprint | Quien publica y aprueba versiones |

**Todos los modos se sirven desde el puntero.** En `manual` lo mueve una persona (`promote`); en `latest` y `derived`
lo mueve el gateway solo, cada vez que cambia lo que lo determina: se crea o se aprueba una versión, o una base
cambia de versión (apply, stamp, resync). Los movimientos automáticos quedan en la bitácora con actor `system` y
nota `automático (<modo>)`, como `promote` (avanza) o `demote` (retrocede).

**El puntero nunca cae sobre una versión sin revisar.** Si la nueva punta o el nuevo mínimo no está aprobada, el
puntero se queda donde estaba y se mueve cuando alguien la aprueba. Al arrancar, el gateway recalcula los punteros
automáticos de todos los blueprints (es el backfill de los canales que existían sin puntero).

`derived` exige un entorno enlazado. Cuenta una base solo si el gateway la posicionó alguna vez y hoy existe en el
motor: quedan fuera las que están en cuarentena, las que no existen y las que nunca se migraron. Es **fail-closed**:
si una base que cuenta no se puede leer (o está en una versión que el blueprint no conoce), el canal no resuelve
(`release_channel.derived_unreadable`) en lugar de calcular un mínimo con las que sí se vieron. Sin ninguna base que
cuente, `release_channel.derived_no_scope`.

## Quién puede qué

- Ver el catálogo y los punteros: `release_channels.read` y `blueprints.read` (los tres roles base).
- Crear, editar y borrar canales: `release_channels.write`, solo `security_officer`, con step-up.
- Promover o retroceder un puntero: `blueprints.promote`, solo `owner` en el alcance, con step-up.

Promover no es autoría: `blueprints.write` (que tiene `operator`) no alcanza.

## Promover y retroceder

Solo en canales `manual` y solo a versiones **revisadas**. Avanzar es directo. **Retroceder** (destino menor que el
actual) es una decisión de liberación y exige repetir el slug del canal (`?confirm_slug=`). Promover la versión que
ya está puesta responde `200` con `changed: false` y no escribe nada. Cada movimiento deja un evento (`promote`,
`demote`) con quién y el motivo opcional.

El historial guarda la **etiqueta** de la versión al momento del evento. Un renumerado posterior no la reescribe,
así que puede no coincidir con la numeración de hoy.

## Referencia y lectura en vivo

Junto al puntero de cualquier canal con entorno, la pantalla muestra el **mínimo del entorno** como
referencia: sirve para ver si el puntero va por delante o por detrás de lo que de verdad corre. No decide nada.

- El listado la calcula con la **caché del inventario** (`source: inventory_cache`) y lo rotula así: la caché puede
  estar vieja si alguien movió una base fuera del gateway.
- «Leer en vivo» llama a `.../resolve`, que **conecta a cada base** del entorno. Tiene un tope de 20 por minuto y es
  tolerante: responde `200` aunque no resuelva, con `resolved: false`, el `code` y el estado de cada base.
- Los consumidores (alta de base, aplicar a todas, integración) **siempre** leen en vivo y fallan con `409` si el
  canal no resuelve. Nunca caen a «la última versión» por su cuenta.

## Consumir un canal

| Dónde | Cómo | Notas |
|---|---|---|
| Alta de una base | `channel` en `POST /managed-databases` | Excluyente con `target_version`; exige `apply_migrations`. Se resuelve **antes** de crear la base |
| Aplicar a todas | `?channel=` o `?target_version=` | Las bases que ya están por encima del tope no se tocan ni retroceden |
| API de integración | `channel` en aplicar migraciones | Excluyente con `version`; sigue siendo solo hacia adelante |

Pasar un canal y una versión a la vez es `422 release_channel.target_conflict`.

## Borrar una versión apuntada por un canal

El plan de borrado (`delete-plan`) lista `channel_moves`: cada canal cuyo puntero está en esa versión pasa a la
**versión revisada inmediatamente anterior**, o queda **sin versión** si no hay ninguna. Es una escritura local del
gateway, no del motor, pero **cambia lo que consumen las integraciones**, así que:

- El borrado nunca se bloquea por un canal.
- Con movimientos de canal, `requires_confirmation` es verdadero y hace falta el `confirm_token` del plan, aunque
  no haya bases que re-marcar.
- Cada movimiento queda como evento `version_deleted` en el historial.
- Un canal que queda sin versión hace que sus consumidores reciban `409 release_channel.no_released_version`.

**Desplegar esta funcionalidad invalida los `confirm_token` de borrado que estuvieran pendientes**: la huella del
plan ahora incluye los movimientos de canal. Hay que volver a pedir el plan.

## Borrar un canal o un entorno

- Un canal con punteros exige repetir su slug (`?confirm_slug=`); los punteros se borran en cascada.
- Un entorno enlazado por canales no se puede borrar: `409 environment.has_release_channels`. Hay que desvincular
  los canales antes.

## Errores

El modo de resolución inválido no tiene código propio: es el `422` estándar de Pydantic y, en producción, no trae
detalle por campo (la SPA valida el modo antes de enviar). El resto de los códigos están en
`app/services/release_channel_catalog.py` y en la tabla de `api-reference.md` §21.7; se clasifican siempre por
`public_context.code`, nunca por el texto del mensaje.

## Operación

- La migración `d2f4a6c8e0b1` crea tres tablas (`release_channels`, `blueprint_channel_pointers`,
  `blueprint_channel_pointer_events`); su `downgrade` las elimina, y con ellas los punteros y el historial.
- El sembrado de arranque (`stable`, `development`) solo corre con la tabla vacía: un canal que el operador borró no
  resucita en cada reinicio.
