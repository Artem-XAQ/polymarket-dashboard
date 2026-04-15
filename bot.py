#!/usr/bin/env python3
"""
Standalone bot runner — run this separately from the dashboard.

Usage:
    python bot.py              # Run with default config (paper mode)
    python bot.py config.yaml  # Run with specific config file

The bot scans Polymarket markets, runs 6 quant formulas on each,
and executes trades when all filters pass. Risk limits are enforced.

Monitor the bot via the Streamlit dashboard (Page 8: Bot Monitor).
"""

import sys
import time
import signal
import logging
from src.bot_engine import TradingBot, load_config
from src import database as db

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/bot.log", mode="a"),
    ]
)
logger = logging.getLogger(__name__)


def main():
    # Load config
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    config = load_config(config_path)

    mode = config.get("bot", {}).get("mode", "paper")
    interval = config.get("bot", {}).get("scan_interval_seconds", 30)

    logger.info("=" * 60)
    logger.info(f"  Polymarket Trading Bot")
    logger.info(f"  Mode: {mode.upper()}")
    logger.info(f"  Scan interval: {interval}s")
    logger.info(f"  Keywords: {config.get('bot', {}).get('markets', [])}")
    logger.info("=" * 60)

    if mode == "live":
        logger.warning("⚠️  LIVE MODE — Real money will be used!")
        logger.warning("    Make sure your config.yaml has valid credentials.")
        logger.warning("    Press Ctrl+C within 5 seconds to abort...")
        time.sleep(5)

    # Initialize bot
    bot = TradingBot(config)

    # Handle Ctrl+C gracefully
    def shutdown(signum, frame):
        logger.info("Shutting down...")
        bot.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Start
    logger.info("Starting bot...")
    bot.start()

    # Keep main thread alive
    try:
        while bot.is_running:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down...")
        bot.stop()


if __name__ == "__main__":
    main()
