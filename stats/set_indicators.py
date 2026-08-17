from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Exists, OuterRef, Q

from cards.models import DEFAULT_AVAILABLE_SET_TYPES, CardOracle, CardPrinting
from stats.models import SetIndicatorExpectedValue
from stats.query_cache import get_visible_stat_queries_for_cache, refresh_stat_query_cache
from stats.query_engine import QuerySyntaxError

RARE_SLOT_MYTHIC_RATE = 1 / 8
REMOVAL_TERMS = ("destroy", "exile", "damage", "-x/-x", "deals", "sacrifice")


@dataclass(frozen=True)
class BoosterSlot:
    label: str
    rarities: tuple[str, ...]
    draws: float
    display_draws: str


@dataclass(frozen=True)
class SetIndicator:
    key: str
    label: str
    description: str
    matcher: object
    stat_query_id: int | None = None


SET_INDICATORS = (
    SetIndicator("white", "Blanc", "Cartes blanches", lambda oracle: has_color(oracle, "W")),
    SetIndicator("blue", "Bleu", "Cartes bleues", lambda oracle: has_color(oracle, "U")),
    SetIndicator("black", "Noir", "Cartes noires", lambda oracle: has_color(oracle, "B")),
    SetIndicator("red", "Rouge", "Cartes rouges", lambda oracle: has_color(oracle, "R")),
    SetIndicator("green", "Vert", "Cartes vertes", lambda oracle: has_color(oracle, "G")),
    SetIndicator("colorless", "Incolores", "Cartes sans couleur", lambda oracle: len(oracle.colors or []) == 0),
    SetIndicator(
        "multicolor", "Multicolores", "Cartes avec au moins deux couleurs", lambda oracle: len(oracle.colors or []) >= 2
    ),
    SetIndicator("mv_0", "MV 0", "Cartes avec mana value 0", lambda oracle: compare_mv(oracle, "eq", 0)),
    SetIndicator("mv_1", "MV 1", "Cartes avec mana value 1", lambda oracle: compare_mv(oracle, "eq", 1)),
    SetIndicator("mv_2", "MV 2", "Cartes avec mana value 2", lambda oracle: compare_mv(oracle, "eq", 2)),
    SetIndicator("mv_3", "MV 3", "Cartes avec mana value 3", lambda oracle: compare_mv(oracle, "eq", 3)),
    SetIndicator("mv_4", "MV 4", "Cartes avec mana value 4", lambda oracle: compare_mv(oracle, "eq", 4)),
    SetIndicator("mv_5", "MV 5", "Cartes avec mana value 5", lambda oracle: compare_mv(oracle, "eq", 5)),
    SetIndicator("mv_6", "MV 6", "Cartes avec mana value 6", lambda oracle: compare_mv(oracle, "eq", 6)),
    SetIndicator("mv_7_plus", "MV 7+", "Cartes avec mana value 7 ou plus", lambda oracle: compare_mv(oracle, "gte", 7)),
    SetIndicator("creatures", "Creatures", "Toutes les creatures", lambda oracle: has_type(oracle, "creature")),
    SetIndicator("lands", "Terrains", "Toutes les cartes de terrain", lambda oracle: has_type(oracle, "land")),
    SetIndicator("artifacts", "Artefacts", "Toutes les cartes d'artefact", lambda oracle: has_type(oracle, "artifact")),
    SetIndicator(
        "enchantments",
        "Enchantements",
        "Toutes les cartes d'enchantement",
        lambda oracle: has_type(oracle, "enchantment"),
    ),
    SetIndicator("instants", "Ephemeres", "Toutes les cartes d'ephemere", lambda oracle: has_type(oracle, "instant")),
    SetIndicator("sorceries", "Rituels", "Toutes les cartes de rituel", lambda oracle: has_type(oracle, "sorcery")),
    SetIndicator(
        "planeswalkers", "Planeswalkers", "Tous les planeswalkers", lambda oracle: has_type(oracle, "planeswalker")
    ),
    SetIndicator("bicolor", "Bicolores", "Cartes exactement bicolores", lambda oracle: len(oracle.colors or []) == 2),
    SetIndicator(
        "flying_creatures",
        "Creatures volantes",
        "Creatures avec la capacite vol",
        lambda oracle: has_type(oracle, "creature") and has_keyword(oracle, "flying"),
    ),
    SetIndicator("removal", "Removal", "Cartes qui retirent ou gerent une menace", lambda oracle: is_removal(oracle)),
    SetIndicator(
        "fixing", "Fixing", "Cartes qui aident a produire ou chercher du mana", lambda oracle: is_fixing(oracle)
    ),
    SetIndicator("cheap", "MV <= 2", "Cartes avec mana value 2 ou moins", lambda oracle: compare_mv(oracle, "lte", 2)),
    SetIndicator(
        "expensive", "MV >= 6", "Cartes avec mana value 6 ou plus", lambda oracle: compare_mv(oracle, "gte", 6)
    ),
)

