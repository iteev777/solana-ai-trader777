
import os
import database
import live_execution


MODE = "live"

LIVE_TRADING_ENABLED = False

MAX_TRADE_SIZE_USD = 5.00
MAX_POSITIONS_PER_STRATEGY = 5

STOP_LOSS = 0.15
TAKE_PROFIT = 0.25


class LiveTrading:

    def __init__(self):

        database.initialize_database()

        database.initialize_account(
            MODE,
            0.00
        )

        self.account = database.get_account_state(MODE)

        self.balance = float(self.account["balance"])

        self.positions = database.get_positions(MODE)

        print("LIVE TRADING ENGINE INITIALIZED")
        print("LIVE BALANCE:", self.balance)
        print("LIVE POSITIONS:", len(self.positions))
        print("LIVE EXECUTION: LOCKED")

    def get_execution_status(self):
        return live_execution.get_execution_status()

    def prepare_swap_plan(self, quote, side):
        return live_execution.build_execution_plan(quote, side)

    def execute_swap_plan(self, plan):
        return live_execution.execute_plan(plan)

    def position_count(self, strategy):

        return sum(
            1
            for position in self.positions.values()
            if position.get("strategy", "main") == strategy
        )

    def get_account_summary(self):

        return {
            "mode": MODE,
            "balance": round(self.balance, 2),
            "positions": len(self.positions),
            "invested": round(
                sum(
                    position["invested"]
                    for position in self.positions.values()
                ),
                2
            ),
            "daily_loss": self.account["daily_loss"],
            "daily_realized_pnl": self.account["daily_realized_pnl"],
            "execution_enabled": LIVE_TRADING_ENABLED
        }

    def open_position(
        self,
        token_address,
        entry_price,
        strategy="main"
    ):

        if not LIVE_TRADING_ENABLED:
            return "LIVE TRADING LOCKED"

        return "LIVE EXECUTION NOT IMPLEMENTED"

    def close_position(
        self,
        token_address,
        current_price,
        reason
    ):

        if not LIVE_TRADING_ENABLED:
            return "LIVE TRADING LOCKED"

        return "LIVE EXECUTION NOT IMPLEMENTED"

    def update_position(
        self,
        token_address,
        current_price
    ):

        if not LIVE_TRADING_ENABLED:
            return "LIVE TRADING LOCKED"

        return "LIVE EXECUTION NOT IMPLEMENTED"


live = LiveTrading()


if __name__ == "__main__":

    print("LIVE ACCOUNT SUMMARY")
    print(live.get_account_summary())
