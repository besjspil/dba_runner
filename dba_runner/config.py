"""
config.py - everything you are likely to want to edit lives here.

To add a new environment:  add a new entry to ENVIRONMENTS.
To add a TNS alias:        append it to the relevant "aliases" list.
"""
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
# Folder scanned (recursively) for *.sql scripts
SQL_DIR = Path(
    r"C:\Users\jspillett\OneDrive - cmcmarkets.com\Personal Drive\python_scripts\sql"
)

# Folder containing tnsnames.ora. Thin mode does NOT use an Oracle client, so it
# reads tnsnames.ora directly. Leave as None to fall back to the TNS_ADMIN
# environment variable.
TNS_ADMIN = None  # e.g. r"C:\oracle\network\admin"

# --------------------------------------------------------------------------
# Environments  (menu is built from this dict, in this order)
# --------------------------------------------------------------------------
#   key                  -> what you type / see in the menu
#   label                -> friendly name
#   aliases              -> TNS aliases to run against
#   confirm_non_select   -> if True, you must confirm before any statement that
#                           is not a SELECT/WITH is executed in this environment
ENVIRONMENTS = {
    "prod": {
        "label": "Production",
        "confirm_non_select": True,
        "aliases": [
            "PROD_REF",
            "PROD_EDW",
            "PROD_NDM",
            "PROD_DWH",
            "PROD_OEM13",
            "PROD_RMAN",
            "PROD_EBS",
            "PROD_HYP",
            "PROD_QUA",
            "PROD_JIRA",
        ],
    },
    "nfr": {
        "label": "NFR / Non-production",
        "confirm_non_select": False,
        "aliases": [
            "NFR_REF",
            "NFR_EDW",
            "NFR_NDM",
            "TEST_OEM13",
            "TEST_RMAN",
        ],
    },
    "poc": {
        "label": "POC",
        "confirm_non_select": False,
        "aliases": [
            "POC_OCI_JDS",
        ],
    },    
    
    
    
    # Example of adding another environment:
    # "dr": {
    #     "label": "DR",
    #     "confirm_non_select": True,
    #     "aliases": ["DR_REF", "DR_EDW"],
    # },
}

# --------------------------------------------------------------------------
# Behaviour
# --------------------------------------------------------------------------
DEFAULT_USERNAME = None      # e.g. "jspillett"  (None = no default, always type it)
CONNECT_TIMEOUT_SEC = 15     # TCP connect timeout per database
CALL_TIMEOUT_MS = 0          # per-statement timeout in ms (0 = none)
MAX_ROWS = 500               # rows shown per query (extra rows are truncated)
FETCH_ARRAY_SIZE = 200       # driver fetch batch size
MAX_COL_WIDTH = 60           # long values are truncated to this on screen
STOP_ON_ERROR = True         # stop the script on a database after a failed statement
ENABLE_SUBSTITUTION = True   # prompt for &var / &&var values found in scripts
ENABLE_DBMS_OUTPUT = True    # print DBMS_OUTPUT after each statement
READ_ONLY_KEYWORDS = {"select", "with"}   # anything else counts as "non-select"