QUERY_INDICATOR_DEFINITIONS = (
    {
        "query_name": "removal",
        "key": "query_removal",
        "label": "Removal 2",
        "description": "Cartes correspondant a la requete sauvegardee removal",
    },
)


def infer_booster_profile(printings):
    has_mythics = has_rarity(printings, "mythic")
    common_draws = 10 if has_mythics else 11
    slots = [
        BoosterSlot("common", ("common",), common_draws, str(common_draws)),
        BoosterSlot("uncommon", ("uncommon",), 3, "3"),
    ]
    if has_mythics:
        slots.extend(
            [
                BoosterSlot("rare", ("rare",), 1 - RARE_SLOT_MYTHIC_RATE, "7/8"),
                BoosterSlot("mythic", ("mythic",), RARE_SLOT_MYTHIC_RATE, "1/8"),
            ]
        )
    else:
        slots.append(BoosterSlot("rare", ("rare",), 1, "1"))
    return slots


def build_booster_slots(printings, matching_printings):
    matching_ids_by_rarity = group_matching_ids_by_rarity(matching_printings)
    slots = []
    rarity_rows = []
    alternate_hit_probability = 0
    has_alternate_slot = False
    for profile_slot in infer_booster_profile(printings):
        rarity_printings = printings.filter(rarity__in=profile_slot.rarities)
        population_size = rarity_printings.count()
        if not population_size:
            continue
        if profile_slot.draws < 1:
            has_alternate_slot = True
            success_count = sum(len(matching_ids_by_rarity.get(rarity, set())) for rarity in profile_slot.rarities)
            hit_rate = success_count / population_size if population_size else 0
            alternate_hit_probability += profile_slot.draws * hit_rate
        else:
            draw_size = min(int(profile_slot.draws), population_size)
            success_count = sum(len(matching_ids_by_rarity.get(rarity, set())) for rarity in profile_slot.rarities)
            slots.append((population_size, success_count, draw_size))
        rarity_rows.append(
            {
                "rarity": profile_slot.label,
                "population": population_size,
                "matching": sum(len(matching_ids_by_rarity.get(rarity, set())) for rarity in profile_slot.rarities),
                "draws": profile_slot.display_draws,
            }
        )
    if has_alternate_slot:
        slots.append({0: 1 - alternate_hit_probability, 1: alternate_hit_probability})
    return slots, rarity_rows


def build_set_indicators(printings, indicators=None):
    indicators = indicators or SET_INDICATORS
    profile = infer_booster_profile(printings)
    return [build_indicator_row(printings, profile, indicator) for indicator in indicators]


def build_set_indicator_benchmarks(printings_by_set, indicators=None):
    indicators = indicators or SET_INDICATORS
    expected_values_by_key = {indicator.key: [] for indicator in indicators}

    for printings in printings_by_set:
        if not printings:
            continue
        profile = infer_booster_profile(printings)
        for indicator in indicators:
            expected_values_by_key[indicator.key].append(calculate_expected_value(printings, profile, indicator))

    return {key: build_boxplot(values) for key, values in expected_values_by_key.items()}


