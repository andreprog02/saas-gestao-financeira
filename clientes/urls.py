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

    # Documentos
    path("<int:cliente_id>/documento/upload/", views.upload_documento, name="upload_documento"),
    path("documento/<int:doc_id>/excluir/", views.excluir_documento, name="excluir_documento"),

    # Bens Móveis
    path("<int:cliente_id>/bem-movel/adicionar/", views.adicionar_bem_movel, name="adicionar_bem_movel"),
    path("bem-movel/<int:bem_id>/excluir/", views.excluir_bem_movel, name="excluir_bem_movel"),
    path("bem-movel/<int:bem_id>/doc/", views.upload_doc_movel, name="upload_doc_movel"),

    # Bens Imóveis
    path("<int:cliente_id>/bem-imovel/adicionar/", views.adicionar_bem_imovel, name="adicionar_bem_imovel"),
    path("bem-imovel/<int:bem_id>/excluir/", views.excluir_bem_imovel, name="excluir_bem_imovel"),
    path("bem-imovel/<int:bem_id>/doc/", views.upload_doc_imovel, name="upload_doc_imovel"),

    # Doc de bem (genérico)
    path("doc-bem/<int:doc_id>/excluir/", views.excluir_doc_bem, name="excluir_doc_bem"),

    # Consulta de Crédito
    path("<int:cliente_id>/consulta-credito/", views.adicionar_consulta_credito, name="adicionar_consulta_credito"),
    path("consulta-credito/<int:consulta_id>/excluir/", views.excluir_consulta_credito, name="excluir_consulta_credito"),
]