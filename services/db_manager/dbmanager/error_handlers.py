"""Database and encryption failures, translated to the same JSON errors as everywhere else."""

import psycopg2
from ase_common import error
from flask import jsonify
from psycopg2 import errorcodes, pool

from .crypto import CryptoError

UNAVAILABLE_CODES = {
    errorcodes.LOCK_NOT_AVAILABLE,
    errorcodes.QUERY_CANCELED,
    errorcodes.DEADLOCK_DETECTED,
    errorcodes.SERIALIZATION_FAILURE,
}


def register_database_errors(app) -> None:
    @app.errorhandler(CryptoError)
    def crypto_error(exception: CryptoError):
        app.logger.error("field encryption failure: %s", exception)
        return error(500, "stored data could not be decrypted")

    @app.errorhandler(psycopg2.Error)
    def database_error(exception: psycopg2.Error):
        code = exception.pgcode or ""
        if code.startswith("22"):
            return error(400, "invalid database value")
        if code.startswith("23"):
            constraint = exception.diag.constraint_name
            return jsonify(
                error="Conflict", message="database constraint violation", constraint=constraint
            ), 409
        if code == errorcodes.INSUFFICIENT_PRIVILEGE:
            app.logger.error("the database refused a statement: %s", exception.diag.message_primary)
            return error(403, "the database refused this operation for the calling service")
        app.logger.warning("database operation failed: %s (%s)", type(exception).__name__, code)
        if (
            isinstance(
                exception, (psycopg2.OperationalError, psycopg2.InterfaceError, pool.PoolError)
            )
            or code in UNAVAILABLE_CODES
        ):
            return error(503, "database unavailable")
        return error(500, "database operation failed")
