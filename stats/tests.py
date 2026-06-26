from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from cards.models import CardOracle, CardPrinting, Set


class StatsSourceTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")
        self.user = get_user_model().objects.create_user(username="stats-user", password="password")
        self.client.force_login(self.user)
        self.card_set = Set.objects.create(code="mrd", name="Mirrodin", set_type="expansion")

    def test_stats_index_redirects_to_selected_set(self):
        response = self.client.get(reverse("stats:index"), {"set": self.card_set.pk})

        self.assertRedirects(response, reverse("stats:set_stats", kwargs={"pk": self.card_set.pk}))

    def test_set_stats_uses_rarity_slots(self):
        self.create_printing("Common Hit", "common", "Creature")
        self.create_printing("Common Miss", "common", "Instant")
        self.create_printing("Uncommon Miss", "uncommon", "Instant")
        self.create_printing("Rare Miss", "rare", "Instant")
        self.create_printing("Mythic Hit", "mythic", "Creature")

        response = self.client.get(
            reverse("stats:set_stats", kwargs={"pk": self.card_set.pk}),
            {"raw_query": "type:Creature", "minimum_hits": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mode extension classique")
        self.assertContains(response, "common")
        self.assertContains(response, "mythic")
        self.assertEqual(response.context["result"]["booster_size"], 4)
        self.assertEqual(response.context["result"]["matching_count"], 2)
        self.assertAlmostEqual(response.context["result"]["at_least"], 1.0)

    def test_set_stats_weights_rare_mythic_slot(self):
        self.create_printing("Common Miss", "common", "Instant")
        self.create_printing("Uncommon Miss", "uncommon", "Instant")
        self.create_printing("Rare Miss", "rare", "Instant")
        self.create_printing("Mythic Hit", "mythic", "Creature")

        response = self.client.get(
            reverse("stats:set_stats", kwargs={"pk": self.card_set.pk}),
            {"raw_query": "type:Creature", "minimum_hits": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertAlmostEqual(response.context["result"]["at_least"], 0.125)

    def create_printing(self, name, rarity, type_line):
        oracle = CardOracle.objects.create(
            scryfall_oracle_id=uuid4(),
            name=name,
            type_line=type_line,
        )
        return CardPrinting.objects.create(
            scryfall_id=uuid4(),
            oracle=oracle,
            set=self.card_set,
            set_code=self.card_set.code,
            collector_number=name,
            rarity=rarity,
            lang="en",
        )
