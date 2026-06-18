from django.contrib import admin

from .models import Cube, CubeCard


class CubeCardInline(admin.TabularInline):
    model = CubeCard
    extra = 0
    autocomplete_fields = ("oracle", "printing")


@admin.register(Cube)
class CubeAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "visibility", "booster_size", "updated_at")
    search_fields = ("name", "description", "owner__username")
    list_filter = ("visibility",)
    inlines = (CubeCardInline,)


@admin.register(CubeCard)
class CubeCardAdmin(admin.ModelAdmin):
    list_display = ("cube", "oracle", "quantity", "section")
    search_fields = ("cube__name", "oracle__name", "section", "notes")
    list_filter = ("section",)
    autocomplete_fields = ("cube", "oracle", "printing")
