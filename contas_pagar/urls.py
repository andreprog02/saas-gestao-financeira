from django.urls import path
from . import views

app_name = "contas_pagar"

urlpatterns = [
    path("", views.painel, name="painel"),
    path("cadastrar/", views.cadastrar, name="cadastrar"),
    path("<int:conta_id>/", views.detalhe, name="detalhe"),
    path("<int:conta_id>/aprovar/", views.aprovar, name="aprovar"),
    path("<int:conta_id>/negar/", views.negar, name="negar"),
    path("<int:conta_id>/devolver/", views.devolver, name="devolver"),
    path("<int:conta_id>/reenviar/", views.reenviar, name="reenviar"),
    path("<int:conta_id>/pagar/", views.confirmar_pagamento, name="confirmar_pagamento"),
]
