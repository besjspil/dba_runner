"""
dba_runner.py - run SQL scripts across a set of Oracle databases (thin mode).

Requires:  pip install oracledb
Edit config.py to change environments, TNS aliases, paths and behaviour.
"""
import getpass
import re
import sys
import time
from decimal import Decimal
from pathlib import Path

import oracledb

import config

# Thin mode is the default in python-oracledb (no Oracle client, no ssh).
# Return CLOBs as plain strings so they print nicely.
oracledb.defaults.fetch_lobs = False

# --------------------------------------------------------------------------
# Small prompt helpers
# --------------------------------------------------------------------------
class Quit(Exception):
    pass


def ask(prompt, default=None):
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    if val.lower() in ("q", "quit", "exit"):
        raise Quit
    return val or (default or "")


def parse_selection(text, count):
    """'1,3,5-7' -> [0, 2, 4, 5, 6]. Empty or 'a' -> all."""
    text = text.strip().lower()
    if text in ("", "a", "all"):
        return list(range(count))
    picked = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            picked.extend(range(int(lo) - 1, int(hi)))
        elif part:
            picked.append(int(part) - 1)
    if any(i < 0 or i >= count for i in picked):
        raise ValueError("selection out of range")
    return list(dict.fromkeys(picked))  # de-dupe, keep order


def choose_one(title, options):
    """options: list of (key, display). Returns chosen key."""
    print(f"\n{title}")
    for i, (_, disp) in enumerate(options, 1):
        print(f"  {i:>2}) {disp}")
    while True:
        raw = ask("Choose number (q to quit)")
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx][0]
        except ValueError:
            pass
        print("  Invalid choice.")


# --------------------------------------------------------------------------
# SQL script parsing
# --------------------------------------------------------------------------
# SQL*Plus-only commands we can't run through the driver; they are skipped.
SQLPLUS_CMD = re.compile(
    r"^\s*(?:(?:set|spool|prompt|col|column|define|undefine|accept|whenever|"
    r"clear|ttitle|btitle|break|compute|exit|quit|rem|remark|show|host|pause|"
    r"timing|store|variable|var|print)\b|@)",
    re.I,
)
PLSQL_START = re.compile(
    r"^\s*(?:begin|declare|create\s+(?:or\s+replace\s+)?(?:editionable\s+|noneditionable\s+)?"
    r"(?:function|procedure|package|trigger|type))\b",
    re.I,
)


def split_statements(text):
    """Split a script into statements. ';' ends SQL, '/' on its own line ends PL/SQL.
    Returns (statements, skipped_sqlplus_lines)."""
    stmts, skipped, buf, in_plsql = [], [], [], False
    for line in text.splitlines():
        s = line.strip()
        if not buf:
            if not s or s.startswith("--"):
                continue
            if SQLPLUS_CMD.match(line):
                skipped.append(s)
                continue
            in_plsql = bool(PLSQL_START.match(line))
        if s == "/":
            if buf:
                stmts.append("\n".join(buf).strip())
                buf, in_plsql = [], False
            continue
        buf.append(line)
        if not in_plsql and s.endswith(";") and not s.startswith("--"):
            stmts.append("\n".join(buf).strip().rstrip(";").rstrip())
            buf = []
    if buf:
        tail = "\n".join(buf).strip()
        if tail:
            stmts.append(tail if in_plsql else tail.rstrip(";").rstrip())
    return stmts, skipped


def apply_substitutions(text):
    """Prompt for &var / &&var and substitute (like SQL*Plus)."""
    names = list(dict.fromkeys(re.findall(r"&&?(\w+)", text)))
    if not names:
        return text
    print("\nScript uses substitution variables:")
    values = {n: ask(f"  Value for &{n}") for n in names}
    return re.sub(r"&&?(\w+)\.?", lambda m: values[m.group(1)], text)


