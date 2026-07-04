from django.contrib import admin

from .models import StatQuery
from .query_cache import get_visible_stat_queries_for_cache, refresh_stat_query_cache


@admin.register(StatQuery)
class StatQueryAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "scope", "raw_query", "created_at")
    search_fields = ("name", "raw_query", "description", "owner__username")
    list_filter = ("scope",)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        refresh_stat_query_cache(obj, get_visible_stat_queries_for_cache(obj))
