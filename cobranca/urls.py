from django.urls import path
from . import views

app_name = 'cobranca'

urlpatterns = [
    path('', views.painel_cobranca, name='painel_cobranca'),
    path('registrar/', views.registrar_evento, name='registrar_evento'),

    # Cartas de Cobrança
    path('cartas/', views.listar_inadimplentes_carta, name='carta_listar'),
    path('cartas/emitir/<int:emprestimo_id>/', views.emitir_carta, name='carta_emitir'),
    path('cartas/consultar/', views.consultar_cartas, name='carta_consultar'),
    path('cartas/reimprimir/<int:carta_id>/', views.reimprimir_carta, name='carta_reimprimir'),

    # Despesas de Cobrança
    path('despesas/', views.listar_despesas, name='despesas_listar'),
    path('despesas/adicionar/<int:emprestimo_id>/', views.adicionar_despesa, name='despesa_adicionar'),
    path('despesas/<int:despesa_id>/excluir/', views.excluir_despesa, name='despesa_excluir'),
]
