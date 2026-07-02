from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from cards.display import apply_oracle_display, build_language_querystrings, get_display_language
from cards.models import DEFAULT_AVAILABLE_SET_TYPES, CardOracle, CardPrinting, Set
from cubes.models import Cube

from .forms import CubeStatsForm, UserStatQueryForm
from .models import StatQuery
from .probabilities import probability_at_least_by_slots, probability_between_by_slots, probability_exactly_by_slots
from .query_engine import QuerySyntaxError, build_oracle_query
from .set_indicators import (
    attach_benchmarks,
    build_booster_slots,
    build_indicator_options,
    build_set_indicator_benchmarks,
    build_set_indicators,
    get_official_set_printings,
    get_selected_indicator_keys,
)


@login_required
def stats_index(request):
    cubes = Cube.objects.filter(owner=request.user).order_by("name")
    sets = Set.objects.filter(set_type__in=DEFAULT_AVAILABLE_SET_TYPES).order_by("-released_at", "name")
    cube_id = request.GET.get("cube")
    set_id = request.GET.get("set")
    if cube_id:
        return redirect("cubes:stats", pk=cube_id)
    if set_id:
        return redirect("stats:set_stats", pk=set_id)
    return render(request, "stats/index.html", {"cubes": cubes, "sets": sets})


@login_required
def set_stats(request, pk):
    card_set = get_object_or_404(Set, pk=pk, set_type__in=DEFAULT_AVAILABLE_SET_TYPES)
    display_language = get_display_language(request)
    form = CubeStatsForm(request.GET or None)
    visible_stat_queries = get_visible_stat_queries(request.user)
    selected_stat_keys = get_selected_indicator_keys(request)
    result = None
    error = None

    printings = CardPrinting.objects.filter(set=card_set, lang="en").select_related("oracle", "set")
    printings_list = list(printings)
    indicators = [row for row in build_set_indicators(printings_list) if row["key"] in selected_stat_keys]
    benchmarks = build_set_indicator_benchmarks(get_official_set_printings())
    attach_benchmarks(indicators, benchmarks)
    if form.is_valid():
        try:
            oracle_query = build_oracle_query(
                form.cleaned_data["raw_query"],
                available_sets=Set.objects.filter(pk=card_set.pk),
                stat_queries=visible_stat_queries,
            )
            matching_oracles = CardOracle.objects.filter(oracle_query).distinct()
            matching_printings = list(printings.filter(oracle__in=matching_oracles).distinct())
            for printing in matching_printings:
                apply_oracle_display(printing.oracle, display_language, Set.objects.filter(pk=card_set.pk))

            slots, rarity_rows = build_booster_slots(printings, matching_printings)
            minimum_hits = form.cleaned_data["minimum_hits"]
            exact_hits = form.cleaned_data["exact_hits"]
            between_min = form.cleaned_data["between_min"]
            between_max = form.cleaned_data["between_max"]
            result = {
                "total_cards": sum(row["population"] for row in rarity_rows),
                "matching_count": len(matching_printings),
                "booster_size": sum(slot_sample_size(slot) for slot in slots),
                "at_least": probability_at_least_by_slots(slots, minimum_hits),
                "minimum_hits": minimum_hits,
                "matching_rows": matching_printings,
                "rarity_rows": rarity_rows,
            }
            result["at_least_percent"] = result["at_least"] * 100
            if exact_hits is not None:
                result["exact_hits"] = exact_hits
                result["exactly"] = probability_exactly_by_slots(slots, exact_hits)
                result["exactly_percent"] = result["exactly"] * 100
            if between_min is not None and between_max is not None:
                result["between_min"] = between_min
                result["between_max"] = between_max
                result["between"] = probability_between_by_slots(slots, between_min, between_max)
                result["between_percent"] = result["between"] * 100
        except QuerySyntaxError as exc:
            error = str(exc)
    elif request.GET:
        error = "Formulaire invalide."

    return render(
        request,
        "stats/set_stats.html",
        {
            "card_set": card_set,
            "form": form,
            "result": result,
            "error": error,
            "indicators": indicators,
            "indicator_options": build_indicator_options(selected_stat_keys),
            "display_language": display_language,
            "language_querystrings": build_language_querystrings(request),
        },
    )


def slot_sample_size(slot):
    if isinstance(slot, dict):
        return 1
    return slot[2]


