# api-reference-unica

Objetivo: una única referencia de API vigente en `docs/api-reference.md`, sin addendums `vN`.
Rama: `main` (el usuario no pidió rama). Entrega: un commit por dominio, en español, solo con el agente `git`. Sin push.
Runner de tests: ninguno (solo documentación; se verifica contra rutas y schemas con `scripts/check_route_capabilities.py` y lectura de código). ClickUp: `1dfm34y4tw3`.

## Specs

- S1. «Te parece limpiar todos los api-reference y quedarnos unicamente con uno completo? ya que veo que llegamos a la version 48» → queda un solo archivo de referencia completo.
- S2. «Sin problema puedes eliminar todos los archivos necesarios, apoyame con ello» → se eliminan los `docs/api-reference-vN.md` que queden absorbidos (el historial queda en git).
- S3. Cada dominio se escribe contra el código (rutas, schemas, controladores), no copiando addendums: varios se contradicen (v46/v48, v9/v13, v14/v15, v8/v17, v23/v29/v38). Supuesto: ante contradicción manda el código.
- S4. Se reapuntan los enlaces a `api-reference-vN` (~22 archivos, `TODO.md`, `app/services/collation_catalog.py`) y se actualiza `CLAUDE.md:54-58`. Supuesto: respetar la regla 6 del CLAUDE.md (tope de 250 líneas).
- S5. Sin cambios de código ni de contrato.

## Tasks

- [x] T1 (S3) Convenciones y autorización (base, v23, v29, v41, v43) — inline
- [x] T2 (S3) Identidades y tokens (v24, v40, v45) — inline
- [x] T3 (S3) Inventario y bases gestionadas (v12, v21, v27, v31) — inline
- [x] T4 (S3) Charsets y collations (v7, v8, v11, v17) — inline
- [x] T5 (S3) Blueprints y proyectos (v2, v16, v22, v25, v26, v28) — inline
- [x] T6 (S3) Ciclo de vida de versiones (v5, v14, v15, v18, v47) — inline
- [x] T7 (S3) Migraciones, captura de SELECT y validación (v9, v13, v20) — inline
- [x] T8 (S3) Adopción, reconciliación, snapshot (v3, v4) — inline
- [x] T9 (S3) Canales de liberación (v46, v48) — inline
- [x] T10 (S3) Clonado (v19) — inline
- [x] T11 (S3) Exportación (v10) — inline
- [x] T12 (S3) Consola SQL (v6) — inline
- [x] T13 (S3) MCP (v30–v39, v42, v44) — inline
- [x] T14 (S3) API de integración (v45, v48) — inline
- [x] T15 (S4) Reapuntar enlaces, actualizar `CLAUDE.md`, verificar con `rg` que no queda ningún `api-reference-v` — inline

Ruta: todas inline y secuenciales. Motivo: el usuario fija un solo escritor en el hilo principal; los dominios comparten el archivo único, así que no son unidades con superficies disjuntas.

## Log

- L1 (2026-11-13, usuario): «Te parece limpiar todos los api-reference y quedarnos unicamente con uno completo? ya que veo que llegamos a la version 48»
- L2 (2026-11-13, usuario): «Sin problema puedes eliminar todos los archivos necesarios, apoyame con ello»
- L3: mapa de los 48 archivos por dominio hecho por un explorador de solo lectura (búsqueda por palabras clave y cabeceras, sin leer los cuerpos completos: puede haber más contradicciones). Búsqueda en ClickUp: sin duplicados.
- L4: `docs/api-reference-v48.md` y la corrección de `docs/integration-api-contract.md` están sin commitear; T9 y T14 los absorben.
- Siguiente paso: T9 (canales) y T14 (integración), los dominios más recientes.
- L5: T9 hecha: sección 21 de api-reference.md (canales + lectura por integración + flujo + errores); borrados v46 (git rm) y v48 (sin trackear); enlaces de release-channels.md, v47 y integration-api-contract.md reapuntados. Falta reapuntar decisiones-e-incidentes.md:2854. Nada commiteado. T14 queda para v45 (tokens y API de integración).
- L6: T14 hecha: sección 22 de api-reference.md (v45 íntegro, verificado contra environments.py y rutas de integration_tokens; corregida la nota "diez scopes/rollback y stamp no existen": son 13 scopes). v45 borrado (git rm). Pendiente de T2: v40 y v24 (identidades y tokens de sesión).
- L7: v47 absorbido en §23 (aprobaciones destructivas, verificadas las 6 rutas) y borrado; T6 queda parcial (faltan v5, v14, v15, v18). Script de reapuntado: $CLAUDE_JOB_DIR/tmp/repoint.sh N SECCION. Siguiente: grupo MCP (T13).
- L8: T13 hecha: §24 MCP (12 subsecciones) con v30–v39, v42, v44; borrados esos 12 addendums; quitadas las frases obsoletas («ninguna tool de datos existe todavía»); enlaces reapuntados a §24.x. Quedan 32 api-reference*.md. Verificación en vivo solo MariaDB 11.8.3 (según el addendum original). Siguiente: T2 (v24, v40), T1 (v23, v29, v41, v43).
- L9 (2026-11-14, usuario): «Adelante, no hay problema con eliminarlos y absorber o de plano, no tener api referente, solo hasta que se solicite, ya que en su mayoria son para aplicar en el frontend aunque la mayoria por no decir todos ya fueron aplicaods»
- L10: decisión: no se absorben v2–v29, v40, v41, v43 (features ya aplicadas en el frontend). Borrados los 31 restantes; quedan solo api-reference.md (§1–§24). Cabecera/pie/índice de api-reference.md y CLAUDE.md:54-57 reescritos; enlaces markdown a vN convertidos a texto. Quedan 94 menciones en texto plano a vN como cita histórica (git show b61def1:docs/api-reference-vN.md). T1–T8, T10–T12 cerradas por esta decisión, no por absorción. Sin commitear.
