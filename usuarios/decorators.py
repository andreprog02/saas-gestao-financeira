from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages


HIERARQUIA = {
    "OPERACIONAL": 1,
    "CAIXA": 1,
    "SUPERVISOR": 2,
    "GERENTE": 3,
    "DIRETOR": 4,
}


def cargo_minimo(cargo_requerido):
    """Exige um nível mínimo de cargo."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("usuarios:login")
            nivel_usuario = HIERARQUIA.get(request.user.cargo, 0)
            nivel_requerido = HIERARQUIA.get(cargo_requerido, 99)
            if nivel_usuario < nivel_requerido:
                messages.error(request, f"Acesso negado. Nível mínimo: {cargo_requerido}.")
                return redirect("dashboard")
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def permissao_modulo(modulo, nivel_minimo="VISUALIZAR"):
    """Exige permissão em um módulo específico."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("usuarios:login")
            if not request.user.tem_permissao(modulo, nivel_minimo):
                messages.error(request, f"Você não tem permissão para acessar o módulo {modulo}.")
                return redirect("dashboard")
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def apenas_gerente(view_func):
    return cargo_minimo("GERENTE")(view_func)


def apenas_diretor(view_func):
    return cargo_minimo("DIRETOR")(view_func)