@login_required
def stat_query_list(request):
    queries = StatQuery.objects.filter(owner=request.user).select_related("cube").order_by("scope", "name")
    global_queries = StatQuery.objects.filter(scope=StatQuery.Scope.GLOBAL, owner__isnull=True).order_by("name")
    return render(
        request,
        "stats/query_list.html",
        {"queries": queries, "global_queries": global_queries},
    )


@login_required
def stat_query_create(request):
    test_result = None
    test_error = None
    if request.method == "POST":
        if "test_query" in request.POST:
            visible_stat_queries = get_visible_stat_queries(request.user)
            form = UserStatQueryForm(initial=get_query_form_initial(request.POST), stat_queries=visible_stat_queries)
            test_result, test_error = test_raw_query(request.POST.get("raw_query", ""), visible_stat_queries)
        else:
            form = UserStatQueryForm(request.POST, stat_queries=get_visible_stat_queries(request.user))
        if "test_query" not in request.POST and form.is_valid():
            stat_query = form.save(commit=False)
            stat_query.owner = request.user
            stat_query.scope = StatQuery.Scope.USER
            stat_query.save()
            return redirect("stats:query_detail", pk=stat_query.pk)
    else:
        form = UserStatQueryForm(stat_queries=get_visible_stat_queries(request.user))
    return render(
        request,
        "stats/query_form.html",
        {"form": form, "title": "Nouvelle requete", "test_result": test_result, "test_error": test_error},
    )


@login_required
def stat_query_detail(request, pk):
    stat_query = get_owned_query(request.user, pk)
    return render(request, "stats/query_detail.html", {"stat_query": stat_query})


@login_required
def stat_query_edit(request, pk):
    stat_query = get_owned_query(request.user, pk)
    test_result = None
    test_error = None
    if request.method == "POST":
        if "test_query" in request.POST:
            visible_stat_queries = get_visible_stat_queries(request.user)
            form = UserStatQueryForm(
                initial=get_query_form_initial(request.POST), instance=stat_query, stat_queries=visible_stat_queries
            )
            test_result, test_error = test_raw_query(request.POST.get("raw_query", ""), visible_stat_queries)
        else:
            form = UserStatQueryForm(
                request.POST, instance=stat_query, stat_queries=get_visible_stat_queries(request.user)
            )
        if "test_query" not in request.POST and form.is_valid():
            form.save()
            return redirect("stats:query_detail", pk=stat_query.pk)
    else:
        form = UserStatQueryForm(instance=stat_query, stat_queries=get_visible_stat_queries(request.user))
    return render(
        request,
        "stats/query_form.html",
        {"form": form, "title": "Modifier la requete", "test_result": test_result, "test_error": test_error},
    )


def test_raw_query(raw_query, stat_queries):
    raw_query = raw_query.strip()
    if not raw_query:
        return None, "La requete est vide."
    try:
        query = build_oracle_query(raw_query, stat_queries=stat_queries)
    except QuerySyntaxError as exc:
        return None, str(exc)
    cards = CardOracle.objects.filter(query).prefetch_related("printings").distinct().order_by("name")
    for card in cards:
        card.test_printing = select_test_result_printing(card)
    return cards, None


def select_test_result_printing(card):
    return (
        card.printings.filter(lang="en")
        .exclude(image_url="")
        .order_by("released_at", "set_code", "collector_number")
        .first()
        or card.printings.exclude(image_url="").order_by("released_at", "set_code", "collector_number").first()
        or card.printings.filter(lang="en").order_by("released_at", "set_code", "collector_number").first()
        or card.printings.order_by("released_at", "set_code", "collector_number").first()
    )


def get_query_form_initial(post_data):
    return {
        "name": post_data.get("name", ""),
        "raw_query": post_data.get("raw_query", ""),
        "description": post_data.get("description", ""),
    }


def get_visible_stat_queries(user):
    if not user.is_authenticated:
        return StatQuery.objects.filter(scope=StatQuery.Scope.GLOBAL, owner__isnull=True).order_by("name")
    return (
        StatQuery.objects.filter(scope=StatQuery.Scope.GLOBAL, owner__isnull=True)
        | StatQuery.objects.filter(owner=user, scope=StatQuery.Scope.USER)
    ).order_by("scope", "name")


@login_required
@require_POST
def stat_query_delete(request, pk):
    stat_query = get_owned_query(request.user, pk)
    stat_query.delete()
    return redirect("stats:query_list")


def get_owned_query(user, pk):
    return get_object_or_404(StatQuery.objects.select_related("cube"), pk=pk, owner=user)
