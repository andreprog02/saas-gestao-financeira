from django.urls import path
from . import views

app_name = 'financeiro'

urlpatterns = [
    path('', views.index, name='index'),
    path('estornar/<int:transacao_id>/', views.estornar, name='estornar'),
]