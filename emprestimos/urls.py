from django.urls import path
from . import views
from .renegociacao import renegociar

app_name = "emprestimos"

urlpatterns = [
    path("novo/", views.novo_emprestimo_busca_cliente, name="novo_busca"),
    path("novo/<int:cliente_id>/", views.novo_emprestimo_form, name="novo_form"),
    path("contratos/", views.contratos, name="contratos"),
    path("contratos/<int:emprestimo_id>/", views.contrato_detalhe, name="contrato_detalhe"),
    path("a-vencer/", views.a_vencer, name="a_vencer"),
    path("vencidos/", views.vencidos, name="vencidos"),
    path("parcela/<int:parcela_id>/pagar/", views.pagar_parcela, name="pagar_parcela"),

   

    path("contratos/<int:emprestimo_id>/cancelar/", views.cancelar_contrato, name="cancelar_contrato"),
    path("contratos/<int:emprestimo_id>/reabrir/", views.reabrir_contrato, name="reabrir_contrato"),



    path("renegociar/<int:emprestimo_id>/", renegociar, name="renegociar"),
]
