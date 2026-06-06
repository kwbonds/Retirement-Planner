"""
Retirement Planner Streamlit App
SQLite-backed manual retirement dashboard

Key features:
- Manual account, balance, and contribution entry
- Inline editing for balances and contributions
- Retirement projection with:
  - pre-retirement contributions
  - explicit cash-target contribution routing
  - cash cap + overflow-to-brokerage logic
  - retirement withdrawals
  - Roth conversions and estimated conversion taxes
  - optional Social Security
  - optional market downturn and layoff/pause scenarios

Run:
  pip install streamlit pandas plotly
  streamlit run app.py

This app stores data locally in retirement_planner.db in the same folder.
"""

from __future__ import annotations
from datetime import datetime

import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Dict

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

DB_PATH = Path("retirement_planner.db")




def backup_database(reason: str = "manual") -> Path | None:
    """Create a timestamped backup copy of the local SQLite database.

    The backup is written to a local ./backups folder next to the app.
    Returns the backup path, or None if the database does not exist yet.
    """
    if not DB_PATH.exists():
        return None

    backup_dir = Path("backups")
    backup_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_reason = "".join(ch for ch in reason.lower().replace(" ", "_") if ch.isalnum() or ch == "_") or "manual"
    backup_path = backup_dir / f"retirement_planner_backup_{safe_reason}_{timestamp}.db"
    shutil.copy2(DB_PATH, backup_path)
    return backup_path


def uploaded_file_is_sqlite_db(uploaded_file) -> bool:
    """Basic validation for an uploaded SQLite database backup."""
    try:
        uploaded_file.seek(0)
        header = uploaded_file.read(16)
        uploaded_file.seek(0)
        return header == b"SQLite format 3\x00"
    except Exception:
        return False


def restore_database_from_upload(uploaded_file) -> tuple[bool, str, Path | None]:
    """Replace the active database with an uploaded SQLite backup.

    Creates a timestamped safety backup of the current DB before replacing it.
    Returns success flag, message, and the safety backup path if created.
    """
    if uploaded_file is None:
        return False, "No file uploaded.", None

    if not uploaded_file_is_sqlite_db(uploaded_file):
        return False, "Uploaded file does not look like a valid SQLite database backup.", None

    safety_backup = backup_database("before_restore")

    restore_dir = Path("restored_uploads")
    restore_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    uploaded_copy = restore_dir / f"uploaded_retirement_planner_restore_{timestamp}.db"

    uploaded_file.seek(0)
    uploaded_copy.write_bytes(uploaded_file.read())

    # Quick integrity check before replacing the live DB.
    try:
        with sqlite3.connect(uploaded_copy) as conn:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            return False, f"Uploaded database failed integrity check: {result}", safety_backup
    except Exception as exc:
        return False, f"Uploaded database could not be opened: {exc}", safety_backup

    shutil.copy2(uploaded_copy, DB_PATH)
    return True, f"Database restored from upload. Uploaded copy saved to {uploaded_copy}.", safety_backup

