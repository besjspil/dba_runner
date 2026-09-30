# How `dba_runner.py` works

The script has six parts, in this order in the file. Small parts are used by bigger ones, and `main()` at the bottom ties them together.

## 1. Imports and setup

```python
import getpass, re, sys, time
from decimal import Decimal
from pathlib import Path
import oracledb
import config

oracledb.defaults.fetch_lobs = False
```

- `getpass` reads your password without showing it on screen.
- `re` is regular expressions, used for pattern-matching text (finding `&variables`, spotting PL/SQL blocks).
- `time` is used for timing each database.
- `Path` handles file paths and finding `.sql` files.
- `oracledb` is the Oracle driver. It runs in thin mode by default, so there is no Oracle client and no SSH.
- `import config` pulls in your settings file. Elsewhere you'll see `config.MAX_ROWS`, which means "the `MAX_ROWS` value from config.py".
- The `fetch_lobs = False` line makes big text columns (CLOBs) come back as normal Python strings, so they print properly.

## 2. Prompt helpers

These small functions handle asking you questions.

```python
class Quit(Exception):
    pass
```
This is a custom "signal". When you type `q`, the code raises `Quit`, which unwinds out of whatever it was doing and back to the very bottom of the file, where it is caught and the script exits cleanly.

```python
def ask(prompt, default=None):
    val = input(f"{prompt}{suffix}: ").strip()
    if val.lower() in ("q", "quit", "exit"):
        raise Quit
    return val or (default or "")
```
`ask` is a wrapper around `input()`. It shows a default in `[brackets]` if there is one, checks whether you typed `q`, and returns your answer. The `val or (default or "")` part means "if you typed nothing, use the default".

```python
def parse_selection(text, count):   # '1,3,5-7' -> [0, 2, 4, 5, 6]
```
This turns what you type for database selection into a list of positions. It splits on commas, expands ranges like `5-7`, and subtracts 1 because Python counts from 0 while the menu counts from 1. Empty input or `a` means everything. If a number is out of range it raises `ValueError`, which the caller catches.

```python
def choose_one(title, options):
```
This is the reusable numbered menu. It prints the options, loops until you enter a valid number, and returns the item you picked. The environment menu and the script menu both use it, which is why adding an environment to `config.py` needs no code changes.

## 3. SQL script parsing

The driver runs one statement at a time, but a `.sql` file can contain many. This section splits the file up.

```python
SQLPLUS_CMD = re.compile(r"^\s*(?:(?:set|spool|prompt|...)\b|@)", re.I)
PLSQL_START = re.compile(r"^\s*(?:begin|declare|create ... procedure ...)", re.I)
```
These are two patterns. The first spots SQL*Plus-only commands (`SET`, `SPOOL`, `@file`) that the driver can't run. The second spots the start of a PL/SQL block (`BEGIN`, `DECLARE`, `CREATE PROCEDURE`, and so on). `re.I` means case-insensitive.

```python
def split_statements(text):
```
This is the core splitter. It reads the script line by line and collects lines in a list called `buf`:

- Blank lines and `--` comments at the start of a statement are ignored.
- SQL*Plus commands are put in a `skipped` list.
- A normal statement ends when a line finishes with `;`.
- A PL/SQL block ignores `;` (they appear inside it) and only ends on a line containing just `/`.
- It returns two lists: the statements to run and the skipped lines.

```python
def apply_substitutions(text):
```
This finds `&name` or `&&name` in the script, asks you for a value for each one, and swaps the values in. It works like SQL*Plus.

```python
def first_keyword(stmt):
```
This returns the first word of a statement in lowercase (`select`, `update`, `alter`), skipping any leading comments. The safety check later uses it to decide whether a statement is read-only.

## 4. Output formatting

```python
def fmt(value):
```
This converts one cell to text for display. `None` becomes blank, newlines are flattened to spaces, and anything longer than `MAX_COL_WIDTH` is cut and ends in `...`.

