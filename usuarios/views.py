from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages


def login_view(request):
    """Tela de login customizada (substitui o /admin/login/)."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        senha = request.POST.get("password", "")

        user = authenticate(request, username=username, password=senha)

        if user is not None:
            if not user.is_active:
                messages.error(request, "Conta desativada. Fale com o administrador.")
                return render(request, "usuarios/login.html")

            login(request, user)
            proximo = request.GET.get("next", "/")
            return redirect(proximo)
        else:
            messages.error(request, "Usuário ou senha inválidos.")

    return render(request, "usuarios/login.html")


def logout_view(request):
    """Logout e redireciona para login."""
    logout(request)
    messages.info(request, "Você saiu do sistema.")
    return redirect("usuarios:login")


@login_required
def perfil_view(request):
    """Tela de perfil do usuário logado."""
    if request.method == "POST":
        user = request.user
        user.first_name = request.POST.get("first_name", user.first_name)
        user.last_name = request.POST.get("last_name", user.last_name)
        user.email = request.POST.get("email", user.email)
        user.telefone = request.POST.get("telefone", user.telefone)

        nova_senha = request.POST.get("nova_senha", "").strip()
        if nova_senha:
            if len(nova_senha) < 6:
                messages.error(request, "A senha deve ter pelo menos 6 caracteres.")
                return render(request, "usuarios/perfil.html")
            user.set_password(nova_senha)
            messages.info(request, "Senha alterada. Faça login novamente.")

        user.save()
        messages.success(request, "Perfil atualizado.")

        if nova_senha:
            return redirect("usuarios:login")
        return redirect("usuarios:perfil")

    return render(request, "usuarios/perfil.html")


@login_required
def sem_empresa(request):
    """Tela exibida quando o usuário não está vinculado a nenhuma empresa."""
    return render(request, "usuarios/sem_empresa.html")
