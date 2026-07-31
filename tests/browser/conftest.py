# These are standalone scripts that drive a real browser against a running
# server, not pytest cases. Their names match test_*.py, so without this pytest
# imports them during collection and executes a full browser run — and then
# dies on their sys.exit(). Run them with tests/browser/run_all.py instead.
collect_ignore_glob = ["*.py"]
