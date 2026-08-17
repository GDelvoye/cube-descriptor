# Cube MTG Analyzer

Application Django pour designer des cubes Magic: The Gathering et calculer des probabilites de boosters.

## Demarrage local

```bash
cp .env.example .env
docker compose up --build
```

La page d'accueil est disponible sur `http://localhost:8010/`.

L'admin Django est disponible sur `http://localhost:8010/admin/`.

La recherche cartes est disponible sur `http://localhost:8010/cards/`.

Les cubes utilisateur sont disponibles sur `http://localhost:8010/cubes/` apres connexion.

Les stats d'un cube permettent de creer des requetes sauvegardees reutilisables, par exemple `tag:removal`, `power=2 AND keyword:Flying` ou `color:U AND type:Creature`.

## Commandes utiles

```bash
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py createsuperuser
docker compose run --rm web python manage.py import_scryfall_bulk --limit 1000
```

## Deploiement

Le deploiement public de production est documente dans [`docs/deployment.md`](docs/deployment.md).

Resume :

- dev : `docker compose up -d --build`, disponible sur `http://localhost:8010/` ;
- prod : `docker compose -p cube-prod -f docker-compose.prod.yml up -d --build`, disponible publiquement sur `https://cube.gdelvoye.fr/` via Caddy ;
- la prod utilise Gunicorn, WhiteNoise, `DEBUG=False`, une DB Postgres separee, et une configuration `.env.prod` locale non commitee.

Pour importer un fichier Scryfall Bulk local :

```bash
docker compose run --rm web python manage.py import_scryfall_bulk --file /app/default-cards.json
```

## Cache des stats

Les boites a moustache des pages stats comparent un cube ou une extension a l'historique des sets officiels. Pour eviter de reparcourir toutes les printings officielles a chaque affichage, l'application stocke des valeurs intermediaires en base :

- `stats_statquerymatch` : cartes qui matchent une requete sauvegardee.
- `stats_statquerydependency` : dependances entre requetes via `query:` et `query_id:`.
- `stats_setindicatorexpectedvalue` : esperance par set officiel et par indicateur.

Les recalculs sont synchrones pour le moment. Ils sont declenches automatiquement quand une requete est creee ou modifiee via l'UI ou l'admin, et aussi en fallback si une page stats a besoin d'un cache absent.

Apres un import Scryfall, lancer explicitement :

```bash
docker compose run --rm web python manage.py refresh_set_indicator_values --source all
```

Commandes utiles :

```bash
docker compose run --rm web python manage.py refresh_stat_query_cache
docker compose run --rm web python manage.py refresh_stat_query_cache --query-id 12
docker compose run --rm web python manage.py refresh_set_indicator_values --source code
docker compose run --rm web python manage.py refresh_set_indicator_values --source query
```

Celery n'est pas utilise actuellement. L'interet serait de deplacer les recalculs longs hors des requetes web : creation/modification de requetes, recalcul complet apres import Scryfall, retries en cas d'erreur temporaire, et planification nocturne avec Celery Beat. Pour l'instant, le compromis retenu est plus simple : cache en base, commandes explicites, et eventuellement un cron systeme apres les imports. Celery pourra etre ajoute plus tard si les recalculs deviennent trop longs ou comme sujet d'apprentissage.