def first_keyword(stmt):
    m = re.match(r"\s*(?:/\*.*?\*/\s*|--[^\n]*\n\s*)*(\w+)", stmt, re.S)
    return m.group(1).lower() if m else ""


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
def fmt(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.hex()
    s = str(value).replace("\r", " ").replace("\n", " ")
    if len(s) > config.MAX_COL_WIDTH:
        s = s[: config.MAX_COL_WIDTH - 3] + "..."
    return s


def print_table(cols, rows):
    numeric = [
        bool(rows) and all(isinstance(r[i], (int, float, Decimal)) or r[i] is None for r in rows)
        for i in range(len(cols))
    ]
    text_rows = [[fmt(v) for v in r] for r in rows]
    widths = [max(len(c), *(len(r[i]) for r in text_rows)) if text_rows else len(c)
              for i, c in enumerate(cols)]

    def line(cells):
        return "  ".join(
            c.rjust(w) if numeric[i] else c.ljust(w)
            for i, (c, w) in enumerate(zip(cells, widths))
        )

    print(line(cols))
    print("  ".join("-" * w for w in widths))
    for r in text_rows:
        print(line(r))


def drain_dbms_output(conn):
    try:
        cur = conn.cursor()
        line_var, status_var = cur.var(str), cur.var(int)
        while True:
            cur.callproc("dbms_output.get_line", (line_var, status_var))
            if status_var.getvalue() != 0:
                break
            print(line_var.getvalue())
        cur.close()
    except oracledb.Error:
        pass


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------
def connect(alias, user, password):
    kwargs = dict(
        user=user,
        password=password,
        dsn=alias,
        tcp_connect_timeout=config.CONNECT_TIMEOUT_SEC,
    )
    if config.TNS_ADMIN:
        kwargs["config_dir"] = str(config.TNS_ADMIN)
    return oracledb.connect(**kwargs)


def run_statements(conn, statements):
    """Returns (ok, wrote) - ok False if a statement failed."""
    cur = conn.cursor()
    cur.arraysize = config.FETCH_ARRAY_SIZE
    wrote = False
    if config.ENABLE_DBMS_OUTPUT:
        try:
            cur.callproc("dbms_output.enable", [None])
        except oracledb.Error:
            pass

    for n, stmt in enumerate(statements, 1):
        kw = first_keyword(stmt)
        if kw not in config.READ_ONLY_KEYWORDS:
            wrote = True
        try:
            cur.execute(stmt)
            if cur.description:
                cols = [d[0] for d in cur.description]
                rows = cur.fetchmany(config.MAX_ROWS + 1)
                truncated = len(rows) > config.MAX_ROWS
                print_table(cols, rows[: config.MAX_ROWS])
                shown = min(len(rows), config.MAX_ROWS)
                print(f"\n{shown} row(s) shown" + (f" (truncated at MAX_ROWS={config.MAX_ROWS})" if truncated else ""))
            else:
                print(f"Statement {n} OK" + (f" ({cur.rowcount} row(s) affected)" if cur.rowcount and cur.rowcount > 0 else ""))
            if config.ENABLE_DBMS_OUTPUT:
                drain_dbms_output(conn)
            print()
        except oracledb.DatabaseError as e:
            print(f"ERROR in statement {n}: {e}\n")
            if config.STOP_ON_ERROR:
                cur.close()
                return False, wrote
    cur.close()
    return True, wrote


def run_script_on_alias(alias, user, password, statements):
    """Returns status string. Raises PermissionError on bad credentials."""
    banner = f" {alias} "
    print("\n" + "=" * 80 + f"\n{banner:=^80}\n" + "=" * 80)
    start = time.time()
    try:
        conn = connect(alias, user, password)
    except oracledb.DatabaseError as e:
        msg = str(e)
        print(f"CONNECT FAILED: {msg}")
        if "ORA-01017" in msg:
            raise PermissionError("Invalid username/password")
        return "CONNECT FAILED"
    try:
        if config.CALL_TIMEOUT_MS:
            conn.call_timeout = config.CALL_TIMEOUT_MS
        ok, wrote = run_statements(conn, statements)
        if wrote:
            ans = input(f"Non-SELECT statements ran on {alias}. Commit? [y/N]: ").strip().lower()
            if ans == "y":
                conn.commit()
                print("Committed.")
            else:
                conn.rollback()
                print("Rolled back.")
        return ("OK" if ok else "ERROR") + f" ({time.time() - start:.1f}s)"
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Main flow
# --------------------------------------------------------------------------
def pick_script():
    sql_dir = Path(config.SQL_DIR)
    if not sql_dir.is_dir():
        raise SystemExit(f"SQL_DIR not found: {sql_dir}")
    files = sorted(sql_dir.rglob("*.sql"), key=lambda p: str(p).lower())
    if not files:
        raise SystemExit(f"No .sql files in {sql_dir}")
    opts = [(f, str(f.relative_to(sql_dir))) for f in files]
    return choose_one(f"SQL scripts in {sql_dir}", opts)


def pick_aliases(env_cfg):
    aliases = env_cfg["aliases"]
    print("\nDatabases:")
    for i, a in enumerate(aliases, 1):
        print(f"  {i:>2}) {a}")
    while True:
        raw = ask("Run against (Enter = all, or e.g. 1,3,5-7)")
        try:
            return [aliases[i] for i in parse_selection(raw, len(aliases))]
        except ValueError:
            print("  Invalid selection.")


def main():
    print("Oracle DBA script runner (python-oracledb thin mode)")
    print("Type 'q' at any prompt to quit.\n")
    user = ask("Username", config.DEFAULT_USERNAME)
    password = getpass.getpass("Password: ")

    while True:
        env_key = choose_one(
            "Environment:",
            [(k, f"{k}  -  {v['label']} ({len(v['aliases'])} dbs)") for k, v in config.ENVIRONMENTS.items()],
        )
        env_cfg = config.ENVIRONMENTS[env_key]
        script = pick_script()
        aliases = pick_aliases(env_cfg)

        text = script.read_text(encoding="utf-8", errors="replace")
        if config.ENABLE_SUBSTITUTION:
            text = apply_substitutions(text)
        statements, skipped = split_statements(text)
        if skipped:
            print(f"\nNote: skipped {len(skipped)} SQL*Plus-only line(s), e.g. '{skipped[0]}'")
        if not statements:
            print("No executable statements found.")
            continue

        non_select = [s for s in statements if first_keyword(s) not in config.READ_ONLY_KEYWORDS]
        if non_select and env_cfg.get("confirm_non_select"):
            print(f"\nWARNING: {script.name} contains {len(non_select)} non-SELECT statement(s):")
            for s in non_select[:5]:
                print("   " + s.splitlines()[0][:100])
            if ask(f"Type the environment name ({env_key}) to proceed") != env_key:
                print("Cancelled.")
                continue

        print(f"\nRunning {script.name} ({len(statements)} statement(s)) on {len(aliases)} database(s) in {env_key.upper()}")
        results = []
        try:
            for alias in aliases:
                results.append((alias, run_script_on_alias(alias, user, password, statements)))
        except PermissionError as e:
            print(f"\n{e} - aborting to avoid locking the account. Re-enter credentials.")
            password = getpass.getpass("Password: ")
        except KeyboardInterrupt:
            print("\nInterrupted.")

        if results:
            print("\n" + "=" * 80 + "\nSUMMARY\n" + "=" * 80)
            for alias, status in results:
                print(f"  {alias:<15} {status}")

        if ask("\nRun another? [Y/n]", "y").lower() != "y":
            break


if __name__ == "__main__":
    try:
        main()
    except (Quit, KeyboardInterrupt):
        print("\nBye.")
        sys.exit(0)
