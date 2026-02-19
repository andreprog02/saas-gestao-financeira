from django.urls import path
from . import views
from .renegociacao import renegociar

app_name = "emprestimos"

urlpatterns = [
    # --- Criação de Empréstimos ---
    # Corrigido: nome da view é novo_emprestimo_busca
    path("novo/", views.novo_emprestimo_busca, name="novo_busca"),
    
    # Mantido: view espera cliente_id
    path("novo/<int:cliente_id>/", views.novo_emprestimo_form, name="novo_form"),

    # --- Gestão de Contratos ---
    # Corrigido: nome da view é lista_contratos
    path("contratos/", views.lista_contratos, name="contratos"),
    
    # Corrigido: alterado para <int:pk> pois a view contrato_detalhe espera pk
    path("contratos/<int:pk>/", views.contrato_detalhe, name="contrato_detalhe"),
    
    # Corrigido: alterado para <int:pk> pois a view cancelar_contrato espera pk
    path("contratos/<int:pk>/cancelar/", views.cancelar_contrato, name="cancelar_contrato"),

    # --- Funcionalidades de Parcelas ---
    # Corrigido: alterado para <int:pk> pois a view pagar_parcela espera pk
    path("parcela/<int:pk>/pagar/", views.pagar_parcela, name="pagar_parcela"),
    
    # Mantido: view espera parcela_id explicitamente
    path("parcela/<int:parcela_id>/calcular-valores/", views.calcular_valores_parcela_ajax, name="calcular_valores_ajax"),

    # --- Renegociação ---
    path("renegociar/<int:emprestimo_id>/", renegociar, name="renegociar"),

    path('contrato/<int:pk>/parceiro/', views.vincular_parceiro, name='vincular_parceiro'),

    # --- TODO: Views ainda não implementadas no views.py ---
    # Descomente estas linhas apenas quando criar as funções correspondentes no views.py
    
    path("a-vencer/", views.a_vencer, name="a_vencer"),
    #path("contratos/<int:pk>/pdf/", views.contrato_pdf, name="contrato_pdf"),
    path("vencidos/", views.vencidos, name="vencidos"),
    #path("contratos/<int:emprestimo_id>/pdf/", views.contrato_pdf, name="contrato_pdf"),
    #path("contratos/<int:emprestimo_id>/reabrir/", views.reabrir_contrato, name="reabrir_contrato"),
]