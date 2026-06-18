from django.contrib import admin
from django.urls import include
from django.views.generic import TemplateView
from django.urls import path

urlpatterns = [
    path("", TemplateView.as_view(template_name="home.html"), name="home"),
    path("admin/", admin.site.urls),
    path("cards/", include("cards.urls")),
]
