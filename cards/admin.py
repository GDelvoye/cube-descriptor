from django.contrib import admin

from .models import CardOracle, CardPrinting, Set


@admin.register(Set)
class SetAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "released_at", "set_type")
    search_fields = ("code", "name")
    list_filter = ("set_type",)


@admin.register(CardOracle)
class CardOracleAdmin(admin.ModelAdmin):
    list_display = ("name", "mana_cost", "mana_value", "type_line")
    search_fields = ("name", "oracle_text", "type_line")
    list_filter = ("colors", "color_identity")


@admin.register(CardPrinting)
class CardPrintingAdmin(admin.ModelAdmin):
    list_display = ("oracle", "set_code", "collector_number", "rarity", "lang", "released_at")
    search_fields = ("oracle__name", "set_code", "collector_number")
    list_filter = ("set_code", "rarity", "lang")
    autocomplete_fields = ("oracle", "set")