def build_cached_set_indicator_benchmarks(indicators):
    benchmarks = {}
    for indicator in indicators:
        ensure_set_indicator_expected_values(indicator)
        values = list(get_expected_value_queryset(indicator).values_list("expected", flat=True))
        benchmarks[indicator.key] = build_boxplot(values)
    return benchmarks


def ensure_set_indicator_expected_values(indicator):
    if not get_expected_value_queryset(indicator).exists():
        refresh_set_indicator_expected_values(indicator)


def refresh_set_indicator_expected_values(indicator, printings_by_set=None):
    printings_by_set = printings_by_set or get_official_set_printings()
    rows = []
    for printings in printings_by_set:
        if not printings:
            continue
        profile = infer_booster_profile(printings)
        rows.append(
            SetIndicatorExpectedValue(
                set_id=printings[0].set_id,
                indicator_key=indicator.key,
                stat_query_id=indicator.stat_query_id,
                expected=calculate_expected_value(printings, profile, indicator),
            )
        )

    with transaction.atomic():
        get_expected_value_queryset(indicator).delete()
        SetIndicatorExpectedValue.objects.bulk_create(rows, ignore_conflicts=True)


def refresh_code_indicator_expected_values(indicators=None):
    indicators = indicators or SET_INDICATORS
    printings_by_set = get_official_set_printings()
    for indicator in indicators:
        refresh_set_indicator_expected_values(indicator, printings_by_set)


def refresh_query_indicator_expected_values_for_queries(stat_queries):
    for stat_query in stat_queries:
        indicator = build_query_indicator_for_stat_query(stat_query)
        if indicator:
            refresh_set_indicator_expected_values(indicator)


def build_query_indicator_for_stat_query(stat_query):
    definition = get_query_indicator_definition(stat_query)
    if not definition:
        return None
    matcher = build_stat_query_matcher(stat_query, None)
    if matcher is None:
        return None
    return SetIndicator(definition["key"], definition["label"], definition["description"], matcher, stat_query.pk)


def get_query_indicator_definition(stat_query):
    for definition in QUERY_INDICATOR_DEFINITIONS:
        if stat_query.name.lower() == definition["query_name"].lower():
            return definition
    return None


def get_expected_value_queryset(indicator):
    queryset = SetIndicatorExpectedValue.objects.filter(indicator_key=indicator.key)
    if indicator.stat_query_id is None:
        return queryset.filter(stat_query__isnull=True)
    return queryset.filter(stat_query_id=indicator.stat_query_id)


def get_official_set_printings():
    printings = (
        CardPrinting.objects.filter(set__set_type__in=DEFAULT_AVAILABLE_SET_TYPES, lang="en")
        .select_related("oracle", "set")
        .order_by("set_id")
    )
    printings_by_set = []
    current_set_id = None
    current_printings = []
    for printing in printings:
        if current_set_id is not None and printing.set_id != current_set_id:
            printings_by_set.append(current_printings)
            current_printings = []
        current_set_id = printing.set_id
        current_printings.append(printing)
    if current_printings:
        printings_by_set.append(current_printings)
    return printings_by_set


def build_indicator_options(selected_stat_keys, indicators=None):
    indicators = indicators or SET_INDICATORS
    selected_stat_keys = set(selected_stat_keys)
    return [
        {
            "key": indicator.key,
            "label": indicator.label,
            "selected": indicator.key in selected_stat_keys,
        }
        for indicator in indicators
    ]


def get_selected_indicator_keys(request, indicators=None):
    indicators = indicators or SET_INDICATORS
    params = request.POST if request.method == "POST" else request.GET
    selected_stat_keys = params.getlist("stats")
    available_stat_keys = {indicator.key for indicator in indicators}
    if not selected_stat_keys and "stats_filter" not in params:
        selected_stat_keys = [indicator.key for indicator in indicators]
    return [key for key in selected_stat_keys if key in available_stat_keys]


def attach_benchmarks(indicators, benchmarks):
    for indicator in indicators:
        benchmark = benchmarks.get(indicator["key"])
        indicator["benchmark"] = benchmark
        if benchmark:
            indicator["marker_position"] = scale_boxplot_value(indicator["expected"], benchmark["d1"], benchmark["d9"])
    return indicators


