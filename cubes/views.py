from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Prefetch, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from cards.display import (
    apply_cube_card_display,
    apply_oracle_display,
    build_language_querystrings,
    get_display_language,
)
from cards.set_availability import get_available_sets
from stats.forms import CubeStatsForm
from stats.models import StatQuery
from stats.probabilities import probability_at_least, probability_between, probability_exactly
from stats.query_engine import QuerySyntaxError, count_cube_matches
from stats.set_indicators import (
    attach_benchmarks,
    attach_removal_projection,
    build_available_indicators,
    build_cached_set_indicator_benchmarks,
    build_cube_indicators,
    build_cube_removal_plan,
    build_indicator_options,
    get_selected_indicator_keys,
)

from .forms import CubeCardForm, CubeForm
from .models import Cube, CubeCard


def cube_list(request):
    public_cubes = Cube.objects.filter(visibility=Cube.Visibility.PUBLIC).annotate(card_total=Sum("cards__quantity"))
    owned_cubes = Cube.objects.none()
    if request.user.is_authenticated:
        owned_cubes = Cube.objects.filter(owner=request.user).annotate(card_total=Sum("cards__quantity"))
        public_cubes = public_cubes.exclude(owner=request.user)
    return render(request, "cubes/list.html", {"cubes": owned_cubes, "public_cubes": public_cubes})


@login_required
def cube_create(request):
    if request.method == "POST":
        form = CubeForm(request.POST)
        if form.is_valid():
            cube = form.save(commit=False)
            cube.owner = request.user
            cube.save()
            return redirect("cubes:detail", pk=cube.pk)
    else:
        form = CubeForm()
    return render(request, "cubes/form.html", {"form": form})


def cube_detail(request, pk):
    cube = get_object_or_404(
        Cube.objects.prefetch_related(
            Prefetch("cards", queryset=CubeCard.objects.select_related("oracle", "printing").order_by("oracle__name"))
        ).filter(get_accessible_cube_filter(request.user)),
        pk=pk,
    )
    can_edit = request.user.is_authenticated and cube.owner_id == request.user.pk
    cube_cards = list(cube.cards.all())
    total_cards = sum(cube_card.quantity for cube_card in cube_cards)
    display_language = get_display_language(request)
    available_sets = get_available_sets(request.user)
    visible_queries = get_visible_stat_queries(request.user, cube)
    selected_query = get_selected_stat_query(request, visible_queries)
    display_language = get_display_language(request)
    raw_query = (selected_query.raw_query if selected_query else request.GET.get("raw_query", "")).strip()
    filter_error = None
    filtered_cards = cube_cards
    available_sets = get_available_sets(request.user)
    if raw_query:
        try:
            _, matching_rows = count_cube_matches(
                cube_cards, raw_query, available_sets=available_sets, stat_queries=visible_queries
            )
            filtered_cards = matching_rows
        except QuerySyntaxError as exc:
            filter_error = str(exc)

    filtered_total = sum(cube_card.quantity for cube_card in filtered_cards)
    for cube_card in filtered_cards:
        cube_card.edit_form = CubeCardForm(instance=cube_card)
        cube_card.display_printing = (
            cube_card.printing
            or cube_card.oracle.printings.filter(set__in=available_sets)
            .order_by("released_at", "set_code", "collector_number")
            .first()
        )
        apply_cube_card_display(cube_card, display_language, available_sets)
        if cube_card.display_localized_printing and cube_card.display_localized_printing.image_url:
            cube_card.display_printing = cube_card.display_localized_printing
    cube_columns = build_cube_overview_columns(filtered_cards)
    return render(
        request,
        "cubes/detail.html",
        {
            "cube": cube,
            "cube_cards": filtered_cards,
            "cube_columns": cube_columns,
            "total_cards": total_cards,
            "filtered_total": filtered_total,
            "raw_query": raw_query,
            "filter_error": filter_error,
            "stat_queries": visible_queries,
            "selected_query": selected_query,
            "can_edit": can_edit,
            "display_language": display_language,
            "language_querystrings": build_language_querystrings(request),
        },
    )


