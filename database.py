import os
import sqlite3
import json
import math
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone


# ==========================================
# TROJAN V2 DATABASE
# ==========================================

DATA_DIR = (
    os.getenv("BOT_DATA_DIR")
    or os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    or "."
)

os.makedirs(DATA_DIR, exist_ok=True)

DATABASE = os.path.join(DATA_DIR, "trading_history.db")

VALID_MODES = ("paper", "live")
VALID_STRATEGIES = ("main", "sniper")
SCHEMA_VERSION = 11

# Conservative paper estimate for each swap side; actual DEX costs vary.
PAPER_FEE_RATE = 0.0075


# ==========================================
# TIME AND VALIDATION
# ==========================================

def now():
    return datetime.now(timezone.utc).isoformat()


def today():
    return datetime.now(timezone.utc).date().isoformat()


def validate_mode(mode):
    if mode not in VALID_MODES:
        raise ValueError("Invalid trading mode: " + str(mode))


def validate_strategy(strategy):
    if strategy not in VALID_STRATEGIES:
        raise ValueError("Invalid strategy: " + str(strategy))


def validate_number(value, name):
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise ValueError(name + " must be numeric")

    if not math.isfinite(number):
        raise ValueError(name + " must be finite")

    return number


# ==========================================
# DATABASE CONNECTION
# ==========================================

@contextmanager
def connect():
    connection = sqlite3.connect(
        DATABASE,
        timeout=30
    )

    connection.row_factory = sqlite3.Row

    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA synchronous = NORMAL")

    try:
        yield connection
        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def add_column_if_missing(
    connection,
    table,
    column,
    definition
):
    existing = [
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(" + table + ")"
        ).fetchall()
    ]

    if column not in existing:
        connection.execute(
            "ALTER TABLE " + table
            + " ADD COLUMN " + column
            + " " + definition
        )


# ==========================================
# DAILY STATISTICS RESET
# ==========================================

def _ensure_daily_stats_current(connection, mode):

    current_date = today()

    account = connection.execute("""
        SELECT daily_date
        FROM account_state
        WHERE mode = ?
    """, (mode,)).fetchone()

    if not account:
        return

    stored_date = account["daily_date"]

    if stored_date != current_date:

        connection.execute("""
            UPDATE account_state
            SET daily_loss = 0,
                daily_realized_pnl = 0,
                daily_date = ?,
                updated_at = ?
            WHERE mode = ?
        """, (
            current_date,
            now(),
            mode
        ))


# ==========================================
# DATABASE INITIALIZATION
# ==========================================

def initialize_database():

    with connect() as connection:

        # Refuse a database written by a newer Trojan schema before changing
        # journal mode, creating tables or applying additive migrations.
        schema_table = connection.execute("""
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'schema_meta'
        """).fetchone()
        if schema_table:
            try:
                version_row = connection.execute(
                    "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                ).fetchone()
            except sqlite3.DatabaseError as error:
                raise RuntimeError("Database schema metadata is unreadable") from error
            if version_row is not None:
                try:
                    stored_version = int(version_row["value"])
                except (TypeError, ValueError, OverflowError) as error:
                    raise RuntimeError("Database schema version is invalid") from error
                if stored_version < 0:
                    raise RuntimeError("Database schema version cannot be negative")
                if stored_version > SCHEMA_VERSION:
                    raise RuntimeError(
                        "Database schema is newer than this Trojan build "
                        f"({stored_version} > {SCHEMA_VERSION}); refusing to modify it"
                    )

        connection.execute("PRAGMA journal_mode = WAL")

        connection.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT NOT NULL,
                action TEXT NOT NULL,
                price REAL NOT NULL,
                amount REAL NOT NULL,
                profit_loss REAL DEFAULT 0,
                timestamp TEXT NOT NULL
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT NOT NULL,
                score INTEGER,
                decision TEXT,
                timestamp TEXT NOT NULL
            )
        """)

        add_column_if_missing(
            connection,
            "trades",
            "mode",
            "TEXT NOT NULL DEFAULT 'paper'"
        )

        add_column_if_missing(
            connection,
            "trades",
            "strategy",
            "TEXT NOT NULL DEFAULT 'main'"
        )
        add_column_if_missing(
            connection,
            "trades",
            "copy_wallet",
            "TEXT"
        )

        add_column_if_missing(
            connection,
            "analyses",
            "mode",
            "TEXT NOT NULL DEFAULT 'paper'"
        )

        connection.execute("""
            CREATE TABLE IF NOT EXISTS account_state (
                mode TEXT PRIMARY KEY,
                balance REAL NOT NULL,
                daily_loss REAL NOT NULL DEFAULT 0,
                daily_realized_pnl REAL NOT NULL DEFAULT 0,
                updated_at TEXT,
                daily_date TEXT
            )
        """)

        add_column_if_missing(
            connection,
            "account_state",
            "daily_date",
            "TEXT"
        )

        connection.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                mode TEXT NOT NULL,
                token_address TEXT NOT NULL,
                entry_price REAL NOT NULL,
                invested REAL NOT NULL,
                strategy TEXT NOT NULL DEFAULT 'main',
                opened_at TEXT,
                metadata TEXT,
                PRIMARY KEY (mode, token_address)
            )
        """)

        # Migrate older positions tables.
        add_column_if_missing(
            connection,
            "positions",
            "strategy",
            "TEXT NOT NULL DEFAULT 'main'"
        )

        add_column_if_missing(
            connection,
            "positions",
            "opened_at",
            "TEXT"
        )

        add_column_if_missing(
            connection,
            "positions",
            "metadata",
            "TEXT"
        )

        connection.execute("""
            CREATE TABLE IF NOT EXISTS pending_tokens (
                mode TEXT NOT NULL,
                token_address TEXT NOT NULL,
                strategy TEXT NOT NULL,
                first_seen REAL NOT NULL,
                token_data TEXT,
                PRIMARY KEY (mode, token_address)
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS bot_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                message TEXT,
                token_address TEXT,
                timestamp TEXT NOT NULL
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS token_filter_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT NOT NULL,
                stage TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT,
                score REAL,
                liquidity REAL,
                source_count INTEGER NOT NULL DEFAULT 0,
                metadata TEXT,
                timestamp TEXT NOT NULL
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_filter_events_token
            ON token_filter_events(token_address)
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_filter_events_timestamp
            ON token_filter_events(timestamp)
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS market_candles (
                pool_address TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                open_time INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                PRIMARY KEY (pool_address, timeframe, open_time)
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_market_candles_pool_time
            ON market_candles(pool_address, timeframe, open_time)
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS strategy_config (
                strategy_name TEXT PRIMARY KEY,
                config_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS copy_wallet_watch (
                wallet TEXT PRIMARY KEY,
                label TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS copy_trade_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT NOT NULL,
                signature TEXT NOT NULL UNIQUE,
                action TEXT,
                token_mint TEXT,
                token_delta REAL,
                sol_delta REAL,
                raw_summary TEXT,
                timestamp TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS subject_asset_deltas (
                chain TEXT NOT NULL,
                wallet_address TEXT NOT NULL,
                signature TEXT NOT NULL,
                mint TEXT NOT NULL,
                slot INTEGER,
                block_time INTEGER,
                tx_index INTEGER NOT NULL,
                delta_raw TEXT NOT NULL,
                before_raw TEXT,
                after_raw TEXT,
                decimals INTEGER NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('trade', 'transfer', 'defi', 'other')),
                venue TEXT,
                fee_native_raw TEXT,
                success INTEGER NOT NULL CHECK(success IN (0, 1)),
                created_at TEXT NOT NULL,
                PRIMARY KEY(chain, wallet_address, signature, mint, tx_index)
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_subject_asset_deltas_wallet_time
            ON subject_asset_deltas(wallet_address, block_time DESC, tx_index DESC)
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_subject_asset_deltas_mint_time
            ON subject_asset_deltas(mint, block_time DESC)
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS protocol_validation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                protocol TEXT NOT NULL,
                pool_address TEXT,
                token_mint TEXT,
                success INTEGER NOT NULL,
                reason TEXT,
                observed_at REAL,
                timestamp TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_protocol_validation_protocol
            ON protocol_validation_events(protocol, timestamp)
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS pool_registry (
                mint TEXT PRIMARY KEY,
                pools_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS blacklist_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_type TEXT NOT NULL CHECK(entry_type IN ('token', 'pool', 'account')),
                entry_value TEXT NOT NULL,
                token_mint TEXT,
                reason TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(entry_type, entry_value)
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_blacklist_active_type_value
            ON blacklist_entries(active, entry_type, entry_value)
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_pool_registry_updated_at
            ON pool_registry(updated_at)
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS discovery_source_health (
                source TEXT PRIMARY KEY,
                provider_family TEXT NOT NULL,
                total_runs INTEGER NOT NULL DEFAULT 0,
                successes INTEGER NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                last_success_at REAL,
                last_error_at REAL,
                last_count INTEGER NOT NULL DEFAULT 0,
                last_ok INTEGER NOT NULL DEFAULT 0 CHECK(last_ok IN (0, 1)),
                last_error TEXT,
                latency_ms REAL NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS discovery_scan_state (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                last_scan_at REAL,
                token_count INTEGER NOT NULL DEFAULT 0,
                source_count INTEGER NOT NULL DEFAULT 0,
                independent_provider_count INTEGER NOT NULL DEFAULT 0
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS native_transaction_approvals (
                request_id TEXT PRIMARY KEY,
                message BLOB NOT NULL,
                message_sha256 TEXT NOT NULL,
                protocol TEXT NOT NULL,
                last_valid_block_height INTEGER NOT NULL,
                expires_at REAL NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_native_approvals_expiry
            ON native_transaction_approvals(expires_at)
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        connection.execute("""
            INSERT OR REPLACE INTO schema_meta (key, value)
            VALUES ('schema_version', ?)
        """, (str(SCHEMA_VERSION),))

        # Indexes.
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_mode
            ON trades(mode)
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_timestamp
            ON trades(timestamp)
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_strategy
            ON trades(strategy)
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_analyses_mode
            ON analyses(mode)
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_analyses_token
            ON analyses(token_address)
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_positions_mode
            ON positions(mode)
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_timestamp
            ON bot_events(timestamp)
        """)

    print("================================")
    print("TROJAN V2 DATABASE INITIALIZED")
    print("DATABASE:", DATABASE)
    print("SCHEMA VERSION:", SCHEMA_VERSION)
    print("================================")