```python
def print_table(cols, rows):
```
This prints query results as aligned columns:

1. It works out which columns are numeric so they can be right-aligned.
2. It works out the width of each column (the longest of the heading and the values).
3. It prints the header, a row of dashes, then each data row.

```python
def drain_dbms_output(conn):
```
PL/SQL's `DBMS_OUTPUT.PUT_LINE` doesn't print directly. It sits in a buffer inside the database. This function repeatedly calls `dbms_output.get_line` and prints each line until the buffer is empty. It sits inside `try/except` so a missing privilege doesn't crash anything.

## 5. Execution (talking to the database)

```python
def connect(alias, user, password):
    kwargs = dict(user=user, password=password, dsn=alias, tcp_connect_timeout=...)
    if config.TNS_ADMIN:
        kwargs["config_dir"] = str(config.TNS_ADMIN)
    return oracledb.connect(**kwargs)
```
This builds a dictionary of connection settings and passes it to `oracledb.connect`. The `**kwargs` syntax unpacks the dictionary into named arguments. The `dsn=alias` is your TNS alias, such as `PROD_REF`. The `config_dir` is only added if you set `TNS_ADMIN`, so the driver knows where `tnsnames.ora` is.

```python
def run_statements(conn, statements):
```
This runs each statement on one open connection. In outline:

1. It creates a cursor (the object that executes SQL and holds results).
2. It turns on `DBMS_OUTPUT`.
3. For each statement it works out whether it is a write (anything not `select` or `with`) and remembers that in `wrote`.
4. It calls `cur.execute(stmt)`.
5. If `cur.description` exists, the statement returned columns, so it fetches up to `MAX_ROWS + 1` rows. The extra one tells it whether results were truncated. It then prints the table.
6. Otherwise it prints "Statement N OK" plus the affected row count.
7. If a statement fails, the `except` block prints the error. With `STOP_ON_ERROR = True` it returns early.
8. It returns `(ok, wrote)`: did everything succeed, and did anything modify data.

```python
def run_script_on_alias(alias, user, password, statements):
```
This is the wrapper for one database. It prints the banner, connects, calls `run_statements`, and handles commit or rollback if anything was written. Two points are worth knowing:

- If connecting fails with `ORA-01017` (bad password), it raises `PermissionError`. `main()` catches this and stops, so it doesn't try the wrong password on every remaining database and lock your account.
- `try: ... finally: conn.close()` guarantees the connection is closed even if something goes wrong halfway.

## 6. Main flow

```python
def pick_script():
```
This finds all `.sql` files under `SQL_DIR` with `rglob("*.sql")`, sorts them, and shows them through `choose_one`. It displays paths relative to the SQL folder. The file list is rebuilt each time, so new scripts appear without restarting.

```python
def pick_aliases(env_cfg):
```
This shows the databases in the chosen environment and asks which to run against. It loops until `parse_selection` accepts your input.

```python
def main():
```
This is the conductor. It:

1. Asks for username and password once.
2. Starts a `while True` loop, so you can run script after script.
3. Inside the loop it picks the environment, script and databases.
4. It reads the file, prompts for `&variables`, then splits it into statements.
5. It counts the non-SELECT statements. If any exist and the environment has `confirm_non_select`, you must type the environment name to continue.
6. It loops over the chosen databases calling `run_script_on_alias`, and collects a `(alias, status)` result for each.
7. It prints the summary, then asks whether to go again.

The `except` blocks around the database loop handle a bad password (re-prompt) and Ctrl+C (stop cleanly).

## 7. Entry point

```python
if __name__ == "__main__":
    try:
        main()
    except (Quit, KeyboardInterrupt):
        print("\nBye.")
```
`if __name__ == "__main__"` means "only run this if the file is executed directly, not if it's imported by something else". Here it also catches `Quit` (you typed `q`) and Ctrl+C, so you get a tidy exit instead of an error message.