def attach_removal_projection(indicators, removal_plan, benchmarks):
    expected_by_key = removal_plan.get("final_expected_by_key", {})
    for indicator in indicators:
        if indicator["key"] not in expected_by_key:
            continue
        indicator["projected_expected"] = expected_by_key[indicator["key"]]
        benchmark = benchmarks.get(indicator["key"])
        if benchmark:
            indicator["projected_marker_position"] = scale_boxplot_value(
                indicator["projected_expected"], benchmark["d1"], benchmark["d9"]
            )
    return indicators


def build_cube_indicators(cube_cards, booster_size, indicators=None):
    indicators = indicators or SET_INDICATORS
    total_cards = sum(cube_card.quantity for cube_card in cube_cards)
    rows = []
    for indicator in indicators:
        matching_count = sum(cube_card.quantity for cube_card in cube_cards if indicator.matcher(cube_card.oracle))
        expected = booster_size * matching_count / total_cards if total_cards else 0
        rows.append(
            {
                "key": indicator.key,
                "label": indicator.label,
                "description": indicator.description,
                "matching_count": matching_count,
                "expected": expected,
            }
        )
    return rows


def build_cube_removal_plan(cube_cards, booster_size, indicators, benchmarks, max_steps=5, available_sets=None):
    total_cards = sum(cube_card.quantity for cube_card in cube_cards)
    indicator_matches = {
        indicator.key: {cube_card.pk for cube_card in cube_cards if indicator.matcher(cube_card.oracle)}
        for indicator in indicators
    }
    matching_counts = {
        indicator.key: sum(
            cube_card.quantity for cube_card in cube_cards if cube_card.pk in indicator_matches[indicator.key]
        )
        for indicator in indicators
    }
    remaining_quantities = {cube_card.pk: cube_card.quantity for cube_card in cube_cards}
    cube_cards_by_id = {cube_card.pk: cube_card for cube_card in cube_cards}
    excluded_oracle_ids = {cube_card.oracle_id for cube_card in cube_cards}
    current_score = calculate_cube_balance_score(
        matching_counts, total_cards, min(booster_size, total_cards), benchmarks
    )
    initial_score = current_score
    steps = []
    if total_cards <= 1 or max_steps <= 0:
        return build_removal_plan_result(
            steps,
            initial_score,
            current_score,
            current_score == 0,
            matching_counts,
            total_cards,
            min(booster_size, total_cards),
        )

    for step_number in range(1, max_steps + 1):
        if current_score == 0:
            break

        best_candidate = None
        for cube_card in cube_cards:
            if remaining_quantities[cube_card.pk] <= 0:
                continue
            next_total = total_cards - 1
            next_booster_size = min(booster_size, next_total)
            next_counts = matching_counts.copy()
            for indicator in indicators:
                if cube_card.pk in indicator_matches[indicator.key]:
                    next_counts[indicator.key] -= 1

            next_score = calculate_cube_balance_score(next_counts, next_total, next_booster_size, benchmarks)
            gain = current_score - next_score
            candidate = {
                "action": "remove",
                "cube_card": cube_card,
                "name": cube_card.oracle.name,
                "matching_counts": next_counts,
                "total_cards": next_total,
                "score": next_score,
                "gain": gain,
                "impacts": build_action_impacts(
                    cube_card.oracle,
                    indicators,
                    matching_counts,
                    total_cards,
                    min(booster_size, total_cards),
                    next_counts,
                    next_total,
                    next_booster_size,
                ),
            }
            if is_better_candidate(candidate, best_candidate):
                best_candidate = candidate

        for oracle in build_addition_candidates(
            available_sets,
            excluded_oracle_ids,
            indicators,
            matching_counts,
            total_cards,
            min(booster_size, total_cards),
            benchmarks,
        ):
            next_total = total_cards + 1
            next_booster_size = min(booster_size, next_total)
            next_counts = matching_counts.copy()
            for indicator in indicators:
                if indicator.matcher(oracle):
                    next_counts[indicator.key] += 1

            next_score = calculate_cube_balance_score(next_counts, next_total, next_booster_size, benchmarks)
            gain = current_score - next_score
            candidate = {
                "action": "add",
                "oracle": oracle,
                "name": oracle.name,
                "matching_counts": next_counts,
                "total_cards": next_total,
                "score": next_score,
                "gain": gain,
                "impacts": build_action_impacts(
                    oracle,
                    indicators,
                    matching_counts,
                    total_cards,
                    min(booster_size, total_cards),
                    next_counts,
                    next_total,
                    next_booster_size,
                ),
            }
            if is_better_candidate(candidate, best_candidate):
                best_candidate = candidate

        if best_candidate is None or best_candidate["gain"] <= 0:
            break

        matching_counts = best_candidate["matching_counts"]
        total_cards = best_candidate["total_cards"]
        current_score = best_candidate["score"]
        if best_candidate["action"] == "remove":
            remaining_quantities[best_candidate["cube_card"].pk] -= 1
            step = {
                "number": step_number,
                "action": "remove",
                "cube_card": cube_cards_by_id[best_candidate["cube_card"].pk],
                "name": best_candidate["name"],
                "gain": best_candidate["gain"],
                "score_after": current_score,
                "impacts": best_candidate["impacts"],
            }
        else:
            excluded_oracle_ids.add(best_candidate["oracle"].pk)
            step = {
                "number": step_number,
                "action": "add",
                "oracle": best_candidate["oracle"],
                "name": best_candidate["name"],
                "gain": best_candidate["gain"],
                "score_after": current_score,
                "impacts": best_candidate["impacts"],
            }
        steps.append(step)

    return build_removal_plan_result(
        steps,
        initial_score,
        current_score,
        current_score == 0,
        matching_counts,
        total_cards,
        min(booster_size, total_cards),
    )


