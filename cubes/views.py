from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from cards.set_availability import get_available_sets

from .models import Cube, CubeCard
from .forms import CubeCardForm, CubeForm
from stats.forms import CubeStatsForm, StatQueryForm
from stats.models import StatQuery
from stats.probabilities import probability_at_least, probability_between, probability_exactly
from stats.query_engine import QuerySyntaxError, count_cube_matches


@login_required
def cube_list(request):
    cubes = Cube.objects.filter(owner=request.user).annotate(card_total=Sum("cards__quantity"))
    return render(request, "cubes/list.html", {"cubes": cubes})


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


@login_required
def cube_detail(request, pk):
    cube = get_object_or_404(
        Cube.objects.prefetch_related(
            Prefetch("cards", queryset=CubeCard.objects.select_related("oracle", "printing").order_by("oracle__name"))
        ),
        pk=pk,
        owner=request.user,
    )
    cube_cards = list(cube.cards.all())
    total_cards = sum(cube_card.quantity for cube_card in cube_cards)
    visible_queries = get_visible_stat_queries(request.user, cube)
    selected_query = get_selected_stat_query(request, visible_queries)
    raw_query = (selected_query.raw_query if selected_query else request.GET.get("raw_query", "")).strip()
    filter_error = None
    filtered_cards = cube_cards
    if raw_query:
        try:
            _, matching_rows = count_cube_matches(cube_cards, raw_query)
            filtered_cards = matching_rows
        except QuerySyntaxError as exc:
            filter_error = str(exc)

    filtered_total = sum(cube_card.quantity for cube_card in filtered_cards)
    available_sets = get_available_sets(request.user)
    for cube_card in filtered_cards:
        cube_card.edit_form = CubeCardForm(instance=cube_card)
        cube_card.display_printing = cube_card.printing or cube_card.oracle.printings.filter(
            set__in=available_sets
        ).order_by("released_at", "set_code", "collector_number").first()
    return render(
        request,
        "cubes/detail.html",
        {
            "cube": cube,
            "cube_cards": filtered_cards,
            "total_cards": total_cards,
            "filtered_total": filtered_total,
            "raw_query": raw_query,
            "filter_error": filter_error,
            "stat_queries": visible_queries,
            "selected_query": selected_query,
        },
    )


@login_required
def cube_stats(request, pk):
    cube = get_object_or_404(
        Cube.objects.prefetch_related(
            Prefetch("cards", queryset=CubeCard.objects.select_related("oracle").order_by("oracle__name"))
        ),
        pk=pk,
        owner=request.user,
    )
    cube_cards = list(cube.cards.all())
    total_cards = sum(cube_card.quantity for cube_card in cube_cards)
    visible_queries = get_visible_stat_queries(request.user, cube)
    selected_query = get_selected_stat_query(request, visible_queries)
    initial = {"raw_query": selected_query.raw_query} if selected_query and "raw_query" not in request.GET else None
    form = CubeStatsForm(request.GET or None)
    if initial and not request.GET.get("raw_query"):
        form = CubeStatsForm({**request.GET.dict(), **initial})
    stat_query_form = StatQueryForm()
    result = None
    error = None

    if request.method == "POST":
        stat_query_form = StatQueryForm(request.POST)
        if stat_query_form.is_valid():
            stat_query = stat_query_form.save(commit=False)
            stat_query.owner = request.user
            if stat_query.scope == StatQuery.Scope.CUBE:
                stat_query.cube = cube
            stat_query.save()
            return redirect(f"{request.path}?stat_query={stat_query.pk}&minimum_hits=1")

    if form.is_valid():
        try:
            matching_count, matching_rows = count_cube_matches(cube_cards, form.cleaned_data["raw_query"])
            booster_size = min(cube.booster_size, total_cards)
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
                result["between"] = probability_between(total_cards, matching_count, booster_size, between_min, between_max)
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
            "stat_query_form": stat_query_form,
            "stat_queries": visible_queries,
            "selected_query": selected_query,
            "result": result,
            "error": error,
            "total_cards": total_cards,
        },
    )


def get_visible_stat_queries(user, cube):
    return (
        StatQuery.objects.filter(scope=StatQuery.Scope.GLOBAL, owner__isnull=True)
        | StatQuery.objects.filter(owner=user, scope=StatQuery.Scope.USER)
        | StatQuery.objects.filter(owner=user, scope=StatQuery.Scope.CUBE, cube=cube)
    ).order_by("scope", "name")


def get_selected_stat_query(request, visible_queries):
    stat_query_id = request.GET.get("stat_query")
    if not stat_query_id or not stat_query_id.isdigit():
        return None
    return visible_queries.filter(pk=stat_query_id).first()


@login_required
@require_POST
def cube_card_edit(request, pk, cube_card_id):
    cube = get_object_or_404(Cube, pk=pk, owner=request.user)
    cube_card = get_object_or_404(CubeCard, pk=cube_card_id, cube=cube)
    form = CubeCardForm(request.POST, instance=cube_card)
    if form.is_valid():
        form.save()
    return redirect("cubes:detail", pk=cube.pk)


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
