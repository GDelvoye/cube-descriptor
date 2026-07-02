import re
from decimal import Decimal, InvalidOperation
from re import Pattern

from django.db.models import Q

TERM_RE = re.compile(
    r'^(?P<field>color|identity|type|text|text_regex|tag|keyword|name|set|query|query_id):(?P<value>"[^"]+"|\S+)$',
    re.IGNORECASE,
)
NUMERIC_RE = re.compile(
    r"^(?P<field>mv|power|toughness)\s*(?P<op><=|>=|=|<|>)\s*(?P<value>\d+(?:\.\d+)?)$", re.IGNORECASE
)
QUERY_TOKEN_RE = re.compile(r'\(|\)|\bAND\b|\bOR\b|\bNOT\b|[A-Za-z_]+:"[^"]+"|[^\s()]+', re.IGNORECASE)


class QuerySyntaxError(ValueError):
    pass


def count_cube_matches(cube_cards, raw_query, available_sets=None, stat_queries=None):
    matchers = parse_query(raw_query, available_sets=available_sets, stat_queries=stat_queries)
    total = 0
    matching_rows = []
    for cube_card in cube_cards:
        if all(matcher(cube_card) for matcher in matchers):
            total += cube_card.quantity
            matching_rows.append(cube_card)
    return total, matching_rows


def build_oracle_matchers(raw_query, available_sets=None, stat_queries=None):
    cube_card_matchers = parse_query(raw_query, available_sets=available_sets, stat_queries=stat_queries)
    return [lambda oracle, matcher=matcher: matcher(OracleProxy(oracle)) for matcher in cube_card_matchers]


def build_oracle_query(raw_query, available_sets=None, stat_queries=None, resolving_query_ids=None):
    raw_query = raw_query.strip()
    if not raw_query:
        raise QuerySyntaxError("La requete est vide.")

    resolving_query_ids = resolving_query_ids or set()
    return QueryParser(
        tokenize_query(raw_query),
        lambda token: parse_oracle_query_term(token, available_sets, stat_queries, resolving_query_ids),
    ).parse()


def parse_query(raw_query, available_sets=None, stat_queries=None, resolving_query_ids=None):
    raw_query = raw_query.strip()
    if not raw_query:
        raise QuerySyntaxError("La requete est vide.")

    resolving_query_ids = resolving_query_ids or set()
    matcher = QueryParser(
        tokenize_query(raw_query),
        lambda token: parse_term(token, available_sets, stat_queries, resolving_query_ids),
    ).parse()
    return [matcher]


def tokenize_query(raw_query):
    return [token.strip() for token in QUERY_TOKEN_RE.findall(raw_query) if token.strip()]


class QueryParser:
    def __init__(self, tokens, parse_leaf):
        self.tokens = tokens
        self.parse_leaf = parse_leaf
        self.position = 0

    def parse(self):
        if not self.tokens:
            raise QuerySyntaxError("La requete est vide.")
        expression = self.parse_or()
        if self.current_token() is not None:
            raise QuerySyntaxError(f"Filtre non reconnu: {self.current_token()}")
        return expression

    def parse_or(self):
        expression = self.parse_and()
        while self.current_token_upper() == "OR":
            self.position += 1
            right = self.parse_and()
            expression = self.combine_or(expression, right)
        return expression

    def parse_and(self):
        expression = self.parse_not()
        while self.current_token_upper() == "AND":
            self.position += 1
            right = self.parse_not()
            expression = self.combine_and(expression, right)
        return expression

    def parse_not(self):
        if self.current_token_upper() == "NOT":
            self.position += 1
            return self.combine_not(self.parse_not())
        return self.parse_factor()

    def parse_factor(self):
        token = self.current_token()
        if token is None:
            raise QuerySyntaxError("La requete est incomplete.")
        if token == "(":
            self.position += 1
            expression = self.parse_or()
            if self.current_token() != ")":
                raise QuerySyntaxError("Parenthese fermante manquante.")
            self.position += 1
            return expression
        if token == ")":
            raise QuerySyntaxError("Parenthese fermante inattendue.")
        if token.upper() in {"AND", "OR", "NOT"}:
            raise QuerySyntaxError(f"Operateur inattendu: {token.upper()}")
        self.position += 1
        return self.parse_leaf(token)

    def current_token(self):
        if self.position >= len(self.tokens):
            return None
        return self.tokens[self.position]

    def current_token_upper(self):
        token = self.current_token()
        return token.upper() if token is not None else None

    @staticmethod
    def combine_and(left, right):
        if isinstance(left, Q):
            return left & right
        return lambda card: left(card) and right(card)

    @staticmethod
    def combine_or(left, right):
        if isinstance(left, Q):
            return left | right
        return lambda card: left(card) or right(card)

    @staticmethod
    def combine_not(expression):
        if isinstance(expression, Q):
            return ~expression
        return lambda card: not expression(card)


