from django import forms


class CubeStatsForm(forms.Form):
    raw_query = forms.CharField(
        label="Requete",
        initial="type:Creature",
        help_text='Exemples: color:U, type:Creature, text:"enters the battlefield", mv<=2, tag:removal, color:U AND type:Creature',
    )
    minimum_hits = forms.IntegerField(label="Au moins", min_value=0, initial=1)
    exact_hits = forms.IntegerField(label="Exactement", min_value=0, required=False)
    between_min = forms.IntegerField(label="Entre min", min_value=0, required=False)
    between_max = forms.IntegerField(label="Entre max", min_value=0, required=False)
