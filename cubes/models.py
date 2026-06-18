from django.conf import settings
from django.db import models

from cards.models import CardOracle, CardPrinting


class Cube(models.Model):
    class Visibility(models.TextChoices):
        PRIVATE = "private", "Private"
        UNLISTED = "unlisted", "Unlisted"
        PUBLIC = "public", "Public"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cubes")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    visibility = models.CharField(max_length=16, choices=Visibility.choices, default=Visibility.PRIVATE)
    booster_size = models.PositiveSmallIntegerField(default=15)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class CubeCard(models.Model):
    cube = models.ForeignKey(Cube, on_delete=models.CASCADE, related_name="cards")
    oracle = models.ForeignKey(CardOracle, on_delete=models.CASCADE, related_name="cube_entries")
    printing = models.ForeignKey(CardPrinting, on_delete=models.SET_NULL, null=True, blank=True, related_name="cube_entries")
    quantity = models.PositiveSmallIntegerField(default=1)
    section = models.CharField(max_length=64, blank=True)
    tags = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["cube", "oracle__name"]
        constraints = [
            models.UniqueConstraint(fields=["cube", "oracle", "section"], name="unique_cube_oracle_section"),
        ]

    def __str__(self):
        return f"{self.quantity}x {self.oracle.name} in {self.cube.name}"
