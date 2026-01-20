from decimal import Decimal
from financeiro.models import LancamentoFinanceiro  # Assuma modelo em financeiro; ajuste se necessário

def registrar_financeiro(contrato):
    """Registra liberação e recebíveis no livro caixa."""
    # Exemplo: Débito para liberação líquido
    LancamentoFinanceiro.objects.create(
        data=contrato.data_ativacao,
        descricao=f'Liberação Adiantamento {contrato.contrato_id} - Cliente {contrato.cliente.nome}',
        debito=contrato.valor_liquido,
        credito=Decimal('0.00')
    )
    # Crédito para recebíveis bruto
    LancamentoFinanceiro.objects.create(
        data=contrato.data_ativacao,
        descricao=f'Recebíveis Cedidos {contrato.contrato_id}',
        debito=Decimal('0.00'),
        credito=contrato.valor_bruto
    )

def registrar_financeiro_ajuste(contrato):
    """Registra ajustes de renegociação."""
    ajuste_desconto = contrato.valor_bruto * contrato.taxa_desconto
    LancamentoFinanceiro.objects.create(
        data=timezone.now(),
        descricao=f'Ajuste Renegociação {contrato.contrato_id} - Desconto',
        debito=Decimal('0.00'),
        credito=ajuste_desconto
    )