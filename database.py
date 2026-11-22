import os
import sqlite3
import json
import math
from contextlib import contextmanager
from datetime import datetime, timezone


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
SCHEMA_VERSION = 5

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
    strategy="main"
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
                strategy
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            token,
            action,
            price,
            amount,
            profit_loss,
            now(),
            mode,
            strategy
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

def save_market_candles(pool_address, timeframe, candles):
    if not pool_address or not isinstance(candles, list):
        return 0
    saved = 0
    with connect() as connection:
        for candle in candles:
            try:
                values = [float(candle[key]) for key in ("open", "high", "low", "close", "volume")]
                timestamp = int(candle["timestamp"])
                if min(values[:4]) <= 0 or values[4] < 0:
                    continue
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            connection.execute("""
                INSERT OR REPLACE INTO market_candles
                (pool_address, timeframe, open_time, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (str(pool_address), str(timeframe), timestamp, *values))
            saved += 1
    return saved


def get_market_candles(pool_address, timeframe="1m", limit=200):
    limit = max(1, min(int(limit), 1000))
    with connect() as connection:
        rows = connection.execute("""
            SELECT open_time AS timestamp, open, high, low, close, volume
            FROM market_candles WHERE pool_address = ? AND timeframe = ?
            ORDER BY open_time DESC LIMIT ?
        """, (str(pool_address), str(timeframe), limit)).fetchall()
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


def record_copy_trade_event(wallet, signature, action=None, token_mint=None,
                            token_delta=None, sol_delta=None, raw_summary=None):
    if not wallet or not signature:
        return False
    with connect() as connection:
        connection.execute("""
            INSERT OR IGNORE INTO copy_trade_events
            (wallet, signature, action, token_mint, token_delta, sol_delta, raw_summary, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(wallet), str(signature), action, token_mint,
              token_delta, sol_delta, str(raw_summary or "")[:1500], now()))
        return connection.total_changes > 0


def get_recent_copy_trade_events(limit=50):
    limit = max(1, min(int(limit), 250))
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM copy_trade_events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


# ==========================================
# HEALTH CHECK
# ==========================================

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

    return {
        "database": DATABASE,
        "integrity": integrity,
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
