from django.urls import path

from . import views

app_name = "stats"

urlpatterns = [
    path("", views.stats_index, name="index"),
    path("sets/<int:pk>/", views.set_stats, name="set_stats"),
    path("queries/", views.stat_query_list, name="query_list"),
    path("queries/new/", views.stat_query_create, name="query_create"),
    path("queries/<int:pk>/", views.stat_query_detail, name="query_detail"),
    path("queries/<int:pk>/edit/", views.stat_query_edit, name="query_edit"),
    path("queries/<int:pk>/delete/", views.stat_query_delete, name="query_delete"),
]
