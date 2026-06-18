DISPLAY_LANGUAGES = {"en", "fr"}


def get_display_language(request):
    language = request.GET.get("display_lang", "en")
    if language not in DISPLAY_LANGUAGES:
        return "en"
    return language


def build_language_querystrings(request):
    querystrings = {}
    for language in sorted(DISPLAY_LANGUAGES):
        params = request.GET.copy()
        params["display_lang"] = language
        querystrings[language] = params.urlencode()
    return querystrings


def apply_oracle_display(oracle, display_language, available_sets=None):
    oracle.display_localized_printing = select_display_printing(oracle, display_language, available_sets)
    apply_display_fields(oracle, oracle, display_language, available_sets, oracle.display_localized_printing)
    return oracle


def apply_cube_card_display(cube_card, display_language, available_sets=None):
    cube_card.display_localized_printing = select_display_printing(cube_card.oracle, display_language, available_sets)
    apply_display_fields(
        cube_card,
        cube_card.oracle,
        display_language,
        available_sets,
        cube_card.display_localized_printing,
    )
    return cube_card


def apply_display_fields(target, oracle, display_language, available_sets=None, preferred_printing=None):
    target.display_name, target.display_name_is_fallback = localized_value(
        oracle, oracle.name, "printed_name", display_language, available_sets, preferred_printing
    )
    target.display_type_line, target.display_type_line_is_fallback = localized_value(
        oracle, oracle.type_line, "printed_type_line", display_language, available_sets, preferred_printing
    )
    target.display_oracle_text, target.display_oracle_text_is_fallback = localized_value(
        oracle, oracle.oracle_text, "printed_oracle_text", display_language, available_sets, preferred_printing
    )


def select_localized_printing(oracle, display_language, available_sets=None, required_field=None):
    if display_language != "fr":
        return None
    printings = oracle.printings.filter(lang="fr").exclude(printed_name="")
    if required_field:
        printings = printings.exclude(**{required_field: ""})
    if available_sets is not None:
        printings = printings.filter(set__in=available_sets)
    return printings.order_by("released_at", "set_code", "collector_number").first()


def select_display_printing(oracle, display_language, available_sets=None):
    paired_printing = select_bilingual_complete_printing(oracle, display_language, available_sets)
    if paired_printing:
        return paired_printing
    if display_language == "fr":
        return select_localized_printing(oracle, display_language, available_sets)
    return select_english_printing(oracle, available_sets)


def select_bilingual_complete_printing(oracle, display_language, available_sets=None):
    printings = oracle.printings.all()
    if available_sets is not None:
        printings = printings.filter(set__in=available_sets)

    pairs = {}
    for printing in printings.order_by("released_at", "set_code", "collector_number", "lang"):
        if printing.lang not in DISPLAY_LANGUAGES or not is_complete_printing(printing):
            continue
        key = (printing.set_code, printing.collector_number)
        pairs.setdefault(key, {})[printing.lang] = printing

    for pair in reversed(list(pairs.values())):
        if "en" in pair and "fr" in pair:
            return pair.get(display_language)
    return None


def select_english_printing(oracle, available_sets=None):
    printings = oracle.printings.filter(lang="en")
    if available_sets is not None:
        printings = printings.filter(set__in=available_sets)
    return printings.order_by("released_at", "set_code", "collector_number").first()


def is_complete_printing(printing):
    return bool(
        printing.printed_name and printing.printed_type_line and printing.printed_oracle_text and printing.image_url
    )


def localized_value(oracle, default, field, display_language, available_sets=None, preferred_printing=None):
    if display_language != "fr":
        return default, False
    if preferred_printing:
        translated = getattr(preferred_printing, field) or ""
        if translated:
            return translated, False
    printing = select_localized_printing(oracle, display_language, available_sets, required_field=field)
    if not printing:
        return default, bool(default)
    translated = getattr(printing, field) or ""
    if translated:
        return translated, False
    return default, bool(default)
