# Insert stocks — providers, licenses and attribution

Searching and downloading stock frames lives in `core/stock.py`; you can compare what the
providers return for your own queries with the `tools/stock_compare.py` stand.

## Providers and order

The order in `PROVIDERS` is the query priority: the search walks the list until enough
candidates are collected. A provider without a key is simply skipped, so "no key" never
cancels the search at the others.

The order was chosen by a measurement of 2026-10-10: 30 of the owner's queries, results
checked by eye. Pexels and Unsplash almost always give 3–4 relevant frames out of 4.
Pixabay on complex queries pulls in foreign tags (giraffes, COVID covers, motorbikes for
"250"), and when it stood ahead of Unsplash it filled Pexels' shortfall with the worst
frames — so it is now third. Openverse mostly returns junk, and Coverr is a video stock
with the least verified results: both come last. Unsplash in demo mode allows 50 requests
per hour: when it refuses (429 or 403), the search does not fail but moves on to the next
stock, and Pixabay fills the rest.

| Provider | Type | Key | What we take |
|---|---|---|---|
| Pexels | photo, video | required | `per_page` up to 80, orientation from the video format |
| Unsplash | photo | required | photo search, download via `links.download_location` |
| Pixabay | photo, video | required | `per_page` 3–200, `large`, for video `large`/`medium` |
| Openverse | photo | NOT required | only `commercial,modification` licenses |
| Coverr | video | required | behind the `REELSI_STOCK_COVERR` flag; `GET /videos` with `urls=true`, key sent as a `Bearer` header |

Keys live in `ai_config.json`, section `stock`, field `<provider>_key`; they are entered
in the UI: ⚙ → Generation → Stock. As with the AI profile keys, instead of the key itself
you can store an `env:NAME` record — then the value comes from the environment and only
the variable name stays in the config. The key is never handed out: the UI only gets the
`•••xxxx` mask.

Two environment variables control the set of providers:

- `REELSI_STOCK_COVERR=1` — enable Coverr (without it the provider is skipped even if a
  key is present);
- `REELSI_STOCK_OFF=openverse,unsplash` — disable the listed stocks. It is needed above
  all for Openverse: it has no key, and without this door it could neither be turned off
  nor used to check the "no stock at all" refusal.

Search responses are cached for 24 hours in `stock_cache.json` (its own path — the
`REELSI_STOCK_CACHE` variable): Pexels and Pixabay allow this in their terms, and without
the cache a single run over the cards would burn the query limit.

## Licenses

Below is a short note on what is allowed and what we must store. This is not legal advice
and not a substitute for the provider licenses: before commercial use of a particular
frame, read the terms on the frame page.

- **Pexels** — [Pexels license](https://www.pexels.com/license/): free, including
  commercially, modification allowed. You may not sell the frame as is or pass it off as
  your own, and you may not use the people in a photo in a bad light. Attribution is
  welcome but not required; we store it.
- **Pixabay** — [Content License](https://pixabay.com/service/license-summary/): free,
  commercial use and modification allowed. **Hotlinking is forbidden** — that is why the
  file is always downloaded to us. Attribution is not required; we store it.
- **Unsplash** — [Unsplash License](https://unsplash.com/license): free, including
  commercial use, modification allowed. For applications their terms require two things
  beyond the license: **reporting every download** with a request to
  `links.download_location` and storing the author with a link. Without the first the
  application key is revoked — that is why the download goes in two steps, and the
  reporting call is repeated even when the file is already in the library.
- **Openverse** — an aggregator, every frame has its own Creative Commons license or is
  in the public domain. We ask the API only for `license_type=commercial,modification`
  (commercial use and modification) and additionally check the response ourselves:
  licenses with `NC` (non-commercial) and `ND` (no derivatives) are not suitable for an
  insert in a video. `CC0`, `PDM`, `BY`, `BY-SA` are suitable; the license version is
  stored.
- **Coverr** — [Coverr license](https://coverr.co/license): free, including commercial
  use, modification allowed, attribution not required (we store it anyway). Forbidden:
  reselling the videos or offering them as part of a service where the video is the
  product (a stock site, a site builder, a theme, an app), building a competing service,
  using them to train AI or as a dataset. Coverr does not clear brands or identifiable
  landmarks in a frame — that is the user's responsibility.

## What is stored: the license file

Next to every downloaded file a `<name>.license.json` is written — a year later it shows
where the frame came from and on what terms it was taken. It holds: provider, id, type,
author and author link, frame page link, download address, **license**, source (for
Openverse — where the frame came from), tags, an attribution line and the download date
in UTC. The author and the license also travel into the library index record: the
auto-match finds the frame by it in later videos.

The downloaded file goes through `insertlib.to_ae_media`: the stocks serve AV1 and
`.webp`, which After Effects does not import at all. The files land in
`<library folder>/stock/<provider>/`.

## What to verify with live keys

The sandbox in which the code was written has no network, so part of the contracts is
confirmed only by documentation and by the recorded responses of the tests. Before
relying on a provider in production, check:

- **Unsplash**: that the `download_location` response contains a `url` field and that it
  downloads exactly that frame; that `orientation=squarish` is accepted for a square
  video.
- **Openverse**: that the API understands `license_type=commercial,modification` and
  `aspect_ratio=tall|wide|square`; that the `license` field arrives both as a short
  record (`cc0`, `by-sa`) and as an address — the parser tolerates both forms.
- **Coverr**: the search schema (`GET /videos`, `query`, `urls=true`, list in `hits`) and
  the `Authorization: Bearer` key — per their documentation (`api.coverr.co/docs`). Not
  confirmed: the `urls.mp4` and `urls.mp4_download` links contain a `{token}` placeholder,
  and the documentation does not explain how to obtain it. Such links are skipped now, so
  without a token substitution Coverr yields no frames at all. Also check whether the
  response carries author and license fields — the documentation does not list them, and
  without them the license file says "not stated in the API response". The provider stays
  off until this is verified; it is enabled only by `REELSI_STOCK_COVERR=1`.

The comparison stand (`tools/stock_compare.py`) exists exactly for such a check: it shows
the output of all stocks for the same queries without writing anything into the insert
library and without downloading the originals.

## Query simplification

The query for a stock is set by the AI prompt (`core/aicut/prompts.py`): English, the object and one or two
visible features, without numbers, doses, units, labels or abstractions ("health", "success") — a stock
picture does not show them, and the stock pulls in other pictures by their tags ("syringe with small 250
mark" on Pixabay gave motorcycles). Earlier, AI inserts phrased their query as a long English sentence ("concept of a copper pot on a
wooden table"), while a stock matches by words — a long sentence blurs the output.
`core.stock.simplify_query` drops the function words and keeps the subject with one or two
attributes. **The rule is NOT enabled in the production search yet**: the card UI calls
`stock.search` with the original query, and only the comparison stand shows the simplified
variant. On 18 of the owner's queries the short variant was no better and lost the object, so it is not enabled in
production. Enabling it is a separate decision.
