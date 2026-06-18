from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import CubeForm
from .models import Cube, CubeCard
from stats.forms import CubeStatsForm
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
    total_cards = sum(cube_card.quantity for cube_card in cube.cards.all())
    return render(request, "cubes/detail.html", {"cube": cube, "total_cards": total_cards})


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
    form = CubeStatsForm(request.GET or None)
    result = None
    error = None

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
        {"cube": cube, "form": form, "result": result, "error": error, "total_cards": total_cards},
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
