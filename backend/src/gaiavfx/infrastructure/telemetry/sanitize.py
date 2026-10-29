"""Sanitize before journaling or handing any data to the SDK."""

import dataclasses
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

SENSITIVE = {
    "authorization",
    "proxy_authorization",
    "cookie",
    "set_cookie",
    "password",
    "passphrase",
    "secret",
    "secret_key",
    "api_key",
    "apikey",
    "public_key",
    "access_token",
    "refresh_token",
    "session_token",
    "token",
}
URL = re.compile(r"https?://[^\s\"'<>]+")
KEY = re.compile(r"\b(?:[sp]k-lf-|sk-proj-|sk-or-v1-)[A-Za-z0-9_-]+")
AUTH = re.compile(
    r"(?i)\b((?:proxy-)?authorization\s*[:=]\s*[\"']?(?:Bearer|Basic))\s+[A-Za-z0-9_./+=:-]+"
)
DATA = re.compile(r"data:[\w.+/-]+;base64,[A-Za-z0-9+/=\r\n]+")
WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/](?!/)(?:[^\s\"'<>]+)")


class Sanitizer:
    def __init__(self, root, credentials, public_keys=lambda: ()):
        self.root = root
        self.credentials = credentials
        self.public_keys = public_keys

    def text(self, value, redactions=None):
        redactions = (
            redactions
            if redactions is not None
            else (*self.credentials.sensitive_values(), *self.public_keys())
        )
        for secret in redactions:
            if secret and len(secret) >= 4:
                value = value.replace(secret, "[credential omitted]")
        value = KEY.sub("[credential omitted]", value)
        value = AUTH.sub(r"\1 [credential omitted]", value)
        value = DATA.sub("[inline media; see attachment]", value)

        def clean_url(match):
            try:
                url = urlsplit(match[0])
                host = url.hostname or ""
                if url.port:
                    host += ":" + str(url.port)
                return urlunsplit((url.scheme, host, url.path, "", ""))
            except ValueError:
                return "[invalid URL omitted]"

        value = URL.sub(clean_url, value)
        value = value.replace(str(self.root), "<library>").replace(
            self.root.as_posix(), "<library>"
        )
        return WINDOWS_PATH.sub(
            lambda m: "<external>/" + m[0].replace("\\", "/").rsplit("/", 1)[-1], value
        )

    def clean(self, value, media=None, *, depth=0, redactions=None):
        if redactions is None:
            redactions = (*self.credentials.sensitive_values(), *self.public_keys())
        if depth > 40:
            return {"capture_error": "Object nesting exceeds 40 levels"}
        if isinstance(value, str):
            if media and value.startswith("data:") and ";base64," in value:
                return media(value)
            # Provider errors and OTel attributes can contain serialized request/response objects.
            if value.lstrip().startswith(("{", "[")):
                try:
                    nested = json.loads(value)
                except (ValueError, RecursionError):
                    pass
                else:
                    cleaned = self.clean(nested, media, depth=depth + 1, redactions=redactions)
                    # Exact prompt/answer whitespace is evidence, too.
                    return value if cleaned == nested else json.dumps(cleaned, ensure_ascii=False)
            return self.text(value, redactions)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, Path):
            return self.text(str(value), redactions)
        if not isinstance(value, type) and hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        elif dataclasses.is_dataclass(value) and not isinstance(value, type):
            value = dataclasses.asdict(value)
        if isinstance(value, dict):
            return {
                self.text(str(k), redactions): "[credential omitted]"
                if str(k).lower().replace("-", "_") in SENSITIVE
                else self.clean(v, media, depth=depth + 1, redactions=redactions)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [self.clean(v, media, depth=depth + 1, redactions=redactions) for v in value]
        if isinstance(value, bytes):
            return {"binary_bytes": len(value), "capture_error": "Use a media attachment"}
        return {"type": type(value).__name__}
