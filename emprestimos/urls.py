from django.urls import path
from . import views
from . import views_esteira
from .renegociacao import renegociar

app_name = "emprestimos"

urlpatterns = [
    # --- Criação de Empréstimos ---
    path("novo/", views.novo_emprestimo_busca, name="novo_busca"),
    path("novo/<int:cliente_id>/", views.novo_emprestimo_form, name="novo_form"),

    # --- Gestão de Contratos ---
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

    # --- ESTEIRA DE APROVAÇÃO (Workflow Multi-Etapa) ---
    path("esteira/", views_esteira.painel_esteira, name="painel_esteira"),
    path("esteira/nova/", views_esteira.nova_proposta, name="esteira_nova"),
    path("esteira/<int:proposta_id>/", views_esteira.detalhe_proposta, name="esteira_detalhe"),
    path("esteira/<int:proposta_id>/avancar/", views_esteira.avancar_etapa, name="esteira_avancar"),
    path("esteira/<int:proposta_id>/devolver/", views_esteira.devolver_etapa, name="esteira_devolver"),
    path("esteira/<int:proposta_id>/negar/", views_esteira.negar_proposta, name="esteira_negar"),
    path("esteira/checklist/<int:item_id>/", views_esteira.marcar_checklist, name="esteira_checklist"),
    path("esteira/simular/", views_esteira.simular_ajax, name="esteira_simular"),

    # --- Legado (propostas antigas, mantido para compatibilidade) ---
    path('propostas/', views.listar_propostas, name='listar_propostas'),
    path('propostas/nova/', views.criar_proposta, name='criar_proposta'),
    path('propostas/<int:proposta_id>/analise/', views.analisar_proposta, name='analisar_proposta'),
]

