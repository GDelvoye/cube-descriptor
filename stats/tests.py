from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from cards.models import CardOracle, CardPrinting, Set
from stats.query_engine import build_oracle_query
from stats.set_indicators import build_set_indicators


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

    def test_set_indicators_infer_old_booster_profile_without_mythics(self):
        self.create_printing("Common Creature", "common", "Creature")
        self.create_printing("Common Land", "common", "Land")
        self.create_printing("Uncommon Instant", "uncommon", "Instant")
        self.create_printing("Rare Artifact", "rare", "Artifact")

        indicators = {row["key"]: row for row in build_set_indicators(list(CardPrinting.objects.all()))}

        self.assertAlmostEqual(indicators["creatures"]["expected"], 11 / 2)
        self.assertAlmostEqual(indicators["artifacts"]["expected"], 1)

    def test_set_indicators_weight_mythics_when_present(self):
        self.create_printing("Common Miss", "common", "Instant")
        self.create_printing("Uncommon Miss", "uncommon", "Instant")
        self.create_printing("Rare Miss", "rare", "Instant")
        self.create_printing("Mythic Walker", "mythic", "Planeswalker")

        indicators = {row["key"]: row for row in build_set_indicators(list(CardPrinting.objects.all()))}

        self.assertAlmostEqual(indicators["planeswalkers"]["expected"], 0.125)

    def test_set_indicators_include_removal_fixing_and_mv_bands(self):
        self.create_printing("Doom Blade", "common", "Instant", oracle_text="Destroy target creature.", mana_value=2)
        self.create_printing("Mana Rock", "common", "Artifact", oracle_text="Add one mana of any color.", mana_value=2)
        self.create_printing("Big Monster", "rare", "Creature", mana_value=6)

        indicators = {row["key"]: row for row in build_set_indicators(list(CardPrinting.objects.all()))}

        self.assertEqual(indicators["removal"]["matching_count"], 1)
        self.assertEqual(indicators["fixing"]["matching_count"], 1)
        self.assertEqual(indicators["cheap"]["matching_count"], 2)
        self.assertEqual(indicators["expensive"]["matching_count"], 1)

    def test_or_query_matches_any_clause(self):
        removal = self.create_printing("Doom Blade", "common", "Instant", oracle_text="Destroy target creature.")
        exile = self.create_printing("Path", "common", "Instant", oracle_text="Exile target creature.")
        self.create_printing("Vanilla", "common", "Creature")

        matching_oracles = CardOracle.objects.filter(build_oracle_query("text:destroy OR text:exile")).order_by("name")

        self.assertQuerySetEqual(matching_oracles, [removal.oracle, exile.oracle], transform=lambda oracle: oracle)

    def test_global_query_shape_can_express_flying_creatures(self):
        flyer = self.create_printing("Wind Drake", "common", "Creature", keywords=["Flying"])
        self.create_printing("Jump", "common", "Instant", keywords=["Flying"])
        self.create_printing("Bear", "common", "Creature")

        matching_oracles = CardOracle.objects.filter(build_oracle_query("type:Creature AND keyword:Flying")).distinct()

        self.assertQuerySetEqual(matching_oracles, [flyer.oracle], transform=lambda oracle: oracle)

    def test_global_query_shape_can_express_simple_fixing(self):
        fixing = self.create_printing("Mana Rock", "common", "Artifact", oracle_text="Add one mana of any color.")
        land = self.create_printing("Dual Land", "common", "Land", oracle_text="Add {U} or {R}.")
        self.create_printing("Vanilla", "common", "Creature")

        matching_oracles = CardOracle.objects.filter(
            build_oracle_query('type:Artifact AND text:add AND text:mana OR type:Land AND text:"add"')
        ).order_by("name")

        self.assertQuerySetEqual(matching_oracles, [land.oracle, fixing.oracle], transform=lambda oracle: oracle)

    def create_printing(self, name, rarity, type_line, oracle_text="", colors=None, mana_value=None, keywords=None):
        oracle = CardOracle.objects.create(
            scryfall_oracle_id=uuid4(),
            name=name,
            type_line=type_line,
            oracle_text=oracle_text,
            colors=colors or [],
            mana_value=mana_value,
            keywords=keywords or [],
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
