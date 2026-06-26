import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase

from cubes.models import Cube, CubeCard
from stats.query_engine import count_cube_matches

from .display import apply_oracle_display
from .models import CardOracle, CardPrinting, Set


class CardLocalizationTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")
        self.set = Set.objects.create(code="tst", name="Test Set", set_type="expansion")

    def test_import_french_all_cards_populates_localized_printing_only_for_existing_oracle(self):
        oracle = CardOracle.objects.create(
            scryfall_oracle_id=uuid4(),
            name="Draw Test",
            type_line="Sorcery",
            oracle_text="Draw two cards.",
        )
        missing_oracle_id = str(uuid4())
        french_id = str(uuid4())
        skipped_id = str(uuid4())
        payload = [
            {
                "id": french_id,
                "oracle_id": str(oracle.scryfall_oracle_id),
                "lang": "fr",
                "name": "Draw Test",
                "printed_name": "Test de pioche",
                "printed_type_line": "Rituel",
                "printed_text": "Piochez deux cartes.",
                "set": "tst",
                "set_name": "Test Set",
                "set_type": "expansion",
                "collector_number": "1",
                "rarity": "common",
                "released_at": "2026-01-01",
                "image_uris": {"normal": "https://example.com/fr.jpg"},
            },
            {
                "id": skipped_id,
                "oracle_id": missing_oracle_id,
                "lang": "fr",
                "name": "Missing Oracle",
                "printed_name": "Oracle manquant",
                "set": "tst",
                "set_name": "Test Set",
                "set_type": "expansion",
                "collector_number": "2",
            },
        ]

        with TemporaryDirectory() as directory:
            file_path = Path(directory) / "all_cards_fr.json"
            file_path.write_text(json.dumps(payload), encoding="utf-8")
            call_command("import_scryfall_french_all_cards", file=file_path)

        printing = CardPrinting.objects.get(scryfall_id=french_id)
        self.assertEqual(printing.oracle, oracle)
        self.assertEqual(printing.lang, "fr")
        self.assertEqual(printing.printed_name, "Test de pioche")
        self.assertEqual(printing.printed_type_line, "Rituel")
        self.assertEqual(printing.printed_oracle_text, "Piochez deux cartes.")
        self.assertEqual(printing.image_url, "https://example.com/fr.jpg")
        self.assertFalse(CardPrinting.objects.filter(scryfall_id=skipped_id).exists())

    def test_display_uses_latest_complete_bilingual_pair(self):
        old_set = Set.objects.create(code="old", name="Old Set", released_at=date(2000, 1, 1), set_type="expansion")
        new_set = Set.objects.create(code="new", name="New Set", released_at=date(2010, 1, 1), set_type="expansion")
        oracle = CardOracle.objects.create(
            scryfall_oracle_id=uuid4(),
            name="Goblin King",
            type_line="Creature - Goblin",
            oracle_text="Other Goblins get +1/+1.",
        )
        self.create_printing(oracle, old_set, "en", "1", "Goblin King", "Creature", "Old EN", "old-en")
        self.create_printing(oracle, old_set, "fr", "1", "Roi ancien", "Creature", "Old FR", "old-fr")
        self.create_printing(oracle, new_set, "en", "1", "Goblin King", "Creature", "New EN", "new-en")
        self.create_printing(oracle, new_set, "fr", "1", "Roi recent", "Creature recente", "New FR", "new-fr")

        apply_oracle_display(oracle, "fr")

        self.assertEqual(oracle.display_localized_printing.set_code, "new")
        self.assertEqual(oracle.display_name, "Roi recent")
        self.assertEqual(oracle.display_type_line, "Creature recente")
        self.assertEqual(oracle.display_oracle_text, "New FR")

    def test_french_search_and_custom_query_match_localized_printing_fields(self):
        oracle = CardOracle.objects.create(
            scryfall_oracle_id=uuid4(),
            name="Blink Deer",
            type_line="Creature - Elk",
            oracle_text="Exile another target permanent.",
        )
        self.create_printing(
            oracle,
            self.set,
            "fr",
            "7",
            "Cerf aux ramures rayonnantes",
            "Creature : elan",
            "Exilez un autre permanent cible.",
            "fr-deer",
        )
        cube = Cube.objects.create(owner=self.create_user(), name="Query Cube")
        cube_card = CubeCard.objects.create(cube=cube, oracle=oracle)

        response = self.client.get("/cards/", {"q": "ramures"})

        self.assertContains(response, "Blink Deer")
        self.assertEqual(count_cube_matches([cube_card], "name:ramures")[0], 1)
        self.assertEqual(count_cube_matches([cube_card], "type:elan")[0], 1)
        self.assertEqual(count_cube_matches([cube_card], "text:Exilez")[0], 1)

    def test_global_custom_query_uses_sql_filters_for_localized_fields(self):
        oracle = CardOracle.objects.create(
            scryfall_oracle_id=uuid4(),
            name="SQL Deer",
            type_line="Creature - Elk",
            oracle_text="Exile another target permanent.",
        )
        self.create_printing(
            oracle,
            self.set,
            "fr",
            "8",
            "Cerf SQL",
            "Creature : elan",
            "Exilez un permanent cible.",
            "sql-deer",
        )

        response = self.client.get("/cards/", {"raw_query": "name:SQL AND type:elan AND text:Exilez"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_count"], 1)
        self.assertContains(response, "SQL Deer")

    def test_global_custom_query_rejects_cube_tags(self):
        response = self.client.get("/cards/", {"raw_query": "tag:removal"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Le filtre tag: est disponible dans les cubes")

    def test_add_selected_redirects_back_to_search_and_shows_toast(self):
        user = self.create_user()
        cube = Cube.objects.create(owner=user, name="Toast Cube")
        oracle = CardOracle.objects.create(scryfall_oracle_id=uuid4(), name="Toast Card")
        self.create_printing(oracle, self.set, "en", "1", "Toast Card", "Creature", "Toast text", "toast")
        self.client.force_login(user)

        response = self.client.post(
            "/cards/add-selected-to-cube/",
            {
                "cube": cube.pk,
                "quantity": 2,
                "oracle_ids": [oracle.pk],
                "next": f"/cards/?cube={cube.pk}&q=Toast",
            },
            follow=True,
        )

        self.assertRedirects(response, f"/cards/?cube={cube.pk}&q=Toast")
        self.assertContains(response, "2 cartes ajoutees a Toast Cube.")
        self.assertContains(response, f'name="cube" value="{cube.pk}"')
        self.assertEqual(CubeCard.objects.get(cube=cube, oracle=oracle).quantity, 2)

    def create_user(self):
        return get_user_model().objects.create_user(username=f"user-{uuid4()}", password="password")

    def create_printing(self, oracle, card_set, lang, collector_number, name, type_line, text, image_slug):
        return CardPrinting.objects.create(
            scryfall_id=uuid4(),
            oracle=oracle,
            set=card_set,
            set_code=card_set.code,
            collector_number=collector_number,
            rarity="common",
            image_url=f"https://example.com/{image_slug}.jpg",
            released_at=card_set.released_at,
            lang=lang,
            printed_name=name,
            printed_type_line=type_line,
            printed_oracle_text=text,
        )