def parse_term(token, available_sets=None, stat_queries=None, resolving_query_ids=None):
    numeric_match = NUMERIC_RE.match(token)
    if numeric_match:
        return numeric_matcher(
            numeric_match.group("field").lower(), numeric_match.group("op"), Decimal(numeric_match.group("value"))
        )

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
        return lambda cube_card: localized_contains(
            cube_card, value, ["type_line"], ["printed_type_line"], available_sets
        )
    if field == "text":
        return lambda cube_card: localized_contains(
            cube_card, value, ["oracle_text"], ["printed_oracle_text"], available_sets
        )
    if field == "text_regex":
        pattern = compile_regex(value)
        return lambda cube_card: localized_regex_contains(
            cube_card, pattern, ["oracle_text"], ["printed_oracle_text"], available_sets
        )
    if field == "tag":
        return lambda cube_card: value.lower() in [tag.lower() for tag in cube_card.tags]
    if field == "keyword":
        return lambda cube_card: value.lower() in [keyword.lower() for keyword in cube_card.oracle.keywords]
    if field == "name":
        return lambda cube_card: localized_contains(cube_card, value, ["name"], ["printed_name"], available_sets)
    if field == "set":
        return lambda cube_card: cube_card_has_set(cube_card, value, available_sets)
    if field in {"query", "query_id"}:
        stat_query = resolve_stat_query(field, value, stat_queries)
        next_resolving_ids = next_query_resolution_ids(stat_query, resolving_query_ids)
        return parse_query(
            stat_query.raw_query,
            available_sets=available_sets,
            stat_queries=stat_queries,
            resolving_query_ids=next_resolving_ids,
        )[0]

    raise QuerySyntaxError(f"Filtre non reconnu: {token}")


def parse_oracle_query_term(token, available_sets=None, stat_queries=None, resolving_query_ids=None):
    numeric_match = NUMERIC_RE.match(token)
    if numeric_match:
        return numeric_oracle_query(
            numeric_match.group("field").lower(), numeric_match.group("op"), Decimal(numeric_match.group("value"))
        )

    term_match = TERM_RE.match(token)
    if not term_match:
        raise QuerySyntaxError(f"Filtre non reconnu: {token}")

    field = term_match.group("field").lower()
    value = unquote(term_match.group("value"))
    if field == "color":
        return Q(colors__contains=[value.upper()])
    if field == "identity":
        return Q(color_identity__contains=[value.upper()])
    if field == "type":
        return localized_oracle_query(value, "type_line", "printed_type_line", available_sets)
    if field == "text":
        return localized_oracle_query(value, "oracle_text", "printed_oracle_text", available_sets)
    if field == "text_regex":
        compile_regex(value)
        return localized_oracle_regex_query(value, "oracle_text", "printed_oracle_text", available_sets)
    if field == "keyword":
        return Q(keywords__contains=[value])
    if field == "name":
        return localized_oracle_query(value, "name", "printed_name", available_sets)
    if field == "set":
        return Q(printings__set__code__iexact=value) | Q(printings__set__name__icontains=value)
    if field in {"query", "query_id"}:
        stat_query = resolve_stat_query(field, value, stat_queries)
        next_resolving_ids = next_query_resolution_ids(stat_query, resolving_query_ids)
        return build_oracle_query(
            stat_query.raw_query,
            available_sets=available_sets,
            stat_queries=stat_queries,
            resolving_query_ids=next_resolving_ids,
        )
    if field == "tag":
        raise QuerySyntaxError("Le filtre tag: est disponible dans les cubes, pas dans la recherche globale.")

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


