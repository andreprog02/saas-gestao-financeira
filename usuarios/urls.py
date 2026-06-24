from django.urls import path
from . import views

app_name = "usuarios"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("perfil/", views.perfil_view, name="perfil"),
    path("sem-empresa/", views.sem_empresa, name="sem_empresa"),

    # Gestão de usuários
    path("gerenciar/", views.listar_usuarios, name="listar"),
    path("gerenciar/novo/", views.criar_usuario, name="criar"),
    path("gerenciar/<int:user_id>/editar/", views.editar_usuario, name="editar"),
    path("gerenciar/<int:user_id>/toggle/", views.toggle_ativo, name="toggle_ativo"),
]
