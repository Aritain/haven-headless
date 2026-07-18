import requests

import db


def send_message(content, mention_role_id=None):
    """Returns (ok: bool, detail: str)."""
    webhook_url = db.get_setting("discord_webhook_url")
    if not webhook_url:
        return False, "No webhook URL is set in Settings."

    payload = {"content": content}
    if mention_role_id:
        payload["content"] = f"<@&{mention_role_id}> {content}"
        # Discord ignores role mentions in content unless explicitly allowed
        payload["allowed_mentions"] = {"parse": [], "roles": [mention_role_id]}

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if 200 <= resp.status_code < 300:
            return True, f"Sent (HTTP {resp.status_code})."
        return False, f"Discord returned HTTP {resp.status_code}: {resp.text[:300]}"
    except Exception as e:
        return False, f"Request failed: {e}"


DEFAULT_TEMPLATE = "**Sighting!** `{character}` ({account}) spotted **{gob}** on the **{road}** road."


def notify_found(account_label, character_name, road_name, gob_name):
    template = db.get_setting("discord_message_template") or DEFAULT_TEMPLATE
    fields = {"character": character_name, "account": account_label, "gob": gob_name, "road": road_name}
    try:
        content = template.format(**fields)
    except (KeyError, IndexError, ValueError):
        content = DEFAULT_TEMPLATE.format(**fields)
    role_id = db.get_setting("discord_role_id")
    ok, detail = send_message(content, mention_role_id=role_id)
    if not ok:
        print(f"Discord notify failed: {detail}")

