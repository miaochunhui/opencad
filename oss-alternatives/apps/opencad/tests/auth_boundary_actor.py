"""Revoke only generated test sessions from an independent SQLite process.

This fixture worker requires a database under OPENCAD_TEST_RUNTIME_ROOT. It
never takes passwords/tokens, opens production data or reports session hashes.
BUSY is a measured lock outcome, not a swallowed storage failure.
"""

import argparse
import json
import os
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    runtime = os.environ.get("OPENCAD_TEST_RUNTIME_ROOT")
    if not runtime:
        raise ValueError("An explicit local test runtime is required")
    database = args.database.absolute()
    root = Path(runtime).resolve()
    if (not database.resolve().is_relative_to(root) or database.is_symlink()
            or not database.is_file() or database.name != "opencad-auth.sqlite3"):
        raise ValueError("Refusing a database outside the generated test fixture")
    with sqlite3.connect(database, timeout=0, isolation_level=None) as db:
        try:
            db.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", None)
            # Python 3.10 lacks sqlite_errorcode and SQLITE_BUSY constants.
            if code == 5 or (code is None and str(exc) == "database is locked"):
                print(json.dumps({"state": "BUSY", "changed": 0}, sort_keys=True))
                return 0
            raise
        try:
            row = db.execute("SELECT user_id FROM users WHERE username=?",
                             (args.username,)).fetchone()
            if row is None:
                raise ValueError("Generated test account does not exist")
            changed = db.execute(
                "UPDATE sessions SET revoked=1 WHERE user_id=? AND revoked=0",
                (row[0],)).rowcount
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
    print(json.dumps({"state": "REVOKED", "changed": changed}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
