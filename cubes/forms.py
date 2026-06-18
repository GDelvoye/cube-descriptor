from django import forms

from .models import Cube, CubeCard


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
            initial_cube = self.initial.get("cube")
            if initial_cube and not self.fields["cube"].queryset.filter(pk=initial_cube).exists():
                self.initial.pop("cube", None)


class CubeCardForm(forms.ModelForm):
    tags_text = forms.CharField(label="Tags", required=False, help_text="Separes par des virgules: removal, fixing")

    class Meta:
        model = CubeCard
        fields = ["quantity", "section", "notes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["quantity"].min_value = 1
        if self.instance and self.instance.pk:
            self.fields["tags_text"].initial = ", ".join(self.instance.tags or [])

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.tags = [tag.strip() for tag in self.cleaned_data["tags_text"].split(",") if tag.strip()]
        if commit:
            instance.save()
        return instance
