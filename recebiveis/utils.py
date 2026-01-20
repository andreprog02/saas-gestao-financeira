# Placeholder para utilitários adicionais, se necessário (ex: validações de vencimento)
def validar_vencimento(vencimento):
    if vencimento < timezone.now().date():
        raise ValueError('Vencimento deve ser futuro.')
    return vencimento