def get_client_ip(request):
    """
    Recupera o IP do cliente que fez a requisição.
    Tenta pegar pelo cabeçalho HTTP_X_FORWARDED_FOR (caso use proxy/nginx)
    ou pega diretamente pelo REMOTE_ADDR.
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip