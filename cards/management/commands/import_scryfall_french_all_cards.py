import json
from pathlib import Path

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from cards.management.commands.import_scryfall_bulk import extract_printed_values, parse_date
from cards.models import CardOracle, CardPrinting, Set

SCRYFALL_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "cube-mtg-analyzer/0.1",
}


class Command(BaseCommand):
    help = "Import French localized printings from Scryfall all_cards bulk data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=Path,
            help="Path to a local Scryfall all_cards JSON file.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Import only the first N French cards, useful for development checks.",
        )

    def handle(self, *args, **options):
        cards = self._load_cards(options["file"])
        limit = options["limit"]
        imported_printings = 0
        updated_printings = 0
        skipped = 0
        processed = 0

        for card in cards:
            if card.get("lang") != "fr":
                continue
            processed += 1
            if limit and processed > limit:
                break
            if not card.get("oracle_id"):
                skipped += 1
                continue

            oracle = CardOracle.objects.filter(scryfall_oracle_id=card["oracle_id"]).first()
            if oracle is None:
                skipped += 1
                continue

            with transaction.atomic():
                card_set = self._upsert_set(card)
                _, created = self._upsert_french_printing(card, oracle, card_set)

            imported_printings += int(created)
            updated_printings += int(not created)

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {imported_printings} and updated {updated_printings} French printings; skipped {skipped}."
            )
        )

    def _load_cards(self, source_file):
        if source_file is not None:
            if not source_file.exists():
                raise CommandError(f"File not found: {source_file}")
            with source_file.open(encoding="utf-8") as handle:
                return json.load(handle)

        bulk_index = requests.get("https://api.scryfall.com/bulk-data", headers=SCRYFALL_HEADERS, timeout=30)
        bulk_index.raise_for_status()
        entry = next(
            (item for item in bulk_index.json().get("data", []) if item.get("type") == "all_cards"),
            None,
        )
        if entry is None:
            raise CommandError("Scryfall bulk type not found: all_cards")

        self.stdout.write("Downloading all_cards from Scryfall...")
        response = requests.get(entry["download_uri"], headers=SCRYFALL_HEADERS, timeout=600)
        response.raise_for_status()
        return response.json()

    def _upsert_set(self, card):
        card_set, _ = Set.objects.update_or_create(
            code=card["set"],
            defaults={
                "name": card.get("set_name", card["set"]),
                "released_at": parse_date(card.get("released_at")),
                "set_type": card.get("set_type", ""),
            },
        )
        return card_set

    def _upsert_french_printing(self, card, oracle, card_set):
        printed_values = extract_printed_values(card)
        return CardPrinting.objects.update_or_create(
            scryfall_id=card["id"],
            defaults={
                "oracle": oracle,
                "set": card_set,
                "set_code": card.get("set", ""),
                "collector_number": card.get("collector_number", ""),
                "rarity": card.get("rarity", ""),
                "image_url": extract_image_url(card),
                "released_at": parse_date(card.get("released_at")),
                "lang": "fr",
                "printed_name": printed_values["name"],
                "printed_type_line": printed_values["type_line"],
                "printed_oracle_text": printed_values["oracle_text"],
            },
        )


def extract_image_url(card):
    if card.get("image_uris"):
        return card["image_uris"].get("normal") or card["image_uris"].get("large") or ""
    if card.get("card_faces"):
        first_face_images = card["card_faces"][0].get("image_uris") or {}
        return first_face_images.get("normal") or first_face_images.get("large") or ""
    return ""
