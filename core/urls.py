from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    path("configuracoes/", views.configuracoes, name="configuracoes"),
]
