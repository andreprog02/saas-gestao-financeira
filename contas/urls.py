from django.urls import path
from . import views

app_name = 'contas'

urlpatterns = [
    path('operacao/saque/', views.operacao_saque, name='operacao_saque'),
]