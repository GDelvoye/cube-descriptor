from django.conf import settings
from django.db import models

DEFAULT_AVAILABLE_SET_TYPES = ("core", "expansion")


class Set(models.Model):
    code = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=255)
    released_at = models.DateField(null=True, blank=True)
    set_type = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-released_at", "code"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class UserSetPreference(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="set_preferences")
    set = models.ForeignKey(Set, on_delete=models.CASCADE, related_name="user_preferences")
    is_available = models.BooleanField()

    class Meta:
        ordering = ["set__code"]
        constraints = [
            models.UniqueConstraint(fields=["user", "set"], name="unique_user_set_preference"),
        ]

    def __str__(self):
        status = "available" if self.is_available else "excluded"
        return f"{self.user} - {self.set.code}: {status}"


class CardOracle(models.Model):
    scryfall_oracle_id = models.UUIDField(unique=True)
    name = models.CharField(max_length=255, db_index=True)
    mana_cost = models.CharField(max_length=255, blank=True)
    mana_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    colors = models.JSONField(default=list, blank=True)
    color_identity = models.JSONField(default=list, blank=True)
    type_line = models.CharField(max_length=255, blank=True, db_index=True)
    oracle_text = models.TextField(blank=True)
    keywords = models.JSONField(default=list, blank=True)
    power = models.CharField(max_length=16, blank=True)
    toughness = models.CharField(max_length=16, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class CardPrinting(models.Model):
    scryfall_id = models.UUIDField(unique=True)
    oracle = models.ForeignKey(CardOracle, on_delete=models.CASCADE, related_name="printings")
    set = models.ForeignKey(Set, on_delete=models.PROTECT, related_name="printings")
    set_code = models.CharField(max_length=16, db_index=True)
    collector_number = models.CharField(max_length=32)
    rarity = models.CharField(max_length=32, blank=True)
    image_url = models.URLField(max_length=500, blank=True)
    released_at = models.DateField(null=True, blank=True)
    lang = models.CharField(max_length=16, default="en", db_index=True)
    printed_name = models.CharField(max_length=255, blank=True, db_index=True)
    printed_type_line = models.CharField(max_length=255, blank=True, db_index=True)
    printed_oracle_text = models.TextField(blank=True)

    class Meta:
        ordering = ["oracle__name", "-released_at", "set_code", "collector_number"]
        indexes = [
            models.Index(fields=["set_code", "collector_number"]),
        ]

    def __str__(self):
        return f"{self.oracle.name} [{self.set_code} #{self.collector_number}]"
