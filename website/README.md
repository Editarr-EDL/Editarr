# editarr.org

Standalone landing page, handbook (`/docs/`), and API reference (`/api/`). All
assets are local; no analytics, third-party fonts, runtime Node dependencies, or
connection to an Editarr installation. Website colors match the app shell palette,
and `public/_headers` keeps a strict CSP (no inline scripts or styles).

## Screenshots

The landing page shows the actual app with synthetic example media from the browser
test fixtures. Screens are PNGs in `public/assets/screens/`: the hero `review.png`
(1440×900) and 1200×800 tour crops such as `review-detail.png`, `library.png`,
`new-scan.png`, and `publish.png`. Clicking a screenshot opens it in an in-page
viewer (`assets/site.js`); without JavaScript the link opens the PNG. Refresh all
of them with:

```sh
EDITARR_CAPTURE_WEBSITE=1 pnpm --dir web exec playwright test website-preview.spec.ts
```

## Cloudflare Workers Static Assets

The `editarr-website` Worker serves `public/` as static assets. The Wrangler
config declares `editarr.org` as the custom domain, and `public/_headers` applies
the site's security headers.

For updates, upload a version from the repository root and run the browser tests
against the version URL printed by Wrangler:

```sh
npx wrangler versions upload --config website/wrangler.toml
EDITARR_WEBSITE_URL=https://your-version.workers.dev \
  pnpm --dir web exec playwright test --config playwright.website.config.ts
```

Check that `/api/` lists the endpoints, `/docs/openapi.json` serves JSON, `/healthz` returns `ok`, and a missing
path returns 404. Once the preview passes, promote the tested version with
`npx wrangler versions deploy --config website/wrangler.toml`.

The zone's Cloudflare Single Redirect sends requests for `www.editarr.org` and
HTTP requests for `editarr.org` to `https://editarr.org` with a 301, preserving
the path and query string. It uses a proxied `www` A record pointing to the
placeholder `192.0.2.1`.

## Run locally or on your own host

From the repository root:

```sh
docker compose -f website/compose.yaml up -d --build
```

Open http://localhost:8088. `EDITARR_SITE_PORT` overrides the host port. The container
runs unprivileged with a read-only filesystem and `/tmp` tmpfs. `/healthz` reports
container health; missing paths return 404. Stop with `docker compose -f
website/compose.yaml down`.

For editarr.org, point DNS to your host and use an HTTPS reverse proxy. A host-level
Caddy example is in `Caddyfile.example`; change its upstream if overriding the port.
The listener defaults to loopback. For a containerized proxy, join its Docker network
and proxy to `website:8080` instead. This image hosts the website, not the scanner.

Without Docker, preview with `python3 -m http.server 8088 --directory website/public`.
This development server does not apply the production security headers.

## Update API documentation

After changing endpoints or schemas, regenerate the public snapshot:

```sh
.venv/bin/python scripts/export_site_openapi.py
```

The exporter uses an isolated temporary data directory and never opens or migrates
an existing installation. The `/api/` reference page renders this snapshot
(`/docs/openapi.json`) in the browser; installed versions expose their own protected
`/openapi.json`.

## Validate

```sh
.venv/bin/python -m pytest -q tests/test_site_contract.py
cd web && pnpm exec playwright test --config playwright.website.config.ts
```