# -----------------------------
# Database helpers
# -----------------------------

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                account_type TEXT NOT NULL,
                tax_bucket TEXT NOT NULL,
                owner TEXT,
                target_balance REAL DEFAULT 0,
                notes TEXT,
                is_active INTEGER DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS balances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                balance REAL NOT NULL,
                as_of_date TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                frequency TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT,
                notes TEXT,
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assumptions (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS roth_conversion_scenarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                start_age INTEGER NOT NULL,
                end_age INTEGER NOT NULL,
                strategy TEXT NOT NULL,
                annual_amount REAL DEFAULT 0,
                target_bracket TEXT DEFAULT '22%',
                pay_tax_from TEXT DEFAULT 'Cash then Taxable',
                other_taxable_income REAL DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                notes TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS planned_purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                purchase_age INTEGER NOT NULL,
                amount REAL NOT NULL,
                funding_strategy TEXT NOT NULL DEFAULT 'Cash then Taxable then Tax-deferred then Roth',
                include_in_projection INTEGER DEFAULT 1,
                notes TEXT
            )
            """
        )
        conn.commit()


def seed_defaults() -> None:
    # Public-repo-safe demo defaults. Existing personal databases are not overwritten
    # because assumptions are inserted with INSERT OR IGNORE. Keep real numbers in
    # retirement_planner.db, which should be excluded from git.
    defaults = {
        "current_age": "50",
        "spouse_age": "50",
        "retirement_age": "60",
        "years_to_project": "35",
        "annual_spend_retirement": "100000",
        "inflation_rate": "0.025",
        "stock_return": "0.07",
        "bond_cash_return": "0.035",
        "tax_rate_on_roth_conversion": "0.12",
        "standard_deduction": "32200",
        "mfj_10_bracket_top": "24800",
        "mfj_12_bracket_top": "100800",
        "mfj_22_bracket_top": "211400",
        "mfj_24_bracket_top": "403550",
        "mfj_32_bracket_top": "512450",
        "mfj_35_bracket_top": "768700",
        "estimate_future_tax_rate_without_conversions": "0.22",
        "base_taxable_income_retirement": "0",
        "rmd_start_age": "73",
        "taxable_cost_basis_ratio": "0.70",
        "capital_gains_rate": "0.15",
        "state_tax_rate": "0.00",
        "retirement_withdrawal_rate": "0.0425",
        "annual_roth_conversion": "0",
        "roth_conversion_start_age": "60",
        "roth_conversion_end_age": "72",
        "pay_roth_conversion_tax_from_portfolio": "1",
        "social_security_age": "67",
        "social_security_annual": "0",
        "bridge_target": "100000",
        "checking_buffer_target": "25000",
        "cash_cap_enabled": "1",
        "use_routed_cashflow": "1",
        "cash_account_name": "Cash Bridge Savings",
        "overflow_account_name": "Taxable Brokerage",
        "paychecks_per_year": "26",
        "brokerage_per_paycheck_before_cash_full": "0",
        "brokerage_per_paycheck_after_cash_full": "0",
        "cash_per_paycheck_until_full": "0",
        "extra_cash_per_quarter_until_full": "0",
        "send_extra_cash_to_brokerage_after_full": "0",
        "apply_market_downturn_scenario": "0",
        "market_downturn_percent": "0.25",
        "market_downturn_age": "60",
        "apply_layoff_pause_scenario": "0",
        "layoff_pause_months": "6",
        "extend_retirement_for_layoff": "0",
        "dashboard_roth_scenario_name": "No conversions",
        "roth_end_at_ss_age": "1",
        "show_purchase_impact_on_dashboard_chart": "1",
        "dashboard_purchase_funding_override": "Auto practical candidate",
        "dashboard_purchase_age_override_enabled": "0",
        "dashboard_purchase_age_override": "0",
        "show_dashboard_today_dollars": "0",
        "lifetime_comparison_age": "85",
        "aca_full_annual_premium": "18000",
        "aca_benchmark_silver_annual_premium": "26000",
        "aca_selected_plan_annual_premium": "22000",
        "aca_model_mode": "Formula",
        "aca_use_400_fpl_cliff": "1",
        "aca_include_oop_estimate": "0",
        "aca_oop_if_under_target": "3000",
        "aca_oop_if_over_target": "6000",

        "aca_subsidized_annual_premium": "6000",
        "irmaa_magi_threshold": "206000",
        "irmaa_annual_surcharge": "0",

        "aca_household_size": "2",
        "aca_fpl_100": "21150",
        "aca_target_fpl_percent": "400",
        "aca_magi_safety_buffer": "1000",
        "aca_medicare_age": "65",
        "aca_start_age": "auto",
        "aca_end_age": "auto",

    }
    with get_conn() as conn:
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO assumptions (key, value) VALUES (?, ?)",
                (key, value),
            )
        # v71 migration: v69/v70 introduced Dashboard purchase funding controls, but
        # existing databases may still have the old default "Saved purchase strategy".
        # For Dashboard what-if work, Auto practical is the intended default because it
        # updates funding as the purchase age changes.
        conn.execute(
            """
            UPDATE assumptions
            SET value = 'Auto practical candidate'
            WHERE key = 'dashboard_purchase_funding_override'
              AND value = 'Saved purchase strategy'
            """
        )
        conn.commit()

    accounts = read_accounts(active_only=False)
    if accounts.empty:
        starter_accounts = [
            ("Cash Bridge Savings", "Savings", "Cash", "Joint", 100000, "Dedicated early retirement bridge fund"),
            ("Checking Buffer", "Checking", "Cash", "Joint", 25000, "Operating cash buffer"),
            ("Taxable Brokerage", "Brokerage", "Taxable", "Joint", 0, "Taxable growth bucket"),
            ("Traditional Retirement Account", "401k", "Tax-deferred", "Joint", 0, "Combined traditional retirement account balances"),
        ]
        with get_conn() as conn:
            conn.executemany(
                """
                INSERT INTO accounts (name, account_type, tax_bucket, owner, target_balance, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                starter_accounts,
            )
            conn.commit()

    scenarios = read_roth_scenarios(active_only=False)
    if scenarios.empty:
        starter_scenarios = [
            ("No conversions", 60, 72, "No conversions", 0, "12%", "Cash then Taxable", 0, "Baseline comparison"),
            ("Fixed annual amount", 60, 72, "Fixed annual amount", 80000, "N/A", "Cash then Taxable", 0, "Configurable fixed annual conversion amount"),
            ("Fill 12% bracket", 60, 72, "Fill bracket", 0, "12%", "Cash then Taxable", 0, "Convert up to top of 12% taxable bracket; default end age is before RMDs"),
            ("Fill 22% bracket", 60, 72, "Fill bracket", 0, "22%", "Cash then Taxable", 0, "Convert up to top of 22% taxable bracket; default end age is before RMDs"),
            ("Hybrid ACA", 60, 78, "Hybrid ACA", 0, "N/A", "Cash then Taxable", 0, "ACA years stay under MAGI target; after Medicare, continue conversions to reduce RMD pressure."),
        ]
        with get_conn() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO roth_conversion_scenarios
                    (name, start_age, end_age, strategy, annual_amount, target_bracket, pay_tax_from, other_taxable_income, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                starter_scenarios,
            )
            conn.commit()

    # Migration for older local databases created by earlier versions of this app:
    # the starter Roth scenarios originally ended at age 65, which made the
    # taper visualization look like a hard stop. For the built-in starter
    # scenarios only, extend a still-default age-65 end date to the year before
    # RMDs begin. You can still edit End Age manually on the Roth Conversions page.
    default_taper_end_age = int(float(get_assumptions().get("rmd_start_age", 73))) - 1
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE roth_conversion_scenarios
            SET end_age = ?
            WHERE name IN ('Fixed $80k/year', 'Fixed annual amount', 'Fill 12% bracket', 'Fill 22% bracket')
              AND end_age = 65
            """,
            (default_taper_end_age,),
        )
        conn.execute(
            "UPDATE roth_conversion_scenarios SET target_bracket = 'N/A' WHERE strategy != 'Fill bracket'"
        )
        conn.execute(
            "UPDATE roth_conversion_scenarios SET name = 'Fixed annual amount' WHERE name = 'Fixed $80k/year' AND strategy = 'Fixed annual amount'"
        )
        # Add ACA optimizer scenario to existing local databases that were created
        # before this strategy existed. This does not overwrite user-edited scenarios.
        existing_aca = conn.execute(
            "SELECT COUNT(*) FROM roth_conversion_scenarios WHERE strategy = 'Optimize ACA' OR name = 'Optimize ACA'"
        ).fetchone()[0]
        if existing_aca == 0:
            retirement_default_age = int(float(get_assumptions().get("retirement_age", defaults.get("retirement_age", 60))))
            aca_default_end_age = int(float(get_assumptions().get("aca_medicare_age", defaults.get("aca_medicare_age", 65)))) - 1
            conn.execute(
                """
                INSERT INTO roth_conversion_scenarios
                    (name, start_age, end_age, strategy, annual_amount, target_bracket, pay_tax_from, other_taxable_income, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Optimize ACA",
                    retirement_default_age,
                    aca_default_end_age,
                    "Optimize ACA",
                    0,
                    "N/A",
                    "Cash then Taxable",
                    0,
                    "Convert only up to the editable ACA MAGI target during ACA years; useful for ACA subsidy planning.",
                ),
            )
        # Add Hybrid ACA scenario to existing local databases.
        # Existing Optimize ACA scenarios may have been created with End Age at
        # Medicare/ACA end, which made charts look like a hard stop. Extend the
        # built-in default scenario label/window while calculation continues
        # dynamically regardless.
        rmd_default_age_for_aca = int(float(get_assumptions().get("rmd_start_age", defaults.get("rmd_start_age", 73))))
        conn.execute(
            """
            UPDATE roth_conversion_scenarios
            SET end_age = MAX(end_age, ?)
            WHERE strategy = 'Optimize ACA' AND name = 'Optimize ACA'
            """,
            (max(rmd_default_age_for_aca + 5, 72),),
        )

        existing_hybrid_aca = conn.execute(
            "SELECT COUNT(*) FROM roth_conversion_scenarios WHERE strategy = 'Hybrid ACA' OR name = 'Hybrid ACA'"
        ).fetchone()[0]
        if existing_hybrid_aca == 0:
            retirement_default_age = int(float(get_assumptions().get("retirement_age", defaults.get("retirement_age", 60))))
            rmd_default_age = int(float(get_assumptions().get("rmd_start_age", defaults.get("rmd_start_age", 73))))
            conn.execute(
                """
                INSERT INTO roth_conversion_scenarios
                    (name, start_age, end_age, strategy, annual_amount, target_bracket, pay_tax_from, other_taxable_income, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Hybrid ACA",
                    retirement_default_age,
                    max(rmd_default_age + 5, 72),
                    "Hybrid ACA",
                    0,
                    "N/A",
                    "Cash then Taxable",
                    0,
                    "ACA years stay under MAGI target; after Medicare, continue 12% bracket fills, pre-RMD 22% cleanup, then taper in RMD years.",
                ),
            )
        conn.commit()


# -----------------------------
# Data access
# -----------------------------

def read_accounts(active_only: bool = True) -> pd.DataFrame:
    query = "SELECT * FROM accounts"
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY name"
    with get_conn() as conn:
        return pd.read_sql_query(query, conn)


def read_latest_balances() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(
            """
            SELECT
                a.id AS account_id,
                a.name,
                a.account_type,
                a.tax_bucket,
                a.owner,
                a.target_balance,
                b.balance,
                b.as_of_date
            FROM accounts a
            LEFT JOIN balances b
                ON b.id = (
                    SELECT b2.id
                    FROM balances b2
                    WHERE b2.account_id = a.id
                    ORDER BY b2.as_of_date DESC, b2.created_at DESC, b2.id DESC
                    LIMIT 1
                )
            WHERE a.is_active = 1
            ORDER BY a.name
            """,
            conn,
        )


def read_balance_history() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(
            """
            SELECT b.id, b.account_id, a.name, a.tax_bucket, b.balance, b.as_of_date, b.created_at
            FROM balances b
            JOIN accounts a ON a.id = b.account_id
            ORDER BY b.as_of_date, b.id
            """,
            conn,
            parse_dates=["as_of_date", "created_at"],
        )


def read_contributions(active_only: bool = True) -> pd.DataFrame:
    # LEFT JOIN keeps orphaned/legacy contribution rows visible so they can be fixed
    # in the inline editor instead of silently disappearing or showing a blank account.
    query = """
        SELECT
            c.*,
            COALESCE(NULLIF(a.name, ''), '[Missing account #' || c.account_id || ']') AS name,
            COALESCE(NULLIF(a.tax_bucket, ''), 'Unknown') AS tax_bucket,
            CASE WHEN a.id IS NULL OR a.name IS NULL OR TRIM(a.name) = '' THEN 1 ELSE 0 END AS account_missing
        FROM contributions c
        LEFT JOIN accounts a ON a.id = c.account_id
    """
    if active_only:
        query += " WHERE c.is_active = 1"
    query += " ORDER BY COALESCE(NULLIF(a.name, ''), '[Missing account #' || c.account_id || ']'), c.start_date, c.id"
    with get_conn() as conn:
        return pd.read_sql_query(query, conn, parse_dates=["start_date", "end_date"])


def read_roth_scenarios(active_only: bool = True) -> pd.DataFrame:
    query = "SELECT * FROM roth_conversion_scenarios"
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY id"
    with get_conn() as conn:
        return pd.read_sql_query(query, conn)


def save_roth_scenario_editor(df_editor: pd.DataFrame) -> None:
    cleaned = df_editor.copy()
    cleaned = cleaned.dropna(subset=["Name", "Start Age", "End Age", "Strategy"], how="any")
    valid_strategies = {"No conversions", "Fixed annual amount", "Fill bracket", "Optimize ACA", "Hybrid ACA"}
    valid_brackets = {"N/A", "10%", "12%", "22%", "24%", "32%", "35%"}
    valid_pay_sources = {"Cash then Taxable", "Taxable then Cash", "Cash only", "Taxable only", "Withhold from conversion"}

    rows = []
    for _, row in cleaned.iterrows():
        strategy = str(row["Strategy"])
        target_bracket = str(row.get("Target Bracket", "22%"))
        pay_tax_from = str(row.get("Pay Tax From", "Cash then Taxable"))
        if strategy not in valid_strategies:
            continue
        if strategy != "Fill bracket":
            # Target bracket is meaningful only for Fill bracket strategies.
            # Fixed annual amount scenarios convert the Annual Amount regardless
            # of which marginal bracket the conversion happens to reach.
            target_bracket = "N/A"
        elif target_bracket not in valid_brackets or target_bracket == "N/A":
            target_bracket = "12%"
        if pay_tax_from not in valid_pay_sources:
            pay_tax_from = "Cash then Taxable"

        existing_id = row.get("ID")
        rows.append((
            None if pd.isna(existing_id) else int(existing_id),
            str(row["Name"]),
            int(row["Start Age"]),
            int(row["End Age"]),
            strategy,
            float(row.get("Annual Amount", 0) or 0),
            target_bracket,
            pay_tax_from,
            float(row.get("Other Taxable Income", 0) or 0),
            1 if bool(row.get("Active", True)) else 0,
            "" if pd.isna(row.get("Notes")) else str(row.get("Notes")),
        ))

    with get_conn() as conn:
        conn.execute("DELETE FROM roth_conversion_scenarios")
        for existing_id, name, start_age, end_age, strategy, annual_amount, target_bracket, pay_tax_from, other_taxable_income, is_active, notes in rows:
            if existing_id:
                conn.execute(
                    """
                    INSERT INTO roth_conversion_scenarios
                        (id, name, start_age, end_age, strategy, annual_amount, target_bracket, pay_tax_from, other_taxable_income, is_active, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (existing_id, name, start_age, end_age, strategy, annual_amount, target_bracket, pay_tax_from, other_taxable_income, is_active, notes),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO roth_conversion_scenarios
                        (name, start_age, end_age, strategy, annual_amount, target_bracket, pay_tax_from, other_taxable_income, is_active, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (name, start_age, end_age, strategy, annual_amount, target_bracket, pay_tax_from, other_taxable_income, is_active, notes),
                )
        conn.commit()



PURCHASE_FUNDING_STRATEGIES = [
    "Cash then Taxable then Tax-deferred then Roth",
    "Taxable then Cash then Tax-deferred then Roth",
    "Cash only",
    "Taxable only",
    "Tax-deferred only",
    "Roth only",
]

PURCHASE_STRATEGY_BUCKETS = {
    "Cash then Taxable then Tax-deferred then Roth": ["Cash", "Taxable", "Tax-deferred", "Roth"],
    "Taxable then Cash then Tax-deferred then Roth": ["Taxable", "Cash", "Tax-deferred", "Roth"],
    "Cash only": ["Cash"],
    "Taxable only": ["Taxable"],
    "Tax-deferred only": ["Tax-deferred"],
    "Roth only": ["Roth"],
}


def read_planned_purchases(active_only: bool = True) -> pd.DataFrame:
    query = "SELECT * FROM planned_purchases"
    if active_only:
        query += " WHERE include_in_projection = 1"
    query += " ORDER BY purchase_age, id"
    with get_conn() as conn:
        return pd.read_sql_query(query, conn)


def save_purchase_editor(df_editor: pd.DataFrame) -> None:
    cleaned = df_editor.copy()
    cleaned = cleaned.dropna(subset=["Name", "Purchase Age", "Amount", "Funding Strategy"], how="any")
    rows = []
    for _, row in cleaned.iterrows():
        # The editor shows user-facing optimizer candidate names. Convert them back
        # to the internal funding strategy string stored in SQLite.
        strategy = candidate_label_to_strategy(str(row.get("Funding Strategy", "Cash first")))
        existing_id = row.get("ID")
        rows.append((
            None if pd.isna(existing_id) else int(existing_id),
            str(row["Name"]),
            int(row["Purchase Age"]),
            float(row.get("Amount", 0) or 0),
            strategy,
            1 if bool(row.get("Include", True)) else 0,
            "" if pd.isna(row.get("Notes")) else str(row.get("Notes")),
        ))

    with get_conn() as conn:
        conn.execute("DELETE FROM planned_purchases")
        for existing_id, name, purchase_age, amount, funding_strategy, include_in_projection, notes in rows:
            if existing_id:
                conn.execute(
                    """
                    INSERT INTO planned_purchases
                        (id, name, purchase_age, amount, funding_strategy, include_in_projection, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (existing_id, name, purchase_age, amount, funding_strategy, include_in_projection, notes),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO planned_purchases
                        (name, purchase_age, amount, funding_strategy, include_in_projection, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (name, purchase_age, amount, funding_strategy, include_in_projection, notes),
                )
        conn.commit()


def purchase_bucket_order(strategy: str) -> list[str]:
    return PURCHASE_STRATEGY_BUCKETS.get(str(strategy), PURCHASE_STRATEGY_BUCKETS[PURCHASE_FUNDING_STRATEGIES[0]])


def custom_mix_strategy(cash: float, taxable: float, tax_deferred: float, roth: float) -> str:
    """Create a compact strategy string for what-if optimizer mixes.

    Percentages are interpreted as target funding shares for the purchase.
    If a bucket does not have enough money, the shortfall is backfilled using
    Cash -> Taxable -> Tax-deferred -> Roth.
    """
    return (
        "Custom mix | "
        f"Cash={cash:.0f} | Taxable={taxable:.0f} | "
        f"Tax-deferred={tax_deferred:.0f} | Roth={roth:.0f}"
    )


def parse_custom_mix_strategy(strategy: str) -> dict[str, float] | None:
    text = str(strategy or "")
    if not text.startswith("Custom mix"):
        return None
    mix = {"Cash": 0.0, "Taxable": 0.0, "Tax-deferred": 0.0, "Roth": 0.0}
    for part in text.split("|"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if key in mix:
            try:
                mix[key] = max(float(value.strip()), 0.0)
            except ValueError:
                mix[key] = 0.0
    total = sum(mix.values())
    if total <= 0:
        return None
    return {k: v / total for k, v in mix.items()}


def purchase_draw_plan_from_current(current: pd.DataFrame, amount: float, funding_strategy: str) -> dict[str, float]:
    """Return purchase funding draws by bucket without mutating current.

    Supports both simple order strategies and custom percentage mixes used by
    the optimizer. The custom mix lets the app test blended funding choices,
    e.g. some taxable brokerage plus some tax-deferred money to reduce future
    RMD pressure without needlessly draining Roth.
    """
    amount = max(float(amount), 0.0)
    remaining = amount
    draws = {"Cash": 0.0, "Taxable": 0.0, "Tax-deferred": 0.0, "Roth": 0.0}
    available = {
        bucket: float(current.loc[current["tax_bucket"] == bucket, "balance"].sum())
        for bucket in draws
    }

    custom_mix = parse_custom_mix_strategy(funding_strategy)
    if custom_mix is not None:
        # First try to satisfy the target mix.
        for bucket, ratio in custom_mix.items():
            target_draw = amount * ratio
            draw = min(target_draw, available.get(bucket, 0.0))
            draws[bucket] += draw
            available[bucket] -= draw
            remaining -= draw

        # Backfill shortages in a conservative order, preserving Roth last.
        for bucket in ["Cash", "Taxable", "Tax-deferred", "Roth"]:
            if remaining <= 0:
                break
            draw = min(available.get(bucket, 0.0), remaining)
            draws[bucket] += draw
            available[bucket] -= draw
            remaining -= draw
    else:
        for bucket in purchase_bucket_order(funding_strategy):
            if remaining <= 0:
                break
            draw = min(available.get(bucket, 0.0), remaining)
            draws[bucket] += draw
            available[bucket] -= draw
            remaining -= draw

    draws["Shortfall"] = max(remaining, 0.0)
    return draws


def estimate_purchase_funding_plan(
    current: pd.DataFrame,
    amount: float,
    funding_strategy: str,
    assumptions: Dict[str, str],
    social_security: float = 0.0,
    ordinary_income_before_purchase: float = 0.0,
) -> dict[str, float | str]:
    """Estimate how a one-time purchase would be funded from the current buckets.

    This is a planning estimate. Taxable draws estimate capital gains using the
    app's cost-basis ratio. Tax-deferred purchase draws estimate incremental
    ordinary tax using the same simplified ordinary-tax model used elsewhere.
    """
    amount = max(float(amount), 0.0)
    draws = purchase_draw_plan_from_current(current, amount, funding_strategy)
    plan = {
        "Purchase Amount": amount,
        "Purchase Funding Strategy": str(funding_strategy),
        "Purchase Cash Withdrawal": float(draws.get("Cash", 0.0)),
        "Purchase Taxable Withdrawal": float(draws.get("Taxable", 0.0)),
        "Purchase Tax-deferred Withdrawal": float(draws.get("Tax-deferred", 0.0)),
        "Purchase Roth Withdrawal": float(draws.get("Roth", 0.0)),
        "Purchase Taxable Gain": 0.0,
        "Purchase Capital Gains Tax": 0.0,
        "Purchase Ordinary Income Tax": 0.0,
        "Purchase Estimated Tax": 0.0,
        "Purchase Shortfall": float(draws.get("Shortfall", 0.0)),
    }

    taxable_draw = float(plan["Purchase Taxable Withdrawal"])
    if taxable_draw > 0:
        taxable_gain, cg_tax = estimate_capital_gains_tax(taxable_draw, assumptions)
        plan["Purchase Taxable Gain"] = taxable_gain
        plan["Purchase Capital Gains Tax"] = cg_tax

    tax_deferred_draw = float(plan["Purchase Tax-deferred Withdrawal"])
    if tax_deferred_draw > 0:
        taxable_before = taxable_income_with_conversion_feedback(
            pre_conversion_income=ordinary_income_before_purchase,
            conversion_amount=0.0,
            social_security=social_security,
            assumptions=assumptions,
        )
        taxable_after = taxable_income_with_conversion_feedback(
            pre_conversion_income=ordinary_income_before_purchase,
            conversion_amount=tax_deferred_draw,
            social_security=social_security,
            assumptions=assumptions,
        )
        plan["Purchase Ordinary Income Tax"] = max(tax_on_taxable_income(taxable_after, assumptions) - tax_on_taxable_income(taxable_before, assumptions), 0.0)

    plan["Purchase Estimated Tax"] = float(plan["Purchase Capital Gains Tax"]) + float(plan["Purchase Ordinary Income Tax"])
    # Shortfall is already computed by purchase_draw_plan_from_current().
    # Do not reference a local remaining variable here; there is no iterative draw in this estimator.
    plan["Purchase Shortfall"] = float(draws.get("Shortfall", 0.0))
    return plan


def apply_purchase_to_portfolio(current: pd.DataFrame, amount: float, funding_strategy: str) -> tuple[pd.DataFrame, dict[str, float]]:
    planned_draws = purchase_draw_plan_from_current(current, amount, funding_strategy)
    actual_draws = {"Cash": 0.0, "Taxable": 0.0, "Tax-deferred": 0.0, "Roth": 0.0}
    for bucket in ["Cash", "Taxable", "Tax-deferred", "Roth"]:
        draw_amount = max(float(planned_draws.get(bucket, 0.0)), 0.0)
        if draw_amount <= 0:
            continue
        current, remaining, drawn = draw_from_bucket_with_amount(current, bucket, draw_amount)
        actual_draws[bucket] += drawn
    actual_draws["Shortfall"] = max(float(planned_draws.get("Shortfall", 0.0)), 0.0)
    return current, actual_draws


def purchases_for_projection(purchases_override: pd.DataFrame | None = None, ignore_purchases: bool = False) -> pd.DataFrame:
    if ignore_purchases:
        return pd.DataFrame(columns=["name", "purchase_age", "amount", "funding_strategy", "include_in_projection", "notes"])
    if purchases_override is not None:
        out = purchases_override.copy()
        if "include_in_projection" in out.columns:
            out = out[out["include_in_projection"].astype(bool)]
        return out
    return read_planned_purchases(active_only=True)


def get_assumptions() -> Dict[str, str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM assumptions").fetchall()
    return {row["key"]: row["value"] for row in rows}


def set_assumption(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO assumptions (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()


def add_account(name: str, account_type: str, tax_bucket: str, owner: str, target_balance: float, notes: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO accounts (name, account_type, tax_bucket, owner, target_balance, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, account_type, tax_bucket, owner, target_balance, notes),
        )
        conn.commit()


def update_account(account_id: int, name: str, account_type: str, tax_bucket: str, owner: str, target_balance: float, notes: str, is_active: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE accounts
            SET name=?, account_type=?, tax_bucket=?, owner=?, target_balance=?, notes=?, is_active=?
            WHERE id=?
            """,
            (name, account_type, tax_bucket, owner, target_balance, notes, is_active, account_id),
        )
        conn.commit()


def add_balance(account_id: int, balance: float, as_of_date: date) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO balances (account_id, balance, as_of_date) VALUES (?, ?, ?)",
            (account_id, balance, as_of_date.isoformat()),
        )
        conn.commit()


def add_contribution(account_id: int, amount: float, frequency: str, start_date: date, end_date: date | None, notes: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO contributions (account_id, amount, frequency, start_date, end_date, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (account_id, amount, frequency, start_date.isoformat(), end_date.isoformat() if end_date else None, notes),
        )
        conn.commit()


def save_balance_editor(df_editor: pd.DataFrame, account_name_to_id: dict[str, int]) -> None:
    cleaned = df_editor.copy()
    cleaned = cleaned.dropna(subset=["Account", "Balance", "As-of Date"], how="any")
    rows = []
    for _, row in cleaned.iterrows():
        account = str(row["Account"])
        if account not in account_name_to_id:
            continue
        existing_id = row.get("ID")
        rows.append((
            None if pd.isna(existing_id) else int(existing_id),
            account_name_to_id[account],
            float(row["Balance"]),
            pd.to_datetime(row["As-of Date"]).date().isoformat(),
        ))

    with get_conn() as conn:
        conn.execute("DELETE FROM balances")
        for existing_id, account_id, balance, as_of_date in rows:
            if existing_id:
                conn.execute(
                    "INSERT INTO balances (id, account_id, balance, as_of_date) VALUES (?, ?, ?, ?)",
                    (existing_id, account_id, balance, as_of_date),
                )
            else:
                conn.execute(
                    "INSERT INTO balances (account_id, balance, as_of_date) VALUES (?, ?, ?)",
                    (account_id, balance, as_of_date),
                )
        conn.commit()


def save_contribution_editor(df_editor: pd.DataFrame, account_name_to_id: dict[str, int]) -> None:
    cleaned = df_editor.copy()
    cleaned = cleaned.dropna(subset=["Amount", "Frequency", "Start Date"], how="any")

    # Normalize names so Streamlit/editor string quirks do not cause false
    # "missing account" errors. Account ID is used as a fallback when present.
    normalized_name_to_id = {str(k).strip(): int(v) for k, v in account_name_to_id.items() if str(k).strip()}

    with get_conn() as conn:
        valid_account_ids = {
            int(row[0])
            for row in conn.execute("SELECT id FROM accounts").fetchall()
        }

    valid_frequencies = {"Monthly", "Semi-monthly", "Biweekly", "Annual", "One-time"}
    rows = []
    skipped_rows = []

    for _, row in cleaned.iterrows():
        account = "" if pd.isna(row.get("Account")) else str(row.get("Account")).strip()
        frequency = str(row["Frequency"]).strip()

        account_id = None
        account_id_value = row.get("Account ID")

        # Prefer the visible account selection if it is valid.
        if account in normalized_name_to_id:
            account_id = normalized_name_to_id[account]
        elif pd.notna(account_id_value):
            try:
                candidate_id = int(float(account_id_value))
                if candidate_id in valid_account_ids:
                    account_id = candidate_id
            except Exception:
                account_id = None

        # Streamlit's editable grid can sometimes blank the visible Account
        # name column during rerenders while still preserving Account ID.
        # If the Account ID is valid, recover the name instead of rejecting
        # the row and wiping the visible value on refresh.
        if (not account) and account_id is not None:
            try:
                account = next(
                    k for k, v in normalized_name_to_id.items()
                    if int(v) == int(account_id)
                )
            except StopIteration:
                pass

        if account_id is None or frequency not in valid_frequencies:
            skipped_rows.append(account or f"Account ID {account_id_value}")
            continue

        end_value = row.get("End Date")
        end_date = None if pd.isna(end_value) or end_value in ("", None) else pd.to_datetime(end_value).date().isoformat()
        existing_id = row.get("ID")
        rows.append((
            None if pd.isna(existing_id) else int(existing_id),
            int(account_id),
            float(row["Amount"]),
            frequency,
            pd.to_datetime(row["Start Date"]).date().isoformat(),
            end_date,
            "" if pd.isna(row.get("Notes")) else str(row.get("Notes")),
            1 if bool(row.get("Active", True)) else 0,
        ))

    if skipped_rows:
        raise ValueError(
            "Some contribution rows have missing/invalid accounts. Select a valid Account before saving: "
            + ", ".join(sorted(set(skipped_rows)))
        )

    with get_conn() as conn:
        conn.execute("DELETE FROM contributions")
        for existing_id, account_id, amount, frequency, start_date, end_date, notes, is_active in rows:
            if existing_id:
                conn.execute(
                    """
                    INSERT INTO contributions (id, account_id, amount, frequency, start_date, end_date, notes, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (existing_id, account_id, amount, frequency, start_date, end_date, notes, is_active),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO contributions (account_id, amount, frequency, start_date, end_date, notes, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (account_id, amount, frequency, start_date, end_date, notes, is_active),
                )
        conn.commit()


# -----------------------------
# Projection logic
# -----------------------------

def annualized_contribution(amount: float, frequency: str) -> float:
    multipliers = {
        "Monthly": 12,
        "Semi-monthly": 24,
        "Biweekly": 26,
        "Annual": 1,
        "One-time": 0,
    }
    return amount * multipliers.get(frequency, 0)


def projected_return_for_bucket(tax_bucket: str, assumptions: Dict[str, str]) -> float:
    stock_return = float(assumptions.get("stock_return", 0.07))
    cash_return = float(assumptions.get("bond_cash_return", 0.035))
    if tax_bucket == "Cash":
        return cash_return
    return stock_return


def cash_cap(assumptions: Dict[str, str]) -> float:
    return float(assumptions.get("bridge_target", 200000)) + float(assumptions.get("checking_buffer_target", 45000))


def add_to_account(current: pd.DataFrame, account_name: str, tax_bucket: str, amount: float) -> pd.DataFrame:
    if amount <= 0:
        return current
    idxs = current.index[current["name"] == account_name].tolist()
    if not idxs:
        idxs = current.index[current["tax_bucket"] == tax_bucket].tolist()
    if idxs:
        current.at[idxs[0], "balance"] = float(current.at[idxs[0], "balance"]) + amount
    else:
        current = pd.concat([
            current,
            pd.DataFrame([{
                "account_id": -1000 - len(current),
                "name": account_name,
                "account_type": "Projected",
                "tax_bucket": tax_bucket,
                "target_balance": 0,
                "balance": amount,
            }]),
        ], ignore_index=True)
    return current


def apply_cash_cap_and_overflow(current: pd.DataFrame, assumptions: Dict[str, str]) -> pd.DataFrame:
    if assumptions.get("cash_cap_enabled", "1") != "1":
        return current

    cap = cash_cap(assumptions)
    cash_total = current.loc[current["tax_bucket"] == "Cash", "balance"].sum()
    excess = max(cash_total - cap, 0)
    if excess <= 0:
        return current

    cash_idxs = current.index[current["tax_bucket"] == "Cash"].tolist()
    cash_idxs = sorted(cash_idxs, key=lambda idx: float(current.at[idx, "balance"]), reverse=True)
    remaining = excess

    for idx in cash_idxs:
        if remaining <= 0:
            break
        target = float(current.at[idx, "target_balance"]) if "target_balance" in current.columns and pd.notna(current.at[idx, "target_balance"]) else 0.0
        available_above_target = max(float(current.at[idx, "balance"]) - target, 0)
        draw = min(available_above_target, remaining)
        current.at[idx, "balance"] = float(current.at[idx, "balance"]) - draw
        remaining -= draw

    for idx in cash_idxs:
        if remaining <= 0:
            break
        available = float(current.at[idx, "balance"])
        draw = min(available, remaining)
        current.at[idx, "balance"] = available - draw
        remaining -= draw

    return add_to_account(current, assumptions.get("overflow_account_name", "Taxable Brokerage"), "Taxable", excess)


def route_pre_retirement_cashflow(current: pd.DataFrame, assumptions: Dict[str, str], layoff_factor: float) -> pd.DataFrame:
    """
    Models the user's actual intended flow:

    Before cash target is full:
      - brokerage_per_paycheck_before_cash_full goes to brokerage
      - cash_per_paycheck_until_full + extra_cash_per_quarter_until_full goes to cash

    After cash target is full:
      - brokerage_per_paycheck_after_cash_full goes to brokerage
      - optional quarterly surplus also routes to brokerage

    This prevents double-counting by replacing Cash/Taxable account-specific contributions.
    401k and Roth contributions are still handled separately by contribution rows.
    """
    if assumptions.get("use_routed_cashflow", "1") != "1":
        return current

    paychecks = float(assumptions.get("paychecks_per_year", 26))
    broker_before = float(assumptions.get("brokerage_per_paycheck_before_cash_full", 3000)) * paychecks
    broker_after = float(assumptions.get("brokerage_per_paycheck_after_cash_full", 4200)) * paychecks
    cash_until_full = float(assumptions.get("cash_per_paycheck_until_full", 1200)) * paychecks
    quarterly_extra = float(assumptions.get("extra_cash_per_quarter_until_full", 16000)) * 4
    extra_to_broker_after = assumptions.get("send_extra_cash_to_brokerage_after_full", "0") == "1"

    broker_before *= layoff_factor
    broker_after *= layoff_factor
    cash_until_full *= layoff_factor
    quarterly_extra *= layoff_factor

    cap = cash_cap(assumptions)
    cash_total = current.loc[current["tax_bucket"] == "Cash", "balance"].sum()
    cash_room = max(cap - cash_total, 0)
    planned_cash_build = cash_until_full + quarterly_extra

    if cash_room <= 0:
        brokerage_amount = broker_after + (quarterly_extra if extra_to_broker_after else 0)
        return add_to_account(current, assumptions.get("overflow_account_name", "Taxable Brokerage"), "Taxable", brokerage_amount)

    if planned_cash_build <= 0:
        return add_to_account(current, assumptions.get("overflow_account_name", "Taxable Brokerage"), "Taxable", broker_before)

    # If cash fills during this projection year, split the year between before/after behavior.
    fill_fraction = min(cash_room / planned_cash_build, 1.0)
    cash_added = min(planned_cash_build, cash_room)
    brokerage_amount = (broker_before * fill_fraction) + (broker_after * (1 - fill_fraction))
    if extra_to_broker_after:
        brokerage_amount += quarterly_extra * (1 - fill_fraction)

    current = add_to_account(current, assumptions.get("cash_account_name", "Cash Bridge Savings"), "Cash", cash_added)
    current = add_to_account(current, assumptions.get("overflow_account_name", "Taxable Brokerage"), "Taxable", brokerage_amount)
    return current


def federal_tax_brackets_mfj(assumptions: Dict[str, str]) -> list[tuple[float, float]]:
    return [
        (float(assumptions.get("mfj_10_bracket_top", 24800)), 0.10),
        (float(assumptions.get("mfj_12_bracket_top", 100800)), 0.12),
        (float(assumptions.get("mfj_22_bracket_top", 211400)), 0.22),
        (float(assumptions.get("mfj_24_bracket_top", 403550)), 0.24),
        (float(assumptions.get("mfj_32_bracket_top", 512450)), 0.32),
        (float(assumptions.get("mfj_35_bracket_top", 768700)), 0.35),
        (float("inf"), 0.37),
    ]


def tax_on_taxable_income(taxable_income: float, assumptions: Dict[str, str]) -> float:
    taxable_income = max(float(taxable_income), 0.0)
    tax = 0.0
    lower = 0.0
    for upper, rate in federal_tax_brackets_mfj(assumptions):
        if taxable_income <= lower:
            break
        amount_in_bracket = min(taxable_income, upper) - lower
        tax += max(amount_in_bracket, 0.0) * rate
        lower = upper
    return tax




def rmd_uniform_lifetime_divisor(age: int) -> float | None:
    """Approximate IRS Uniform Lifetime Table divisor for RMD estimates.

    The default RMD start age is editable in the Roth Conversion Planner. This
    table is for planning only, not tax filing.
    """
    table = {
        73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9, 78: 22.0,
        79: 21.1, 80: 20.2, 81: 19.4, 82: 18.5, 83: 17.7, 84: 16.8,
        85: 16.0, 86: 15.2, 87: 14.4, 88: 13.7, 89: 12.9, 90: 12.2,
        91: 11.5, 92: 10.8, 93: 10.1, 94: 9.5, 95: 8.9, 96: 8.4,
        97: 7.8, 98: 7.3, 99: 6.8, 100: 6.4, 101: 6.0, 102: 5.6,
        103: 5.2, 104: 4.9, 105: 4.6, 106: 4.3, 107: 4.1, 108: 3.9,
        109: 3.7, 110: 3.5, 111: 3.4, 112: 3.3, 113: 3.1, 114: 3.0,
        115: 2.9, 116: 2.8, 117: 2.7, 118: 2.5, 119: 2.3, 120: 2.0,
    }
    if age in table:
        return table[age]
    if age > 120:
        return 2.0
    return None


def estimate_rmd(age: int, tax_deferred_balance: float, assumptions: Dict[str, str]) -> float:
    """Estimate the forced taxable distribution from tax-deferred accounts."""
    rmd_start_age = int(float(assumptions.get("rmd_start_age", 73)))
    if age < rmd_start_age or tax_deferred_balance <= 0:
        return 0.0
    divisor = rmd_uniform_lifetime_divisor(age)
    if divisor is None or divisor <= 0:
        return 0.0
    return float(tax_deferred_balance) / divisor


def estimate_taxable_social_security(social_security: float, other_income: float) -> float:
    """Simplified MFJ taxable Social Security estimate.

    other_income includes ordinary income plus tax-exempt interest if you later
    add it. This uses the common provisional-income thresholds for married
    filing jointly: $32k and $44k. It is good enough for scenario planning, but
    not a tax return calculation.
    """
    ss = max(float(social_security), 0.0)
    provisional_income = max(float(other_income), 0.0) + 0.5 * ss
    if provisional_income <= 32000:
        return 0.0
    if provisional_income <= 44000:
        return min(0.5 * ss, 0.5 * (provisional_income - 32000))
    return min(0.85 * ss, 0.5 * (44000 - 32000) + 0.85 * (provisional_income - 44000))


def estimate_taxable_gain_from_brokerage_withdrawal(withdrawal: float, assumptions: Dict[str, str]) -> float:
    """Estimate realized long-term capital gains from taxable brokerage withdrawals.

    taxable_cost_basis_ratio = 0.70 means 70% of the withdrawal is assumed to be
    basis/principal and 30% is assumed to be taxable gain.
    """
    withdrawal = max(float(withdrawal), 0.0)
    basis_ratio = min(max(float(assumptions.get("taxable_cost_basis_ratio", 0.70)), 0.0), 1.0)
    return withdrawal * (1.0 - basis_ratio)


def estimate_capital_gains_tax(brokerage_withdrawal: float, assumptions: Dict[str, str]) -> tuple[float, float]:
    taxable_gain = estimate_taxable_gain_from_brokerage_withdrawal(brokerage_withdrawal, assumptions)
    federal_rate = float(assumptions.get("capital_gains_rate", 0.15))
    state_rate = float(assumptions.get("state_tax_rate", 0.0))
    return taxable_gain, taxable_gain * max(federal_rate + state_rate, 0.0)


def estimate_taxable_draw_for_spending(current: pd.DataFrame, amount: float) -> float:
    """Estimate how much of a spending withdrawal would come from Taxable.

    The app's spending order is Cash -> Taxable -> Tax-deferred -> Roth. This
    helper estimates the Taxable portion before the actual draw is applied so we
    can estimate capital-gains tax.
    """
    remaining = max(float(amount), 0.0)
    taxable_draw = 0.0
    for bucket in ["Cash", "Taxable", "Tax-deferred", "Roth"]:
        available = float(current.loc[current["tax_bucket"] == bucket, "balance"].sum())
        draw = min(available, remaining)
        if bucket == "Taxable":
            taxable_draw += draw
        remaining -= draw
        if remaining <= 0:
            break
    return taxable_draw


def draw_from_bucket_with_amount(current: pd.DataFrame, bucket: str, amount: float) -> tuple[pd.DataFrame, float, float]:
    """Draw from one tax bucket and return current, remaining, amount_drawn."""
    remaining = max(float(amount), 0.0)
    before = remaining
    idxs = current.index[current["tax_bucket"] == bucket].tolist()
    for idx in idxs:
        if remaining <= 0:
            break
        available = float(current.at[idx, "balance"])
        draw = min(available, remaining)
        current.at[idx, "balance"] = available - draw
        remaining -= draw
    return current, remaining, before - remaining


def draw_spending_from_portfolio(current: pd.DataFrame, amount: float) -> tuple[pd.DataFrame, float, dict[str, float]]:
    """Withdraw spending/tax cash need using the app's default order."""
    remaining = max(float(amount), 0.0)
    draws = {"Cash": 0.0, "Taxable": 0.0, "Tax-deferred": 0.0, "Roth": 0.0}
    for bucket in ["Cash", "Taxable", "Tax-deferred", "Roth"]:
        current, remaining, drawn = draw_from_bucket_with_amount(current, bucket, remaining)
        draws[bucket] += drawn
        if remaining <= 0:
            break
    return current, remaining, draws




def marginal_bracket_rate_for_taxable_income(taxable_income: float, assumptions: Dict[str, str]) -> float:
    """Return the highest ordinary federal bracket reached by taxable income."""
    taxable_income = max(float(taxable_income), 0.0)
    if taxable_income <= 0:
        return 0.0
    for upper, rate in federal_tax_brackets_mfj(assumptions):
        if taxable_income <= upper:
            return float(rate)
    return 0.37


def marginal_bracket_label_for_taxable_income(taxable_income: float, assumptions: Dict[str, str]) -> str:
    rate = marginal_bracket_rate_for_taxable_income(taxable_income, assumptions)
    return "0%" if rate <= 0 else f"{rate:.0%}"


def bracket_headroom_after_taxable_income(taxable_income: float, assumptions: Dict[str, str]) -> float:
    """Dollars of room left before the next ordinary bracket top."""
    taxable_income = max(float(taxable_income), 0.0)
    for upper, _rate in federal_tax_brackets_mfj(assumptions):
        if taxable_income <= upper:
            if upper == float("inf"):
                return 0.0
            return max(float(upper) - taxable_income, 0.0)
    return 0.0


def conversion_outcome_metrics(
    conversion_amount: float,
    pre_conversion_income: float,
    social_security: float,
    assumptions: Dict[str, str],
) -> tuple[float, str, float]:
    """Return effective rate, marginal bracket reached, and post-conversion headroom.

    Effective rate is the average federal tax on the Roth conversion.
    Marginal bracket reached is the highest ordinary bracket touched by the
    converted dollars, using taxable income after the conversion and including
    Social Security tax feedback. Headroom is the remaining taxable-income room
    in that marginal bracket after the conversion.
    """
    conversion_amount = max(float(conversion_amount), 0.0)
    if conversion_amount <= 0:
        return 0.0, "—", 0.0

    taxable_before = taxable_income_with_conversion_feedback(
        pre_conversion_income=pre_conversion_income,
        conversion_amount=0.0,
        social_security=social_security,
        assumptions=assumptions,
    )
    taxable_after = taxable_income_with_conversion_feedback(
        pre_conversion_income=pre_conversion_income,
        conversion_amount=conversion_amount,
        social_security=social_security,
        assumptions=assumptions,
    )
    tax_before = tax_on_taxable_income(taxable_before, assumptions)
    tax_after = tax_on_taxable_income(taxable_after, assumptions)
    effective_rate = (tax_after - tax_before) / conversion_amount
    bracket_label = marginal_bracket_label_for_taxable_income(taxable_after, assumptions)
    headroom = bracket_headroom_after_taxable_income(taxable_after, assumptions)
    return effective_rate, bracket_label, headroom


def max_conversion_bracket_reached(projection: pd.DataFrame, assumptions: Dict[str, str]) -> str:
    """Return the highest ordinary marginal bracket reached in years with Roth conversions."""
    if projection.empty or "Roth Conversion" not in projection.columns or "Ordinary Taxable Income" not in projection.columns:
        return "—"
    conv_years = projection[projection["Roth Conversion"].fillna(0).astype(float) > 0]
    if conv_years.empty:
        return "—"
    max_rate = 0.0
    for taxable_income in conv_years["Ordinary Taxable Income"].fillna(0).astype(float):
        max_rate = max(max_rate, marginal_bracket_rate_for_taxable_income(taxable_income, assumptions))
    return "0%" if max_rate <= 0 else f"{max_rate:.0%}"
def estimate_ordinary_income_tax(
    rmd: float,
    roth_conversion: float,
    social_security: float,
    assumptions: Dict[str, str],
) -> tuple[float, float, float]:
    """Return ordinary tax, taxable Social Security, taxable ordinary income."""
    base_other_income = float(assumptions.get("base_taxable_income_retirement", 0.0))
    ordinary_before_ss = base_other_income + max(float(rmd), 0.0) + max(float(roth_conversion), 0.0)
    taxable_ss = estimate_taxable_social_security(social_security, ordinary_before_ss)
    standard_deduction = float(assumptions.get("standard_deduction", 32200))
    taxable_ordinary_income = max(ordinary_before_ss + taxable_ss - standard_deduction, 0.0)
    ordinary_tax = tax_on_taxable_income(taxable_ordinary_income, assumptions)
    return ordinary_tax, taxable_ss, taxable_ordinary_income


def estimate_annual_tax_on_tax_deferred_balance(row: pd.Series, assumptions: Dict[str, str]) -> tuple[float, float]:
    """Backwards-compatible estimate for older projections without tax columns."""
    age = int(row.get("Age", 0))
    tax_deferred = float(row.get("Tax-deferred", 0.0) or 0.0)
    rmd = estimate_rmd(age, tax_deferred, assumptions)
    if rmd <= 0:
        return 0.0, 0.0
    ordinary_tax, _, _ = estimate_ordinary_income_tax(
        rmd=rmd,
        roth_conversion=0.0,
        social_security=float(row.get("Social Security", 0.0) or 0.0),
        assumptions=assumptions,
    )
    return rmd, ordinary_tax


def estimate_conversion_tax(conversion_amount: float, other_taxable_income: float, assumptions: Dict[str, str]) -> float:
    standard_deduction = float(assumptions.get("standard_deduction", 32200))
    taxable_before = max(float(other_taxable_income) - standard_deduction, 0.0)
    taxable_after = max(float(other_taxable_income) + float(conversion_amount) - standard_deduction, 0.0)
    return tax_on_taxable_income(taxable_after, assumptions) - tax_on_taxable_income(taxable_before, assumptions)


def taxable_income_with_conversion_feedback(
    pre_conversion_income: float,
    conversion_amount: float,
    social_security: float,
    assumptions: Dict[str, str],
) -> float:
    """Taxable income after a Roth conversion, including SS tax feedback.

    Roth conversions increase provisional income, which can make more Social
    Security taxable. A simple bracket-room calculation that only adds the
    conversion amount will overstate available 12%/22% room after SS begins.
    """
    pre_conversion_income = max(float(pre_conversion_income), 0.0)
    conversion_amount = max(float(conversion_amount), 0.0)
    taxable_ss = estimate_taxable_social_security(
        float(social_security),
        pre_conversion_income + conversion_amount,
    )
    standard_deduction = float(assumptions.get("standard_deduction", 32200))
    return max(pre_conversion_income + conversion_amount + taxable_ss - standard_deduction, 0.0)


def estimate_conversion_tax_with_ss_feedback(
    conversion_amount: float,
    pre_conversion_income: float,
    social_security: float,
    assumptions: Dict[str, str],
) -> float:
    """Marginal tax on a conversion, including conversion-driven SS taxation."""
    taxable_before = taxable_income_with_conversion_feedback(
        pre_conversion_income=pre_conversion_income,
        conversion_amount=0.0,
        social_security=social_security,
        assumptions=assumptions,
    )
    taxable_after = taxable_income_with_conversion_feedback(
        pre_conversion_income=pre_conversion_income,
        conversion_amount=conversion_amount,
        social_security=social_security,
        assumptions=assumptions,
    )
    return tax_on_taxable_income(taxable_after, assumptions) - tax_on_taxable_income(taxable_before, assumptions)


def max_conversion_to_fill_bracket_with_ss_feedback(
    pre_conversion_income: float,
    social_security: float,
    target_top: float,
    available_tax_deferred: float,
    assumptions: Dict[str, str],
) -> float:
    """Solve the largest conversion that stays within a target bracket.

    Uses a binary search because taxable Social Security changes as the
    conversion itself increases provisional income. This makes post-SS bracket
    room shrink much more realistically.
    """
    available_tax_deferred = max(float(available_tax_deferred), 0.0)
    target_top = max(float(target_top), 0.0)
    if available_tax_deferred <= 0:
        return 0.0

    taxable_without_conversion = taxable_income_with_conversion_feedback(
        pre_conversion_income=pre_conversion_income,
        conversion_amount=0.0,
        social_security=social_security,
        assumptions=assumptions,
    )
    if taxable_without_conversion >= target_top:
        return 0.0

    lo = 0.0
    hi = available_tax_deferred
    for _ in range(40):
        mid = (lo + hi) / 2
        taxable_mid = taxable_income_with_conversion_feedback(
            pre_conversion_income=pre_conversion_income,
            conversion_amount=mid,
            social_security=social_security,
            assumptions=assumptions,
        )
        if taxable_mid <= target_top:
            lo = mid
        else:
            hi = mid
    return max(min(lo, available_tax_deferred), 0.0)



def aca_start_age(assumptions: Dict[str, str]) -> int:
    """ACA planning usually starts when employer coverage stops: retirement age."""
    value = str(assumptions.get("aca_start_age", "auto")).strip().lower()
    if value in {"", "auto", "retirement", "retirement age"}:
        return int(float(assumptions.get("retirement_age", 60)))
    return int(float(value))


def aca_end_age(assumptions: Dict[str, str]) -> int:
    """ACA planning usually ends the year before Medicare eligibility."""
    value = str(assumptions.get("aca_end_age", "auto")).strip().lower()
    medicare_age = int(float(assumptions.get("aca_medicare_age", 65)))
    if value in {"", "auto", "medicare", "medicare age"}:
        return medicare_age - 1
    return int(float(value))


def aca_magi_limit(assumptions: Dict[str, str]) -> float:
    fpl_100 = float(assumptions.get("aca_fpl_100", 21150))
    target_pct = float(assumptions.get("aca_target_fpl_percent", 400))
    buffer = float(assumptions.get("aca_magi_safety_buffer", 1000))
    return max(fpl_100 * (target_pct / 100.0) - buffer, 0.0)


def estimate_aca_magi(pre_conversion_income: float, conversion_amount: float, social_security: float, taxable_gain_income: float = 0.0) -> float:
    """Simplified ACA MAGI estimate.

    ACA MAGI generally includes AGI plus tax-exempt interest and excluded foreign income.
    For this app's planning model, we include ordinary pre-conversion income, Roth conversions,
    taxable brokerage gains, and taxable Social Security feedback.
    """
    pre = max(float(pre_conversion_income), 0.0)
    conv = max(float(conversion_amount), 0.0)
    gains = max(float(taxable_gain_income), 0.0)
    taxable_ss = estimate_taxable_social_security(float(social_security), pre + conv + gains)
    return pre + conv + gains + taxable_ss


def max_conversion_for_aca_magi(
    pre_conversion_income: float,
    social_security: float,
    taxable_gain_income: float,
    magi_limit: float,
    available_tax_deferred: float,
) -> float:
    available_tax_deferred = max(float(available_tax_deferred), 0.0)
    if available_tax_deferred <= 0:
        return 0.0
    current_magi = estimate_aca_magi(pre_conversion_income, 0.0, social_security, taxable_gain_income)
    if current_magi >= magi_limit:
        return 0.0
    lo, hi = 0.0, available_tax_deferred
    for _ in range(40):
        mid = (lo + hi) / 2
        magi_mid = estimate_aca_magi(pre_conversion_income, mid, social_security, taxable_gain_income)
        if magi_mid <= magi_limit:
            lo = mid
        else:
            hi = mid
    return max(min(lo, available_tax_deferred), 0.0)


def target_bracket_top(target_bracket: str, assumptions: Dict[str, str]) -> float:
    key = str(target_bracket).replace("%", "").strip()
    mapping = {
        "10": "mfj_10_bracket_top",
        "12": "mfj_12_bracket_top",
        "22": "mfj_22_bracket_top",
        "24": "mfj_24_bracket_top",
        "32": "mfj_32_bracket_top",
        "35": "mfj_35_bracket_top",
    }
    return float(assumptions.get(mapping.get(key, "mfj_22_bracket_top"), assumptions.get("mfj_22_bracket_top", 211400)))


def calculate_roth_conversion_amount(
    age: int,
    current: pd.DataFrame,
    assumptions: Dict[str, str],
    roth_scenario: dict | None,
    social_security: float = 0.0,
    estimated_rmd: float = 0.0,
    taxable_gain_income: float = 0.0,
    previous_conversion_amount: float = 0.0,
) -> tuple[float, float, str, float, str, float]:
    """Return conversion amount, tax, strategy, effective rate, marginal bracket, and headroom.

    Important modeling detail:
    Fill-bracket scenarios must account for other taxable income already present
    in that year. That includes RMDs, taxable Social Security, and taxable gains
    from brokerage withdrawals. Otherwise the app overstates available 12%/22%
    bracket room after Social Security begins.

    Post-Social Security, the app accounts for the Social Security tax
    feedback loop: conversions increase provisional income, which can make more
    Social Security taxable and reduce available low-bracket conversion room.
    """
    base_other_income = float(assumptions.get("base_taxable_income_retirement", 0.0))

    if roth_scenario is None:
        start_age, end_age, _linked_to_ss = effective_roth_conversion_window(assumptions, None)
        if not (start_age <= age <= end_age):
            return 0.0, 0.0, "Legacy fixed", 0.0, "—", 0.0
        available = float(current.loc[current["tax_bucket"] == "Tax-deferred", "balance"].sum())
        amount = min(float(assumptions.get("annual_roth_conversion", 0)), available)

        # Legacy mode still uses the bracket-aware tax estimator so Social
        # Security/RMD/capital-gain income affects conversion tax.
        pre_conversion_income = base_other_income + max(float(estimated_rmd), 0.0) + max(float(taxable_gain_income), 0.0)
        tax = estimate_conversion_tax_with_ss_feedback(amount, pre_conversion_income, float(social_security), assumptions)
        effective_rate, bracket_reached, headroom = conversion_outcome_metrics(amount, pre_conversion_income, float(social_security), assumptions)
        return amount, tax, "Legacy fixed", effective_rate, bracket_reached, headroom

    start_age, end_age, _linked_to_ss = effective_roth_conversion_window(assumptions, roth_scenario)
    strategy = str(roth_scenario.get("strategy", "No conversions"))

    # ACA-aware strategies should not hard-stop at the ACA/Medicare boundary.
    # Their scenario Start/End Age still controls the low-income/ACA window label,
    # but the strategy itself continues after ACA using bracket-fill/taper logic.
    if strategy in {"Optimize ACA", "Hybrid ACA"}:
        rmd_age = int(float(assumptions.get("rmd_start_age", 73)))
        projection_end_age = int(float(assumptions.get("current_age", 52))) + int(float(assumptions.get("years_to_project", 35)))
        strategy_end_age = max(end_age, rmd_age + 8, projection_end_age)
        if age < start_age or age > strategy_end_age:
            return 0.0, 0.0, strategy, 0.0, "—", 0.0
    else:
        if strategy == "No conversions" or not (start_age <= age <= end_age):
            return 0.0, 0.0, strategy, 0.0, "—", 0.0

    if strategy == "No conversions":
        return 0.0, 0.0, strategy, 0.0, "—", 0.0

    available_tax_deferred = float(current.loc[current["tax_bucket"] == "Tax-deferred", "balance"].sum())
    if available_tax_deferred <= 0:
        return 0.0, 0.0, strategy, 0.0, "—", 0.0

    scenario_other_income = float(roth_scenario.get("other_taxable_income", 0) or 0)

    # Income already occupying bracket space before the Roth conversion.
    # Taxable gains are included because they affect AGI/provisional income and
    # reduce practical headroom, even though they receive preferential rates.
    # Social Security is handled with feedback below because the conversion
    # itself can make more Social Security taxable.
    pre_conversion_income = (
        base_other_income
        + scenario_other_income
        + max(float(estimated_rmd), 0.0)
        + max(float(taxable_gain_income), 0.0)
    )

    if strategy == "Fixed annual amount":
        amount = min(float(roth_scenario.get("annual_amount", 0) or 0), available_tax_deferred)
    elif strategy == "Fill bracket":
        amount = max_conversion_to_fill_bracket_with_ss_feedback(
            pre_conversion_income=pre_conversion_income,
            social_security=float(social_security),
            target_top=target_bracket_top(str(roth_scenario.get("target_bracket", "22%")), assumptions),
            available_tax_deferred=available_tax_deferred,
            assumptions=assumptions,
        )
    elif strategy == "Optimize ACA":
        start_aca = aca_start_age(assumptions)
        end_aca = aca_end_age(assumptions)

        if start_aca <= age <= end_aca:
            amount = max_conversion_for_aca_magi(
                pre_conversion_income=pre_conversion_income,
                social_security=float(social_security),
                taxable_gain_income=max(float(taxable_gain_income), 0.0),
                magi_limit=aca_magi_limit(assumptions),
                available_tax_deferred=available_tax_deferred,
            )
        else:
            # After ACA years, continue with a conservative 12% bracket-fill strategy.
            amount = max_conversion_to_fill_bracket_with_ss_feedback(
                pre_conversion_income=pre_conversion_income,
                social_security=float(social_security),
                target_top=target_bracket_top("12%", assumptions),
                available_tax_deferred=available_tax_deferred,
                assumptions=assumptions,
            )

    elif strategy == "Hybrid ACA":
        start_aca = aca_start_age(assumptions)
        end_aca = aca_end_age(assumptions)
        social_security_age = int(float(assumptions.get("social_security_age", 67)))
        medicare_age = int(float(assumptions.get("aca_medicare_age", 65)))

        if start_aca <= age <= end_aca:
            # During ACA years, preserve subsidies by staying under MAGI targets.
            amount = max_conversion_for_aca_magi(
                pre_conversion_income=pre_conversion_income,
                social_security=float(social_security),
                taxable_gain_income=max(float(taxable_gain_income), 0.0),
                magi_limit=aca_magi_limit(assumptions),
                available_tax_deferred=available_tax_deferred,
            )

        elif medicare_age <= age < 70:
            # Medicare but before/around early SS years: ACA cliff is gone, so
            # resume meaningful conversions by filling the 12% bracket.
            amount = max_conversion_to_fill_bracket_with_ss_feedback(
                pre_conversion_income=pre_conversion_income,
                social_security=float(social_security),
                target_top=target_bracket_top("12%", assumptions),
                available_tax_deferred=available_tax_deferred,
                assumptions=assumptions,
            )

        elif 70 <= age < int(float(assumptions.get("rmd_start_age", 73))):
            # Pre-RMD cleanup window: allow moderate 22% conversions while
            # there is still time to reduce future RMD pressure.
            amount = max_conversion_to_fill_bracket_with_ss_feedback(
                pre_conversion_income=pre_conversion_income,
                social_security=float(social_security),
                target_top=target_bracket_top("22%", assumptions),
                available_tax_deferred=available_tax_deferred,
                assumptions=assumptions,
            )

        else:
            # RMD years and later: taper back to 12% bracket fills.
            amount = max_conversion_to_fill_bracket_with_ss_feedback(
                pre_conversion_income=pre_conversion_income,
                social_security=float(social_security),
                target_top=target_bracket_top("12%", assumptions),
                available_tax_deferred=available_tax_deferred,
                assumptions=assumptions,
            )

    else:
        amount = 0.0

    amount = max(min(float(amount), available_tax_deferred), 0.0)

    # Avoid artificial post-SS spikes in bracket-fill plans. If taxable brokerage
    # draws fall late in the scenario, the raw bracket-room formula can suddenly
    # increase the final-year conversion even though the intended strategy is a
    # reduced/tapered conversion phase after Social Security begins.
    if strategy == "Fill bracket":
        social_security_age = int(float(assumptions.get("social_security_age", 67)))
        if age > social_security_age and previous_conversion_amount > 0:
            amount = min(amount, float(previous_conversion_amount))

    tax = estimate_conversion_tax_with_ss_feedback(amount, pre_conversion_income, float(social_security), assumptions)
    effective_rate, bracket_reached, headroom = conversion_outcome_metrics(amount, pre_conversion_income, float(social_security), assumptions)
    return amount, tax, strategy, effective_rate, bracket_reached, headroom

def draw_from_bucket(current: pd.DataFrame, bucket: str, amount: float) -> tuple[pd.DataFrame, float]:
    remaining = max(float(amount), 0.0)
    idxs = current.index[current["tax_bucket"] == bucket].tolist()
    for idx in idxs:
        if remaining <= 0:
            break
        available = float(current.at[idx, "balance"])
        draw = min(available, remaining)
        current.at[idx, "balance"] = available - draw
        remaining -= draw
    return current, remaining


def pay_roth_conversion_tax(current: pd.DataFrame, tax_due: float, pay_tax_from: str) -> tuple[pd.DataFrame, float, float]:
    remaining = max(float(tax_due), 0.0)
    paid = 0.0
    order_map = {
        "Cash then Taxable": ["Cash", "Taxable"],
        "Taxable then Cash": ["Taxable", "Cash"],
        "Cash only": ["Cash"],
        "Taxable only": ["Taxable"],
        "Withhold from conversion": [],
    }
    for bucket in order_map.get(pay_tax_from, ["Cash", "Taxable"]):
        before = remaining
        current, remaining = draw_from_bucket(current, bucket, remaining)
        paid += before - remaining
    return current, paid, remaining


def apply_roth_conversion(current: pd.DataFrame, conversion_amount: float, tax_due: float, assumptions: Dict[str, str], roth_scenario: dict | None) -> tuple[pd.DataFrame, float, float]:
    if conversion_amount <= 0:
        return current, 0.0, 0.0

    pay_tax_from = roth_scenario.get("pay_tax_from", "Cash then Taxable") if roth_scenario else "Cash then Taxable"
    net_to_roth = conversion_amount

    # If taxes are withheld from the conversion, less money lands in Roth.
    if pay_tax_from == "Withhold from conversion":
        net_to_roth = max(conversion_amount - tax_due, 0.0)
        tax_paid = min(tax_due, conversion_amount)
        unpaid_tax = max(tax_due - tax_paid, 0.0)
    else:
        tax_paid = 0.0
        unpaid_tax = 0.0

    remaining_conversion = conversion_amount
    tax_deferred_idxs = current.index[current["tax_bucket"] == "Tax-deferred"].tolist()
    for idx in tax_deferred_idxs:
        if remaining_conversion <= 0:
            break
        available = float(current.at[idx, "balance"])
        move = min(available, remaining_conversion)
        current.at[idx, "balance"] = available - move
        remaining_conversion -= move

    current = add_to_account(current, "Projected Roth Conversions", "Roth", net_to_roth)

    if pay_tax_from != "Withhold from conversion" and tax_due > 0:
        current, tax_paid, unpaid_tax = pay_roth_conversion_tax(current, tax_due, pay_tax_from)

    return current, tax_paid, unpaid_tax


def build_projection(years: int, assumptions: Dict[str, str], roth_scenario: dict | None = None, purchases_override: pd.DataFrame | None = None, ignore_purchases: bool = False) -> pd.DataFrame:
    latest = read_latest_balances().fillna({"balance": 0, "target_balance": 0})
    contribs = read_contributions(active_only=True)
    purchases_df = purchases_for_projection(purchases_override=purchases_override, ignore_purchases=ignore_purchases)

    current_age = int(float(assumptions.get("current_age", 52)))
    current_year = datetime.now().year
    base_retirement_age = int(float(assumptions.get("retirement_age", 58)))
    apply_layoff_pause = assumptions.get("apply_layoff_pause_scenario", "0") == "1"
    layoff_pause_months = int(float(assumptions.get("layoff_pause_months", 0))) if apply_layoff_pause else 0
    extend_retirement_for_layoff = assumptions.get("extend_retirement_for_layoff", "0") == "1"
    retirement_age = base_retirement_age + round(layoff_pause_months / 12) if extend_retirement_for_layoff else base_retirement_age

    social_security_age = int(float(assumptions.get("social_security_age", 67)))
    social_security_annual = float(assumptions.get("social_security_annual", 0))
    annual_spend_retirement = float(assumptions.get("annual_spend_retirement", 75000))
    withdrawal_rate = float(assumptions.get("retirement_withdrawal_rate", 0.0425))
    inflation_rate = float(assumptions.get("inflation_rate", 0.025))
    apply_market_downturn = assumptions.get("apply_market_downturn_scenario", "0") == "1"
    market_downturn_percent = float(assumptions.get("market_downturn_percent", 0.25)) if apply_market_downturn else 0.0
    market_downturn_age = int(float(assumptions.get("market_downturn_age", retirement_age)))

    use_routed_cashflow = assumptions.get("use_routed_cashflow", "1") == "1"
    annual_contrib_by_account = {}
    for _, row in contribs.iterrows():
        if use_routed_cashflow and row["tax_bucket"] in {"Cash", "Taxable"}:
            continue
        annual_contrib_by_account[row["account_id"]] = annual_contrib_by_account.get(row["account_id"], 0) + annualized_contribution(row["amount"], row["frequency"])

    current = latest[["account_id", "name", "account_type", "tax_bucket", "target_balance", "balance"]].copy()
    current["balance"] = current["balance"].fillna(0).astype(float)
    current["target_balance"] = current["target_balance"].fillna(0).astype(float)
    current = apply_cash_cap_and_overflow(current, assumptions)

    rows = []
    retirement_start_total = None
    base_withdrawal = 0.0
    cumulative_estimated_tax = 0.0
    previous_roth_conversion = 0.0

    for year in range(0, years + 1):
        age = current_age + year

        if apply_market_downturn and age == market_downturn_age:
            affected_idxs = current.index[current["tax_bucket"].isin(["Taxable", "Tax-deferred", "Roth"])].tolist()
            for idx in affected_idxs:
                current.at[idx, "balance"] = float(current.at[idx, "balance"]) * (1 - market_downturn_percent)

        total = current["balance"].sum()
        is_retired = age >= retirement_age

        if is_retired and retirement_start_total is None:
            retirement_start_total = total
            # The annual spending target is entered in today's dollars.
            # Inflate it to the first retirement year, then continue inflating
            # from there in later projection years. The withdrawal-rate setting
            # is kept as a reference / reasonableness check, not as the spending driver.
            base_withdrawal = inflate_today_dollars_to_age(annual_spend_retirement, age, assumptions)

        gross_withdrawal = 0.0
        social_security = 0.0
        net_withdrawal = 0.0
        roth_conversion = 0.0
        roth_conversion_tax = 0.0
        roth_strategy = ""
        roth_effective_rate = 0.0
        roth_marginal_bracket_reached = "—"
        roth_conversion_headroom = 0.0
        roth_tax_shortfall = 0.0
        estimated_rmd = 0.0
        taxable_social_security = 0.0
        taxable_brokerage_withdrawal = 0.0
        taxable_gain_realized = 0.0
        capital_gains_tax = 0.0
        ordinary_income_tax = 0.0
        ordinary_taxable_income = 0.0
        total_annual_tax = 0.0
        ordinary_marginal_bracket = "—"
        aca_magi_before_conversion = 0.0
        aca_magi_after_conversion = 0.0
        aca_magi_limit_value = aca_magi_limit(assumptions)
        aca_headroom_after_conversion = 0.0
        aca_status = "Not ACA year"
        aca_fpl_percent = 0.0
        aca_annual_premium_cost = 0.0
        aca_annual_oop_estimate = 0.0
        aca_annual_healthcare_cost = 0.0
        aca_estimated_subsidy = 0.0
        aca_cost_status = "Not ACA year"
        marginal_bracket_reached = ""
        purchase_amount = 0.0
        purchase_names = ""
        purchase_funding_strategy = ""
        purchase_cash_withdrawal = 0.0
        purchase_taxable_withdrawal = 0.0
        purchase_tax_deferred_withdrawal = 0.0
        purchase_roth_withdrawal = 0.0
        purchase_taxable_gain = 0.0
        purchase_capital_gains_tax = 0.0
        purchase_ordinary_income_tax = 0.0
        purchase_estimated_tax = 0.0
        purchase_shortfall = 0.0

        active_purchase_rows = purchases_df[purchases_df["purchase_age"].astype(int) == age] if not purchases_df.empty else pd.DataFrame()
        if not active_purchase_rows.empty:
            purchase_names = ", ".join(active_purchase_rows["name"].astype(str).tolist())
            purchase_funding_strategy = "; ".join(active_purchase_rows["funding_strategy"].astype(str).unique().tolist())

        if is_retired:
            years_since_retirement = age - retirement_age
            gross_withdrawal = base_withdrawal * ((1 + inflation_rate) ** years_since_retirement)
            if age >= social_security_age:
                ss_years = age - social_security_age
                social_security = social_security_annual * ((1 + inflation_rate) ** ss_years)

            portfolio_need_before_rmd = max(gross_withdrawal - social_security, 0.0)
            tax_deferred_before_actions = float(current.loc[current["tax_bucket"] == "Tax-deferred", "balance"].sum())
            estimated_rmd = min(estimate_rmd(age, tax_deferred_before_actions, assumptions), tax_deferred_before_actions)
            net_withdrawal = max(portfolio_need_before_rmd - estimated_rmd, 0.0)

            # Estimate capital gains from spending withdrawals that must come from taxable brokerage.
            # This is calculated before choosing a bracket-fill conversion so Social Security
            # and capital gains can reduce available 12%/22% bracket room.
            taxable_brokerage_withdrawal = estimate_taxable_draw_for_spending(current, net_withdrawal)
            taxable_gain_realized, capital_gains_tax = estimate_capital_gains_tax(taxable_brokerage_withdrawal, assumptions)

            (
                roth_conversion,
                roth_conversion_tax,
                roth_strategy,
                roth_effective_rate,
                roth_marginal_bracket_reached,
                roth_conversion_headroom,
            ) = calculate_roth_conversion_amount(
                age,
                current,
                assumptions,
                roth_scenario,
                social_security=social_security,
                estimated_rmd=estimated_rmd,
                taxable_gain_income=taxable_gain_realized,
                previous_conversion_amount=previous_roth_conversion,
            )

            ordinary_income_tax, taxable_social_security, ordinary_taxable_income = estimate_ordinary_income_tax(
                rmd=estimated_rmd,
                roth_conversion=roth_conversion,
                social_security=social_security,
                assumptions=assumptions,
            )

            total_annual_tax = ordinary_income_tax + capital_gains_tax
            ordinary_marginal_bracket = marginal_bracket_label_for_taxable_income(ordinary_taxable_income, assumptions)

            # One-time purchase planner. Purchases are modeled after normal annual
            # spending and Roth conversion decisions, but the planning row shows
            # the expected source buckets and estimated incremental taxes.
            if not active_purchase_rows.empty:
                purchase_ordinary_base_income = estimated_rmd + roth_conversion + float(assumptions.get("base_taxable_income_retirement", 0.0) or 0.0)
                for _, purchase in active_purchase_rows.iterrows():
                    plan = estimate_purchase_funding_plan(
                        current=current,
                        amount=float(purchase.get("amount", 0.0) or 0.0),
                        funding_strategy=str(purchase.get("funding_strategy", PURCHASE_FUNDING_STRATEGIES[0])),
                        assumptions=assumptions,
                        social_security=social_security,
                        ordinary_income_before_purchase=purchase_ordinary_base_income,
                    )
                    purchase_amount += float(plan["Purchase Amount"])
                    purchase_cash_withdrawal += float(plan["Purchase Cash Withdrawal"])
                    purchase_taxable_withdrawal += float(plan["Purchase Taxable Withdrawal"])
                    purchase_tax_deferred_withdrawal += float(plan["Purchase Tax-deferred Withdrawal"])
                    purchase_roth_withdrawal += float(plan["Purchase Roth Withdrawal"])
                    purchase_taxable_gain += float(plan["Purchase Taxable Gain"])
                    purchase_capital_gains_tax += float(plan["Purchase Capital Gains Tax"])
                    purchase_ordinary_income_tax += float(plan["Purchase Ordinary Income Tax"])
                    purchase_estimated_tax += float(plan["Purchase Estimated Tax"])
                    purchase_shortfall += float(plan["Purchase Shortfall"])
                total_annual_tax += purchase_estimated_tax

            cumulative_estimated_tax += total_annual_tax

        # ACA MAGI must include purchase funding effects too.
        # Tax-deferred purchase dollars are ordinary income; taxable-purchase gains
        # also increase ACA MAGI. Without this, the purchase optimizer can wrongly
        # prefer "tax-deferred only" during ACA years even though it would blow
        # through the subsidy cliff.
        ordinary_income_for_aca = (
            float(assumptions.get("base_taxable_income_retirement", 0.0))
            + max(float(estimated_rmd), 0.0)
            + max(float(purchase_tax_deferred_withdrawal), 0.0)
        )
        taxable_gain_income_for_aca = (
            max(float(taxable_gain_realized), 0.0)
            + max(float(purchase_taxable_gain), 0.0)
        )
        aca_magi_before_conversion = estimate_aca_magi(
            pre_conversion_income=ordinary_income_for_aca,
            conversion_amount=0.0,
            social_security=float(social_security),
            taxable_gain_income=float(taxable_gain_income_for_aca),
        )
        aca_magi_after_conversion = estimate_aca_magi(
            pre_conversion_income=ordinary_income_for_aca,
            conversion_amount=float(roth_conversion),
            social_security=float(social_security),
            taxable_gain_income=float(taxable_gain_income_for_aca),
        )
        aca_magi_limit_value = aca_magi_limit(assumptions)
        aca_headroom_after_conversion = max(aca_magi_limit_value - aca_magi_after_conversion, 0.0)
        if aca_start_age(assumptions) <= age <= aca_end_age(assumptions):
            aca_status = "Under target" if aca_magi_after_conversion <= aca_magi_limit_value else "Over target"
            aca_cost_details = estimate_aca_annual_cost_from_magi(aca_magi_after_conversion, assumptions)
            aca_fpl_percent = float(aca_cost_details["ACA FPL Percent"])
            aca_annual_premium_cost = float(aca_cost_details["ACA Annual Premium Cost"])
            aca_annual_oop_estimate = float(aca_cost_details["ACA Annual OOP Estimate"])
            aca_annual_healthcare_cost = float(aca_cost_details["ACA Annual Healthcare Cost"])
            aca_estimated_subsidy = float(aca_cost_details["ACA Estimated Subsidy"])
            aca_cost_status = str(aca_cost_details["ACA Cost Status"])
        else:
            aca_status = "Not ACA year"
            aca_cost_status = "Not ACA year"

        by_bucket = current.groupby("tax_bucket")["balance"].sum().to_dict()
        rows.append({
            "Year": current_year + year,
            "Age": age,
            "Retired": is_retired,
            "Total": total,
            "Cash": by_bucket.get("Cash", 0),
            "Taxable": by_bucket.get("Taxable", 0),
            "Tax-deferred": by_bucket.get("Tax-deferred", 0),
            "Roth": by_bucket.get("Roth", 0),
            "Gross Withdrawal Need": gross_withdrawal,
            "Social Security": social_security,
            "Net Portfolio Withdrawal": net_withdrawal,
            "Estimated RMD": estimated_rmd,
            "Roth Conversion": roth_conversion,
            "Estimated Roth Tax": roth_conversion_tax,
            "Roth Strategy": roth_strategy,
            "Effective Conversion Tax Rate": roth_effective_rate,
            "Marginal Bracket Reached": roth_marginal_bracket_reached,
            "Conversion Headroom Remaining": roth_conversion_headroom,
            "Ordinary Marginal Bracket": ordinary_marginal_bracket,
            "Tax Payment Shortfall": roth_tax_shortfall,
            "Taxable Social Security": taxable_social_security,
            "Ordinary Taxable Income": ordinary_taxable_income,
            "Income Tax": ordinary_income_tax,
            "Taxable Brokerage Withdrawal": taxable_brokerage_withdrawal,
            "Taxable Gain Realized": taxable_gain_realized,
            "ACA MAGI Before Conversion": aca_magi_before_conversion,
            "ACA MAGI After Conversion": aca_magi_after_conversion,
            "ACA MAGI Limit": aca_magi_limit_value,
            "ACA Headroom After Conversion": aca_headroom_after_conversion,
            "ACA Status": aca_status,
            "ACA FPL Percent": aca_fpl_percent,
            "ACA Annual Premium Cost": aca_annual_premium_cost,
            "ACA Annual OOP Estimate": aca_annual_oop_estimate,
            "ACA Annual Healthcare Cost": aca_annual_healthcare_cost,
            "ACA Estimated Subsidy": aca_estimated_subsidy,
            "ACA Cost Status": aca_cost_status,
            "Capital Gains Tax": capital_gains_tax,
            "Estimated Total Annual Tax": total_annual_tax,
            "Cumulative Estimated Tax": cumulative_estimated_tax,
            "Purchase Names": purchase_names,
            "Purchase Amount": purchase_amount,
            "Purchase Funding Strategy": purchase_funding_strategy,
            "Purchase Cash Withdrawal": purchase_cash_withdrawal,
            "Purchase Taxable Withdrawal": purchase_taxable_withdrawal,
            "Purchase Tax-deferred Withdrawal": purchase_tax_deferred_withdrawal,
            "Purchase Roth Withdrawal": purchase_roth_withdrawal,
            "Purchase Taxable Gain": purchase_taxable_gain,
            "Purchase Capital Gains Tax": purchase_capital_gains_tax,
            "Purchase Ordinary Income Tax": purchase_ordinary_income_tax,
            "Purchase Estimated Tax": purchase_estimated_tax,
            "Purchase Shortfall": purchase_shortfall,
        })

        previous_roth_conversion = roth_conversion

        if year == years:
            break

        layoff_factor = 1.0
        if apply_layoff_pause and not is_retired:
            layoff_factor = max(1 - (min(layoff_pause_months, 12) / 12), 0)

        # Grow balances and add only account-specific contributions for non-routed accounts.
        for idx, acct in current.iterrows():
            r = projected_return_for_bucket(acct["tax_bucket"], assumptions)
            annual_contrib = 0.0 if is_retired else annual_contrib_by_account.get(acct["account_id"], 0.0) * layoff_factor
            current.at[idx, "balance"] = float(acct["balance"]) * (1 + r) + annual_contrib

        if not is_retired:
            current = route_pre_retirement_cashflow(current, assumptions, layoff_factor)

        if not is_retired and assumptions.get("use_routed_cashflow", "1") != "1":
            current = apply_cash_cap_and_overflow(current, assumptions)

        if is_retired:
            # Apply the Roth conversion and pay the conversion-tax portion from the selected source.
            if roth_conversion > 0:
                current, tax_paid, roth_tax_shortfall = apply_roth_conversion(current, roth_conversion, roth_conversion_tax, assumptions, roth_scenario)

            # Satisfy the forced RMD. The RMD can cover spending; any excess is moved to cash.
            portfolio_need_before_rmd = max(gross_withdrawal - social_security, 0.0)
            rmd_used_for_spending = min(estimated_rmd, portfolio_need_before_rmd)
            rmd_excess = max(estimated_rmd - rmd_used_for_spending, 0.0)
            if estimated_rmd > 0:
                current, remaining_rmd, _ = draw_from_bucket_with_amount(current, "Tax-deferred", estimated_rmd)
                if rmd_excess > 0:
                    current = add_to_account(current, assumptions.get("cash_account_name", "Cash Bridge Savings"), "Cash", rmd_excess)

            # Withdraw remaining spending need after Social Security and RMD.
            if net_withdrawal > 0:
                current, spending_shortfall, spending_draws = draw_spending_from_portfolio(current, net_withdrawal)

            # Pay non-conversion income tax and capital-gains tax. Conversion tax was already handled above.
            additional_tax_due = max(ordinary_income_tax - roth_conversion_tax, 0.0) + capital_gains_tax
            if additional_tax_due > 0:
                current, tax_shortfall, tax_draws = draw_spending_from_portfolio(current, additional_tax_due)

            # Apply one-time planned purchases after annual spending/tax flows so
            # their impact is visible in subsequent projected balances.
            if not active_purchase_rows.empty:
                for _, purchase in active_purchase_rows.iterrows():
                    current, purchase_draws = apply_purchase_to_portfolio(
                        current,
                        amount=float(purchase.get("amount", 0.0) or 0.0),
                        funding_strategy=str(purchase.get("funding_strategy", PURCHASE_FUNDING_STRATEGIES[0])),
                    )
                    purchase_tax_due = estimate_purchase_funding_plan(
                        current=current,
                        amount=0.0,
                        funding_strategy=str(purchase.get("funding_strategy", PURCHASE_FUNDING_STRATEGIES[0])),
                        assumptions=assumptions,
                    ).get("Purchase Estimated Tax", 0.0)
                # The estimated purchase tax is included in the year-row tax
                # totals. Draw it here using the normal tax/spending order.
                if purchase_estimated_tax > 0:
                    current, purchase_tax_shortfall_actual, purchase_tax_draws = draw_spending_from_portfolio(current, purchase_estimated_tax)

    return pd.DataFrame(rows)

def format_money(value: float) -> str:
    return f"${value:,.0f}"


def direction_arrow(current_value: float, previous_value: float | None, tolerance: float = 1.0) -> str:
    """Small visual indicator for table cells that changed from the prior row."""
    if previous_value is None:
        return ""
    change = float(current_value) - float(previous_value)
    if change > tolerance:
        return " 🟢↑"
    if change < -tolerance:
        return " 🔴↓"
    return ""


def format_money_with_direction(current_value: float, previous_value: float | None) -> str:
    return f"{format_money(float(current_value))}{direction_arrow(float(current_value), previous_value)}"


def purchasing_power_factor_for_age(age: float, assumptions: Dict[str, str]) -> float:
    """Return the multiplier that converts a future nominal dollar to today's purchasing power.

    Example: if inflation is 2.5% and the row is 20 years in the future,
    $1 future dollar is worth roughly 1 / 1.025^20 in today's dollars.
    """
    current_age = float(assumptions.get("current_age", 52))
    inflation = float(assumptions.get("inflation_rate", 0.025))
    years_forward = max(float(age) - current_age, 0.0)
    return 1 / ((1 + inflation) ** years_forward) if inflation > -1 else 1.0


def value_in_today_dollars(value: float, age: float, assumptions: Dict[str, str]) -> float:
    return float(value) * purchasing_power_factor_for_age(age, assumptions)


def inflate_today_dollars_to_age(value_today: float, age: float, assumptions: Dict[str, str]) -> float:
    """Convert a today's-dollar target into nominal dollars at a future age."""
    current_age = float(assumptions.get("current_age", 52))
    inflation = float(assumptions.get("inflation_rate", 0.025))
    years_forward = max(float(age) - current_age, 0.0)
    return float(value_today) * ((1 + inflation) ** years_forward)


def first_retirement_year_spending(assumptions: Dict[str, str]) -> float:
    """Nominal first-retirement-year spending from today's-dollar annual target."""
    retirement_age = float(assumptions.get("retirement_age", 58))
    if assumptions.get("apply_layoff_pause_scenario", "0") == "1" and assumptions.get("extend_retirement_for_layoff", "0") == "1":
        retirement_age += round(float(assumptions.get("layoff_pause_months", 0)) / 12)
    annual_spend_today = float(assumptions.get("annual_spend_retirement", 75000))
    return inflate_today_dollars_to_age(annual_spend_today, retirement_age, assumptions)


def add_today_dollar_columns(df: pd.DataFrame, assumptions: Dict[str, str], columns: list[str]) -> pd.DataFrame:
    """Add inflation-adjusted purchasing-power columns for selected money fields."""
    out = df.copy()
    if "Age" not in out.columns:
        return out
    factors = out["Age"].map(lambda age: purchasing_power_factor_for_age(float(age), assumptions))
    for col in columns:
        if col in out.columns:
            out[f"{col} - Today's $"] = out[col].astype(float) * factors
    return out


WITHDRAWAL_BUCKET_ORDER = ["Cash", "Taxable", "Tax-deferred", "Roth"]


def withdrawal_plan_for_row(row: pd.Series) -> dict[str, float]:
    """Derive a practical bucket withdrawal plan from a projection row.

    This is a planning view, not tax advice. It uses the app's modeled order:
    Cash -> Taxable -> Tax-deferred -> Roth for discretionary spending and taxes,
    while showing RMDs as a separate forced Tax-deferred distribution.
    """
    balances = {
        "Cash": max(float(row.get("Cash", 0.0) or 0.0), 0.0),
        "Taxable": max(float(row.get("Taxable", 0.0) or 0.0), 0.0),
        "Tax-deferred": max(float(row.get("Tax-deferred", 0.0) or 0.0), 0.0),
        "Roth": max(float(row.get("Roth", 0.0) or 0.0), 0.0),
    }
    rmd = max(float(row.get("Estimated RMD", 0.0) or 0.0), 0.0)
    gross_need = max(float(row.get("Gross Withdrawal Need", 0.0) or 0.0), 0.0)
    social_security = max(float(row.get("Social Security", 0.0) or 0.0), 0.0)
    net_portfolio_withdrawal = max(float(row.get("Net Portfolio Withdrawal", 0.0) or 0.0), 0.0)
    annual_tax = max(float(row.get("Estimated Total Annual Tax", 0.0) or 0.0), 0.0)

    rmd_used_for_spending = min(rmd, max(gross_need - social_security, 0.0))
    rmd_excess_to_cash = max(rmd - rmd_used_for_spending, 0.0)

    planned = {
        "Planned Cash Withdrawal": 0.0,
        "Planned Taxable Withdrawal": 0.0,
        "Planned Tax-deferred Withdrawal": rmd,
        "Planned Roth Withdrawal": 0.0,
        "RMD Used For Spending": rmd_used_for_spending,
        "RMD Excess To Cash": rmd_excess_to_cash,
        "Planned Tax Payments": annual_tax,
        "Planned Spending From Portfolio": net_portfolio_withdrawal,
        "Total Planned Portfolio Withdrawal": rmd + net_portfolio_withdrawal + annual_tax,
        "Withdrawal Planning Shortfall": 0.0,
    }

    # Reduce tax-deferred balance by the forced RMD before any additional fallback draw.
    balances["Tax-deferred"] = max(balances["Tax-deferred"] - rmd, 0.0)

    # Discretionary portfolio draw = spending not covered by Social Security/RMD + estimated taxes.
    remaining = net_portfolio_withdrawal + annual_tax
    for bucket in WITHDRAWAL_BUCKET_ORDER:
        if remaining <= 0:
            break
        draw = min(balances[bucket], remaining)
        planned[f"Planned {bucket} Withdrawal"] += draw
        balances[bucket] -= draw
        remaining -= draw

    planned["Withdrawal Planning Shortfall"] = max(remaining, 0.0)
    return planned


def add_withdrawal_plan_columns(projection: pd.DataFrame) -> pd.DataFrame:
    out = projection.copy()
    if out.empty:
        return out
    plans = out.apply(withdrawal_plan_for_row, axis=1, result_type="expand")
    for col in plans.columns:
        out[col] = plans[col]
    return out


def render_withdrawal_planner(projection: pd.DataFrame, assumptions: Dict[str, str]) -> None:
    st.subheader("Withdrawal planning by year")
    st.caption(
        "Pick an age/year to see the modeled withdrawal plan by bucket. RMDs are shown as forced "
        "Tax-deferred distributions. Remaining spending and estimated taxes are drawn Cash → Taxable → Tax-deferred → Roth."
    )
    plan_projection = add_withdrawal_plan_columns(projection_with_after_tax_estimate(projection, assumptions))
    retired_rows = plan_projection[plan_projection["Retired"] == True]
    if retired_rows.empty:
        st.info("No retirement years are included in the current projection window.")
        return

    options = [f"Age {int(r.Age)} / Year {int(r.Year)}" for r in retired_rows.itertuples()]
    selected = st.selectbox("Planning year", options, help="Choose the projection year to create a bucket-by-bucket withdrawal plan.")
    selected_pos = options.index(selected)
    row = retired_rows.iloc[selected_pos]

    bucket_rows = [
        {
            "Bucket": "Cash",
            "Modeled withdrawal": row["Planned Cash Withdrawal"],
            "Notes": "Used first for spending and tax cash needs after Social Security/RMD offsets.",
        },
        {
            "Bucket": "Taxable brokerage",
            "Modeled withdrawal": row["Planned Taxable Withdrawal"],
            "Notes": "Used after Cash. Gain portion is estimated using the taxable cost-basis ratio assumption.",
        },
        {
            "Bucket": "Tax-deferred / 401k / IRA",
            "Modeled withdrawal": row["Planned Tax-deferred Withdrawal"],
            "Notes": "Includes the forced RMD. Additional use happens only if Cash/Taxable are insufficient.",
        },
        {
            "Bucket": "Roth",
            "Modeled withdrawal": row["Planned Roth Withdrawal"],
            "Notes": "Last-resort bucket in this model; usually preserved for tax-free growth/flexibility.",
        },
    ]
    bucket_df = pd.DataFrame(bucket_rows)
    bucket_df["Modeled withdrawal"] = bucket_df["Modeled withdrawal"].map(lambda x: format_money(float(x)))

    summary_rows = [
        {"Item": "Gross spending need", "Amount": row["Gross Withdrawal Need"], "Notes": "Annual retirement spending target inflated to this age."},
        {"Item": "Social Security", "Amount": row["Social Security"], "Notes": "Modeled SS benefit for this age."},
        {"Item": "RMD used for spending", "Amount": row["RMD Used For Spending"], "Notes": "Part of the RMD that covers spending need."},
        {"Item": "RMD excess moved to cash", "Amount": row["RMD Excess To Cash"], "Notes": "Forced distribution beyond spending need; modeled as moving to Cash."},
        {"Item": "Spending from portfolio after SS/RMD", "Amount": row["Planned Spending From Portfolio"], "Notes": "Remaining spending need after Social Security and RMD."},
        {"Item": "Estimated tax payments", "Amount": row["Planned Tax Payments"], "Notes": "Modeled ordinary + capital gains tax for this year."},
        {"Item": "Total portfolio outflow", "Amount": row["Total Planned Portfolio Withdrawal"], "Notes": "RMD + remaining spending draw + estimated tax payments."},
        {"Item": "Planning shortfall", "Amount": row["Withdrawal Planning Shortfall"], "Notes": "Amount not covered by available modeled buckets."},
    ]
    summary_df = pd.DataFrame(summary_rows)
    summary_df["Amount"] = summary_df["Amount"].map(lambda x: format_money(float(x)))

    c1, c2 = st.columns([1, 1])
    with c1:
        st.markdown("#### Bucket withdrawal plan")
        st.dataframe(bucket_df, use_container_width=True, hide_index=True)
    with c2:
        st.markdown("#### Planning summary")
        st.dataframe(summary_df, use_container_width=True, hide_index=True)



# -----------------------------
# UI helpers
# -----------------------------

def account_select(accounts: pd.DataFrame, label: str, key: str) -> int:
    name_to_id = dict(zip(accounts["name"], accounts["id"]))
    selected = st.selectbox(label, list(name_to_id.keys()), key=key)
    return int(name_to_id[selected])


# -----------------------------
# Pages
# -----------------------------

def projection_with_after_tax_estimate(projection: pd.DataFrame, assumptions: Dict[str, str]) -> pd.DataFrame:
    """Add after-tax and annual-tax-planning estimates for each projection year.

    Newer projections already include RMD, Social Security, ordinary-income tax,
    and capital-gains tax columns. For older projections, this function fills in
    a backwards-compatible RMD-only estimate.
    """
    future_tax_rate = float(assumptions.get("estimate_future_tax_rate_without_conversions", 0.22))
    out = projection.copy()
    out["Estimated Future Tax Drag"] = out.get("Tax-deferred", 0.0) * future_tax_rate
    out["After-tax Estimate"] = out["Total"] - out["Estimated Future Tax Drag"]

    if "Estimated RMD" not in out.columns or "Estimated Total Annual Tax" not in out.columns:
        rmd_values = out.apply(lambda r: estimate_annual_tax_on_tax_deferred_balance(r, assumptions), axis=1)
        out["Estimated RMD"] = [x[0] for x in rmd_values]
        out["Estimated Annual Tax on RMD"] = [x[1] for x in rmd_values]
        out["Estimated Total Annual Tax"] = out.get("Estimated Roth Tax", 0.0) + out["Estimated Annual Tax on RMD"]
        out["Cumulative Estimated Tax"] = out["Estimated Total Annual Tax"].cumsum()
    else:
        if "Estimated Annual Tax on RMD" not in out.columns:
            out["Estimated Annual Tax on RMD"] = out.get("Income Tax", 0.0)
        if "Cumulative Estimated Tax" not in out.columns:
            out["Cumulative Estimated Tax"] = out["Estimated Total Annual Tax"].cumsum()

    out = add_withdrawal_plan_columns(out)
    return out


def comparison_through_age(assumptions: Dict[str, str]) -> int:
    """Age used for lifetime tax / healthcare comparison metrics."""
    return int(float(assumptions.get("lifetime_comparison_age", 85)))


def projection_row_at_or_before_age(projection: pd.DataFrame, age: int) -> pd.Series:
    """Return the row for age, or the last projected row before it."""
    if projection.empty:
        return pd.Series(dtype="object")
    rows = projection[projection["Age"].astype(int) <= int(age)]
    if rows.empty:
        return projection.iloc[0]
    return rows.iloc[-1]


def projection_through_age(projection: pd.DataFrame, age: int) -> pd.DataFrame:
    if projection.empty:
        return projection.copy()
    return projection[projection["Age"].astype(int) <= int(age)].copy()



def aca_expected_contribution_rate(fpl_percent: float, assumptions: Dict[str, str]) -> float:
    """Approximate ACA household expected contribution rate.

    The app defaults to the 2026 post-enhanced-subsidy-style curve:
    under 133% FPL: 2.10%
    133–150%: 3.14%–4.19%
    150–200%: 4.19%–6.60%
    200–250%: 6.60%–8.44%
    250–300%: 8.44%–9.96%
    300–400%: 9.96%
    over 400%: no subsidy if the cliff is enabled.
    """
    fpl = max(float(fpl_percent), 0.0)
    if fpl < 133:
        return 0.0210
    if fpl < 150:
        return 0.0314 + (fpl - 133) / (150 - 133) * (0.0419 - 0.0314)
    if fpl < 200:
        return 0.0419 + (fpl - 150) / (200 - 150) * (0.0660 - 0.0419)
    if fpl < 250:
        return 0.0660 + (fpl - 200) / (250 - 200) * (0.0844 - 0.0660)
    if fpl < 300:
        return 0.0844 + (fpl - 250) / (300 - 250) * (0.0996 - 0.0844)
    if fpl <= 400:
        return 0.0996
    return 1.0 if assumptions.get("aca_use_400_fpl_cliff", "1") == "1" else 0.0996


def estimate_aca_annual_cost_from_magi(magi: float, assumptions: Dict[str, str]) -> dict[str, float | str]:
    """Estimate household ACA premium/OOP cost for one year.

    Inputs are household annual numbers, not per-person numbers.
    """
    magi = max(float(magi), 0.0)
    fpl100 = max(float(assumptions.get("aca_fpl_100", 21150)), 1.0)
    fpl_percent = 100.0 * magi / fpl100
    target_limit = aca_magi_limit(assumptions)
    mode = str(assumptions.get("aca_model_mode", "Formula"))

    # Legacy/manual mode preserves the old simple under/over target model.
    if mode.lower().startswith("manual"):
        full_premium = float(assumptions.get("aca_full_annual_premium", 18000))
        subsidized_premium = float(assumptions.get("aca_subsidized_annual_premium", 6000))
        premium = subsidized_premium if magi <= target_limit else full_premium
        subsidy = max(full_premium - premium, 0.0)
        status = "Under target" if magi <= target_limit else "Over target"
    else:
        benchmark = float(assumptions.get("aca_benchmark_silver_annual_premium", assumptions.get("aca_full_annual_premium", 18000)))
        selected_plan = float(assumptions.get("aca_selected_plan_annual_premium", benchmark))
        cliff_enabled = assumptions.get("aca_use_400_fpl_cliff", "1") == "1"
        if cliff_enabled and fpl_percent > 400:
            expected_contribution = benchmark
            subsidy = 0.0
            status = "Over 400% FPL cliff"
        else:
            rate = aca_expected_contribution_rate(fpl_percent, assumptions)
            expected_contribution = magi * rate
            subsidy = max(benchmark - expected_contribution, 0.0)
            status = "Subsidy eligible" if subsidy > 0 else "No subsidy"
        premium = max(selected_plan - subsidy, 0.0)

    oop = 0.0
    if assumptions.get("aca_include_oop_estimate", "0") == "1":
        oop = float(assumptions.get("aca_oop_if_under_target", 3000)) if magi <= target_limit else float(assumptions.get("aca_oop_if_over_target", 6000))

    return {
        "ACA Annual Premium Cost": max(float(premium), 0.0),
        "ACA Annual OOP Estimate": max(float(oop), 0.0),
        "ACA Annual Healthcare Cost": max(float(premium) + float(oop), 0.0),
        "ACA Estimated Subsidy": max(float(subsidy), 0.0),
        "ACA FPL Percent": fpl_percent,
        "ACA Cost Status": status,
    }


def estimate_aca_premium_cost(projection: pd.DataFrame, assumptions: Dict[str, str], through_age: int) -> float:
    """Estimate household ACA cost through a target age.

    This uses household annual premium assumptions. In Formula mode, it estimates
    premium tax credits from MAGI, FPL, benchmark silver premium, selected-plan
    premium, and the optional 400% FPL cliff. In Manual mode, it uses the older
    under-target / over-target premium assumptions.
    """
    if projection.empty:
        return 0.0
    start_age = aca_start_age(assumptions)
    end_age = min(aca_end_age(assumptions), int(through_age))
    if end_age < start_age:
        return 0.0

    total = 0.0
    for _, row in projection.iterrows():
        age = int(row.get("Age", 0))
        if not (start_age <= age <= end_age):
            continue
        magi = float(row.get("ACA MAGI After Conversion", 0.0) or 0.0)
        total += float(estimate_aca_annual_cost_from_magi(magi, assumptions)["ACA Annual Healthcare Cost"])
    return total

def estimate_irmaa_cost(projection: pd.DataFrame, assumptions: Dict[str, str], through_age: int) -> float:
    """Very simplified Medicare IRMAA planning estimate.

    Uses an editable MAGI threshold and annual surcharge. This is intentionally
    approximate because IRMAA uses a two-year lookback and multiple brackets.
    """
    if projection.empty:
        return 0.0
    medicare_age = int(float(assumptions.get("aca_medicare_age", 65)))
    threshold = float(assumptions.get("irmaa_magi_threshold", 206000))
    surcharge = float(assumptions.get("irmaa_annual_surcharge", 0))
    total = 0.0
    for _, row in projection.iterrows():
        age = int(row.get("Age", 0))
        if age < medicare_age or age > int(through_age):
            continue
        magi = float(row.get("ACA MAGI After Conversion", 0.0) or 0.0)
        if magi > threshold:
            total += surcharge
    return total


def lifetime_strategy_metrics(projection: pd.DataFrame, assumptions: Dict[str, str]) -> dict:
    """Decision-oriented cumulative tax and healthcare metrics."""
    through_age = comparison_through_age(assumptions)
    subset = projection_through_age(projection, through_age)
    if subset.empty:
        subset = projection.copy()

    row = projection_row_at_or_before_age(projection, through_age)
    subset = projection_with_after_tax_estimate(subset, assumptions) if not subset.empty else subset
    row_df = projection_with_after_tax_estimate(pd.DataFrame([row]), assumptions) if not row.empty else pd.DataFrame()
    row_after_tax = float(row_df.iloc[0].get("After-tax Estimate", 0.0)) if not row_df.empty else 0.0

    total_tax = float(subset["Estimated Total Annual Tax"].sum()) if "Estimated Total Annual Tax" in subset else 0.0
    roth_tax = float(subset["Estimated Roth Tax"].sum()) if "Estimated Roth Tax" in subset else 0.0
    ordinary_tax = float(subset["Income Tax"].sum()) if "Income Tax" in subset else 0.0
    cap_gains_tax = float(subset["Capital Gains Tax"].sum()) if "Capital Gains Tax" in subset else 0.0
    cumulative_rmds = float(subset["Estimated RMD"].sum()) if "Estimated RMD" in subset else 0.0
    aca_cost = estimate_aca_premium_cost(projection, assumptions, through_age)
    irmaa_cost = estimate_irmaa_cost(projection, assumptions, through_age)

    return {
        "Comparison Through Age": through_age,
        "Lifetime Estimated Tax": total_tax,
        "Lifetime Roth Conversion Tax": roth_tax,
        "Lifetime Ordinary Income Tax": ordinary_tax,
        "Lifetime Capital Gains Tax": cap_gains_tax,
        "Estimated ACA Premium Cost": aca_cost,
        "Estimated IRMAA Cost": irmaa_cost,
        "Total Tax + Healthcare Cost": total_tax + aca_cost + irmaa_cost,
        "Cumulative RMDs": cumulative_rmds,
        "Tax-deferred at Comparison Age": float(row.get("Tax-deferred", 0.0) or 0.0),
        "Roth at Comparison Age": float(row.get("Roth", 0.0) or 0.0),
        "After-tax Value at Comparison Age": row_after_tax,
    }


def summarize_projection(projection: pd.DataFrame, assumptions: Dict[str, str]) -> dict:
    projection = projection_with_after_tax_estimate(projection, assumptions)
    ending = projection.iloc[-1]
    cumulative_conversion_tax = float(projection["Estimated Roth Tax"].sum()) if "Estimated Roth Tax" in projection else 0.0
    total_converted = float(projection["Roth Conversion"].sum()) if "Roth Conversion" in projection else 0.0
    ending_tax_deferred = float(ending.get("Tax-deferred", 0.0))
    after_tax_ending = float(ending["After-tax Estimate"])
    lifetime_metrics = lifetime_strategy_metrics(projection, assumptions)

    return {
        **lifetime_metrics,
        "Ending Age": int(ending["Age"]),
        "Ending Total": float(ending["Total"]),
        "After-tax Ending Estimate": after_tax_ending,
        "Total Converted": total_converted,
        "Conversion Tax Paid": cumulative_conversion_tax,
        "Ending Cash": float(ending.get("Cash", 0.0)),
        "Ending Taxable": float(ending.get("Taxable", 0.0)),
        "Ending Tax-deferred": ending_tax_deferred,
        "Ending Roth": float(ending.get("Roth", 0.0)),
        "Estimated Future Tax Drag": float(ending.get("Estimated Future Tax Drag", 0.0)),
        "Max Conversion Bracket Reached": max_conversion_bracket_reached(projection, assumptions),
    }


def analyze_tax_break_even(projection: pd.DataFrame, baseline_projection: pd.DataFrame, assumptions: Dict[str, str]) -> dict:
    """Compare cumulative taxes versus the no-conversion baseline.

    Break-even means the scenario has recovered its upfront Roth-conversion tax
    cost through lower estimated future taxes on tax-deferred/RMD exposure.
    In other words: cumulative estimated taxes under the scenario are now less
    than or equal to cumulative estimated taxes under no conversions.
    """
    scenario = projection_with_after_tax_estimate(projection, assumptions)[
        ["Age", "Estimated Roth Tax", "Estimated RMD", "Income Tax", "Capital Gains Tax", "Estimated Total Annual Tax", "Cumulative Estimated Tax"]
    ].copy()
    baseline = projection_with_after_tax_estimate(baseline_projection, assumptions)[
        ["Age", "Estimated Total Annual Tax", "Cumulative Estimated Tax"]
    ].copy()

    merged = scenario.merge(baseline, on="Age", suffixes=("", " Baseline"))
    if merged.empty:
        return {
            "Break-even Age": None,
            "Years After Break-even": None,
            "Cumulative Tax Savings at End": 0.0,
        }

    merged["Cumulative Tax Savings"] = merged["Cumulative Estimated Tax Baseline"] - merged["Cumulative Estimated Tax"]
    has_conversion_tax = merged["Estimated Roth Tax"].sum() > 0
    positive_savings = merged[merged["Cumulative Tax Savings"] >= 0]

    break_even_age = None
    if has_conversion_tax and not positive_savings.empty:
        # Avoid counting age 0/current-year equality as break-even. Require at least
        # one conversion tax year and use the first age where cumulative savings
        # have paid back the conversion taxes.
        first_conversion_age = int(merged.loc[merged["Estimated Roth Tax"] > 0, "Age"].min())
        candidates = positive_savings[positive_savings["Age"] > first_conversion_age]
        if not candidates.empty:
            break_even_age = int(candidates.iloc[0]["Age"])

    ending_age = int(merged.iloc[-1]["Age"])
    years_after = None if break_even_age is None else max(ending_age - break_even_age, 0)
    return {
        "Break-even Age": break_even_age,
        "Years After Break-even": years_after,
        "Cumulative Tax Savings at End": float(merged.iloc[-1]["Cumulative Tax Savings"]),
    }


def build_roth_scenario_comparison(years: int, assumptions: Dict[str, str]) -> pd.DataFrame:
    scenarios = read_roth_scenarios(active_only=True)
    rows = []

    baseline_scenario = None
    if not scenarios.empty:
        no_conversion_rows = scenarios[scenarios["strategy"] == "No conversions"]
        if not no_conversion_rows.empty:
            baseline_scenario = no_conversion_rows.iloc[0].to_dict()

    baseline_projection = build_projection(years, assumptions, roth_scenario=baseline_scenario)
    baseline_summary = summarize_projection(baseline_projection, assumptions)

    if scenarios.empty:
        row = baseline_summary
        row["Scenario"] = "Current assumption"
        row["Strategy"] = "Current assumption"
        row["Target Bracket"] = "—"
        row["Tax Source"] = "—"
        row["Break-even Age"] = "Baseline"
        row["Years After Break-even"] = "Baseline"
        row["Cumulative Tax Savings at End"] = 0.0
        rows.append(row)
    else:
        for _, scenario_row in scenarios.iterrows():
            scenario = scenario_row.to_dict()
            projection = build_projection(years, assumptions, roth_scenario=scenario)
            row = summarize_projection(projection, assumptions)
            row["Scenario"] = scenario["name"]
            row["Strategy"] = scenario["strategy"]
            row["Target Bracket"] = scenario.get("target_bracket", "N/A") if scenario["strategy"] == "Fill bracket" else "N/A"
            row["Tax Source"] = scenario["pay_tax_from"]
            if scenario["strategy"] == "No conversions":
                row["Break-even Age"] = "Baseline"
                row["Years After Break-even"] = "Baseline"
                row["Cumulative Tax Savings at End"] = 0.0
            else:
                tax_break_even = analyze_tax_break_even(projection, baseline_projection, assumptions)
                row["Break-even Age"] = tax_break_even["Break-even Age"] if tax_break_even["Break-even Age"] is not None else "Not within window"
                row["Years After Break-even"] = tax_break_even["Years After Break-even"] if tax_break_even["Years After Break-even"] is not None else "—"
                row["Cumulative Tax Savings at End"] = tax_break_even["Cumulative Tax Savings at End"]
            rows.append(row)

    return pd.DataFrame(rows)


def get_dashboard_roth_scenario(assumptions: Dict[str, str]) -> tuple[dict | None, str, pd.DataFrame]:
    """Return the Roth scenario selected for Dashboard-style projections."""
    active_roth_scenarios = read_roth_scenarios(active_only=True)
    if active_roth_scenarios.empty:
        return None, "Legacy assumptions / no active scenario", active_roth_scenarios

    scenario_names = active_roth_scenarios["name"].tolist()
    saved_roth_name = assumptions.get("dashboard_roth_scenario_name", "No conversions")
    if saved_roth_name not in scenario_names:
        saved_roth_name = scenario_names[0]

    scenario = active_roth_scenarios.loc[
        active_roth_scenarios["name"] == saved_roth_name
    ].iloc[0].to_dict()
    return scenario, saved_roth_name, active_roth_scenarios


def effective_roth_conversion_window(assumptions: Dict[str, str], roth_scenario: dict | None) -> tuple[int, int, bool]:
    """Return scenario start/end age and whether SS should be shown as a taper point.

    Earlier versions used Social Security age as a hard stop. That was too
    rigid: after Social Security starts, conversions may still make sense, but
    bracket-fill amounts should usually shrink because taxable Social Security
    occupies part of the 12%/22% bracket. The projection already includes SS,
    RMDs, and taxable gains when calculating bracket room, so this function now
    leaves the scenario End Age intact and uses SS only as a visual/tax-context
    taper marker.
    """
    retirement_age = int(float(assumptions.get("retirement_age", 58)))
    social_security_age = int(float(assumptions.get("social_security_age", 67)))
    show_ss_taper = assumptions.get("roth_end_at_ss_age", "1") == "1"

    if roth_scenario is None:
        start_age = int(float(assumptions.get("roth_conversion_start_age", retirement_age)))
        scenario_end_age = int(float(assumptions.get("roth_conversion_end_age", social_security_age)))
    else:
        start_age = int(roth_scenario.get("start_age", retirement_age))
        scenario_end_age = int(roth_scenario.get("end_age", social_security_age))

    return start_age, scenario_end_age, show_ss_taper


def render_roth_window_callout(assumptions: Dict[str, str], roth_scenario: dict | None, plan_name: str) -> None:
    """Show the Roth conversion/taper window used by a projection."""
    strategy = "Legacy fixed" if roth_scenario is None else str(roth_scenario.get("strategy", "No conversions"))
    start_age, end_age, show_ss_taper = effective_roth_conversion_window(assumptions, roth_scenario)
    ss_age = int(float(assumptions.get("social_security_age", 67)))

    if strategy == "No conversions":
        st.info(f"Roth conversion window for **{plan_name}**: no conversions are modeled.")
        return

    if end_age < start_age:
        st.warning(f"Roth conversion window for **{plan_name}**: none. The scenario End Age ({end_age}) is before the Start Age ({start_age}).")
        return

    if show_ss_taper and start_age <= ss_age < end_age:
        st.info(
            f"Roth conversion plan for **{plan_name}**: peak low-income window age **{start_age} through {ss_age}**; "
            f"conversions can continue through age **{end_age}**, but bracket-fill amounts are reduced after Social Security starts."
        )
    elif show_ss_taper and ss_age <= start_age:
        st.info(
            f"Roth conversion plan for **{plan_name}**: age **{start_age} through {end_age}**. "
            f"Social Security is already active at the start, so bracket-fill amounts are automatically reduced by SS income."
        )
    else:
        st.info(f"Roth conversion plan for **{plan_name}**: age **{start_age} through {end_age}** using the scenario Start/End Age columns.")


def roth_window_summary_text(assumptions: Dict[str, str], roth_scenario: dict | None, plan_name: str) -> str:
    """Compact label for chart annotations."""
    strategy = "Legacy fixed" if roth_scenario is None else str(roth_scenario.get("strategy", "No conversions"))
    retirement_age = int(float(assumptions.get("retirement_age", 58)))
    ss_age = int(float(assumptions.get("social_security_age", 67)))
    start_age, end_age, show_ss_taper = effective_roth_conversion_window(assumptions, roth_scenario)

    if strategy == "No conversions":
        return f"Plan: {plan_name}<br>Roth Conv: none<br>Retire: {retirement_age} | SS: {ss_age}"

    if end_age < start_age:
        return f"Plan: {plan_name}<br>Roth Conv: none<br>Retire: {retirement_age} | SS: {ss_age}"

    if show_ss_taper and start_age <= ss_age < end_age:
        return (
            f"Plan: {plan_name}"
            f"<br>Roth Conv: peak {start_age}–{ss_age}"
            f"<br>taper {ss_age + 1}–{end_age}"
            f"<br>Retire: {retirement_age} | SS: {ss_age}"
        )

    if show_ss_taper and ss_age <= start_age:
        return (
            f"Plan: {plan_name}"
            f"<br>Roth Conv: reduced by SS"
            f"<br>ages {start_age}–{end_age}"
            f"<br>Retire: {retirement_age} | SS: {ss_age}"
        )

    return (
        f"Plan: {plan_name}"
        f"<br>Roth Conv: ages {start_age}–{end_age}"
        f"<br>Retire: {retirement_age} | SS: {ss_age}"
    )


def add_roth_window_annotation(fig: go.Figure, assumptions: Dict[str, str], roth_scenario: dict | None, plan_name: str) -> go.Figure:
    """Add concise conversion/taper context directly onto a Plotly projection chart."""
    strategy = "Legacy fixed" if roth_scenario is None else str(roth_scenario.get("strategy", "No conversions"))
    retirement_age = int(float(assumptions.get("retirement_age", 58)))
    ss_age = int(float(assumptions.get("social_security_age", 67)))
    start_age, end_age, show_ss_taper = effective_roth_conversion_window(assumptions, roth_scenario)

    if strategy != "No conversions" and end_age >= start_age:
        if show_ss_taper and start_age <= ss_age < end_age:
            fig.add_vrect(
                x0=start_age,
                x1=ss_age,
                line_width=0,
                fillcolor="rgba(255, 255, 255, 0.09)",
                annotation_text="Peak conversion window",
                annotation_position="top left",
            )
            fig.add_vrect(
                x0=ss_age,
                x1=end_age,
                line_width=0,
                fillcolor="rgba(255, 255, 255, 0.045)",
                annotation_text="Reduced/tapered conversions",
                annotation_position="top left",
            )
        else:
            fig.add_vrect(
                x0=start_age,
                x1=end_age,
                line_width=0,
                fillcolor="rgba(255, 255, 255, 0.08)",
                annotation_text="Roth conversion window",
                annotation_position="top left",
            )

    fig.add_vline(x=retirement_age, line_dash="dash", annotation_text="Retire", annotation_position="top left")
    fig.add_vline(x=ss_age, line_dash="dot", annotation_text="SS", annotation_position="top right")
    fig.add_annotation(
        text=roth_window_summary_text(assumptions, roth_scenario, plan_name),
        xref="paper",
        yref="paper",
        x=0.01,
        y=0.98,
        showarrow=False,
        align="left",
        borderpad=8,
        bgcolor="rgba(20, 40, 60, 0.88)",
        bordercolor="rgba(255, 255, 255, 0.25)",
        font={"size": 11},
    )
    return fig


def render_annual_roth_conversion_chart(projection: pd.DataFrame, assumptions: Dict[str, str], roth_scenario: dict | None, plan_name: str) -> None:
    """Show annual conversion amounts so post-SS tapering is visible."""
    if "Roth Conversion" not in projection.columns or float(projection["Roth Conversion"].sum()) <= 0:
        return

    st.subheader("Annual Roth conversion amounts")
    st.caption(
        "This shows whether conversions stop or taper. For bracket-fill plans, Social Security, RMDs, taxable brokerage gains, and conversion-driven Social Security taxation reduce available bracket room after they begin."
    )

    strategy = str(roth_scenario.get("strategy", "")) if roth_scenario is not None else ""
    rmd_start_age = int(float(assumptions.get("rmd_start_age", 73)))
    if strategy == "Hybrid ACA":
        st.info(
            f"Hybrid ACA strategy: ACA years preserve subsidies by staying under the MAGI target. "
            f"After Medicare, conversions continue. Ages 70–{rmd_start_age - 1} intentionally allow larger 22% bracket cleanup conversions "
            f"to reduce the future tax-deferred balance and soften the RMD spike. In RMD years, the strategy tapers back toward 12% bracket fills."
        )
    elif strategy == "Optimize ACA":
        st.info(
            "Optimize ACA strategy: ACA years prioritize staying under the MAGI target. "
            "After ACA/Medicare, the model continues smaller conservative conversions instead of hard-stopping, so you can still reduce future RMD pressure."
        )
    elif strategy == "Fill bracket":
        st.info(
            "Fill-bracket strategy: larger conversions are intentional when bracket room is available. "
            "This may raise taxes now, but it can reduce future RMDs, Social Security taxation, IRMAA risk, and later-life tax-deferred balances."
        )

    fig = px.bar(
        projection,
        x="Age",
        y="Roth Conversion",
        title=f"Annual Roth conversions: {plan_name}",
    )
    add_roth_window_annotation(fig, assumptions, roth_scenario, plan_name)
    fig.update_layout(yaxis_tickprefix="$", yaxis_tickformat=",.0f", xaxis_title="Age", yaxis_title="Roth conversion")
    st.plotly_chart(fig, use_container_width=True)


def page_dashboard() -> None:
    st.title("Retirement Dashboard")

    assumptions = get_assumptions()
    latest = read_latest_balances().fillna({"balance": 0, "target_balance": 0})
    projection_years = int(float(assumptions.get("years_to_project", 20)))

    total_assets = latest["balance"].sum()
    bridge_target = float(assumptions.get("bridge_target", 200000))
    checking_target = float(assumptions.get("checking_buffer_target", 45000))
    annual_spend = float(assumptions.get("annual_spend_retirement", 75000))
    current_age = int(float(assumptions.get("current_age", 52)))
    effective_retirement_age = int(float(assumptions.get("retirement_age", 58)))
    if assumptions.get("apply_layoff_pause_scenario", "0") == "1" and assumptions.get("extend_retirement_for_layoff", "0") == "1":
        effective_retirement_age += round(int(float(assumptions.get("layoff_pause_months", 0))) / 12)

    cash_total = latest.loc[latest["tax_bucket"] == "Cash", "balance"].sum()
    taxable_total = latest.loc[latest["tax_bucket"] == "Taxable", "balance"].sum()
    tax_deferred_total = latest.loc[latest["tax_bucket"] == "Tax-deferred", "balance"].sum()
    roth_total = latest.loc[latest["tax_bucket"] == "Roth", "balance"].sum()
    cash_cap_value = bridge_target + checking_target
    current_cash_runway = cash_total / annual_spend if annual_spend else 0

    # Use the saved dashboard settings first so the snapshot appears directly under the title.
    roth_scenario, saved_roth_name, active_roth_scenarios = get_dashboard_roth_scenario(assumptions)
    selected_roth_name = saved_roth_name
    show_today_dollars = assumptions.get("show_dashboard_today_dollars", "0") == "1"
    show_purchase_impacts = assumptions.get("show_purchase_impact_on_dashboard_chart", "1") == "1"
    dashboard_purchase_funding_override = assumptions.get("dashboard_purchase_funding_override", "Auto practical candidate")
    dashboard_purchase_age_override_enabled = assumptions.get("dashboard_purchase_age_override_enabled", "0") == "1"
    dashboard_purchase_age_override_raw = int(float(assumptions.get("dashboard_purchase_age_override", 0) or 0))

    # Dashboard purchase toggle controls whether planned purchases actually affect this chart and snapshot.
    # The Include checkbox on the Purchase Planner marks purchases as eligible; this Dashboard toggle
    # turns their impact on/off for visual comparison. The funding/age overrides let you test without
    # changing the saved Purchase Planner row.
    active_dashboard_purchases_base = read_planned_purchases(active_only=True) if show_purchase_impacts else pd.DataFrame()
    default_purchase_age = (
        int(active_dashboard_purchases_base.iloc[0]["purchase_age"])
        if not active_dashboard_purchases_base.empty
        else int(float(assumptions.get("retirement_age", 57)))
    )
    dashboard_purchase_age_override = dashboard_purchase_age_override_raw if dashboard_purchase_age_override_raw > 0 else default_purchase_age
    effective_dashboard_purchase_age = dashboard_purchase_age_override if dashboard_purchase_age_override_enabled else None
    active_dashboard_purchases, effective_dashboard_purchase_funding = (
        dashboard_purchase_rows_with_override(
            active_dashboard_purchases_base,
            dashboard_purchase_funding_override,
            assumptions=assumptions,
            roth_scenario=roth_scenario,
            years=projection_years,
            age_override=effective_dashboard_purchase_age,
        )
        if show_purchase_impacts
        else (pd.DataFrame(), "")
    )

    projection = build_projection(
        projection_years,
        assumptions,
        roth_scenario=roth_scenario,
        purchases_override=active_dashboard_purchases if show_purchase_impacts else None,
        ignore_purchases=not show_purchase_impacts,
    )

    years_to_retirement = max(effective_retirement_age - current_age, 0)
    retirement_projection_row = (
        build_projection(
            years_to_retirement,
            assumptions,
            roth_scenario=roth_scenario,
            purchases_override=active_dashboard_purchases if show_purchase_impacts else None,
            ignore_purchases=not show_purchase_impacts,
        ).iloc[-1]
        if years_to_retirement > 0
        else projection.iloc[0]
    )
    projected_at_retirement = float(retirement_projection_row["Total"]) if years_to_retirement > 0 else float(total_assets)
    projected_cash_at_retirement = float(retirement_projection_row.get("Cash", 0.0)) if years_to_retirement > 0 else float(cash_total)
    projected_first_year_spend = first_retirement_year_spending(assumptions)
    implied_withdrawal_rate_at_retirement = (projected_first_year_spend / projected_at_retirement) if projected_at_retirement else 0.0
    projected_cash_runway = projected_cash_at_retirement / projected_first_year_spend if projected_first_year_spend else 0

    st.subheader("Dashboard snapshot")
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Current tracked assets", format_money(total_assets), help="Current latest balance total from the Balances page. This is not projected.")
    projected_at_retirement_today = value_in_today_dollars(projected_at_retirement, effective_retirement_age, assumptions)
    if show_today_dollars:
        k2.metric(
            f"Projected at age {effective_retirement_age} (today's $)",
            format_money(projected_at_retirement_today),
            help="Inflation-adjusted purchasing power of the projected portfolio at retirement, using the selected Dashboard Roth plan.",
        )
    else:
        k2.metric(
            f"Projected at age {effective_retirement_age}",
            format_money(projected_at_retirement),
            help="Projected nominal portfolio value at the target retirement age using the selected Dashboard Roth plan.",
        )
    k3.metric(
        "1st-year spend @ retirement",
        format_money(projected_first_year_spend),
        help=(
            "The spending target in Assumptions is entered in today's dollars. "
            "This inflates that target to the first retirement year, then increases it with inflation in later projection years. "
            f"Today's target is {format_money(annual_spend)}. At projected retirement assets, the inflated first-year spend implies about {implied_withdrawal_rate_at_retirement:.2%}."
        ),
    )
    k4.metric("Current cash runway", f"{current_cash_runway:.1f} years", help="Current Cash bucket ÷ annual retirement spending target. This is a current snapshot, not the future cash target.")
    k5.metric("Projected cash runway", f"{projected_cash_runway:.1f} years", help="Projected Cash bucket at retirement ÷ annual retirement spending target, using the selected Dashboard Roth plan.")
    k6.metric("Cash cap target", f"{cash_total / cash_cap_value:.0%}" if cash_cap_value else "—", help="Current cash / (bridge target + checking buffer target).")
    st.caption(
        f"Dashboard Roth plan: **{selected_roth_name}**. Current metrics use latest balances; projected metrics use the selected Roth plan. "
        f"Spending target: {format_money(annual_spend)} in today's dollars, projected to {format_money(projected_first_year_spend)} in the first retirement year at age {effective_retirement_age}."
    )

    st.divider()

    # Dashboard controls live beside the chart they affect.
    st.subheader("Projection controls")
    ctrl1, ctrl2, ctrl3, ctrl4, ctrl5 = st.columns([2.1, 1.0, 1.0, 1.8, 1.8])

    with ctrl1:
        if not active_roth_scenarios.empty:
            scenario_names = active_roth_scenarios["name"].tolist()
            default_idx = scenario_names.index(saved_roth_name) if saved_roth_name in scenario_names else 0
            new_roth_name = st.selectbox(
                "Roth plan for dashboard projection",
                scenario_names,
                index=default_idx,
                help=(
                    "This choice drives the Dashboard projection, projected cash runway, and projection detail table. "
                    "Use the Roth Conversions page to compare all active scenarios side by side."
                ),
            )
            if new_roth_name != saved_roth_name:
                set_assumption("dashboard_roth_scenario_name", new_roth_name)
                st.rerun()
        else:
            st.info("Add active Roth scenarios on the Roth Conversions page to select a plan here.")

    with ctrl2:
        new_show_today_dollars = st.checkbox(
            "Today's dollars",
            value=show_today_dollars,
            help=(
                "Inflation-adjust projected balances and spending back into today's purchasing power. "
                "This answers: what will a future balance feel like in today's dollars?"
            ),
        )
        if new_show_today_dollars != show_today_dollars:
            set_assumption("show_dashboard_today_dollars", "1" if new_show_today_dollars else "0")
            st.rerun()

    with ctrl3:
        new_show_purchase_impacts = st.checkbox(
            "Show purchases",
            value=show_purchase_impacts,
            help=(
                "When ON, active purchases from the Purchase Planner are included in the Dashboard projection, chart, and snapshot. "
                "When OFF, the Dashboard ignores purchases so you can compare the base plan."
            ),
        )
        if new_show_purchase_impacts != show_purchase_impacts:
            set_assumption("show_purchase_impact_on_dashboard_chart", "1" if new_show_purchase_impacts else "0")
            st.rerun()

    with ctrl4:
        purchase_override_options = ["Auto practical candidate", "Saved purchase strategy"] + purchase_funding_candidate_labels()
        override_idx = (
            purchase_override_options.index(dashboard_purchase_funding_override)
            if dashboard_purchase_funding_override in purchase_override_options
            else 0
        )
        new_purchase_override = st.selectbox(
            "Dashboard purchase funding",
            purchase_override_options,
            index=override_idx,
            disabled=not show_purchase_impacts,
            help=(
                "Dashboard-only funding control. Auto practical candidate is the default and updates when purchase age changes: "
                "ACA years prioritize ACA-safe funding; after ACA it can prefer tax-deferred funding to reduce RMD pressure."
            ),
        )
        if new_purchase_override != dashboard_purchase_funding_override:
            set_assumption("dashboard_purchase_funding_override", new_purchase_override)
            st.rerun()

    with ctrl5:
        age_override_enabled = st.checkbox(
            "Override purchase age",
            value=dashboard_purchase_age_override_enabled,
            disabled=not show_purchase_impacts or active_dashboard_purchases_base.empty,
            help="Dashboard-only age override. This lets you move the planned purchase on the chart without editing the saved Purchase Planner row.",
        )
        if age_override_enabled != dashboard_purchase_age_override_enabled:
            set_assumption("dashboard_purchase_age_override_enabled", "1" if age_override_enabled else "0")
            st.rerun()

        if not active_dashboard_purchases_base.empty:
            new_purchase_age_override = st.slider(
                "Purchase age",
                min_value=int(float(assumptions.get("current_age", 52))),
                max_value=90,
                value=int(dashboard_purchase_age_override),
                step=1,
                disabled=not show_purchase_impacts or not age_override_enabled,
                help="Move the active purchase earlier/later on the Dashboard projection.",
            )
            if int(new_purchase_age_override) != int(dashboard_purchase_age_override):
                set_assumption("dashboard_purchase_age_override", str(int(new_purchase_age_override)))
                # Changing the age should default back to the age-aware practical candidate.
                # Users can still choose an explicit funding candidate after the age is set.
                set_assumption("dashboard_purchase_funding_override", "Auto practical candidate")
                st.rerun()

    render_roth_window_callout(assumptions, roth_scenario, selected_roth_name)

    if show_today_dollars:
        sample_age = 75
        sample_nominal = 4_000_000
        sample_today = value_in_today_dollars(sample_nominal, sample_age, assumptions)
        st.info(
            f"Purchasing-power view is ON. At {float(assumptions.get('inflation_rate', 0.025)):.1%} inflation, "
            f"$4M at age {sample_age} feels like about {format_money(sample_today)} in today's dollars."
        )

    if not active_dashboard_purchases.empty:
        included_summary = ", ".join(
            f"{str(r['name'])} at age {int(r['purchase_age'])} ({format_money(float(r['amount']))}, funded by {purchase_strategy_display_name(str(r['funding_strategy']))})"
            for _, r in active_dashboard_purchases.iterrows()
        )
        if dashboard_purchase_funding_override == "Saved purchase strategy" and not dashboard_purchase_age_override_enabled:
            st.caption(f"Active planned purchases included in Dashboard projection: {included_summary}.")
        else:
            extra_bits = []
            if dashboard_purchase_funding_override == "Auto practical candidate":
                extra_bits.append(f"auto practical funding: {effective_dashboard_purchase_funding}")
            elif dashboard_purchase_funding_override != "Saved purchase strategy":
                extra_bits.append(f"funding override: {effective_dashboard_purchase_funding}")
            if dashboard_purchase_age_override_enabled:
                extra_bits.append(f"age override: {dashboard_purchase_age_override}")
            suffix = "; ".join(extra_bits)
            st.caption(f"Active planned purchases included in Dashboard projection ({suffix}): {included_summary}.")

        # Show the actual bucket withdrawals used in the current Dashboard projection.
        # This makes it clear whether the chart is using brokerage, traditional, Roth, or cash.
        purchase_ages = set(active_dashboard_purchases["purchase_age"].astype(int).tolist())
        purchase_rows = projection[projection["Age"].astype(int).isin(purchase_ages)].copy()
        if not purchase_rows.empty:
            breakdown_cols = [
                "Age",
                "Purchase Names",
                "Purchase Funding Strategy",
                "Purchase Amount",
                "Purchase Cash Withdrawal",
                "Purchase Taxable Withdrawal",
                "Purchase Tax-deferred Withdrawal",
                "Purchase Roth Withdrawal",
                "Purchase Estimated Tax",
                "ACA MAGI After Conversion",
                "ACA Status",
            ]
            breakdown_cols = [c for c in breakdown_cols if c in purchase_rows.columns]
            breakdown = purchase_rows[breakdown_cols].copy()
            for col in [
                "Purchase Amount",
                "Purchase Cash Withdrawal",
                "Purchase Taxable Withdrawal",
                "Purchase Tax-deferred Withdrawal",
                "Purchase Roth Withdrawal",
                "Purchase Estimated Tax",
                "ACA MAGI After Conversion",
            ]:
                if col in breakdown.columns:
                    breakdown[col] = breakdown[col].map(lambda x: format_money(float(x)))
            st.caption("Dashboard purchase funding breakdown for the current chart:")
            st.dataframe(
                breakdown,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Purchase Cash Withdrawal": st.column_config.TextColumn("Cash", help="Amount of purchase funded from Cash."),
                    "Purchase Taxable Withdrawal": st.column_config.TextColumn("Taxable", help="Amount of purchase funded from taxable brokerage."),
                    "Purchase Tax-deferred Withdrawal": st.column_config.TextColumn("Tax-deferred", help="Amount of purchase funded from traditional/401k/IRA."),
                    "Purchase Roth Withdrawal": st.column_config.TextColumn("Roth", help="Amount of purchase funded from Roth."),
                    "ACA MAGI After Conversion": st.column_config.TextColumn("ACA MAGI", help="Modeled ACA MAGI in the purchase year."),
                },
            )

    if assumptions.get("apply_market_downturn_scenario", "0") == "1" or assumptions.get("apply_layoff_pause_scenario", "0") == "1":
        st.warning("Dashboard includes active scenario assumptions from the Scenarios tab.")

    st.subheader("Projection with selected Roth plan, withdrawals, and planned purchases")
    chart_projection = projection.copy()
    y_axis_title = "Projected balance"
    chart_value_suffix = ""
    if show_today_dollars:
        for col in ["Cash", "Taxable", "Tax-deferred", "Roth", "Total"]:
            if col in chart_projection.columns:
                chart_projection[col] = chart_projection.apply(
                    lambda r, c=col: value_in_today_dollars(float(r[c]), float(r["Age"]), assumptions), axis=1
                )
        y_axis_title = "Projected balance (today's dollars)"
        chart_value_suffix = " in today's dollars"

    fig = go.Figure()
    for col in ["Cash", "Taxable", "Tax-deferred", "Roth"]:
        if col in chart_projection.columns and chart_projection[col].sum() > 0:
            fig.add_trace(go.Scatter(
                x=chart_projection["Age"],
                y=chart_projection[col],
                mode="lines",
                stackgroup="one",
                name=col,
                hovertemplate=f"Age=%{{x}}<br>{col}{chart_value_suffix}=%{{y:$,.0f}}<extra></extra>",
            ))

    # Add an invisible total trace so hover shows total portfolio value at each age.
    # This is especially useful with stacked bucket charts, where the default hover
    # only reports the bucket under the cursor.
    if "Total" in chart_projection.columns:
        fig.add_trace(go.Scatter(
            x=chart_projection["Age"],
            y=chart_projection["Total"],
            mode="lines",
            name="Total portfolio",
            line=dict(color="rgba(255,255,255,0)", width=6),
            hovertemplate=f"Age=%{{x}}<br>Total portfolio{chart_value_suffix}=%{{y:$,.0f}}<extra></extra>",
            showlegend=False,
        ))

    if show_purchase_impacts and not active_dashboard_purchases.empty:
        no_purchase_projection = build_projection(
            projection_years,
            assumptions,
            roth_scenario=roth_scenario,
            ignore_purchases=True,
        )
        if show_today_dollars:
            no_purchase_projection = no_purchase_projection.copy()
            no_purchase_projection["Total"] = no_purchase_projection.apply(
                lambda r: value_in_today_dollars(float(r["Total"]), float(r["Age"]), assumptions), axis=1
            )
        fig.add_trace(go.Scatter(
            x=no_purchase_projection["Age"],
            y=no_purchase_projection["Total"],
            mode="lines",
            name="Total without purchases",
            line=dict(dash="dash"),
            hovertemplate=f"Age=%{{x}}<br>Total without purchases{chart_value_suffix}=%{{y:$,.0f}}<extra></extra>",
        ))

        purchase_points = chart_projection[projection["Purchase Amount"].fillna(0).astype(float) > 0].copy()
        if not purchase_points.empty:
            purchase_points["Purchase Label"] = purchase_points.apply(
                lambda r: f"{r.get('Purchase Names', 'Purchase')}: {format_money(float(r.get('Purchase Amount', 0.0)))}",
                axis=1,
            )
            fig.add_trace(go.Scatter(
                x=purchase_points["Age"],
                y=purchase_points["Total"],
                mode="markers+text",
                name="Planned purchase",
                marker=dict(symbol="diamond", size=12),
                text=purchase_points["Purchase Label"],
                textposition="top center",
                hovertemplate=f"Age=%{{x}}<br>%{{text}}<br>Total after purchase{chart_value_suffix}=%{{y:$,.0f}}<extra></extra>",
            ))

        for _, purchase in active_dashboard_purchases.iterrows():
            purchase_age = int(purchase["purchase_age"])
            if current_age <= purchase_age <= current_age + projection_years:
                fig.add_vline(
                    x=purchase_age,
                    line_dash="dot",
                    annotation_text=f"Purchase: {str(purchase['name'])}",
                    annotation_position="top right",
                )

    fig.add_vline(x=effective_retirement_age, line_dash="dash", annotation_text="Retirement", annotation_position="top left")
    if assumptions.get("apply_market_downturn_scenario", "0") == "1":
        fig.add_vline(x=int(float(assumptions.get("market_downturn_age", effective_retirement_age))), line_dash="dot", annotation_text="Downturn", annotation_position="top right")
    add_roth_window_annotation(fig, assumptions, roth_scenario, selected_roth_name)
    fig.update_layout(
        yaxis_tickprefix="$",
        yaxis_tickformat=",.0f",
        xaxis_title="Age",
        yaxis_title=y_axis_title,
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)

    if show_today_dollars:
        st.caption(
            "Purchasing-power view: projected future balances are discounted back to today's dollars using the inflation assumption. "
            "This is useful for judging what future balances will feel like, not for tracking nominal account statements."
        )

    if show_purchase_impacts and not active_dashboard_purchases.empty:
        st.caption(
            "Purchases are ON for this Dashboard view: active planned purchases are included in the chart and snapshot. "
            "The dashed line shows the same selected Roth plan without purchases, so the gap visualizes the long-term purchase impact."
        )
    elif not show_purchase_impacts:
        st.caption(
            "Purchases are OFF for this Dashboard view: active planned purchases are ignored in this chart and snapshot. "
            "Turn Show purchases on to see their impact."
        )

    with st.expander("Current bucket view", expanded=False):
        st.caption("This uses latest entered balances only. It is not a projected retirement-age allocation.")
        bucket_df = pd.DataFrame({
            "Bucket": ["Cash", "Taxable", "Tax-deferred", "Roth"],
            "Balance": [cash_total, taxable_total, tax_deferred_total, roth_total],
        })
        bucket_df = bucket_df[bucket_df["Balance"] > 0]
        if not bucket_df.empty:
            c1, c2 = st.columns([1, 2])
            with c1:
                bucket_display = bucket_df.copy()
                bucket_display["Balance"] = bucket_display["Balance"].map(lambda x: format_money(float(x)))
                st.dataframe(bucket_display, hide_index=True, use_container_width=True)
            with c2:
                fig_bucket = px.pie(bucket_df, names="Bucket", values="Balance", hole=0.45)
                fig_bucket.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), showlegend=True)
                st.plotly_chart(fig_bucket, use_container_width=True)
        else:
            st.info("Enter balances to see bucket allocation.")

    with st.expander("Projection details"):
        st.caption("Balance columns include green ↑ / red ↓ arrows versus the prior year where applicable.")
        detail = projection_with_after_tax_estimate(projection, assumptions)
        if "Year" in detail.columns:
            detail["Year"] = detail["Year"].astype(int)
        if show_today_dollars:
            detail = add_today_dollar_columns(
                detail,
                assumptions,
                ["Total", "Cash", "Taxable", "Tax-deferred", "Roth", "Gross Withdrawal Need", "Social Security"],
            )

        numeric_detail = detail.copy()
        arrow_cols = [
            "Total", "Cash", "Taxable", "Tax-deferred", "Roth",
            "After-tax Estimate",
            "Total - Today's $", "Cash - Today's $", "Taxable - Today's $",
            "Tax-deferred - Today's $", "Roth - Today's $",
        ]
        # Format all money-like fields consistently before rendering.
        # Keep percentages/status labels as text, and keep Year/Age as integers.
        money_cols = [
            "Total", "Cash", "Taxable", "Tax-deferred", "Roth",
            "After-tax Estimate", "After-tax Tax Drag",
            "Gross Withdrawal Need", "Social Security", "Net Portfolio Withdrawal",
            "Estimated RMD", "Roth Conversion", "Estimated Roth Tax",
            "Estimated Total Annual Tax", "Income Tax", "Capital Gains Tax",
            "Taxable Brokerage Withdrawal", "Taxable Gain Realized",
            "ACA MAGI Before Conversion", "ACA MAGI After Conversion",
            "ACA MAGI Limit", "ACA Headroom After Conversion",
            "ACA Annual Premium Cost", "ACA Annual OOP Estimate",
            "ACA Annual Healthcare Cost", "ACA Estimated Subsidy",
            "Planned Cash Withdrawal", "Planned Taxable Withdrawal",
            "Planned Tax-deferred Withdrawal", "Planned Roth Withdrawal",
            "Total Planned Portfolio Withdrawal", "Withdrawal Planning Shortfall",
            "Purchase Amount", "Purchase Cash Withdrawal", "Purchase Taxable Withdrawal",
            "Purchase Tax-deferred Withdrawal", "Purchase Roth Withdrawal",
            "Purchase Taxable Gain", "Purchase Capital Gains Tax",
            "Purchase Ordinary Income Tax", "Purchase Estimated Tax",
            "Purchase Shortfall",
            "Total - Today's $", "Cash - Today's $", "Taxable - Today's $",
            "Tax-deferred - Today's $", "Roth - Today's $",
            "Gross Withdrawal Need - Today's $", "Social Security - Today's $",
        ]
        # Also catch any new money-like columns that were added after this list was written.
        money_name_patterns = (
            "Tax", "Taxable", "MAGI", "Headroom", "Limit", "Withdrawal", "Balance",
            "Amount", "RMD", "Conversion", "Income", "Need", "Social Security",
            "Cash", "Roth", "Total", "After-tax", "Purchase", "Shortfall"
        )
        for col in detail.columns:
            if col in {"Year", "Age"} or "Rate" in str(col) or "Bracket" in str(col) or "Status" in str(col) or "Strategy" in str(col) or "Retired" in str(col):
                continue
            if col in money_cols or any(pattern in str(col) for pattern in money_name_patterns):
                try:
                    if col in arrow_cols and col in numeric_detail.columns:
                        prior_values = numeric_detail[col].shift(1)
                        detail[col] = [
                            format_money_with_direction(cur, None if pd.isna(prev) else float(prev))
                            if pd.notnull(cur) and str(cur).strip() not in ["", "-", "—", "nan"]
                            else "—"
                            for cur, prev in zip(numeric_detail[col], prior_values)
                        ]
                    else:
                        detail[col] = detail[col].apply(
                            lambda x: format_money(float(x))
                            if pd.notnull(x) and str(x).strip() not in ["", "-", "—", "nan"]
                            else "—"
                        )
                except Exception:
                    pass

        if "Year" in detail.columns:
            detail["Year"] = detail["Year"].astype(int)
        if "Age" in detail.columns:
            detail["Age"] = detail["Age"].astype(int)
        if "ACA FPL Percent" in detail.columns:
            detail["ACA FPL Percent"] = detail["ACA FPL Percent"].apply(
                lambda x: f"{float(x):.0f}%" if pd.notnull(x) and str(x).strip() not in ["", "-", "—", "nan"] else "—"
            )

        st.dataframe(detail, use_container_width=True, hide_index=True)

    render_withdrawal_planner(projection, assumptions)

    st.subheader("Latest balances")
    st.caption("These are the latest manual balance entries by account. They are current values, not projected values.")
    display = latest[["name", "account_type", "tax_bucket", "balance", "as_of_date", "target_balance"]].copy()
    display["balance"] = display["balance"].map(lambda x: format_money(float(x)))
    display["target_balance"] = display["target_balance"].map(lambda x: format_money(float(x or 0)))
    st.dataframe(display, use_container_width=True, hide_index=True)

