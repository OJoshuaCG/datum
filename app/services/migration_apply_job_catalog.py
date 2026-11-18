"""
Vocabulario cerrado de los jobs de ``apply-all`` en background (``POST .../apply-all-jobs``).

Estos códigos viajan en ``public_context["code"]``, **nunca** en ``context``: ``context`` solo se
expone en ``development``, así que en producción el operador recibiría el error sin poder
clasificarlo.
"""

#: Ya hay un job ``pending`` o ``running`` para este blueprint. Dos recorridos simultáneos del
#: mismo blueprint competirían por las mismas bases (el advisory lock por base los serializa,
#: pero el segundo reportaría como fallidas las bases que el primero tiene tomadas). La respuesta
#: lleva ``active_job_id`` para que el cliente siga ese job en vez de crear otro.
CODE_JOB_ALREADY_ACTIVE = "migration_apply_job.already_active"

#: El job se grabó pero no se pudo encolar (por ejemplo, el pool ya está apagado). El job queda
#: ``failed`` en el acto: si quedara ``pending``, bloquearía el blueprint con
#: ``already_active`` hasta el próximo reinicio. Se puede volver a pedir.
CODE_JOB_ENQUEUE_FAILED = "migration_apply_job.enqueue_failed"
