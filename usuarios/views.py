from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q

from .models import Usuario, PermissaoModulo, Empresa


def login_view(request):
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
            return redirect(request.GET.get("next", "/"))
        else:
            messages.error(request, "Usuário ou senha inválidos.")
    return render(request, "usuarios/login.html")


def logout_view(request):
    logout(request)
    messages.info(request, "Você saiu do sistema.")
    return redirect("usuarios:login")


@login_required
def perfil_view(request):
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
    return render(request, "usuarios/sem_empresa.html")


# ==============================================================================
# GESTÃO DE USUÁRIOS
# ==============================================================================

@login_required
def listar_usuarios(request):
    """Lista todos os usuários com seus cargos e status."""
    if not request.user.tem_permissao("USUARIOS", "VISUALIZAR"):
        messages.error(request, "Sem permissão para acessar gestão de usuários.")
        return redirect("dashboard")

    usuarios = Usuario.objects.filter(is_superuser=False).order_by("first_name", "username")

    busca = request.GET.get("q", "")
    if busca:
        usuarios = usuarios.filter(
            Q(first_name__icontains=busca) |
            Q(last_name__icontains=busca) |
            Q(username__icontains=busca)
        )

    cargo_filtro = request.GET.get("cargo", "")
    if cargo_filtro:
        usuarios = usuarios.filter(cargo=cargo_filtro)

    return render(request, "usuarios/listar.html", {
        "usuarios": usuarios,
        "busca": busca,
        "cargo_filtro": cargo_filtro,
        "cargos": Usuario.Cargo.choices,
    })


@login_required
def criar_usuario(request):
    """Cria novo usuário com cargo e permissões."""
    if not request.user.tem_permissao("USUARIOS", "GERENCIAR"):
        messages.error(request, "Sem permissão para criar usuários.")
        return redirect("usuarios:listar")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        senha = request.POST.get("senha", "")
        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        email = request.POST.get("email", "").strip()
        cargo = request.POST.get("cargo", "OPERACIONAL")
        telefone = request.POST.get("telefone", "").strip()

        if not username or not senha:
            messages.error(request, "Usuário e senha são obrigatórios.")
        elif Usuario.objects.filter(username=username).exists():
            messages.error(request, f"Usuário '{username}' já existe.")
        else:
            user = Usuario.objects.create_user(
                username=username,
                password=senha,
                first_name=first_name,
                last_name=last_name,
                email=email,
                cargo=cargo,
                telefone=telefone,
                empresa=request.user.empresa,
            )

            # Salva permissões de módulo
            for modulo, _ in PermissaoModulo.MODULO_CHOICES:
                nivel = request.POST.get(f"perm_{modulo}", "NENHUM")
                if nivel != "NENHUM":
                    PermissaoModulo.objects.create(
                        usuario=user, modulo=modulo, nivel=nivel
                    )

            messages.success(request, f"Usuário '{username}' criado com sucesso.")
            return redirect("usuarios:listar")

    return render(request, "usuarios/criar.html", {
        "cargos": Usuario.Cargo.choices,
        "modulos": PermissaoModulo.MODULO_CHOICES,
        "niveis": PermissaoModulo.NIVEL_CHOICES,
    })


@login_required
def editar_usuario(request, user_id):
    """Edita usuário existente e suas permissões."""
    if not request.user.tem_permissao("USUARIOS", "GERENCIAR"):
        messages.error(request, "Sem permissão para editar usuários.")
        return redirect("usuarios:listar")

    user = get_object_or_404(Usuario, id=user_id)

    if request.method == "POST":
        user.first_name = request.POST.get("first_name", user.first_name)
        user.last_name = request.POST.get("last_name", user.last_name)
        user.email = request.POST.get("email", user.email)
        user.cargo = request.POST.get("cargo", user.cargo)
        user.telefone = request.POST.get("telefone", user.telefone)
        user.is_active = request.POST.get("ativo") == "on"

        nova_senha = request.POST.get("nova_senha", "").strip()
        if nova_senha:
            user.set_password(nova_senha)

        user.save()

        # Atualiza permissões
        for modulo, _ in PermissaoModulo.MODULO_CHOICES:
            nivel = request.POST.get(f"perm_{modulo}", "NENHUM")
            perm, created = PermissaoModulo.objects.get_or_create(
                usuario=user, modulo=modulo,
                defaults={"nivel": nivel}
            )
            if not created:
                perm.nivel = nivel
                perm.save()

        messages.success(request, f"Usuário '{user.username}' atualizado.")
        return redirect("usuarios:listar")

    # Monta dict de permissões atuais
    perms_atuais = {}
    for p in user.permissoes_modulo.all():
        perms_atuais[p.modulo] = p.nivel

    import json
    perms_json = json.dumps(perms_atuais)

    return render(request, "usuarios/editar.html", {
        "usuario_edit": user,
        "cargos": Usuario.Cargo.choices,
        "modulos": PermissaoModulo.MODULO_CHOICES,
        "niveis": PermissaoModulo.NIVEL_CHOICES,
        "perms_atuais": perms_atuais,
        "perms_json": perms_json,
    })


@login_required
def toggle_ativo(request, user_id):
    """Ativa/desativa um usuário."""
    if not request.user.tem_permissao("USUARIOS", "GERENCIAR"):
        messages.error(request, "Sem permissão.")
        return redirect("usuarios:listar")

    user = get_object_or_404(Usuario, id=user_id)
    user.is_active = not user.is_active
    user.save()
    status = "ativado" if user.is_active else "desativado"
    messages.info(request, f"Usuário '{user.username}' {status}.")
    return redirect("usuarios:listar")