def page_balances() -> None:
    st.title("Balances")
    accounts = read_accounts()
    if accounts.empty:
        st.warning("Add an account first.")
        return

    account_names = accounts["name"].tolist()
    account_name_to_id = dict(zip(accounts["name"], accounts["id"]))

    st.subheader("Add quick balance snapshot")
    with st.form("add_balance"):
        account_id = account_select(accounts, "Account", "balance_account")
        balance = st.number_input("Balance", min_value=0.0, step=100.0, format="%.2f")
        as_of = st.date_input("As-of date", value=date.today())
        submitted = st.form_submit_button("Save balance")
        if submitted:
            add_balance(account_id, balance, as_of)
            st.success("Balance saved.")
            st.rerun()

    st.subheader("Edit balance history inline")
    hist = read_balance_history()
    if hist.empty:
        editor_df = pd.DataFrame({
            "ID": pd.Series(dtype="Int64"),
            "Account ID": pd.Series(dtype="Int64"),
            "Account": pd.Series(dtype="object"),
            "Balance": pd.Series(dtype="float"),
            "As-of Date": pd.Series(dtype="datetime64[ns]"),
        })
    else:
        editor_df = hist.rename(columns={"id": "ID", "name": "Account", "balance": "Balance", "as_of_date": "As-of Date"})
        editor_df = editor_df[["ID", "Account", "Balance", "As-of Date"]]

    edited = st.data_editor(
        editor_df,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "ID": st.column_config.NumberColumn("ID", disabled=True),
            "Account ID": st.column_config.NumberColumn("Account ID", disabled=True, help="Internal account id. Used to preserve the account link if the editor blanks the visible name."),
            "Account": st.column_config.SelectboxColumn("Account", options=account_names, required=True),
            "Balance": st.column_config.NumberColumn("Balance", min_value=0.0, step=100.0, format="$%.2f", required=True),
            "As-of Date": st.column_config.DateColumn("As-of Date", required=True),
        },
        key="balances_editor",
    )

    c1, c2 = st.columns([1, 4])
    with c1:
        if st.button("Save balance edits", type="primary"):
            save_balance_editor(edited, account_name_to_id)
            st.success("Balance history updated.")
            st.rerun()
    with c2:
        st.caption("Tip: Add new rows at the bottom. Delete rows with the row menu. Click save when done.")

    st.subheader("Balance history chart")
    hist = read_balance_history()
    if not hist.empty:
        fig = px.line(hist, x="as_of_date", y="balance", color="name", markers=True)
        fig.update_layout(yaxis_tickprefix="$", yaxis_tickformat=",.0f")
        st.plotly_chart(fig, use_container_width=True)