def build_removal_plan_result(steps, initial_score, final_score, balanced, matching_counts, total_cards, booster_size):
    final_expected_by_key = {}
    for key, matching_count in matching_counts.items():
        final_expected_by_key[key] = booster_size * matching_count / total_cards if total_cards else 0
    return {
        "steps": steps,
        "initial_score": initial_score,
        "final_score": final_score,
        "balanced": balanced,
        "final_expected_by_key": final_expected_by_key,
    }


def is_better_candidate(candidate, best_candidate):
    if best_candidate is None:
        return True
    return candidate["gain"] > best_candidate["gain"] or (
        candidate["gain"] == best_candidate["gain"] and candidate["name"].lower() < best_candidate["name"].lower()
    )


def build_addition_candidates(
    available_sets, excluded_oracle_ids, indicators, matching_counts, total_cards, booster_size, benchmarks, limit=300
):
    if available_sets is None:
        return []

    available_printings = CardPrinting.objects.filter(oracle=OuterRef("pk"), set__in=available_sets, lang="en")
    base_queryset = CardOracle.objects.filter(Exists(available_printings)).exclude(pk__in=excluded_oracle_ids)
    desired_constraints, avoided_constraints = build_addition_constraints(
        indicators, matching_counts, total_cards, booster_size, benchmarks
    )

    for desired_count in range(min(3, len(desired_constraints)), -1, -1):
        for avoided_count in range(min(3, len(avoided_constraints)), -1, -1):
            queryset = base_queryset
            for constraint in desired_constraints[:desired_count]:
                queryset = queryset.filter(constraint["query"])
            for constraint in avoided_constraints[:avoided_count]:
                queryset = queryset.exclude(constraint["query"])
            candidates = list(queryset.order_by("name")[:limit])
            if candidates:
                return candidates
    return []


