"""
Motor de Score de Crédito Interno.

Calcula um score de 0 a 1000 baseado em 5 fatores ponderados:
1. Histórico de pagamento (pontualidade, contratos quitados)
2. Comprometimento de renda (parcela vs renda)
3. Consulta de crédito (SPC/Serasa)
4. Garantias oferecidas
5. Perfil do cliente (idade, estado civil, tempo de cadastro, patrimônio)

Retorna score + detalhamento de cada fator.
"""
from decimal import Decimal
from datetime import date
from django.utils import timezone
from django.db.models import Sum


def calcular_score(cliente, proposta):
    """
    Calcula o score de crédito do cliente para a proposta.

    Returns:
        dict com score_total, faixa, cor, fatores (lista com detalhamento)
    """
    from core.models import ConfiguracaoScore
    from emprestimos.models import Emprestimo, Parcela, ParcelaStatus
    from emprestimos.services import simular
    from clientes.models import ConsultaCredito

    cfg = ConfiguracaoScore.get_config()
    fatores = []

    # =========================================================================
    # FATOR 1: HISTÓRICO DE PAGAMENTO (0-1000 → ponderado)
    # =========================================================================
    contratos = Emprestimo.objects.filter(cliente=cliente)
    total_contratos = contratos.count()
    quitados = contratos.filter(status="QUITADO").count()
    ativos = contratos.filter(status__in=["ATIVO", "ATRASADO"]).count()
    atrasados = contratos.filter(status="ATRASADO").count()

    parcelas_pagas = Parcela.objects.filter(
        emprestimo__cliente=cliente, status=ParcelaStatus.PAGA
    )
    total_pagas = parcelas_pagas.count()

    pagas_em_dia = 0
    total_dias_atraso = 0
    for p in parcelas_pagas:
        if p.data_pagamento and p.data_pagamento <= p.vencimento:
            pagas_em_dia += 1
        elif p.data_pagamento:
            total_dias_atraso += (p.data_pagamento - p.vencimento).days

    if total_pagas > 0:
        pontualidade = (pagas_em_dia / total_pagas) * 100
        media_atraso = total_dias_atraso / total_pagas
    else:
        pontualidade = 50  # neutro pra cliente novo
        media_atraso = 0

    nota_historico = 0
    if total_contratos == 0:
        # Cliente novo — score neutro
        nota_historico = 500
        detalhe_hist = "Cliente novo, sem histórico"
    else:
        # Pontualidade (0-600)
        nota_historico += min(600, pontualidade * 6)
        # Quitados bonus (0-200)
        nota_historico += min(200, quitados * 100)
        # Penalidade atraso atual (-300)
        nota_historico -= atrasados * 150
        # Penalidade média de atraso
        if media_atraso > 30:
            nota_historico -= 200
        elif media_atraso > 15:
            nota_historico -= 100
        elif media_atraso > 7:
            nota_historico -= 50

        nota_historico = max(0, min(1000, nota_historico))
        detalhe_hist = f"Pontualidade: {pontualidade:.0f}% ({pagas_em_dia}/{total_pagas})"
        if quitados > 0:
            detalhe_hist += f" · {quitados} quitado(s)"
        if atrasados > 0:
            detalhe_hist += f" · {atrasados} em atraso"

    fatores.append({
        "nome": "Histórico de Pagamento",
        "icone": "bi-clock-history",
        "nota": int(nota_historico),
        "peso": cfg.peso_historico,
        "pontos": int(nota_historico * cfg.peso_historico / 100),
        "detalhe": detalhe_hist,
        "cor": _cor_nota(nota_historico),
    })

    # =========================================================================
    # FATOR 2: COMPROMETIMENTO DE RENDA (0-1000)
    # =========================================================================
    renda = cliente.renda_mensal or Decimal("0")
    outros = cliente.outros_rendimentos or Decimal("0")
    renda_total = renda + outros

    # Calcula parcela
    try:
        _, parc_aplicada, _, _, _ = simular(
            valor_emprestado=proposta.valor_solicitado,
            qtd_parcelas=proposta.qtd_parcelas,
            taxa_juros_mensal=proposta.taxa_juros,
            primeiro_vencimento=proposta.primeiro_vencimento,
        )
    except Exception:
        parc_aplicada = Decimal("0")

    if renda_total > 0 and parc_aplicada > 0:
        comprometimento = float(parc_aplicada / renda_total * 100)
        ideal = float(cfg.comprometimento_ideal)
        maximo = float(cfg.comprometimento_maximo)

        if comprometimento <= ideal:
            nota_comprom = 1000
        elif comprometimento <= maximo:
            # Escala linear de 1000 a 300
            nota_comprom = 1000 - ((comprometimento - ideal) / (maximo - ideal)) * 700
        else:
            # Acima do máximo
            nota_comprom = max(0, 300 - (comprometimento - maximo) * 10)

        detalhe_comprom = f"Parcela R$ {parc_aplicada:.2f} / Renda R$ {renda_total:.2f} = {comprometimento:.1f}%"
    else:
        nota_comprom = 200  # sem renda informada é ruim
        comprometimento = 0
        detalhe_comprom = "Renda não informada"

    nota_comprom = max(0, min(1000, nota_comprom))

    fatores.append({
        "nome": "Comprometimento de Renda",
        "icone": "bi-graph-down-arrow",
        "nota": int(nota_comprom),
        "peso": cfg.peso_comprometimento,
        "pontos": int(nota_comprom * cfg.peso_comprometimento / 100),
        "detalhe": detalhe_comprom,
        "cor": _cor_nota(nota_comprom),
    })

    # =========================================================================
    # FATOR 3: CONSULTA DE CRÉDITO (0-1000)
    # =========================================================================
    ultima_consulta = ConsultaCredito.objects.filter(
        cliente=cliente
    ).order_by("-criado_em").first()

    if ultima_consulta:
        if ultima_consulta.status == "NADA_CONSTA":
            nota_consulta = 1000
            detalhe_consulta = "Nada consta nos órgãos de crédito"
        elif ultima_consulta.status == "ALERTA":
            nota_consulta = 500
            total_rest = float(ultima_consulta.total_restricoes or 0)
            detalhe_consulta = f"Alerta — valor: R$ {total_rest:.2f}"
        else:
            # Com restrições — penalidade proporcional ao valor
            total_rest = float(ultima_consulta.total_restricoes or 0)
            qtd_rest = ultima_consulta.restricoes.count()
            nota_consulta = 200
            if total_rest > 10000:
                nota_consulta = 50
            elif total_rest > 5000:
                nota_consulta = 100
            elif total_rest > 1000:
                nota_consulta = 150
            # Mais credores = pior
            nota_consulta -= min(100, qtd_rest * 20)
            nota_consulta = max(0, nota_consulta)
            detalhe_consulta = f"{qtd_rest} restrição(ões) — Total: R$ {total_rest:.2f}"
    else:
        nota_consulta = 400  # sem consulta é risco
        detalhe_consulta = "Nenhuma consulta realizada"

    fatores.append({
        "nome": "Consulta de Crédito",
        "icone": "bi-search",
        "nota": int(nota_consulta),
        "peso": cfg.peso_consulta_credito,
        "pontos": int(nota_consulta * cfg.peso_consulta_credito / 100),
        "detalhe": detalhe_consulta,
        "cor": _cor_nota(nota_consulta),
    })

    # =========================================================================
    # FATOR 4: GARANTIAS (0-1000)
    # =========================================================================
    garantias = proposta.garantias.all()
    nota_garantia = 0

    tem_cheque = garantias.filter(tipo="CHEQUE").exists()
    tem_avalista = garantias.filter(tipo="AVALISTA").exists()
    tem_movel = garantias.filter(tipo="BEM_MOVEL").exists()
    tem_imovel = garantias.filter(tipo="BEM_IMOVEL").exists()

    if tem_imovel:
        nota_garantia += 500
    if tem_movel:
        nota_garantia += 250
    if tem_avalista:
        nota_garantia += 200
    if tem_cheque:
        nota_garantia += 100

    if not garantias.exists():
        nota_garantia = 100  # sem garantia
        detalhe_garantia = "Sem garantias"
    else:
        partes = []
        if tem_imovel:
            partes.append("Imóvel")
        if tem_movel:
            partes.append("Veículo")
        if tem_avalista:
            partes.append("Avalista")
        if tem_cheque:
            partes.append("Cheque")
        detalhe_garantia = "Garantias: " + ", ".join(partes)

    nota_garantia = min(1000, nota_garantia)

    fatores.append({
        "nome": "Garantias",
        "icone": "bi-shield-check",
        "nota": int(nota_garantia),
        "peso": cfg.peso_garantias,
        "pontos": int(nota_garantia * cfg.peso_garantias / 100),
        "detalhe": detalhe_garantia,
        "cor": _cor_nota(nota_garantia),
    })

    # =========================================================================
    # FATOR 5: PERFIL DO CLIENTE (0-1000)
    # =========================================================================
    nota_perfil = 500  # base neutra

    # Idade
    if cliente.data_nascimento:
        hoje = date.today()
        idade = hoje.year - cliente.data_nascimento.year
        if cfg.idade_minima_ideal <= idade <= cfg.idade_maxima_ideal:
            nota_perfil += 150
        elif idade < 21:
            nota_perfil -= 100
        elif idade > 70:
            nota_perfil -= 50

    # Estado civil
    ec = cliente.estado_civil
    if ec in ("CASADO", "UNIAO_ESTAVEL"):
        nota_perfil += 80
    elif ec == "SOLTEIRO":
        nota_perfil += 0  # neutro

    # Patrimônio
    qtd_moveis = cliente.bens_moveis.count()
    qtd_imoveis = cliente.bens_imoveis.count()
    nota_perfil += min(150, qtd_imoveis * 100 + qtd_moveis * 30)

    # Tempo como cliente
    dias_cadastro = (timezone.now() - cliente.data_cadastro).days if hasattr(cliente, 'data_cadastro') and cliente.data_cadastro else 0
    if dias_cadastro > 365:
        nota_perfil += 100
    elif dias_cadastro > 180:
        nota_perfil += 50

    nota_perfil = max(0, min(1000, nota_perfil))

    partes_perfil = []
    if cliente.data_nascimento:
        partes_perfil.append(f"Idade: {idade}")
    if ec:
        partes_perfil.append(f"{cliente.get_estado_civil_display()}")
    if qtd_moveis + qtd_imoveis > 0:
        partes_perfil.append(f"{qtd_moveis} veículo(s), {qtd_imoveis} imóvel(is)")
    detalhe_perfil = " · ".join(partes_perfil) if partes_perfil else "Dados incompletos"

    fatores.append({
        "nome": "Perfil do Cliente",
        "icone": "bi-person",
        "nota": int(nota_perfil),
        "peso": cfg.peso_perfil,
        "pontos": int(nota_perfil * cfg.peso_perfil / 100),
        "detalhe": detalhe_perfil,
        "cor": _cor_nota(nota_perfil),
    })

    # =========================================================================
    # SCORE FINAL
    # =========================================================================
    score_total = sum(f["pontos"] for f in fatores)
    score_total = max(0, min(1000, score_total))

    faixa, cor = _faixa_score(score_total, cfg)

    return {
        "score": score_total,
        "faixa": faixa,
        "cor": cor,
        "fatores": fatores,
        "score_minimo": cfg.score_minimo_aprovacao,
        "score_atencao": cfg.score_atencao,
        "aprovado_auto": score_total >= cfg.score_atencao,
        "reprovado_auto": score_total < cfg.score_minimo_aprovacao,
    }


def _cor_nota(nota):
    if nota >= 700:
        return "success"
    elif nota >= 500:
        return "info"
    elif nota >= 300:
        return "warning"
    return "danger"


def _faixa_score(score, cfg):
    if score >= 850:
        return "Excelente", "success"
    elif score >= 700:
        return "Bom", "success"
    elif score >= cfg.score_atencao:
        return "Moderado", "info"
    elif score >= cfg.score_minimo_aprovacao:
        return "Atenção", "warning"
    else:
        return "Alto Risco", "danger"