def build_cube_overview_columns(cube_cards):
    column_definitions = [
        ("white", "White"),
        ("blue", "Blue"),
        ("black", "Black"),
        ("red", "Red"),
        ("green", "Green"),
        ("multicolored", "Multicolored"),
        ("colorless", "Colorless"),
        ("lands", "Lands"),
    ]
    section_definitions = [
        ("creatures", "Creatures"),
        ("instants", "Instants"),
        ("sorceries", "Sorceries"),
        ("artifacts", "Artifacts"),
        ("enchantments", "Enchantments"),
        ("planeswalkers", "Planeswalkers"),
        ("other", "Other"),
    ]
    columns = [
        {
            "key": key,
            "label": label,
            "count": 0,
            "sections": [
                {"key": section_key, "label": section_label, "cards": []}
                for section_key, section_label in section_definitions
            ],
        }
        for key, label in column_definitions
    ]
    columns_by_key = {column["key"]: column for column in columns}

    for cube_card in cube_cards:
        column = columns_by_key[get_cube_card_overview_column(cube_card)]
        section_key = get_cube_card_overview_section(cube_card)
        column["count"] += cube_card.quantity
        for section in column["sections"]:
            if section["key"] == section_key:
                section["cards"].append(cube_card)
                break

    for column in columns:
        for section in column["sections"]:
            section["cards"].sort(key=get_cube_card_sort_key)
            section["mana_value_groups"] = build_mana_value_groups(section["cards"])
            section["count"] = sum(cube_card.quantity for cube_card in section["cards"])
        column["sections"] = [section for section in column["sections"] if section["cards"]]
    return columns


def build_mana_value_groups(cube_cards):
    groups = []
    current_mana_value = object()
    for cube_card in cube_cards:
        mana_value = get_cube_card_mana_value(cube_card)
        if mana_value != current_mana_value:
            groups.append({"mana_value": mana_value, "cards": []})
            current_mana_value = mana_value
        groups[-1]["cards"].append(cube_card)
    return groups


def get_cube_card_sort_key(cube_card):
    return (get_cube_card_mana_value(cube_card), cube_card.oracle.name.lower())


def get_cube_card_mana_value(cube_card):
    if cube_card.oracle.mana_value is None:
        return 0
    return int(cube_card.oracle.mana_value)


def get_cube_card_overview_column(cube_card):
    if has_cube_card_type(cube_card, "land"):
        return "lands"
    colors = cube_card.oracle.colors or []
    if len(colors) >= 2:
        return "multicolored"
    if not colors:
        return "colorless"
    return {
        "W": "white",
        "U": "blue",
        "B": "black",
        "R": "red",
        "G": "green",
    }.get(colors[0], "colorless")


def get_cube_card_overview_section(cube_card):
    for card_type, section in [
        ("creature", "creatures"),
        ("instant", "instants"),
        ("sorcery", "sorceries"),
        ("artifact", "artifacts"),
        ("enchantment", "enchantments"),
        ("planeswalker", "planeswalkers"),
    ]:
        if has_cube_card_type(cube_card, card_type):
            return section
    return "other"


def has_cube_card_type(cube_card, card_type):
    return card_type in (cube_card.oracle.type_line or "").lower()


