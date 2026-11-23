"""db-manager: the only service allowed to touch PostgreSQL.

It exposes generic CRUD plus a transactional batch endpoint. Callers never send SQL. Each
call is authorised against `dbmanager.policy`, validated, turned into SQL by `dbmanager.sql`
and executed with the PostgreSQL role of the calling service (`dbmanager.database`).
"""

from ase_common import create_app
from dbmanager.database import transaction
from dbmanager.error_handlers import register_database_errors
from dbmanager.routes import bp
from flask import abort, request

app = create_app(
    "db-manager",
    required_secrets=("POSTGRES_PASSWORD", "DATA_ENCRYPTION_KEY"),
    required_keys=(),  # it only verifies callers; it signs nothing and never sees user tokens
)
register_database_errors(app)
app.register_blueprint(bp)


@app.get("/ready")
def ready():
    """Container healthcheck: answers only to the container itself, and proves the database works."""
    if request.remote_addr not in {"127.0.0.1", "::1"}:
        abort(404)
    with transaction() as cursor:
        cursor.execute("SELECT 1")
    return {"status": "ok"}
