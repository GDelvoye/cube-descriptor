from django import forms

from .models import StatQuery
from .query_engine import QuerySyntaxError, parse_query


class CubeStatsForm(forms.Form):
    raw_query = forms.CharField(
        label="Requete",
        initial="type:Creature",
        help_text='Exemples: color:U, type:Creature, text:"enters the battlefield", mv<=2, power=2, keyword:Flying, tag:removal',
    )
    minimum_hits = forms.IntegerField(label="Au moins", min_value=0, initial=1)
    exact_hits = forms.IntegerField(label="Exactement", min_value=0, required=False)
    between_min = forms.IntegerField(label="Entre min", min_value=0, required=False)
    between_max = forms.IntegerField(label="Entre max", min_value=0, required=False)


class StatQueryForm(forms.ModelForm):
    class Meta:
        model = StatQuery
        fields = ["name", "raw_query", "description", "scope"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["scope"].choices = [
            (StatQuery.Scope.USER, "Utilisateur"),
            (StatQuery.Scope.CUBE, "Cube courant"),
        ]

    def clean_raw_query(self):
        raw_query = self.cleaned_data["raw_query"]
        try:
            parse_query(raw_query)
        except QuerySyntaxError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return raw_query


class UserStatQueryForm(forms.ModelForm):
    class Meta:
        model = StatQuery
        fields = ["name", "raw_query", "description"]

    def clean_raw_query(self):
        raw_query = self.cleaned_data["raw_query"]
        try:
            parse_query(raw_query)
        except QuerySyntaxError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return raw_query
