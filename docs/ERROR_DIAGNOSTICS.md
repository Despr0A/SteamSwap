# Request and task error diagnostics

History failures retain their cause from the HTTP adapter through the account
retry service to the pipeline candidate log. A missing result is no longer
reported as an undifferentiated lack of history.

## Reading a failure

The visible message includes a short explanation and named fields:

- `request_id`: groups a history operation across account/proxy attempts.
- `code`, `stage`, `operation`: failure category and processing stage.
- `source`: module/function for protocol failures; file, line, and function for
  exceptions.
- `item`, `app_id`, `account`, `attempt`: the failing item and attempted account.
- `endpoint`, `proxy`, `method`: host/path and network route. URL queries and
  proxy credentials are omitted. `direct/system` means no explicit proxy was
  selected; requests may still use operating-system proxy settings.
- `status`: HTTP status; `no_response` means no HTTP response was obtained.
- `elapsed_ms`, `timeout_seconds`: duration and configured timeout.
- `auth_refresh_redirected`, `retry_after`, `content_type`: response clues.
- `cookie_present`, `cookie_identity_matches`, `cookie_expired`: local hints.
  Unknown/unparseable values remain null. JWT expiry is parsed without signature
  verification and is a diagnostic hint, not proof of server-side auth validity.
- `exception`, `detail`, `action`: exception type, safe cause, and next handling.

`GET /api/log` entries may additionally contain a `diagnostic` object. This
contains the safe redirect chain, exception causes (including Windows error
numbers), a stack of file/line/function frames, per-attempt identifiers, and
failure counts by code. Candidate processing also emits a structured `report`
object in two categories: `item_report` for one item's decision chain and
`network_report` for the Steam/BUFF read operation used by that decision.
The UI defaults to expandable item reports with result filters, price formulas,
data age and source. Each item links to its network reports through `report_id`.
History reads distinguish memory/database reuse from refreshes; calling the
history service does not by itself imply an HTTP request. Both report and
diagnostic JSON survive polling, download, export and restoration. The existing
in-memory log retains its most recent 500 entries; export before older entries
roll off.

## Main categories

| Code | Meaning |
| --- | --- |
| `CONNECTION_RESET` | Network path reset the connection, including WinError 10054 |
| `CONNECTION_REFUSED`, `DNS_ERROR` | Port refusal or domain-resolution failure |
| `PROXY_ERROR`, `PROXY_AUTH_REQUIRED`, `TLS_ERROR` | Proxy or TLS failure |
| `CONNECT_TIMEOUT`, `READ_TIMEOUT`, `TIMEOUT` | Connection or response timeout |
| `RATE_LIMITED` | HTTP 429 at the final history endpoint |
| `AUTH_REFRESH_RATE_LIMITED` | HTTP 429 at the final Steam JWT refresh endpoint |
| `AUTH_REQUIRED`, `ACCESS_DENIED` | HTTP 401/403; 403 alone does not prove expired cookies |
| `INVALID_JSON` | Response arrived but JSON decoding failed; HTTP status is retained |
| `INVALID_RESPONSE`, `API_REJECTED` | Unexpected response schema or explicit API rejection |
| `EMPTY_HISTORY` | Successful response with an empty prices array |
| `HISTORY_CACHE_READ_ERROR`, `HISTORY_STORAGE_ERROR` | Local cache read/write failure |
| `INVALID_HISTORY_POINTS`, `HISTORY_CURRENCY_UNSUPPORTED` | Invalid points or currency conversion failure |
| `NO_AVAILABLE_PROXY`, `ACCOUNTS_COOLING_DOWN`, `CANCELLED` | Local routing, cooldown, or task stop |
| `COOKIE_EXPIRED`, `COOKIE_REFRESH_FAILED` | Expired credentials skipped or login recovery failed before history request |
| `HISTORY_ADAPTER_ERROR` | Adapter implementation error; not retried as a compatibility fallback |
| `WRITE_RESULT_UNKNOWN` | BUFF write may have reached the server; never automatically replay |

A redirect through JWT refresh is retained independently of the final endpoint.
A 429 from history after a completed refresh is not labelled as refresh-endpoint
throttling. The existing forced-login recovery behavior is preserved.

## Compatibility and redaction

Low-level history calls keep their existing default, `return_status`, and
`return_metadata` interfaces. `return_diagnostics=True` opts into
`(data, status, diagnostic)`. The service still returns data or `None`; callers
can inspect `get_last_history_error(item, app_id)` on the same thread. Context is
thread-local and reset for each fetch, including cache hits and successful
retries. Pipeline logs use this context before advancing to another candidate.

Task log messages and diagnostic fields are redacted at the state boundary and
again during export. Cookie, password, authorization, CSRF token, JWT, and proxy
URL credentials are removed. HTTP bodies and response headers containing
credentials are not copied into diagnostics. Only selected response metadata is
retained. Logs are diagnostic artifacts, not packet captures.

BUFF errors keep their original exception classes and retry/circuit behavior.
The diagnostic records whether a request was attempted, which is not proof that
the server received it. A write failure retains the original cause chain and
continues to require order reconciliation.

## Steam Cookie renewal

An independent background worker checks saved accounts hourly while the pipeline
and checkout are idle. It performs password login when a Cookie is missing,
belongs to another identity, has an expired JWT, or was last obtained at least
seven days ago. The seven-day age is persisted as `cookie_refreshed_at`; legacy
Cookies use the JWT issue time, and an unknown age triggers renewal. A failed
attempt is retried at the next hourly check. This does not require BUFF session
keepalive to be enabled.

Renewal requires the account's saved login credentials and Steam Guard secret
when required by Steam. It saves credentials under the target account without
switching the selected account. A mismatched returned Steam ID is rejected.
`steam_cookie_refresh` logs the account ID, reason and result. Current-account
history requests also renew a locally expired Cookie before sending a request;
failed renewal skips that Cookie. Other expired history accounts wait for the
idle background renewal.

Network failures during automatic Steam login do not place the account in a
cooldown. Login uses the configured direct/proxy retry policy, and a subsequent
renewal can immediately retry after a transient connection failure. Request
diagnostics retain the original endpoint, network route and exception chain.

Proxy warmup updates each node's weight as soon as its probe completes. Failed
nodes become ineligible immediately while other probes continue. An HTTP CONNECT
407 found in the exception chain is classified as `PROXY_AUTH_REQUIRED`, even
when no target-site HTTP response was received.

History responses containing a bare yen/yuan symbol retain it as
`currency_symbol` when the ISO currency cannot be detected. The history service
resolves this symbol only from the matching request account's verified wallet
currency (CNY or JPY). If the account is unverified, it refreshes the wallet
currency using the same Cookie before conversion. Failed wallet checks or
identity mismatches leave the currency unresolved instead of assuming CNY.
