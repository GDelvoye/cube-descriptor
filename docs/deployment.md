# Deployment

This project has two Docker Compose environments: development and production.

## Development

Development is defined by `docker-compose.yml`.

```bash
docker compose up -d --build
```

Services:

| Service | Container | Purpose |
| --- | --- | --- |
| `web` | `cube-web-1` | Django development server |
| `db` | `cube-db-1` | PostgreSQL development database |

Development URL:

```text
http://localhost:8010/
```

## Production

Production is defined by `docker-compose.prod.yml` and uses the Compose project name `cube-prod`.

```bash
docker compose -p cube-prod -f docker-compose.prod.yml up -d --build
```

Services:

| Service | Container | Purpose |
| --- | --- | --- |
| `web` | `cube-prod-web-1` | Django served by Gunicorn |
| `db` | `cube-prod-db-1` | PostgreSQL production database |

Production URLs:

```text
https://cube.gdelvoye.fr/
http://127.0.0.1:8011/  # internal only, behind Caddy
```

The production web container is bound to `127.0.0.1:8011`, so it is reachable by Caddy on the server but not directly exposed publicly.

The production database is not published on a host port. It is reachable only from containers on the `cube-prod` Docker network.

## Branch Workflow

`main` is the deployable branch and should match what is intended to run in production.

Future development should happen on short-lived branches, for example:

```bash
git switch -c dev/my-feature
```

Before merging or pushing changes to `main`:

```bash
uv run ruff format --check .
uv run ruff check .
DATABASE_URL=postgres://cube:cube@localhost:5450/cube uv run python manage.py test
```

After `main` is updated and pushed, rebuild production explicitly:

```bash
docker compose -p cube-prod -f docker-compose.prod.yml up -d --build
```

Avoid deploying directly from uncommitted work. If production is rebuilt from uncommitted changes during an emergency, commit and push that exact state as soon as possible.

## Production Environment

Production secrets and settings live in `.env.prod`.

This file must not be committed.

Required values:

```env
DEBUG=False
SECRET_KEY=...
ALLOWED_HOSTS=cube.gdelvoye.fr
CSRF_TRUSTED_ORIGINS=https://cube.gdelvoye.fr
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_SSL_REDIRECT=False

POSTGRES_DB=cube_prod
POSTGRES_USER=cube_prod
POSTGRES_PASSWORD=...
DATABASE_URL=postgres://cube_prod:...@db:5432/cube_prod
```

`SECURE_SSL_REDIRECT` is kept false because Caddy already redirects HTTP to HTTPS before proxying requests to Django.

## Caddy

Caddy terminates HTTPS and proxies public traffic to the production web container.

Expected Caddy route:

```caddyfile
cube.gdelvoye.fr {
    reverse_proxy 127.0.0.1:8011
}
```

## Static Files

Production runs:

```bash
python manage.py collectstatic --noinput
```

before starting Gunicorn. Static files are served by WhiteNoise from inside the Django app.

## Copy Development Database To Production

To copy the current development database into production:

```bash
docker exec cube-db-1 pg_dump -U cube -d cube --no-owner --no-acl | docker exec -i cube-prod-db-1 psql -U cube_prod -d cube_prod
```

This should only be used intentionally, because it writes development data into the production database.

## Useful Commands

```bash
docker compose -p cube-prod -f docker-compose.prod.yml ps
docker compose -p cube-prod -f docker-compose.prod.yml logs --since 5m
curl -I https://cube.gdelvoye.fr/
curl -I https://cube.gdelvoye.fr/admin/login/
```

## Security Follow-Ups

Recommended next hardening steps:

- Bind development ports to `127.0.0.1` instead of `0.0.0.0` where possible.
- Avoid exposing development Postgres on the host unless needed.
- Review the Debian firewall before enabling a default-deny policy, because this server hosts multiple services.
- Keep router NAT/PAT limited to ports `80` and `443` for this deployment.
