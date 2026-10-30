# Getting started with the To-Do API

Use the To-Do API to create, view, update, and delete to-do items. All requests require an API key.

This guide uses an illustrative API contract. Replace the base URL and API key with the values provided by your API service.

## 1. Set up authentication

Store your API key in an environment variable:

```bash
export TODO_API_KEY="your-api-key"
export TODO_API_URL="https://api.example.com/v1"
```

Send the key in the `X-API-Key` header with every request. Keep it private and make requests from a trusted server rather than browser code.

## 2. Create a to-do item

Send a `POST` request with a title:

```bash
curl "$TODO_API_URL/todos" \
  -X POST \
  -H "X-API-Key: $TODO_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title":"Buy groceries"}'
```

A successful request returns `201 Created` and the new item:

```json
{
  "id": "todo_123",
  "title": "Buy groceries",
  "completed": false,
  "created_at": "2026-10-30T10:00:00Z"
}
```

Save the `id` to retrieve, update, or delete the item.

## 3. List your to-do items

```bash
curl "$TODO_API_URL/todos" \
  -H "X-API-Key: $TODO_API_KEY"
```

Response: `200 OK`

```json
{
  "items": [
    {
      "id": "todo_123",
      "title": "Buy groceries",
      "completed": false,
      "created_at": "2026-10-30T10:00:00Z"
    }
  ]
}
```

To retrieve a single item:

```bash
curl "$TODO_API_URL/todos/todo_123" \
  -H "X-API-Key: $TODO_API_KEY"
```

## 4. Mark an item as complete

Use `PATCH` to change individual fields:

```bash
curl "$TODO_API_URL/todos/todo_123" \
  -X PATCH \
  -H "X-API-Key: $TODO_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"completed":true}'
```

Response: `200 OK` with the updated item. You can also change its title by sending a `title` field.

## 5. Delete an item

```bash
curl "$TODO_API_URL/todos/todo_123" \
  -X DELETE \
  -H "X-API-Key: $TODO_API_KEY"
```

A successful deletion returns `204 No Content` with no response body.

## Endpoint reference

| Method | Endpoint | Action |
|---|---|---|
| `POST` | `/todos` | Create an item |
| `GET` | `/todos` | List items |
| `GET` | `/todos/{id}` | Retrieve an item |
| `PATCH` | `/todos/{id}` | Update an item |
| `DELETE` | `/todos/{id}` | Delete an item |

## Handle errors

Failed requests return an HTTP status code and a JSON error body:

```json
{
  "error": {
    "code": "invalid_api_key",
    "message": "Provide a valid API key in the X-API-Key header."
  }
}
```

| Status | Meaning | What to do |
|---|---|---|
| `400 Bad Request` | Invalid request data | Check the JSON body and field values |
| `401 Unauthorized` | Missing or invalid API key | Check the `X-API-Key` header |
| `404 Not Found` | Item does not exist | Check the item ID |
| `429 Too Many Requests` | Rate limit exceeded | Wait before retrying; follow `Retry-After` if provided |
| `500 Internal Server Error` | Server error | Retry later |

You have now completed the basic workflow: create an item, retrieve it, mark it complete, and delete it.
