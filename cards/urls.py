from django.urls import path

from . import views

app_name = "cards"

urlpatterns = [
    path("", views.card_search, name="search"),
    path("sets/preferences/", views.set_preferences, name="set_preferences"),
    path("sets/preferences/update/", views.update_set_preferences, name="update_set_preferences"),
    path("add-selected-to-cube/", views.add_selected_to_cube, name="add_selected_to_cube"),
    path("<int:oracle_id>/add-to-cube/", views.add_to_cube, name="add_to_cube"),
]
