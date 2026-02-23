from django.urls import path
from . import views
from .renegociacao import renegociar

app_name = "emprestimos"

urlpatterns = [
    # --- Criação de Empréstimos ---
    path("novo/", views.novo_emprestimo_busca, name="novo_busca"),
    path("novo/<int:cliente_id>/", views.novo_emprestimo_form, name="novo_form"),

    # --- Gestão de Contratos ---
    # CORREÇÃO: Usando views.listar_contratos (com R)
    path("contratos/", views.listar_contratos, name="contratos"),
    
    path("contratos/<int:pk>/", views.contrato_detalhe, name="contrato_detalhe"),
    path("contratos/<int:pk>/cancelar/", views.cancelar_contrato, name="cancelar_contrato"),

    # --- Funcionalidades de Parcelas ---
    path("parcela/<int:pk>/pagar/", views.pagar_parcela, name="pagar_parcela"),
    path("parcela/<int:parcela_id>/calcular-valores/", views.calcular_valores_parcela_ajax, name="calcular_valores_ajax"),

    # --- Renegociação e Parceiros ---
    path("renegociar/<int:emprestimo_id>/", renegociar, name="renegociar"),
    path('contrato/<int:pk>/parceiro/', views.vincular_parceiro, name='vincular_parceiro'),

    # --- Views Auxiliares ---
    path("a-vencer/", views.a_vencer, name="a_vencer"),
    path("vencidos/", views.vencidos, name="vencidos"),

    # --- NOVAS URLS DA ESTEIRA DE CRÉDITO (Unificadas aqui) ---
    path('propostas/', views.listar_propostas, name='listar_propostas'),
    path('propostas/nova/', views.criar_proposta, name='criar_proposta'),
    path('propostas/<int:proposta_id>/analise/', views.analisar_proposta, name='analisar_proposta'),
]

