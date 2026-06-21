"""
Motor de conciliação bancária.

Estratégias de matching (em ordem de confiança):
1. Match EXATO: valor igual + data igual → concilia automaticamente
2. Match PRÓXIMO: valor igual + data difere em até 3 dias → sugere
3. Sem match: fica pendente para conciliação manual
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from financeiro.models import Transacao
from .models import LancamentoExtrato


def conciliar_automatico(extrato):
    """
    Percorre todos os lançamentos PENDENTES de um extrato e tenta
    encontrar transações correspondentes no sistema.

    Retorna dict com contadores: {exatos, sugeridos, pendentes}
    """
    lancamentos = extrato.lancamentos.filter(status="PENDENTE")
    contadores = {"exatos": 0, "sugeridos": 0, "pendentes": 0}

    for lanc in lancamentos:
        resultado = _encontrar_match(lanc)

        if resultado["tipo"] == "exato":
            lanc.transacao = resultado["transacao"]
            lanc.status = "CONCILIADO"
            lanc.conciliado_em = timezone.now()
            lanc.save()
            contadores["exatos"] += 1

        elif resultado["tipo"] == "proximo":
            # Não concilia automaticamente, mas deixa o vínculo sugerido
            lanc.transacao = resultado["transacao"]
            lanc.save()
            contadores["sugeridos"] += 1

        else:
            contadores["pendentes"] += 1

    extrato.atualizar_contadores()
    return contadores


def _encontrar_match(lancamento):
    """
    Busca uma transação do sistema que corresponda ao lançamento do extrato.
    
    Critérios:
    - O valor absoluto deve bater
    - A transação não pode já estar conciliada com outro lançamento
    """
    valor_busca = lancamento.valor
    data_lanc = lancamento.data

    # Transações que ainda não foram conciliadas
    transacoes_livres = Transacao.objects.filter(
        lancamentos_extrato__isnull=True
    )

    # 1. Match EXATO: mesmo valor e mesma data
    match_exato = transacoes_livres.filter(
        valor=valor_busca,
        data__date=data_lanc,
    ).first()

    if match_exato:
        return {"tipo": "exato", "transacao": match_exato}

    # Tenta com valor absoluto (transações de saída são negativas no sistema)
    if valor_busca < 0:
        # Lançamento de débito — busca transação negativa no sistema
        match_exato = transacoes_livres.filter(
            valor=valor_busca,
            data__date=data_lanc,
        ).first()
        if not match_exato:
            match_exato = transacoes_livres.filter(
                valor=-valor_busca,
                data__date=data_lanc,
            ).first()
    else:
        # Lançamento de crédito — busca transação positiva
        match_exato = transacoes_livres.filter(
            valor=valor_busca,
            data__date=data_lanc,
        ).first()

    if match_exato:
        return {"tipo": "exato", "transacao": match_exato}

    # 2. Match PRÓXIMO: mesmo valor, data ±3 dias
    data_inicio = data_lanc - timedelta(days=3)
    data_fim = data_lanc + timedelta(days=3)

    match_proximo = transacoes_livres.filter(
        data__date__gte=data_inicio,
        data__date__lte=data_fim,
    ).filter(
        Q(valor=valor_busca) | Q(valor=-valor_busca)
    ).first()

    if match_proximo:
        return {"tipo": "proximo", "transacao": match_proximo}

    # 3. Sem match
    return {"tipo": "nenhum", "transacao": None}


def sugestoes_para_lancamento(lancamento):
    """
    Retorna lista de transações candidatas para conciliação manual.
    Ordena por proximidade de data e valor.
    """
    valor_abs = abs(lancamento.valor)
    data_lanc = lancamento.data

    # Busca transações livres com valor próximo (±20%) e data próxima (±15 dias)
    margem_valor = valor_abs * Decimal("0.20")
    data_inicio = data_lanc - timedelta(days=15)
    data_fim = data_lanc + timedelta(days=15)

    candidatas = Transacao.objects.filter(
        lancamentos_extrato__isnull=True,
        data__date__gte=data_inicio,
        data__date__lte=data_fim,
    ).filter(
        Q(valor__gte=valor_abs - margem_valor, valor__lte=valor_abs + margem_valor) |
        Q(valor__gte=-(valor_abs + margem_valor), valor__lte=-(valor_abs - margem_valor))
    ).order_by("data")[:10]

    return candidatas
