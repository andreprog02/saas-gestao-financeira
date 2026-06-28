from django.urls import path
from . import views

app_name = "financeiro"

urlpatterns = [
    path("", views.index, name="index"),
    path("estornar/<int:transacao_id>/", views.estornar, name="estornar"),

    # Caixa
    path("caixa/", views.caixa_painel, name="caixa_painel"),
    path("caixa/abrir/", views.caixa_abrir, name="caixa_abrir"),
    path("caixa/fechar/", views.caixa_fechar, name="caixa_fechar"),
    path("caixa/fechar/<int:caixa_id>/", views.caixa_fechar, name="caixa_fechar_id"),
    path("caixa/reabrir/", views.caixa_reabrir, name="caixa_reabrir"),
    path("caixa/lancamento/", views.caixa_lancamento, name="caixa_lancamento"),
    path("caixa/estornar/<int:mov_id>/", views.caixa_estornar, name="caixa_estornar"),
    path("caixa/<int:caixa_id>/", views.caixa_detalhe, name="caixa_detalhe"),
    path("caixa/historico/", views.caixa_historico, name="caixa_historico"),

    # AJAX
    path("buscar-cliente/", views.buscar_cliente_ajax, name="buscar_cliente"),

    # Tesouraria
    path("tesouraria/", views.tesouraria_painel, name="tesouraria_painel"),
    path("tesouraria/lancamento/", views.tesouraria_lancamento, name="tesouraria_lancamento"),

    # Custódia de cheques
    path("custodia/", views.custodia_painel, name="custodia_painel"),
    path("custodia/entrada/", views.custodia_entrada, name="custodia_entrada"),
    path("custodia/<int:cheque_id>/acao/", views.custodia_acao, name="custodia_acao"),
]
