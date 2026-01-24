from django.urls import path
from .views import (
    lista_contratos, criar_contrato, adicionar_item, simular_contrato, ativar_contrato,
    editar_item, excluir_item, liquidar_item, liquidar_contrato  # Add these if they exist in views.py
)
from .renegociacao import renegociar_contrato

urlpatterns = [
    path('', lista_contratos, name='lista_contratos'),
    path('criar/', criar_contrato, name='criar_contrato'),
    path('adicionar_item/<int:contrato_id>/', adicionar_item, name='adicionar_item'),
    path('simular/<int:contrato_id>/', simular_contrato, name='simular_contrato'),
    path('ativar/<int:contrato_id>/', ativar_contrato, name='ativar_contrato'),
    path('renegociar/<int:contrato_id>/', renegociar_contrato, name='renegociar_contrato'),
    # Add these to fix NoReverseMatch
    path('editar_item/<int:item_id>/', editar_item, name='editar_item'),
    path('excluir_item/<int:item_id>/', excluir_item, name='excluir_item'),
    path('liquidar_item/<int:item_id>/', liquidar_item, name='liquidar_item'),
    path('liquidar_contrato/<int:contrato_id>/', liquidar_contrato, name='liquidar_contrato'),
]