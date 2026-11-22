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
SCHEMA_VERSION = 3


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

        if balance < invested:
            return "Insufficient balance"

        existing = connection.execute("""
            SELECT token_address
            FROM positions
            WHERE mode = ? AND token_address = ?
        """, (mode, token_address)).fetchone()

        if existing:
            return "Position already exists"

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
            json.dumps(metadata or {})
        ))

        connection.execute("""
            UPDATE account_state
            SET balance = ?,
                updated_at = ?
            WHERE mode = ?
        """, (
            balance - invested,
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
                strategy
            )
            VALUES (?, 'BUY', ?, ?, 0, ?, ?, ?)
        """, (
            token_address,
            entry_price,
            invested,
            now(),
            mode,
            strategy
        ))

    return "POSITION OPENED"


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

        returned_amount = invested * (
            current_price / entry_price
        )

        profit_loss = round(
            returned_amount - invested,
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
                strategy
            )
            VALUES (?, 'SELL', ?, ?, ?, ?, ?, ?)
        """, (
            token_address,
            current_price,
            returned_amount,
            profit_loss,
            now(),
            mode,
            strategy
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
