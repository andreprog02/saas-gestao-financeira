from django.db.models import Sum, Avg, Count, Q
from django.utils import timezone
from .models import Emprestimo, Parcela, EmprestimoStatus, ParcelaStatus

def gerar_dossie_cliente(cliente):
    """
    Gera um relatório completo do histórico financeiro do cliente para análise de crédito.
    """
    hoje = timezone.localdate()
    
    # 1. Contratos
    todos_contratos = Emprestimo.objects.filter(cliente=cliente)
    qtd_contratos = todos_contratos.count()
    contratos_ativos = todos_contratos.filter(status__in=[EmprestimoStatus.ATIVO, EmprestimoStatus.ATRASADO]).count()
    contratos_quitados = todos_contratos.filter(status=EmprestimoStatus.QUITADO).count()
    
    # 2. Valores
    total_emprestado = todos_contratos.aggregate(soma=Sum('valor_emprestado'))['soma'] or 0
    taxa_media = todos_contratos.aggregate(media=Avg('taxa_juros_mensal'))['media'] or 0
    
    # 3. Comportamento de Pagamento (O mais importante)
    todas_parcelas = Parcela.objects.filter(emprestimo__cliente=cliente)
    
    # Parcelas Pagas
    pagas = todas_parcelas.filter(status__in=[ParcelaStatus.PAGA, ParcelaStatus.LIQUIDADA_RENEGOCIACAO])
    qtd_pagas = pagas.count()
    
    # Pagas com Atraso
    pagas_com_atraso = 0
    dias_atraso_acumulado = 0
    
    for p in pagas:
        if p.data_pagamento and p.data_pagamento > p.vencimento:
            pagas_com_atraso += 1
            dias = (p.data_pagamento - p.vencimento).days
            dias_atraso_acumulado += dias

    media_atraso = (dias_atraso_acumulado / pagas_com_atraso) if pagas_com_atraso > 0 else 0
    
    # Em Atraso Hoje (Crítico)
    em_aberto_vencidas = todas_parcelas.filter(
        status=ParcelaStatus.ABERTA, 
        vencimento__lt=hoje
    )
    valor_em_atraso = sum([p.valor_atual for p in em_aberto_vencidas])
    
    # Classificação Simples (Sugestão)
    score_desc = "Neutro"
    cor_score = "secondary"
    
    if qtd_contratos > 0:
        if valor_em_atraso > 0:
            score_desc = "Risco Alto (Inadimplente)"
            cor_score = "danger"
        elif media_atraso > 5:
            score_desc = "Risco Médio (Atrasos Frequentes)"
            cor_score = "warning"
        else:
            score_desc = "Bom Pagador"
            cor_score = "success"

    return {
        'resumo': {
            'cliente_desde': cliente.criado_em, # Assumindo que tem esse campo, se não tiver, use o primeiro contrato
            'qtd_total': qtd_contratos,
            'ativos': contratos_ativos,
            'quitados': contratos_quitados,
            'total_movimentado': total_emprestado,
            'taxa_media_historica': taxa_media,
        },
        'pagamentos': {
            'total_pagas': qtd_pagas,
            'pagas_atraso': pagas_com_atraso,
            'media_dias_atraso': round(media_atraso, 1),
            'percentual_pontualidade': round(((qtd_pagas - pagas_com_atraso)/qtd_pagas * 100), 1) if qtd_pagas > 0 else 0
        },
        'risco': {
            'atrasado_hoje_qtd': em_aberto_vencidas.count(),
            'atrasado_hoje_valor': valor_em_atraso,
            'score_texto': score_desc,
            'score_cor': cor_score
        }
    }