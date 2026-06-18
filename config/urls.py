from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

urlpatterns = [
    path("", TemplateView.as_view(template_name="home.html"), name="home"),
    path("admin/", admin.site.urls),
    path("cards/", include("cards.urls")),
    path("cubes/", include("cubes.urls")),
    path("stats/", include("stats.urls")),
]