def page_accounts() -> None:
    st.title("Accounts")
    st.subheader("Add account")
    with st.form("add_account"):
        name = st.text_input("Account name")
        account_type = st.selectbox("Account type", ["Brokerage", "401k", "IRA", "Roth IRA", "Savings", "Checking", "Other"])
        tax_bucket = st.selectbox("Tax bucket", ["Taxable", "Tax-deferred", "Roth", "Cash"])
        owner = st.selectbox("Owner", ["Primary", "Partner", "Joint", "Other"])
        target = st.number_input("Target balance", min_value=0.0, step=1000.0, format="%.2f")
        notes = st.text_area("Notes")
        submitted = st.form_submit_button("Add account")
        if submitted and name:
            try:
                add_account(name, account_type, tax_bucket, owner, target, notes)
                st.success("Account added.")
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("An account with that name already exists.")

    st.subheader("Manage accounts")
    accounts = read_accounts(active_only=False)
    if accounts.empty:
        st.info("No accounts yet.")
    else:
        st.dataframe(accounts, use_container_width=True, hide_index=True)
        with st.expander("Edit account"):
            account_id = account_select(accounts, "Account to edit", "edit_account")
            row = accounts.loc[accounts["id"] == account_id].iloc[0]
            with st.form("edit_account_form"):
                name = st.text_input("Account name", value=row["name"])
                account_types = ["Brokerage", "401k", "IRA", "Roth IRA", "Savings", "Checking", "Other"]
                tax_buckets = ["Taxable", "Tax-deferred", "Roth", "Cash"]
                owners = ["Primary", "Partner", "Joint", "Other"]
                account_type = st.selectbox("Account type", account_types, index=account_types.index(row["account_type"]) if row["account_type"] in account_types else 0)
                tax_bucket = st.selectbox("Tax bucket", tax_buckets, index=tax_buckets.index(row["tax_bucket"]))
                owner = st.selectbox("Owner", owners, index=owners.index(row["owner"]) if row["owner"] in owners else 0)
                target = st.number_input("Target balance", min_value=0.0, step=1000.0, value=float(row["target_balance"] or 0), format="%.2f")
                notes = st.text_area("Notes", value=row["notes"] or "")
                is_active = st.checkbox("Active", value=bool(row["is_active"]))
                submitted = st.form_submit_button("Update account")
                if submitted:
                    update_account(account_id, name, account_type, tax_bucket, owner, target, notes, 1 if is_active else 0)
                    st.success("Account updated.")
                    st.rerun()


