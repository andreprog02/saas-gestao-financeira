from django.db.models import Sum, Avg, Count, Q
from django.utils import timezone
from .models import Emprestimo, Parcela, EmprestimoStatus, ParcelaStatus

def gerar_dossie_cliente(cliente):
    """
    Gera um relatório completo do histórico financeiro do cliente para análise de crédito.
    Otimizado para manter perfil neutro até o primeiro pagamento em dia e exibir média geral de atraso.
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
    
    # 3. Comportamento de Pagamento
    todas_parcelas = Parcela.objects.filter(emprestimo__cliente=cliente)
    
    # Parcelas Pagas (Inclui renegociações liquidadas)
    pagas = todas_parcelas.filter(status__in=[ParcelaStatus.PAGA, ParcelaStatus.LIQUIDADA_RENEGOCIACAO])
    qtd_pagas = pagas.count()
    
    # Análise de Atrasos
    pagas_com_atraso = 0
    dias_atraso_acumulado = 0
    
    for p in pagas:
        # Verifica se houve pagamento e se foi posterior ao vencimento
        if p.data_pagamento and p.vencimento and p.data_pagamento > p.vencimento:
            pagas_com_atraso += 1
            dias = (p.data_pagamento - p.vencimento).days
            dias_atraso_acumulado += dias

    # Média de atraso ponderada sobre TODAS as parcelas pagas.
    # Se o cliente paga em dia, conta como 0 na média, melhorando o score.
    media_atraso_geral = (dias_atraso_acumulado / qtd_pagas) if qtd_pagas > 0 else 0
    
    # Quantidade de parcelas pagas rigorosamente em dia
    qtd_pagas_em_dia = qtd_pagas - pagas_com_atraso
    
    # Em Atraso Hoje (Inadimplência Atual - Fator Crítico)
    em_aberto_vencidas = todas_parcelas.filter(
        status=ParcelaStatus.ABERTA, 
        vencimento__lt=hoje
    )
    valor_em_atraso = sum([p.valor_atual for p in em_aberto_vencidas])
    
    # 4. Classificação de Risco (Score)
    score_desc = "Neutro"
    cor_score = "secondary"
    
    # Lógica hierárquica de decisão
    if valor_em_atraso > 0:
        # Prioridade 1: Se deve hoje, é risco alto
        score_desc = "Risco Alto (Inadimplente)"
        cor_score = "danger"
    elif qtd_pagas_em_dia == 0:
        # Prioridade 2: Se nunca pagou uma em dia, mantém Neutro (mesmo se pagou algumas com atraso e hoje está ok)
        # Isso atende ao requisito: "perfil seja neutro até o cliente pagar a primeira prestação em dia"
        score_desc = "Neutro (Sem histórico pontual)"
        cor_score = "secondary"
    elif media_atraso_geral > 5:
        # Prioridade 3: Paga, mas com atrasos frequentes na média
        score_desc = f"Risco Médio (Média {round(media_atraso_geral, 1)} dias atraso)"
        cor_score = "warning"
    else:
        # Prioridade 4: Bom histórico
        score_desc = "Bom Pagador"
        cor_score = "success"

    return {
        'resumo': {
            'cliente_desde': cliente.criado_em,
            'qtd_total': qtd_contratos,
            'ativos': contratos_ativos,
            'quitados': contratos_quitados,
            'total_movimentado': total_emprestado,
            'taxa_media_historica': taxa_media,
        },
        'pagamentos': {
            'total_pagas': qtd_pagas,
            'pagas_em_dia': qtd_pagas_em_dia,
            'pagas_atraso': pagas_com_atraso,
            'media_dias_atraso': round(media_atraso_geral, 1), # Exibe a média geral solicitada
            'percentual_pontualidade': round((qtd_pagas_em_dia/qtd_pagas * 100), 1) if qtd_pagas > 0 else 0
        },
        'risco': {
            'atrasado_hoje_qtd': em_aberto_vencidas.count(),
            'atrasado_hoje_valor': valor_em_atraso,
            'score_texto': score_desc,
            'score_cor': cor_score
        }
    }