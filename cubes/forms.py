from django import forms

from .models import Cube


class CubeForm(forms.ModelForm):
    class Meta:
        model = Cube
        fields = ["name", "description", "visibility", "booster_size"]


class AddCardToCubeForm(forms.Form):
    cube = forms.ModelChoiceField(queryset=Cube.objects.none())
    quantity = forms.IntegerField(min_value=1, initial=1)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and user.is_authenticated:
            self.fields["cube"].queryset = Cube.objects.filter(owner=user).order_by("name")
