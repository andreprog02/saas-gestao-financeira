from django.urls import path
from . import views

app_name = 'cobranca'

urlpatterns = [
    path('', views.painel_cobranca, name='painel_cobranca'),
    path('registrar/', views.registrar_evento, name='registrar_evento'),
]