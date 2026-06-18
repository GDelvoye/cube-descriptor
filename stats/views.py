from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import UserStatQueryForm
from .models import StatQuery


@login_required
def stat_query_list(request):
    queries = StatQuery.objects.filter(owner=request.user).select_related("cube").order_by("scope", "name")
    global_queries = StatQuery.objects.filter(scope=StatQuery.Scope.GLOBAL, owner__isnull=True).order_by("name")
    return render(request, "stats/query_list.html", {"queries": queries, "global_queries": global_queries})


@login_required
def stat_query_create(request):
    if request.method == "POST":
        form = UserStatQueryForm(request.POST)
        if form.is_valid():
            stat_query = form.save(commit=False)
            stat_query.owner = request.user
            stat_query.scope = StatQuery.Scope.USER
            stat_query.save()
            return redirect("stats:query_detail", pk=stat_query.pk)
    else:
        form = UserStatQueryForm()
    return render(request, "stats/query_form.html", {"form": form, "title": "Nouvelle requete"})


@login_required
def stat_query_detail(request, pk):
    stat_query = get_owned_query(request.user, pk)
    return render(request, "stats/query_detail.html", {"stat_query": stat_query})


@login_required
def stat_query_edit(request, pk):
    stat_query = get_owned_query(request.user, pk)
    if request.method == "POST":
        form = UserStatQueryForm(request.POST, instance=stat_query)
        if form.is_valid():
            form.save()
            return redirect("stats:query_detail", pk=stat_query.pk)
    else:
        form = UserStatQueryForm(instance=stat_query)
    return render(request, "stats/query_form.html", {"form": form, "title": "Modifier la requete"})


@login_required
@require_POST
def stat_query_delete(request, pk):
    stat_query = get_owned_query(request.user, pk)
    stat_query.delete()
    return redirect("stats:query_list")


def get_owned_query(user, pk):
    return get_object_or_404(StatQuery.objects.select_related("cube"), pk=pk, owner=user)
