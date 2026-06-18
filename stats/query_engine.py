import re
from decimal import Decimal, InvalidOperation


TOKEN_RE = re.compile(r'\s+AND\s+', re.IGNORECASE)
TERM_RE = re.compile(r'^(?P<field>color|type|text|tag):(?P<value>"[^"]+"|\S+)$', re.IGNORECASE)
MV_RE = re.compile(r'^mv\s*(?P<op><=|>=|=|<|>)\s*(?P<value>\d+(?:\.\d+)?)$', re.IGNORECASE)


class QuerySyntaxError(ValueError):
    pass


def count_cube_matches(cube_cards, raw_query):
    matchers = parse_query(raw_query)
    total = 0
    matching_rows = []
    for cube_card in cube_cards:
        if all(matcher(cube_card) for matcher in matchers):
            total += cube_card.quantity
            matching_rows.append(cube_card)
    return total, matching_rows


def parse_query(raw_query):
    raw_query = raw_query.strip()
    if not raw_query:
        raise QuerySyntaxError("La requete est vide.")

    matchers = []
    for token in TOKEN_RE.split(raw_query):
        token = token.strip()
        if not token:
            continue
        matchers.append(parse_term(token))
    if not matchers:
        raise QuerySyntaxError("La requete est vide.")
    return matchers


def parse_term(token):
    mv_match = MV_RE.match(token)
    if mv_match:
        return mana_value_matcher(mv_match.group("op"), Decimal(mv_match.group("value")))

    term_match = TERM_RE.match(token)
    if not term_match:
        raise QuerySyntaxError(f"Filtre non reconnu: {token}")

    field = term_match.group("field").lower()
    value = unquote(term_match.group("value"))
    if field == "color":
        return lambda cube_card: value.upper() in (cube_card.oracle.colors or [])
    if field == "type":
        return lambda cube_card: value.lower() in cube_card.oracle.type_line.lower()
    if field == "text":
        return lambda cube_card: value.lower() in cube_card.oracle.oracle_text.lower()
    if field == "tag":
        return lambda cube_card: value.lower() in [tag.lower() for tag in cube_card.tags]

    raise QuerySyntaxError(f"Filtre non reconnu: {token}")


def mana_value_matcher(operator, expected):
    def matcher(cube_card):
        try:
            mana_value = Decimal(cube_card.oracle.mana_value)
        except (InvalidOperation, TypeError):
            return False
        if operator == "<=":
            return mana_value <= expected
        if operator == ">=":
            return mana_value >= expected
        if operator == "<":
            return mana_value < expected
        if operator == ">":
            return mana_value > expected
        return mana_value == expected

    return matcher


def unquote(value):
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value