def cube_stats(request, pk):
    cube = get_object_or_404(
        Cube.objects.prefetch_related(
            Prefetch("cards", queryset=CubeCard.objects.select_related("oracle").order_by("oracle__name"))
        ).filter(get_accessible_cube_filter(request.user)),
        pk=pk,
    )
    can_edit = request.user.is_authenticated and cube.owner_id == request.user.pk
    cube_cards = list(cube.cards.all())
    total_cards = sum(cube_card.quantity for cube_card in cube_cards)
    display_language = get_display_language(request)
    available_sets = get_available_sets(request.user)
    visible_queries = get_visible_stat_queries(request.user, cube)
    selected_query = get_selected_stat_query(request, visible_queries)
    initial = {"raw_query": selected_query.raw_query} if selected_query and "raw_query" not in request.GET else None
    form = CubeStatsForm(request.GET or None)
    if initial and not request.GET.get("raw_query"):
        form = CubeStatsForm({**request.GET.dict(), **initial})
    indicators_available = build_available_indicators(visible_queries)
    selected_stat_keys = get_selected_indicator_keys(request, indicators_available)
    selected_indicators = [indicator for indicator in indicators_available if indicator.key in selected_stat_keys]
    booster_size = min(cube.booster_size, total_cards)
    indicators = [
        row
        for row in build_cube_indicators(cube_cards, booster_size, selected_indicators)
        if row["key"] in selected_stat_keys
    ]
    benchmarks = build_cached_set_indicator_benchmarks(selected_indicators)
    attach_benchmarks(indicators, benchmarks)
    max_removals = get_max_removals(request)
    removal_plan = build_adjustment_plan(
        cube, cube_cards, selected_indicators, benchmarks, max_removals, available_sets
    )
    attach_plan_step_images(removal_plan, display_language, available_sets)
    attach_removal_projection(indicators, removal_plan, benchmarks)
    result = None
    error = None

    if form.is_valid():
        try:
            matching_count, matching_rows = count_cube_matches(
                cube_cards, form.cleaned_data["raw_query"], available_sets=available_sets, stat_queries=visible_queries
            )
            for cube_card in matching_rows:
                apply_cube_card_display(cube_card, display_language, available_sets)
            minimum_hits = form.cleaned_data["minimum_hits"]
            exact_hits = form.cleaned_data["exact_hits"]
            between_min = form.cleaned_data["between_min"]
            between_max = form.cleaned_data["between_max"]
            result = {
                "total_cards": total_cards,
                "matching_count": matching_count,
                "booster_size": booster_size,
                "at_least": probability_at_least(total_cards, matching_count, booster_size, minimum_hits),
                "minimum_hits": minimum_hits,
                "matching_rows": matching_rows,
            }
            result["at_least_percent"] = result["at_least"] * 100
            if exact_hits is not None:
                result["exact_hits"] = exact_hits
                result["exactly"] = probability_exactly(total_cards, matching_count, booster_size, exact_hits)
                result["exactly_percent"] = result["exactly"] * 100
            if between_min is not None and between_max is not None:
                result["between_min"] = between_min
                result["between_max"] = between_max
                result["between"] = probability_between(
                    total_cards, matching_count, booster_size, between_min, between_max
                )
                result["between_percent"] = result["between"] * 100
        except QuerySyntaxError as exc:
            error = str(exc)
    elif request.GET:
        error = "Formulaire invalide."

    return render(
        request,
        "cubes/stats.html",
        {
            "cube": cube,
            "form": form,
            "stat_queries": visible_queries,
            "selected_query": selected_query,
            "result": result,
            "error": error,
            "indicators": indicators,
            "indicator_options": build_indicator_options(selected_stat_keys, indicators_available),
            "max_removals": max_removals,
            "removal_plan": removal_plan,
            "can_edit": can_edit,
            "total_cards": total_cards,
            "display_language": display_language,
            "language_querystrings": build_language_querystrings(request),
        },
    )


@login_required
@require_POST
def cube_apply_adjustment_plan(request, pk):
    cube = get_object_or_404(
        Cube.objects.prefetch_related(
            Prefetch("cards", queryset=CubeCard.objects.select_related("oracle", "printing").order_by("oracle__name"))
        ),
        pk=pk,
        owner=request.user,
    )
    cube_cards = list(cube.cards.all())
    available_sets = get_available_sets(request.user)
    visible_queries = get_visible_stat_queries(request.user, cube)
    indicators_available = build_available_indicators(visible_queries)
    selected_stat_keys = get_selected_indicator_keys(request, indicators_available)
    selected_indicators = [indicator for indicator in indicators_available if indicator.key in selected_stat_keys]
    benchmarks = build_cached_set_indicator_benchmarks(selected_indicators)
    max_removals = get_max_removals(request)
    adjustment_plan = build_adjustment_plan(
        cube, cube_cards, selected_indicators, benchmarks, max_removals, available_sets
    )

    with transaction.atomic():
        improved_cube = Cube.objects.create(
            owner=cube.owner,
            name=get_next_improved_cube_name(cube),
            description=cube.description,
            visibility=cube.visibility,
            booster_size=cube.booster_size,
        )
        for cube_card in cube_cards:
            CubeCard.objects.create(
                cube=improved_cube,
                oracle=cube_card.oracle,
                printing=cube_card.printing,
                quantity=cube_card.quantity,
                section=cube_card.section,
                tags=cube_card.tags,
                notes=cube_card.notes,
            )

        for step in adjustment_plan["steps"]:
            if step["action"] == "remove":
                remove_card_from_improved_cube(improved_cube, step["cube_card"])
            else:
                add_card_to_improved_cube(improved_cube, step["oracle"])

    return redirect("cubes:detail", pk=improved_cube.pk)


def build_adjustment_plan(cube, cube_cards, selected_indicators, benchmarks, max_removals, available_sets):
    return build_cube_removal_plan(
        cube_cards, cube.booster_size, selected_indicators, benchmarks, max_removals, available_sets=available_sets
    )


