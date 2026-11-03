"""Small HTTP client utility for lifecycle adapters."""

import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def json_request(
    url: str,
    method: str,
    payload: dict[str, object],
    auth_header: str,
    timeout_seconds: float = 10.0,
    retries: int = 2,
    backoff_seconds: float = 0.25,
) -> dict[str, object]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"}:
        return {"ok": False, "status": 0, "error": "unsupported_url_scheme"}
    if parsed.scheme == "http" and auth_header and parsed.hostname not in _LOOPBACK_HOSTS:
        return {"ok": False, "status": 0, "error": "credentials_require_https"}

    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": auth_header,
    }

    started = time.time()
    attempt = 0
    retry_delays: list[float] = []
    while True:
        # the scheme is checked above
        request = Request(url=url, data=body, method=method.upper(), headers=headers)  # noqa: S310  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                status = int(getattr(response, "status", 200))
                raw_body = response.read().decode("utf-8", errors="replace")
                return {
                    "ok": 200 <= status < 300,
                    "status": status,
                    "body": raw_body,
                    "attempts": attempt + 1,
                    "retry_delays": retry_delays,
                    "elapsed_ms": int((time.time() - started) * 1000),
                }
        except HTTPError as exc:
            status = int(getattr(exc, "code", 500))
            raw_error = exc.read().decode("utf-8", errors="replace")
            if status in RETRYABLE_STATUS_CODES and attempt < retries:
                delay = backoff_seconds * (2**attempt)
                retry_delays.append(delay)
                time.sleep(delay)
                attempt += 1
                continue
            return {
                "ok": False,
                "status": status,
                "error": raw_error or str(exc),
                "attempts": attempt + 1,
                "retry_delays": retry_delays,
                "elapsed_ms": int((time.time() - started) * 1000),
            }
        except (URLError, OSError) as exc:
            if attempt < retries:
                delay = backoff_seconds * (2**attempt)
                retry_delays.append(delay)
                time.sleep(delay)
                attempt += 1
                continue
            return {
                "ok": False,
                "status": 0,
                "error": str(exc),
                "attempts": attempt + 1,
                "retry_delays": retry_delays,
                "elapsed_ms": int((time.time() - started) * 1000),
            }
