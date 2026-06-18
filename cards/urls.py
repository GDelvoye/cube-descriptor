from django.urls import path

from . import views

app_name = "cards"

urlpatterns = [
    path("", views.card_search, name="search"),
    path("add-selected-to-cube/", views.add_selected_to_cube, name="add_selected_to_cube"),
    path("<int:oracle_id>/add-to-cube/", views.add_to_cube, name="add_to_cube"),
]
