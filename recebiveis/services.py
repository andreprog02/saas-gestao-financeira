from decimal import Decimal
from django.utils import timezone
from financeiro.models import Transacao

def registrar_financeiro(contrato):
    """
    Registra a saída de caixa referente à liberação do adiantamento.
    Usa o modelo Transacao para impactar o saldo do Dashboard/Fluxo de Caixa.
    """
    # Valor negativo pois é uma saída de dinheiro (pagamento ao cliente)
    valor_saida = -abs(contrato.valor_liquido)
    
    # Define a data do lançamento (usa agora se não tiver data de ativação)
    data_lancamento = contrato.data_ativacao or timezone.now()
    
    Transacao.objects.create(
        data=data_lancamento,
        descricao=f'Pagamento Adiantamento {contrato.contrato_id} - {contrato.cliente.nome_completo}',
        valor=valor_saida,
        tipo='ANTECIPAÇÃO DE RECEBÍVEIS',  # Usamos 'SAQUE' para representar a saída de recursos
        transacao_original=None
    )

def registrar_financeiro_ajuste(contrato):
    """
    Registra ajustes financeiros de renegociação, se necessário.
    """
    # Mantido vazio para uso futuro, evitando erros de importação
    pass