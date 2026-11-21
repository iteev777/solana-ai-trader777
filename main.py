import sqlite3
from datetime import datetime

DATABASE = "trading_history.db"


def connect():
    return sqlite3.connect(DATABASE)


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


def save_trade(token, action, price, amount, profit_loss=0):

    with connect() as connection:

        connection.execute("""
            INSERT INTO trades
            (token_address, action, price, amount, profit_loss, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            token,
            action,
            price,
            amount,
            profit_loss,
            datetime.utcnow().isoformat()
        ))


def save_analysis(token, score, decision):

    with connect() as connection:

        connection.execute("""
            INSERT INTO analyses
            (token_address, score, decision, timestamp)
            VALUES (?, ?, ?, ?)
        """, (
            token,
            score,
            decision,
            datetime.utcnow().isoformat()
        ))


def get_trade_stats():

    with connect() as connection:

        result = connection.execute("""
            SELECT
                COUNT(*),
                SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END),
                SUM(CASE WHEN profit_loss < 0 THEN 1 ELSE 0 END),
                SUM(profit_loss)
            FROM trades
            WHERE action = 'SELL'
        """).fetchone()

    return {
        "total_trades": result[0] or 0,
        "winning_trades": result[1] or 0,
        "losing_trades": result[2] or 0,
        "total_profit": round(result[3] or 0, 2)
    }


if __name__ == "__main__":
    initialize_database()
    print("DATABASE 2.0 READY")
