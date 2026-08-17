from datetime import date
from io import StringIO
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from cards.models import CardOracle, CardPrinting, Set
from cubes.models import Cube, CubeCard
from stats.models import SetIndicatorExpectedValue, StatQuery, StatQueryDependency, StatQueryMatch
from stats.query_engine import build_oracle_query, count_cube_matches
from stats.set_indicators import (
    SET_INDICATORS,
    build_cached_set_indicator_benchmarks,
    build_cube_removal_plan,
    build_set_indicator_benchmarks,
    build_set_indicators,
)


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

    def test_query_create_refreshes_match_cache(self):
        removal = self.create_printing("Doom Blade", "common", "Instant", oracle_text="Destroy target creature.")
        self.create_printing("Bear", "common", "Creature")

        response = self.client.post(
            reverse("stats:query_create"),
            {"name": "destroy removal", "raw_query": "text:destroy", "description": ""},
        )

        stat_query = StatQuery.objects.get(name="destroy removal")
        self.assertRedirects(response, reverse("stats:query_detail", kwargs={"pk": stat_query.pk}))
        self.assertIsNotNone(stat_query.match_cache_refreshed_at)
        self.assertQuerySetEqual(
            StatQueryMatch.objects.filter(stat_query=stat_query),
            [removal.oracle],
            transform=lambda match: match.oracle,
        )

    def test_query_edit_refreshes_dependent_match_caches(self):
        destroy = self.create_printing("Doom Blade", "common", "Instant", oracle_text="Destroy target creature.")
        exile = self.create_printing("Path", "common", "Instant", oracle_text="Exile target creature.")
        self.create_printing("Bear", "common", "Creature")

        self.client.post(
            reverse("stats:query_create"),
            {"name": "destroy removal", "raw_query": "text:destroy", "description": ""},
        )
        child = StatQuery.objects.get(name="destroy removal")
        self.client.post(
            reverse("stats:query_create"),
            {"name": "removal", "raw_query": 'query:"destroy removal"', "description": ""},
        )
        parent = StatQuery.objects.get(name="removal")

        self.assertTrue(StatQueryDependency.objects.filter(parent=parent, child=child).exists())
        self.assertQuerySetEqual(
            StatQueryMatch.objects.filter(stat_query=parent),
            [destroy.oracle],
            transform=lambda match: match.oracle,
        )

        response = self.client.post(
            reverse("stats:query_edit", kwargs={"pk": child.pk}),
            {"name": "destroy removal", "raw_query": "text:exile", "description": ""},
        )

        self.assertRedirects(response, reverse("stats:query_detail", kwargs={"pk": child.pk}))
        self.assertQuerySetEqual(
            StatQueryMatch.objects.filter(stat_query=child),
            [exile.oracle],
            transform=lambda match: match.oracle,
        )
        self.assertQuerySetEqual(
            StatQueryMatch.objects.filter(stat_query=parent),
            [exile.oracle],
            transform=lambda match: match.oracle,
        )

    def test_query_detail_shows_matching_cards(self):
        removal = self.create_printing("Doom Blade", "common", "Instant", oracle_text="Destroy target creature.")
        self.create_printing("Bear", "common", "Creature")
        stat_query = StatQuery.objects.create(owner=self.user, name="destroy removal", raw_query="text:destroy")

        response = self.client.get(reverse("stats:query_detail", kwargs={"pk": stat_query.pk}))

        stat_query.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(stat_query.match_cache_refreshed_at)
        self.assertContains(response, "destroy removal")
        self.assertContains(response, "text:destroy")
        self.assertContains(response, "1 carte matchante")
        self.assertContains(response, removal.oracle.name)
        self.assertNotContains(response, "Bear")

    def test_query_detail_allows_read_only_global_query(self):
        stat_query = StatQuery.objects.create(
            owner=None,
            scope=StatQuery.Scope.GLOBAL,
            name="global creatures",
            raw_query="type:Creature",
        )

        response = self.client.get(reverse("stats:query_detail", kwargs={"pk": stat_query.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "global creatures")
        self.assertNotContains(response, "Modifier")
        self.assertNotContains(response, "Supprimer")

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

    def test_set_stats_can_include_removal_query_indicator(self):
        StatQuery.objects.create(owner=self.user, name="removal", raw_query="text:destroy OR text:exile")
        self.create_printing("Doom Blade", "common", "Instant", oracle_text="Destroy target creature.")
        self.create_printing("Path", "common", "Instant", oracle_text="Exile target creature.")
        self.create_printing("Burn", "common", "Instant", oracle_text="Burn deals 3 damage to any target.")

        response = self.client.get(
            reverse("stats:set_stats", kwargs={"pk": self.card_set.pk}),
            {"stats_filter": "1", "stats": ["query_removal"]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([indicator["key"] for indicator in response.context["indicators"]], ["query_removal"])
        self.assertContains(response, "Removal 2 (#2)")

    def test_cube_stats_can_include_removal_query_indicator(self):
        StatQuery.objects.create(owner=self.user, name="removal", raw_query="text:destroy")
        removal = self.create_printing("Doom Blade", "common", "Instant", oracle_text="Destroy target creature.")
        burn = self.create_printing("Burn", "common", "Instant", oracle_text="Burn deals 3 damage to any target.")
        cube = Cube.objects.create(owner=self.user, name="Query Indicator Cube", booster_size=2)
        CubeCard.objects.create(cube=cube, oracle=removal.oracle, quantity=1)
        CubeCard.objects.create(cube=cube, oracle=burn.oracle, quantity=1)

        response = self.client.get(
            reverse("cubes:stats", kwargs={"pk": cube.pk}),
            {"stats_filter": "1", "stats": ["query_removal"]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([indicator["key"] for indicator in response.context["indicators"]], ["query_removal"])
        self.assertContains(response, "Removal 2 (#1)")

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

    def test_cube_removal_plan_prefers_card_fixing_overloaded_indicator(self):
        first_creature = self.create_printing("A Creature", "common", "Creature")
        second_creature = self.create_printing("B Creature", "common", "Creature")
        third_creature = self.create_printing("C Creature", "common", "Creature")
        first_land = self.create_printing("A Land", "common", "Land")
        second_land = self.create_printing("B Land", "common", "Land")
        cube = Cube.objects.create(owner=self.user, name="Removal Cube", booster_size=2)
        CubeCard.objects.create(cube=cube, oracle=first_creature.oracle)
        CubeCard.objects.create(cube=cube, oracle=second_creature.oracle)
        CubeCard.objects.create(cube=cube, oracle=third_creature.oracle)
        CubeCard.objects.create(cube=cube, oracle=first_land.oracle)
        CubeCard.objects.create(cube=cube, oracle=second_land.oracle)
        indicators = [indicator for indicator in SET_INDICATORS if indicator.key in {"creatures", "lands"}]
        benchmarks = {
            "creatures": {"q1": 0.5, "q3": 1.0},
            "lands": {"q1": 0.5, "q3": 1.0},
        }

        plan = build_cube_removal_plan(
            list(cube.cards.select_related("oracle")), 2, indicators, benchmarks, max_steps=1
        )

        self.assertEqual(len(plan["steps"]), 1)
        self.assertEqual(plan["steps"][0]["cube_card"].oracle.name, "A Creature")
        self.assertEqual(plan["final_score"], 0)

    def test_cube_removal_plan_can_add_card_from_available_pool(self):
        first_land = self.create_printing("A Land", "common", "Land")
        second_land = self.create_printing("B Land", "common", "Land")
        candidate_creature = self.create_printing("Candidate Creature", "common", "Creature")
        cube = Cube.objects.create(owner=self.user, name="Addition Cube", booster_size=2)
        CubeCard.objects.create(cube=cube, oracle=first_land.oracle)
        CubeCard.objects.create(cube=cube, oracle=second_land.oracle)
        indicators = [indicator for indicator in SET_INDICATORS if indicator.key in {"creatures", "lands"}]
        benchmarks = {
            "creatures": {"q1": 0.5, "q3": 1.0},
            "lands": {"q1": 0.5, "q3": 2.0},
        }

        plan = build_cube_removal_plan(
            list(cube.cards.select_related("oracle")),
            2,
            indicators,
            benchmarks,
            max_steps=1,
            available_sets=Set.objects.filter(pk=self.card_set.pk),
        )

        self.assertEqual(len(plan["steps"]), 1)
        self.assertEqual(plan["steps"][0]["action"], "add")
        self.assertEqual(plan["steps"][0]["oracle"], candidate_creature.oracle)

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

    def test_cached_set_indicator_benchmarks_populate_expected_values(self):
        self.create_printing("Common Creature", "common", "Creature")
        self.create_printing("Common Instant", "common", "Instant")
        creature_indicator = next(indicator for indicator in SET_INDICATORS if indicator.key == "creatures")

        benchmarks = build_cached_set_indicator_benchmarks([creature_indicator])

        self.assertIn("creatures", benchmarks)
        expected_value = SetIndicatorExpectedValue.objects.get(
            set=self.card_set,
            indicator_key="creatures",
            stat_query__isnull=True,
        )
        self.assertAlmostEqual(expected_value.expected, 11 / 2)

    def test_refresh_set_indicator_values_command_refreshes_code_indicators(self):
        self.create_printing("Common Creature", "common", "Creature")
        self.create_printing("Common Instant", "common", "Instant")

        call_command("refresh_set_indicator_values", "--source", "code", stdout=StringIO())

        expected_value = SetIndicatorExpectedValue.objects.get(
            set=self.card_set,
            indicator_key="creatures",
            stat_query__isnull=True,
        )
        self.assertAlmostEqual(expected_value.expected, 11 / 2)

    def test_refresh_stat_query_cache_command_refreshes_query_indicator_expected_values(self):
        self.create_printing("Doom Blade", "common", "Instant", oracle_text="Destroy target creature.")
        self.create_printing("Path", "common", "Instant", oracle_text="Exile target creature.")
        self.create_printing("Bear", "common", "Creature")
        stat_query = StatQuery.objects.create(owner=self.user, name="removal", raw_query="text:destroy OR text:exile")

        call_command("refresh_stat_query_cache", "--query-id", stat_query.pk, stdout=StringIO())

        expected_value = SetIndicatorExpectedValue.objects.get(
            set=self.card_set,
            indicator_key="query_removal",
            stat_query=stat_query,
        )
        self.assertAlmostEqual(expected_value.expected, 22 / 3)

    def test_query_edit_refreshes_query_indicator_expected_values(self):
        self.create_printing("Doom Blade", "common", "Instant", oracle_text="Destroy target creature.")
        self.create_printing("Path", "common", "Instant", oracle_text="Exile target creature.")
        self.create_printing("Swords", "common", "Instant", oracle_text="Exile target creature.")
        self.create_printing("Bear", "common", "Creature")
        self.client.post(
            reverse("stats:query_create"),
            {"name": "removal", "raw_query": "text:destroy", "description": ""},
        )
        stat_query = StatQuery.objects.get(name="removal")

        expected_value = SetIndicatorExpectedValue.objects.get(
            set=self.card_set,
            indicator_key="query_removal",
            stat_query=stat_query,
        )
        self.assertAlmostEqual(expected_value.expected, 11 / 4)

        self.client.post(
            reverse("stats:query_edit", kwargs={"pk": stat_query.pk}),
            {"name": "removal", "raw_query": "text:exile", "description": ""},
        )

        expected_value = SetIndicatorExpectedValue.objects.get(
            set=self.card_set,
            indicator_key="query_removal",
            stat_query=stat_query,
        )
        self.assertAlmostEqual(expected_value.expected, 22 / 4)

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

    def test_query_reference_uses_match_cache_in_cube_search(self):
        stat_query = StatQuery.objects.create(
            owner=self.user,
            name="Removal",
            raw_query="text:destroy",
            match_cache_refreshed_at=timezone.now(),
        )
        cached_card = self.create_printing("Cached Match", "common", "Instant", oracle_text="Draw a card.")
        direct_match = self.create_printing("Direct Match", "common", "Instant", oracle_text="Destroy target creature.")
        StatQueryMatch.objects.create(stat_query=stat_query, oracle=cached_card.oracle)
        cube = Cube.objects.create(owner=self.user, name="Cached Query Cube")
        cached_cube_card = CubeCard.objects.create(cube=cube, oracle=cached_card.oracle)
        CubeCard.objects.create(cube=cube, oracle=direct_match.oracle)

        count, rows = count_cube_matches(
            list(cube.cards.select_related("oracle")),
            'query:"Removal"',
            stat_queries=StatQuery.objects.filter(owner=self.user),
        )

        self.assertEqual(count, 1)
        self.assertEqual(rows, [cached_cube_card])

    def test_tag_query_reference_keeps_direct_cube_matching(self):
        stat_query = StatQuery.objects.create(
            owner=self.user,
            name="Tagged Removal",
            raw_query="tag:removal",
            match_cache_refreshed_at=timezone.now(),
        )
        tagged_card = self.create_printing("Tagged Card", "common", "Instant")
        cached_only_card = self.create_printing("Cached Only", "common", "Instant")
        StatQueryMatch.objects.create(stat_query=stat_query, oracle=cached_only_card.oracle)
        cube = Cube.objects.create(owner=self.user, name="Tagged Query Cube")
        tagged_cube_card = CubeCard.objects.create(cube=cube, oracle=tagged_card.oracle, tags=["removal"])
        CubeCard.objects.create(cube=cube, oracle=cached_only_card.oracle)

        count, rows = count_cube_matches(
            list(cube.cards.select_related("oracle")),
            'query:"Tagged Removal"',
            stat_queries=StatQuery.objects.filter(owner=self.user),
        )

        self.assertEqual(count, 1)
        self.assertEqual(rows, [tagged_cube_card])

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
