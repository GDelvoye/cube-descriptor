from django.contrib import admin

from .models import StatQuery


@admin.register(StatQuery)
class StatQueryAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "scope", "raw_query", "created_at")
    search_fields = ("name", "raw_query", "description", "owner__username")
    list_filter = ("scope",)