def page_contributions() -> None:
    st.title("Contributions")

    # Use all accounts for the inline editor so existing contribution rows do not
    # appear blank just because an account was marked inactive. The projection
    # can still decide what to include based on contribution Active flags and
    # routed cashflow settings.
    accounts_all = read_accounts(active_only=False)
    accounts_active = read_accounts(active_only=True)

    if accounts_all.empty:
        st.warning("Add an account first.")
        return

    if accounts_active.empty:
        st.warning("All accounts are currently inactive. You can still edit existing contribution rows below, but activate an account before adding new contributions.")

    if get_assumptions().get("use_routed_cashflow", "1") == "1":
        st.info("Routed cashflow is ON. Cash and taxable/brokerage contribution rows are ignored in the projection to avoid double-counting. 401k/Roth contribution rows are still included.")

    # Normalize account names for the Streamlit data editor. Trailing/hidden
    # whitespace in the DB can make SelectboxColumn treat a visible name as
    # invalid, which caused edited account names to disappear after refresh.
    accounts_all = accounts_all.copy()
    accounts_all["name"] = accounts_all["name"].astype(str).str.strip()
    accounts_active = accounts_active.copy()
    if not accounts_active.empty:
        accounts_active["name"] = accounts_active["name"].astype(str).str.strip()

    account_names_all = sorted([x for x in accounts_all["name"].dropna().tolist() if str(x).strip()])
    account_name_to_id = {
        str(row["name"]).strip(): int(row["id"])
        for _, row in accounts_all.iterrows()
        if str(row.get("name", "")).strip()
    }
    account_id_to_name = {
        int(row["id"]): str(row["name"]).strip()
        for _, row in accounts_all.iterrows()
        if str(row.get("name", "")).strip()
    }

    st.subheader("Add quick planned contribution")
    with st.form("add_contribution"):
        quick_accounts = accounts_active if not accounts_active.empty else accounts_all
        account_id = account_select(quick_accounts, "Account", "contribution_account")
        amount = st.number_input("Amount per contribution", min_value=0.0, step=100.0, format="%.2f")
        frequency = st.selectbox("Frequency", ["Monthly", "Semi-monthly", "Biweekly", "Annual", "One-time"])
        start = st.date_input("Start date", value=date.today())
        has_end = st.checkbox("Has end date")
        end = st.date_input("End date", value=date.today()) if has_end else None
        notes = st.text_area("Notes")
        submitted = st.form_submit_button("Save contribution")
        if submitted:
            add_contribution(account_id, amount, frequency, start, end, notes)
            st.success("Contribution saved.")
            st.rerun()

    st.subheader("Edit contributions inline")
    contribs_all = read_contributions(active_only=False)
    if contribs_all.empty:
        editor_df = pd.DataFrame({
            "ID": pd.Series(dtype="Int64"),
            "Account ID": pd.Series(dtype="Int64"),
            "Account": pd.Series(dtype="object"),
            "Amount": pd.Series(dtype="float"),
            "Frequency": pd.Series(dtype="object"),
            "Start Date": pd.Series(dtype="datetime64[ns]"),
            "End Date": pd.Series(dtype="datetime64[ns]"),
            "Notes": pd.Series(dtype="object"),
            "Active": pd.Series(dtype="bool"),
        })
    else:
        editor_df = contribs_all.rename(
            columns={
                "id": "ID",
                "account_id": "Account ID",
                "name": "Account",
                "amount": "Amount",
                "frequency": "Frequency",
                "start_date": "Start Date",
                "end_date": "End Date",
                "notes": "Notes",
                "is_active": "Active",
            }
        )
        editor_df["Active"] = editor_df["Active"].astype(bool)
        editor_df["Account ID"] = pd.to_numeric(editor_df["Account ID"], errors="coerce").astype("Int64")
        editor_df["Account"] = editor_df.apply(
            lambda r: account_id_to_name.get(int(r["Account ID"]), str(r.get("Account", "")).strip())
            if pd.notna(r.get("Account ID")) else str(r.get("Account", "")).strip(),
            axis=1,
        )
        editor_df = editor_df[["ID", "Account ID", "Account", "Amount", "Frequency", "Start Date", "End Date", "Notes", "Active"]]
        missing_rows = editor_df["Account"].astype(str).str.startswith("[Missing account #") | (editor_df["Account"].astype(str).str.strip() == "")
        if missing_rows.any():
            st.warning(
                "Some contribution rows point to a deleted or blank account. Use the Account dropdown to select the correct account, then save."
            )

    edited = st.data_editor(
        editor_df,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "ID": st.column_config.NumberColumn("ID", disabled=True),
            "Account": st.column_config.SelectboxColumn("Account", options=account_names_all, required=True, help="Uses all accounts, including inactive accounts. Account ID is also preserved as a fallback when saving."),
            "Amount": st.column_config.NumberColumn("Amount", min_value=0.0, step=100.0, format="$%.2f", required=True),
            "Frequency": st.column_config.SelectboxColumn("Frequency", options=["Monthly", "Semi-monthly", "Biweekly", "Annual", "One-time"], required=True),
            "Start Date": st.column_config.DateColumn("Start Date", required=True),
            "End Date": st.column_config.DateColumn("End Date"),
            "Notes": st.column_config.TextColumn("Notes", help="Free-form reminder of what this scenario is testing."),
            "Active": st.column_config.CheckboxColumn("Active", help="Only active scenarios are included in the comparison."),
        },
        key="contrib_editor",
    )

    c1, c2 = st.columns([1, 4])
    with c1:
        if st.button("Save contribution edits", type="primary"):
            try:
                save_contribution_editor(edited, account_name_to_id)
                st.success("Contributions updated.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    with c2:
        st.caption("Uncheck Active instead of deleting if you want to keep an old contribution in history.")

    active_contribs = read_contributions(active_only=True)
    if not active_contribs.empty:
        st.subheader("Annualized active contributions")
        annual = active_contribs.copy()
        annual["annualized"] = annual.apply(lambda r: annualized_contribution(r["amount"], r["frequency"]), axis=1)
        display = annual[["name", "tax_bucket", "amount", "frequency", "annualized", "notes"]]
        st.dataframe(display, use_container_width=True, hide_index=True)
        st.metric("Total annualized contribution rows", format_money(float(annual["annualized"].sum())))


def page_roth_conversions() -> None:
    st.title("Roth Conversion Planner")
    st.caption("Compare fixed annual conversions against bracket-fill strategies, including taxes paid from cash/brokerage. The key metric is Break-even Age: when cumulative tax savings versus no conversions pay back the upfront conversion taxes.")
    st.info(
        "Social Security is now treated as a conversion taper point, not a hard stop. The scenario End Age column controls the final conversion year; after SS begins, bracket-fill conversions naturally shrink because taxable Social Security is included in the bracket-room calculation."
    )

    with st.expander("How to read this page", expanded=False):
        st.markdown(
            """
            **Strategy** controls how much is converted each year. **Fixed annual amount** converts the editable Annual Amount every year, regardless of which bracket it reaches. **Fill bracket** converts only enough to reach the selected taxable bracket ceiling. **No conversions** is the baseline.

            **Other Taxable Income** is taxable income before Roth conversions. Use it for pensions, part-time work, taxable interest, dividends, realized gains, or taxable withdrawals you want counted when deciding how much bracket space is available.

            **Pay Tax From** controls where conversion-tax cash comes from. Paying from Cash/Taxable keeps more money going into Roth. **Withhold from conversion** reduces the amount that lands in Roth.

            **Break-even Age** is the first age where cumulative estimated tax savings have paid back the upfront Roth conversion taxes. It compares cumulative estimated taxes against the no-conversion baseline, including later estimated RMD/tax-deferred taxes. **Not within window** means the conversion taxes are not recovered before the selected comparison period ends. Bracket-fill conversions now account for Social Security, estimated RMDs, and taxable gains from brokerage withdrawals, so changing Social Security start age can reduce or stop later conversion room.
            """
        )

    assumptions = get_assumptions()
    with st.expander("Tax bracket assumptions", expanded=True):
        st.info("These are editable because brackets change over time and your filing/tax situation can differ. Defaults below are 2026 MFJ federal brackets. Bracket tops are taxable income after the standard deduction, not gross income.")
        with st.form("roth_tax_assumptions"):
            standard_deduction = st.number_input("Standard deduction", value=float(assumptions.get("standard_deduction", 32200)), step=100.0, help="Estimated deduction used before calculating taxable income. For bracket-fill conversions, this creates more conversion room above your other taxable income.")
            mfj_10 = st.number_input("Top of 10% taxable bracket", value=float(assumptions.get("mfj_10_bracket_top", 24800)), step=100.0, help="Top of the 10% married-filing-jointly taxable income bracket. Taxable income means after deductions.")
            mfj_12 = st.number_input("Top of 12% taxable bracket", value=float(assumptions.get("mfj_12_bracket_top", 100800)), step=100.0, help="Used by Fill 12% bracket scenarios. The app estimates the largest conversion that keeps taxable income at or below this amount.")
            mfj_22 = st.number_input("Top of 22% taxable bracket", value=float(assumptions.get("mfj_22_bracket_top", 211400)), step=100.0, help="Used by Fill 22% bracket scenarios. This is often the main Roth-conversion planning threshold.")
            mfj_24 = st.number_input("Top of 24% taxable bracket", value=float(assumptions.get("mfj_24_bracket_top", 403550)), step=100.0, help="Optional higher-bracket threshold for larger conversion scenarios.")
            mfj_32 = st.number_input("Top of 32% taxable bracket", value=float(assumptions.get("mfj_32_bracket_top", 512450)), step=100.0, help="Optional higher-bracket threshold for stress-testing larger conversions.")
            mfj_35 = st.number_input("Top of 35% taxable bracket", value=float(assumptions.get("mfj_35_bracket_top", 768700)), step=100.0, help="Top of the 35% bracket; income above this is modeled at 37%.")
            future_tax_rate = st.number_input("Estimated future tax rate on remaining tax-deferred balance", value=float(assumptions.get("estimate_future_tax_rate_without_conversions", 0.22)), step=0.005, format="%.3f", help="Used only for after-tax ending-value comparison. Break-even uses annual projected taxes instead.")
            rmd_start_age = st.number_input("RMD start age", value=int(float(assumptions.get("rmd_start_age", 73))), step=1, help="Age when required minimum distributions begin in this planning model. Current-law details depend on birth year; keep this editable.")
            taxable_cost_basis_ratio = st.number_input("Taxable brokerage cost-basis ratio", value=float(assumptions.get("taxable_cost_basis_ratio", 0.70)), min_value=0.0, max_value=1.0, step=0.05, format="%.2f", help="Estimated share of taxable-brokerage withdrawals that is cost basis, not capital gain. 0.70 means a $10k sale realizes about $3k of taxable gains.")
            capital_gains_rate = st.number_input("Capital gains tax rate", value=float(assumptions.get("capital_gains_rate", 0.15)), min_value=0.0, max_value=0.50, step=0.005, format="%.3f", help="Federal long-term capital gains rate assumption used on the gain portion of taxable brokerage withdrawals.")
            state_tax_rate = st.number_input("State tax rate on gains / ordinary income add-on", value=float(assumptions.get("state_tax_rate", 0.0)), min_value=0.0, max_value=0.20, step=0.005, format="%.3f", help="Optional rough state-tax add-on for capital gains. Leave at 0 if you want federal-only planning.")
            submitted = st.form_submit_button("Save tax assumptions")
            if submitted:
                updates = {
                    "standard_deduction": standard_deduction,
                    "mfj_10_bracket_top": mfj_10,
                    "mfj_12_bracket_top": mfj_12,
                    "mfj_22_bracket_top": mfj_22,
                    "mfj_24_bracket_top": mfj_24,
                    "mfj_32_bracket_top": mfj_32,
                    "mfj_35_bracket_top": mfj_35,
                    "estimate_future_tax_rate_without_conversions": future_tax_rate,
                    "rmd_start_age": rmd_start_age,
                    "taxable_cost_basis_ratio": taxable_cost_basis_ratio,
                    "capital_gains_rate": capital_gains_rate,
                    "state_tax_rate": state_tax_rate,
                }
                for k, v in updates.items():
                    set_assumption(k, str(v))
                st.success("Tax assumptions saved.")
                st.rerun()

    with st.expander("ACA optimization assumptions", expanded=False):
        st.info(
            "ACA optimization is usually relevant from retirement until Medicare. "
            "By default, ACA start age follows Retirement Age and ACA end age is the year before Medicare."
        )
        c1, c2, c3 = st.columns(3)
        with c1:
            aca_fpl = st.number_input(
                "100% FPL amount",
                value=float(assumptions.get("aca_fpl_100", 21150)),
                step=100.0,
                help="Federal Poverty Level amount for your ACA household size. Update annually."
            )
        with c2:
            aca_target_pct = st.number_input(
                "ACA target % FPL",
                value=float(assumptions.get("aca_target_fpl_percent", 400)),
                step=10.0,
                help="Example: 400 means target ACA MAGI near 400% of FPL. Lower targets may increase subsidies but reduce Roth conversion room."
            )
        with c3:
            aca_buffer = st.number_input(
                "ACA MAGI safety buffer",
                value=float(assumptions.get("aca_magi_safety_buffer", 1000)),
                step=100.0,
                help="Subtracts a cushion from the ACA MAGI limit so the model does not aim exactly at the cliff/target."
            )
        c4, c5, c6 = st.columns(3)
        with c4:
            medicare_age = st.number_input(
                "Medicare age",
                value=int(float(assumptions.get("aca_medicare_age", 65))),
                step=1,
                help="ACA optimization normally ends the year before this age."
            )
        with c5:
            st.metric("ACA start age", aca_start_age(assumptions), help="Defaults to Retirement Age.")
        with c6:
            st.metric("ACA end age", aca_end_age(assumptions), help="Defaults to the year before Medicare.")
        st.caption(
            f"Current ACA MAGI target: {format_money(aca_magi_limit_value := max(aca_fpl * (aca_target_pct / 100.0) - aca_buffer, 0.0))}."
        )
        if st.button("Save ACA assumptions"):
            set_assumption("aca_fpl_100", str(aca_fpl))
            set_assumption("aca_target_fpl_percent", str(aca_target_pct))
            set_assumption("aca_magi_safety_buffer", str(aca_buffer))
            set_assumption("aca_medicare_age", str(medicare_age))
            set_assumption("aca_start_age", "auto")
            set_assumption("aca_end_age", "auto")
            st.success("ACA assumptions saved.")
            st.rerun()

    st.subheader("How much should I convert each year?")
    st.caption(
        "Use this section as the yearly instruction table. For the ACA optimizer, "
        "the app calculates the Roth conversion amount that stays under the ACA MAGI target during ACA years."
    )

    active_conversion_scenarios = read_roth_scenarios(active_only=True)
    if not active_conversion_scenarios.empty:
        # Prefer the ACA optimizer scenario by default. Existing databases may have
        # several older scenarios, so do not default to the first row.
        names_for_plan = active_conversion_scenarios["name"].tolist()
        default_idx = 0
        for i, row_s in active_conversion_scenarios.iterrows():
            if str(row_s.get("strategy", "")).strip() == "Optimize ACA" or "ACA" in str(row_s.get("name", "")).upper():
                default_idx = names_for_plan.index(row_s["name"])
                break

        selected_conversion_plan = st.selectbox(
            "Conversion guidance scenario",
            names_for_plan,
            index=default_idx,
            key="aca_conversion_guidance_scenario",
            help="Choose 'Optimize ACA' to see the year-by-year Roth conversion amount that stays under the ACA MAGI target."
        )
        guidance_scenario = active_conversion_scenarios.loc[
            active_conversion_scenarios["name"] == selected_conversion_plan
        ].iloc[0].to_dict()

        if str(guidance_scenario.get("strategy", "")) != "Optimize ACA":
            st.info(
                "This selected scenario is not the ACA optimizer. It will still show year-by-year conversions, "
                "but it may not adjust conversions to stay below the ACA MAGI target. Select 'Optimize ACA' for ACA subsidy planning."
            )

        guidance_years = int(float(assumptions.get("years_to_project", 35)))
        guidance_projection = build_projection(guidance_years, assumptions, roth_scenario=guidance_scenario)

        current_age_value = int(float(assumptions.get("current_age", 50)))
        default_planning_age = max(current_age_value, aca_start_age(assumptions))
        if default_planning_age > int(guidance_projection["Age"].max()):
            default_planning_age = int(guidance_projection["Age"].iloc[0])

        available_ages = guidance_projection["Age"].astype(int).tolist()
        default_age_idx = available_ages.index(default_planning_age) if default_planning_age in available_ages else 0

        selected_planning_age = st.selectbox(
            "Planning age / year",
            available_ages,
            index=default_age_idx,
            key="aca_conversion_guidance_age",
            help="Pick the age/year you want the app to answer: 'How much should I convert this year?'"
        )

        row = guidance_projection[guidance_projection["Age"].astype(int) == int(selected_planning_age)].iloc[0]
        calendar_year = int(row.get("Year", date.today().year))
        projection_year = calendar_year - date.today().year

        m1, m2, m3, m4 = st.columns(4)
        m1.metric(
            f"Convert at age {int(selected_planning_age)}",
            format_money(float(row.get("Roth Conversion", 0.0))),
            help=f"Projection year {projection_year}, approximately calendar year {calendar_year}."
        )
        m2.metric(
            "Approx. calendar year",
            str(calendar_year),
            help="Calculated from the current calendar year plus the projection year."
        )
        m3.metric(
            "ACA MAGI after conversion",
            format_money(float(row.get("ACA MAGI After Conversion", 0.0))),
            help="Estimated ACA MAGI after the modeled Roth conversion."
        )
        m4.metric(
            "ACA headroom",
            format_money(float(row.get("ACA Headroom After Conversion", 0.0))),
            help="Remaining room below the ACA MAGI target after the modeled conversion."
        )

        if aca_start_age(assumptions) <= int(selected_planning_age) <= aca_end_age(assumptions):
            st.success(
                f"ACA optimization is active at age {int(selected_planning_age)}. "
                f"The suggested conversion is {format_money(float(row.get('Roth Conversion', 0.0)))}."
            )
        else:
            st.caption(
                f"Age {int(selected_planning_age)} is outside the ACA optimization window "
                f"({aca_start_age(assumptions)}–{aca_end_age(assumptions)})."
            )

        guidance_cols = [
            "Year", "Age", "Roth Conversion", "Estimated Roth Tax",
            "ACA MAGI Before Conversion", "ACA MAGI After Conversion",
            "ACA MAGI Limit", "ACA Headroom After Conversion", "ACA Status"
        ]
        guidance_display = guidance_projection[[c for c in guidance_cols if c in guidance_projection.columns]].copy()
        if "Year" in guidance_display.columns:
            guidance_display["Year"] = guidance_display["Year"].astype(int)

        for col in [c for c in guidance_display.columns if c not in {"Year", "Age", "Approx. Calendar Year", "ACA Status"}]:
            guidance_display[col] = guidance_display[col].apply(
                lambda x: format_money(float(x))
                if pd.notnull(x) and str(x).strip() not in ["", "-", "—", "nan"]
                else "—"
            )

        st.dataframe(
            guidance_display,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Year": st.column_config.NumberColumn("Calendar Year", help="Projected calendar year."),
                "Age": st.column_config.NumberColumn("Age", help="Your projected age in that row."),
                "Roth Conversion": st.column_config.TextColumn("Roth Conversion", help="The modeled amount to convert to Roth that year."),
                "ACA MAGI Before Conversion": st.column_config.TextColumn("ACA MAGI Before Conversion", help="Estimated ACA MAGI before the Roth conversion."),
                "ACA MAGI After Conversion": st.column_config.TextColumn("ACA MAGI After Conversion", help="Estimated ACA MAGI after the Roth conversion."),
                "ACA MAGI Limit": st.column_config.TextColumn("ACA MAGI Limit", help="Editable ACA MAGI target based on FPL target minus safety buffer."),
                "ACA Headroom After Conversion": st.column_config.TextColumn("ACA Headroom After Conversion", help="Remaining room under the ACA target after conversion."),
                "ACA Status": st.column_config.TextColumn("ACA Status", help="Whether the row is under/over the ACA MAGI target or outside ACA years."),
            },
        )

    st.caption("For Fixed annual amount scenarios, edit Annual Amount to test any yearly conversion amount. Target Bracket is intentionally N/A for fixed and no-conversion rows; bracket reached is reported later as an outcome metric.")
    scenarios = read_roth_scenarios(active_only=False)
    if scenarios.empty:
        editor_df = pd.DataFrame({
            "ID": pd.Series(dtype="Int64"),
            "Name": pd.Series(dtype="object"),
            "Start Age": pd.Series(dtype="int"),
            "End Age": pd.Series(dtype="int"),
            "Strategy": pd.Series(dtype="object"),
            "Annual Amount": pd.Series(dtype="float"),
            "Target Bracket": pd.Series(dtype="object"),
            "Pay Tax From": pd.Series(dtype="object"),
            "Other Taxable Income": pd.Series(dtype="float"),
            "Active": pd.Series(dtype="bool"),
            "Notes": pd.Series(dtype="object"),
        })
    else:
        editor_df = scenarios.rename(columns={
            "id": "ID",
            "name": "Name",
            "start_age": "Start Age",
            "end_age": "End Age",
            "strategy": "Strategy",
            "annual_amount": "Annual Amount",
            "target_bracket": "Target Bracket",
            "pay_tax_from": "Pay Tax From",
            "other_taxable_income": "Other Taxable Income",
            "is_active": "Active",
            "notes": "Notes",
        })
        editor_df["Active"] = editor_df["Active"].astype(bool)
        editor_df.loc[editor_df["Strategy"] != "Fill bracket", "Target Bracket"] = "N/A"
        editor_df = editor_df[["ID", "Name", "Start Age", "End Age", "Strategy", "Annual Amount", "Target Bracket", "Pay Tax From", "Other Taxable Income", "Active", "Notes"]]

    edited = st.data_editor(
        editor_df,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "ID": st.column_config.NumberColumn("ID", disabled=True),
            "Name": st.column_config.TextColumn("Name", required=True, help="Scenario label shown in charts and comparisons."),
            "Start Age": st.column_config.NumberColumn("Start Age", min_value=0, max_value=120, step=1, required=True, help="First age when this conversion strategy is allowed to run."),
            "End Age": st.column_config.NumberColumn("End Age", min_value=0, max_value=120, step=1, required=True, help="Last age when this conversion strategy is allowed to run. Social Security no longer hard-stops conversions; instead, SS income reduces bracket-fill room after SS begins."),
            "Strategy": st.column_config.SelectboxColumn("Strategy", options=["No conversions", "Fixed annual amount", "Fill bracket", "Optimize ACA", "Hybrid ACA"], required=True, help="No conversions is the baseline. Fixed annual amount uses Annual Amount. Fill bracket targets a tax bracket. Optimize ACA adjusts the Roth conversion amount year by year to stay under the ACA MAGI target during ACA years."),
            "Annual Amount": st.column_config.NumberColumn("Annual Amount", min_value=0.0, step=5000.0, format="$%.2f", help="Configurable amount used only for Fixed annual amount scenarios. Change this from $80,000 to any annual conversion amount you want to test. The app will not convert more than the remaining tax-deferred balance."),
            "Target Bracket": st.column_config.SelectboxColumn("Target Bracket", options=["N/A", "10%", "12%", "22%", "24%", "32%", "35%"], help="Used only for Fill bracket scenarios. Fixed annual amount and No conversions rows should show N/A because they do not target a bracket."),
            "Pay Tax From": st.column_config.SelectboxColumn("Pay Tax From", options=["Cash then Taxable", "Taxable then Cash", "Cash only", "Taxable only", "Withhold from conversion"], help="Where conversion-tax cash is drawn from. Withhold from conversion means the tax comes out of the converted IRA dollars, so less lands in Roth."),
            "Other Taxable Income": st.column_config.NumberColumn("Other Taxable Income", min_value=0.0, step=1000.0, format="$%.2f", help="Annual taxable income before Roth conversions. Use this for pension, taxable withdrawals, part-time work, etc."),
            "Active": st.column_config.CheckboxColumn("Active", help="Only active scenarios are included in the comparison."),
            "Notes": st.column_config.TextColumn("Notes", help="Free-form reminder of what this scenario is testing."),
        },
        key="roth_scenario_editor",
    )

    if st.button("Save Roth scenarios", type="primary"):
        save_roth_scenario_editor(edited)
        st.success("Roth scenarios saved.")
        st.rerun()

    st.subheader("Scenario comparison")

    with st.expander("Lifetime comparison settings", expanded=False):
        st.caption(
            "Set the lifetime comparison horizon here. ACA premium assumptions now live on the ACA Planner tab. "
            "This table compares cumulative taxes, estimated healthcare costs, and after-tax value through the selected age."
        )
        c1, c2, c3 = st.columns(3)
        with c1:
            new_compare_age = st.number_input(
                "Compare through age",
                min_value=int(float(assumptions.get("current_age", 50))),
                max_value=120,
                value=comparison_through_age(assumptions),
                step=1,
                help="Cumulative tax/healthcare metrics are summed through this age."
            )
        with c2:
            new_aca_subsidized = st.number_input(
                "Estimated annual ACA premium if under target",
                min_value=0.0,
                value=float(assumptions.get("aca_subsidized_annual_premium", 6000)),
                step=500.0,
                help="Planning estimate for annual ACA premiums when MAGI stays under the target."
            )
        with c3:
            new_aca_full = st.number_input(
                "Estimated annual ACA premium if over target",
                min_value=0.0,
                value=float(assumptions.get("aca_full_annual_premium", 18000)),
                step=500.0,
                help="Planning estimate for annual ACA premiums when MAGI exceeds the target."
            )
        c4, c5 = st.columns(2)
        with c4:
            new_irmaa_threshold = st.number_input(
                "IRMAA MAGI threshold estimate",
                min_value=0.0,
                value=float(assumptions.get("irmaa_magi_threshold", 206000)),
                step=1000.0,
                help="Simplified MAGI threshold used to estimate Medicare IRMAA risk."
            )
        with c5:
            new_irmaa_surcharge = st.number_input(
                "Estimated annual IRMAA surcharge",
                min_value=0.0,
                value=float(assumptions.get("irmaa_annual_surcharge", 0)),
                step=100.0,
                help="Simplified annual surcharge added in Medicare years when MAGI exceeds the threshold."
            )
        if st.button("Save lifetime comparison settings"):
            set_assumption("lifetime_comparison_age", str(int(new_compare_age)))
            set_assumption("aca_subsidized_annual_premium", str(float(new_aca_subsidized)))
            set_assumption("aca_full_annual_premium", str(float(new_aca_full)))
            set_assumption("irmaa_magi_threshold", str(float(new_irmaa_threshold)))
            set_assumption("irmaa_annual_surcharge", str(float(new_irmaa_surcharge)))
            st.success("Lifetime comparison settings saved.")
            st.rerun()

    dashboard_scenario, dashboard_plan_name, _active_roth_scenarios = get_dashboard_roth_scenario(assumptions)
    render_roth_window_callout(assumptions, dashboard_scenario, dashboard_plan_name)
    current_age_for_compare = int(float(assumptions.get("current_age", 50)))
    compare_years_default = max(
        35,
        comparison_through_age(assumptions) - current_age_for_compare,
        int(float(assumptions.get("years_to_project", 20))),
    )
    years = st.slider(
        "Years to project in comparison",
        5,
        60,
        compare_years_default,
        help="Make this long enough to reach the comparison age. Longer windows are better for Roth, RMD, ACA, and IRMAA analysis.",
    )
    comparison = build_roth_scenario_comparison(years, assumptions)
    if comparison.empty:
        st.info("Add an active scenario to compare.")
        return

    baseline_after_tax = comparison.iloc[0]["After-tax Ending Estimate"]
    comparison["After-tax Gain vs Baseline"] = comparison["After-tax Ending Estimate"] - baseline_after_tax

    metric_cols = [
        "Scenario",
        "Strategy",
        "Target Bracket",
        "Max Conversion Bracket Reached",
        "Tax Source",
        "Break-even Age",
        "Years After Break-even",
        "Total Converted",
        "Conversion Tax Paid",
        "Cumulative Tax Savings at End",
        "Lifetime Estimated Tax",
        "Estimated ACA Premium Cost",
        "Estimated IRMAA Cost",
        "Total Tax + Healthcare Cost",
        "Cumulative RMDs",
        "Tax-deferred at Comparison Age",
        "Roth at Comparison Age",
        "After-tax Value at Comparison Age",
        "Ending Tax-deferred",
        "Ending Roth",
        "After-tax Ending Estimate",
        "After-tax Gain vs Baseline",
    ]
    display = comparison[metric_cols].copy()
    for col in [
        "Total Converted",
        "Conversion Tax Paid",
        "Ending Tax-deferred",
        "Ending Roth",
        "After-tax Ending Estimate",
        "After-tax Gain vs Baseline",
        "Cumulative Tax Savings at End",
        "Lifetime Estimated Tax",
        "Estimated ACA Premium Cost",
        "Estimated IRMAA Cost",
        "Total Tax + Healthcare Cost",
        "Cumulative RMDs",
        "Tax-deferred at Comparison Age",
        "Roth at Comparison Age",
        "After-tax Value at Comparison Age",
    ]:
        display[col] = display[col].map(lambda x: format_money(float(x)))

    comparison_column_config = {
        "Scenario": st.column_config.TextColumn("Scenario", help="The name of the Roth conversion scenario from the editor above."),
        "Strategy": st.column_config.TextColumn("Strategy", help="How the yearly conversion amount is chosen: no conversions, a fixed dollar amount, or enough to fill a selected tax bracket."),
        "Tax Source": st.column_config.TextColumn("Tax Source", help="Where the estimated tax bill from the Roth conversion is paid from. Cash/Taxable preserves more Roth money than withholding from the conversion."),
        "Break-even Age": st.column_config.TextColumn("Break-even Age", help="The first age where cumulative estimated tax savings versus no conversions have paid back the cumulative Roth conversion taxes. This is the main payback metric."),
        "Years After Break-even": st.column_config.TextColumn("Years After Break-even", help="How many years remain in the selected comparison window after the break-even age. This approximates how many years of net tax benefit you would see if you live to the ending age."),
        "Total Converted": st.column_config.TextColumn("Total Converted", help="Total pre-tax retirement dollars moved from Tax-deferred into Roth during the scenario window."),
        "Conversion Tax Paid": st.column_config.TextColumn("Conversion Tax Paid", help="Cumulative estimated federal tax generated by the Roth conversions. This is the upfront tax cost that future tax savings need to recover."),
        "Cumulative Tax Savings at End": st.column_config.TextColumn("Cumulative Tax Savings at End", help="Baseline cumulative estimated taxes minus this scenario's cumulative estimated taxes at the end of the comparison window. Positive means the scenario has produced net lifetime tax savings by then."),
        "Lifetime Estimated Tax": st.column_config.TextColumn("Lifetime Estimated Tax", help="Sum of estimated annual taxes through the configurable comparison age."),
        "Estimated ACA Premium Cost": st.column_config.TextColumn("Estimated ACA Premium Cost", help="Editable estimate of ACA premiums through the ACA window. Uses the subsidized estimate when MAGI is under target and full estimate when over target."),
        "Estimated IRMAA Cost": st.column_config.TextColumn("Estimated IRMAA Cost", help="Simplified Medicare IRMAA estimate after Medicare age when MAGI exceeds the editable threshold."),
        "Total Tax + Healthcare Cost": st.column_config.TextColumn("Total Tax + Healthcare Cost", help="Lifetime estimated tax plus estimated ACA premiums plus estimated IRMAA costs through the comparison age. Lower is better, all else equal."),
        "Cumulative RMDs": st.column_config.TextColumn("Cumulative RMDs", help="Estimated total forced RMDs through the comparison age. This shows future pre-tax pressure."),
        "Tax-deferred at Comparison Age": st.column_config.TextColumn("Tax-deferred at Comparison Age", help="Traditional/401k/IRA-style balance remaining at the configured comparison age."),
        "Roth at Comparison Age": st.column_config.TextColumn("Roth at Comparison Age", help="Roth balance at the configured comparison age."),
        "After-tax Value at Comparison Age": st.column_config.TextColumn("After-tax Value at Comparison Age", help="After-tax balance-sheet estimate at the configured comparison age."),
        "Ending Tax-deferred": st.column_config.TextColumn("Ending Tax-deferred", help="Projected ending balance still in traditional/401k/IRA-style tax-deferred accounts. Higher values imply more future taxable withdrawals/RMD exposure."),
        "Ending Roth": st.column_config.TextColumn("Ending Roth", help="Projected ending Roth balance. Roth dollars are treated as after-tax in this simplified model."),
        "After-tax Ending Estimate": st.column_config.TextColumn("After-tax Ending Estimate", help="Projected ending total minus the editable future-tax-rate estimate applied to remaining tax-deferred balances. This is a balance-sheet estimate, separate from the break-even tax-payback metric."),
        "After-tax Gain vs Baseline": st.column_config.TextColumn("After-tax Gain vs Baseline", help="Ending after-tax estimate minus the no-conversion scenario's ending after-tax estimate."),
    }
    st.dataframe(display, use_container_width=True, hide_index=True, column_config=comparison_column_config)
    if "Total Tax + Healthcare Cost" in comparison.columns:
        best_idx = comparison["Total Tax + Healthcare Cost"].astype(float).idxmin()
        best_row = comparison.loc[best_idx]
        st.success(
            f"Lowest estimated tax + healthcare cost through age {comparison_through_age(assumptions)}: "
            f"{best_row['Scenario']} at {format_money(float(best_row['Total Tax + Healthcare Cost']))}."
        )
    st.caption(
        "Break-even Age is based on cumulative estimated taxes. Total Tax + Healthcare Cost adds editable ACA premium and IRMAA estimates, "
        "so it is useful for comparing ACA subsidy preservation against more aggressive Roth conversions. "
        "All healthcare/tax numbers are planning estimates, not tax advice."
    )

    with st.expander("Column definitions", expanded=False):
        st.markdown(
            """
            - **Break-even Age:** first age where cumulative estimated tax savings have paid back the upfront Roth conversion taxes.
            - **Years After Break-even:** number of years from break-even to the end of the comparison window.
            - **Conversion Tax Paid:** estimated federal tax caused by conversions, paid from the selected source.
            - **Cumulative Tax Savings at End:** net cumulative tax difference versus no conversions by the final comparison age.
            - **After-tax Ending Estimate:** total ending balance minus estimated future tax drag on remaining tax-deferred money.
            - **Effective Conversion Tax Rate:** average federal tax rate on that year's conversion, calculated as estimated Roth tax divided by conversion amount.
            - **Marginal Bracket Reached:** highest ordinary bracket touched by the conversion dollars. This can be 22% even when the effective rate is only 17–19%.
            - **Conversion Headroom Remaining:** estimated room left after the conversion. For fill-bracket scenarios, "At target" means the app intentionally used the available room for the selected bracket.
            - **Important:** Effective tax rate can be higher than the statutory marginal bracket because Roth conversions can make more Social Security taxable. That is the Social Security tax feedback/torpedo effect.
            - **Max Conversion Bracket Reached:** highest ordinary federal bracket reached in any year that has a Roth conversion. This is an outcome metric, especially useful for fixed annual conversions.
            """
        )

    fig = px.bar(comparison, x="Scenario", y="After-tax Ending Estimate", title="Estimated after-tax ending wealth by Roth strategy")
    fig.update_layout(yaxis_tickprefix="$", yaxis_tickformat=",.0f")
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Detailed projection for one scenario")
    active_scenarios = read_roth_scenarios(active_only=True)
    if not active_scenarios.empty:
        names = active_scenarios["name"].tolist()
        selected = st.selectbox("Scenario", names)
        scenario = active_scenarios.loc[active_scenarios["name"] == selected].iloc[0].to_dict()

        active_detail_purchases_base = read_planned_purchases(active_only=True)
        saved_purchase_chart_default = assumptions.get("show_purchase_impact_on_dashboard_chart", "1") == "1"
        include_detail_purchases = True
        detail_purchase_funding_override = assumptions.get("dashboard_purchase_funding_override", "Auto practical candidate")
        detail_purchase_age_override_enabled = assumptions.get("dashboard_purchase_age_override_enabled", "0") == "1"
        detail_purchase_age_override_raw = int(float(assumptions.get("dashboard_purchase_age_override", 0) or 0))

        if not active_detail_purchases_base.empty:
            detail_ctrl1, detail_ctrl2, detail_ctrl3 = st.columns([1.4, 2.0, 2.0])
            with detail_ctrl1:
                include_detail_purchases = st.checkbox(
                    "Include active planned purchases in this detailed scenario chart and Roth conversion chart",
                    value=saved_purchase_chart_default,
                    help=(
                        "When checked, active purchases reduce the projected buckets and can change Roth conversions, "
                        "withdrawals, ACA MAGI, taxes, and future balances. When unchecked, this detailed scenario area "
                        "shows the same plan without those purchase impacts."
                    ),
                    key="detail_include_purchase_impacts",
                )

            with detail_ctrl2:
                detail_purchase_options = ["Auto practical candidate", "Saved purchase strategy"] + purchase_funding_candidate_labels()
                detail_idx = (
                    detail_purchase_options.index(detail_purchase_funding_override)
                    if detail_purchase_funding_override in detail_purchase_options
                    else 0
                )
                detail_purchase_funding_override = st.selectbox(
                    "Detailed chart purchase funding",
                    detail_purchase_options,
                    index=detail_idx,
                    disabled=not include_detail_purchases,
                    help=(
                        "Controls purchase funding inside this Roth scenario detail view. "
                        "Auto practical candidate recalculates by age and scenario; Saved purchase strategy follows the Purchase Planner row."
                    ),
                    key="detail_purchase_funding_override",
                )

            with detail_ctrl3:
                detail_age_override_enabled = st.checkbox(
                    "Override purchase age here",
                    value=detail_purchase_age_override_enabled,
                    disabled=not include_detail_purchases,
                    help="Scenario-detail-only control for moving the purchase age in this Roth chart/table.",
                    key="detail_purchase_age_override_enabled",
                )
                default_detail_purchase_age = (
                    int(active_detail_purchases_base.iloc[0]["purchase_age"])
                    if not active_detail_purchases_base.empty
                    else int(float(assumptions.get("retirement_age", 57)))
                )
                detail_purchase_age_override = detail_purchase_age_override_raw if detail_purchase_age_override_raw > 0 else default_detail_purchase_age
                detail_purchase_age_override = st.slider(
                    "Detailed purchase age",
                    min_value=int(float(assumptions.get("current_age", 52))),
                    max_value=90,
                    value=int(detail_purchase_age_override),
                    step=1,
                    disabled=not include_detail_purchases or not detail_age_override_enabled,
                    help="Move the active purchase earlier/later in this Roth scenario detail projection.",
                    key="detail_purchase_age_override",
                )
                if detail_age_override_enabled and detail_purchase_funding_override == "Saved purchase strategy":
                    st.caption("Tip: choose Auto practical candidate if you want funding to change as this age changes.")

            detail_age_override_value = int(detail_purchase_age_override) if detail_age_override_enabled else None
            active_detail_purchases, effective_detail_purchase_funding = (
                dashboard_purchase_rows_with_override(
                    active_detail_purchases_base,
                    detail_purchase_funding_override,
                    assumptions=assumptions,
                    roth_scenario=scenario,
                    years=years,
                    age_override=detail_age_override_value,
                )
                if include_detail_purchases
                else (pd.DataFrame(), "")
            )
        else:
            active_detail_purchases = pd.DataFrame()
            effective_detail_purchase_funding = ""
            st.caption("No active planned purchases are currently available to include in this detailed scenario view.")

        proj = projection_with_after_tax_estimate(
            build_projection(
                years,
                assumptions,
                roth_scenario=scenario,
                purchases_override=active_detail_purchases if include_detail_purchases else None,
                ignore_purchases=not include_detail_purchases,
            ),
            assumptions,
        )
        if not active_detail_purchases_base.empty:
            if include_detail_purchases:
                included_summary = ", ".join(
                    f"{str(r['name'])} at age {int(r['purchase_age'])} ({format_money(float(r['amount']))}, funded by {purchase_strategy_display_name(str(r['funding_strategy']))})"
                    for _, r in active_detail_purchases.iterrows()
                )
                st.caption(f"Active planned purchases are included here using: {included_summary}.")
                purchase_ages = set(active_detail_purchases["purchase_age"].astype(int).tolist())
                detail_purchase_rows = proj[proj["Age"].astype(int).isin(purchase_ages)].copy()
                if not detail_purchase_rows.empty:
                    breakdown_cols = [
                        "Age",
                        "Purchase Names",
                        "Purchase Funding Strategy",
                        "Purchase Amount",
                        "Purchase Cash Withdrawal",
                        "Purchase Taxable Withdrawal",
                        "Purchase Tax-deferred Withdrawal",
                        "Purchase Roth Withdrawal",
                        "Purchase Estimated Tax",
                        "ACA MAGI After Conversion",
                        "ACA Status",
                    ]
                    breakdown_cols = [c for c in breakdown_cols if c in detail_purchase_rows.columns]
                    breakdown = detail_purchase_rows[breakdown_cols].copy()
                    for col in [
                        "Purchase Amount",
                        "Purchase Cash Withdrawal",
                        "Purchase Taxable Withdrawal",
                        "Purchase Tax-deferred Withdrawal",
                        "Purchase Roth Withdrawal",
                        "Purchase Estimated Tax",
                        "ACA MAGI After Conversion",
                    ]:
                        if col in breakdown.columns:
                            breakdown[col] = breakdown[col].map(lambda x: format_money(float(x)))
                    st.caption("Detailed scenario purchase funding breakdown for this chart:")
                    st.dataframe(breakdown, use_container_width=True, hide_index=True)
            else:
                st.caption("Active planned purchases are hidden here, so the detailed balance chart, Roth conversion chart, and table below show the plan without purchase impacts.")
        fig2 = go.Figure()
        for col in ["Cash", "Taxable", "Tax-deferred", "Roth"]:
            if col in proj.columns and proj[col].sum() > 0:
                fig2.add_trace(go.Scatter(
                    x=proj["Age"],
                    y=proj[col],
                    mode="lines",
                    stackgroup="one",
                    name=col,
                    hovertemplate=f"Age=%{{x}}<br>{col}=%{{y:$,.0f}}<extra></extra>",
                ))
        if "Total" in proj.columns:
            fig2.add_trace(go.Scatter(
                x=proj["Age"],
                y=proj["Total"],
                mode="lines",
                name="Total portfolio",
                line=dict(color="rgba(255,255,255,0)", width=6),
                hovertemplate="Age=%{x}<br>Total portfolio=%{y:$,.0f}<extra></extra>",
                showlegend=False,
            ))
        add_roth_window_annotation(fig2, assumptions, scenario, selected)
        fig2.update_layout(
            yaxis_tickprefix="$",
            yaxis_tickformat=",.0f",
            xaxis_title="Age",
            yaxis_title="Projected balance",
            hovermode="x unified",
        )
        st.plotly_chart(fig2, use_container_width=True)
        render_annual_roth_conversion_chart(proj, assumptions, scenario, selected)

        details = proj.copy()
        numeric_details = proj.copy()
        arrow_cols = [
            "Total", "Cash", "Taxable", "Tax-deferred", "Roth",
            "After-tax Estimate",
        ]

        rmd_start_age = int(float(assumptions.get("rmd_start_age", 73)))

        standard_money_cols = [
            "Total",
            "After-tax Estimate",
            "Estimated Future Tax Drag",
            "Cash",
            "Taxable",
            "Tax-deferred",
            "Roth",
            "Gross Withdrawal Need",
            "Social Security",
            "Net Portfolio Withdrawal",
            "Roth Conversion",
            "Estimated Roth Tax",
            "Taxable Social Security",
            "Ordinary Taxable Income",
            "Income Tax",
            "Taxable Brokerage Withdrawal",
            "Taxable Gain Realized",
            "Capital Gains Tax",
            "Estimated Total Annual Tax",
            "Cumulative Estimated Tax",
            "Purchase Amount",
            "Purchase Cash Withdrawal",
            "Purchase Taxable Withdrawal",
            "Purchase Tax-deferred Withdrawal",
            "Purchase Roth Withdrawal",
            "Purchase Taxable Gain",
            "Purchase Capital Gains Tax",
            "Purchase Ordinary Income Tax",
            "Purchase Estimated Tax",
            "Purchase Shortfall",
            "ACA MAGI Before Conversion",
            "ACA MAGI After Conversion",
            "ACA MAGI Limit",
            "ACA Headroom After Conversion",
        ]
        for col in standard_money_cols:
            if col in details.columns:
                if col in arrow_cols and col in numeric_details.columns:
                    prior_values = numeric_details[col].shift(1)
                    details[col] = [
                        format_money_with_direction(cur, None if pd.isna(prev) else float(prev))
                        if pd.notnull(cur) and str(cur).strip() not in ["", "-", "—", "nan"]
                        else "—"
                        for cur, prev in zip(numeric_details[col], prior_values)
                    ]
                else:
                    details[col] = details[col].apply(
                        lambda x: format_money(float(x))
                        if pd.notnull(x) and str(x).strip() not in ["", "-", "—", "nan"]
                        else "—"
                    )

        # Also catch new money-like columns not explicitly listed above.
        money_name_patterns = (
            "Tax", "Taxable", "MAGI", "Headroom", "Limit", "Withdrawal", "Balance",
            "Amount", "RMD", "Conversion", "Income", "Need", "Social Security",
            "Cash", "Roth", "Total", "After-tax", "Purchase", "Shortfall"
        )
        for col in details.columns:
            if col in arrow_cols:
                continue
            if col in {"Year", "Age"} or "Rate" in str(col) or "Bracket" in str(col) or "Status" in str(col) or "Strategy" in str(col) or "Retired" in str(col):
                continue
            if col in standard_money_cols or any(pattern in str(col) for pattern in money_name_patterns):
                try:
                    details[col] = details[col].apply(
                        lambda x: format_money(float(x))
                        if pd.notnull(x) and str(x).strip() not in ["", "-", "—", "nan"]
                        else "—"
                    )
                except Exception:
                    pass

        if "Year" in details.columns:
            details["Year"] = details["Year"].astype(int)
        if "Age" in details.columns:
            details["Age"] = details["Age"].astype(int)

        # Make zero-heavy diagnostic columns easier to read.
        # A dash means "not applicable yet" rather than an actual modeled $0 event.
        if "Estimated RMD" in details.columns:
            details["Estimated RMD"] = numeric_details.apply(
                lambda r: "—" if float(r.get("Age", 0)) < rmd_start_age else format_money(float(r.get("Estimated RMD", 0.0))),
                axis=1,
            )

        if "Conversion Headroom Remaining" in details.columns:
            def _format_headroom(row):
                conversion = float(row.get("Roth Conversion", 0.0) or 0.0)
                headroom = float(row.get("Conversion Headroom Remaining", 0.0) or 0.0)
                if conversion <= 0:
                    return "—"
                if abs(headroom) < 1:
                    return "At target"
                return format_money(headroom)
            details["Conversion Headroom Remaining"] = numeric_details.apply(_format_headroom, axis=1)

        if "Tax Payment Shortfall" in details.columns:
            def _format_shortfall(row):
                shortfall = float(row.get("Tax Payment Shortfall", 0.0) or 0.0)
                if abs(shortfall) < 1:
                    return "$0"
                return format_money(shortfall)
            details["Tax Payment Shortfall"] = numeric_details.apply(_format_shortfall, axis=1)

        if "Effective Conversion Tax Rate" in details.columns:
            details["Effective Conversion Tax Rate"] = details["Effective Conversion Tax Rate"].map(lambda x: f"{float(x):.1%}" if float(x) > 0 else "—")

        detail_column_config = {
            "Year": st.column_config.NumberColumn("Year", help="Projection year number, starting at 0 for the current year."),
            "Age": st.column_config.NumberColumn("Age", help="Your projected age in this row."),
            "Retired": st.column_config.CheckboxColumn("Retired", help="Whether the model treats this year as a retirement year. Retirement changes contributions, withdrawals, Social Security, and Roth conversion behavior."),
            "Total": st.column_config.TextColumn("Total", help="Projected total of all modeled buckets before subtracting estimated future tax drag."),
            "Cash": st.column_config.TextColumn("Cash", help="Projected cash/checking/savings balances."),
            "Taxable": st.column_config.TextColumn("Taxable", help="Projected regular brokerage/taxable investment balance."),
            "Tax-deferred": st.column_config.TextColumn("Tax-deferred", help="Projected traditional 401k/IRA-style balance before future withdrawal taxes."),
            "Roth": st.column_config.TextColumn("Roth", help="Projected Roth balance. Roth assets are treated as after-tax in this model."),
            "Gross Withdrawal Need": st.column_config.TextColumn("Gross Withdrawal Need", help="Modeled retirement spending need before subtracting Social Security."),
            "Social Security": st.column_config.TextColumn("Social Security", help="Projected Social Security income based on the start age and annual amount in Assumptions."),
            "Net Portfolio Withdrawal": st.column_config.TextColumn("Net Portfolio Withdrawal", help="Gross withdrawal need minus Social Security. This amount is drawn from portfolio buckets in order: Cash, Taxable, Tax-deferred, Roth."),
            "Roth Conversion": st.column_config.TextColumn("Roth Conversion", help="Amount moved from Tax-deferred into Roth in this year under the selected scenario."),
            "Estimated Roth Tax": st.column_config.TextColumn("Estimated Roth Tax", help="Estimated federal tax generated by this year's Roth conversion."),
            "Roth Strategy": st.column_config.TextColumn("Roth Strategy", help="The conversion strategy active in this row."),
            "Effective Conversion Tax Rate": st.column_config.TextColumn("Effective Conversion Tax Rate", help="Average federal tax rate on this year's Roth conversion: Estimated Roth Tax divided by Roth Conversion. This can rise while total tax falls if the conversion amount shrinks."),
            "Marginal Bracket Reached": st.column_config.TextColumn("Marginal Bracket Reached", help="Highest statutory ordinary federal bracket reached after the Roth conversion. This can still show 12% even when the effective conversion tax rate is 17–19% because Social Security tax feedback can raise the effective rate without entering the 22% statutory bracket."),
            "Conversion Headroom Remaining": st.column_config.TextColumn("Conversion Headroom Remaining", help="Estimated taxable-income room still left after this year's conversion. 'At target' means the bracket-fill solver intentionally used up the available room for the selected target bracket. A dash means no Roth conversion occurred."),
            "Ordinary Marginal Bracket": st.column_config.TextColumn("Ordinary Marginal Bracket", help="Highest ordinary federal bracket reached by total taxable ordinary income in this row after deductions. Included for context; use Marginal Bracket Reached for the Roth conversion outcome."),
            "Tax Payment Shortfall": st.column_config.TextColumn("Tax Payment Shortfall", help="Any conversion tax that could not be paid from the selected tax source. $0 is good: it means cash/taxable funds covered the tax bill."),
            "Estimated RMD": st.column_config.TextColumn("Estimated RMD", help="Approximate required minimum distribution from tax-deferred accounts. It shows a dash before the RMD start age because RMDs are not applicable yet."),
            "Taxable Social Security": st.column_config.TextColumn("Taxable Social Security", help="Estimated portion of Social Security subject to ordinary income tax based on provisional-income rules."),
            "Ordinary Taxable Income": st.column_config.TextColumn("Ordinary Taxable Income", help="Taxable income after the standard deduction from RMDs, Roth conversions, taxable Social Security, and any base taxable income assumption."),
            "Income Tax": st.column_config.TextColumn("Income Tax", help="Estimated ordinary federal income tax for this year, including tax caused by Roth conversions and later RMD/SS stacking."),
            "Taxable Brokerage Withdrawal": st.column_config.TextColumn("Taxable Brokerage Withdrawal", help="Estimated amount of spending withdrawals that came from taxable brokerage after cash/RMDs were used."),
            "Taxable Gain Realized": st.column_config.TextColumn("Taxable Gain Realized", help="Estimated capital gain portion of taxable brokerage withdrawals, based on the editable cost-basis ratio."),
            "Capital Gains Tax": st.column_config.TextColumn("Capital Gains Tax", help="Estimated tax on realized gains from taxable brokerage withdrawals."),
            "Estimated Annual Tax on RMD": st.column_config.TextColumn("Estimated Annual Tax on RMD", help="Legacy compatibility column. In this version, use Income Tax and Capital Gains Tax for the main annual tax view."),
            "Estimated Total Annual Tax": st.column_config.TextColumn("Estimated Total Annual Tax", help="Estimated Income Tax plus Capital Gains Tax for this row. Break-even compares cumulative totals against the no-conversion baseline."),
            "Cumulative Estimated Tax": st.column_config.TextColumn("Cumulative Estimated Tax", help="Running total of Estimated Total Annual Tax. Break-even is where the scenario's cumulative taxes fall below the no-conversion baseline."),
            "Purchase Names": st.column_config.TextColumn("Purchase Names", help="Names of one-time planned purchases modeled in this row."),
            "Purchase Amount": st.column_config.TextColumn("Purchase Amount", help="One-time purchase amount modeled in this year, such as a boat, vehicle, house project, or other major expense."),
            "Purchase Funding Strategy": st.column_config.TextColumn("Purchase Funding Strategy", help="Bucket order used to fund the purchase. Edit this on the Purchase Planner page."),
            "Purchase Cash Withdrawal": st.column_config.TextColumn("Purchase Cash Withdrawal", help="Estimated purchase dollars drawn from cash/savings."),
            "Purchase Taxable Withdrawal": st.column_config.TextColumn("Purchase Taxable Withdrawal", help="Estimated purchase dollars drawn from taxable brokerage."),
            "Purchase Tax-deferred Withdrawal": st.column_config.TextColumn("Purchase Tax-deferred Withdrawal", help="Estimated purchase dollars drawn from traditional 401k/IRA-style accounts. This may create ordinary income tax."),
            "Purchase Roth Withdrawal": st.column_config.TextColumn("Purchase Roth Withdrawal", help="Estimated purchase dollars drawn from Roth accounts."),
            "Purchase Taxable Gain": st.column_config.TextColumn("Purchase Taxable Gain", help="Estimated capital-gain portion of taxable brokerage withdrawals used for the purchase."),
            "Purchase Capital Gains Tax": st.column_config.TextColumn("Purchase Capital Gains Tax", help="Estimated capital-gains tax generated by taxable brokerage withdrawals for the purchase."),
            "Purchase Ordinary Income Tax": st.column_config.TextColumn("Purchase Ordinary Income Tax", help="Estimated incremental ordinary income tax if the purchase is funded from tax-deferred accounts."),
            "Purchase Estimated Tax": st.column_config.TextColumn("Purchase Estimated Tax", help="Estimated incremental tax from the purchase funding strategy."),
            "Purchase Shortfall": st.column_config.TextColumn("Purchase Shortfall", help="Purchase amount that could not be funded under the selected strategy and available balances."),
            "Estimated Future Tax Drag": st.column_config.TextColumn("Estimated Future Tax Drag", help="Remaining tax-deferred balance multiplied by the editable future tax-rate assumption."),
            "After-tax Estimate": st.column_config.TextColumn("After-tax Estimate", help="Total projected balance minus Estimated Future Tax Drag."),
        }
        st.caption("Scroll horizontally to review detailed projection columns. Hover column headers for field notes. Dashes mean not applicable in that year; $0 means the event was modeled but no dollars were due.")
        st.dataframe(
            details,
            use_container_width=True,
            hide_index=True,
            height=520,
            column_config=detail_column_config,
        )