def build_addition_constraints(indicators, matching_counts, total_cards, booster_size, benchmarks):
    desired_constraints = []
    avoided_constraints = []
    if total_cards <= 0:
        return desired_constraints, avoided_constraints

    for indicator in indicators:
        benchmark = benchmarks.get(indicator.key)
        query = get_indicator_query(indicator.key)
        if not benchmark or query is None:
            continue
        expected = booster_size * matching_counts[indicator.key] / total_cards
        q1 = benchmark["q1"]
        q3 = benchmark["q3"]
        width = q3 - q1
        if width <= 0:
            continue
        if expected < q1:
            desired_constraints.append({"query": query, "weight": (q1 - expected) / width})
        elif expected > q3:
            avoided_constraints.append({"query": query, "weight": (expected - q3) / width})

    desired_constraints.sort(key=lambda constraint: constraint["weight"], reverse=True)
    avoided_constraints.sort(key=lambda constraint: constraint["weight"], reverse=True)
    return desired_constraints, avoided_constraints


def get_indicator_query(key):
    color_queries = {
        "white": Q(colors__contains=["W"]),
        "blue": Q(colors__contains=["U"]),
        "black": Q(colors__contains=["B"]),
        "red": Q(colors__contains=["R"]),
        "green": Q(colors__contains=["G"]),
    }
    if key in color_queries:
        return color_queries[key]
    if key == "colorless":
        return Q(colors=[])
    if key.startswith("mv_") and key != "mv_7_plus":
        return Q(mana_value=int(key.removeprefix("mv_")))
    if key == "mv_7_plus":
        return Q(mana_value__gte=7)
    type_queries = {
        "creatures": "creature",
        "lands": "land",
        "artifacts": "artifact",
        "enchantments": "enchantment",
        "instants": "instant",
        "sorceries": "sorcery",
        "planeswalkers": "planeswalker",
    }
    if key in type_queries:
        return Q(type_line__icontains=type_queries[key])
    if key == "cheap":
        return Q(mana_value__lte=2)
    if key == "expensive":
        return Q(mana_value__gte=6)
    return None


def calculate_cube_balance_score(matching_counts, total_cards, booster_size, benchmarks):
    if total_cards <= 0:
        return 0
    score = 0
    for key, matching_count in matching_counts.items():
        benchmark = benchmarks.get(key)
        if not benchmark:
            continue
        expected = booster_size * matching_count / total_cards
        imbalance = calculate_indicator_imbalance(expected, benchmark)
        score += imbalance * imbalance
    return score


def calculate_indicator_imbalance(value, benchmark):
    q1 = benchmark["q1"]
    q3 = benchmark["q3"]
    width = q3 - q1
    if width <= 0:
        return 0
    if value < q1:
        return (q1 - value) / width
    if value > q3:
        return (value - q3) / width
    return 0


def build_action_impacts(
    oracle,
    indicators,
    current_counts,
    current_total,
    current_booster_size,
    next_counts,
    next_total,
    next_booster_size,
):
    impacts = []
    for indicator in indicators:
        if not indicator.matcher(oracle):
            continue
        before = current_booster_size * current_counts[indicator.key] / current_total if current_total else 0
        after = next_booster_size * next_counts[indicator.key] / next_total if next_total else 0
        impacts.append(
            {
                "label": indicator.label,
                "before": before,
                "after": after,
                "delta": after - before,
            }
        )
    return impacts


def build_available_indicators(stat_queries=None):
    return SET_INDICATORS + tuple(build_query_indicators(stat_queries))


def build_query_indicators(stat_queries=None):
    if stat_queries is None:
        return []

    indicators = []
    for definition in QUERY_INDICATOR_DEFINITIONS:
        stat_query = stat_queries.filter(name__iexact=definition["query_name"]).first()
        if not stat_query:
            continue
        matcher = build_stat_query_matcher(stat_query, stat_queries)
        if matcher is None:
            continue
        indicators.append(
            SetIndicator(definition["key"], definition["label"], definition["description"], matcher, stat_query.pk)
        )
    return indicators


def build_stat_query_matcher(stat_query, stat_queries):
    if stat_query.match_cache_refreshed_at is None:
        try:
            stat_queries = stat_queries or get_visible_stat_queries_for_cache(stat_query)
            refresh_stat_query_cache(stat_query, stat_queries)
        except QuerySyntaxError:
            return None
    matching_oracle_ids = set(stat_query.matches.values_list("oracle_id", flat=True))
    return lambda oracle: oracle.pk in matching_oracle_ids


