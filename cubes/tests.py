from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from cards.models import CardOracle
from stats.models import StatQuery

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

    def test_anonymous_user_can_view_public_cube_without_edit_actions(self):
        cube = Cube.objects.create(owner=self.user, name="Portfolio Cube", visibility=Cube.Visibility.PUBLIC)
        oracle = self.create_oracle("Public Bear", "Creature", colors=["G"], mana_value=2)
        CubeCard.objects.create(cube=cube, oracle=oracle)
        self.client.logout()

        response = self.client.get(reverse("cubes:detail", kwargs={"pk": cube.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Portfolio Cube")
        self.assertContains(response, "Public Bear")
        self.assertNotContains(response, "Ajouter des cartes")
        self.assertNotContains(
            response, reverse("cubes:card_edit", kwargs={"pk": cube.pk, "cube_card_id": cube.cards.first().pk})
        )

    def test_anonymous_user_cannot_view_private_cube(self):
        cube = Cube.objects.create(owner=self.user, name="Private Cube", visibility=Cube.Visibility.PRIVATE)
        self.client.logout()

        response = self.client.get(reverse("cubes:detail", kwargs={"pk": cube.pk}))

        self.assertEqual(response.status_code, 404)

    def test_cube_list_shows_public_cubes_to_anonymous_user(self):
        Cube.objects.create(owner=self.user, name="Listed Public Cube", visibility=Cube.Visibility.PUBLIC)
        Cube.objects.create(owner=self.user, name="Hidden Private Cube", visibility=Cube.Visibility.PRIVATE)
        self.client.logout()

        response = self.client.get(reverse("cubes:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Listed Public Cube")
        self.assertNotContains(response, "Hidden Private Cube")
        self.assertNotContains(response, "Creer un cube")

    def test_anonymous_user_can_view_public_cube_stats(self):
        cube = Cube.objects.create(
            owner=self.user, name="Public Stats Cube", visibility=Cube.Visibility.PUBLIC, booster_size=1
        )
        oracle = self.create_oracle("Stats Bear", "Creature", colors=["G"], mana_value=2)
        CubeCard.objects.create(cube=cube, oracle=oracle)
        StatQuery.objects.create(
            owner=self.user, cube=cube, scope=StatQuery.Scope.CUBE, name="Cube creatures", raw_query="type:Creature"
        )
        self.client.logout()

        response = self.client.get(
            reverse("cubes:stats", kwargs={"pk": cube.pk}), {"stats_filter": "1", "stats": ["query_cube-creatures"]}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Public Stats Cube")
        self.assertContains(response, "Cube creatures")
        self.assertNotContains(response, "Creer le cube ameliore")

    def create_oracle(self, name, type_line, colors, mana_value=0):
        return CardOracle.objects.create(
            scryfall_oracle_id=uuid4(),
            name=name,
            mana_value=mana_value,
            type_line=type_line,
            colors=colors,
            color_identity=colors,
        )