def attach_plan_step_images(adjustment_plan, display_language, available_sets):
    for step in adjustment_plan["steps"]:
        oracle = step["oracle"] if step["action"] == "add" else step["cube_card"].oracle
        apply_oracle_display(oracle, display_language, available_sets)
        printing = (
            oracle.display_localized_printing
            or oracle.printings.filter(set__in=available_sets)
            .order_by("released_at", "set_code", "collector_number")
            .first()
        )
        step["image_url"] = printing.image_url if printing else ""


def get_next_improved_cube_name(cube):
    for suffix in build_name_suffixes():
        name = f"{cube.name}_{suffix}"
        if not Cube.objects.filter(owner=cube.owner, name=name).exists():
            return name
    return f"{cube.name}_improved"


def build_name_suffixes():
    letters = "abcdefghijklmnopqrstuvwxyz"
    yield from letters
    for first in letters:
        for second in letters:
            yield f"{first}{second}"


def remove_card_from_improved_cube(improved_cube, original_cube_card):
    cube_card = CubeCard.objects.filter(
        cube=improved_cube,
        oracle=original_cube_card.oracle,
        section=original_cube_card.section,
    ).first()
    if not cube_card:
        return
    if cube_card.quantity > 1:
        cube_card.quantity -= 1
        cube_card.save(update_fields=["quantity", "updated_at"])
    else:
        cube_card.delete()


def add_card_to_improved_cube(improved_cube, oracle):
    cube_card, created = CubeCard.objects.get_or_create(
        cube=improved_cube,
        oracle=oracle,
        section=CubeCard.Section.MAIN,
        defaults={"quantity": 1},
    )
    if not created:
        cube_card.quantity += 1
        cube_card.save(update_fields=["quantity", "updated_at"])


def get_max_removals(request):
    params = request.POST if request.method == "POST" else request.GET
    raw_max_removals = params.get("max_removals", "5")
    if not raw_max_removals.isdigit():
        return 5
    return max(1, min(int(raw_max_removals), 50))


def get_accessible_cube_filter(user):
    public_filter = Q(visibility=Cube.Visibility.PUBLIC)
    if not user.is_authenticated:
        return public_filter
    return public_filter | Q(owner=user)


def get_visible_stat_queries(user, cube):
    queries = StatQuery.objects.filter(scope=StatQuery.Scope.GLOBAL, owner__isnull=True) | StatQuery.objects.filter(
        scope=StatQuery.Scope.CUBE, cube=cube
    )
    if user.is_authenticated:
        queries = queries | StatQuery.objects.filter(owner=user, scope=StatQuery.Scope.USER)
    return queries.order_by("scope", "name")


def get_selected_stat_query(request, visible_queries):
    stat_query_id = request.GET.get("stat_query")
    if not stat_query_id or not stat_query_id.isdigit():
        return None
    return visible_queries.filter(pk=stat_query_id).first()


@login_required
def cube_card_edit(request, pk, cube_card_id):
    cube = get_object_or_404(Cube, pk=pk, owner=request.user)
    cube_card = get_object_or_404(CubeCard, pk=cube_card_id, cube=cube)
    if request.method == "POST":
        form = CubeCardForm(request.POST, instance=cube_card)
        if form.is_valid():
            form.save()
            return redirect("cubes:detail", pk=cube.pk)
    else:
        form = CubeCardForm(instance=cube_card)

    display_language = get_display_language(request)
    available_sets = get_available_sets(request.user)
    cube_card.display_printing = (
        cube_card.printing
        or cube_card.oracle.printings.filter(set__in=available_sets)
        .order_by("released_at", "set_code", "collector_number")
        .first()
    )
    apply_cube_card_display(cube_card, display_language, available_sets)
    if cube_card.display_localized_printing and cube_card.display_localized_printing.image_url:
        cube_card.display_printing = cube_card.display_localized_printing
    return render(
        request,
        "cubes/card_edit.html",
        {
            "cube": cube,
            "cube_card": cube_card,
            "form": form,
            "display_language": display_language,
            "language_querystrings": build_language_querystrings(request),
        },
    )


@login_required
@require_POST
def cube_card_remove(request, pk, cube_card_id):
    cube = get_object_or_404(Cube, pk=pk, owner=request.user)
    cube_card = get_object_or_404(CubeCard, pk=cube_card_id, cube=cube)
    decrement = request.POST.get("decrement") == "1"
    if decrement and cube_card.quantity > 1:
        cube_card.quantity -= 1
        cube_card.save(update_fields=["quantity", "updated_at"])
    else:
        cube_card.delete()
    return redirect("cubes:detail", pk=cube.pk)
