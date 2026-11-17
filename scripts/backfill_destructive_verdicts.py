"""
Precarga los veredictos de destructividad (``model_migration_destructive_verdicts``) de los
blueprints que ya existen, para que ni siquiera la primera lectura del listado de versiones por
aprobar sea lenta.

NO es necesario para que el sistema sea correcto: el listado recalcula y guarda lo que falta por su
cuenta. Es seguro repetirlo (cada fila se reemplaza por el veredicto actual) y solo escribe en la
BD de metadatos del gateway; no abre conexión a ningún servidor de bases de datos de terceros.

Uso (desde la raíz del repo, con el ``.env`` del entorno que quieras precargar):

    .venv/bin/python scripts/backfill_destructive_verdicts.py            # todos los blueprints
    .venv/bin/python scripts/backfill_destructive_verdicts.py --model-id 19
    .venv/bin/python scripts/backfill_destructive_verdicts.py --dry-run  # no escribe
"""

import argparse
import sys
import time
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT))

from app.controllers.destructive_approval_controller import (  # noqa: E402
    DestructiveApprovalController,
)
from app.controllers.managed_migration_controller import ManagedMigrationController  # noqa: E402
from app.core.database import Database  # noqa: E402
from app.models.database_model import DatabaseModel  # noqa: E402


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model-id", type=int, help="Solo este blueprint (por defecto, todos).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analiza y cuenta, pero revierte en vez de guardar.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    session = Database().get_declarative_base_session()
    controller = DestructiveApprovalController()
    try:
        models_query = session.query(DatabaseModel.id, DatabaseModel.name)
        if arguments.model_id is not None:
            models_query = models_query.filter(DatabaseModel.id == arguments.model_id)
        blueprints = models_query.order_by(DatabaseModel.id).all()
        if not blueprints:
            print("No hay blueprints que procesar.")
            return 1

        total_written = 0
        for blueprint_id, blueprint_name in blueprints:
            started_at = time.perf_counter()
            specs = ManagedMigrationController._load_specs(session, blueprint_id)
            written_count = controller.refresh_verdicts(session, specs)
            # Un commit por blueprint: si uno falla, los anteriores ya quedaron guardados.
            if arguments.dry_run:
                session.rollback()
            else:
                session.commit()
            elapsed_seconds = time.perf_counter() - started_at
            total_written += written_count
            print(
                f"blueprint {blueprint_id} ({blueprint_name}): {len(specs)} versiones, "
                f"{written_count} veredictos en {elapsed_seconds:.1f}s"
            )

        mode_label = "analizados (dry-run, nada guardado)" if arguments.dry_run else "guardados"
        print(f"Total: {total_written} veredictos {mode_label}.")
        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
