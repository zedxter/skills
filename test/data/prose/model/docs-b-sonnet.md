# Getting Started with the To-Do API

This guide walks you through making your first authenticated requests to the To-Do API. By the end, you'll have created, listed, updated, and deleted a to-do item.

## Prerequisites

- A to-do API account
- `curl` or any HTTP client
- Basic familiarity with JSON and HTTP

**Base URL:** `https://api.example.com/v1`

Replace this with your actual API host.

## 1. Get an API key

1. Sign in to the dashboard.
2. Open **Settings → API Keys**.
3. Click **Create key**, give it a name (for example, "local-dev"), and copy the key.

The key is shown only once, so store it somewhere safe. Treat it like a password: don't commit it to source control, embed it in client-side code, or share it in chat.

For the examples below, store it in an environment variable:

```bash
export TODO_API_KEY="your_api_key_here"
```

## 2. Authenticate

Send your key in the `Authorization` header as a bearer token on every request:

```
Authorization: Bearer YOUR_API_KEY
```

Test it:

```bash
curl https://api.example.com/v1/todos \
  -H "Authorization: Bearer $TODO_API_KEY"
```

A successful response looks like:

```json
{
  "data": [],
  "next_cursor": null
}
```

If the key is missing or invalid, you'll get:

```json
{
  "error": {
    "code": "unauthorized",
    "message": "Invalid or missing API key."
  }
}
```

with HTTP status `401`.

## 3. Create a to-do

```bash
curl -X POST https://api.example.com/v1/todos \
  -H "Authorization: Bearer $TODO_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Write the quarterly report",
    "notes": "Include revenue and churn charts",
    "due_date": "2026-11-15"
  }'
```

Response (`201 Created`):

```json
{
  "id": "td_8f3k2a",
  "title": "Write the quarterly report",
  "notes": "Include revenue and churn charts",
  "due_date": "2026-11-15",
  "completed": false,
  "created_at": "2026-10-30T09:12:44Z",
  "updated_at": "2026-10-30T09:12:44Z"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | Yes | Short summary, up to 200 characters |
| `notes` | string | No | Longer description |
| `due_date` | string | No | Date in `YYYY-MM-DD` format |
| `completed` | boolean | No | Defaults to `false` |

## 4. List to-dos

```bash
curl "https://api.example.com/v1/todos?completed=false&limit=20" \
  -H "Authorization: Bearer $TODO_API_KEY"
```

Query parameters:

| Parameter | Description |
|-----------|-------------|
| `completed` | Filter by status: `true` or `false` |
| `limit` | Items per page (default 20, max 100) |
| `cursor` | Pass the `next_cursor` value from the previous response to get the next page |

## 5. Get a single to-do

```bash
curl https://api.example.com/v1/todos/td_8f3k2a \
  -H "Authorization: Bearer $TODO_API_KEY"
```

Returns `404` if the item doesn't exist.

## 6. Update a to-do

Use `PATCH` to change only the fields you send:

```bash
curl -X PATCH https://api.example.com/v1/todos/td_8f3k2a \
  -H "Authorization: Bearer $TODO_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"completed": true}'
```

The response is the updated to-do object.

## 7. Delete a to-do

```bash
curl -X DELETE https://api.example.com/v1/todos/td_8f3k2a \
  -H "Authorization: Bearer $TODO_API_KEY"
```

A successful delete returns `204 No Content` with an empty body.

## Endpoint summary

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/todos` | List to-dos |
| `POST` | `/todos` | Create a to-do |
| `GET` | `/todos/{id}` | Retrieve a to-do |
| `PATCH` | `/todos/{id}` | Update a to-do |
| `DELETE` | `/todos/{id}` | Delete a to-do |

## Errors

Errors use a consistent format:

```json
{
  "error": {
    "code": "validation_error",
    "message": "title is required."
  }
}
```

| Status | Code | Meaning |
|--------|------|---------|
| `400` | `validation_error` | The request body or parameters are invalid |
| `401` | `unauthorized` | API key is missing or invalid |
| `404` | `not_found` | The resource doesn't exist |
| `429` | `rate_limited` | Too many requests |
| `500` | `server_error` | Something went wrong on our side |

## Rate limits

Each API key is limited to 100 requests per minute. Every response includes these headers:

- `X-RateLimit-Limit`: your per-minute allowance
- `X-RateLimit-Remaining`: requests left in the current window
- `X-RateLimit-Reset`: Unix timestamp when the window resets

If you receive a `429`, wait until the reset time before retrying.

## Example: JavaScript

```javascript
const BASE_URL = "https://api.example.com/v1";
const headers = {
  Authorization: `Bearer ${process.env.TODO_API_KEY}`,
  "Content-Type": "application/json",
};

async function createTodo(title) {
  const res = await fetch(`${BASE_URL}/todos`, {
    method: "POST",
    headers,
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error((await res.json()).error.message);
  return res.json();
}

createTodo("Buy groceries").then(console.log);
```

## Example: Python

```python
import os
import requests

BASE_URL = "https://api.example.com/v1"
headers = {"Authorization": f"Bearer {os.environ['TODO_API_KEY']}"}

resp = requests.post(
    f"{BASE_URL}/todos",
    headers=headers,
    json={"title": "Buy groceries"},
)
resp.raise_for_status()
print(resp.json())
```

## Security best practices

- **Keep keys server-side.** Never expose them in browser or mobile app code.
- **Use one key per application** so you can revoke one without affecting others.
- **Rotate keys periodically.** Create a new key, deploy it, then delete the old one.
- **Revoke immediately** if you suspect a key has leaked (Settings → API Keys → Revoke).
- **Always use HTTPS.** Requests over plain HTTP are rejected.

## Next steps

- Browse the full API reference for every endpoint and field
- Set up retries with exponential backoff for `429` and `5xx` responses
- Contact support if you run into problems
