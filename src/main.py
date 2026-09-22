from __future__ import annotations
import asyncio
import logging
import os
import signal
from src.config import load_config
from src.notifier import Notifier
from src.watcher import run_watch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

def main():
    config_path = os.environ.get("CONFIG_PATH", "config.yaml")
    storage_path = os.environ.get("STORAGE_STATE", "data/storageState.json")
    config = load_config(config_path)
    notifier = Notifier(config.notify.webhook_url)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(run_watch(config, notifier, storage_path))

    def _stop(*_):
        notifier.send("stopping", "received shutdown signal")
        task.cancel()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _stop())  # Windows fallback

    try:
        loop.run_until_complete(task)
    except asyncio.CancelledError:
        pass
    finally:
        loop.close()

if __name__ == "__main__":
    main()
