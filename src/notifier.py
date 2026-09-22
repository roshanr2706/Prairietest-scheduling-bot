from __future__ import annotations
import logging
import requests

log = logging.getLogger(__name__)

class Notifier:
    def __init__(self, webhook_url: str | None):
        self.webhook_url = webhook_url

    def send(self, event: str, message: str) -> bool:
        text = f"[{event}] {message}"
        if not self.webhook_url:
            log.info("notify (no webhook): %s", text)
            return False
        if "discord.com" in self.webhook_url or "discordapp.com" in self.webhook_url:
            payload = {"content": text}
        else:
            payload = {"text": text}
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            ok = 200 <= resp.status_code < 300
            if not ok:
                log.warning("notify failed status=%s", resp.status_code)
            return ok
        except requests.RequestException as e:
            log.warning("notify error: %s", e)
            return False
