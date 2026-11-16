"""Background learning for sentinel: per-door baselines, drift, trends and anomalies.

Runs nightly as its own low-priority job (``--learn-now``), never inside the polling
service. It reads only the tamper-evident ledger, writes only into its own folders
(``ml`` state and the ML-Reports folder), and its output is advisory: it never touches
the daily logs, the ledger, an alarm or the PLC. Heavy libraries (numpy, scikit-learn)
are imported only by the modules that need them, so the main service never loads them.
"""
