from django.db import transaction
from django.utils import timezone

from cards.models import CardOracle

from .models import StatQuery, StatQueryDependency, StatQueryMatch
from .query_engine import TERM_RE, QuerySyntaxError, build_oracle_query, resolve_stat_query, tokenize_query, unquote


def refresh_stat_query_cache(stat_query, stat_queries):
    update_stat_query_dependencies(stat_query, stat_queries)
    refreshed_queries = get_refresh_order(stat_query)
    for query in refreshed_queries:
        refresh_single_stat_query_matches(query, stat_queries)
    return refreshed_queries


def get_visible_stat_queries_for_cache(stat_query):
    queries = StatQuery.objects.filter(scope=StatQuery.Scope.GLOBAL, owner__isnull=True)
    if stat_query.owner_id:
        queries = queries | StatQuery.objects.filter(owner=stat_query.owner, scope=StatQuery.Scope.USER)
    if stat_query.cube_id:
        queries = queries | StatQuery.objects.filter(
            owner=stat_query.owner,
            scope=StatQuery.Scope.CUBE,
            cube=stat_query.cube,
        )
    return queries.order_by("scope", "name")


def update_stat_query_dependencies(stat_query, stat_queries):
    dependencies = extract_stat_query_dependencies(stat_query.raw_query, stat_queries)
    if stat_query.pk in {dependency.pk for dependency in dependencies}:
        raise QuerySyntaxError(f"Reference circulaire de requete: {stat_query.name}")

    with transaction.atomic():
        StatQueryDependency.objects.filter(parent=stat_query).delete()
        StatQueryDependency.objects.bulk_create(
            [StatQueryDependency(parent=stat_query, child=dependency) for dependency in dependencies],
            ignore_conflicts=True,
        )


def extract_stat_query_dependencies(raw_query, stat_queries):
    dependencies = []
    seen_ids = set()
    for token in tokenize_query(raw_query):
        term_match = TERM_RE.match(token)
        if not term_match or term_match.group("field").lower() not in {"query", "query_id"}:
            continue

        stat_query = resolve_stat_query(
            term_match.group("field").lower(),
            unquote(term_match.group("value")),
            stat_queries,
        )
        if stat_query.pk not in seen_ids:
            dependencies.append(stat_query)
            seen_ids.add(stat_query.pk)
    return dependencies


def get_refresh_order(stat_query):
    ordered_queries = []
    seen_ids = set()

    def visit(query):
        if query.pk in seen_ids:
            return
        seen_ids.add(query.pk)
        ordered_queries.append(query)
        for dependency in StatQueryDependency.objects.filter(child=query).select_related("parent"):
            visit(dependency.parent)

    visit(stat_query)
    return ordered_queries


def refresh_single_stat_query_matches(stat_query, stat_queries):
    oracle_query = build_oracle_query(stat_query.raw_query, stat_queries=stat_queries)
    matching_oracle_ids = CardOracle.objects.filter(oracle_query).values_list("pk", flat=True).distinct()

    with transaction.atomic():
        StatQueryMatch.objects.filter(stat_query=stat_query).delete()
        StatQueryMatch.objects.bulk_create(
            [StatQueryMatch(stat_query=stat_query, oracle_id=oracle_id) for oracle_id in matching_oracle_ids],
            ignore_conflicts=True,
        )
        stat_query.match_cache_refreshed_at = timezone.now()
        stat_query.save(update_fields=["match_cache_refreshed_at"])
