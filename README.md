# posthook

The official Python client library for the [Posthook](https://posthook.io) API -- schedule webhooks and deliver them reliably.

## Installation

```bash
pip install posthook
```

**Requirements:** Python 3.9+. Only dependency is [httpx](https://www.python-httpx.org/).

## Quick Start

```python
import posthook

client = posthook.Posthook("pk_...")

# Schedule a webhook 5 minutes from now
hook = client.hooks.schedule(
    path="/webhooks/user-created",
    post_in="5m",
    data={"userId": "123", "event": "user.created"},
)

print(hook.id)      # UUID
print(hook.status)  # "pending"
```

## How It Works

Your Posthook project has a **domain** configured in the [dashboard](https://posthook.io) (e.g., `webhook.example.com`). When you schedule a hook, you specify a **path** (e.g., `/webhooks/user-created`). At the scheduled time, Posthook delivers the hook by POSTing to the full URL (`https://webhook.example.com/webhooks/user-created`) with your data payload and signature headers.

## Authentication

You can find your API key under **Project Settings** in the [Posthook dashboard](https://posthook.io). Pass it directly to the constructor, or set the `POSTHOOK_API_KEY` environment variable:

```python
# Explicit API key
client = posthook.Posthook("pk_...")

# From environment variable
client = posthook.Posthook()  # reads POSTHOOK_API_KEY
```

For webhook signature verification, also provide a signing key:

```python
client = posthook.Posthook("pk_...", signing_key="ph_sk_...")
```

The signing key can also be set via the `POSTHOOK_SIGNING_KEY` environment variable.

## Scheduling Hooks

Three mutually exclusive scheduling modes are available. You must provide exactly one of `post_in`, `post_at`, or `post_at_local`.

### Relative delay (`post_in`)

Schedule after a relative delay. Accepts `s` (seconds), `m` (minutes), `h` (hours), or `d` (days):

```python
hook = client.hooks.schedule(
    path="/webhooks/send-reminder",
    post_in="30m",
    data={"userId": "123"},
)
```

### Absolute UTC time (`post_at`)

Schedule at an exact UTC time. Accepts `datetime` objects or ISO 8601 strings:

```python
from datetime import datetime, timedelta, timezone

# Using a datetime object (automatically converted to UTC)
hook = client.hooks.schedule(
    path="/webhooks/send-reminder",
    post_at=datetime.now(timezone.utc) + timedelta(hours=1),
    data={"userId": "123"},
)

# Using an ISO string
hook = client.hooks.schedule(
    path="/webhooks/send-reminder",
    post_at="2026-06-15T10:00:00Z",
    data={"userId": "123"},
)
```

### Local time with timezone (`post_at_local`)

Schedule at a local time. Posthook handles DST transitions automatically:

```python
hook = client.hooks.schedule(
    path="/webhooks/daily-digest",
    post_at_local="2026-03-01T09:00:00",
    timezone="America/New_York",
    data={"userId": "123"},
)
```

### Custom retry configuration

Override your project's default retry behavior for a specific hook:

```python
hook = client.hooks.schedule(
    path="/webhooks/critical",
    post_in="1m",
    data={"orderId": "456"},
    retry_override=posthook.HookRetryOverride(
        min_retries=10,
        delay_secs=15,
        strategy="exponential",
        backoff_factor=2.0,
        max_delay_secs=3600,
        jitter=True,
    ),
)
```

## Managing Hooks

### Get a hook

```python
hook = client.hooks.get("hook-uuid")
```

### List hooks

```python
hooks = client.hooks.list(status=posthook.STATUS_FAILED, limit=50)
print(f"Found {len(hooks)} hooks")
```

All list parameters are optional:

| Parameter | Description |
|-----------|-------------|
| `status` | Filter by status: `"pending"`, `"retry"`, `"completed"`, `"failed"` |
| `limit` | Max results per page |
| `sort_by` | Sort field (e.g., `"createdAt"`, `"postAt"`) |
| `sort_order` | `"ASC"` or `"DESC"` |
| `post_at_before` | Filter hooks scheduled before this time (ISO string) |
| `post_at_after` | Cursor: hooks scheduled after this time (ISO string) |
| `created_at_before` | Filter hooks created before this time (ISO string) |
| `created_at_after` | Filter hooks created after this time (ISO string) |

### Cursor-based pagination

Use `post_at_after` as a cursor. After each page, advance it to the last hook's `post_at`:

```python
limit = 100
cursor = None
while True:
    hooks = client.hooks.list(status="failed", limit=limit, post_at_after=cursor)
    for hook in hooks:
        print(hook.id, hook.failure_error)

    if len(hooks) < limit:
        break  # last page
    cursor = hooks[-1].post_at.isoformat()
```

### Auto-paginating iterator (`list_all`)

For convenience, `list_all` yields every matching hook across all pages automatically:

```python
for hook in client.hooks.list_all(status="failed"):
    process(hook)
```

The async client returns an async iterator:

```python
async for hook in client.hooks.list_all(status="failed"):
    await process(hook)
```

### Delete a hook

Idempotent -- returns `None` on both success and 404 (already delivered or gone):

```python
client.hooks.delete("hook-uuid")
```

## Bulk Operations

Three bulk operations are available, each supporting by-IDs or by-filter:

- **Retry** -- Re-attempts delivery for failed hooks
- **Replay** -- Re-delivers completed hooks (useful for reprocessing)
- **Cancel** -- Cancels pending hooks before delivery

### By IDs

```python
result = client.hooks.bulk.retry(["id-1", "id-2", "id-3"])
print(f"Retried {result.affected} hooks")
```

### By filter

```python
result = client.hooks.bulk.cancel_by_filter(
    start_time="2026-02-01T00:00:00Z",
    end_time="2026-02-22T00:00:00Z",
    limit=500,
    endpoint_key="/webhooks/deprecated",
)
print(f"Cancelled {result.affected} hooks")
```

All six methods:

```python
# By IDs
client.hooks.bulk.retry(hook_ids)
client.hooks.bulk.replay(hook_ids)
client.hooks.bulk.cancel(hook_ids)

# By filter
client.hooks.bulk.retry_by_filter(start_time, end_time, limit, ...)
client.hooks.bulk.replay_by_filter(start_time, end_time, limit, ...)
client.hooks.bulk.cancel_by_filter(start_time, end_time, limit, ...)
```

Filter methods also accept optional `endpoint_key` and `sequence_id` keyword arguments.

## Verifying Webhook Signatures

When Posthook delivers a hook to your endpoint, it includes signature headers for verification. Use `parse_delivery` to verify and parse the delivery.

**Important:** You must pass the **raw request body** (bytes or string), not a parsed JSON object.

### Flask

```python
from flask import Flask, request
import posthook

app = Flask(__name__)
client = posthook.Posthook("pk_...", signing_key="ph_sk_...")

@app.route("/webhooks/user-created", methods=["POST"])
def handle_webhook():
    try:
        delivery = client.signatures.parse_delivery(
            body=request.get_data(),
            headers=dict(request.headers),
        )
    except posthook.SignatureVerificationError:
        return "invalid signature", 401

    print(delivery.hook_id)   # from Posthook-Id header
    print(delivery.path)      # "/webhooks/user-created"
    print(delivery.data)      # your custom data payload
    print(delivery.post_at)   # when it was scheduled
    print(delivery.posted_at) # when it was delivered

    return "", 200
```

### Django

```python
from django.http import HttpResponse
import posthook

client = posthook.Posthook("pk_...", signing_key="ph_sk_...")

def handle_webhook(request):
    try:
        delivery = client.signatures.parse_delivery(
            body=request.body,
            headers=dict(request.headers),
        )
    except posthook.SignatureVerificationError:
        return HttpResponse(status=401)

    print(delivery.hook_id)
    print(delivery.data)

    return HttpResponse(status=200)
```

### FastAPI

```python
from fastapi import FastAPI, Request, Response
import posthook

app = FastAPI()
client = posthook.Posthook("pk_...", signing_key="ph_sk_...")

@app.post("/webhooks/user-created")
async def handle_webhook(request: Request):
    body = await request.body()
    try:
        delivery = client.signatures.parse_delivery(
            body=body,
            headers=dict(request.headers),
        )
    except posthook.SignatureVerificationError:
        return Response(status_code=401)

    print(delivery.hook_id)
    print(delivery.data)

    return Response(status_code=200)
```

### Custom tolerance

By default, signatures older than 5 minutes are rejected. You can override this:

```python
delivery = client.signatures.parse_delivery(
    body=raw_body,
    headers=headers,
    tolerance=600,  # 10 minutes, in seconds
)
```

## Error Handling

All API errors extend `PosthookError` and can be caught with `isinstance` or `except`:

```python
import posthook

try:
    hook = client.hooks.get("hook-id")
except posthook.RateLimitError:
    print("Rate limited, retry later")
except posthook.AuthenticationError:
    print("Invalid API key")
except posthook.NotFoundError:
    print("Hook not found")
except posthook.PosthookError as err:
    print(f"API error: {err.message} (status={err.status_code})")
```

| Error class | HTTP Status | Code |
|---|---|---|
| `BadRequestError` | 400 | `bad_request` |
| `AuthenticationError` | 401 | `authentication_error` |
| `ForbiddenError` | 403 | `forbidden` |
| `NotFoundError` | 404 | `not_found` |
| `PayloadTooLargeError` | 413 | `payload_too_large` |
| `RateLimitError` | 429 | `rate_limit_exceeded` |
| `InternalServerError` | 5xx | `internal_error` |
| `PosthookConnectionError` | -- | `connection_error` |
| `SignatureVerificationError` | -- | `signature_verification_error` |

## Configuration

```python
client = posthook.Posthook(
    "pk_...",
    base_url="https://api.staging.posthook.io",
    timeout=60,
    signing_key="ph_sk_...",
)
```

| Option | Description | Default |
|--------|-------------|---------|
| `api_key` | Your Posthook API key | `POSTHOOK_API_KEY` env var |
| `base_url` | Custom API base URL | `https://api.posthook.io` |
| `timeout` | Request timeout in seconds | `30` |
| `signing_key` | Signing key for webhook verification | `POSTHOOK_SIGNING_KEY` env var |
| `http_client` | Custom `httpx.Client` instance | -- |

## Quota Info

After scheduling a hook, quota information is available on the returned `Hook` object:

```python
hook = client.hooks.schedule(path="/test", post_in="5m")

if hook.quota:
    print(f"Limit:     {hook.quota.limit}")
    print(f"Usage:     {hook.quota.usage}")
    print(f"Remaining: {hook.quota.remaining}")
    print(f"Resets at: {hook.quota.resets_at}")
```

## Async Client

The `AsyncPosthook` client provides an identical API -- just `await` each call:

```python
import posthook

async with posthook.AsyncPosthook("pk_...") as client:
    hook = await client.hooks.schedule(path="/test", post_in="5m")
    print(hook.id)

    hooks = await client.hooks.list(status="pending")
```

Both the sync and async clients support context managers for automatic cleanup:

```python
# Sync
with posthook.Posthook("pk_...") as client:
    hook = client.hooks.schedule(path="/test", post_in="5m")

# Async
async with posthook.AsyncPosthook("pk_...") as client:
    hook = await client.hooks.schedule(path="/test", post_in="5m")
```

You can also call `close()` / `await close()` manually if you prefer.

## Debug Logging

The SDK logs all requests via Python's `logging` module under the `"posthook"` logger. Enable it to see request details:

```python
import logging

logging.basicConfig(level=logging.DEBUG)
```

Example output:

```
DEBUG:posthook:POST /v1/hooks -> 200 (0.153s)
DEBUG:posthook:GET /v1/hooks -> 200 (0.089s)
```

## Advanced

### Proxy support

Pass a custom `httpx.Client` configured with a proxy:

```python
import httpx
import posthook

http_client = httpx.Client(proxy="http://proxy.example.com:8080")
client = posthook.Posthook("pk_...", http_client=http_client)
```

### Custom CA certificates

```python
import httpx
import posthook

http_client = httpx.Client(verify="/path/to/custom-ca-bundle.crt")
client = posthook.Posthook("pk_...", http_client=http_client)
```

### Custom httpx client

For full control over HTTP behavior, provide your own `httpx.Client` (sync) or `httpx.AsyncClient` (async). The SDK will add its authentication headers automatically:

```python
import httpx
import posthook

http_client = httpx.Client(
    timeout=60,
    verify=True,
    proxy="http://proxy.example.com:8080",
    limits=httpx.Limits(max_connections=20),
)

client = posthook.Posthook("pk_...", http_client=http_client)
```

When you provide a custom client, the SDK does **not** close it on `client.close()` -- you are responsible for its lifecycle.

## Requirements

- Python 3.9+
- [httpx](https://www.python-httpx.org/) >= 0.25.0
