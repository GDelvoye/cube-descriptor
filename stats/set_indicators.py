from dataclasses import dataclass
from decimal import Decimal

from cards.models import DEFAULT_AVAILABLE_SET_TYPES, CardPrinting
from stats.query_cache import refresh_stat_query_cache
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


SET_INDICATORS = (
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
        "multicolor", "Multicolores", "Cartes avec au moins deux couleurs", lambda oracle: len(oracle.colors or []) >= 2
    ),
    SetIndicator("colorless", "Incolores", "Cartes sans couleur", lambda oracle: len(oracle.colors or []) == 0),
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
    selected_stat_keys = request.GET.getlist("stats")
    available_stat_keys = {indicator.key for indicator in indicators}
    if not selected_stat_keys and "stats_filter" not in request.GET:
        selected_stat_keys = [indicator.key for indicator in indicators]
    return [key for key in selected_stat_keys if key in available_stat_keys]


def attach_benchmarks(indicators, benchmarks):
    for indicator in indicators:
        benchmark = benchmarks.get(indicator["key"])
        indicator["benchmark"] = benchmark
        if benchmark:
            indicator["marker_position"] = scale_boxplot_value(indicator["expected"], benchmark["d1"], benchmark["d9"])
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
        indicators.append(SetIndicator(definition["key"], definition["label"], definition["description"], matcher))
    return indicators


def build_stat_query_matcher(stat_query, stat_queries):
    if stat_query.match_cache_refreshed_at is None:
        try:
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


def has_keyword(oracle, keyword):
    return keyword.lower() in [card_keyword.lower() for card_keyword in oracle.keywords or []]


def compare_mv(oracle, operator, value):
    if oracle.mana_value is None:
        return False
    mana_value = Decimal(oracle.mana_value)
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
