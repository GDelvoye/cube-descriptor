from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from cards.models import CardOracle

from .models import Cube, CubeCard


class CubeDetailTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")
        self.user = get_user_model().objects.create_user(username="cube-user", password="password")
        self.client.force_login(self.user)

    def test_cube_detail_groups_cards_by_color_and_type(self):
        cube = Cube.objects.create(owner=self.user, name="Overview Cube")
        white_creature = self.create_oracle("White Bear", "Creature", colors=["W"], mana_value=2)
        cheap_white_creature = self.create_oracle("Cheap White Bear", "Creature", colors=["W"], mana_value=1)
        blue_instant = self.create_oracle("Blue Trick", "Instant", colors=["U"])
        gold_sorcery = self.create_oracle("Gold Spell", "Sorcery", colors=["W", "U"])
        land = self.create_oracle("Test Land", "Land", colors=[])
        CubeCard.objects.create(cube=cube, oracle=white_creature)
        CubeCard.objects.create(cube=cube, oracle=cheap_white_creature)
        CubeCard.objects.create(cube=cube, oracle=blue_instant)
        CubeCard.objects.create(cube=cube, oracle=gold_sorcery)
        CubeCard.objects.create(cube=cube, oracle=land)

        response = self.client.get(reverse("cubes:detail", kwargs={"pk": cube.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "White")
        self.assertContains(response, "Blue")
        self.assertContains(response, "Multicolored")
        self.assertContains(response, "Lands")
        self.assertContains(response, "Creatures (2)")
        self.assertContains(response, "Instants (1)")
        self.assertContains(response, "Sorceries (1)")
        self.assertContains(response, "White Bear")
        self.assertContains(response, "Cheap White Bear")
        self.assertContains(response, "Blue Trick")
        self.assertContains(response, "Gold Spell")
        self.assertContains(response, "Test Land")
        content = response.content.decode()
        self.assertLess(content.index("Cheap White Bear"), content.index("White Bear"))

    def test_cube_card_edit_page_shows_metadata_form(self):
        cube = Cube.objects.create(owner=self.user, name="Overview Cube")
        oracle = self.create_oracle("White Bear", "Creature", colors=["W"], mana_value=2)
        cube_card = CubeCard.objects.create(cube=cube, oracle=oracle)

        response = self.client.get(reverse("cubes:card_edit", kwargs={"pk": cube.pk, "cube_card_id": cube_card.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Editer White Bear")
        self.assertContains(response, "Sauvegarder")
        self.assertContains(response, "Retirer du cube")

    def create_oracle(self, name, type_line, colors, mana_value=0):
        return CardOracle.objects.create(
            scryfall_oracle_id=uuid4(),
            name=name,
            mana_value=mana_value,
            type_line=type_line,
            colors=colors,
            color_identity=colors,
        )
