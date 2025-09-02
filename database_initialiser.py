from datetime import datetime
import os
import shutil
from constants import (
    DATABASE_STRUCTURE,
    DATABASE_STRUCTURE_CREATIONSTRINGMAPPING,
    SQLITEFILE,
)


import sqlite3


def check_database_structure(db_file):

    try:
        conn = sqlite3.connect(db_file)
        c = conn.cursor()

        c.execute("SELECT name FROM sqlite_master WHERE type='table';")
        actual_tables = {row[0] for row in c.fetchall()}
        missing = []
        for table, expected_columns in DATABASE_STRUCTURE.items():
            if table not in actual_tables:
                print(f"Missing table: {table}")
                missing.append({"type": "table", "table": table})
                for col in expected_columns:
                    missing.append({"type": "column", "table": table, "column": col})
                continue

            c.execute(f"PRAGMA table_info({table});")
            actual_columns = {row[1] for row in c.fetchall()}
            for col in expected_columns:
                if col not in actual_columns:
                    print(f"Missing column in {table}: {col}")
                    missing.append({"type": "column", "table": table, "column": col})
        extra, wrong_type = [], []
        for table in actual_tables:
            if table == "sqlite_sequence":
                continue
            if table not in DATABASE_STRUCTURE:
                extra.append({"type": "table", "table": table})
                print(f"Extra table: {table}")
                c.execute(f"PRAGMA table_info({table});")
                table_info = c.fetchall()
                actual_columns = {row[1] for row in table_info}
                for col in actual_columns:
                    extra.append({"type": "column", "table": table, "column": col})
            elif table in DATABASE_STRUCTURE:
                expected_columns = DATABASE_STRUCTURE[table]
                c.execute(f"PRAGMA table_info({table});")
                table_info = c.fetchall()
                actual_columns = {row[1]: row[2] for row in table_info}
                for col, type in actual_columns.items():
                    if col not in expected_columns:
                        print(f"Extra column in {table}: {col}")
                        extra.append({"type": "column", "table": table, "column": col})
                    else:
                        colmap = DATABASE_STRUCTURE_CREATIONSTRINGMAPPING[table]

                        if col not in colmap:
                            continue
                        expected_type = DATABASE_STRUCTURE_CREATIONSTRINGMAPPING[table][
                            col
                        ].split(" ")[0]
                        if type.upper() != expected_type.upper():
                            wrong_type.append(
                                {
                                    "table": table,
                                    "column": col,
                                    "type": type,
                                    "expected_type": expected_type,
                                }
                            )

        return missing, extra, wrong_type
    except sqlite3.Error as e:
        print(f"SQLite error: {e}")
        return False
    finally:
        conn.close()


def _build_table_string(table):
    sqlstring = f"CREATE TABLE {table} ({DATABASE_STRUCTURE_CREATIONSTRINGMAPPING['Tables'][table]}"
    items = DATABASE_STRUCTURE_CREATIONSTRINGMAPPING[table].items()
    for coltitle, coltype in items:
        if coltitle == "foreignkeyconstraint":
            sqlstring += f", {coltype}"
            continue
        sqlstring += f", {coltitle} {coltype}"
    sqlstring += ");"
    return sqlstring


def repair_db(reduced_missing, reduced_extra):
    conn = sqlite3.connect(SQLITEFILE)
    shutil.copy(SQLITEFILE, f"backup/{datetime.now().timestamp()}_{SQLITEFILE}")
    for missed in reduced_missing:
        c = conn.cursor()
        table = missed["table"]
        match missed["type"]:
            case "table":
                sqlstring = _build_table_string(table)
                c.execute(sqlstring)
                conn.commit()
                print(f"added {table}")
            case "column":
                col = missed["column"]
                sqlstring = f"ALTER TABLE {table} ADD COLUMN {col} {DATABASE_STRUCTURE_CREATIONSTRINGMAPPING[table][col]};"
                c.execute(sqlstring)
                conn.commit()
                print(f"added {col} to {table}")

    for ext in reduced_extra:
        c = conn.cursor()
        table = ext["table"]
        match ext["type"]:
            case "table":
                sqlstring = f"DROP TABLE {table};"
                c.execute(sqlstring)
                conn.commit()
                print(f"dropped {table}")
            case "column":
                col = ext["column"]
                sqlstring = f"ALTER TABLE {table} DROP COLUMN {col};"
                c.execute(sqlstring)
                conn.commit
                print(f"dropped {col} in {table}")
    conn.commit()
    conn.close()


def _reduce(list):
    skip, reduced_list = [], []
    for missed in list:
        if missed["type"] == "table":
            skip.append(missed["table"])
        elif missed["table"] in skip:
            continue
        reduced_list.append(missed)

    return reduced_list


def init_db():
    missing, extra, wrong_type = check_database_structure(SQLITEFILE)

    reduced_missing, reduced_extra = _reduce(missing), _reduce(extra)
    if not os.path.exists("backup"):
        os.makedirs("backup")
    if len(missing) + len(extra) > 0:
        repair_db(reduced_missing, reduced_extra)
    if len(wrong_type) > 0:
        print(wrong_type)
        if input(
            "wrong prefered column types detected do you want to repair typestructure(yes/NO):"
        ).lower() in ["y", "yes"]:
            conn = sqlite3.connect(SQLITEFILE)
            shutil.copy(SQLITEFILE, f"backup/{datetime.now().timestamp()}_{SQLITEFILE}")
            c = conn.cursor()
            for table in list({entry["table"] for entry in wrong_type}):
                c.execute(f"ALTER TABLE {table} RENAME TO old_{table}")
                conn.commit()
                c.execute(_build_table_string(table))
                conn.commit()
                c.execute(f"INSERT INTO {table} SELECT * from old_{table}")
                conn.commit()
                c.execute(f"DROP TABLE old_{table}")
                conn.commit()
            conn.close()
