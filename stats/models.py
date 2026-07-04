from django.conf import settings
from django.db import models


class StatQuery(models.Model):
    class Scope(models.TextChoices):
        GLOBAL = "global", "Global"
        USER = "user", "User"
        CUBE = "cube", "Cube"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="stat_queries"
    )
    cube = models.ForeignKey("cubes.Cube", on_delete=models.CASCADE, null=True, blank=True, related_name="stat_queries")
    name = models.CharField(max_length=255)
    raw_query = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    scope = models.CharField(max_length=16, choices=Scope.choices, default=Scope.USER)
    created_at = models.DateTimeField(auto_now_add=True)
    match_cache_refreshed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "stat queries"

    def __str__(self):
        return self.name


class StatQueryMatch(models.Model):
    stat_query = models.ForeignKey(StatQuery, on_delete=models.CASCADE, related_name="matches")
    oracle = models.ForeignKey("cards.CardOracle", on_delete=models.CASCADE, related_name="stat_query_matches")
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["stat_query", "oracle"], name="unique_stat_query_oracle_match"),
        ]


class StatQueryDependency(models.Model):
    parent = models.ForeignKey(StatQuery, on_delete=models.CASCADE, related_name="dependencies")
    child = models.ForeignKey(StatQuery, on_delete=models.CASCADE, related_name="dependents")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["parent", "child"], name="unique_stat_query_dependency"),
        ]


class SetIndicatorExpectedValue(models.Model):
    set = models.ForeignKey("cards.Set", on_delete=models.CASCADE, related_name="indicator_expected_values")
    indicator_key = models.CharField(max_length=64)
    stat_query = models.ForeignKey(StatQuery, on_delete=models.CASCADE, null=True, blank=True)
    expected = models.FloatField()
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["set", "indicator_key", "stat_query"],
                name="unique_set_indicator_expected_value",
                nulls_distinct=False,
            ),
        ]
