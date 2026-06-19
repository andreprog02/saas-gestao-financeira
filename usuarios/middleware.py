from django.shortcuts import redirect


class EmpresaMiddleware:
    """
    Injeta request.empresa com base no usuário logado.
    
    Toda view pode usar request.empresa para filtrar dados.
    Se o usuário não tem empresa vinculada, redireciona para uma
    página de configuração (exceto admin e login).
    """

    URLS_LIVRES = [
        "/admin/",
        "/usuarios/login/",
        "/usuarios/logout/",
        "/usuarios/sem-empresa/",
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.empresa = None

        if request.user.is_authenticated:
            request.empresa = getattr(request.user, "empresa", None)

            # Se o usuário não tem empresa e não está em URL livre
            if not request.empresa and not request.user.is_superuser:
                if not any(request.path.startswith(url) for url in self.URLS_LIVRES):
                    return redirect("usuarios:sem_empresa")

        return self.get_response(request)
