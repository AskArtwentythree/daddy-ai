"""Per-address limits on the endpoints anyone can call without a token: signing up, joining and logging in.

Codes and tokens are far too long to guess, so these limits are about floods: a script hammering the join or
sign-up endpoints gets 429 instead of filling the database or the approvers' screens. The limits are generous
because a whole hackathon or office often shares one public address.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Request


@dataclass(frozen=True)
class Limit:
    requests: int
    seconds: int
    what: str


LIMITS: dict[tuple[str, str], Limit] = {
    ("POST", "/api/bootstrap"): Limit(20, 3600, "new teams"),
    ("POST", "/api/join"): Limit(60, 600, "join attempts"),
    ("POST", "/api/join/project"): Limit(60, 600, "join attempts"),
    ("POST", "/api/join/status"): Limit(300, 60, "approval checks"),
    ("POST", "/api/dashboard/session"): Limit(60, 600, "dashboard logins"),
}


class RateLimited(Exception):
    def __init__(self, limit: Limit, retry_after: int):
        super().__init__(f"Too many {limit.what} from your network; try again in {retry_after} s")
        self.retry_after = retry_after


class Limiter:
    def __init__(self) -> None:
        self._hits: dict[tuple, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: tuple, limit: Limit, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - limit.seconds:
                hits.popleft()
            if len(hits) >= limit.requests:
                raise RateLimited(limit, max(1, int(hits[0] + limit.seconds - now) + 1))
            hits.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = Limiter()


def client_address(request: Request) -> str:
    """The visitor's address. Behind CloudFront and the load balancer (the only way in, proven by the origin
    header the load balancer insists on), CloudFront adds the visitor and the balancer adds CloudFront, so the
    visitor is second from the end; anything earlier in the header was sent by the client and can't be trusted."""
    forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
    if request.headers.get("x-origin-verify") and len(forwarded) >= 2:
        return forwarded[-2]
    return request.client.host if request.client else "unknown"


def enforce(request: Request) -> None:
    limit = LIMITS.get((request.method, request.url.path))
    if limit is not None:
        limiter.check((request.url.path, client_address(request)), limit)
