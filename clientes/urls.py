from django.urls import path
from . import views

app_name = "clientes"

urlpatterns = [
    path("", views.clientes_lista, name="lista"),
    path("novo/", views.clientes_novo, name="novo"),
    path("<int:cliente_id>/", views.clientes_detalhe, name="detalhe"),
    path("<int:cliente_id>/editar/", views.clientes_editar, name="editar"),
    path("<int:cliente_id>/excluir/", views.clientes_excluir, name="excluir"),
]
