# Getting Started with the To-Do API

This guide shows you how to authenticate, make your first request, and manage to-do items through the REST API.

## 1. Get an API key

Request an API key from your account dashboard. Keys look like this:

```
tdk_live_4f9c2a7e8b1d6f03a5c9e2d7b8a1f456
```

Treat your key like a password:

- Don't commit it to source control or put it in client-side code.
- Store it in an environment variable or a secrets manager.
- Rotate it immediately if it is exposed.

Export it in your shell for the examples below:

```bash
export TODO_API_KEY="tdk_live_your_key_here"
```

## 2. Base URL and format

- **Base URL:** `https://api.example.com/v1`
- **Format:** All request and response bodies are JSON. Send `Content-Type: application/json` on requests that have a body.
- **Timestamps:** ISO 8601 in UTC, for example `2026-10-30T14:05:00Z`.

## 3. Authenticate every request

Send your key in the `X-API-Key` header:

```http
X-API-Key: tdk_live_your_key_here
```

Requests without a valid key receive `401 Unauthorized`.

## 4. Make your first request

Check that your key works by listing your to-do items:

```bash
curl https://api.example.com/v1/todos \
  -H "X-API-Key: $TODO_API_KEY"
```

A successful response returns an empty list if you have no items yet:

```json
{
  "data": [],
  "meta": { "total": 0 }
}
```

## 5. The to-do object

Every to-do item has the following fields:

| Field        | Type            | Description                                      |
|--------------|-----------------|--------------------------------------------------|
| `id`         | string          | Unique identifier, assigned by the server        |
| `title`      | string          | Required. 1 to 200 characters.                   |
| `notes`      | string or null  | Optional. Up to 2,000 characters.                |
| `due_date`   | string or null  | Optional. A date in `YYYY-MM-DD` format.         |
| `completed`  | boolean         | Defaults to `false`.                             |
| `created_at` | string          | Set by the server when the item is created.      |
| `updated_at` | string          | Set by the server when the item is last changed. |

## 6. Endpoints

| Method   | Path              | Purpose                          |
|----------|-------------------|----------------------------------|
| `GET`    | `/todos`          | List your to-do items            |
| `POST`   | `/todos`          | Create a to-do item              |
| `GET`    | `/todos/{id}`     | Get one to-do item               |
| `PATCH`  | `/todos/{id}`     | Update one or more fields        |
| `DELETE` | `/todos/{id}`     | Delete a to-do item              |

### Create a to-do item

```bash
curl -X POST https://api.example.com/v1/todos \
  -H "X-API-Key: $TODO_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "title": "Write the getting-started guide",
        "notes": "Cover authentication first",
        "due_date": "2026-11-05"
      }'
```

Response (`201 Created`):

```json
{
  "id": "todo_8f3k2m",
  "title": "Write the getting-started guide",
  "notes": "Cover authentication first",
  "due_date": "2026-11-05",
  "completed": false,
  "created_at": "2026-10-30T14:05:00Z",
  "updated_at": "2026-10-30T14:05:00Z"
}
```

### Mark an item as complete

`PATCH` changes only the fields you send:

```bash
curl -X PATCH https://api.example.com/v1/todos/todo_8f3k2m \
  -H "X-API-Key: $TODO_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{ "completed": true }'
```

### Delete an item

```bash
curl -X DELETE https://api.example.com/v1/todos/todo_8f3k2m \
  -H "X-API-Key: $TODO_API_KEY"
```

A successful delete returns `204 No Content` with an empty body.

### Filter and paginate the list

`GET /todos` accepts these query parameters:

| Parameter   | Example                | Effect                                  |
|-------------|------------------------|-----------------------------------------|
| `completed` | `?completed=false`     | Return only open or only finished items |
| `due_before`| `?due_before=2026-11-01` | Return items due before a date        |
| `limit`     | `?limit=25`            | Page size. Default 20, maximum 100.     |
| `cursor`    | `?cursor=eyJpZCI6...`  | Pass the `next_cursor` value from the previous page |

Example:

```bash
curl "https://api.example.com/v1/todos?completed=false&limit=25" \
  -H "X-API-Key: $TODO_API_KEY"
```

## 7. Use it from Python

If you prefer code to curl, this example uses the `requests` library:

```python
import os
import requests

BASE_URL = "https://api.example.com/v1"
HEADERS = {
    "X-API-Key": os.environ["TODO_API_KEY"],
    "Content-Type": "application/json",
}

# Create an item
resp = requests.post(
    f"{BASE_URL}/todos",
    headers=HEADERS,
    json={"title": "Review pull requests"},
)
resp.raise_for_status()
todo = resp.json()
print("Created:", todo["id"])

# Mark it complete
resp = requests.patch(
    f"{BASE_URL}/todos/{todo['id']}",
    headers=HEADERS,
    json={"completed": True},
)
resp.raise_for_status()
print("Completed:", resp.json()["completed"])
```

## 8. Handle errors

Errors use standard HTTP status codes and return a JSON body:

```json
{
  "error": {
    "code": "validation_failed",
    "message": "title is required",
    "request_id": "req_7d2f9a"
  }
}
```

| Status | Meaning                                  | What to do                                         |
|--------|------------------------------------------|----------------------------------------------------|
| `400`  | Malformed JSON                           | Check the request body syntax.                     |
| `401`  | Missing or invalid API key               | Confirm the header name and key value.             |
| `404`  | Item not found, or it belongs to another account | Check the ID.                             |
| `422`  | Validation failed                        | Read `error.message` and fix the field it names.   |
| `429`  | Rate limit exceeded                      | Wait for the `Retry-After` interval, then retry.   |
| `500`  | Server error                             | Retry with exponential backoff. Include `request_id` if you contact support. |

Rate limits are 600 requests per minute per API key. Each response includes `X-RateLimit-Remaining` so you can track your usage.

## 9. Next steps

- Store your key in a secrets manager before deploying anything.
- Use `limit` and `cursor` when you list items, so large accounts don't return one huge response.
- Send a `PATCH` instead of a `PUT` to change one field. The API has no `PUT` endpoint.
- Keep requests to the 600-per-minute limit by caching reads where you can.
