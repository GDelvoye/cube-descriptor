# Cube MTG Analyzer

Cube MTG Analyzer is a deployed Django application for building Magic: The Gathering cubes, searching card data, and analyzing booster probabilities from custom statistical queries.

Live application: [https://cube.gdelvoye.fr/](https://cube.gdelvoye.fr/)

## Why This Project Exists

Magic cube design is a data-heavy hobby: a cube owner needs to manage card lists, classify cards by role, compare archetype density, and estimate how often specific effects appear in draft boosters. This project turns those questions into a web application backed by a PostgreSQL card database and a custom query engine.

## Features

- Build and manage user-owned cubes with card quantities, sections, tags, notes, and visibility rules.
- Search Scryfall-backed card data with English and French localized printing support.
- Create reusable statistical queries such as `tag:removal`, `power=2 AND keyword:Flying`, or `color:U AND type:Creature`.
- Analyze booster probabilities with hypergeometric calculations and rarity-aware slots.
- Compare cube indicators against historical official Magic sets.
- Cache expensive query matches and set indicator benchmarks in PostgreSQL.
- Run locally with Docker Compose and deploy with a separate production Compose stack.

## Tech Stack

- Backend: Django 5, Python 3.12
- Database: PostgreSQL 16
- Runtime: Docker Compose, Gunicorn, WhiteNoise
- Tooling: uv, Ruff, Django test runner
- Deployment: self-hosted production behind Caddy at `cube.gdelvoye.fr`

## Architecture Highlights

- `cards/`: Scryfall import, card oracle data, printings, localized display, card search.
- `cubes/`: cube ownership, visibility, card list management, cube detail and stats views.
- `stats/`: custom query parser, saved stat queries, probability calculations, cache refresh commands.
- `templates/`: server-rendered Django UI.
- `docs/deployment.md`: production deployment and operational notes.

## Local Development

Create an environment file and start the application:

```bash
cp .env.example .env
docker compose up -d --build
```

Useful local URLs:

- Home: `http://localhost:8010/`
- Django admin: `http://localhost:8010/admin/`
- Card search: `http://localhost:8010/cards/`
- Cubes: `http://localhost:8010/cubes/`

Run common Django commands:

```bash
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py createsuperuser
docker compose run --rm web python manage.py import_scryfall_bulk --limit 1000
```

Import a local Scryfall bulk file:

```bash
docker compose run --rm web python manage.py import_scryfall_bulk --file /app/default-cards.json
```

## Quality Checks

Format and lint:

```bash
uv run ruff format --check .
uv run ruff check .
```

Run the Django test suite against the local development database:

```bash
DATABASE_URL=postgres://cube:cube@localhost:5450/cube uv run python manage.py test
```

The tests cover card localization, cube visibility rules, query parsing, saved query caching, and probability/stat indicator calculations.

## Production Deployment

Production uses a dedicated Compose file and project name:

```bash
docker compose -p cube-prod -f docker-compose.prod.yml up -d --build
```

Production characteristics:

- Django served by Gunicorn.
- Static files collected at container startup and served by WhiteNoise.
- PostgreSQL runs in a private Docker network without a published host port.
- The web container binds to `127.0.0.1:8011` and is reverse-proxied by Caddy.
- Secrets and production settings live in a local `.env.prod` file that is not committed.

See [`docs/deployment.md`](docs/deployment.md) for operational details.

## Stats Cache

Some stats pages compare a cube or a set with historical official sets. To avoid recomputing every printing on each request, the application stores intermediate results in PostgreSQL:

- `stats_statquerymatch`: cards matching saved statistical queries.
- `stats_statquerydependency`: dependencies between saved queries via `query:` and `query_id:`.
- `stats_setindicatorexpectedvalue`: expected values by official set and indicator.

Refresh commands:

```bash
docker compose run --rm web python manage.py refresh_stat_query_cache
docker compose run --rm web python manage.py refresh_stat_query_cache --query-id 12
docker compose run --rm web python manage.py refresh_set_indicator_values --source all
```

Celery is intentionally not used yet. The current tradeoff keeps the system simple with database caching, explicit refresh commands, and room for a future background worker if refresh times become too long.
