"""Database access for the CRM.

SQLite is the default development backend. Set CRM_DB_DRIVER=sqlserver and
the DB_* environment variables to use the shared SQL Server database.
"""

import os
import sqlite3
import threading

_local = threading.local()


def backend_name():
    return os.environ.get("CRM_DB_DRIVER", "sqlite").lower()


def _sqlite_path():
    return os.environ.get("CRM_SQLITE_PATH", os.path.join(os.path.dirname(__file__), "crm.sqlite3"))


def _connect():
    if backend_name() == "sqlite":
        conn = sqlite3.connect(_sqlite_path(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        _local.conn = conn
        return conn

    try:
        import pymssql
    except ImportError as exc:
        raise RuntimeError("SQL Server mode requires pymssql. Install requirements.txt first.") from exc

    required = {name: os.environ.get(name) for name in ("DB_SERVER", "DB_NAME", "DB_USER", "DB_PASSWORD")}
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing SQL Server environment variables: {', '.join(missing)}")
    conn = pymssql.connect(
        server=required["DB_SERVER"], user=required["DB_USER"],
        password=required["DB_PASSWORD"], database=required["DB_NAME"],
        port=os.environ.get("DB_PORT", "1433"), autocommit=True,
        as_dict=True, appname="rpatech-crm", charset="UTF-8", tds_version="7.4",
    )
    _local.conn = conn
    return conn


def _get_conn(force_new=False):
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
    try:
        return operation(_get_conn())
    except Exception as exc:
        if backend_name() == "sqlserver":
            try:
                import pymssql
                retryable = (pymssql.OperationalError, pymssql.InterfaceError)
            except ImportError:
                retryable = ()
            if retryable and isinstance(exc, retryable):
                return operation(_get_conn(force_new=True))
        raise


def query(sql, params=None):
    def operation(conn):
        cur = conn.cursor()
        cur.execute(sql, params or ())
        rowcount = cur.rowcount
        cur.close()
        return rowcount
    return _run(operation)


def select(sql, params=None):
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
    rows = select(sql, params)
    return rows[0] if rows else None


def commit():
    _get_conn().commit()


def rollback():
    _get_conn().rollback()