def page_assumptions() -> None:
    st.title("Assumptions")
    st.caption("General model settings. Roth conversion amounts and bracket-fill rules live on the Roth Conversions page; this page only selects which plan the Dashboard uses.")
    assumptions = get_assumptions()
    accounts = read_accounts()
    account_names = accounts["name"].tolist() if not accounts.empty else []
    overflow_default = assumptions.get("overflow_account_name", "Taxable Brokerage")
    cash_default = assumptions.get("cash_account_name", "Cash Bridge Savings")
    overflow_index = account_names.index(overflow_default) if overflow_default in account_names else 0
    cash_index = account_names.index(cash_default) if cash_default in account_names else 0

    with st.form("assumptions_form"):
        current_age = st.number_input("Current age", value=int(float(assumptions.get("current_age", 52))), step=1, help="Your current age. The model uses this to map projection years to actual ages.")
        spouse_age = st.number_input("Spouse age", value=int(float(assumptions.get("spouse_age", 50))), step=1, help="Tracked for reference. The current model primarily uses your current age for projection rows.")
        retirement_age = st.number_input("Target retirement age", value=int(float(assumptions.get("retirement_age", 58))), step=1, help="Age when work contributions stop and retirement withdrawals begin.")
        years_to_project = st.number_input("Years to project", value=int(float(assumptions.get("years_to_project", 20))), step=1, help="Number of future years shown on the main Dashboard projection.")
        annual_spend = st.number_input(
            "Annual retirement spending target (today's dollars)",
            value=float(assumptions.get("annual_spend_retirement", 75000)),
            step=1000.0,
            help=(
                "Enter the lifestyle spending target in today's purchasing power. "
                "The app inflates this amount to the first retirement year, then continues inflating it annually in the projection."
            ),
        )
        withdrawal_rate = st.number_input("Retirement withdrawal rate", value=float(assumptions.get("retirement_withdrawal_rate", 0.0425)), step=0.0025, format="%.4f", help="Reference withdrawal-rate metric only. Spending is driven by the annual retirement spending target above, then increased with inflation.")

        st.markdown("### Cash / bridge behavior")
        bridge_target = st.number_input("Bridge fund target", value=float(assumptions.get("bridge_target", 200000)), step=1000.0, help="Dedicated stable retirement bridge target, e.g. high-yield savings.")
        checking_target = st.number_input("Checking buffer target", value=float(assumptions.get("checking_buffer_target", 45000)), step=1000.0, help="Operating cash buffer target.")
        cash_cap_enabled = st.checkbox("Cap cash at bridge + checking target", value=assumptions.get("cash_cap_enabled", "1") == "1", help="When enabled, excess pre-retirement cash above the bridge + checking targets is routed to the overflow brokerage account.")
        use_routed_cashflow = st.checkbox("Use routed cashflow instead of Cash/Taxable contribution rows", value=assumptions.get("use_routed_cashflow", "1") == "1", help="Recommended. Prevents double-counting by ignoring Cash/Taxable contribution rows and using the routing rules below instead.")
        cash_account_name = st.selectbox("Cash-build account", account_names, index=cash_index, help="Account that receives routed cash contributions until the bridge/checking cash target is full.") if account_names else "Cash Bridge Savings"
        overflow_account_name = st.selectbox("Overflow / brokerage account", account_names, index=overflow_index, help="Account that receives overflow once cash is full, usually your taxable brokerage account.") if account_names else "Taxable Brokerage"

        st.markdown("### Routed cashflow rules")
        paychecks_per_year = st.number_input("Paychecks per year", value=float(assumptions.get("paychecks_per_year", 26)), step=1.0, help="Used only by routed cashflow rules. 26 = biweekly paychecks.")
        brokerage_before = st.number_input("Brokerage per paycheck before cash is full", value=float(assumptions.get("brokerage_per_paycheck_before_cash_full", 3000)), step=100.0, help="Amount routed to taxable brokerage each paycheck while the cash target is still being filled.")
        brokerage_after = st.number_input("Brokerage per paycheck after cash is full", value=float(assumptions.get("brokerage_per_paycheck_after_cash_full", 4200)), step=100.0, help="Amount routed to taxable brokerage each paycheck after the cash target is full.")
        cash_per_paycheck = st.number_input("Cash per paycheck until cash is full", value=float(assumptions.get("cash_per_paycheck_until_full", 1200)), step=100.0, help="Amount routed to the cash-build account each paycheck until the target is full.")
        extra_cash_quarter = st.number_input("Extra cash per quarter until cash is full", value=float(assumptions.get("extra_cash_per_quarter_until_full", 16000)), step=1000.0, help="Additional quarterly amount used to fill the cash bridge. Optional; set to 0 if not applicable.")
        send_extra_after = st.checkbox(
            "After cash is full, continue sending quarterly extra cash to brokerage",
            value=assumptions.get("send_extra_cash_to_brokerage_after_full", "0") == "1",
            help="Leave this OFF if the quarterly extra cash is only temporary overflow used to fill the cash bridge. Turn it ON only if that extra surplus continues after the cash target is reached.",
        )

        st.markdown("### Return / inflation assumptions")
        stock_return = st.number_input("Annual stock return assumption", value=float(assumptions.get("stock_return", 0.07)), step=0.005, format="%.3f", help="Annual growth rate applied to Taxable, Tax-deferred, and Roth buckets before withdrawals/conversions.")
        cash_return = st.number_input("Annual cash/bond return assumption", value=float(assumptions.get("bond_cash_return", 0.035)), step=0.005, format="%.3f", help="Expected annual return for Cash bucket accounts such as high-yield savings or checking.")
        inflation = st.number_input("Inflation assumption", value=float(assumptions.get("inflation_rate", 0.025)), step=0.005, format="%.3f", help="Inflates the today's-dollar spending target to the first retirement year, then inflates spending and Social Security after their start years.")

        first_year_spend_estimate = annual_spend * ((1 + inflation) ** max(float(retirement_age) - float(current_age), 0))
        st.info(
            f"Spending calculator: {format_money(annual_spend)} in today's dollars ≈ "
            f"{format_money(first_year_spend_estimate)} in the first retirement year at age {int(retirement_age)} "
            f"using {inflation:.1%} annual inflation."
        )

        st.markdown("### Roth conversion plan used by dashboard")
        st.caption(
            "Roth conversion amounts are now controlled on the Roth Conversions page, not by the old single annual conversion fields. "
            "This selector chooses which active Roth plan the main Dashboard and the Scenarios withdrawal chart use."
        )
        active_roth_scenarios = read_roth_scenarios(active_only=True)
        if active_roth_scenarios.empty:
            dashboard_roth_scenario_name = assumptions.get("dashboard_roth_scenario_name", "No conversions")
            st.info("No active Roth scenarios found. Add or activate scenarios on the Roth Conversions page.")
        else:
            roth_names = active_roth_scenarios["name"].tolist()
            saved_roth_name = assumptions.get("dashboard_roth_scenario_name", "No conversions")
            roth_idx = roth_names.index(saved_roth_name) if saved_roth_name in roth_names else 0
            dashboard_roth_scenario_name = st.selectbox(
                "Default Dashboard Roth plan",
                roth_names,
                index=roth_idx,
                help="This selected plan drives the main Dashboard projection. Edit the actual conversion amounts, bracket-fill rules, and tax-payment source on the Roth Conversions page.",
            )

        roth_end_at_ss_age = st.checkbox(
            "Show Social Security as the Roth conversion taper point",
            value=assumptions.get("roth_end_at_ss_age", "1") == "1",
            help=(
                "When enabled, charts label the years before Social Security as the peak conversion window and years after SS as reduced/tapered conversion years. "
                "This is not a hard stop: scenario End Age still controls the final conversion year, and bracket-fill amounts naturally shrink after SS begins because SS income is included in the tax calculation."
            ),
        )

        with st.expander("Legacy Roth fields", expanded=False):
            st.markdown(
                "The old fields `annual_roth_conversion`, `roth_conversion_start_age`, `roth_conversion_end_age`, "
                "`tax_rate_on_roth_conversion`, and `pay_roth_conversion_tax_from_portfolio` remain in the database only as a fallback "
                "for older code paths. The current app uses the named scenarios on the Roth Conversions page instead. "
                "Social Security is treated as a conversion taper point, not a hard stop; use each scenario's End Age to control the final conversion year."
            )

        st.markdown("### Social Security")
        social_security_age = st.number_input("Social Security start age", value=int(float(assumptions.get("social_security_age", 67))), step=1, help="Age when Social Security begins. This affects portfolio withdrawals, taxable Social Security, and Roth bracket-fill room.")
        social_security_annual = st.number_input("Annual Social Security estimate", value=float(assumptions.get("social_security_annual", 0)), step=1000.0, help="Annual Social Security benefit at the start age, before inflation adjustments in later years.")

        submitted = st.form_submit_button("Save assumptions")
        if submitted:
            updates = {
                "current_age": current_age,
                "spouse_age": spouse_age,
                "retirement_age": retirement_age,
                "years_to_project": years_to_project,
                "annual_spend_retirement": annual_spend,
                "retirement_withdrawal_rate": withdrawal_rate,
                "bridge_target": bridge_target,
                "checking_buffer_target": checking_target,
                "cash_cap_enabled": "1" if cash_cap_enabled else "0",
                "use_routed_cashflow": "1" if use_routed_cashflow else "0",
                "cash_account_name": cash_account_name,
                "overflow_account_name": overflow_account_name,
                "paychecks_per_year": paychecks_per_year,
                "brokerage_per_paycheck_before_cash_full": brokerage_before,
                "brokerage_per_paycheck_after_cash_full": brokerage_after,
                "cash_per_paycheck_until_full": cash_per_paycheck,
                "extra_cash_per_quarter_until_full": extra_cash_quarter,
                "send_extra_cash_to_brokerage_after_full": "1" if send_extra_after else "0",
                "stock_return": stock_return,
                "bond_cash_return": cash_return,
                "inflation_rate": inflation,
                "dashboard_roth_scenario_name": dashboard_roth_scenario_name,
                "roth_end_at_ss_age": "1" if roth_end_at_ss_age else "0",
                "social_security_age": social_security_age,
                "social_security_annual": social_security_annual,
            }
            for k, v in updates.items():
                set_assumption(k, str(v))
            st.success("Assumptions saved.")
            st.rerun()



