from django.urls import path
from . import views

app_name = "financeiro"

urlpatterns = [
    path('', views.index, name='index'),
    path('estornar/<int:transacao_id>/', views.estornar, name='estornar'),

    # Caixa
    path('caixa/', views.caixa_painel, name='caixa_painel'),
    path('caixa/abrir/', views.caixa_abrir, name='caixa_abrir'),
    path('caixa/fechar/', views.caixa_fechar, name='caixa_fechar'),
    path('caixa/<int:caixa_id>/', views.caixa_detalhe, name='caixa_detalhe'),
]