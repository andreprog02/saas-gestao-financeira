from django.urls import path
from . import views

urlpatterns = [
    # Listagem e Criação Inicial
    path('', views.lista_contratos, name='lista_contratos'),
    path('criar/', views.criar_contrato, name='criar_contrato'),

    # Gestão de Itens (Cheques/Títulos)
    path('adicionar-item/<int:contrato_id>/', views.adicionar_item, name='adicionar_item'),
    path('editar-item/<int:item_id>/', views.editar_item, name='editar_item'),
    path('excluir-item/<int:item_id>/', views.excluir_item, name='excluir_item'),
    
    # === NOVA ROTA: Exclusão de Contrato (Apenas Simulação) ===
    path('excluir-contrato/<int:contrato_id>/', views.excluir_contrato, name='excluir_contrato'),
    
    # Finalização e Ativação
    path('simular/<int:contrato_id>/', views.simular_contrato, name='simular_contrato'),
    path('ativar/<int:contrato_id>/', views.ativar_contrato, name='ativar_contrato'),
    
    # Liquidação (Baixa)
    path('liquidar-item/<int:item_id>/', views.liquidar_item, name='liquidar_item'),
    path('liquidar-contrato/<int:contrato_id>/', views.liquidar_contrato, name='liquidar_contrato'),
]