from django.core.management.base import BaseCommand

from stats.models import StatQuery
from stats.query_cache import get_visible_stat_queries_for_cache, refresh_stat_query_cache
from stats.query_engine import QuerySyntaxError
from stats.set_indicators import (
    refresh_code_indicator_expected_values,
    refresh_query_indicator_expected_values_for_queries,
)


class Command(BaseCommand):
    help = "Refresh cached expected values for set indicator benchmarks."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            choices=["all", "code", "query"],
            default="all",
            help="Refresh code indicators, query indicators, or both.",
        )

    def handle(self, *args, **options):
        source = options["source"]
        if source in {"all", "code"}:
            refresh_code_indicator_expected_values()
            self.stdout.write(self.style.SUCCESS("Refreshed code indicator expected values."))

        if source in {"all", "query"}:
            refreshed_count = 0
            skipped_count = 0
            queries = StatQuery.objects.select_related("owner", "cube").order_by("scope", "name")
            for stat_query in queries:
                try:
                    refreshed_queries = refresh_stat_query_cache(
                        stat_query, get_visible_stat_queries_for_cache(stat_query)
                    )
                    refresh_query_indicator_expected_values_for_queries(refreshed_queries)
                except QuerySyntaxError as exc:
                    skipped_count += 1
                    self.stderr.write(self.style.WARNING(f"Skipped {stat_query.pk} ({stat_query.name}): {exc}"))
                    continue
                refreshed_count += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"Refreshed query indicator expected values. Queries: {refreshed_count}. Skipped: {skipped_count}."
                )
            )
