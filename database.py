import os
import sqlite3
import json
from datetime import datetime, timezone


# DATABASE LOCATION
# Railway Volume can later be mounted at /data.
DATA_DIR = (
    os.getenv("BOT_DATA_DIR")
    or os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    or "."
)

os.makedirs(DATA_DIR, exist_ok=True)

DATABASE = os.path.join(DATA_DIR, "trading_history.db")

VALID_MODES = ("paper", "live")


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_mode(mode):
    if mode not in VALID_MODES:
        raise ValueError("Invalid trading mode: " + str(mode))


def connect():
    connection = sqlite3.connect(
        DATABASE,
        timeout=30
    )

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


def add_column_if_missing(connection, table, column, definition):

    existing = [
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(" + table + ")"
        ).fetchall()
    ]

    if column not in existing:
        connection.execute(
            "ALTER TABLE " + table +
            " ADD COLUMN " + column + " " + definition
        )


def initialize_database():

    with connect() as connection:

        connection.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT,
                action TEXT,
                price REAL,
                amount REAL,
                profit_loss REAL DEFAULT 0,
                timestamp TEXT
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT,
                score INTEGER,
                decision TEXT,
                timestamp TEXT
            )
        """)

        # Migrate older database versions safely.
        add_column_if_missing(
            connection, "trades", "mode",
            "TEXT NOT NULL DEFAULT 'paper'"
        )

        add_column_if_missing(
            connection, "trades", "strategy",
            "TEXT NOT NULL DEFAULT 'main'"
        )

        add_column_if_missing(
            connection, "analyses", "mode",
            "TEXT NOT NULL DEFAULT 'paper'"
        )

        connection.execute("""
            CREATE TABLE IF NOT EXISTS account_state (
                mode TEXT PRIMARY KEY,
                balance REAL NOT NULL,
                daily_loss REAL NOT NULL DEFAULT 0,
                daily_realized_pnl REAL NOT NULL DEFAULT 0,
                updated_at TEXT
            )
        """)

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
            CREATE INDEX IF NOT EXISTS idx_trades_mode
            ON trades(mode)
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_analyses_mode
            ON analyses(mode)
        """)

    print("DATABASE INITIALIZED")
    print("DATABASE PATH:", DATABASE)


def initialize_account(mode, starting_balance):

    validate_mode(mode)

    with connect() as connection:

        connection.execute("""
            INSERT OR IGNORE INTO account_state
            (mode, balance, daily_loss, daily_realized_pnl, updated_at)
            VALUES (?, ?, 0, 0, ?)
        """, (mode, starting_balance, now()))


def get_account_state(mode):

    validate_mode(mode)

    with connect() as connection:

        row = connection.execute("""
            SELECT * FROM account_state
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

    with connect() as connection:

        connection.execute("""
            INSERT INTO account_state
            (mode, balance, daily_loss, daily_realized_pnl, updated_at)
            VALUES (?, ?, ?, ?, ?)

            ON CONFLICT(mode) DO UPDATE SET
                balance = excluded.balance,
                daily_loss = excluded.daily_loss,
                daily_realized_pnl = excluded.daily_realized_pnl,
                updated_at = excluded.updated_at
        """, (
            mode,
            balance,
            daily_loss,
            daily_realized_pnl,
            now()
        ))


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


def save_analysis(token, score, decision, mode="paper"):

    validate_mode(mode)

    with connect() as connection:

        connection.execute("""
            INSERT INTO analyses
            (token_address, score, decision, timestamp, mode)
            VALUES (?, ?, ?, ?, ?)
        """, (
            token,
            score,
            decision,
            now(),
            mode
        ))


def save_position(
    mode,
    token_address,
    entry_price,
    invested,
    strategy="main",
    metadata=None
):

    validate_mode(mode)

    metadata_json = json.dumps(metadata or {})

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
            metadata_json
        ))


def delete_position(mode, token_address):

    validate_mode(mode)

    with connect() as connection:

        connection.execute("""
            DELETE FROM positions
            WHERE mode = ? AND token_address = ?
        """, (mode, token_address))


def get_positions(mode):

    validate_mode(mode)

    with connect() as connection:

        rows = connection.execute("""
            SELECT * FROM positions
            WHERE mode = ?
        """, (mode,)).fetchall()

    positions = {}

    for row in rows:

        position = dict(row)

        try:
            position["metadata"] = json.loads(
                position.get("metadata") or "{}"
            )
        except (ValueError, TypeError):
            position["metadata"] = {}

        positions[position["token_address"]] = {
            "entry_price": position["entry_price"],
            "invested": position["invested"],
            "strategy": position["strategy"],
            "opened_at": position["opened_at"],
            "metadata": position["metadata"]
        }

    return positions


def save_pending_token(
    mode,
    token_address,
    strategy,
    first_seen,
    token_data
):

    validate_mode(mode)

    with connect() as connection:

        connection.execute("""
            INSERT INTO pending_tokens
            (mode, token_address, strategy, first_seen, token_data)
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
        """, (mode, token_address))


def get_pending_tokens(mode):

    validate_mode(mode)

    with connect() as connection:

        rows = connection.execute("""
            SELECT * FROM pending_tokens
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


def get_trade_stats(mode="paper"):

    validate_mode(mode)

    with connect() as connection:

        result = connection.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN profit_loss < 0 THEN 1 ELSE 0 END) AS losses,
                SUM(profit_loss) AS total_profit
            FROM trades
            WHERE action = 'SELL' AND mode = ?
        """, (mode,)).fetchone()

    return {
        "total_trades": result["total"] or 0,
        "winning_trades": result["wins"] or 0,
        "losing_trades": result["losses"] or 0,
        "total_profit": round(result["total_profit"] or 0, 2)
    }


if __name__ == "__main__":

    initialize_database()

    initialize_account("paper", 100.00)
    initialize_account("live", 0.00)

    print("PAPER ACCOUNT:", get_account_state("paper"))
    print("LIVE ACCOUNT:", get_account_state("live"))
    print("DATABASE TEST COMPLETED")
