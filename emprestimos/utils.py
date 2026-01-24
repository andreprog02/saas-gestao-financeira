from django.db.models import Max
from django.utils import timezone
from .models import Emprestimo


def gerar_codigo_contrato(prefixo="CTR") -> str:
    """
    Gera códigos baseados no prefixo.
    Ex Padrão: CTR-2026-000001
    Ex Reneg:  RNG-EMP-2026-000001
    """
    ano = timezone.localdate().year
    
    # Define a base do código para busca (ex: "CTR-2026-" ou "RNG-EMP-2026-")
    # Ajustando formato para garantir separação limpa com hífen
    prefixo_formatado = f"{prefixo}-{ano}-".replace(" ", "-") 
    
    # Busca o último contrato que começa com esse prefixo específico
    ultimo = Emprestimo.objects.filter(codigo_contrato__startswith=prefixo_formatado).aggregate(m=Max("codigo_contrato"))["m"]

    if not ultimo:
        seq = 1
    else:
        # Pega a parte final (número) após o último hífen
        try:
            seq = int(ultimo.split("-")[-1]) + 1
        except ValueError:
            seq = 1

    return f"{prefixo_formatado}{seq:06d}"