"""
telegram.py — a thin wrapper over Telegram's Bot API.

No libraries. Telegram's API is plain HTTPS with JSON, so urllib is enough.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.telegram.org"


class Blocked(Exception):
    """The user blocked the bot or deleted the chat. Stop writing to them."""


class Telegram:
    def __init__(self, token):
        self.token = token
        self.offset = None

    def _call(self, method, params, timeout=30):
        url = f"{API}/bot{self.token}/{method}"
        data = urllib.parse.urlencode(
            {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
             for k, v in params.items() if v is not None}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            # 403 = blocked by user. 400 = chat not found / deactivated.
            if e.code in (400, 403) and (
                    "blocked" in body or "chat not found" in body
                    or "deactivated" in body or "kicked" in body):
                raise Blocked(body) from e
            raise RuntimeError(f"telegram {method} -> {e.code} {body[:200]}") from e

    # --- sending -----------------------------------------------------------

    def send(self, chat_id, text, buttons=None, preview=False):
        return self._call("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "false" if preview else "true",
            "reply_markup": {"inline_keyboard": buttons} if buttons else None,
        })

    def edit(self, chat_id, message_id, text, buttons=None):
        try:
            return self._call("editMessageText", {
                "chat_id": chat_id, "message_id": message_id, "text": text,
                "reply_markup": {"inline_keyboard": buttons} if buttons else None,
            })
        except RuntimeError:
            # Editing fails if the message is unchanged or too old; not fatal.
            return None

    def ack(self, callback_id, text=None):
        """Stop the button showing a spinner."""
        try:
            self._call("answerCallbackQuery",
                       {"callback_query_id": callback_id, "text": text})
        except RuntimeError:
            pass

    # --- receiving ---------------------------------------------------------

    def updates(self, wait=25):
        """Long-poll for new messages. Blocks up to `wait` seconds."""
        r = self._call("getUpdates",
                       {"offset": self.offset, "timeout": wait,
                        "allowed_updates": ["message", "callback_query"]},
                       timeout=wait + 15)
        out = r.get("result", [])
        if out:
            self.offset = out[-1]["update_id"] + 1
        return out

    def me(self):
        return self._call("getMe", {}).get("result", {})


def keyboard(rows):
    """[[('Label', 'callback_data'), ...], ...] -> Telegram's format."""
    return [[{"text": t, "callback_data": d} for t, d in row] for row in rows]