# ==========================================
# ACCOUNTS
# ==========================================

def initialize_account(mode, starting_balance):

    validate_mode(mode)

    starting_balance = validate_number(
        starting_balance,
        "starting_balance"
    )

    with connect() as connection:

        connection.execute("""
            INSERT OR IGNORE INTO account_state
            (
                mode,
                balance,
                daily_loss,
                daily_realized_pnl,
                updated_at,
                daily_date
            )
            VALUES (?, ?, 0, 0, ?, ?)
        """, (
            mode,
            starting_balance,
            now(),
            today()
        ))


def get_account_state(mode):

    validate_mode(mode)

    with connect() as connection:

        _ensure_daily_stats_current(
            connection,
            mode
        )

        row = connection.execute("""
            SELECT *
            FROM account_state
            WHERE mode = ?
        """, (mode,)).fetchone()

    return dict(row) if row else None


def save_account_state(
    mode,
    balance,
    daily_loss=0,
    daily_realized_pnl=0
):

    validate_mode(mode)

    balance = validate_number(balance, "balance")
    daily_loss = validate_number(daily_loss, "daily_loss")

    daily_realized_pnl = validate_number(
        daily_realized_pnl,
        "daily_realized_pnl"
    )

    with connect() as connection:

        connection.execute("""
            INSERT INTO account_state
            (
                mode,
                balance,
                daily_loss,
                daily_realized_pnl,
                updated_at,
                daily_date
            )
            VALUES (?, ?, ?, ?, ?, ?)

            ON CONFLICT(mode) DO UPDATE SET
                balance = excluded.balance,
                daily_loss = excluded.daily_loss,
                daily_realized_pnl = excluded.daily_realized_pnl,
                updated_at = excluded.updated_at,
                daily_date = excluded.daily_date
        """, (
            mode,
            balance,
            daily_loss,
            daily_realized_pnl,
            now(),
            today()
        ))


def reset_daily_stats(mode):

    validate_mode(mode)

    with connect() as connection:

        connection.execute("""
            UPDATE account_state
            SET daily_loss = 0,
                daily_realized_pnl = 0,
                daily_date = ?,
                updated_at = ?
            WHERE mode = ?
        """, (
            today(),
            now(),
            mode
        ))


def get_account_summary(mode):

    account = get_account_state(mode)

    if not account:
        return None

    positions = get_positions(mode)

    invested = sum(
        position["invested"]
        for position in positions.values()
    )

    return {
        "mode": mode,
        "balance": round(account["balance"], 2),
        "positions": len(positions),
        "invested": round(invested, 2),
        "daily_loss": round(account["daily_loss"], 2),
        "daily_realized_pnl": round(
            account["daily_realized_pnl"], 2
        )
    }


# ==========================================
# ATOMIC POSITION OPENING
# ==========================================

