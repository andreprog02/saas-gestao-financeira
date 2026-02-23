from django.urls import path
from . import views

app_name = "clientes"

urlpatterns = [
    # --- CORREÇÃO 1: Mudamos o name de "lista" para "cliente_list" ---
    # Isso resolve o erro "Reverse for 'cliente_list' not found"
    path("", views.clientes_lista, name="cliente_list"),
    
    path("novo/", views.novo_cliente, name="novo"),
    path("<int:cliente_id>/", views.clientes_detalhe, name="detalhe"),
    path("<int:cliente_id>/editar/", views.clientes_editar, name="editar"),
    path("<int:cliente_id>/excluir/", views.clientes_excluir, name="excluir"),

    # --- CORREÇÃO 2: Adicionamos as rotas de Importar/Exportar ---
    path("exportar/", views.exportar_clientes_csv, name="exportar_clientes"),
    path("importar/", views.importar_clientes_csv, name="importar_clientes"),
]