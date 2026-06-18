import json
from datetime import date
from pathlib import Path

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from cards.models import CardOracle, CardPrinting, Set

SCRYFALL_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "cube-mtg-analyzer/0.1",
}


class Command(BaseCommand):
    help = "Import Scryfall default_cards bulk data into CardOracle, CardPrinting and Set."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=Path,
            help="Path to a local Scryfall default_cards JSON file.",
        )
        parser.add_argument(
            "--bulk-type",
            default="default_cards",
            help="Scryfall bulk type to download when --file is not provided.",
        )
        parser.add_argument(
            "--include-non-english",
            action="store_true",
            help="Import every language. By default only English and French cards are imported.",
        )
        parser.add_argument(
            "--languages",
            default="en,fr",
            help="Comma-separated languages to import unless --include-non-english is set.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Import only the first N cards, useful for development checks.",
        )

    def handle(self, *args, **options):
        source_file = options["file"]
        include_non_english = options["include_non_english"]
        languages = {language.strip() for language in options["languages"].split(",") if language.strip()}
        limit = options["limit"]

        if source_file is None:
            cards = self._download_bulk_cards(options["bulk_type"])
        else:
            if not source_file.exists():
                raise CommandError(f"File not found: {source_file}")
            with source_file.open(encoding="utf-8") as handle:
                cards = json.load(handle)

        if limit:
            cards = cards[:limit]

        imported_oracles = 0
        imported_printings = 0
        skipped = 0

        for card in cards:
            if not include_non_english and card.get("lang") not in languages:
                skipped += 1
                continue
            if not card.get("oracle_id"):
                skipped += 1
                continue

            with transaction.atomic():
                card_set = self._upsert_set(card)
                oracle, oracle_created = self._upsert_oracle(card)
                _, printing_created = self._upsert_printing(card, oracle, card_set)

            imported_oracles += int(oracle_created)
            imported_printings += int(printing_created)

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {imported_oracles} new oracles and {imported_printings} new printings; skipped {skipped}."
            )
        )

    def _download_bulk_cards(self, bulk_type):
        bulk_index = requests.get("https://api.scryfall.com/bulk-data", headers=SCRYFALL_HEADERS, timeout=30)
        bulk_index.raise_for_status()
        entry = next(
            (item for item in bulk_index.json().get("data", []) if item.get("type") == bulk_type),
            None,
        )
        if entry is None:
            raise CommandError(f"Scryfall bulk type not found: {bulk_type}")

        self.stdout.write(f"Downloading {bulk_type} from Scryfall...")
        response = requests.get(entry["download_uri"], headers=SCRYFALL_HEADERS, timeout=120)
        response.raise_for_status()
        return response.json()

    def _upsert_set(self, card):
        released_at = parse_date(card.get("released_at"))
        card_set, _ = Set.objects.update_or_create(
            code=card["set"],
            defaults={
                "name": card.get("set_name", card["set"]),
                "released_at": released_at,
                "set_type": card.get("set_type", ""),
            },
        )
        return card_set

    def _upsert_oracle(self, card):
        face_values = extract_face_values(card)
        defaults = {
            "name": card.get("name", ""),
            "mana_cost": face_values["mana_cost"] or card.get("mana_cost", ""),
            "mana_value": card.get("cmc"),
            "colors": card.get("colors") or [],
            "color_identity": card.get("color_identity") or [],
            "type_line": card.get("type_line", ""),
            "oracle_text": face_values["oracle_text"] or card.get("oracle_text", ""),
            "keywords": card.get("keywords") or [],
            "power": face_values["power"] or card.get("power", ""),
            "toughness": face_values["toughness"] or card.get("toughness", ""),
        }
        if card.get("lang") != "en":
            return CardOracle.objects.get_or_create(scryfall_oracle_id=card["oracle_id"], defaults=defaults)
        return CardOracle.objects.update_or_create(scryfall_oracle_id=card["oracle_id"], defaults=defaults)

    def _upsert_printing(self, card, oracle, card_set):
        printed_values = extract_printed_values(card)
        image_url = ""
        if card.get("image_uris"):
            image_url = card["image_uris"].get("normal") or card["image_uris"].get("large") or ""
        elif card.get("card_faces"):
            first_face_images = card["card_faces"][0].get("image_uris") or {}
            image_url = first_face_images.get("normal") or first_face_images.get("large") or ""

        return CardPrinting.objects.update_or_create(
            scryfall_id=card["id"],
            defaults={
                "oracle": oracle,
                "set": card_set,
                "set_code": card.get("set", ""),
                "collector_number": card.get("collector_number", ""),
                "rarity": card.get("rarity", ""),
                "image_url": image_url,
                "released_at": parse_date(card.get("released_at")),
                "lang": card.get("lang", ""),
                "printed_name": printed_values["name"],
                "printed_type_line": printed_values["type_line"],
                "printed_oracle_text": printed_values["oracle_text"],
            },
        )


def parse_date(value):
    if not value:
        return None
    return date.fromisoformat(value)


def extract_face_values(card):
    faces = card.get("card_faces") or []
    if not faces:
        return {"mana_cost": "", "oracle_text": "", "power": "", "toughness": ""}

    return {
        "mana_cost": " // ".join(face.get("mana_cost", "") for face in faces).strip(),
        "oracle_text": "\n---\n".join(face.get("oracle_text", "") for face in faces).strip(),
        "power": " // ".join(face.get("power", "") for face in faces if face.get("power")).strip(),
        "toughness": " // ".join(face.get("toughness", "") for face in faces if face.get("toughness")).strip(),
    }


def extract_printed_values(card):
    use_oracle_fallback = card.get("lang") == "en"
    faces = card.get("card_faces") or []
    if faces:
        return {
            "name": " // ".join(
                (face.get("printed_name") or (face.get("name") if use_oracle_fallback else "") or "") for face in faces
            ).strip(),
            "type_line": " // ".join(
                (face.get("printed_type_line") or (face.get("type_line") if use_oracle_fallback else "") or "")
                for face in faces
            ).strip(),
            "oracle_text": "\n---\n".join(
                (face.get("printed_text") or (face.get("oracle_text") if use_oracle_fallback else "") or "")
                for face in faces
            ).strip(),
        }
    return {
        "name": card.get("printed_name") or (card.get("name", "") if use_oracle_fallback else ""),
        "type_line": card.get("printed_type_line") or (card.get("type_line", "") if use_oracle_fallback else ""),
        "oracle_text": card.get("printed_text") or (card.get("oracle_text", "") if use_oracle_fallback else ""),
    }
