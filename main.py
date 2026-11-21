import time
from datetime import datetime

def main():
    print("SOLANA AI TRADER 1.0")
    print("Robot started successfully!")

    while True:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] Trading bot is running...")
        time.sleep(60)

if __name__ == "__main__":
    main()