def purchase_optimizer_candidate_strategies() -> list[tuple[str, str, str]]:
    """Candidate funding mixes for the purchase optimizer.

    Returns display label, internal strategy string, and a short note.
    The optimizer compares these by running full projections, so it can show
    when using some tax-deferred money for a purchase lowers future RMD/tax
    pressure enough to offset some of the purchase-year tax.
    """
    return [
        ("Cash first", "Cash then Taxable then Tax-deferred then Roth", "Preserve investments first; usually lowest current tax if cash is available."),
        ("Taxable first", "Taxable then Cash then Tax-deferred then Roth", "Uses brokerage first; may realize capital gains but preserves cash."),
        ("Taxable only", "Taxable only", "Shows the pure brokerage-funded case."),
        ("Tax-deferred only", "Tax-deferred only", "Shows the strongest RMD-reduction case, but may create a large ordinary tax bill in the purchase year."),
        ("Preserve Roth mix", custom_mix_strategy(25, 50, 25, 0), "Blended approach: cash + taxable + some IRA, but leaves Roth untouched."),
        ("RMD reduction mix", custom_mix_strategy(15, 25, 60, 0), "Intentionally uses more tax-deferred dollars to reduce future RMD pressure."),
        ("Balanced mix", custom_mix_strategy(25, 35, 40, 0), "Middle-ground tax smoothing: taxable plus some traditional money, preserving Roth."),
        ("Low current-tax mix", custom_mix_strategy(60, 40, 0, 0), "Avoids ordinary-income spike by using cash and taxable first."),
        ("Roth only", "Roth only", "Usually a last-resort case; included for comparison."),
    ]


def purchase_funding_candidate_labels() -> list[str]:
    """User-facing candidate names shown in the purchase editor dropdown."""
    return [label for label, _, _ in purchase_optimizer_candidate_strategies()]


def candidate_label_to_strategy(label_or_strategy: str) -> str:
    """Convert a displayed candidate label to the internal funding strategy string.

    The purchase table stores the internal strategy string so projections can use
    simple bucket-order/custom-mix logic. The UI shows friendly candidate names
    so the purchase row and optimizer candidates stay aligned.
    """
    text = str(label_or_strategy)
    for label, strategy, _ in purchase_optimizer_candidate_strategies():
        if text == label or text == strategy:
            return strategy
    return PURCHASE_FUNDING_STRATEGIES[0]


def purchase_strategy_display_name(strategy: str) -> str:
    """Return the friendly candidate label for a stored funding strategy."""
    text = str(strategy)
    for label, internal_strategy, _ in purchase_optimizer_candidate_strategies():
        if text == label or text == internal_strategy:
            return label
    return text


def best_practical_purchase_candidate_label(
    purchase: dict,
    purchase_age: int,
    assumptions: Dict[str, str],
    roth_scenario: dict | None,
    years: int,
) -> str:
    """Choose the practical dashboard funding candidate for a purchase age.

    During ACA years, prefer fully funded ACA-safe candidates. After ACA years,
    prefer tax-deferred funding when fully funded because it may reduce future
    RMD pressure and the ACA cliff is gone. Otherwise fall back to the strongest
    after-tax ending value among fully funded candidates.
    """
    purchase_age = int(purchase_age)
    test_is_aca_year = aca_start_age(assumptions) <= purchase_age <= aca_end_age(assumptions)

    baseline = projection_with_after_tax_estimate(
        build_projection(years, assumptions, roth_scenario=roth_scenario, ignore_purchases=True),
        assumptions,
    )
    base_end = baseline.iloc[-1]
    base_age_rows = baseline[baseline["Age"].astype(int) == purchase_age]
    base_age_row = base_age_rows.iloc[0] if not base_age_rows.empty else base_end
    base_aca_cost = float(base_age_row.get("ACA Annual Healthcare Cost", 0.0) or 0.0)

    rows = []
    for label, strategy, _note in purchase_optimizer_candidate_strategies():
        tmp = pd.DataFrame([{
            "name": purchase.get("name", "Purchase"),
            "purchase_age": purchase_age,
            "amount": float(purchase.get("amount", 0.0) or 0.0),
            "funding_strategy": strategy,
            "include_in_projection": 1,
            "notes": purchase.get("notes", ""),
        }])
        proj = projection_with_after_tax_estimate(
            build_projection(years, assumptions, roth_scenario=roth_scenario, purchases_override=tmp),
            assumptions,
        )
        end = proj.iloc[-1]
        purchase_year_rows = proj[proj["Age"].astype(int) == purchase_age]
        pr = purchase_year_rows.iloc[0] if not purchase_year_rows.empty else end

        aca_status = str(pr.get("ACA Status", "Not ACA year"))
        aca_cost_status = str(pr.get("ACA Cost Status", "Not ACA year"))
        aca_healthcare_cost = float(pr.get("ACA Annual Healthcare Cost", 0.0) or 0.0)
        aca_cost_change = aca_healthcare_cost - base_aca_cost
        aca_over_target = test_is_aca_year and (
            aca_status != "Under target"
            or "Over" in aca_cost_status
            or "cliff" in aca_cost_status.lower()
        )

        rows.append({
            "Candidate": label,
            "Shortfall": float(pr.get("Purchase Shortfall", 0.0) or 0.0),
            "ACA Safe?": "No" if aca_over_target else "Yes",
            "ACA Cost Change": aca_cost_change,
            "After-tax Ending Impact": float(end.get("After-tax Estimate", 0.0) or 0.0) - float(base_end.get("After-tax Estimate", 0.0) or 0.0),
        })

    candidates = pd.DataFrame(rows)
    if candidates.empty:
        return "Saved purchase strategy"

    fully_funded = candidates[candidates["Shortfall"].astype(float) <= 0.0].copy()
    if fully_funded.empty:
        return "Saved purchase strategy"

    if test_is_aca_year:
        safe = fully_funded[fully_funded["ACA Safe?"] == "Yes"].copy()
        if not safe.empty:
            return str(safe.sort_values(["ACA Cost Change", "After-tax Ending Impact"], ascending=[True, False]).iloc[0]["Candidate"])
        return str(fully_funded.sort_values(["ACA Cost Change", "After-tax Ending Impact"], ascending=[True, False]).iloc[0]["Candidate"])

    if purchase_age > aca_end_age(assumptions):
        td = fully_funded[fully_funded["Candidate"] == "Tax-deferred only"]
        if not td.empty:
            return "Tax-deferred only"

    return str(fully_funded.sort_values(["After-tax Ending Impact"], ascending=[False]).iloc[0]["Candidate"])


def dashboard_purchase_rows_with_override(
    purchases: pd.DataFrame,
    override_label: str,
    assumptions: Dict[str, str] | None = None,
    roth_scenario: dict | None = None,
    years: int = 35,
    age_override: int | None = None,
) -> tuple[pd.DataFrame, str]:
    """Apply dashboard-only purchase age/funding overrides.

    The Purchase Planner keeps the saved/default strategy. Dashboard controls
    let you test a different purchase age and funding candidate without editing
    the saved purchase row.
    """
    if purchases is None or purchases.empty:
        return pd.DataFrame(), ""

    out = purchases.copy()
    if age_override is not None:
        out["purchase_age"] = int(age_override)

    override_label = str(override_label or "Auto practical candidate")
    effective_label = override_label

    if override_label == "Auto practical candidate":
        labels = []
        if assumptions is not None:
            for idx, row in out.iterrows():
                label = best_practical_purchase_candidate_label(
                    row.to_dict(),
                    int(row.get("purchase_age", 0)),
                    assumptions,
                    roth_scenario,
                    years,
                )
                out.at[idx, "funding_strategy"] = candidate_label_to_strategy(label)
                labels.append(label)
        effective_label = ", ".join(sorted(set(labels))) if labels else "Auto practical candidate"
    elif override_label != "Saved purchase strategy":
        out["funding_strategy"] = candidate_label_to_strategy(override_label)
        effective_label = override_label
    else:
        effective_label = "Saved purchase strategy"

    return out, effective_label


def estimate_rmd_at_age_from_projection(projection: pd.DataFrame, age: int) -> float:
    rows = projection[projection["Age"].astype(int) == int(age)]
    if rows.empty:
        return 0.0
    return float(rows.iloc[0].get("Estimated RMD", 0.0) or 0.0)


def bucket_at_age_from_projection(projection: pd.DataFrame, age: int, bucket: str) -> float:
    rows = projection[projection["Age"].astype(int) == int(age)]
    if rows.empty or bucket not in projection.columns:
        return 0.0
    return float(rows.iloc[0].get(bucket, 0.0) or 0.0)


