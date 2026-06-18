from decimal import Decimal, InvalidOperation

from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from cubes.forms import AddCardToCubeForm
from cubes.models import CubeCard

from .models import CardOracle, CardPrinting, Set


def card_search(request):
    filters = {
        "q": request.GET.get("q", "").strip(),
        "color": request.GET.get("color", "").strip().upper(),
        "type": request.GET.get("type", "").strip(),
        "text": request.GET.get("text", "").strip(),
        "mv_lte": request.GET.get("mv_lte", "").strip(),
        "set": request.GET.get("set", "").strip().lower(),
    }

    queryset = CardOracle.objects.all().prefetch_related(
        Prefetch(
            "printings",
            queryset=CardPrinting.objects.select_related("set").order_by("-released_at", "set_code", "collector_number"),
        )
    )

    if filters["q"]:
        queryset = queryset.filter(Q(name__icontains=filters["q"]) | Q(type_line__icontains=filters["q"]))
    if filters["color"]:
        queryset = queryset.filter(colors__contains=[filters["color"]])
    if filters["type"]:
        queryset = queryset.filter(type_line__icontains=filters["type"])
    if filters["text"]:
        queryset = queryset.filter(oracle_text__icontains=filters["text"])
    if filters["mv_lte"]:
        try:
            queryset = queryset.filter(mana_value__lte=Decimal(filters["mv_lte"]))
        except InvalidOperation:
            filters["mv_lte_error"] = "Mana value invalide"
    if filters["set"]:
        queryset = queryset.filter(printings__set_code=filters["set"]).distinct()

    paginator = Paginator(queryset, 25)
    page = paginator.get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)

    return render(
        request,
        "cards/search.html",
        {
            "filters": filters,
            "page": page,
            "sets": Set.objects.order_by("code"),
            "total_count": paginator.count,
            "querystring": query_params.urlencode(),
            "add_card_form": AddCardToCubeForm(user=request.user) if request.user.is_authenticated else None,
        },
    )


@login_required
@require_POST
def add_to_cube(request, oracle_id):
    oracle = get_object_or_404(CardOracle, pk=oracle_id)
    form = AddCardToCubeForm(request.POST, user=request.user)
    if form.is_valid():
        cube = add_oracles_to_cube(form.cleaned_data["cube"], [oracle], form.cleaned_data["quantity"])
        return redirect("cubes:detail", pk=cube.pk)
    return redirect("cards:search")


@login_required
@require_POST
def add_selected_to_cube(request):
    form = AddCardToCubeForm(request.POST, user=request.user)
    oracle_ids = request.POST.getlist("oracle_ids")
    if form.is_valid() and oracle_ids:
        oracles = CardOracle.objects.filter(pk__in=oracle_ids)
        cube = add_oracles_to_cube(form.cleaned_data["cube"], oracles, form.cleaned_data["quantity"])
        return redirect("cubes:detail", pk=cube.pk)
    return redirect("cards:search")


def add_oracles_to_cube(cube, oracles, quantity):
    for oracle in oracles:
        cube_card, created = CubeCard.objects.get_or_create(
            cube=cube,
            oracle=oracle,
            section="",
            defaults={"quantity": quantity},
        )
        if not created:
            cube_card.quantity += quantity
            cube_card.save(update_fields=["quantity", "updated_at"])
    return cube
