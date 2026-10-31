# Source policies

Scripts call free public endpoints from the user's own machine. The ones that call a service themselves
(`cite-check`, `hackernews`, `podcast-search`, `scholar-search`, `telegram-channel`, `wayback`, `weather`,
`web-extract` and `wiki-lookup`) send a `utility-belt-<skill>` User-Agent that links to this repository,
and `web-browse` adds `web-browse/1.0` to a headless browser's. They stay polite on purpose:

- **Apple iTunes Search API** (`podcast-search`): about 20 requests a minute per IP; the script throttles
  itself across runs.
- **Internet Archive** (`wayback`): the Availability, CDX and Save Page Now endpoints rate-limit early and
  unannounced. The script keeps to about one request a second across runs, honours `Retry-After`, retries
  once and then stops. It saves a page only when asked, because a save publishes a public copy.
- **Scholarly APIs** (`cite-check`, `scholar-search`): Crossref (polite pool with the user's email), arXiv (one
  request every 3 s, as its terms ask; `scholar-search` keeps that spacing across runs on the machine), NCBI
  E-utilities (3 per second without a key), Europe PMC, doi.org and Unpaywall (needs a real email). Requests are
  spaced per host and carry the user's email when given.
- **Wikimedia** (`wiki-lookup`): the REST and Action APIs of Wikipedia and Wikidata, one request at a time,
  with the User-Agent the [policy](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy)
  asks for. The script backs off once on 429 or 503 and then stops. Text is CC BY-SA 4.0, and Wikidata is CC0.
- **Open-Meteo** (`weather`): the free API is for **non-commercial use only** (600 calls a minute, 5,000 an hour,
  10,000 a day, 300,000 a month); its data is CC BY 4.0, and the output credits it. A commercial user sets `OPEN_METEO_API_KEY`.
  Addresses and landmarks fall back to OpenStreetMap Nominatim: one request per lookup, within its 1-a-second
  policy, credited as ODbL.
- **Hacker News** (`hackernews`): the official Firebase API and Algolia HN Search, both key-less. HN Search allows
  10,000 requests an hour per IP; the script spaces them 0.4 s apart. Lists read at most 8 items in parallel
  from Firebase.
- **Telegram web preview** (`telegram-channel`): `t.me/s/<channel>` is a public HTML page, not an API.
  Telegram publishes no terms for reading it automatically, so the script reads public channels only,
  spaces its requests 1.5 s apart and never downloads media. It cannot see private channels, groups,
  comments or member lists, and must not be used to try.
