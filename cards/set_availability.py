from django.db.models import Q

from .models import DEFAULT_AVAILABLE_SET_TYPES, Set, UserSetPreference


def get_available_sets(user):
    sets = Set.objects.all()
    if not user.is_authenticated:
        return sets.filter(set_type__in=DEFAULT_AVAILABLE_SET_TYPES)

    preferences = UserSetPreference.objects.filter(user=user)
    included_ids = preferences.filter(is_available=True).values("set_id")
    excluded_ids = preferences.filter(is_available=False).values("set_id")
    return sets.filter(set_type__in=DEFAULT_AVAILABLE_SET_TYPES).exclude(pk__in=excluded_ids) | sets.filter(
        pk__in=included_ids
    )


def get_excluded_sets(user):
    sets = Set.objects.all()
    preferences = UserSetPreference.objects.filter(user=user)
    included_ids = preferences.filter(is_available=True).values("set_id")
    excluded_ids = preferences.filter(is_available=False).values("set_id")
    return sets.filter(~Q(set_type__in=DEFAULT_AVAILABLE_SET_TYPES) | Q(pk__in=excluded_ids)).exclude(
        pk__in=included_ids
    )