def numeric_oracle_query(field, operator, expected):
    if field == "mv":
        lookup_field = "mana_value"
    elif field in {"power", "toughness"}:
        lookup_field = field
    else:
        raise QuerySyntaxError(f"Filtre non reconnu: {field}")

    lookup_suffix = {
        "=": "",
        "<": "__lt",
        ">": "__gt",
        "<=": "__lte",
        ">=": "__gte",
    }[operator]
    return Q(**{f"{lookup_field}{lookup_suffix}": expected})


def localized_oracle_query(value, oracle_field, printing_field, available_sets=None):
    printing_filter = {f"printings__{printing_field}__icontains": value}
    if available_sets is not None:
        printing_filter["printings__set__in"] = available_sets
    return Q(**{f"{oracle_field}__icontains": value}) | Q(**printing_filter)


def localized_oracle_regex_query(value, oracle_field, printing_field, available_sets=None):
    printing_filter = {f"printings__{printing_field}__iregex": value}
    if available_sets is not None:
        printing_filter["printings__set__in"] = available_sets
    return Q(**{f"{oracle_field}__iregex": value}) | Q(**printing_filter)


def localized_contains(cube_card, value, oracle_fields, printing_fields, available_sets=None):
    needle = value.lower()
    if any(needle in (getattr(cube_card.oracle, field) or "").lower() for field in oracle_fields):
        return True

    printings = cube_card.oracle.printings.all()
    if available_sets is not None:
        printings = printings.filter(set__in=available_sets)
    for printing in printings:
        if any(needle in (getattr(printing, field) or "").lower() for field in printing_fields):
            return True
    return False


def localized_regex_contains(cube_card, pattern: Pattern, oracle_fields, printing_fields, available_sets=None):
    if any(pattern.search(getattr(cube_card.oracle, field) or "") for field in oracle_fields):
        return True

    printings = cube_card.oracle.printings.all()
    if available_sets is not None:
        printings = printings.filter(set__in=available_sets)
    for printing in printings:
        if any(pattern.search(getattr(printing, field) or "") for field in printing_fields):
            return True
    return False


def compile_regex(value):
    try:
        return re.compile(value, re.IGNORECASE)
    except re.error as exc:
        raise QuerySyntaxError(f"Regex invalide: {exc}") from exc


def cube_card_has_set(cube_card, value, available_sets=None):
    needle = value.lower()
    printings = cube_card.oracle.printings.all()
    if available_sets is not None:
        printings = printings.filter(set__in=available_sets)
    return printings.filter(Q(set__code__iexact=value) | Q(set__name__icontains=needle)).exists()


def resolve_stat_query(field, value, stat_queries):
    if stat_queries is None:
        raise QuerySyntaxError("Les references query: ne sont pas disponibles ici.")
    if field == "query_id":
        if not value.isdigit():
            raise QuerySyntaxError(f"Identifiant de requete invalide: {value}")
        stat_query = stat_queries.filter(pk=int(value)).first()
    else:
        stat_query = stat_queries.filter(name__iexact=value).first()
    if not stat_query:
        raise QuerySyntaxError(f"Requete sauvegardee introuvable: {value}")
    return stat_query


def next_query_resolution_ids(stat_query, resolving_query_ids):
    resolving_query_ids = resolving_query_ids or set()
    if stat_query.pk in resolving_query_ids:
        raise QuerySyntaxError(f"Reference circulaire de requete: {stat_query.name}")
    return resolving_query_ids | {stat_query.pk}


class OracleProxy:
    def __init__(self, oracle):
        self.oracle = oracle
        self.tags = []


def unquote(value):
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value
