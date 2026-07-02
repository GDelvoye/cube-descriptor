from datetime import date
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from cards.models import CardOracle, CardPrinting, Set
from cubes.models import Cube, CubeCard
from stats.models import StatQuery
from stats.query_engine import build_oracle_query, count_cube_matches
from stats.set_indicators import build_set_indicator_benchmarks, build_set_indicators


class StatsSourceTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")
        self.user = get_user_model().objects.create_user(username="stats-user", password="password")
        self.client.force_login(self.user)
        self.card_set = Set.objects.create(code="mrd", name="Mirrodin", set_type="expansion")

    def test_stats_index_redirects_to_selected_set(self):
        response = self.client.get(reverse("stats:index"), {"set": self.card_set.pk})

        self.assertRedirects(response, reverse("stats:set_stats", kwargs={"pk": self.card_set.pk}))

    def test_query_list_links_to_dedicated_creation_page(self):
        response = self.client.get(reverse("stats:query_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("stats:query_create"))
        self.assertNotContains(response, "Sauvegarder la requete")

    def test_query_create_page_uses_visual_builder(self):
        response = self.client.get(reverse("stats:query_create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Construire la requete")
        self.assertContains(response, "id_raw_query")
        self.assertContains(response, "AND")
        self.assertContains(response, "OR")
        self.assertContains(response, "NEW GROUP")
        self.assertContains(response, "Sortir du groupe")
        self.assertContains(response, "Supprimer")

    def test_query_create_page_can_test_current_query(self):
        self.create_printing(
            "Wind Drake", "common", "Creature", keywords=["Flying"], image_url="https://example.com/wind.jpg"
        )
        self.create_printing("Bear", "common", "Creature")

        response = self.client.post(
            reverse("stats:query_create"),
            {"name": "", "raw_query": "type:Creature AND keyword:Flying", "description": "", "test_query": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1 carte matchante")
        self.assertContains(response, "Liste")
        self.assertContains(response, "Cartes")
        self.assertContains(response, "Illustration")
        self.assertContains(response, "https://example.com/wind.jpg")
        self.assertContains(response, "Wind Drake")
        self.assertNotContains(response, "Bear - Creature")
        self.assertContains(response, 'data-initial-query="type:Creature AND keyword:Flying"')
        self.assertNotContains(response, "[&#x27;type:Creature AND keyword:Flying&#x27;]")

    def test_query_test_preserves_regex_parentheses(self):
        self.create_printing(
            "Opponent Burn",
            "common",
            "Instant",
            oracle_text="Opponent Burn deals 3 damage to target opponent.",
        )

        response = self.client.post(
            reverse("stats:query_create"),
            {
                "name": "",
                "raw_query": 'set:MRD AND text_regex:"damage to (target|each) opponent"',
                "description": "",
                "test_query": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Opponent Burn")
        self.assertContains(response, "text_regex:&quot;damage to (target|each) opponent&quot;")

    def test_query_test_uses_first_english_printing_image(self):
        oracle = CardOracle.objects.create(scryfall_oracle_id=uuid4(), name="Reprinted Card", type_line="Creature")
        old_set = Set.objects.create(code="old", name="Old", released_at=date(2000, 1, 1), set_type="expansion")
        new_set = Set.objects.create(code="new", name="New", released_at=date(2020, 1, 1), set_type="expansion")
        CardPrinting.objects.create(
            scryfall_id=uuid4(),
            oracle=oracle,
            set=old_set,
            set_code=old_set.code,
            collector_number="1",
            rarity="common",
            lang="en",
            released_at=old_set.released_at,
            image_url="https://example.com/old.jpg",
        )
        CardPrinting.objects.create(
            scryfall_id=uuid4(),
            oracle=oracle,
            set=new_set,
            set_code=new_set.code,
            collector_number="1",
            rarity="common",
            lang="en",
            released_at=new_set.released_at,
            image_url="https://example.com/new.jpg",
        )

        response = self.client.post(
            reverse("stats:query_create"),
            {"name": "", "raw_query": "name:Reprinted", "description": "", "test_query": "1"},
        )

        self.assertContains(response, "https://example.com/old.jpg")
        self.assertNotContains(response, "https://example.com/new.jpg")

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

    def test_set_stats_filters_visible_indicators(self):
        self.create_printing("Common Creature", "common", "Creature")
        self.create_printing("Common Land", "common", "Land")

        response = self.client.get(
            reverse("stats:set_stats", kwargs={"pk": self.card_set.pk}),
            {"stats_filter": "1", "stats": ["creatures"]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([indicator["key"] for indicator in response.context["indicators"]], ["creatures"])
        self.assertContains(response, "Creatures (#1)")
        self.assertNotContains(response, "Terrains (#1)")

    def test_cube_stats_show_cube_indicators_against_set_benchmarks(self):
        creature = self.create_printing("Cube Creature", "common", "Creature")
        land = self.create_printing("Cube Land", "common", "Land")
        cube = Cube.objects.create(owner=self.user, name="Indicator Cube", booster_size=2)
        CubeCard.objects.create(cube=cube, oracle=creature.oracle, quantity=3)
        CubeCard.objects.create(cube=cube, oracle=land.oracle, quantity=1)

        response = self.client.get(
            reverse("cubes:stats", kwargs={"pk": cube.pk}),
            {"stats_filter": "1", "stats": ["creatures"]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([indicator["key"] for indicator in response.context["indicators"]], ["creatures"])
        self.assertContains(response, "Creatures (#3)")
        self.assertContains(response, "Indicator Cube: 1,50")
        self.assertNotContains(response, "Terrains (#1)")

    def test_set_indicator_benchmarks_include_deciles_and_quartiles(self):
        old_set = Set.objects.create(code="old", name="Old", set_type="expansion")
        mid_set = Set.objects.create(code="mid", name="Middle", set_type="expansion")
        new_set = Set.objects.create(code="new", name="New", set_type="expansion")
        self.create_printing("Old Creature", "common", "Creature", set_obj=old_set)
        self.create_printing("Mid Creature", "common", "Creature", set_obj=mid_set)
        self.create_printing("Mid Instant", "common", "Instant", set_obj=mid_set)
        self.create_printing("New Instant", "common", "Instant", set_obj=new_set)

        benchmarks = build_set_indicator_benchmarks(
            [
                list(CardPrinting.objects.filter(set=old_set).select_related("oracle")),
                list(CardPrinting.objects.filter(set=mid_set).select_related("oracle")),
                list(CardPrinting.objects.filter(set=new_set).select_related("oracle")),
            ]
        )

        creatures = benchmarks["creatures"]
        self.assertEqual(creatures["count"], 3)
        self.assertAlmostEqual(creatures["d1"], 1.1)
        self.assertAlmostEqual(creatures["median"], 5.5)
        self.assertAlmostEqual(creatures["d9"], 9.9)

    def test_or_query_matches_any_clause(self):
        removal = self.create_printing("Doom Blade", "common", "Instant", oracle_text="Destroy target creature.")
        exile = self.create_printing("Path", "common", "Instant", oracle_text="Exile target creature.")
        self.create_printing("Vanilla", "common", "Creature")

        matching_oracles = CardOracle.objects.filter(build_oracle_query("text:destroy OR text:exile")).order_by("name")

        self.assertQuerySetEqual(matching_oracles, [removal.oracle, exile.oracle], transform=lambda oracle: oracle)

    def test_text_regex_filter_matches_oracle_text(self):
        opponent_burn = self.create_printing(
            "Opponent Burn", "common", "Instant", oracle_text="Opponent Burn deals 3 damage to target opponent."
        )
        self.create_printing("Self Burn", "common", "Instant", oracle_text="Self Burn deals 3 damage to you.")

        matching_oracles = CardOracle.objects.filter(build_oracle_query('text_regex:"damage .* opponent"')).distinct()

        self.assertQuerySetEqual(matching_oracles, [opponent_burn.oracle], transform=lambda oracle: oracle)

    def test_text_regex_filter_rejects_invalid_regex(self):
        with self.assertRaisesMessage(Exception, "Regex invalide"):
            build_oracle_query('text_regex:"damage ("')

    def test_text_regex_filter_matches_cube_cards(self):
        opponent_burn = self.create_printing(
            "Opponent Burn", "common", "Instant", oracle_text="Opponent Burn deals 3 damage to target opponent."
        )
        self_burn = self.create_printing(
            "Self Burn", "common", "Instant", oracle_text="Self Burn deals 3 damage to you."
        )
        cube = Cube.objects.create(owner=self.user, name="Regex Cube")
        opponent_cube_card = CubeCard.objects.create(cube=cube, oracle=opponent_burn.oracle)
        CubeCard.objects.create(cube=cube, oracle=self_burn.oracle)

        count, rows = count_cube_matches(list(cube.cards.select_related("oracle")), 'text_regex:"damage .* opponent"')

        self.assertEqual(count, 1)
        self.assertEqual(rows, [opponent_cube_card])

    def test_global_query_shape_can_express_flying_creatures(self):
        flyer = self.create_printing("Wind Drake", "common", "Creature", keywords=["Flying"])
        self.create_printing("Jump", "common", "Instant", keywords=["Flying"])
        self.create_printing("Bear", "common", "Creature")

        matching_oracles = CardOracle.objects.filter(build_oracle_query("type:Creature AND keyword:Flying")).distinct()

        self.assertQuerySetEqual(matching_oracles, [flyer.oracle], transform=lambda oracle: oracle)

    def test_parenthesized_or_group_combines_with_and(self):
        flyer = self.create_printing("Wind Drake", "common", "Creature", keywords=["Flying"])
        trampler = self.create_printing("Rhino", "common", "Creature", keywords=["Trample"])
        self.create_printing("Jump", "common", "Instant", keywords=["Flying"])
        self.create_printing("Bear", "common", "Creature")

        matching_oracles = CardOracle.objects.filter(
            build_oracle_query("type:Creature AND (keyword:Flying OR keyword:Trample)")
        ).order_by("name")

        self.assertQuerySetEqual(matching_oracles, [trampler.oracle, flyer.oracle], transform=lambda oracle: oracle)

    def test_not_operator_excludes_matching_clause(self):
        burn = self.create_printing("Burn", "common", "Instant", oracle_text="Burn deals 2 damage to target creature.")
        self.create_printing(
            "Combat Trigger",
            "common",
            "Creature",
            oracle_text="Whenever this creature deals combat damage to a player, draw a card.",
        )

        matching_oracles = CardOracle.objects.filter(
            build_oracle_query(
                'text_regex:"deals?.*damage to (target |a )?(creature|player)" AND NOT text_regex:"combat damage"'
            )
        ).distinct()

        self.assertQuerySetEqual(matching_oracles, [burn.oracle], transform=lambda oracle: oracle)

    def test_not_operator_excludes_cube_matches(self):
        burn = self.create_printing("Burn", "common", "Instant", oracle_text="Burn deals 2 damage to target creature.")
        combat_trigger = self.create_printing(
            "Combat Trigger",
            "common",
            "Creature",
            oracle_text="Whenever this creature deals combat damage to a player, draw a card.",
        )
        cube = Cube.objects.create(owner=self.user, name="Not Cube")
        burn_cube_card = CubeCard.objects.create(cube=cube, oracle=burn.oracle)
        CubeCard.objects.create(cube=cube, oracle=combat_trigger.oracle)

        count, rows = count_cube_matches(
            list(cube.cards.select_related("oracle")),
            'text_regex:"deals?.*damage to (target |a )?(creature|player)" AND NOT text_regex:"combat damage"',
        )

        self.assertEqual(count, 1)
        self.assertEqual(rows, [burn_cube_card])

    def test_global_query_shape_can_express_simple_fixing(self):
        fixing = self.create_printing("Mana Rock", "common", "Artifact", oracle_text="Add one mana of any color.")
        land = self.create_printing("Dual Land", "common", "Land", oracle_text="Add {U} or {R}.")
        self.create_printing("Vanilla", "common", "Creature")

        matching_oracles = CardOracle.objects.filter(
            build_oracle_query('type:Artifact AND text:add AND text:mana OR type:Land AND text:"add"')
        ).order_by("name")

        self.assertQuerySetEqual(matching_oracles, [land.oracle, fixing.oracle], transform=lambda oracle: oracle)

    def test_set_filter_matches_extension_code_or_name(self):
        mirrodin_card = self.create_printing("Mirrodin Card", "common", "Creature")
        other_set = Set.objects.create(code="tmp", name="Tempest", set_type="expansion")
        other_oracle = CardOracle.objects.create(scryfall_oracle_id=uuid4(), name="Tempest Card", type_line="Creature")
        CardPrinting.objects.create(
            scryfall_id=uuid4(),
            oracle=other_oracle,
            set=other_set,
            set_code=other_set.code,
            collector_number="1",
            rarity="common",
            lang="en",
        )

        by_code = CardOracle.objects.filter(build_oracle_query("set:mrd")).distinct()
        by_name = CardOracle.objects.filter(build_oracle_query("set:Mirrodin")).distinct()

        self.assertQuerySetEqual(by_code, [mirrodin_card.oracle], transform=lambda oracle: oracle)
        self.assertQuerySetEqual(by_name, [mirrodin_card.oracle], transform=lambda oracle: oracle)

    def test_query_reference_combines_saved_queries_in_global_search(self):
        removal = StatQuery.objects.create(owner=self.user, name="Removal", raw_query="text:destroy")
        exile = StatQuery.objects.create(owner=self.user, name="Exile", raw_query="text:exile")
        destroy_card = self.create_printing(
            "Destroy Spell", "common", "Instant", oracle_text="Destroy target creature."
        )
        exile_card = self.create_printing("Exile Spell", "common", "Instant", oracle_text="Exile target creature.")
        self.create_printing("Vanilla", "common", "Creature")
        visible_queries = StatQuery.objects.filter(owner=self.user)

        matching_oracles = CardOracle.objects.filter(
            build_oracle_query(f'query_id:{removal.pk} OR query:"{exile.name}"', stat_queries=visible_queries)
        ).order_by("name")

        self.assertQuerySetEqual(
            matching_oracles, [destroy_card.oracle, exile_card.oracle], transform=lambda oracle: oracle
        )

    def test_query_reference_combines_saved_queries_in_cube_search(self):
        StatQuery.objects.create(owner=self.user, name="Removal", raw_query="text:destroy")
        destroy_card = self.create_printing(
            "Destroy Spell", "common", "Instant", oracle_text="Destroy target creature."
        )
        vanilla = self.create_printing("Vanilla", "common", "Creature")
        cube = Cube.objects.create(owner=self.user, name="Nested Query Cube")
        destroy_cube_card = CubeCard.objects.create(cube=cube, oracle=destroy_card.oracle)
        CubeCard.objects.create(cube=cube, oracle=vanilla.oracle)

        count, rows = count_cube_matches(
            list(cube.cards.select_related("oracle")),
            'query:"Removal"',
            stat_queries=StatQuery.objects.filter(owner=self.user),
        )

        self.assertEqual(count, 1)
        self.assertEqual(rows, [destroy_cube_card])

    def test_query_reference_rejects_cycles(self):
        first = StatQuery.objects.create(owner=self.user, name="First", raw_query="text:destroy")
        second = StatQuery.objects.create(owner=self.user, name="Second", raw_query=f"query_id:{first.pk}")
        first.raw_query = f"query_id:{second.pk}"
        first.save(update_fields=["raw_query"])

        with self.assertRaisesMessage(Exception, "Reference circulaire"):
            build_oracle_query(f"query_id:{first.pk}", stat_queries=StatQuery.objects.filter(owner=self.user))

    def create_printing(
        self,
        name,
        rarity,
        type_line,
        oracle_text="",
        colors=None,
        mana_value=None,
        keywords=None,
        image_url="",
        set_obj=None,
    ):
        set_obj = set_obj or self.card_set
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
            set=set_obj,
            set_code=set_obj.code,
            collector_number=name,
            rarity=rarity,
            lang="en",
            image_url=image_url,
        )
