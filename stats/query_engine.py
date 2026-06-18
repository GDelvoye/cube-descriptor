import re
from decimal import Decimal, InvalidOperation


TOKEN_RE = re.compile(r'\s+AND\s+', re.IGNORECASE)
TERM_RE = re.compile(r'^(?P<field>color|identity|type|text|tag|keyword|name):(?P<value>"[^"]+"|\S+)$', re.IGNORECASE)
NUMERIC_RE = re.compile(r'^(?P<field>mv|power|toughness)\s*(?P<op><=|>=|=|<|>)\s*(?P<value>\d+(?:\.\d+)?)$', re.IGNORECASE)


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


def build_oracle_matchers(raw_query):
    cube_card_matchers = parse_query(raw_query)
    return [lambda oracle, matcher=matcher: matcher(OracleProxy(oracle)) for matcher in cube_card_matchers]


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
    numeric_match = NUMERIC_RE.match(token)
    if numeric_match:
        return numeric_matcher(numeric_match.group("field").lower(), numeric_match.group("op"), Decimal(numeric_match.group("value")))

    term_match = TERM_RE.match(token)
    if not term_match:
        raise QuerySyntaxError(f"Filtre non reconnu: {token}")

    field = term_match.group("field").lower()
    value = unquote(term_match.group("value"))
    if field == "color":
        return lambda cube_card: value.upper() in (cube_card.oracle.colors or [])
    if field == "identity":
        return lambda cube_card: value.upper() in (cube_card.oracle.color_identity or [])
    if field == "type":
        return lambda cube_card: value.lower() in cube_card.oracle.type_line.lower()
    if field == "text":
        return lambda cube_card: value.lower() in cube_card.oracle.oracle_text.lower()
    if field == "tag":
        return lambda cube_card: value.lower() in [tag.lower() for tag in cube_card.tags]
    if field == "keyword":
        return lambda cube_card: value.lower() in [keyword.lower() for keyword in cube_card.oracle.keywords]
    if field == "name":
        return lambda cube_card: value.lower() in cube_card.oracle.name.lower()

    raise QuerySyntaxError(f"Filtre non reconnu: {token}")


def numeric_matcher(field, operator, expected):
    def matcher(cube_card):
        try:
            actual = Decimal(getattr(cube_card.oracle, "mana_value" if field == "mv" else field))
        except (InvalidOperation, TypeError):
            return False
        if operator == "<=":
            return actual <= expected
        if operator == ">=":
            return actual >= expected
        if operator == "<":
            return actual < expected
        if operator == ">":
            return actual > expected
        return actual == expected

    return matcher


class OracleProxy:
    def __init__(self, oracle):
        self.oracle = oracle
        self.tags = []


def unquote(value):
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value
