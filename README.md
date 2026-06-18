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

## Commandes utiles

```bash
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py createsuperuser
docker compose run --rm web python manage.py import_scryfall_bulk --limit 1000
```

Pour importer un fichier Scryfall Bulk local :

```bash
docker compose run --rm web python manage.py import_scryfall_bulk --file /app/default-cards.json
```
