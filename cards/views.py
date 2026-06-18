from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from cubes.forms import AddCardToCubeForm
from cubes.models import CubeCard
from stats.models import StatQuery
from stats.query_engine import QuerySyntaxError, build_oracle_matchers

from .display import apply_oracle_display, build_language_querystrings, get_display_language
from .models import CardOracle, CardPrinting, Set, UserSetPreference
from .set_availability import get_available_sets, get_excluded_sets


def card_search(request):
    selected_stat_query = get_selected_stat_query(request)
    display_language = get_display_language(request)
    selected_cube_id = request.GET.get("cube", "").strip()
    filters = {
        "q": request.GET.get("q", "").strip(),
        "color": request.GET.get("color", "").strip().upper(),
        "type": request.GET.get("type", "").strip(),
        "text": request.GET.get("text", "").strip(),
        "mv_lte": request.GET.get("mv_lte", "").strip(),
        "set": request.GET.get("set", "").strip().lower(),
        "raw_query": (
            selected_stat_query.raw_query if selected_stat_query else request.GET.get("raw_query", "")
        ).strip(),
        "stat_query": request.GET.get("stat_query", "").strip(),
        "cube": selected_cube_id,
    }
    available_sets = get_available_sets(request.user)
    printings = CardPrinting.objects.select_related("set").order_by("released_at", "set_code", "collector_number")
    available_printings = CardPrinting.objects.filter(oracle=OuterRef("pk"), set__in=available_sets)
    queryset = CardOracle.objects.filter(Exists(available_printings))
    printings = printings.filter(set__in=available_sets)

    queryset = queryset.prefetch_related(
        Prefetch(
            "printings",
            queryset=printings,
        )
    )

    if filters["q"]:
        queryset = queryset.filter(
            Q(name__icontains=filters["q"])
            | Q(type_line__icontains=filters["q"])
            | Q(printings__set__in=available_sets, printings__printed_name__icontains=filters["q"])
            | Q(printings__set__in=available_sets, printings__printed_type_line__icontains=filters["q"])
        ).distinct()
    if filters["color"]:
        queryset = queryset.filter(colors__contains=[filters["color"]])
    if filters["type"]:
        queryset = queryset.filter(
            Q(type_line__icontains=filters["type"])
            | Q(printings__set__in=available_sets, printings__printed_type_line__icontains=filters["type"])
        ).distinct()
    if filters["text"]:
        queryset = queryset.filter(
            Q(oracle_text__icontains=filters["text"])
            | Q(printings__set__in=available_sets, printings__printed_oracle_text__icontains=filters["text"])
        ).distinct()
    if filters["mv_lte"]:
        try:
            queryset = queryset.filter(mana_value__lte=Decimal(filters["mv_lte"]))
        except InvalidOperation:
            filters["mv_lte_error"] = "Mana value invalide"
    if filters["set"]:
        queryset = queryset.filter(printings__set_code=filters["set"]).distinct()
    if filters["raw_query"]:
        try:
            matchers = build_oracle_matchers(filters["raw_query"], available_sets=available_sets)
            queryset = [oracle for oracle in queryset if all(matcher(oracle) for matcher in matchers)]
        except QuerySyntaxError as exc:
            filters["raw_query_error"] = str(exc)

    paginator = Paginator(queryset, 25)
    page = paginator.get_page(request.GET.get("page"))
    for oracle in page.object_list:
        oracle.display_printing = next(iter(oracle.printings.all()), None)
        apply_oracle_display(oracle, display_language, available_sets)
        if oracle.display_localized_printing and oracle.display_localized_printing.image_url:
            oracle.display_printing = oracle.display_localized_printing
    query_params = request.GET.copy()
    query_params.pop("page", None)

    return render(
        request,
        "cards/search.html",
        {
            "filters": filters,
            "page": page,
            "sets": available_sets.order_by("code"),
            "total_count": paginator.count,
            "querystring": query_params.urlencode(),
            "add_card_form": AddCardToCubeForm(user=request.user, initial={"cube": selected_cube_id})
            if request.user.is_authenticated
            else None,
            "stat_queries": get_visible_stat_queries(request.user),
            "display_language": display_language,
            "language_querystrings": build_language_querystrings(request),
        },
    )


@login_required
def set_preferences(request):
    available_sets = list(get_available_sets(request.user).order_by("code"))
    excluded_sets = list(get_excluded_sets(request.user).order_by("code"))
    set_types = get_set_type_summaries(available_sets, excluded_sets)
    return render(
        request,
        "cards/set_preferences.html",
        {
            "available_sets": available_sets,
            "excluded_sets": excluded_sets,
            "set_types": set_types,
        },
    )


@login_required
@require_POST
def update_set_preferences(request):
    action = request.POST.get("action")
    set_ids = request.POST.getlist("set_ids")
    if action in {"include", "exclude"} and set_ids:
        save_set_preferences(request.user, set_ids, action == "include")
    elif action in {"include_type", "exclude_type"} and request.POST.get("set_type"):
        set_ids = Set.objects.filter(set_type=request.POST["set_type"]).values_list("pk", flat=True)
        save_set_preferences(request.user, set_ids, action == "include_type")
    return redirect("cards:set_preferences")


def save_set_preferences(user, set_ids, is_available):
    UserSetPreference.objects.bulk_create(
        [UserSetPreference(user=user, set_id=set_id, is_available=is_available) for set_id in set_ids],
        update_conflicts=True,
        update_fields=["is_available"],
        unique_fields=["user", "set"],
    )


def get_set_type_summaries(available_sets, excluded_sets):
    rows = {}
    for set_obj in available_sets:
        row = rows.setdefault(set_obj.set_type, {"name": set_obj.set_type, "available_count": 0, "excluded_count": 0})
        row["available_count"] += 1
    for set_obj in excluded_sets:
        row = rows.setdefault(set_obj.set_type, {"name": set_obj.set_type, "available_count": 0, "excluded_count": 0})
        row["excluded_count"] += 1
    return sorted(rows.values(), key=lambda row: row["name"])


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
        cube = form.cleaned_data["cube"]
        added_count = len(oracle_ids) * form.cleaned_data["quantity"]
        add_oracles_to_cube(cube, oracles, form.cleaned_data["quantity"])
        messages.success(
            request,
            f"{added_count} carte{'s' if added_count > 1 else ''} ajoutee{'s' if added_count > 1 else ''} a {cube.name}.",
        )
        return redirect_to_next(request)
    return redirect("cards:search")


def redirect_to_next(request):
    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect("cards:search")


def add_oracles_to_cube(cube, oracles, quantity):
    for oracle in oracles:
        cube_card, created = CubeCard.objects.get_or_create(
            cube=cube,
            oracle=oracle,
            section=CubeCard.Section.MAIN,
            defaults={"quantity": quantity},
        )
        if not created:
            cube_card.quantity += quantity
            cube_card.save(update_fields=["quantity", "updated_at"])
    return cube


def get_visible_stat_queries(user):
    if not user.is_authenticated:
        return StatQuery.objects.filter(scope=StatQuery.Scope.GLOBAL, owner__isnull=True).order_by("name")
    return (
        StatQuery.objects.filter(scope=StatQuery.Scope.GLOBAL, owner__isnull=True)
        | StatQuery.objects.filter(owner=user)
    ).order_by("scope", "name")


def get_selected_stat_query(request):
    stat_query_id = request.GET.get("stat_query")
    if not stat_query_id or not stat_query_id.isdigit():
        return None
    return get_visible_stat_queries(request.user).filter(pk=stat_query_id).first()
