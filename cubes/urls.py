from django.urls import path

from . import views

app_name = "cubes"

urlpatterns = [
    path("", views.cube_list, name="list"),
    path("new/", views.cube_create, name="create"),
    path("<int:pk>/", views.cube_detail, name="detail"),
    path("<int:pk>/stats/", views.cube_stats, name="stats"),
    path("<int:pk>/cards/<int:cube_card_id>/edit/", views.cube_card_edit, name="card_edit"),
    path("<int:pk>/cards/<int:cube_card_id>/remove/", views.cube_card_remove, name="card_remove"),
]
