import base64
import json
import math
from urllib.parse import unquote


def cookie_metadata(cookies: str) -> dict:
    parts = dict(part.strip().split("=", 1) for part in (cookies or "").split(";") if "=" in part)
    secure = unquote(parts.get("steamLoginSecure", ""))
    identity, _, token = secure.partition("||")
    metadata = {"steam_id": identity if identity.isdigit() else "", "session_id": parts.get("sessionid", "")}
    try:
        fragment = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(fragment + "=" * (-len(fragment) % 4)))
        for source, target in (("exp", "expires_at"), ("iat", "issued_at")):
            stamp = float(claims.get(source, 0))
            if math.isfinite(stamp) and stamp > 0:
                metadata[target] = stamp
    except (ValueError, TypeError, AttributeError, IndexError, OverflowError):
        pass
    return metadata
