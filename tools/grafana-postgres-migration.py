"""Copy Grafana application data into a schema initialized by the same Grafana version.

Run with uv run --with pymysql --with 'psycopg[binary]' python.
Credentials are read from Kubernetes without printing them. Backups contain private
application data and must remain in a private local directory. migration_log is
deliberately retained from PostgreSQL: its entries describe dialect-specific DDL.
"""

import argparse
import base64
import datetime
import decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pymysql


def password(namespace, name, key):
    result = subprocess.run(
        ["kubectl", "get", "-n", namespace, "secret", name, "-o", "json"],
        capture_output=True, check=True,
    )
    return base64.b64decode(json.loads(result.stdout)["data"][key]).decode()


def encode(value):
    if isinstance(value, bytes):
        return {"bytes": base64.b64encode(value).decode()}
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return {"datetime": value.isoformat()}
    if isinstance(value, decimal.Decimal):
        return {"decimal": str(value)}
    return value


def decode(value):
    if isinstance(value, dict):
        if "bytes" in value:
            return base64.b64decode(value["bytes"])
        if "datetime" in value:
            return value["datetime"]
        if "decimal" in value:
            return decimal.Decimal(value["decimal"])
    return value


def export_mysql(path):
    connection = pymysql.connect(
        host="127.0.0.1", port=15336, user="grafana", database="grafana",
        password=password("grafana", "mysql-secret", "password"), charset="utf8mb4",
    )
    tables = {}
    with connection, connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        cursor.execute("SHOW TABLES")
        names = [row[0] for row in cursor.fetchall()]
        for name in names:
            cursor.execute("SELECT * FROM `" + name.replace("`", "``") + "`")
            columns = [item[0] for item in cursor.description]
            rows = [[encode(value) for value in row] for row in cursor.fetchall()]
            tables[name] = {"columns": columns, "rows": rows}
        connection.rollback()
    with open(path, "w") as output:
        os.chmod(path, 0o600)
        json.dump(tables, output, ensure_ascii=False)
    print("Exported", len(tables), "tables,", sum(len(t["rows"]) for t in tables.values()), "rows")


def target_connection():
    return psycopg.connect(
        host="127.0.0.1", port=15436, user="grafana_app", dbname="grafana",
        password=password("cnpg-database", "grafana-db-credentials", "password"),
    )


def schema(connection):
    result = {}
    with connection.cursor() as cursor:
        cursor.execute("""SELECT table_name, column_name, data_type, column_default, is_identity
            FROM information_schema.columns WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position""")
        for name, column, kind, default, identity in cursor:
            result.setdefault(name, {})[column] = (kind, default, identity)
    return result


def adapt(value, kind):
    value = decode(value)
    if value is None:
        return None
    if kind == "boolean":
        return bool(int(value)) if isinstance(value, (str, int, decimal.Decimal)) else bool(value)
    if kind in ("smallint", "integer", "bigint"):
        return int(value)
    if kind in ("real", "double precision"):
        return float(value)
    if kind == "bytea":
        return value.encode() if isinstance(value, str) else bytes(value)
    if kind in ("json", "jsonb"):
        return Jsonb(json.loads(value) if isinstance(value, str) else value)
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def validated_tables(data, target):
    for name, table in data.items():
        if name == "migration_log":
            continue
        if name not in target:
            if table["rows"]:
                raise RuntimeError("Nonempty source table missing from target: " + name)
            continue
        missing = set(table["columns"]) - target[name].keys()
        if missing:
            raise RuntimeError("Source columns missing from target: " + name + ": " + ",".join(sorted(missing)))
        yield name, table


def import_postgres(path):
    data = json.loads(Path(path).read_text())
    with target_connection() as connection:
        target = schema(connection)
        tables = list(validated_tables(data, target))
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL TIME ZONE 'UTC'")
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")
            cleared = [sql.Identifier("public", name) for name in target if name != "migration_log"]
            cursor.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY").format(sql.SQL(",").join(cleared)))
            for name, table in tables:
                columns = table["columns"]
                query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier("public", name), sql.SQL(",").join(map(sql.Identifier, columns)),
                    sql.SQL(",").join(sql.Placeholder() for _ in columns),
                )
                rows = [tuple(adapt(v, target[name][c][0]) for c, v in zip(columns, row)) for row in table["rows"]]
                cursor.executemany(query, rows)
                for column, (_, default, identity) in target[name].items():
                    if identity == "YES" or (default and default.startswith("nextval(")):
                        cursor.execute("SELECT pg_get_serial_sequence(%s,%s)", ("public." + name, column))
                        sequence = cursor.fetchone()[0]
                        if sequence:
                            cursor.execute(sql.SQL("SELECT max({}) FROM {}").format(sql.Identifier(column), sql.Identifier("public", name)))
                            maximum = cursor.fetchone()[0]
                            cursor.execute("SELECT setval(%s::regclass,%s,%s)", (sequence, max(1, maximum or 0), bool(maximum and maximum > 0)))
                print("Imported", name, len(rows), "rows")
    print("Import transaction committed")


def canonical(value):
    if isinstance(value, Jsonb):
        return value.obj
    if isinstance(value, memoryview):
        value = bytes(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        value = value.isoformat()
    if isinstance(value, str):
        # Grafana timestamps use UTC; MySQL returns naive values while PostgreSQL
        # may attach a UTC offset. Normalize only ISO timestamp-shaped values.
        if len(value) >= 19 and value[4:5] == "-" and value[10:11] in ("T", " "):
            return value.replace(" ", "T").removesuffix("+00:00")
    return encode(value)


def fingerprint(rows):
    normalized = [json.dumps([canonical(v) for v in row], sort_keys=True, ensure_ascii=False) for row in rows]
    return hashlib.sha256("\n".join(sorted(normalized)).encode()).hexdigest()


def verify(path):
    data = json.loads(Path(path).read_text())
    with target_connection() as connection:
        target = schema(connection)
        for name, table in validated_tables(data, target):
            columns = table["columns"]
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("SELECT {} FROM {}").format(sql.SQL(",").join(map(sql.Identifier, columns)), sql.Identifier("public", name)))
                actual = cursor.fetchall()
            expected = [tuple(adapt(v, target[name][c][0]) for c, v in zip(columns, row)) for row in table["rows"]]
            # MySQL strips CHAR padding on reads; PostgreSQL returns it.
            def without_padding(rows):
                return [tuple(v.rstrip(" ") if isinstance(v, str) and target[name][c][0] == "character" else v
                              for c, v in zip(columns, row)) for row in rows]
            actual = without_padding(actual)
            expected = without_padding(expected)
            if len(actual) != len(expected) or fingerprint(actual) != fingerprint(expected):
                raise RuntimeError("Row comparison failed for " + name)
        print("Verified all application table row counts and contents; PostgreSQL migration_log retained")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["export", "import", "verify"])
    parser.add_argument("backup", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        {"export": export_mysql, "import": import_postgres, "verify": verify}[args.mode](args.backup)
    except Exception as error:
        # SQL driver exceptions can include application records. Do not expose
        # credentials, encrypted datasource settings, or private Grafana data.
        detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print("Migration failed:", detail, "SQLSTATE:", getattr(error, "sqlstate", None), file=sys.stderr)
        raise SystemExit(1) from None
