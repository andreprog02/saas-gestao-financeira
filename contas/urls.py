from django.urls import path
from . import views

app_name = 'contas'

urlpatterns = [
    path('sacar/<int:cliente_id>/', views.realizar_saque, name='realizar_saque'),
]