def open_position_atomic(
    mode,
    token_address,
    entry_price,
    invested,
    strategy="main",
    metadata=None
):

    validate_mode(mode)
    validate_strategy(strategy)

    if not token_address:
        raise ValueError("Missing token address")

    entry_price = validate_number(
        entry_price,
        "entry_price"
    )

    invested = validate_number(
        invested,
        "invested"
    )

    if entry_price <= 0 or invested <= 0:
        raise ValueError("Price and investment must be positive")

    with connect() as connection:

        connection.execute("BEGIN IMMEDIATE")

        account = connection.execute("""
            SELECT balance
            FROM account_state
            WHERE mode = ?
        """, (mode,)).fetchone()

        if not account:
            raise ValueError("Account not initialized: " + mode)

        balance = float(account["balance"])

        entry_fee = invested * PAPER_FEE_RATE
        if balance < invested + entry_fee:
            return "Insufficient balance including estimated entry fee"

        existing = connection.execute("""
            SELECT token_address
            FROM positions
            WHERE mode = ? AND token_address = ?
        """, (mode, token_address)).fetchone()

        if existing:
            return "Position already exists"

        position_metadata = dict(metadata or {})
        position_metadata["paper_fee_rate"] = PAPER_FEE_RATE

        connection.execute("""
            INSERT INTO positions
            (
                mode,
                token_address,
                entry_price,
                invested,
                strategy,
                opened_at,
                metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            mode,
            token_address,
            entry_price,
            invested,
            strategy,
            now(),
            json.dumps(position_metadata)
        ))

        connection.execute("""
            UPDATE account_state
            SET balance = ?,
                updated_at = ?
            WHERE mode = ?
        """, (
            balance - invested - entry_fee,
            now(),
            mode
        ))

        connection.execute("""
            INSERT INTO trades
            (
                token_address, action, price, amount, profit_loss,
                timestamp, mode, strategy, copy_wallet
            )
            VALUES (?, 'BUY', ?, ?, 0, ?, ?, ?, ?)
        """, (
            token_address, entry_price, invested, now(), mode, strategy,
            position_metadata.get("copy_wallet")
        ))

    return "POSITION OPENED"


# ==========================================
# ATOMIC DCA + PARTIAL EXIT OPERATIONS
# ==========================================

def add_position_investment_atomic(
    mode, token_address, current_price, additional_investment, metadata=None
):
    validate_mode(mode)
    current_price = validate_number(current_price, "current_price")
    additional_investment = validate_number(additional_investment, "additional_investment")
    if current_price <= 0 or additional_investment <= 0:
        raise ValueError("DCA price and investment must be positive")
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        position = connection.execute(
            "SELECT * FROM positions WHERE mode = ? AND token_address = ?",
            (mode, token_address)
        ).fetchone()
        account = connection.execute(
            "SELECT * FROM account_state WHERE mode = ?", (mode,)
        ).fetchone()
        if not position or not account:
            return "Position or account not found"
        old_invested = float(position["invested"])
        old_entry = float(position["entry_price"])
        fee = additional_investment * PAPER_FEE_RATE
        if float(account["balance"]) < additional_investment + fee:
            return "Insufficient balance for DCA including estimated fee"
        new_invested = old_invested + additional_investment
        new_entry = ((old_entry * old_invested) + (current_price * additional_investment)) / new_invested
        try:
            stored = json.loads(position["metadata"] or "{}")
        except (ValueError, TypeError):
            stored = {}
        stored.update(metadata or {})
        stored["paper_fee_rate"] = PAPER_FEE_RATE
        connection.execute("""
            UPDATE positions SET entry_price = ?, invested = ?, metadata = ?
            WHERE mode = ? AND token_address = ?
        """, (new_entry, new_invested, json.dumps(stored), mode, token_address))
        connection.execute("""
            UPDATE account_state SET balance = ?, updated_at = ? WHERE mode = ?
        """, (float(account["balance"]) - additional_investment - fee, now(), mode))
        connection.execute("""
            INSERT INTO trades (token_address, action, price, amount, profit_loss,
                                timestamp, mode, strategy, copy_wallet)
            VALUES (?, 'BUY', ?, ?, 0, ?, ?, ?, ?)
        """, (token_address, current_price, additional_investment, now(), mode,
              position["strategy"], stored.get("copy_wallet")))
    return {
        "result": "DCA ADDED", "entry_price": new_entry,
        "invested": new_invested, "additional_investment": additional_investment
    }


def partial_close_position_atomic(
    mode, token_address, current_price, fraction=0.5, reason="PARTIAL TAKE PROFIT"
):
    validate_mode(mode)
    current_price = validate_number(current_price, "current_price")
    fraction = validate_number(fraction, "fraction")
    if current_price <= 0 or not 0 < fraction < 1:
        raise ValueError("Invalid partial exit price or fraction")
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _ensure_daily_stats_current(connection, mode)
        position = connection.execute(
            "SELECT * FROM positions WHERE mode = ? AND token_address = ?",
            (mode, token_address)
        ).fetchone()
        account = connection.execute(
            "SELECT * FROM account_state WHERE mode = ?", (mode,)
        ).fetchone()
        if not position or not account:
            return "Position or account not found"
        invested = float(position["invested"])
        sold_investment = invested * fraction
        remaining = invested - sold_investment
        entry_price = float(position["entry_price"])
        gross_return = sold_investment * (current_price / entry_price)
        try:
            metadata = json.loads(position["metadata"] or "{}")
        except (ValueError, TypeError):
            metadata = {}
        entry_fee_rate = validate_number(metadata.get("paper_fee_rate", PAPER_FEE_RATE), "paper_fee_rate")
        entry_fee_allocated = sold_investment * entry_fee_rate
        exit_fee = gross_return * PAPER_FEE_RATE
        returned = gross_return - exit_fee
        profit_loss = round(returned - sold_investment - entry_fee_allocated, 2)
        new_balance = float(account["balance"]) + returned
        new_daily_loss = float(account["daily_loss"]) + max(0, -profit_loss)
        new_daily_pnl = float(account["daily_realized_pnl"]) + profit_loss
        metadata["paper_fee_rate"] = entry_fee_rate
        metadata["partial_exit_count"] = int(metadata.get("partial_exit_count", 0)) + 1
        connection.execute("""
            UPDATE positions SET invested = ?, metadata = ?
            WHERE mode = ? AND token_address = ?
        """, (remaining, json.dumps(metadata), mode, token_address))
        connection.execute("""
            UPDATE account_state SET balance = ?, daily_loss = ?,
                daily_realized_pnl = ?, daily_date = ?, updated_at = ?
            WHERE mode = ?
        """, (new_balance, new_daily_loss, new_daily_pnl, today(), now(), mode))
        connection.execute("""
            INSERT INTO trades (token_address, action, price, amount, profit_loss,
                                timestamp, mode, strategy, copy_wallet)
            VALUES (?, 'SELL', ?, ?, ?, ?, ?, ?, ?)
        """, (token_address, current_price, returned, profit_loss, now(), mode,
              position["strategy"], metadata.get("copy_wallet")))
    return {
        "result": reason, "profit_loss": profit_loss,
        "balance": round(new_balance, 2), "strategy": position["strategy"],
        "invested": round(sold_investment, 2),
        "remaining_invested": round(remaining, 2),
        "returned_amount": round(returned, 2), "partial": True
    }


# ==========================================
# ATOMIC POSITION CLOSING
# ==========================================

def close_position_atomic(
    mode,
    token_address,
    current_price,
    reason="CLOSED"
):

    validate_mode(mode)

    if not token_address:
        raise ValueError("Missing token address")

    current_price = validate_number(
        current_price,
        "current_price"
    )

    if current_price <= 0:
        raise ValueError("Price must be positive")

    with connect() as connection:

        connection.execute("BEGIN IMMEDIATE")

        # Reset daily statistics before recording today's close.
        _ensure_daily_stats_current(
            connection,
            mode
        )

        position = connection.execute("""
            SELECT *
            FROM positions
            WHERE mode = ? AND token_address = ?
        """, (mode, token_address)).fetchone()

        if not position:
            return "Position not found"

        account = connection.execute("""
            SELECT *
            FROM account_state
            WHERE mode = ?
        """, (mode,)).fetchone()

        if not account:
            raise ValueError("Account not initialized")

        entry_price = float(position["entry_price"])
        invested = float(position["invested"])
        strategy = position["strategy"]

        gross_return = invested * (
            current_price / entry_price
        )

        try:
            position_metadata = json.loads(
                position["metadata"] or "{}"
            )
        except (ValueError, TypeError):
            position_metadata = {}

        entry_fee_rate = validate_number(
            position_metadata.get("paper_fee_rate", 0),
            "paper_fee_rate"
        )
        exit_fee = gross_return * PAPER_FEE_RATE
        entry_fee = invested * entry_fee_rate
        returned_amount = gross_return - exit_fee

        # Report net P/L after estimated entry and exit costs.
        profit_loss = round(
            returned_amount - invested - entry_fee,
            2
        )

        new_balance = (
            float(account["balance"])
            + returned_amount
        )

        new_daily_loss = (
            float(account["daily_loss"])
            + max(0, -profit_loss)
        )

        new_daily_pnl = (
            float(account["daily_realized_pnl"])
            + profit_loss
        )

        connection.execute("""
            DELETE FROM positions
            WHERE mode = ? AND token_address = ?
        """, (
            mode,
            token_address
        ))

        connection.execute("""
            UPDATE account_state
            SET balance = ?,
                daily_loss = ?,
                daily_realized_pnl = ?,
                daily_date = ?,
                updated_at = ?
            WHERE mode = ?
        """, (
            new_balance,
            new_daily_loss,
            new_daily_pnl,
            today(),
            now(),
            mode
        ))

        connection.execute("""
            INSERT INTO trades
            (
                token_address,
                action,
                price,
                amount,
                profit_loss,
                timestamp,
                mode,
                strategy,
                copy_wallet
            )
            VALUES (?, 'SELL', ?, ?, ?, ?, ?, ?, ?)
        """, (
            token_address, current_price, returned_amount, profit_loss,
            now(), mode, strategy, position_metadata.get("copy_wallet")
        ))

    return {
        "result": reason,
        "profit_loss": profit_loss,
        "balance": round(new_balance, 2),
        "strategy": strategy,
        "invested": invested,
        "returned_amount": round(returned_amount, 2)
    }


# ==========================================
# POSITIONS
# ==========================================

def save_position(
    mode,
    token_address,
    entry_price,
    invested,
    strategy="main",
    metadata=None
):

    validate_mode(mode)
    validate_strategy(strategy)

    entry_price = validate_number(
        entry_price,
        "entry_price"
    )

    invested = validate_number(
        invested,
        "invested"
    )

    if entry_price <= 0 or invested <= 0:
        raise ValueError("Invalid position values")

    with connect() as connection:

        connection.execute("""
            INSERT INTO positions
            (
                mode,
                token_address,
                entry_price,
                invested,
                strategy,
                opened_at,
                metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(mode, token_address) DO UPDATE SET
                entry_price = excluded.entry_price,
                invested = excluded.invested,
                strategy = excluded.strategy,
                metadata = excluded.metadata
        """, (
            mode,
            token_address,
            entry_price,
            invested,
            strategy,
            now(),
            json.dumps(metadata or {})
        ))


def delete_position(mode, token_address):

    validate_mode(mode)

    with connect() as connection:

        connection.execute("""
            DELETE FROM positions
            WHERE mode = ? AND token_address = ?
        """, (
            mode,
            token_address
        ))


def get_positions(mode):

    validate_mode(mode)

    with connect() as connection:

        rows = connection.execute("""
            SELECT *
            FROM positions
            WHERE mode = ?
        """, (mode,)).fetchall()

    positions = {}

    for row in rows:

        position = dict(row)

        try:
            metadata = json.loads(
                position.get("metadata") or "{}"
            )
        except (ValueError, TypeError):
            metadata = {}

        positions[position["token_address"]] = {
            "entry_price": position["entry_price"],
            "invested": position["invested"],
            "strategy": position["strategy"],
            "opened_at": position["opened_at"],
            "metadata": metadata
        }

    return positions



def update_position_metadata(mode, token_address, metadata):
    """Persist strategy state such as a trailing-stop high-water price."""
    validate_mode(mode)
    if not token_address or not isinstance(metadata, dict):
        return False

    with connect() as connection:
        cursor = connection.execute("""
            UPDATE positions
            SET metadata = ?
            WHERE mode = ? AND token_address = ?
        """, (
            json.dumps(metadata),
            mode,
            token_address
        ))
        return cursor.rowcount > 0


# ==========================================
# TRADES
# ==========================================

def save_trade(
    token,
    action,
    price,
    amount,
    profit_loss=0,
    mode="paper",
    strategy="main",
    copy_wallet=None
):

    validate_mode(mode)
    validate_strategy(strategy)

    price = validate_number(price, "price")
    amount = validate_number(amount, "amount")

    profit_loss = validate_number(
        profit_loss,
        "profit_loss"
    )

    with connect() as connection:

        connection.execute("""
            INSERT INTO trades
            (
                token_address,
                action,
                price,
                amount,
                profit_loss,
                timestamp,
                mode,
                strategy,
                copy_wallet
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            token, action, price, amount, profit_loss, now(),
            mode, strategy, copy_wallet
        ))


def get_trade_stats(mode="paper", strategy=None):

    validate_mode(mode)

    if strategy is not None:
        validate_strategy(strategy)

    with connect() as connection:

        if strategy:

            result = connection.execute("""
                SELECT
                    COUNT(*) AS total,
                    SUM(
                        CASE WHEN profit_loss > 0
                        THEN 1 ELSE 0 END
                    ) AS wins,
                    SUM(
                        CASE WHEN profit_loss < 0
                        THEN 1 ELSE 0 END
                    ) AS losses,
                    SUM(profit_loss) AS total_profit
                FROM trades
                WHERE action = 'SELL'
                AND mode = ?
                AND strategy = ?
            """, (
                mode,
                strategy
            )).fetchone()

        else:

            result = connection.execute("""
                SELECT
                    COUNT(*) AS total,
                    SUM(
                        CASE WHEN profit_loss > 0
                        THEN 1 ELSE 0 END
                    ) AS wins,
                    SUM(
                        CASE WHEN profit_loss < 0
                        THEN 1 ELSE 0 END
                    ) AS losses,
                    SUM(profit_loss) AS total_profit
                FROM trades
                WHERE action = 'SELL'
                AND mode = ?
            """, (mode,)).fetchone()

    total = result["total"] or 0
    wins = result["wins"] or 0
    losses = result["losses"] or 0

    return {
        "total_trades": total,
        "winning_trades": wins,
        "losing_trades": losses,
        "win_rate": (
            round((wins / total) * 100, 2)
            if total else 0
        ),
        "total_profit": round(
            result["total_profit"] or 0,
            2
        )
    }


def get_recent_trades(mode="paper", limit=20):

    validate_mode(mode)

    limit = max(1, min(int(limit), 500))

    with connect() as connection:

        rows = connection.execute("""
            SELECT *
            FROM trades
            WHERE mode = ?
            ORDER BY id DESC
            LIMIT ?
        """, (
            mode,
            limit
        )).fetchall()

    return [dict(row) for row in rows]


def get_period_realized_loss(mode="paper", window_hours=24):
    """Return absolute realized losses from closed SELL rows in a rolling window."""
    validate_mode(mode)
    if isinstance(window_hours, bool):
        raise ValueError("Loss window must be positive")
    try:
        hours = float(window_hours)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Loss window must be positive")
    if not math.isfinite(hours) or hours <= 0 or hours > 24 * 31:
        raise ValueError("Loss window must be between 0 and 744 hours")
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with connect() as connection:
        row = connection.execute("""
            SELECT COALESCE(SUM(CASE WHEN profit_loss < 0
                THEN -profit_loss ELSE 0 END), 0) AS realized_loss
            FROM trades
            WHERE mode = ? AND action = 'SELL' AND timestamp >= ?
        """, (mode, since)).fetchone()
    return max(0.0, float(row["realized_loss"] or 0.0))


def get_last_exit_timestamp(token_address, mode="paper", strategy=None):
    """Return the latest closed SELL timestamp for one token, mode and optional strategy."""
    validate_mode(mode)
    if strategy is not None:
        validate_strategy(strategy)
    if not isinstance(token_address, str) or not token_address.strip():
        raise ValueError("Token address is required")
    query = """
        SELECT timestamp
        FROM trades
        WHERE token_address = ? AND mode = ? AND action = 'SELL'
    """
    params = [token_address.strip(), mode]
    if strategy is not None:
        query += " AND strategy = ?"
        params.append(strategy)
    query += " ORDER BY timestamp DESC, id DESC LIMIT 1"
    with connect() as connection:
        row = connection.execute(query, params).fetchone()
    return row["timestamp"] if row else None


# ==========================================
# ANALYSES
# ==========================================

def save_analysis(
    token,
    score,
    decision,
    mode="paper"
):

    validate_mode(mode)

    with connect() as connection:

        connection.execute("""
            INSERT INTO analyses
            (
                token_address,
                score,
                decision,
                timestamp,
                mode
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            token,
            score,
            str(decision),
            now(),
            mode
        ))


# ==========================================
# PENDING TOKENS
# ==========================================

def save_pending_token(
    mode,
    token_address,
    strategy,
    first_seen,
    token_data
):

    validate_mode(mode)
    validate_strategy(strategy)

    with connect() as connection:

        connection.execute("""
            INSERT INTO pending_tokens
            (
                mode,
                token_address,
                strategy,
                first_seen,
                token_data
            )
            VALUES (?, ?, ?, ?, ?)

            ON CONFLICT(mode, token_address) DO UPDATE SET
                strategy = excluded.strategy,
                first_seen = excluded.first_seen,
                token_data = excluded.token_data
        """, (
            mode,
            token_address,
            strategy,
            first_seen,
            json.dumps(token_data or {})
        ))


def delete_pending_token(mode, token_address):

    validate_mode(mode)

    with connect() as connection:

        connection.execute("""
            DELETE FROM pending_tokens
            WHERE mode = ? AND token_address = ?
        """, (
            mode,
            token_address
        ))


def get_pending_tokens(mode):

    validate_mode(mode)

    with connect() as connection:

        rows = connection.execute("""
            SELECT *
            FROM pending_tokens
            WHERE mode = ?
        """, (mode,)).fetchall()

    result = []

    for row in rows:

        item = dict(row)

        try:
            item["token_data"] = json.loads(
                item.get("token_data") or "{}"
            )
        except (ValueError, TypeError):
            item["token_data"] = {}

        result.append(item)

    return result


# ==========================================
# BOT EVENT LOG
# ==========================================

def log_event(
    event_type,
    message,
    token_address=None
):

    with connect() as connection:

        connection.execute("""
            INSERT INTO bot_events
            (
                event_type,
                message,
                token_address,
                timestamp
            )
            VALUES (?, ?, ?, ?)
        """, (
            str(event_type),
            str(message),
            token_address,
            now()
        ))


def get_recent_events(limit=50):

    limit = max(1, min(int(limit), 500))

    with connect() as connection:

        rows = connection.execute("""
            SELECT *
            FROM bot_events
            ORDER BY id DESC
            LIMIT ?
        """, (limit,)).fetchall()

    return [dict(row) for row in rows]


# ==========================================
# TOKEN FILTERING AUDIT + ANALYTICS
# ==========================================

def record_filter_decision(
    token_address,
    stage,
    decision,
    reason=None,
    score=None,
    liquidity=None,
    source_count=0,
    metadata=None
):
    """Persist a transparent, queryable result for every filter stage."""
    if not isinstance(token_address, str) or not token_address.strip():
        return False
    if not isinstance(stage, str) or not stage.strip():
        return False
    if not isinstance(decision, str) or not decision.strip():
        return False
    score_value = None if score is None else validate_number(score, "score")
    liquidity_value = None if liquidity is None else validate_number(liquidity, "liquidity")
    try:
        source_count_value = max(0, int(source_count))
    except (TypeError, ValueError, OverflowError):
        source_count_value = 0
    with connect() as connection:
        connection.execute("""
            INSERT INTO token_filter_events
            (token_address, stage, decision, reason, score, liquidity,
             source_count, metadata, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            token_address.strip(), stage.strip().upper(), decision.strip().upper(),
            str(reason or "")[:1000], score_value, liquidity_value,
            source_count_value, json.dumps(metadata or {}, default=str)[:8000], now()
        ))
    return True


def get_filtering_summary(limit=1000):
    limit = max(1, min(int(limit), 10000))
    with connect() as connection:
        totals = connection.execute("""
            SELECT decision, COUNT(*) AS count
            FROM token_filter_events
            WHERE id > (SELECT COALESCE(MAX(id), 0) - ? FROM token_filter_events)
            GROUP BY decision
        """, (limit,)).fetchall()
        reasons = connection.execute("""
            SELECT stage, decision, COALESCE(NULLIF(reason, ''), 'No reason') AS reason,
                   COUNT(*) AS count
            FROM token_filter_events
            WHERE id > (SELECT COALESCE(MAX(id), 0) - ? FROM token_filter_events)
            GROUP BY stage, decision, reason
            ORDER BY count DESC
            LIMIT 20
        """, (limit,)).fetchall()
        total = connection.execute("SELECT COUNT(*) FROM token_filter_events").fetchone()[0]
    return {
        "total_recorded": int(total or 0),
        "window_size": limit,
        "decisions": {row["decision"]: int(row["count"]) for row in totals},
        "top_reasons": [dict(row) for row in reasons]
    }


def get_recent_filter_events(limit=100):
    limit = max(1, min(int(limit), 500))
    with connect() as connection:
        rows = connection.execute("""
            SELECT id, token_address, stage, decision, reason, score,
                   liquidity, source_count, timestamp
            FROM token_filter_events
            ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
    return [dict(row) for row in rows]


# ==========================================
# MARKET CANDLES + STRATEGY CONFIG + COPY FEED
# ==========================================

MARKET_CANDLE_TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m", "1h", "4h")


def save_market_candles(pool_address, timeframe, candles):
    """Persist only normalized OHLCV rows; duplicate timestamps replace older values."""
    if (not isinstance(pool_address, str) or not pool_address.strip()
            or timeframe not in MARKET_CANDLE_TIMEFRAMES
            or not isinstance(candles, list)):
        return 0
    from ohlcv_integrity import normalize_ohlcv_rows
    rows, _quality = normalize_ohlcv_rows(candles)
    saved = 0
    with connect() as connection:
        for candle in rows:
            values = [candle[key] for key in ("open", "high", "low", "close", "volume")]
            connection.execute("""
                INSERT OR REPLACE INTO market_candles
                (pool_address, timeframe, open_time, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (pool_address.strip(), timeframe, candle["timestamp"], *values))
            saved += 1
    return saved


def prune_market_candles(retention_days=None):
    """Delete historical OHLCV older than the configured bounded retention period."""
    import time
    raw_days = (
        retention_days if retention_days is not None
        else os.getenv("TROJAN_OHLCV_RETENTION_DAYS", "30")
    )
    if isinstance(raw_days, bool):
        raise ValueError("OHLCV retention days must be an integer")
    try:
        days = int(raw_days)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("OHLCV retention days must be an integer") from error
    if not 1 <= days <= 3650:
        raise ValueError("OHLCV retention days must be between 1 and 3650")
    cutoff = int(time.time()) - days * 86400
    with connect() as connection:
        cursor = connection.execute(
            "DELETE FROM market_candles WHERE open_time < ?", (cutoff,)
        )
        return max(0, cursor.rowcount)


def get_market_candles(pool_address, timeframe="1m", limit=200):
    if (not isinstance(pool_address, str) or not pool_address.strip()
            or timeframe not in MARKET_CANDLE_TIMEFRAMES):
        return []
    if isinstance(limit, bool):
        limit = 200
    try:
        limit = int(limit)
    except (TypeError, ValueError, OverflowError):
        limit = 200
    # 30 days of 1m candles at the default retention period.
    limit = max(1, min(limit, 43200))
    with connect() as connection:
        rows = connection.execute("""
            SELECT open_time AS timestamp, open, high, low, close, volume
            FROM market_candles WHERE pool_address = ? AND timeframe = ?
            ORDER BY open_time DESC LIMIT ?
        """, (pool_address.strip(), timeframe, limit)).fetchall()
    return [dict(row) for row in reversed(rows)]


def save_strategy_config(strategy_name, config_json):
    if strategy_name not in ("main", "sniper"):
        raise ValueError("Invalid strategy name")
    encoded = json.dumps(config_json, separators=(",", ":"), sort_keys=True)
    if len(encoded) > 12000:
        raise ValueError("Strategy config is too large")
    with connect() as connection:
        connection.execute("""
            INSERT INTO strategy_config(strategy_name, config_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(strategy_name) DO UPDATE SET
                config_json=excluded.config_json, updated_at=excluded.updated_at
        """, (strategy_name, encoded, now()))
    return True


def delete_strategy_config(strategy_name):
    if strategy_name not in ("main", "sniper"):
        raise ValueError("Invalid strategy name")
    with connect() as connection:
        connection.execute("DELETE FROM strategy_config WHERE strategy_name = ?", (strategy_name,))
    return True


def get_strategy_configs():
    with connect() as connection:
        rows = connection.execute(
            "SELECT strategy_name, config_json, updated_at FROM strategy_config"
        ).fetchall()
    result = {}
    for row in rows:
        try:
            result[row["strategy_name"]] = {
                "config": json.loads(row["config_json"]),
                "updated_at": row["updated_at"]
            }
        except (ValueError, TypeError):
            continue
    return result


def list_copy_watch_wallets():
    with connect() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS copy_wallet_watch (
                wallet TEXT PRIMARY KEY,
                label TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        rows = connection.execute("""
            SELECT wallet, label, enabled, created_at, updated_at
            FROM copy_wallet_watch
            ORDER BY created_at, wallet
        """).fetchall()
    return [{**dict(row), "enabled": bool(row["enabled"])} for row in rows]


def upsert_copy_watch_wallet(wallet, label=None, enabled=True):
    if not isinstance(wallet, str) or not wallet.strip() or len(wallet.strip()) > 64:
        raise ValueError("wallet address is invalid")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    clean_label = str(label or "").strip()[:80] or None
    address = wallet.strip()
    timestamp = now()
    with connect() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS copy_wallet_watch (
                wallet TEXT PRIMARY KEY,
                label TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        connection.execute("""
            INSERT INTO copy_wallet_watch (wallet, label, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(wallet) DO UPDATE SET
                label=excluded.label,
                enabled=excluded.enabled,
                updated_at=excluded.updated_at
        """, (address, clean_label, 1 if enabled else 0, timestamp, timestamp))
    return next((item for item in list_copy_watch_wallets() if item["wallet"] == address), None)


def delete_copy_watch_wallet(wallet):
    if not isinstance(wallet, str) or not wallet.strip():
        return False
    with connect() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS copy_wallet_watch (
                wallet TEXT PRIMARY KEY,
                label TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        connection.execute("DELETE FROM copy_wallet_watch WHERE wallet = ?", (wallet.strip(),))
        return connection.total_changes > 0


def record_copy_trade_event(wallet, signature, action=None, token_mint=None,
                            token_delta=None, sol_delta=None, raw_summary=None,
                            asset_deltas=None):
    if not wallet or not signature:
        return False
    if not isinstance(wallet, str) or not isinstance(signature, str):
        raise ValueError("copy event wallet and signature must be strings")
    if asset_deltas is None:
        asset_deltas = []
    if not isinstance(asset_deltas, (list, tuple)):
        raise ValueError("asset_deltas must be a list")
    normalized_deltas = [
        _normalize_subject_asset_delta(item, wallet.strip(), signature.strip())
        for item in asset_deltas
    ]
    with connect() as connection:
        connection.execute("""
            INSERT OR IGNORE INTO copy_trade_events
            (wallet, signature, action, token_mint, token_delta, sol_delta, raw_summary, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (wallet.strip(), signature.strip(), action, token_mint,
              token_delta, sol_delta, str(raw_summary or "")[:1500], now()))
        event_inserted = connection.total_changes > 0
        if normalized_deltas:
            connection.executemany("""
                INSERT OR IGNORE INTO subject_asset_deltas (
                    chain, wallet_address, signature, mint, slot, block_time,
                    tx_index, delta_raw, before_raw, after_raw, decimals, kind,
                    venue, fee_native_raw, success, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [row + (now(),) for row in normalized_deltas])
        return event_inserted


def _normalize_subject_asset_delta(item, wallet, signature):
    if hasattr(item, "as_dict") and callable(item.as_dict):
        item = item.as_dict()
    if not isinstance(item, dict):
        raise ValueError("subject asset delta must be an object")

    def integer(value, label, minimum, maximum, optional=False):
        if optional and value is None:
            return None
        if isinstance(value, bool):
            raise ValueError(label + " must be an integer")
        try:
            result = int(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(label + " must be an integer") from error
        if isinstance(value, float) and value != result:
            raise ValueError(label + " must be an exact integer")
        if result < minimum or result > maximum:
            raise ValueError(label + " is outside the allowed range")
        return result

    chain = item.get("chain")
    address = item.get("wallet_address")
    row_signature = item.get("signature")
    mint = item.get("mint")
    kind = item.get("kind")
    venue = item.get("venue")
    success = item.get("success")
    if not isinstance(chain, str) or not chain or len(chain) > 32:
        raise ValueError("asset delta chain is invalid")
    if address != wallet or row_signature != signature:
        raise ValueError("asset delta subject or signature does not match its event")
    if not isinstance(mint, str) or not mint or len(mint) > 128:
        raise ValueError("asset delta mint is invalid")
    if hasattr(kind, "value"):
        kind = kind.value
    if kind not in ("trade", "transfer", "defi", "other"):
        raise ValueError("asset delta kind is invalid")
    if venue is not None and (not isinstance(venue, str) or len(venue) > 128):
        raise ValueError("asset delta venue is invalid")
    if not isinstance(success, bool):
        raise ValueError("asset delta success must be boolean")
    return (
        chain, address, row_signature, mint,
        integer(item.get("slot"), "asset delta slot", 1, (1 << 64) - 1, True),
        integer(item.get("block_time"), "asset delta block time", 0, (1 << 63) - 1, True),
        integer(item.get("tx_index"), "asset delta tx index", 0, (1 << 32) - 1),
        str(integer(item.get("delta_raw"), "asset delta amount", -(1 << 127), (1 << 127) - 1)),
        None if item.get("before_raw") is None else str(integer(item.get("before_raw"), "asset delta before", 0, (1 << 128) - 1)),
        None if item.get("after_raw") is None else str(integer(item.get("after_raw"), "asset delta after", 0, (1 << 128) - 1)),
        integer(item.get("decimals"), "asset delta decimals", 0, 18),
        kind, venue,
        None if item.get("fee_native_raw") is None else str(integer(item.get("fee_native_raw"), "asset delta fee", 0, (1 << 128) - 1)),
        1 if success else 0,
    )


def get_subject_asset_deltas(wallet, limit=100):
    if not isinstance(wallet, str) or not wallet.strip():
        raise ValueError("wallet address is required")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    with connect() as connection:
        rows = connection.execute("""
            SELECT * FROM subject_asset_deltas
            WHERE wallet_address = ?
            ORDER BY COALESCE(block_time, 0) DESC, tx_index DESC
            LIMIT ?
        """, (wallet.strip(), limit)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["delta_raw"] = int(item["delta_raw"])
        for field in ("before_raw", "after_raw", "fee_native_raw"):
            item[field] = int(item[field]) if item[field] is not None else None
        item["success"] = bool(item["success"])
        result.append(item)
    return result


def get_recent_copy_trade_events(limit=50):
    limit = max(1, min(int(limit), 250))
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM copy_trade_events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_copy_watch_cursor(wallet):
    """Return the last fully processed signature for a watched wallet."""
    if not isinstance(wallet, str) or not wallet.strip():
        raise ValueError("wallet address is required")
    with connect() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS copy_wallet_watch_cursors (
                wallet TEXT PRIMARY KEY,
                signature TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        row = connection.execute(
            "SELECT signature FROM copy_wallet_watch_cursors WHERE wallet = ?",
            (wallet.strip(),)
        ).fetchone()
    return row["signature"] if row else None


def set_copy_watch_cursor(wallet, signature):
    """Persist a cursor only after the complete older-to-newer range was processed."""
    if not isinstance(wallet, str) or not wallet.strip():
        raise ValueError("wallet address is required")
    if not isinstance(signature, str) or not signature.strip() or len(signature) > 128:
        raise ValueError("signature cursor is invalid")
    address = wallet.strip()
    with connect() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS copy_wallet_watch_cursors (
                wallet TEXT PRIMARY KEY,
                signature TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        connection.execute("""
            INSERT INTO copy_wallet_watch_cursors (wallet, signature, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(wallet) DO UPDATE SET
                signature=excluded.signature,
                updated_at=excluded.updated_at
        """, (address, signature.strip(), now()))
    return signature.strip()


# ==========================================
# HEALTH CHECK
# ==========================================

def list_database_backups(backup_dir=None):
    directory = backup_dir or os.getenv("TROJAN_BACKUP_DIR") or os.path.join(DATA_DIR, "backups")
    if not os.path.isdir(directory):
        return []
    backups = []
    for name in os.listdir(directory):
        if not name.endswith(".sqlite3"):
            continue
        path = os.path.join(directory, name)
        try:
            stat = os.stat(path)
            if not os.path.isfile(path):
                continue
            backups.append({
                "name": name,
                "size_bytes": int(stat.st_size),
                "created_at": datetime.fromtimestamp(
                    stat.st_mtime, timezone.utc
                ).isoformat()
            })
        except OSError:
            continue
    return sorted(backups, key=lambda item: item["created_at"], reverse=True)


def backup_database(backup_dir=None, keep=7):
    """Create a consistent SQLite online backup and prune old verified snapshots."""
    if isinstance(keep, bool) or not isinstance(keep, int) or not 1 <= keep <= 90:
        raise ValueError("keep must be an integer between 1 and 90")
    if not os.path.isfile(DATABASE):
        raise FileNotFoundError("Database file does not exist")

    directory = backup_dir or os.getenv("TROJAN_BACKUP_DIR") or os.path.join(DATA_DIR, "backups")
    os.makedirs(directory, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = os.path.join(directory, "trojan-" + stamp + ".sqlite3")
    temporary = destination + ".tmp"

    source = None
    target = None
    try:
        source = sqlite3.connect(DATABASE, timeout=30)
        target = sqlite3.connect(temporary, timeout=30)
        source.backup(target, pages=256, sleep=0.01)
        check = target.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise sqlite3.DatabaseError("Backup integrity check failed")
        target.commit()
    except Exception:
        if target is not None:
            target.close()
            target = None
        if source is not None:
            source.close()
            source = None
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()

    os.replace(temporary, destination)
    backups = list_database_backups(directory)
    for obsolete in backups[keep:]:
        try:
            os.remove(os.path.join(directory, obsolete["name"]))
        except OSError:
            pass
    return {
        "created": True,
        "backup": os.path.basename(destination),
        "size_bytes": os.path.getsize(destination),
        "integrity": "ok",
        "retained": len(list_database_backups(directory)),
    }



def restore_database(backup_name, backup_dir=None):
    """Restore a verified snapshot while the application is in offline maintenance mode.

    This deliberately is not exposed as a normal dashboard action: replacing a
    SQLite database while application workers are writing to it is unsafe.
    Stop all workers, set TROJAN_RESTORE_MAINTENANCE_MODE=true, then invoke this
    operation from a one-shot maintenance process.
    """
    if os.getenv("TROJAN_RESTORE_MAINTENANCE_MODE", "").strip().lower() not in ("1", "true", "yes"):
        raise RuntimeError("Database restore requires offline maintenance mode")
    if not isinstance(backup_name, str) or not backup_name or os.path.basename(backup_name) != backup_name:
        raise ValueError("A backup filename, not a path, is required")
    if not backup_name.endswith(".sqlite3"):
        raise ValueError("Only .sqlite3 backup files can be restored")

    directory = backup_dir or os.getenv("TROJAN_BACKUP_DIR") or os.path.join(DATA_DIR, "backups")
    source_path = os.path.realpath(os.path.join(directory, backup_name))
    if os.path.commonpath((os.path.realpath(directory), source_path)) != os.path.realpath(directory):
        raise ValueError("Backup path is outside the configured backup directory")
    if not os.path.isfile(source_path) or os.path.getsize(source_path) <= 0:
        raise FileNotFoundError("Selected database backup does not exist or is empty")

    # Verify the candidate before touching the active database.
    candidate = sqlite3.connect(source_path, timeout=30)
    try:
        integrity = candidate.execute("PRAGMA integrity_check").fetchone()
        version = candidate.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        if not integrity or integrity[0] != "ok":
            raise sqlite3.DatabaseError("Selected backup failed SQLite integrity_check")
        # Schema 10 is the immediately previous compatible layout. Its tables
        # are migrated by initialize_database after the verified restore.
        if not version or int(version[0]) not in (SCHEMA_VERSION - 1, SCHEMA_VERSION):
            raise sqlite3.DatabaseError("Selected backup schema version is incompatible")
    finally:
        candidate.close()

    # Create an emergency snapshot of the current database before replacement.
    emergency = backup_database(directory, keep=90)
    destination = DATABASE
    temporary = destination + ".restore.tmp"
    source = None
    target = None
    try:
        source = sqlite3.connect(source_path, timeout=30)
        target = sqlite3.connect(temporary, timeout=30)
        source.backup(target, pages=256, sleep=0.01)
        target.commit()
        restored_integrity = target.execute("PRAGMA integrity_check").fetchone()
        if not restored_integrity or restored_integrity[0] != "ok":
            raise sqlite3.DatabaseError("Restored temporary database failed integrity_check")
    except Exception:
        if target is not None:
            target.close()
            target = None
        if source is not None:
            source.close()
            source = None
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()

    # This replacement must run only after every application worker has stopped.
    for suffix in ("-wal", "-shm"):
        sidecar = destination + suffix
        if os.path.exists(sidecar):
            os.remove(sidecar)
    os.replace(temporary, destination)
    # Apply additive schema migrations while still in offline maintenance mode.
    initialize_database()
    return {
        "restored": True,
        "backup": backup_name,
        "integrity": "ok",
        "schema_version": SCHEMA_VERSION,
        "emergency_backup": emergency["backup"],
        "requires_application_restart": True,
    }


def get_database_health():

    with connect() as connection:

        integrity = connection.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        tables = connection.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            ORDER BY name
        """).fetchall()

        version = connection.execute("""
            SELECT value
            FROM schema_meta
            WHERE key = 'schema_version'
        """).fetchone()
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    backup_dir = os.getenv("TROJAN_BACKUP_DIR") or os.path.join(DATA_DIR, "backups")
    backups = list_database_backups(backup_dir)
    storage_backend = (
        "railway_volume" if os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
        else "custom_data_dir" if os.getenv("BOT_DATA_DIR")
        else "ephemeral_default"
    )
    return {
        "database": DATABASE,
        "integrity": integrity,
        "journal_mode": str(journal_mode).lower(),
        "database_size_bytes": os.path.getsize(DATABASE) if os.path.isfile(DATABASE) else 0,
        "wal_size_bytes": os.path.getsize(DATABASE + "-wal") if os.path.isfile(DATABASE + "-wal") else 0,
        "storage_backend": storage_backend,
        "persistent_storage_configured": storage_backend != "ephemeral_default",
        "backup_directory": backup_dir,
        "backup_count": len(backups),
        "latest_backup": backups[0] if backups else None,
        "schema_version": (
            int(version["value"])
            if version else None
        ),
        "tables": [
            row["name"]
            for row in tables
        ]
    }


# ==========================================
# TROJAN V2 DATABASE TEST
# ==========================================

if __name__ == "__main__":

    initialize_database()

    initialize_account("paper", 100.00)
    initialize_account("live", 0.00)

    print("PAPER ACCOUNT:")
    print(get_account_summary("paper"))

    print("LIVE ACCOUNT:")
    print(get_account_summary("live"))

    print("PAPER TRADE STATS:")
    print(get_trade_stats("paper"))

    print("MAIN STATS:")
    print(get_trade_stats("paper", "main"))

    print("SNIPER STATS:")
    print(get_trade_stats("paper", "sniper"))

    print("DATABASE HEALTH:")
    print(get_database_health())

    print("TROJAN V2 DATABASE TEST COMPLETED")



# ==========================================
# COPY WALLET PERFORMANCE + PROTOCOL VALIDATION
# ==========================================

def get_copy_wallet_performance():
    with connect() as connection:
        trade_rows = connection.execute("""
            SELECT copy_wallet AS wallet,
                   SUM(CASE WHEN action='SELL' THEN 1 ELSE 0 END) AS closed_trades,
                   SUM(CASE WHEN action='SELL' AND profit_loss > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN action='SELL' AND profit_loss <= 0 THEN 1 ELSE 0 END) AS losses,
                   COALESCE(SUM(CASE WHEN action='SELL' THEN profit_loss ELSE 0 END), 0) AS realized_pnl,
                   COALESCE(SUM(CASE WHEN action='BUY' THEN amount ELSE 0 END), 0) AS gross_buys,
                   COALESCE(SUM(CASE WHEN action='SELL' AND date(timestamp)=? THEN profit_loss ELSE 0 END), 0) AS daily_realized_pnl,
                   COALESCE(SUM(CASE WHEN action='SELL' AND date(timestamp)=? AND profit_loss < 0 THEN -profit_loss ELSE 0 END), 0) AS daily_loss
            FROM trades
            WHERE mode='paper' AND copy_wallet IS NOT NULL AND copy_wallet <> ''
            GROUP BY copy_wallet
        """, (today(), today())).fetchall()
        event_rows = connection.execute("""
            SELECT wallet,
                   COUNT(*) AS observed_events,
                   SUM(CASE WHEN action='BUY' THEN 1 ELSE 0 END) AS buys,
                   SUM(CASE WHEN action='SELL' THEN 1 ELSE 0 END) AS sells
            FROM copy_trade_events
            GROUP BY wallet
        """).fetchall()
        position_rows = connection.execute("""
            SELECT metadata, invested FROM positions WHERE mode='paper'
        """).fetchall()

    result = {}
    for row in trade_rows:
        wallet = row["wallet"]
        closed = int(row["closed_trades"] or 0)
        wins = int(row["wins"] or 0)
        result[wallet] = {
            "wallet": wallet, "closed_trades": closed, "wins": wins,
            "losses": int(row["losses"] or 0),
            "win_rate": round(wins / closed * 100, 2) if closed else 0.0,
            "realized_pnl": round(float(row["realized_pnl"] or 0), 4),
            "daily_realized_pnl": round(float(row["daily_realized_pnl"] or 0), 4),
            "daily_loss": round(float(row["daily_loss"] or 0), 4),
            "gross_buys": round(float(row["gross_buys"] or 0), 4),
            "open_positions": 0, "open_exposure_usd": 0.0,
            "observed_events": 0, "observed_buys": 0, "observed_sells": 0,
        }
    for row in event_rows:
        item = result.setdefault(row["wallet"], {
            "wallet": row["wallet"], "closed_trades": 0, "wins": 0,
            "losses": 0, "win_rate": 0.0, "realized_pnl": 0.0,
            "gross_buys": 0.0, "daily_realized_pnl": 0.0,
            "daily_loss": 0.0, "open_positions": 0,
            "open_exposure_usd": 0.0, "observed_events": 0,
            "observed_buys": 0, "observed_sells": 0,
        })
        item["observed_events"] = int(row["observed_events"] or 0)
        item["observed_buys"] = int(row["buys"] or 0)
        item["observed_sells"] = int(row["sells"] or 0)
    for row in position_rows:
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except (ValueError, TypeError):
            metadata = {}
        wallet = metadata.get("copy_wallet") if isinstance(metadata, dict) else None
        if not wallet:
            continue
        item = result.setdefault(wallet, {
            "wallet": wallet, "closed_trades": 0, "wins": 0, "losses": 0,
            "win_rate": 0.0, "realized_pnl": 0.0, "gross_buys": 0.0,
            "open_positions": 0, "open_exposure_usd": 0.0,
            "observed_events": 0, "observed_buys": 0, "observed_sells": 0,
        })
        item["open_positions"] += 1
        item["open_exposure_usd"] += float(row["invested"] or 0)
    for item in result.values():
        item["open_exposure_usd"] = round(item["open_exposure_usd"], 4)
    return sorted(result.values(), key=lambda item: item["realized_pnl"], reverse=True)


def record_protocol_validation(protocol, pool_address, token_mint, success,
                               reason=None, observed_at=None):
    with connect() as connection:
        connection.execute("""
            INSERT INTO protocol_validation_events
            (protocol, pool_address, token_mint, success, reason, observed_at, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            str(protocol or "unknown")[:64],
            str(pool_address or "")[:128],
            str(token_mint or "")[:128],
            1 if success else 0,
            str(reason or "")[:220] or None,
            float(observed_at) if observed_at is not None else None,
            now()
        ))


def get_protocol_validation_summary():
    with connect() as connection:
        rows = connection.execute("""
            SELECT protocol, COUNT(*) AS attempts,
                   SUM(success) AS successes,
                   SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failures,
                   MAX(timestamp) AS last_seen,
                   MAX(CASE WHEN success=0 THEN reason ELSE NULL END) AS last_error
            FROM protocol_validation_events
            GROUP BY protocol
            ORDER BY protocol
        """).fetchall()
    by_protocol = {row["protocol"]: {
        "protocol": row["protocol"],
        "attempts": int(row["attempts"] or 0),
        "successes": int(row["successes"] or 0),
        "failures": int(row["failures"] or 0),
        "success_rate": round(
            float(row["successes"] or 0) / int(row["attempts"] or 1) * 100, 2
        ),
        "last_seen": row["last_seen"],
        "last_error": row["last_error"],
    } for row in rows}
    supported = [
        "pumpswap", "pumpfun_legacy", "raydium_amm_v4", "raydium_cpmm",
        "raydium_clmm", "orca_whirlpool", "meteora_dlmm", "meteora_damm_v2",
        "meteora_dbc", "fluxbeam_amm", "moonit_amm",
    ]
    return {
        "supported_protocols": supported,
        "validated_protocols": sum(
            1 for protocol in supported
            if by_protocol.get(protocol, {}).get("successes", 0) > 0
        ),
        "protocols": [
            by_protocol.get(protocol, {
                "protocol": protocol, "attempts": 0, "successes": 0,
                "failures": 0, "success_rate": 0.0,
                "last_seen": None, "last_error": None
            }) for protocol in supported
        ],
        "unclassified": by_protocol.get("unknown"),
        "commitment": "confirmed",
    }


# ==========================================
# PERSISTENT MULTI-SOURCE POOL REGISTRY
# ==========================================

def save_pool_registry(mint, pools, updated_at=None):
    if not isinstance(mint, str) or not mint.strip():
        raise ValueError("mint is required")
    if not isinstance(pools, list):
        raise ValueError("pools must be a list")
    payload = json.dumps(
        pools, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    timestamp = float(updated_at) if updated_at is not None else time.time()
    with connect() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS pool_registry (
                mint TEXT PRIMARY KEY,
                pools_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        connection.execute("""
            INSERT OR REPLACE INTO pool_registry (mint, pools_json, updated_at)
            VALUES (?, ?, ?)
        """, (mint.strip(), payload, timestamp))
    return True


def load_pool_registry(mint):
    if not isinstance(mint, str) or not mint.strip():
        return None
    with connect() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS pool_registry (
                mint TEXT PRIMARY KEY,
                pools_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        row = connection.execute("""
            SELECT pools_json, updated_at
            FROM pool_registry WHERE mint = ?
        """, (mint.strip(),)).fetchone()
    if not row:
        return None
    try:
        pools = json.loads(row["pools_json"])
    except (ValueError, TypeError):
        return None
    if not isinstance(pools, list):
        return None
    return {"pools": pools, "updated_at": float(row["updated_at"] or 0)}


def delete_pool_registry(mint=None):
    with connect() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS pool_registry (
                mint TEXT PRIMARY KEY,
                pools_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        if mint is None:
            connection.execute("DELETE FROM pool_registry")
        else:
            connection.execute("DELETE FROM pool_registry WHERE mint = ?", (str(mint),))
    return True


# ==========================================
# PERSISTENT TOKEN / POOL / ACCOUNT BLACKLIST
# ==========================================

BLACKLIST_TYPES = ("token", "pool", "account")


def upsert_blacklist_entry(entry_type, entry_value, reason, source="manual", token_mint=None):
    if entry_type not in BLACKLIST_TYPES:
        raise ValueError("entry_type must be token, pool or account")
    if not isinstance(entry_value, str) or not entry_value.strip():
        raise ValueError("entry_value is required")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("A non-empty blacklist reason is required")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source is required")
    value = entry_value.strip()
    if len(value) > 128:
        raise ValueError("entry_value is too long")
    reason_value = reason.strip()[:500]
    source_value = source.strip()[:80]
    mint_value = token_mint.strip() if isinstance(token_mint, str) and token_mint.strip() else None
    timestamp = now()
    with connect() as connection:
        connection.execute("""
            INSERT INTO blacklist_entries
                (entry_type, entry_value, token_mint, reason, source, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(entry_type, entry_value) DO UPDATE SET
                token_mint=excluded.token_mint,
                reason=excluded.reason,
                source=excluded.source,
                active=1,
                updated_at=excluded.updated_at
        """, (entry_type, value, mint_value, reason_value, source_value, timestamp, timestamp))
        row = connection.execute("""
            SELECT * FROM blacklist_entries WHERE entry_type=? AND entry_value=?
        """, (entry_type, value)).fetchone()
    return dict(row)


def delete_blacklist_entry(entry_type, entry_value):
    if entry_type not in BLACKLIST_TYPES:
        raise ValueError("entry_type must be token, pool or account")
    if not isinstance(entry_value, str) or not entry_value.strip():
        raise ValueError("entry_value is required")
    with connect() as connection:
        cursor = connection.execute("""
            UPDATE blacklist_entries SET active=0, updated_at=?
            WHERE entry_type=? AND entry_value=? AND active=1
        """, (now(), entry_type, entry_value.strip()))
    return cursor.rowcount > 0


def list_blacklist_entries(entry_type=None, include_inactive=False, limit=500):
    if entry_type is not None and entry_type not in BLACKLIST_TYPES:
        raise ValueError("entry_type must be token, pool or account")
    limit = max(1, min(int(limit), 2000))
    clauses = []
    params = []
    if entry_type:
        clauses.append("entry_type=?")
        params.append(entry_type)
    if not include_inactive:
        clauses.append("active=1")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM blacklist_entries" + where +
            " ORDER BY active DESC, updated_at DESC LIMIT ?",
            tuple(params)
        ).fetchall()
    return [dict(row) for row in rows]


def match_blacklist_entries(entries):
    """Match a batch of (entry_type, entry_value) pairs against active entries."""
    normalized = []
    seen = set()
    for item in entries or []:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            continue
        entry_type, value = item
        if entry_type not in BLACKLIST_TYPES or not isinstance(value, str) or not value.strip():
            continue
        key = (entry_type, value.strip())
        if key not in seen:
            normalized.append(key)
            seen.add(key)
    if not normalized:
        return []
    clauses = " OR ".join("(entry_type=? AND entry_value=?)" for _ in normalized)
    params = [part for pair in normalized for part in pair]
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM blacklist_entries WHERE active=1 AND (" + clauses + ")",
            tuple(params)
        ).fetchall()
    return [dict(row) for row in rows]
