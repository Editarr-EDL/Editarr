# editarr.org

Standalone landing page and documentation. All assets are local; no analytics,
third-party fonts, runtime Node dependencies, or connection to an Editarr installation.
The product preview shows the actual app with synthetic example media from the browser
test fixtures. Refresh it with `EDITARR_CAPTURE_WEBSITE=1 pnpm --dir web exec
playwright test website-preview.spec.ts`. Website colors match the app shell palette.

## Run

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
an existing installation. The searchable reference resolves schema definitions from
this snapshot; installed versions expose their own protected `/openapi.json`.

## Validate

```sh
.venv/bin/python -m pytest -q tests/test_site_contract.py
cd web && pnpm exec playwright test --config playwright.website.config.ts
```
