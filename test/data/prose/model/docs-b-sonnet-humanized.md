# Getting Started with the Tasks API

This guide takes you from nothing to a working request in a few minutes. You will get an API key, create a to-do item, read it back, change it, and delete it. The examples use curl, but any HTTP client will do.

## Base URL and format

All requests go to:

```
https://api.example.com/v1
```

The API speaks JSON. Send `Content-Type: application/json` on any request with a body, and expect JSON in every response. Requests must use HTTPS; plain HTTP is refused.

## Getting an API key

Sign in to the dashboard, open Settings, and choose API Keys. Click Create Key and give it a name that tells you where it will be used, such as "home-server" or "phone-shortcut". The key is displayed once. Copy it somewhere safe before you close the dialog, because the dashboard stores only a hash and cannot show it to you again. If you lose it, revoke it and make a new one.

Keys are tied to your account and can read and write all of your items. Treat one the way you would treat a password. Keep it out of source control, load it from an environment variable, and never put it in client-side code that other people can inspect.

For the examples below, set it in your shell:

```
export API_KEY="tk_live_your_key_here"
```

## Authenticating requests

Send the key in the `Authorization` header with the `Bearer` scheme:

```
curl https://api.example.com/v1/todos \
  -H "Authorization: Bearer $API_KEY"
```

A missing or malformed header returns `401 Unauthorized`, as does a key that has been revoked. The response body says which problem it was:

```
{
  "error": {
    "code": "invalid_api_key",
    "message": "The API key provided is not valid."
  }
}
```

If you see this on a request that worked yesterday, check whether someone revoked the key in the dashboard.

## Creating a to-do item

Send a `POST` to `/todos`. Only `title` is required.

```
curl -X POST https://api.example.com/v1/todos \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Renew passport",
    "notes": "Photo must be less than six months old",
    "due_date": "2026-12-01"
  }'
```

The API responds with `201 Created` and the new item:

```
{
  "id": "td_8f3k2m9",
  "title": "Renew passport",
  "notes": "Photo must be less than six months old",
  "due_date": "2026-12-01",
  "completed": false,
  "created_at": "2026-10-30T09:14:22Z",
  "updated_at": "2026-10-30T09:14:22Z"
}
```

Dates in `due_date` use the `YYYY-MM-DD` format. Timestamps are UTC and follow ISO 8601. The `id` is what you will use to refer to this item from now on.

## Listing items

A `GET` to `/todos` returns your items, newest first:

```
curl https://api.example.com/v1/todos \
  -H "Authorization: Bearer $API_KEY"
```

```
{
  "data": [
    {
      "id": "td_8f3k2m9",
      "title": "Renew passport",
      "completed": false,
      "due_date": "2026-12-01"
    }
  ],
  "next_cursor": null
}
```

Results come in pages of 25. When more are available, `next_cursor` holds a string; pass it back as the `cursor` query parameter to get the next page. A `null` cursor means you have reached the end. You can change the page size with `limit`, up to 100.

To narrow the list, add filters to the query string. `completed=false` returns only open items, and `due_before=2026-11-15` returns items due before that date. Filters combine, so this request returns open items due soon:

```
curl "https://api.example.com/v1/todos?completed=false&due_before=2026-11-15" \
  -H "Authorization: Bearer $API_KEY"
```

## Fetching a single item

```
curl https://api.example.com/v1/todos/td_8f3k2m9 \
  -H "Authorization: Bearer $API_KEY"
```

If the ID does not exist, or belongs to another account, you get `404 Not Found`. The API deliberately does not distinguish between the two.

## Updating an item

Use `PATCH` to change specific fields. Anything you leave out stays as it was.

```
curl -X PATCH https://api.example.com/v1/todos/td_8f3k2m9 \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"completed": true}'
```

The response is the full updated item, with a new `updated_at` value. To clear an optional field such as `due_date`, set it to `null`.

## Deleting an item

```
curl -X DELETE https://api.example.com/v1/todos/td_8f3k2m9 \
  -H "Authorization: Bearer $API_KEY"
```

A successful delete returns `204 No Content` and an empty body. Deletion is permanent. If you only want an item out of your open list, mark it completed and filter it out instead.

## Errors

Errors use standard HTTP status codes and the same body shape as the authentication example above, with a machine-readable `code` and a human-readable `message`. The ones you are most likely to meet:

- `400 Bad Request`: the body is not valid JSON, or a field has the wrong type or format. The message names the field.
- `401 Unauthorized`: the API key is missing, malformed, or revoked.
- `404 Not Found`: no item with that ID exists in your account.
- `422 Unprocessable Entity`: the JSON is well formed but fails validation, for example an empty `title` or a title longer than 200 characters.
- `429 Too Many Requests`: you have exceeded the rate limit.

## Rate limits

Each key can make 60 requests per minute. Every response includes `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset` headers, the last of which is a Unix timestamp for when your allowance refills. A `429` response also carries a `Retry-After` header giving the number of seconds to wait. If you are importing a lot of items at once, spread the requests out rather than retrying in a tight loop.

## Rotating a key

To replace a key without downtime, create the new one first, deploy it wherever the old one is used, confirm that requests succeed, and only then revoke the old key from the dashboard. Revocation takes effect within a few seconds.