def page_purchase_planner() -> None:
    st.title("Purchase Planner")
    st.caption(
        "Model one-time major purchases, such as a boat, car, remodel, or relocation cost. "
        "Active purchases are included in Dashboard and Roth projections. Use funding strategies to test where the money should come from."
    )

    assumptions = get_assumptions()
    dashboard_roth_scenario, dashboard_roth_name, _ = get_dashboard_roth_scenario(assumptions)

    st.subheader("Purchase editor")
    purchases = read_planned_purchases(active_only=False)
    if purchases.empty:
        editor_df = pd.DataFrame({
            "ID": pd.Series(dtype="Int64"),
            "Name": pd.Series(dtype="object"),
            "Purchase Age": pd.Series(dtype="int"),
            "Amount": pd.Series(dtype="float"),
            "Funding Strategy": pd.Series(dtype="object"),
            "Include": pd.Series(dtype="bool"),
            "Notes": pd.Series(dtype="object"),
        })
    else:
        editor_df = purchases.rename(columns={
            "id": "ID",
            "name": "Name",
            "purchase_age": "Purchase Age",
            "amount": "Amount",
            "funding_strategy": "Funding Strategy",
            "include_in_projection": "Include",
            "notes": "Notes",
        })
        editor_df["Include"] = editor_df["Include"].astype(bool)
        editor_df["Funding Strategy"] = editor_df["Funding Strategy"].map(purchase_strategy_display_name)
        editor_df = editor_df[["ID", "Name", "Purchase Age", "Amount", "Funding Strategy", "Include", "Notes"]]

    edited = st.data_editor(
        editor_df,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "ID": st.column_config.NumberColumn("ID", disabled=True),
            "Name": st.column_config.TextColumn("Name", required=True, help="Short label for the planned purchase, e.g. 'Boat purchase'."),
            "Purchase Age": st.column_config.NumberColumn("Purchase Age", min_value=0, max_value=120, step=1, required=True, help="Age when the one-time purchase occurs."),
            "Amount": st.column_config.NumberColumn("Amount", min_value=0.0, step=10000.0, format="$%.2f", required=True, help="Purchase price or one-time outflow amount."),
            "Funding Strategy": st.column_config.SelectboxColumn(
                "Funding Strategy",
                options=purchase_funding_candidate_labels(),
                required=True,
                help="Choose one of the same candidate strategies used by the optimizer. The saved purchase will use this candidate's funding mix/order in the Dashboard and projections.",
            ),
            "Include": st.column_config.CheckboxColumn("Include", help="Only included purchases affect Dashboard/Roth projections."),
            "Notes": st.column_config.TextColumn("Notes", help="Optional notes about the purchase or assumptions."),
        },
        key="purchase_editor",
    )

    st.caption(
        "Funding Strategy now uses the same candidate names as the optimizer below. "
        "Pick the candidate you want reflected in Dashboard/projections; use the optimizer table to decide which candidate is best."
    )

    if st.button("Save purchases", type="primary"):
        save_purchase_editor(edited)
        st.success("Planned purchases saved.")
        st.rerun()

    st.subheader("Plan impact")
    years = st.slider("Years to compare", 5, 45, max(35, int(float(assumptions.get("years_to_project", 20)))), help="Projection length used for purchase impact summaries.")
    active_purchases = read_planned_purchases(active_only=True)
    baseline = projection_with_after_tax_estimate(build_projection(years, assumptions, roth_scenario=dashboard_roth_scenario, ignore_purchases=True), assumptions)
    with_purchases = projection_with_after_tax_estimate(build_projection(years, assumptions, roth_scenario=dashboard_roth_scenario), assumptions)

    if active_purchases.empty:
        st.info("No active purchases are currently included. Add one above, check Include, and save.")
    else:
        st.caption(f"Impact uses the Dashboard Roth plan: **{dashboard_roth_name}**.")
        b_end = baseline.iloc[-1]
        p_end = with_purchases.iloc[-1]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Active planned purchases", str(len(active_purchases)))
        c2.metric("Total purchase dollars", format_money(float(active_purchases["amount"].sum())))
        c3.metric("Ending portfolio impact", format_money(float(p_end["Total"] - b_end["Total"])))
        c4.metric("After-tax ending impact", format_money(float(p_end["After-tax Estimate"] - b_end["After-tax Estimate"])))

        impact = pd.DataFrame([{
            "Ending Age": int(p_end["Age"]),
            "Total Without Purchases": float(b_end["Total"]),
            "Total With Purchases": float(p_end["Total"]),
            "Total Impact": float(p_end["Total"] - b_end["Total"]),
            "After-tax Without Purchases": float(b_end["After-tax Estimate"]),
            "After-tax With Purchases": float(p_end["After-tax Estimate"]),
            "After-tax Impact": float(p_end["After-tax Estimate"] - b_end["After-tax Estimate"]),
            "Cumulative Tax Impact": float(p_end.get("Cumulative Estimated Tax", 0.0) - b_end.get("Cumulative Estimated Tax", 0.0)),
        }])
        for col in impact.columns:
            if col != "Ending Age":
                impact[col] = impact[col].map(lambda x: format_money(float(x)))
        st.dataframe(impact, use_container_width=True, hide_index=True)

    st.subheader("Funding strategy comparison")
    st.caption("Pick one purchase and compare different ways to fund it. This creates temporary what-if projections and does not change your saved purchase row.")
    if purchases.empty:
        st.info("Add a purchase above to compare funding strategies.")
        return
    purchase_names = purchases["name"].tolist()
    selected_name = st.selectbox("Purchase to compare", purchase_names)
    selected_purchase = purchases.loc[purchases["name"] == selected_name].iloc[0].to_dict()
    purchase_age = int(selected_purchase["purchase_age"])
    purchase_is_aca_year = aca_start_age(assumptions) <= purchase_age <= aca_end_age(assumptions)
    if purchase_is_aca_year:
        st.warning(
            f"This purchase occurs at age {purchase_age}, inside the ACA window "
            f"({aca_start_age(assumptions)}–{aca_end_age(assumptions)}). "
            "Funding with tax-deferred dollars can create ordinary income and may push MAGI over the ACA target/cliff. "
            "The optimizer now prioritizes ACA-safe candidates before maximizing after-tax ending value."
        )
    else:
        st.info(
            f"This purchase occurs at age {purchase_age}, outside the modeled ACA window "
            f"({aca_start_age(assumptions)}–{aca_end_age(assumptions)}). ACA subsidy preservation is less important for this purchase age."
        )

    comparison_rows = []
    for strategy in PURCHASE_FUNDING_STRATEGIES:
        tmp = pd.DataFrame([{
            "name": selected_purchase["name"],
            "purchase_age": int(selected_purchase["purchase_age"]),
            "amount": float(selected_purchase["amount"]),
            "funding_strategy": strategy,
            "include_in_projection": 1,
            "notes": selected_purchase.get("notes", ""),
        }])
        proj = projection_with_after_tax_estimate(build_projection(years, assumptions, roth_scenario=dashboard_roth_scenario, purchases_override=tmp), assumptions)
        end = proj.iloc[-1]
        purchase_year_rows = proj[proj["Age"] == int(selected_purchase["purchase_age"])]
        pr = purchase_year_rows.iloc[0] if not purchase_year_rows.empty else end
        comparison_rows.append({
            "Funding Strategy": strategy,
            "Purchase Age": int(selected_purchase["purchase_age"]),
            "Purchase Amount": float(selected_purchase["amount"]),
            "Purchase Tax": float(pr.get("Purchase Estimated Tax", 0.0)),
            "Purchase Shortfall": float(pr.get("Purchase Shortfall", 0.0)),
            "ACA MAGI After Purchase": float(pr.get("ACA MAGI After Conversion", 0.0)),
            "ACA Headroom After Purchase": float(pr.get("ACA Headroom After Conversion", 0.0)),
            "ACA Healthcare Cost": float(pr.get("ACA Annual Healthcare Cost", 0.0)),
            "ACA Status": str(pr.get("ACA Status", "Not ACA year")),
            "ACA Cost Status": str(pr.get("ACA Cost Status", "Not ACA year")),
            "Ending Total": float(end["Total"]),
            "After-tax Ending Estimate": float(end["After-tax Estimate"]),
            "Cumulative Estimated Tax": float(end.get("Cumulative Estimated Tax", 0.0)),
            "Ending Cash": float(end.get("Cash", 0.0)),
            "Ending Taxable": float(end.get("Taxable", 0.0)),
            "Ending Tax-deferred": float(end.get("Tax-deferred", 0.0)),
            "Ending Roth": float(end.get("Roth", 0.0)),
        })
    comp = pd.DataFrame(comparison_rows)
    if not comp.empty:
        best_after_tax = comp["After-tax Ending Estimate"].max()
        comp["After-tax Difference vs Best"] = comp["After-tax Ending Estimate"] - best_after_tax
        money_cols = [c for c in comp.columns if c not in {"Funding Strategy", "Purchase Age", "ACA Status", "ACA Cost Status"}]
        display = comp.copy()
        if "Funding Strategy" in display.columns:
            display["Funding Strategy"] = display["Funding Strategy"].map(purchase_strategy_display_name)
        for col in money_cols:
            display[col] = display[col].map(lambda x: format_money(float(x)))
        st.dataframe(
            display,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Funding Strategy": st.column_config.TextColumn("Funding Strategy", help="Bucket order used to fund the purchase."),
                "Purchase Tax": st.column_config.TextColumn("Purchase Tax", help="Estimated incremental tax generated in the purchase year by this funding strategy."),
                "After-tax Difference vs Best": st.column_config.TextColumn("After-tax Difference vs Best", help="How far this strategy trails the best after-tax ending estimate in this comparison window."),
            },
        )


    st.subheader("Tax-aware funding optimizer")
    st.caption(
        "This compares blended funding mixes by running full projections. It helps answer whether a large purchase can reduce future RMD/tax pressure enough to offset some purchase-year tax. "
        "Negative future-tax change means the purchase funding choice lowers modeled taxes after the purchase year."
    )

    optimizer_rows = []
    baseline_no_purchase = projection_with_after_tax_estimate(
        build_projection(years, assumptions, roth_scenario=dashboard_roth_scenario, ignore_purchases=True),
        assumptions,
    )
    base_end = baseline_no_purchase.iloc[-1]
    purchase_age = int(selected_purchase["purchase_age"])
    rmd_check_age = max(75, int(float(assumptions.get("rmd_start_age", 73))) + 2)
    base_purchase_age_rows = baseline_no_purchase[baseline_no_purchase["Age"].astype(int) == purchase_age]
    base_purchase_row = base_purchase_age_rows.iloc[0] if not base_purchase_age_rows.empty else base_end
    base_purchase_cum_tax = float(base_purchase_row.get("Cumulative Estimated Tax", 0.0) or 0.0)
    base_purchase_aca_cost = float(base_purchase_row.get("ACA Annual Healthcare Cost", 0.0) or 0.0)
    base_future_tax_after_purchase = float(base_end.get("Cumulative Estimated Tax", 0.0)) - base_purchase_cum_tax
    base_rmd_check = estimate_rmd_at_age_from_projection(baseline_no_purchase, rmd_check_age)
    base_td_check = bucket_at_age_from_projection(baseline_no_purchase, rmd_check_age, "Tax-deferred")

    for label, strategy, note in purchase_optimizer_candidate_strategies():
        tmp = pd.DataFrame([{
            "name": selected_purchase["name"],
            "purchase_age": purchase_age,
            "amount": float(selected_purchase["amount"]),
            "funding_strategy": strategy,
            "include_in_projection": 1,
            "notes": selected_purchase.get("notes", ""),
        }])
        proj = projection_with_after_tax_estimate(
            build_projection(years, assumptions, roth_scenario=dashboard_roth_scenario, purchases_override=tmp),
            assumptions,
        )
        end = proj.iloc[-1]
        purchase_year_rows = proj[proj["Age"].astype(int) == purchase_age]
        pr = purchase_year_rows.iloc[0] if not purchase_year_rows.empty else end
        purchase_cum_tax = float(pr.get("Cumulative Estimated Tax", 0.0) or 0.0)
        future_tax_after_purchase = float(end.get("Cumulative Estimated Tax", 0.0) or 0.0) - purchase_cum_tax
        future_tax_change = future_tax_after_purchase - base_future_tax_after_purchase
        rmd_check = estimate_rmd_at_age_from_projection(proj, rmd_check_age)
        td_check = bucket_at_age_from_projection(proj, rmd_check_age, "Tax-deferred")

        aca_status = str(pr.get("ACA Status", "Not ACA year"))
        aca_cost_status = str(pr.get("ACA Cost Status", "Not ACA year"))
        aca_healthcare_cost = float(pr.get("ACA Annual Healthcare Cost", 0.0) or 0.0)
        aca_cost_change = aca_healthcare_cost - base_purchase_aca_cost
        aca_over_target = purchase_is_aca_year and (aca_status != "Under target" or "Over" in aca_cost_status or "cliff" in aca_cost_status.lower())

        optimizer_rows.append({
            "Candidate": label,
            "Suggested Cash": float(pr.get("Purchase Cash Withdrawal", 0.0) or 0.0),
            "Suggested Taxable": float(pr.get("Purchase Taxable Withdrawal", 0.0) or 0.0),
            "Suggested Tax-deferred": float(pr.get("Purchase Tax-deferred Withdrawal", 0.0) or 0.0),
            "Suggested Roth": float(pr.get("Purchase Roth Withdrawal", 0.0) or 0.0),
            "Purchase-year Tax": float(pr.get("Purchase Estimated Tax", 0.0) or 0.0),
            "ACA MAGI After Purchase": float(pr.get("ACA MAGI After Conversion", 0.0) or 0.0),
            "ACA Headroom After Purchase": float(pr.get("ACA Headroom After Conversion", 0.0) or 0.0),
            "ACA Healthcare Cost": aca_healthcare_cost,
            "ACA Cost Change": aca_cost_change,
            "ACA Status": aca_status,
            "ACA Cost Status": aca_cost_status,
            "ACA Safe?": "No" if aca_over_target else "Yes",
            "Lifetime Tax Impact": float(end.get("Cumulative Estimated Tax", 0.0) or 0.0) - float(base_end.get("Cumulative Estimated Tax", 0.0) or 0.0),
            "Future Tax Change After Purchase": future_tax_change,
            "Future Tax Savings After Purchase": max(-future_tax_change, 0.0),
            f"RMD Reduction at {rmd_check_age}": base_rmd_check - rmd_check,
            f"Tax-deferred Reduction at {rmd_check_age}": base_td_check - td_check,
            "After-tax Ending Impact": float(end.get("After-tax Estimate", 0.0) or 0.0) - float(base_end.get("After-tax Estimate", 0.0) or 0.0),
            "Ending Tax-deferred": float(end.get("Tax-deferred", 0.0) or 0.0),
            "Ending Roth": float(end.get("Roth", 0.0) or 0.0),
            "Shortfall": float(pr.get("Purchase Shortfall", 0.0) or 0.0),
            "Lowers Future Taxes?": "Yes" if future_tax_change < 0 else "No",
            "Note": note,
        })

    opt = pd.DataFrame(optimizer_rows)
    if not opt.empty:
        # If the purchase happens during ACA years, avoid candidates that blow the ACA target/cliff
        # before choosing the strongest after-tax ending value. Outside ACA years, use the old sort.
        if purchase_is_aca_year and "ACA Safe?" in opt.columns:
            opt["ACA Sort Penalty"] = opt["ACA Safe?"].map({"Yes": 0, "No": 1}).fillna(0)
            opt = opt.sort_values(
                ["Shortfall", "ACA Sort Penalty", "ACA Cost Change", "After-tax Ending Impact", "Future Tax Savings After Purchase"],
                ascending=[True, True, True, False, False],
            )
        else:
            opt = opt.sort_values(["Shortfall", "After-tax Ending Impact", "Future Tax Savings After Purchase"], ascending=[True, False, False])
        best = opt.iloc[0]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Best candidate", str(best["Candidate"]))
        m2.metric("Suggested purchase-year tax", format_money(float(best["Purchase-year Tax"])))
        if purchase_is_aca_year:
            m3.metric("ACA cost change in purchase year", format_money(float(best["ACA Cost Change"])))
        else:
            m3.metric("Future tax savings after purchase", format_money(float(best["Future Tax Savings After Purchase"])))
        m4.metric(f"RMD reduction at {rmd_check_age}", format_money(float(best[f"RMD Reduction at {rmd_check_age}"])))

        show_cols = [
            "Candidate",
            "Suggested Cash",
            "Suggested Taxable",
            "Suggested Tax-deferred",
            "Suggested Roth",
            "Purchase-year Tax",
            "ACA Safe?",
            "ACA MAGI After Purchase",
            "ACA Headroom After Purchase",
            "ACA Healthcare Cost",
            "ACA Cost Change",
            "ACA Status",
            "ACA Cost Status",
            "Future Tax Change After Purchase",
            "Future Tax Savings After Purchase",
            f"RMD Reduction at {rmd_check_age}",
            f"Tax-deferred Reduction at {rmd_check_age}",
            "Lifetime Tax Impact",
            "After-tax Ending Impact",
            "Lowers Future Taxes?",
            "Shortfall",
            "Note",
        ]
        opt_display = opt[show_cols].copy()
        for col in opt_display.columns:
            if col not in {"Candidate", "Lowers Future Taxes?", "ACA Safe?", "ACA Status", "ACA Cost Status", "Note"}:
                opt_display[col] = opt_display[col].map(lambda x: format_money(float(x)))
        st.dataframe(
            opt_display,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Candidate": st.column_config.TextColumn("Candidate", help="Funding mix or order being tested."),
                "Suggested Cash": st.column_config.TextColumn("Suggested Cash", help="Purchase amount modeled from cash/savings."),
                "Suggested Taxable": st.column_config.TextColumn("Suggested Taxable", help="Purchase amount modeled from taxable brokerage. The model estimates capital gains using the cost-basis ratio assumption."),
                "Suggested Tax-deferred": st.column_config.TextColumn("Suggested Tax-deferred", help="Purchase amount modeled from traditional 401k/IRA money. This can create current ordinary income tax but may reduce future RMDs."),
                "Suggested Roth": st.column_config.TextColumn("Suggested Roth", help="Purchase amount modeled from Roth. Usually preserved unless intentionally tested."),
                "Purchase-year Tax": st.column_config.TextColumn("Purchase-year Tax", help="Estimated incremental tax created by funding the purchase in the purchase year."),
                "ACA Safe?": st.column_config.TextColumn("ACA Safe?", help="For purchase ages inside the ACA window, Yes means the candidate stays under the modeled ACA MAGI target/cliff."),
                "ACA MAGI After Purchase": st.column_config.TextColumn("ACA MAGI After Purchase", help="Estimated ACA MAGI in the purchase year after Roth conversions and purchase funding effects."),
                "ACA Headroom After Purchase": st.column_config.TextColumn("ACA Headroom After Purchase", help="Remaining room under the ACA MAGI target after the purchase."),
                "ACA Healthcare Cost": st.column_config.TextColumn("ACA Healthcare Cost", help="Estimated household ACA premium/OOP cost in the purchase year."),
                "ACA Cost Change": st.column_config.TextColumn("ACA Cost Change", help="Purchase-year ACA healthcare cost change versus the no-purchase baseline."),
                "ACA Status": st.column_config.TextColumn("ACA Status", help="Whether the purchase-year row remains under the ACA target."),
                "ACA Cost Status": st.column_config.TextColumn("ACA Cost Status", help="Subsidy status from the ACA cost model."),
                "Future Tax Change After Purchase": st.column_config.TextColumn("Future Tax Change After Purchase", help="Modeled cumulative tax change after the purchase year versus no purchase. Negative means future taxes are lower."),
                "Future Tax Savings After Purchase": st.column_config.TextColumn("Future Tax Savings After Purchase", help="Positive display of future tax reduction after the purchase year. This is where using some tax-deferred money can show benefits."),
                f"RMD Reduction at {rmd_check_age}": st.column_config.TextColumn(f"RMD Reduction at {rmd_check_age}", help="Estimated reduction in forced RMD at the selected later age versus no purchase."),
                f"Tax-deferred Reduction at {rmd_check_age}": st.column_config.TextColumn(f"Tax-deferred Reduction at {rmd_check_age}", help="Reduction in traditional/tax-deferred balance at the selected later age versus no purchase."),
                "Lifetime Tax Impact": st.column_config.TextColumn("Lifetime Tax Impact", help="Total modeled tax change through the end of the comparison window, including purchase-year tax and later tax changes."),
                "After-tax Ending Impact": st.column_config.TextColumn("After-tax Ending Impact", help="Ending after-tax estimate minus the no-purchase baseline. This includes the fact that the purchase spent money."),
                "Lowers Future Taxes?": st.column_config.TextColumn("Lowers Future Taxes?", help="Yes means future modeled taxes after the purchase year are lower than the no-purchase baseline."),
                "Shortfall": st.column_config.TextColumn("Shortfall", help="Amount that could not be funded under this mix/order from available modeled balances."),
                "Note": st.column_config.TextColumn("Note", help="Plain-English description of what the candidate is testing."),
            },
        )
        st.caption(
            "Use this as a planning signal, not tax advice. The optimizer now separates purchase-year tax, ACA healthcare/subsidy impact, and future tax changes. "
            "During ACA years, a strategy that looks tax-efficient long term may be a bad funding choice if it pushes MAGI over the ACA cliff."
        )

    st.subheader("Purchase timing comparison")
    st.caption(
        "This answers the timing question: does the same purchase look better before, during, or after ACA years? "
        "The table tests the selected purchase amount across a range of ages and shows the best ACA-aware candidate at each age."
    )

    timing_col1, timing_col2, timing_col3 = st.columns(3)
    default_start_age = max(int(float(assumptions.get("retirement_age", 57))), purchase_age - 3)
    default_end_age = min(90, purchase_age + 8)
    default_latest_enjoyable_age = min(default_end_age, max(aca_end_age(assumptions) + 2, purchase_age + 3))
    with timing_col1:
        timing_start_age = st.number_input(
            "Timing comparison start age",
            min_value=int(float(assumptions.get("current_age", 52))),
            max_value=120,
            value=int(default_start_age),
            step=1,
            help="First purchase age to test."
        )
    with timing_col2:
        timing_end_age = st.number_input(
            "Timing comparison end age",
            min_value=int(timing_start_age),
            max_value=120,
            value=int(default_end_age),
            step=1,
            help="Last purchase age to test."
        )
    with timing_col3:
        latest_enjoyable_age = st.number_input(
            "Latest age you actually want it",
            min_value=int(timing_start_age),
            max_value=int(timing_end_age),
            value=int(min(max(default_latest_enjoyable_age, timing_start_age), timing_end_age)),
            step=1,
            help="Lifestyle/practical cutoff. The app will show an earliest-practical recommendation so the math does not blindly push the purchase too late."
        )

    prefer_traditional_after_aca = st.checkbox(
        "After ACA, prefer tax-deferred funding if fully funded",
        value=True,
        help=(
            "Useful when you do not care about leaving a large estate and would rather use traditional 401k/IRA dollars after ACA ends, "
            "pay the tax, and reduce future RMD pressure."
        ),
    )
    st.caption(
        "Timing results now separate the mathematically best age from the practical/lifestyle-first age. "
        "For a boat or lifestyle upgrade, the earliest ACA-safe post-ACA age may be more useful than the age with the highest ending portfolio."
    )

    timing_rows = []
    for test_age in range(int(timing_start_age), int(timing_end_age) + 1):
        # Baseline for this age: same projection without the purchase.
        baseline_for_age = projection_with_after_tax_estimate(
            build_projection(years, assumptions, roth_scenario=dashboard_roth_scenario, ignore_purchases=True),
            assumptions,
        )
        base_end_for_age = baseline_for_age.iloc[-1]
        base_age_rows = baseline_for_age[baseline_for_age["Age"].astype(int) == int(test_age)]
        base_age_row = base_age_rows.iloc[0] if not base_age_rows.empty else base_end_for_age
        base_age_aca_cost = float(base_age_row.get("ACA Annual Healthcare Cost", 0.0) or 0.0)
        test_is_aca_year = aca_start_age(assumptions) <= int(test_age) <= aca_end_age(assumptions)

        candidate_rows_for_age = []
        for label, strategy, note in purchase_optimizer_candidate_strategies():
            tmp = pd.DataFrame([{
                "name": selected_purchase["name"],
                "purchase_age": int(test_age),
                "amount": float(selected_purchase["amount"]),
                "funding_strategy": strategy,
                "include_in_projection": 1,
                "notes": selected_purchase.get("notes", ""),
            }])
            proj = projection_with_after_tax_estimate(
                build_projection(years, assumptions, roth_scenario=dashboard_roth_scenario, purchases_override=tmp),
                assumptions,
            )
            end = proj.iloc[-1]
            purchase_year_rows = proj[proj["Age"].astype(int) == int(test_age)]
            pr = purchase_year_rows.iloc[0] if not purchase_year_rows.empty else end

            aca_status = str(pr.get("ACA Status", "Not ACA year"))
            aca_cost_status = str(pr.get("ACA Cost Status", "Not ACA year"))
            aca_healthcare_cost = float(pr.get("ACA Annual Healthcare Cost", 0.0) or 0.0)
            aca_cost_change = aca_healthcare_cost - base_age_aca_cost
            aca_over_target = test_is_aca_year and (aca_status != "Under target" or "Over" in aca_cost_status or "cliff" in aca_cost_status.lower())

            candidate_rows_for_age.append({
                "Purchase Age": int(test_age),
                "ACA Year?": "Yes" if test_is_aca_year else "No",
                "Candidate": label,
                "ACA Safe?": "No" if aca_over_target else "Yes",
                "ACA MAGI After Purchase": float(pr.get("ACA MAGI After Conversion", 0.0) or 0.0),
                "ACA Headroom After Purchase": float(pr.get("ACA Headroom After Conversion", 0.0) or 0.0),
                "ACA Healthcare Cost": aca_healthcare_cost,
                "ACA Cost Change": aca_cost_change,
                "Purchase-year Tax": float(pr.get("Purchase Estimated Tax", 0.0) or 0.0),
                "Shortfall": float(pr.get("Purchase Shortfall", 0.0) or 0.0),
                "After-tax Ending Impact": float(end.get("After-tax Estimate", 0.0) or 0.0) - float(base_end_for_age.get("After-tax Estimate", 0.0) or 0.0),
                "Ending Tax-deferred": float(end.get("Tax-deferred", 0.0) or 0.0),
                "Note": note,
            })

        candidates_for_age = pd.DataFrame(candidate_rows_for_age)
        if candidates_for_age.empty:
            continue

        # Pure modeled best for reference.
        modeled_best_for_age = candidates_for_age.sort_values(
            ["Shortfall", "After-tax Ending Impact"],
            ascending=[True, False],
        ).iloc[0]

        if test_is_aca_year:
            candidates_for_age["ACA Sort Penalty"] = candidates_for_age["ACA Safe?"].map({"Yes": 0, "No": 1}).fillna(0)
            candidates_for_age = candidates_for_age.sort_values(
                ["Shortfall", "ACA Sort Penalty", "ACA Cost Change", "After-tax Ending Impact"],
                ascending=[True, True, True, False],
            )
        else:
            if prefer_traditional_after_aca and int(test_age) > aca_end_age(assumptions):
                trad_rows = candidates_for_age[
                    (candidates_for_age["Candidate"] == "Tax-deferred only")
                    & (candidates_for_age["Shortfall"].astype(float) <= 0.0)
                ]
                if not trad_rows.empty:
                    candidates_for_age = pd.concat([
                        trad_rows,
                        candidates_for_age[candidates_for_age["Candidate"] != "Tax-deferred only"],
                    ], ignore_index=True)
                else:
                    candidates_for_age = candidates_for_age.sort_values(
                        ["Shortfall", "After-tax Ending Impact"],
                        ascending=[True, False],
                    )
            else:
                candidates_for_age = candidates_for_age.sort_values(
                    ["Shortfall", "After-tax Ending Impact"],
                    ascending=[True, False],
                )

        best_for_age = candidates_for_age.iloc[0].to_dict()
        best_for_age["Modeled Best Candidate"] = str(modeled_best_for_age["Candidate"])
        best_for_age["Modeled Best After-tax Impact"] = float(modeled_best_for_age["After-tax Ending Impact"])
        timing_rows.append(best_for_age)

    timing = pd.DataFrame(timing_rows)
    if not timing.empty:
        timing_display = timing.drop(columns=["ACA Sort Penalty"], errors="ignore").copy()
        money_cols = [
            "ACA MAGI After Purchase",
            "ACA Headroom After Purchase",
            "ACA Healthcare Cost",
            "ACA Cost Change",
            "Purchase-year Tax",
            "Shortfall",
            "After-tax Ending Impact",
            "Ending Tax-deferred",
            "Modeled Best After-tax Impact",
        ]
        for col in money_cols:
            if col in timing_display.columns:
                timing_display[col] = timing_display[col].map(lambda x: format_money(float(x)))
        st.dataframe(
            timing_display,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Purchase Age": st.column_config.NumberColumn("Purchase Age", help="Age being tested for the selected purchase."),
                "ACA Year?": st.column_config.TextColumn("ACA Year?", help="Whether this age is inside the modeled ACA window."),
                "Candidate": st.column_config.TextColumn("Practical candidate", help="Candidate selected after ACA-safety and lifestyle preferences. This may differ from the pure modeled after-tax best."),
                "Modeled Best Candidate": st.column_config.TextColumn("Modeled Best Candidate", help="The pure highest after-tax ending value candidate for this age, before lifestyle-first overrides."),
                "ACA Safe?": st.column_config.TextColumn("ACA Safe?", help="During ACA years, Yes means the best candidate remains under the modeled ACA target/cliff."),
                "ACA MAGI After Purchase": st.column_config.TextColumn("ACA MAGI After Purchase", help="Estimated MAGI in the purchase year."),
                "ACA Headroom After Purchase": st.column_config.TextColumn("ACA Headroom After Purchase", help="Remaining ACA MAGI room after purchase funding."),
                "ACA Healthcare Cost": st.column_config.TextColumn("ACA Healthcare Cost", help="Estimated household ACA healthcare cost in the purchase year."),
                "ACA Cost Change": st.column_config.TextColumn("ACA Cost Change", help="Purchase-year ACA healthcare cost change versus no purchase."),
                "Purchase-year Tax": st.column_config.TextColumn("Purchase-year Tax", help="Estimated incremental tax in the purchase year."),
                "After-tax Ending Impact": st.column_config.TextColumn("After-tax Ending Impact", help="Ending after-tax estimate versus no purchase baseline."),
                "Ending Tax-deferred": st.column_config.TextColumn("Ending Tax-deferred", help="Traditional/tax-deferred balance at the end of the projection for this timing/candidate."),
                "Modeled Best After-tax Impact": st.column_config.TextColumn("Modeled Best After-tax Impact", help="After-tax ending impact for the pure modeled-best candidate at this purchase age."),
            },
        )

        fully_funded_timing = timing[timing["Shortfall"].astype(float) <= 0.0].copy()
        aca_safe_timing = fully_funded_timing[fully_funded_timing["ACA Safe?"] == "Yes"].copy()

        if not aca_safe_timing.empty:
            modeled_best_timing = aca_safe_timing.sort_values(["After-tax Ending Impact"], ascending=[False]).iloc[0]
            st.info(
                f"Pure modeled-best ACA-safe timing in this range: age {int(modeled_best_timing['Purchase Age'])} "
                f"using {modeled_best_timing['Candidate']}. This maximizes modeled after-tax ending value, not lifestyle enjoyment."
            )

            practical_pool = aca_safe_timing[
                (aca_safe_timing["Purchase Age"].astype(int) > aca_end_age(assumptions))
                & (aca_safe_timing["Purchase Age"].astype(int) <= int(latest_enjoyable_age))
            ].copy()
            if practical_pool.empty:
                practical_pool = aca_safe_timing[aca_safe_timing["Purchase Age"].astype(int) <= int(latest_enjoyable_age)].copy()

            if not practical_pool.empty:
                practical_timing = practical_pool.sort_values(["Purchase Age"], ascending=[True]).iloc[0]
                st.success(
                    f"Lifestyle-first timing: age {int(practical_timing['Purchase Age'])} "
                    f"using {practical_timing['Candidate']}. This prioritizes buying soon after ACA risk is gone rather than waiting for the mathematically highest ending balance."
                )
            else:
                st.warning(
                    f"No ACA-safe, fully funded option was found by your latest enjoyable age of {int(latest_enjoyable_age)}. "
                    "Try increasing cash/taxable funding, shrinking the purchase, or accepting ACA/tax tradeoffs."
                )
        else:
            st.warning(
                "No ACA-safe, fully funded timing option was found in this range. "
                "Try delaying the purchase until after the ACA window or increasing cash/taxable funding."
            )



def page_scenarios() -> None:
    st.title("Scenario Planner")
    assumptions = get_assumptions()
    latest = read_latest_balances().fillna({"balance": 0})

    st.info("Scenario settings below can optionally feed into the main Dashboard projection. Turn them on/off here, then view the Dashboard.")

    st.subheader("Market downturn stress test")
    apply_market = st.checkbox("Apply market downturn scenario to Dashboard projection", value=assumptions.get("apply_market_downturn_scenario", "0") == "1")
    drop = st.slider("Market drop", min_value=0, max_value=50, value=int(float(assumptions.get("market_downturn_percent", 0.25)) * 100), step=5) / 100
    downturn_age = st.number_input("Age when downturn occurs", value=int(float(assumptions.get("market_downturn_age", assumptions.get("retirement_age", 58)))), step=1)
    if st.button("Save market downturn scenario"):
        set_assumption("apply_market_downturn_scenario", "1" if apply_market else "0")
        set_assumption("market_downturn_percent", str(drop))
        set_assumption("market_downturn_age", str(downturn_age))
        st.success("Market downturn scenario saved.")
        st.rerun()

    total_assets = latest["balance"].sum()
    invested = latest.loc[latest["tax_bucket"].isin(["Taxable", "Tax-deferred", "Roth"]), "balance"].sum()
    cash = latest.loc[latest["tax_bucket"] == "Cash", "balance"].sum()
    stressed_total = cash + invested * (1 - drop)
    c1, c2, c3 = st.columns(3)
    c1.metric("Current tracked assets", format_money(total_assets))
    c2.metric("After downturn today", format_money(stressed_total))
    c3.metric("Decline", format_money(total_assets - stressed_total))

    st.subheader("Layoff / contribution pause")
    apply_layoff = st.checkbox("Apply layoff/contribution pause scenario to Dashboard projection", value=assumptions.get("apply_layoff_pause_scenario", "0") == "1")
    pause_months = st.slider("Months of paused contributions", 0, 24, int(float(assumptions.get("layoff_pause_months", 6))))
    extend_retirement = st.checkbox("Extend retirement by the same number of months", value=assumptions.get("extend_retirement_for_layoff", "0") == "1")
    if st.button("Save layoff scenario"):
        set_assumption("apply_layoff_pause_scenario", "1" if apply_layoff else "0")
        set_assumption("layoff_pause_months", str(pause_months))
        set_assumption("extend_retirement_for_layoff", "1" if extend_retirement else "0")
        st.success("Layoff scenario saved.")
        st.rerun()

    contribs = read_contributions(active_only=True)
    annual_contrib = sum(annualized_contribution(r["amount"], r["frequency"]) for _, r in contribs.iterrows()) if not contribs.empty else 0
    if assumptions.get("use_routed_cashflow", "1") == "1":
        paychecks = float(assumptions.get("paychecks_per_year", 26))
        annual_contrib += float(assumptions.get("brokerage_per_paycheck_before_cash_full", 3000)) * paychecks
        annual_contrib += float(assumptions.get("cash_per_paycheck_until_full", 1200)) * paychecks
        annual_contrib += float(assumptions.get("extra_cash_per_quarter_until_full", 16000)) * 4
    missed = annual_contrib * pause_months / 12
    st.metric("Estimated missed contributions", format_money(missed))

    st.subheader("Retirement withdrawal model")
    dashboard_roth_scenario, dashboard_roth_name, _ = get_dashboard_roth_scenario(assumptions)
    st.caption(f"This chart uses the same Roth plan selected for the Dashboard: **{dashboard_roth_name}**.")
    years = st.slider("Years to model", 5, 45, 30, help="Projection length for this scenario chart.")
    proj = build_projection(years, assumptions, roth_scenario=dashboard_roth_scenario)
    fig = px.line(proj, x="Age", y="Total", markers=True, title="Total nest egg after retirement withdrawals")
    fig.update_layout(yaxis_tickprefix="$", yaxis_tickformat=",.0f")
    st.plotly_chart(fig, use_container_width=True)
    ending = proj.iloc[-1]
    st.metric(f"Projected balance at age {int(ending['Age'])}", format_money(float(ending["Total"])))

    st.subheader("Spending sensitivity")
    spend_values = [50000, 60000, 75000, 90000, 100000, 120000]
    wrs = [0.03, 0.035, 0.04, 0.0425]
    rows = []
    for spend in spend_values:
        row = {"Annual spend": format_money(spend)}
        for wr in wrs:
            row[f"Needed at {wr:.2%}"] = format_money(spend / wr)
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# -----------------------------
# App entry
# -----------------------------


def page_aca_planner() -> None:
    st.title("ACA Planner")
    st.caption(
        "Central place for ACA/healthcare assumptions. Premium numbers are household annual totals for both of you together, not per person."
    )

    assumptions = get_assumptions()

    st.info(
        "Current model: ACA premium tax credits are based on household MAGI and FPL. "
        "The app supports a formula mode using benchmark silver premium, selected-plan premium, FPL %, and an optional 400% FPL cliff, plus a manual simple under/over-target mode."
    )

    with st.form("aca_planner_settings"):
        st.subheader("ACA household and timing")
        c1, c2, c3 = st.columns(3)
        with c1:
            household_size = st.number_input(
                "ACA household size",
                min_value=1,
                max_value=10,
                value=int(float(assumptions.get("aca_household_size", 2))),
                step=1,
                help="Used as a reminder. The FPL dollar amount below is what the model actually uses."
            )
        with c2:
            fpl100 = st.number_input(
                "100% FPL amount for household",
                min_value=0.0,
                value=float(assumptions.get("aca_fpl_100", 21150)),
                step=100.0,
                help="For 2026 coverage, 100% FPL for household size 2 is $21,150."
            )
        with c3:
            target_pct = st.number_input(
                "ACA target % FPL",
                min_value=100.0,
                max_value=800.0,
                value=float(assumptions.get("aca_target_fpl_percent", 400)),
                step=10.0,
                help="Used by Optimize ACA to target MAGI headroom. 400% is the classic subsidy-cliff threshold."
            )

        c4, c5, c6 = st.columns(3)
        with c4:
            safety_buffer = st.number_input(
                "MAGI safety buffer",
                min_value=0.0,
                value=float(assumptions.get("aca_magi_safety_buffer", 1000)),
                step=100.0,
                help="Subtracts this from the MAGI target to avoid modeling exactly on the cliff."
            )
        with c5:
            medicare_age = st.number_input(
                "Medicare age",
                min_value=60,
                max_value=70,
                value=int(float(assumptions.get("aca_medicare_age", 65))),
                step=1,
                help="ACA modeling normally ends the year before Medicare."
            )
        with c6:
            st.metric("Modeled ACA window", f"{aca_start_age(assumptions)}–{aca_end_age(assumptions)}")

        st.subheader("ACA premium model")
        model_mode = st.selectbox(
            "ACA cost model",
            ["Formula", "Manual under/over target"],
            index=0 if str(assumptions.get("aca_model_mode", "Formula")).lower().startswith("formula") else 1,
            help="Formula mode estimates subsidy from benchmark premium and MAGI. Manual mode uses simple under/over annual premium amounts."
        )
        use_cliff = st.checkbox(
            "Apply 400% FPL subsidy cliff",
            value=assumptions.get("aca_use_400_fpl_cliff", "1") == "1",
            help="Current 2026-style rules can create a cliff above 400% FPL if enhanced subsidies are not extended."
        )

        c7, c8 = st.columns(2)
        with c7:
            benchmark = st.number_input(
                "Benchmark Silver annual premium — household total",
                min_value=0.0,
                value=float(assumptions.get("aca_benchmark_silver_annual_premium", 26000)),
                step=500.0,
                help="Full annual premium for the second-lowest-cost Silver plan for both of you together."
            )
        with c8:
            selected_plan = st.number_input(
                "Selected plan annual premium — household total",
                min_value=0.0,
                value=float(assumptions.get("aca_selected_plan_annual_premium", 22000)),
                step=500.0,
                help="Full annual premium for the plan you expect to buy, for both of you together."
            )

        c9, c10 = st.columns(2)
        with c9:
            subsidized_manual = st.number_input(
                "Manual annual premium if under target — household total",
                min_value=0.0,
                value=float(assumptions.get("aca_subsidized_annual_premium", 6000)),
                step=500.0,
                help="Only used in Manual mode."
            )
        with c10:
            full_manual = st.number_input(
                "Manual annual premium if over target — household total",
                min_value=0.0,
                value=float(assumptions.get("aca_full_annual_premium", 18000)),
                step=500.0,
                help="Only used in Manual mode."
            )

        include_oop = st.checkbox(
            "Include rough out-of-pocket estimate",
            value=assumptions.get("aca_include_oop_estimate", "0") == "1",
            help="Optional rough estimate. Premiums are usually the first thing to model; OOP is much harder to predict."
        )
        c11, c12 = st.columns(2)
        with c11:
            oop_under = st.number_input(
                "Annual OOP estimate if under target",
                min_value=0.0,
                value=float(assumptions.get("aca_oop_if_under_target", 3000)),
                step=500.0,
            )
        with c12:
            oop_over = st.number_input(
                "Annual OOP estimate if over target",
                min_value=0.0,
                value=float(assumptions.get("aca_oop_if_over_target", 6000)),
                step=500.0,
            )

        st.subheader("Medicare / IRMAA rough estimate")
        c13, c14 = st.columns(2)
        with c13:
            irmaa_threshold = st.number_input(
                "IRMAA MAGI threshold estimate",
                min_value=0.0,
                value=float(assumptions.get("irmaa_magi_threshold", 206000)),
                step=1000.0,
            )
        with c14:
            irmaa_surcharge = st.number_input(
                "Estimated annual IRMAA surcharge",
                min_value=0.0,
                value=float(assumptions.get("irmaa_annual_surcharge", 0)),
                step=100.0,
            )

        submitted = st.form_submit_button("Save ACA / healthcare settings", type="primary")
        if submitted:
            set_assumption("aca_household_size", str(int(household_size)))
            set_assumption("aca_fpl_100", str(float(fpl100)))
            set_assumption("aca_target_fpl_percent", str(float(target_pct)))
            set_assumption("aca_magi_safety_buffer", str(float(safety_buffer)))
            set_assumption("aca_medicare_age", str(int(medicare_age)))
            set_assumption("aca_start_age", "auto")
            set_assumption("aca_end_age", "auto")
            set_assumption("aca_model_mode", model_mode)
            set_assumption("aca_use_400_fpl_cliff", "1" if use_cliff else "0")
            set_assumption("aca_benchmark_silver_annual_premium", str(float(benchmark)))
            set_assumption("aca_selected_plan_annual_premium", str(float(selected_plan)))
            set_assumption("aca_subsidized_annual_premium", str(float(subsidized_manual)))
            set_assumption("aca_full_annual_premium", str(float(full_manual)))
            set_assumption("aca_include_oop_estimate", "1" if include_oop else "0")
            set_assumption("aca_oop_if_under_target", str(float(oop_under)))
            set_assumption("aca_oop_if_over_target", str(float(oop_over)))
            set_assumption("irmaa_magi_threshold", str(float(irmaa_threshold)))
            set_assumption("irmaa_annual_surcharge", str(float(irmaa_surcharge)))
            st.success("ACA / healthcare settings saved.")
            st.rerun()

    st.subheader("ACA cost preview")
    preview_magi_values = [
        aca_magi_limit(assumptions) * 0.75,
        aca_magi_limit(assumptions),
        aca_magi_limit(assumptions) + 1000,
        aca_magi_limit(assumptions) * 1.25,
    ]
    rows = []
    for magi in preview_magi_values:
        cost = estimate_aca_annual_cost_from_magi(magi, assumptions)
        rows.append({
            "MAGI": magi,
            "FPL %": cost["ACA FPL Percent"],
            "Estimated Subsidy": cost["ACA Estimated Subsidy"],
            "Premium Cost": cost["ACA Annual Premium Cost"],
            "OOP Estimate": cost["ACA Annual OOP Estimate"],
            "Total Healthcare Cost": cost["ACA Annual Healthcare Cost"],
            "Status": cost["ACA Cost Status"],
        })
    preview = pd.DataFrame(rows)
    for col in ["MAGI", "Estimated Subsidy", "Premium Cost", "OOP Estimate", "Total Healthcare Cost"]:
        preview[col] = preview[col].map(lambda x: format_money(float(x)))
    preview["FPL %"] = preview["FPL %"].map(lambda x: f"{float(x):.0f}%")
    st.dataframe(preview, hide_index=True, use_container_width=True)


def main() -> None:
    st.set_page_config(page_title="Retirement Planner", page_icon="📈", layout="wide")
    init_db()
    seed_defaults()

    if "startup_db_backup_done" not in st.session_state:
        startup_backup = backup_database("startup")
        st.session_state["startup_db_backup_done"] = True
        if startup_backup is not None:
            st.session_state["last_db_backup_path"] = str(startup_backup)

    with st.sidebar:
        st.title("Planner")
        page = st.radio("Navigate", ["Dashboard", "Balances", "Accounts", "Contributions", "Assumptions", "ACA Planner", "Roth Conversions", "Purchase Planner", "Scenarios"])
        st.divider()
        st.caption(f"Database: {DB_PATH.resolve()}")

        st.markdown("### Database backup / restore")
        st.caption("Backups are timestamped automatically. The app also creates one startup backup per session.")

        if st.button("Backup database now", use_container_width=True):
            backup_path = backup_database("manual")
            if backup_path is None:
                st.error("Database file does not exist yet. Add/save data first, then try again.")
            else:
                st.session_state["last_db_backup_path"] = str(backup_path)
                st.success(f"Backup saved: {backup_path}")

        last_backup_path = st.session_state.get("last_db_backup_path")
        if last_backup_path and Path(last_backup_path).exists():
            st.download_button(
                "Download latest backup",
                data=Path(last_backup_path).read_bytes(),
                file_name=Path(last_backup_path).name,
                mime="application/octet-stream",
                use_container_width=True,
                help="Downloads the most recent timestamped backup created during this app session.",
            )

        with st.expander("Restore from backup", expanded=False):
            st.warning("Restoring replaces the active retirement_planner.db. A timestamped safety backup is created first.")
            uploaded_db = st.file_uploader(
                "Upload a .db backup",
                type=["db", "sqlite", "sqlite3"],
                help="Choose a backup database file previously downloaded from this app.",
            )
            confirm_restore = st.checkbox("I understand this will replace the current database", key="confirm_db_restore")
            if st.button("Restore uploaded database", use_container_width=True, disabled=not confirm_restore):
                ok, message, safety_backup = restore_database_from_upload(uploaded_db)
                if safety_backup is not None:
                    st.session_state["last_db_backup_path"] = str(safety_backup)
                if ok:
                    st.success(message)
                    st.info("Reloading app with restored database...")
                    st.rerun()
                else:
                    st.error(message)
                    if safety_backup is not None:
                        st.info(f"Current database safety backup was saved to: {safety_backup}")

    if page == "Dashboard":
        page_dashboard()
    elif page == "Balances":
        page_balances()
    elif page == "Accounts":
        page_accounts()
    elif page == "Contributions":
        page_contributions()
    elif page == "Assumptions":
        page_assumptions()
    elif page == "ACA Planner":
        page_aca_planner()
    elif page == "Roth Conversions":
        page_roth_conversions()
    elif page == "Purchase Planner":
        page_purchase_planner()
    elif page == "Scenarios":
        page_scenarios()


if __name__ == "__main__":
    main()