def build_indicator_row(printings, profile, indicator):
    matching_printings = [printing for printing in printings if indicator.matcher(printing.oracle)]
    matching_ids_by_rarity = group_matching_ids_by_rarity(matching_printings)
    detail_rows = []
    expected = 0
    for slot in profile:
        rarity_printings = [printing for printing in printings if printing.rarity in slot.rarities]
        population = len(rarity_printings)
        matching = sum(len(matching_ids_by_rarity.get(rarity, set())) for rarity in slot.rarities)
        rate = matching / population if population else 0
        contribution = slot.draws * rate
        expected += contribution
        detail_rows.append(
            {
                "rarity": slot.label,
                "draws": slot.display_draws,
                "population": population,
                "matching": matching,
                "expected": contribution,
            }
        )
    return {
        "key": indicator.key,
        "label": indicator.label,
        "description": indicator.description,
        "matching_count": len(matching_printings),
        "expected": expected,
        "detail_rows": detail_rows,
    }


def calculate_expected_value(printings, profile, indicator):
    expected = 0
    for slot in profile:
        rarity_printings = [printing for printing in printings if printing.rarity in slot.rarities]
        population = len(rarity_printings)
        if not population:
            continue
        matching = sum(1 for printing in rarity_printings if indicator.matcher(printing.oracle))
        expected += slot.draws * (matching / population)
    return expected


def build_boxplot(values):
    if not values:
        return None

    sorted_values = sorted(values)
    d1 = percentile(sorted_values, 0.1)
    q1 = percentile(sorted_values, 0.25)
    median = percentile(sorted_values, 0.5)
    q3 = percentile(sorted_values, 0.75)
    d9 = percentile(sorted_values, 0.9)
    q1_position = scale_boxplot_value(q1, d1, d9)
    q3_position = scale_boxplot_value(q3, d1, d9)
    return {
        "count": len(sorted_values),
        "d1": d1,
        "q1": q1,
        "median": median,
        "q3": q3,
        "d9": d9,
        "q1_position": q1_position,
        "median_position": scale_boxplot_value(median, d1, d9),
        "q3_position": q3_position,
        "box_width": q3_position - q1_position,
    }


def percentile(sorted_values, ratio):
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * ratio
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    fraction = position - lower_index
    return sorted_values[lower_index] + (sorted_values[upper_index] - sorted_values[lower_index]) * fraction


def scale_boxplot_value(value, minimum, maximum):
    if maximum == minimum:
        return 50
    return max(0, min(100, ((value - minimum) / (maximum - minimum)) * 100))


def group_matching_ids_by_rarity(printings):
    matching_ids_by_rarity = {}
    for printing in printings:
        matching_ids_by_rarity.setdefault(printing.rarity, set()).add(printing.pk)
    return matching_ids_by_rarity


def has_rarity(printings, rarity):
    if hasattr(printings, "filter"):
        return printings.filter(rarity=rarity).exists()
    return any(printing.rarity == rarity for printing in printings)


def has_type(oracle, card_type):
    return card_type in (oracle.type_line or "").lower()


def has_color(oracle, color):
    return color in (oracle.colors or [])


def has_keyword(oracle, keyword):
    return keyword.lower() in [card_keyword.lower() for card_keyword in oracle.keywords or []]


def compare_mv(oracle, operator, value):
    if oracle.mana_value is None:
        return False
    mana_value = Decimal(oracle.mana_value)
    if operator == "eq":
        return mana_value == value
    if operator == "lte":
        return mana_value <= value
    return mana_value >= value


def is_removal(oracle):
    text = (oracle.oracle_text or "").lower()
    return any(term in text for term in REMOVAL_TERMS)


def is_fixing(oracle):
    text = (oracle.oracle_text or "").lower()
    if has_type(oracle, "land") and (
        "add" in text or any(color in text for color in ("{w}", "{u}", "{b}", "{r}", "{g}"))
    ):
        return True
    if has_type(oracle, "artifact") and "add" in text and "mana" in text:
        return True
    return "search your library" in text and ("land" in text or "basic" in text)
