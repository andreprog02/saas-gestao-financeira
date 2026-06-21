from django.urls import path
from . import views

app_name = "conciliacao"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("conta/nova/", views.criar_conta, name="criar_conta"),
    path("conta/<int:conta_id>/extrato/", views.extrato_conta, name="extrato_conta"),
    path("conta/<int:conta_id>/extrato/pdf/", views.extrato_conta_pdf, name="extrato_conta_pdf"),
    path("importar/", views.importar_extrato, name="importar"),
    path("extrato/<int:extrato_id>/", views.detalhe_extrato, name="detalhe_extrato"),
    path("extrato/<int:extrato_id>/reconciliar/", views.reconciliar, name="reconciliar"),
    path("extrato/<int:extrato_id>/pdf/", views.exportar_pdf, name="exportar_pdf"),
    path("lancamento/<int:lancamento_id>/conciliar/", views.conciliar_manual, name="conciliar_manual"),
    path("lancamento/<int:lancamento_id>/confirmar/", views.confirmar_sugestao, name="confirmar_sugestao"),
    path("lancamento/<int:lancamento_id>/ignorar/", views.ignorar_lancamento, name="ignorar"),
    path("lancamento/<int:lancamento_id>/criar-transacao/", views.criar_transacao_de_lancamento, name="criar_transacao"),
]
