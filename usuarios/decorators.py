from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages


def cargo_minimo(cargo_requerido):
    """
    Decorator que exige um nível mínimo de cargo.
    
    Uso:
        @cargo_minimo("ANALISTA")
        def minha_view(request):
            ...
    
    Hierarquia: OPERADOR < ANALISTA < GERENTE < ADMIN
    """
    HIERARQUIA = {
        "OPERADOR": 1,
        "ANALISTA": 2,
        "GERENTE": 3,
        "ADMIN": 4,
    }

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("usuarios:login")

            nivel_usuario = HIERARQUIA.get(request.user.cargo, 0)
            nivel_requerido = HIERARQUIA.get(cargo_requerido, 99)

            if nivel_usuario < nivel_requerido:
                messages.error(
                    request,
                    f"Acesso negado. Esta ação requer nível mínimo: {cargo_requerido}."
                )
                return redirect("dashboard")

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def apenas_gerente(view_func):
    """Atalho: exige pelo menos Gerente."""
    return cargo_minimo("GERENTE")(view_func)


def apenas_admin(view_func):
    """Atalho: exige Admin da empresa."""
    return cargo_minimo("ADMIN")(view_func)
