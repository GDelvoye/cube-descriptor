from django.core.management.base import BaseCommand

from stats.models import StatQuery
from stats.query_cache import get_visible_stat_queries_for_cache, refresh_stat_query_cache
from stats.query_engine import QuerySyntaxError


class Command(BaseCommand):
    help = "Refresh cached matches and dependencies for saved stat queries."

    def add_arguments(self, parser):
        parser.add_argument("--query-id", type=int, help="Refresh a single stat query by id.")

    def handle(self, *args, **options):
        queries = StatQuery.objects.select_related("owner", "cube").order_by("scope", "name")
        if options["query_id"]:
            queries = queries.filter(pk=options["query_id"])

        refreshed_count = 0
        skipped_count = 0
        for stat_query in queries:
            try:
                refresh_stat_query_cache(stat_query, get_visible_stat_queries_for_cache(stat_query))
            except QuerySyntaxError as exc:
                skipped_count += 1
                self.stderr.write(self.style.WARNING(f"Skipped {stat_query.pk} ({stat_query.name}): {exc}"))
                continue
            refreshed_count += 1
            self.stdout.write(self.style.SUCCESS(f"Refreshed {stat_query.pk} ({stat_query.name})"))

        self.stdout.write(self.style.SUCCESS(f"Done. Refreshed: {refreshed_count}. Skipped: {skipped_count}."))
