"""
Database access for the CRM.

Default backend: SQL Server
Configure using environment variables:

    CRM_DB_DRIVER=sqlserver
    DB_SERVER=20.197.41.205
    DB_NAME=Mayank_DB
    DB_USER=Mayank
    DB_PASSWORD=May@2@26!
    DB_PORT=1433

For local development, SQLite can still be used by setting:
    CRM_DB_DRIVER=sqlite
"""

import os
import sqlite3
import threading

_local = threading.local()


def backend_name():
    return os.environ.get("CRM_DB_DRIVER", "sqlserver").lower()


def _sqlite_path():
    return os.environ.get(
        "CRM_SQLITE_PATH",
        os.path.join(os.path.dirname(__file__), "crm.sqlite3")
    )


def _connect():
    """
    Create a database connection based on the configured backend.
    """

    # SQLite Backend
    if backend_name() == "sqlite":
        conn = sqlite3.connect(
            _sqlite_path(),
            check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        _local.conn = conn
        return conn

    # MSSQL Backend
    try:
        import pymssql
    except ImportError as exc:
        raise RuntimeError(
            "SQL Server mode requires pymssql. "
            "Install it using: pip install pymssql"
        ) from exc

    server = os.environ.get("DB_SERVER", "20.197.41.205")
    database = os.environ.get("DB_NAME", "Mayank_DB")
    user = os.environ.get("DB_USER", "Mayank")
    password = os.environ.get("DB_PASSWORD", "May@2@26!")
    port = int(os.environ.get("DB_PORT", "1433"))

    conn = pymssql.connect(
        server=server,
        user=user,
        password=password,
        database=database,
        port=port,
        autocommit=False,
        as_dict=True,
        charset="UTF-8",
        appname="rpatech-crm"
    )

    _local.conn = conn
    return conn


def _get_conn(force_new=False):
    """
    Get connection from thread-local storage.
    """

    if force_new:
        old = getattr(_local, "conn", None)

        if old is not None:
            try:
                old.close()
            except Exception:
                pass

        _local.conn = None

    return getattr(_local, "conn", None) or _connect()


def _run(operation):
    """
    Execute operation with retry logic for MSSQL connection failures.
    """

    try:
        return operation(_get_conn())

    except Exception as exc:

        if backend_name() == "sqlserver":
            try:
                import pymssql

                retryable = (
                    pymssql.OperationalError,
                    pymssql.InterfaceError,
                )

            except ImportError:
                retryable = ()

            if isinstance(exc, retryable):
                return operation(_get_conn(force_new=True))

        raise


def query(sql, params=None):
    """
    Execute INSERT, UPDATE, DELETE statements.
    Returns affected row count.
    """

    def operation(conn):
        cur = conn.cursor()
        cur.execute(sql, params or ())
        conn.commit()

        rowcount = cur.rowcount
        cur.close()

        return rowcount

    return _run(operation)


def select(sql, params=None):
    """
    Execute SELECT query.
    Returns list of dictionaries.
    """

    def operation(conn):

        if backend_name() == "sqlserver":
            cur = conn.cursor(as_dict=True)
        else:
            cur = conn.cursor()

        cur.execute(sql, params or ())
        rows = cur.fetchall()
        cur.close()

        if backend_name() == "sqlserver":
            return rows

        return [dict(row) for row in rows]

    return _run(operation)


def select_one(sql, params=None):
    """
    Return first row or None.
    """

    rows = select(sql, params)
    return rows[0] if rows else None


def execute_scalar(sql, params=None):
    """
    Return first column of first row.
    """

    row = select_one(sql, params)

    if not row:
        return None

    return next(iter(row.values()))


def commit():
    """
    Commit transaction.
    """

    _get_conn().commit()


def rollback():
    """
    Rollback transaction.
    """

    _get_conn().rollback()


def close():
    """
    Close current thread connection.
    """

    conn = getattr(_local, "conn", None)

    if conn:
        try:
            conn.close()
        except Exception:
            pass

        _local.conn = None