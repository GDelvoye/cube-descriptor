from django.conf import settings
from django.db import models


class StatQuery(models.Model):
    class Scope(models.TextChoices):
        GLOBAL = "global", "Global"
        USER = "user", "User"
        CUBE = "cube", "Cube"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="stat_queries")
    cube = models.ForeignKey("cubes.Cube", on_delete=models.CASCADE, null=True, blank=True, related_name="stat_queries")
    name = models.CharField(max_length=255)
    raw_query = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    scope = models.CharField(max_length=16, choices=Scope.choices, default=Scope.USER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "stat queries"

    def __str__(self):
        return self.name
