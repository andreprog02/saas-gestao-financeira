# Placeholder para utilitários adicionais, se necessário (ex: validações de vencimento)
def validar_vencimento(vencimento):
    if vencimento < timezone.now().date():
        raise ValueError('Vencimento deve ser futuro.')
    return vencimento

def gerar_id_recebivel(prefixo="REC"):
    """
    Gera ID para recebíveis.
    Padrão: REC001, REC002...
    Renegociação: RNG-ADT-001...
    """
    # Importação dentro da função para evitar Circular Import com models.py
    from .models import ContratoRecebivel
    
    # Filtra contratos que começam com o prefixo
    ultimo = ContratoRecebivel.objects.filter(contrato_id__startswith=prefixo).aggregate(m=Max("contrato_id"))["m"]
    
    if not ultimo:
        seq = 1
    else:
        # Tenta extrair a parte numérica do final da string
        # Ex: REC005 -> 5
        # Ex: RNG-ADT-005 -> 5
        try:
            # Pega apenas os dígitos do final da string
            import re
            match = re.search(r'(\d+)$', ultimo)
            if match:
                seq = int(match.group(1)) + 1
            else:
                seq = 1
        except:
            seq = 1
            
    # Formatação
    if prefixo == "REC":
        return f"REC{seq:03d}"
    else:
        # Formato para renegociação: RNG-ADT-001
        return f"{prefixo}-{seq:03d}"