"""Signed media links. An <img>, a <video> or a splat loader cannot send the bearer token, so the URLs the API hands
out carry an expiring HMAC signature over what they may serve. One expiry rule for every link: valid six to seven
hours, rounded to the hour, so repeated reads give the same URL and the renderer's caches hold."""

import hashlib
import hmac
import time

from gaiavfx.shared.errors import ApplicationError


class SignedLinks:
    def __init__(self, token: str):
        self._key = token.encode()

    def _signature(self, parts) -> str:
        return hmac.new(self._key, ":".join(map(str, parts)).encode(), hashlib.sha256).hexdigest()

    def url(self, path: str, *scope) -> str:
        """`path`, signed for `scope` (what the link may serve)."""
        expires = (int(time.time()) // 3600 + 7) * 3600
        return f"{path}?expires={expires}&signature={self._signature((*scope, expires))}"

    def check(self, signature: str, *scope, expires: int, expired: str) -> None:
        """Refuse a link whose time is up or whose signature is not for `scope`, saying `expired`."""
        if expires < time.time() or not hmac.compare_digest(
            signature, self._signature((*scope, expires))
        ):
            raise ApplicationError(expired, 401)

    def media(self, asset_id: str, rendition: str) -> str:
        """A Library asset's rendition: original, preview or thumbnail."""
        return self.url(f"/api/v1/media/{asset_id}/{rendition}", asset_id, rendition)
