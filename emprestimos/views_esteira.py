"""
Views da Esteira de Aprovação — Workflow Multi-Etapa.

Fluxo: Captação → Documentação → Análise de Crédito → Comitê → Formalização → Liberação
"""
from decimal import Decimal

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.utils import timezone
from django.http import JsonResponse
from django.db.models import Q, Case, When, Value, IntegerField

from .models import (
    PropostaEmprestimo, EtapaProposta, ChecklistItem,
    PoliticaCredito, Emprestimo, Parcela, EmprestimoStatus,
    ParcelaStatus, ContratoLog, GarantiaProposta
)
from .services import simular
from .services_analise import gerar_dossie_cliente
from clientes.models import Cliente, BemMovel, BemImovel
from financeiro.models import Transacao
from contas.models import ContaCorrente, MovimentacaoConta
from usuarios.decorators import cargo_minimo


# ==============================================================================
# CHECKLIST PADRÃO POR ETAPA
# ==============================================================================

CHECKLIST_PADRAO = {
    "CAPTACAO": [
        ("Dados cadastrais completos", True),
        ("Telefone de contato válido", True),
        ("Endereço atualizado", True),
    ],
    "DOCUMENTACAO": [
        ("RG ou CNH (cópia)", True),
        ("CPF (cópia)", True),
        ("Comprovante de residência (últimos 90 dias)", True),
        ("Comprovante de renda", True),
        ("Referências pessoais (2)", False),
    ],
    "ANALISE_CREDITO": [
        ("Consulta SPC/Serasa realizada", True),
        ("Dossiê do cliente revisado", True),
        ("Capacidade de pagamento validada", True),
        ("Histórico de contratos anteriores verificado", False),
    ],
    "COMITE": [],
     "FORMALIZACAO": [
        ("Contrato assinado pelo cliente", True),
        ("Nota promissória assinada", True),
        ("Garantias cadastradas", True),
        ("Contrato assinado pela empresa", False),
    ],
      "LIBERACAO": [
        ("Valor conferido", True),
        ("Dados bancários confirmados", True),
        ("Liberação autorizada pelo gerente", True),
        ("Checklist de formalização revisado", True),
    ],
}


def _criar_checklist_para_etapa(etapa_obj):
    """Cria os itens de checklist padrão para uma etapa."""
    itens = CHECKLIST_PADRAO.get(etapa_obj.etapa, [])
    for descricao, obrigatorio in itens:
        ChecklistItem.objects.create(
            etapa_proposta=etapa_obj,
            descricao=descricao,
            obrigatorio=obrigatorio,
        )


def _proxima_etapa(etapa_atual_str, proposta):
    """
    Determina qual é a próxima etapa com base na atual.
    Se o valor for alto, não pula o COMITE.
    """
    ordem = ["CAPTACAO", "DOCUMENTACAO", "ANALISE_CREDITO", "COMITE", "FORMALIZACAO", "LIBERACAO"]

    idx = ordem.index(etapa_atual_str) if etapa_atual_str in ordem else -1
    if idx < 0 or idx >= len(ordem) - 1:
        return None  # Já é a última

    proxima = ordem[idx + 1]

    # Pular COMITE se valor estiver dentro da alçada
    if proxima == "COMITE":
        politica = PoliticaCredito.objects.filter(ativo=True).first()
        if politica and proposta.valor_solicitado <= politica.valor_max_sem_comite:
            proxima = "FORMALIZACAO"

    return proxima


def _etapa_anterior(etapa_atual_str):
    """Retorna a etapa anterior."""
    ordem = ["CAPTACAO", "DOCUMENTACAO", "ANALISE_CREDITO", "COMITE", "FORMALIZACAO", "LIBERACAO"]
    idx = ordem.index(etapa_atual_str) if etapa_atual_str in ordem else -1
    if idx <= 0:
        return None
    return ordem[idx - 1]


# ==============================================================================
# 1. PAINEL DA ESTEIRA (Kanban-style)
# ==============================================================================

@login_required
def painel_esteira(request):
    """Visão geral de todas as propostas organizadas por etapa."""
    etapas_nomes = [
        ("CAPTACAO", "Captação"),
        ("DOCUMENTACAO", "Documentação"),
        ("ANALISE_CREDITO", "Análise de Crédito"),
        ("COMITE", "Comitê"),
        ("FORMALIZACAO", "Formalização"),
        ("LIBERACAO", "Liberação"),
    ]

    colunas = []
    for codigo, nome in etapas_nomes:
        propostas = PropostaEmprestimo.objects.filter(
            status=codigo
        ).select_related("cliente").order_by("-data_solicitacao")

        colunas.append({
            "codigo": codigo,
            "nome": nome,
            "propostas": propostas,
            "total": propostas.count(),
        })

    # Propostas finalizadas (últimas 10)
    finalizadas = PropostaEmprestimo.objects.filter(
        status__in=["APROVADO", "NEGADO", "CANCELADO"]
    ).select_related("cliente").order_by("-data_solicitacao")[:10]

    return render(request, "emprestimos/esteira/painel.html", {
        "colunas": colunas,
        "finalizadas": finalizadas,
    })


# ==============================================================================
# 2. CRIAR PROPOSTA (entrada na esteira)
# ==============================================================================

@login_required
def nova_proposta(request):
    """Operador cria proposta — entra na etapa CAPTAÇÃO."""
    if request.method == "POST":
        try:
            from .views import to_decimal

            cliente_id = request.POST.get("cliente_id")
            valor = to_decimal(request.POST.get("valor"))
            taxa = Decimal(request.POST.get("taxa", "0").replace(",", "."))
            qtd = int(request.POST.get("qtd_parcelas", 1))
            vencimento = request.POST.get("vencimento")
            obs = request.POST.get("observacoes", "")

            # Multa e mora
            tem_multa = request.POST.get("tem_multa") == "on"
            multa_pct = Decimal(request.POST.get("multa_percent", "2.00").replace(",", "."))
            tem_mora = request.POST.get("tem_juros_mora") == "on"
            mora_pct = Decimal(request.POST.get("juros_mora_percent", "2.00").replace(",", "."))

            # Finalidade e IOF
            finalidade = request.POST.get("finalidade", "CREDITO_PESSOAL")
            tem_iof = request.POST.get("tem_iof") == "on"

            # Cálculo IOF
            valor_iof = Decimal("0.00")
            valor_bruto = valor
            if tem_iof:
                iof_diario = Decimal("0.0082")  # 0,0082% a.d.
                iof_adicional = Decimal("0.38")  # 0,38%
                dias_contrato = qtd * 30  # aprox
                if dias_contrato > 365:
                    dias_contrato = 365
                iof_calc = valor * (iof_diario / Decimal("100")) * Decimal(str(dias_contrato))
                iof_adic = valor * (iof_adicional / Decimal("100"))
                valor_iof = (iof_calc + iof_adic).quantize(Decimal("0.01"))
                valor_bruto = valor + valor_iof

            # Débitos extras
            debitos_str = request.POST.get("valor_debitos_extras", "0")
            debitos_limpo = debitos_str.replace("R$", "").replace(" ", "").strip()
            if "," in debitos_limpo and "." in debitos_limpo:
                debitos_limpo = debitos_limpo.replace(".", "").replace(",", ".")
            elif "," in debitos_limpo:
                debitos_limpo = debitos_limpo.replace(",", ".")
            valor_debitos = Decimal(debitos_limpo or "0").quantize(Decimal("0.01"))
            desc_debitos = request.POST.get("descricao_debitos", "").strip()
            valor_bruto += valor_debitos

            if valor <= 0:
                raise ValueError("Valor deve ser maior que zero.")

            # Validar contra política de crédito
            politica = PoliticaCredito.objects.filter(ativo=True).first()
            if politica:
                if valor < politica.valor_minimo:
                    raise ValueError(f"Valor mínimo: R$ {politica.valor_minimo}")
                if valor > politica.valor_maximo:
                    raise ValueError(f"Valor máximo: R$ {politica.valor_maximo}")
                if qtd > politica.prazo_maximo_meses:
                    raise ValueError(f"Prazo máximo: {politica.prazo_maximo_meses} meses")
                if taxa < politica.taxa_minima or taxa > politica.taxa_maxima:
                    raise ValueError(f"Taxa deve estar entre {politica.taxa_minima}% e {politica.taxa_maxima}%")

            with transaction.atomic():
                proposta = PropostaEmprestimo.objects.create(
                    cliente_id=cliente_id,
                    valor_solicitado=valor,
                    qtd_parcelas=qtd,
                    taxa_juros=taxa,
                    primeiro_vencimento=vencimento,
                    usuario_solicitante=request.user,
                    observacoes=obs,
                    status="CAPTACAO",
                    tem_multa=tem_multa,
                    multa_percent=multa_pct if tem_multa else Decimal("0"),
                    tem_juros_mora=tem_mora,
                    juros_mora_percent=mora_pct if tem_mora else Decimal("0"),
                    finalidade=finalidade,
                    tem_iof=tem_iof,
                    valor_iof=valor_iof,
                    valor_debitos_extras=valor_debitos,
                    descricao_debitos=desc_debitos,
                    valor_bruto=valor_bruto,
                )

                # Garantias
                # Cheques
                tem_cheque = request.POST.get("tem_cheque", "nao")
                if tem_cheque == "sim":
                    GarantiaProposta.objects.create(
                        proposta=proposta,
                        tipo="CHEQUE",
                    )

                # Avalistas
                avalista_ids = request.POST.getlist("avalista_ids")
                for av_id in avalista_ids:
                    if av_id:
                        GarantiaProposta.objects.create(
                            proposta=proposta, tipo="AVALISTA", avalista_id=int(av_id),
                        )

                # Bens móveis
                movel_ids = request.POST.getlist("bem_movel_ids")
                for bm_id in movel_ids:
                    if bm_id:
                        GarantiaProposta.objects.create(
                            proposta=proposta, tipo="BEM_MOVEL", bem_movel_id=int(bm_id),
                        )

                # Bens imóveis
                imovel_ids = request.POST.getlist("bem_imovel_ids")
                for bi_id in imovel_ids:
                    if bi_id:
                        GarantiaProposta.objects.create(
                            proposta=proposta, tipo="BEM_IMOVEL", bem_imovel_id=int(bi_id),
                        )

                # Cria primeira etapa
                etapa = EtapaProposta.objects.create(
                    proposta=proposta,
                    etapa="CAPTACAO",
                    responsavel=request.user,
                )
                _criar_checklist_para_etapa(etapa)

            messages.success(request, f"Proposta #{proposta.id} criada na esteira.")
            return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)

        except ValueError as e:
            messages.error(request, str(e))
        except Exception as e:
            messages.error(request, f"Erro: {e}")

    clientes = Cliente.objects.all().order_by("nome_completo")
    politica = PoliticaCredito.objects.filter(ativo=True).first()
    return render(request, "emprestimos/esteira/nova_proposta.html", {
        "clientes": clientes,
        "politica": politica,
    })


# ==============================================================================
# 3. DETALHE DA PROPOSTA (tela principal da etapa)
# ==============================================================================

@login_required
def detalhe_proposta(request, proposta_id):
    """Tela de trabalho da proposta — mostra etapa atual, checklist, timeline."""
    proposta = get_object_or_404(
        PropostaEmprestimo.objects.select_related("cliente"),
        id=proposta_id
    )

    etapa_ativa = proposta.etapa_atual_obj
    todas_etapas = proposta.etapas.all().order_by("criado_em")
    checklist = etapa_ativa.checklist.all() if etapa_ativa else []

    # Dossiê do cliente
    try:
        dossie = gerar_dossie_cliente(proposta.cliente)
    except Exception:
        dossie = None

    # Parceiros para comissão
    parceiros = Cliente.objects.all().order_by("nome_completo")

    # Verifica se o usuário tem cargo suficiente para a etapa
    pode_atuar = False
    if etapa_ativa:
        from usuarios.decorators import cargo_minimo as _  # just for HIERARQUIA check
        HIERARQUIA = {"OPERACIONAL": 1, "CAIXA": 1, "SUPERVISOR": 2, "GERENTE": 3, "DIRETOR": 4}
        nivel_user = HIERARQUIA.get(request.user.cargo, 0)
        nivel_req = HIERARQUIA.get(etapa_ativa.cargo_minimo, 0)
        pode_atuar = nivel_user >= nivel_req

    # Mapa de progresso
    TODAS_ETAPAS = ["CAPTACAO", "DOCUMENTACAO", "ANALISE_CREDITO", "COMITE", "FORMALIZACAO", "LIBERACAO"]
    progresso = []
    for e in TODAS_ETAPAS:
        etapa_obj = todas_etapas.filter(etapa=e).last()
        status_class = "secondary"
        if etapa_obj:
            if etapa_obj.ativa:
                status_class = "primary"
            elif etapa_obj.resultado == "APROVADO":
                status_class = "success"
            elif etapa_obj.resultado == "NEGADO":
                status_class = "danger"
            elif etapa_obj.resultado == "DEVOLVIDO":
                status_class = "warning"
        progresso.append({
            "codigo": e,
            "nome": dict(EtapaProposta.Etapa.choices).get(e, e),
            "obj": etapa_obj,
            "status_class": status_class,
        })

    # Documentos do cliente
    from clientes.models import DocumentoCliente
    documentos = proposta.cliente.documentos.all()
    docs_dict = proposta.cliente.documentos_dict
    todos_tipos = DocumentoCliente.TIPO_CHOICES

    # Monta lista de documentos com status
    docs_status = []
    for tipo_cod, tipo_nome in todos_tipos:
        doc = docs_dict.get(tipo_cod)
        docs_status.append({
            "tipo": tipo_cod,
            "nome": tipo_nome,
            "doc": doc,
            "presente": doc is not None,
            "vencido": doc.vencido if doc else False,
            "status": doc.status_texto if doc else "Ausente",
        })

    # Observações automáticas para o comitê
    pendencias_docs = []
    for ds in docs_status:
        if not ds["presente"]:
            pendencias_docs.append(f"{ds['nome']}: pendente")
        elif ds["vencido"]:
            pendencias_docs.append(f"{ds['nome']}: desatualizado")

    # Votos do comitê (se estiver na etapa COMITE)
    from .models import VotoComite, ContratoFormalizado
    votos = proposta.votos_comite.select_related("usuario").all()
    ja_votou = votos.filter(usuario=request.user).exists()
    is_comite = etapa_ativa and etapa_ativa.etapa == "COMITE"

    # Dados completos do cliente para o comitê
    cliente_completo = None
    contratos_abertos = []
    historico_pagamentos = []
    if is_comite:
        cli = proposta.cliente
        cliente_completo = cli

        # Contratos em aberto
        contratos_abertos = Emprestimo.objects.filter(
            cliente=cli, status__in=["ATIVO", "ATRASADO"]
        ).prefetch_related("parcelas").order_by("-primeiro_vencimento")

        # Histórico de pagamentos (últimas 30 parcelas pagas)
        historico_pagamentos = Parcela.objects.filter(
            emprestimo__cliente=cli,
            status="PAGA",
        ).select_related("emprestimo").order_by("-data_pagamento")[:30]

    # Contrato formalizado (se estiver na FORMALIZACAO)
    from .models import ContratoFormalizado
    contrato_formal = ContratoFormalizado.objects.filter(proposta=proposta).first()
    is_formalizacao = etapa_ativa and etapa_ativa.etapa == "FORMALIZACAO"

    # Dados para auto-preenchimento de cheques
    parcelas_json = "[]"
    if is_formalizacao:
        from .services import simular
        try:
            _, parc_val, _, _, tabela = simular(
                proposta.valor_solicitado, proposta.qtd_parcelas,
                proposta.taxa_juros, proposta.primeiro_vencimento,
            )
            import json
            parcelas_json = json.dumps([
                {"numero": p.numero, "vencimento": p.vencimento.strftime("%Y-%m-%d"), "valor": str(p.valor)}
                for p in tabela
            ])
        except Exception:
            parcelas_json = "[]"

    # Garantias
    garantias = proposta.garantias.select_related(
        "avalista", "bem_movel", "bem_imovel"
    ).all()

    # Dados dos avalistas para o comitê (documentos e cadastro)
    avalistas_detalhes = []
    if is_comite:
        for g in garantias.filter(tipo="AVALISTA", avalista__isnull=False):
            av = g.avalista
            av_docs = av.documentos.all()
            av_docs_dict = av.documentos_dict
            avalistas_detalhes.append({
                "cliente": av,
                "documentos": av_docs,
                "docs_dict": av_docs_dict,
                "bens_moveis": av.bens_moveis.all(),
                "bens_imoveis": av.bens_imoveis.all(),
            })

    # Checklist da formalização para exibir na liberação
    checklist_formalizacao = []
    if etapa_ativa and etapa_ativa.etapa == "LIBERACAO":
        etapa_form = todas_etapas.filter(etapa="FORMALIZACAO", resultado="APROVADO").last()
        if etapa_form:
            checklist_formalizacao = etapa_form.checklist.all()

    # === ANÁLISE DE RENDA E COMPROMETIMENTO ===
    analise_renda = None
    consulta_credito = None
    score_resultado = None
    is_analise_ou_comite = etapa_ativa and etapa_ativa.etapa in ("ANALISE_CREDITO", "COMITE")
    if is_analise_ou_comite:
        cli = proposta.cliente
        from clientes.models import DocumentoCliente, ConsultaCredito
        from .score_credito import calcular_score

        # Score de crédito
        try:
            score_resultado = calcular_score(cli, proposta)
            # Salva na proposta se ainda não foi salvo
            if proposta.score_calculado != score_resultado["score"]:
                proposta.score_calculado = score_resultado["score"]
                proposta.score_detalhamento = {
                    "fatores": [
                        {"nome": f["nome"], "nota": f["nota"], "pontos": f["pontos"], "detalhe": f["detalhe"]}
                        for f in score_resultado["fatores"]
                    ]
                }
                proposta.save(update_fields=["score_calculado", "score_detalhamento"])
        except Exception as e:
            score_resultado = None

        # Última consulta de crédito
        consulta_credito = ConsultaCredito.objects.filter(
            cliente=cli
        ).prefetch_related("restricoes", "documento").order_by("-criado_em").first()

        # Busca último comprovante de renda
        ultimo_comp = DocumentoCliente.objects.filter(
            cliente=cli, tipo="COMP_RENDA"
        ).order_by("-ano_referencia", "-mes_referencia").first()

        renda_bruta = ultimo_comp.renda_bruta if ultimo_comp and ultimo_comp.renda_bruta else None
        renda_liquida = ultimo_comp.renda_liquida if ultimo_comp and ultimo_comp.renda_liquida else None
        renda_cadastro = cli.renda_mensal
        outros = cli.outros_rendimentos

        # Simulação da parcela
        _, parc_aplicada, _, _, _ = simular(
            valor_emprestado=proposta.valor_solicitado,
            qtd_parcelas=proposta.qtd_parcelas,
            taxa_juros_mensal=proposta.taxa_juros,
            primeiro_vencimento=proposta.primeiro_vencimento,
        )

        # Cálculo de comprometimento
        comp_bruto = None
        comp_liquido = None
        comp_cadastro = None
        if renda_bruta and renda_bruta > 0:
            comp_bruto = (parc_aplicada / renda_bruta * Decimal("100")).quantize(Decimal("0.1"))
        if renda_liquida and renda_liquida > 0:
            comp_liquido = (parc_aplicada / renda_liquida * Decimal("100")).quantize(Decimal("0.1"))
        if renda_cadastro and renda_cadastro > 0:
            comp_cadastro = (parc_aplicada / renda_cadastro * Decimal("100")).quantize(Decimal("0.1"))

        # Bens do cliente
        bens_moveis = cli.bens_moveis.all()
        bens_imoveis = cli.bens_imoveis.all()

        # Referência do comprovante
        ref_comp = None
        if ultimo_comp and ultimo_comp.mes_referencia:
            ref_comp = f"{ultimo_comp.mes_referencia}/{ultimo_comp.ano_referencia}"

        analise_renda = {
            "renda_bruta": renda_bruta,
            "renda_liquida": renda_liquida,
            "renda_cadastro": renda_cadastro,
            "outros_rendimentos": outros,
            "ref_comprovante": ref_comp,
            "parcela": parc_aplicada,
            "comp_bruto": comp_bruto,
            "comp_liquido": comp_liquido,
            "comp_cadastro": comp_cadastro,
            "bens_moveis": bens_moveis,
            "bens_imoveis": bens_imoveis,
        }

    return render(request, "emprestimos/esteira/detalhe.html", {
        "proposta": proposta,
        "etapa_ativa": etapa_ativa,
        "checklist": checklist,
        "todas_etapas": todas_etapas,
        "dossie": dossie,
        "parceiros": parceiros,
        "pode_atuar": pode_atuar,
        "progresso": progresso,
        "docs_status": docs_status,
        "pendencias_docs": pendencias_docs,
        "votos": votos,
        "ja_votou": ja_votou,
        "is_comite": is_comite,
        "cliente_completo": cliente_completo,
        "contratos_abertos": contratos_abertos,
        "historico_pagamentos": historico_pagamentos,
        "docs_tipos": DocumentoCliente.TIPO_CHOICES,
        "contrato_formal": contrato_formal,
        "is_formalizacao": is_formalizacao,
        "parcelas_json": parcelas_json,
        "clientes": Cliente.objects.all().order_by("nome_completo") if is_formalizacao else [],
        "garantias": garantias,
        "checklist_formalizacao": checklist_formalizacao,
        "analise_renda": analise_renda,
        "is_analise_ou_comite": is_analise_ou_comite,
        "consulta_credito": consulta_credito,
        "score_resultado": score_resultado,
    })


# ==============================================================================
# 4. AÇÕES DA ETAPA (avançar, devolver, negar, marcar checklist)
# ==============================================================================

@login_required
@transaction.atomic
def avancar_etapa(request, proposta_id):
    """Aprova a etapa atual e avança para a próxima."""
    proposta = get_object_or_404(PropostaEmprestimo, id=proposta_id)
    etapa_ativa = proposta.etapa_atual_obj

    if not etapa_ativa or request.method != "POST":
        return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)

    # Verifica checklist obrigatório
    pendentes = etapa_ativa.checklist.filter(obrigatorio=True, concluido=False)
    if pendentes.exists():
        nomes = ", ".join([p.descricao for p in pendentes[:3]])
        messages.error(request, f"Itens obrigatórios pendentes: {nomes}")
        return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)

    parecer = request.POST.get("parecer", "")

    # Finaliza etapa atual
    etapa_ativa.resultado = EtapaProposta.Resultado.APROVADO
    etapa_ativa.ativa = False
    etapa_ativa.finalizado_em = timezone.now()
    etapa_ativa.responsavel = request.user
    etapa_ativa.parecer = parecer
    etapa_ativa.save()

    # Determina próxima etapa
    proxima = _proxima_etapa(etapa_ativa.etapa, proposta)

    if proxima:
        # Cria próxima etapa
        nova = EtapaProposta.objects.create(
            proposta=proposta,
            etapa=proxima,
        )
        _criar_checklist_para_etapa(nova)
        proposta.status = proxima
        proposta.save()
        messages.success(request, f"Avançou para: {nova.get_etapa_display()}")
    else:
        # Última etapa (LIBERACAO) — hora de aprovar e liberar
        return _liberar_proposta(request, proposta)

    return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)


@login_required
@transaction.atomic
def devolver_etapa(request, proposta_id):
    """Devolve a proposta para etapa anterior ou para etapa específica."""
    proposta = get_object_or_404(PropostaEmprestimo, id=proposta_id)
    etapa_ativa = proposta.etapa_atual_obj

    if not etapa_ativa or request.method != "POST":
        return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)

    motivo = request.POST.get("motivo", "Sem motivo informado")
    devolver_para = request.POST.get("devolver_para", "")

    # Se especificou destino, usa; senão, volta uma etapa
    if devolver_para:
        destino = devolver_para
    else:
        destino = _etapa_anterior(etapa_ativa.etapa)

    if not destino:
        messages.error(request, "Não é possível devolver desta etapa.")
        return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)

    # Finaliza etapa atual como devolvida
    etapa_ativa.resultado = EtapaProposta.Resultado.DEVOLVIDO
    etapa_ativa.ativa = False
    etapa_ativa.finalizado_em = timezone.now()
    etapa_ativa.responsavel = request.user
    etapa_ativa.parecer = f"DEVOLVIDO para {destino}: {motivo}"
    etapa_ativa.save()

    # Cria nova etapa no destino
    nova = EtapaProposta.objects.create(
        proposta=proposta,
        etapa=destino,
    )
    _criar_checklist_para_etapa(nova)
    proposta.status = destino
    proposta.save()

    messages.warning(request, f"Proposta devolvida para: {nova.get_etapa_display()}")
    return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)


@login_required
@transaction.atomic
def negar_proposta(request, proposta_id):
    """Nega a proposta em qualquer etapa."""
    proposta = get_object_or_404(PropostaEmprestimo, id=proposta_id)
    etapa_ativa = proposta.etapa_atual_obj

    if not etapa_ativa or request.method != "POST":
        return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)

    parecer = request.POST.get("parecer", "")

    etapa_ativa.resultado = EtapaProposta.Resultado.NEGADO
    etapa_ativa.ativa = False
    etapa_ativa.finalizado_em = timezone.now()
    etapa_ativa.responsavel = request.user
    etapa_ativa.parecer = parecer
    etapa_ativa.save()

    proposta.status = "NEGADO"
    proposta.parecer_analise = parecer
    proposta.usuario_aprovador = request.user
    proposta.data_analise = timezone.now()
    proposta.save()

    messages.info(request, f"Proposta #{proposta.id} negada.")
    return redirect("emprestimos:painel_esteira")


@login_required
def marcar_checklist(request, item_id):
    """Marca/desmarca um item do checklist via AJAX ou POST."""
    item = get_object_or_404(ChecklistItem, id=item_id)
    proposta_id = item.etapa_proposta.proposta_id

    if item.concluido:
        item.concluido = False
        item.concluido_por = None
        item.concluido_em = None
    else:
        item.concluido = True
        item.concluido_por = request.user
        item.concluido_em = timezone.now()
    item.save()

    return redirect("emprestimos:esteira_detalhe", proposta_id=proposta_id)


# ==============================================================================
# 5. LIBERAÇÃO FINAL (gera contrato + movimentação financeira)
# ==============================================================================

@transaction.atomic
def _liberar_proposta(request, proposta):
    """
    Última etapa: gera o contrato, parcelas e movimentações financeiras.
    Reutiliza a lógica que já existia em analisar_proposta.
    """
    from .views import to_decimal

    # Simulação Price
    _, parc_aplicada, total_ctr, ajuste, parcelas_sim = simular(
        valor_emprestado=proposta.valor_solicitado,
        qtd_parcelas=proposta.qtd_parcelas,
        taxa_juros_mensal=proposta.taxa_juros,
        primeiro_vencimento=proposta.primeiro_vencimento,
    )

    codigo_novo = f"EMP{timezone.now().strftime('%Y%m%d%H%M%S')}"

    # Cria contrato
    emprestimo = Emprestimo.objects.create(
        cliente=proposta.cliente,
        codigo_contrato=codigo_novo,
        valor_emprestado=proposta.valor_solicitado,
        qtd_parcelas=proposta.qtd_parcelas,
        taxa_juros_mensal=proposta.taxa_juros,
        primeiro_vencimento=proposta.primeiro_vencimento,
        valor_parcela_aplicada=parc_aplicada,
        total_contrato=total_ctr,
        total_juros=(total_ctr - proposta.valor_solicitado).quantize(Decimal("0.01")),
        ajuste_arredondamento=ajuste,
        parceiro=proposta.parceiro,
        percentual_comissao=proposta.percentual_comissao,
        tem_multa_atraso=proposta.tem_multa,
        multa_atraso_percent=proposta.multa_percent,
        juros_mora_mensal_percent=proposta.juros_mora_percent,
        status=EmprestimoStatus.ATIVO,
    )

    # Cria parcelas
    Parcela.objects.bulk_create([
        Parcela(
            emprestimo=emprestimo,
            numero=p.numero,
            vencimento=p.vencimento,
            valor=p.valor,
            status=ParcelaStatus.ABERTA,
        )
        for p in parcelas_sim
    ])

    # Log de auditoria
    ContratoLog.objects.create(
        contrato=emprestimo,
        acao=ContratoLog.Acao.CRIADO,
        usuario=request.user,
        observacao=f"Via Esteira — Proposta #{proposta.id}",
    )

    # Movimentação financeira: saída do caixa
    Transacao.objects.create(
        tipo="EMPRESTIMO_SAIDA",
        valor=-abs(proposta.valor_solicitado),
        descricao=f"Liberação {codigo_novo} — {proposta.cliente.nome_completo}",
        emprestimo=emprestimo,
        usuario=request.user,
    )

    # Crédito na conta do cliente
    conta_cli, _ = ContaCorrente.objects.get_or_create(cliente=proposta.cliente)
    MovimentacaoConta.objects.create(
        conta=conta_cli,
        tipo="CREDITO",
        origem="EMPRESTIMO",
        valor=abs(proposta.valor_solicitado),
        descricao=f"Liberação Empréstimo {codigo_novo}",
        emprestimo=emprestimo,
    )

    # Atualiza proposta
    proposta.status = "APROVADO"
    proposta.emprestimo_gerado = emprestimo
    proposta.usuario_aprovador = request.user
    proposta.data_analise = timezone.now()
    proposta.save()

    # === RENEGOCIAÇÃO: Liquidar contrato antigo ===
    if proposta.finalidade == "RENEGOCIACAO" and proposta.contrato_renegociado:
        contrato_antigo = proposta.contrato_renegociado
        parcelas_antigas = contrato_antigo.parcelas.filter(status=ParcelaStatus.ABERTA)

        for p in parcelas_antigas:
            p.status = ParcelaStatus.LIQUIDADA_RENEGOCIACAO if hasattr(ParcelaStatus, 'LIQUIDADA_RENEGOCIACAO') else ParcelaStatus.PAGA
            p.data_pagamento = timezone.now()
            p.save()

        contrato_antigo.status = EmprestimoStatus.RENEGOCIADO
        contrato_antigo.save(update_fields=["status", "atualizado_em"])

        messages.info(request, f"Contrato antigo {contrato_antigo.codigo_contrato} liquidado pela renegociação.")

    messages.success(
        request,
        f"Proposta #{proposta.id} aprovada! Contrato {codigo_novo} gerado "
        f"e R$ {proposta.valor_solicitado:,.2f} liberado."
    )
    return redirect("emprestimos:contrato_detalhe", pk=emprestimo.id)


# ==============================================================================
# 5B. FORMALIZAÇÃO — Emissão de Contrato e Nota Promissória
# ==============================================================================

MESES = {
    1: "janeiro", 2: "fevereiro", 3: "março", 4: "abril",
    5: "maio", 6: "junho", 7: "julho", 8: "agosto",
    9: "setembro", 10: "outubro", 11: "novembro", 12: "dezembro",
}


@login_required
def emitir_contrato_pdf(request, proposta_id):
    """Gera o contrato de empréstimo em PDF e registra a emissão."""
    from django.http import HttpResponse
    from num2words import num2words
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_JUSTIFY
    from reportlab.lib import colors
    from .models import ContratoFormalizado

    proposta = get_object_or_404(PropostaEmprestimo.objects.select_related("cliente"), id=proposta_id)
    cli = proposta.cliente
    hoje = timezone.localdate()

    # Cria ou recupera o contrato formalizado
    contrato_f, criado = ContratoFormalizado.objects.get_or_create(
        proposta=proposta,
        defaults={
            "numero": ContratoFormalizado.proximo_numero(),
            "ano": hoje.year,
            "numero_formatado": ContratoFormalizado.gerar_numero_formatado(
                ContratoFormalizado.proximo_numero(), hoje.year
            ),
            "emitido_por": request.user,
        }
    )

    if criado or not contrato_f.contrato_emitido:
        contrato_f.contrato_emitido = True
        contrato_f.contrato_emitido_em = timezone.now()
        contrato_f.save()

    # Simulação Price
    _, parc_aplicada, total_ctr, _, parcelas_sim = simular(
        valor_emprestado=proposta.valor_solicitado,
        qtd_parcelas=proposta.qtd_parcelas,
        taxa_juros_mensal=proposta.taxa_juros,
        primeiro_vencimento=proposta.primeiro_vencimento,
    )
    total_juros = (total_ctr - proposta.valor_solicitado).quantize(Decimal("0.01"))

    # Valor por extenso
    val_int = int(proposta.valor_solicitado)
    val_cents = int((proposta.valor_solicitado % 1) * 100)
    extenso = num2words(val_int, lang="pt_BR")
    if val_cents > 0:
        extenso += f" reais e {num2words(val_cents, lang='pt_BR')} centavos"
    else:
        extenso += " reais"

    fmt = lambda v: f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    mes_ext = MESES.get(hoje.month, "")
    data_ext = f"{hoje.day} de {mes_ext} de {hoje.year}"

    # PDF
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="contrato_{contrato_f.numero_formatado.replace("/", "_").replace(" ", "_")}.pdf"'

    doc = SimpleDocTemplate(response, pagesize=A4, topMargin=20*mm, bottomMargin=20*mm, leftMargin=25*mm, rightMargin=25*mm)
    styles = getSampleStyleSheet()
    els = []

    st_titulo = ParagraphStyle("T", parent=styles["Title"], fontSize=14, spaceAfter=4)
    st_sub = ParagraphStyle("S", parent=styles["Normal"], fontSize=10, alignment=TA_CENTER, spaceAfter=12)
    st_corpo = ParagraphStyle("C", parent=styles["Normal"], fontSize=10, leading=16, alignment=TA_JUSTIFY, spaceAfter=8)
    st_negrito = ParagraphStyle("N", parent=styles["Normal"], fontSize=10, leading=16, alignment=TA_JUSTIFY, spaceAfter=8, fontName="Helvetica-Bold")
    st_data = ParagraphStyle("D", parent=styles["Normal"], fontSize=10, alignment=TA_RIGHT, spaceBefore=20)
    st_assina = ParagraphStyle("A", parent=styles["Normal"], fontSize=10, alignment=TA_CENTER, spaceBefore=40)

    els.append(Paragraph("CONTRATO DE EMPRÉSTIMO", st_titulo))
    els.append(Paragraph(f"Nº {contrato_f.numero_formatado}", st_sub))
    els.append(Spacer(1, 5*mm))

    endereco = f"{cli.logradouro}, {cli.numero}"
    if cli.complemento:
        endereco += f" - {cli.complemento}"
    endereco += f", {cli.bairro}, {cli.cidade}/{cli.uf} - CEP: {cli.cep}"

    els.append(Paragraph(
        f"Pelo presente instrumento particular, de um lado a <b>CREDORA</b>, doravante denominada "
        f"simplesmente CREDORA, e de outro lado <b>{cli.nome_completo}</b>, inscrito(a) no CPF sob o "
        f"nº <b>{cli.cpf}</b>, residente e domiciliado(a) em <b>{endereco}</b>, "
        f"doravante denominado(a) DEVEDOR(A), têm entre si justo e contratado o seguinte:",
        st_corpo,
    ))

    # Cláusulas
    clausulas = [
        (
            "CLÁUSULA PRIMEIRA — DO OBJETO",
            f"A CREDORA concede ao(à) DEVEDOR(A) um empréstimo no valor de "
            f"<b>{fmt(proposta.valor_solicitado)} ({extenso})</b>, que o(a) DEVEDOR(A) "
            f"declara ter recebido nesta data, dando plena e irrevogável quitação.",
        ),
        (
            "CLÁUSULA SEGUNDA — DOS JUROS E ENCARGOS",
            f"Sobre o valor emprestado incidirão juros remuneratórios de <b>{proposta.taxa_juros}% "
            f"ao mês</b> (tabela Price), totalizando <b>{fmt(total_juros)}</b> de juros "
            f"e o montante final de <b>{fmt(total_ctr)}</b>.",
        ),
        (
            "CLÁUSULA TERCEIRA — DO PAGAMENTO",
            f"O(A) DEVEDOR(A) se obriga a pagar o valor total em <b>{proposta.qtd_parcelas} "
            f"({num2words(proposta.qtd_parcelas, lang='pt_BR')}) parcelas</b> mensais, "
            f"fixas e consecutivas, no valor de <b>{fmt(parc_aplicada)}</b> cada, "
            f"com primeiro vencimento em <b>{proposta.primeiro_vencimento.strftime('%d/%m/%Y')}</b>.",
        ),
        (
            "CLÁUSULA QUARTA — DA MORA",
            "Em caso de atraso no pagamento de qualquer parcela, incidirá multa de <b>2% (dois por cento)</b> "
            "sobre o valor da parcela em atraso, acrescida de juros de mora de <b>1% (um por cento) ao mês</b>, "
            "calculados pro rata die.",
        ),
        (
            "CLÁUSULA QUINTA — DO VENCIMENTO ANTECIPADO",
            "O não pagamento de qualquer parcela em seu vencimento acarretará o vencimento antecipado "
            "de todas as demais parcelas, tornando-se a dívida integralmente exigível, podendo a CREDORA "
            "inscrever o nome do(a) DEVEDOR(A) nos órgãos de proteção ao crédito, protestar o título "
            "em cartório e promover a execução judicial do débito.",
        ),
        (
            "CLÁUSULA SEXTA — DO FORO",
            "Fica eleito o foro da Comarca de <b>Rio de Janeiro/RJ</b> para dirimir quaisquer questões "
            "oriundas deste contrato, com renúncia expressa de qualquer outro, por mais privilegiado que seja.",
        ),
    ]

    for titulo, texto in clausulas:
        els.append(Paragraph(titulo, st_negrito))
        els.append(Paragraph(texto, st_corpo))

    els.append(Paragraph(
        "E, por estarem assim justos e contratados, assinam o presente instrumento em "
        "duas vias de igual teor e forma.",
        st_corpo,
    ))

    els.append(Paragraph(f"Rio de Janeiro, {data_ext}.", st_data))

    # Assinaturas
    els.append(Spacer(1, 15*mm))
    assin_data = [
        [Paragraph("_" * 35, st_assina), Paragraph("_" * 35, st_assina)],
        [Paragraph("<b>CREDORA</b>", st_assina), Paragraph(f"<b>DEVEDOR(A)</b><br/>{cli.nome_completo}<br/>CPF: {cli.cpf}", st_assina)],
    ]
    assin_tab = Table(assin_data, colWidths=[80*mm, 80*mm])
    assin_tab.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    els.append(assin_tab)

    # Testemunhas
    els.append(Spacer(1, 15*mm))
    els.append(Paragraph("<b>Testemunhas:</b>", st_corpo))
    test_data = [
        [Paragraph("_" * 30, st_assina), Paragraph("_" * 30, st_assina)],
        [Paragraph("Nome:<br/>CPF:", st_assina), Paragraph("Nome:<br/>CPF:", st_assina)],
    ]
    test_tab = Table(test_data, colWidths=[80*mm, 80*mm])
    els.append(test_tab)

    doc.build(els)
    return response


@login_required
def emitir_promissoria_pdf(request, proposta_id):
    """Gera a nota promissória em PDF."""
    from django.http import HttpResponse
    from num2words import num2words
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_JUSTIFY
    from reportlab.lib import colors
    from .models import ContratoFormalizado

    proposta = get_object_or_404(PropostaEmprestimo.objects.select_related("cliente"), id=proposta_id)
    cli = proposta.cliente
    hoje = timezone.localdate()

    # Recupera contrato formalizado
    contrato_f = ContratoFormalizado.objects.filter(proposta=proposta).first()
    if contrato_f:
        contrato_f.promissoria_emitida = True
        contrato_f.promissoria_emitida_em = timezone.now()
        contrato_f.save()
        num_contrato = contrato_f.numero_formatado
    else:
        num_contrato = "—"

    # Simulação
    _, parc_aplicada, total_ctr, _, _ = simular(
        valor_emprestado=proposta.valor_solicitado,
        qtd_parcelas=proposta.qtd_parcelas,
        taxa_juros_mensal=proposta.taxa_juros,
        primeiro_vencimento=proposta.primeiro_vencimento,
    )

    fmt = lambda v: f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    val_int = int(total_ctr)
    val_cents = int((total_ctr % 1) * 100)
    extenso = num2words(val_int, lang="pt_BR")
    if val_cents > 0:
        extenso += f" reais e {num2words(val_cents, lang='pt_BR')} centavos"
    else:
        extenso += " reais"

    mes_ext = MESES.get(hoje.month, "")
    data_ext = f"{hoje.day} de {mes_ext} de {hoje.year}"

    # Vencimento da última parcela
    ultima_parc = proposta.primeiro_vencimento
    from dateutil.relativedelta import relativedelta
    ultima_parc = proposta.primeiro_vencimento + relativedelta(months=proposta.qtd_parcelas - 1)

    endereco = f"{cli.logradouro}, {cli.numero}"
    if cli.complemento:
        endereco += f" - {cli.complemento}"
    endereco += f", {cli.bairro}, {cli.cidade}/{cli.uf}"

    # PDF
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="promissoria_{num_contrato.replace("/", "_").replace(" ", "_")}.pdf"'

    doc = SimpleDocTemplate(response, pagesize=A4, topMargin=25*mm, bottomMargin=25*mm, leftMargin=25*mm, rightMargin=25*mm)
    styles = getSampleStyleSheet()
    els = []

    st_titulo = ParagraphStyle("T", parent=styles["Title"], fontSize=16, spaceAfter=4, fontName="Helvetica-Bold")
    st_sub = ParagraphStyle("S", parent=styles["Normal"], fontSize=10, alignment=TA_CENTER, spaceAfter=15)
    st_corpo = ParagraphStyle("C", parent=styles["Normal"], fontSize=11, leading=18, alignment=TA_JUSTIFY, spaceAfter=10)
    st_campo = ParagraphStyle("F", parent=styles["Normal"], fontSize=10, leading=16, spaceAfter=4)
    st_data = ParagraphStyle("D", parent=styles["Normal"], fontSize=10, alignment=TA_RIGHT, spaceBefore=20)
    st_assina = ParagraphStyle("A", parent=styles["Normal"], fontSize=10, alignment=TA_CENTER, spaceBefore=30)

    # Cabeçalho
    els.append(Paragraph("NOTA PROMISSÓRIA", st_titulo))
    els.append(Paragraph(f"Vinculada ao Contrato {num_contrato}", st_sub))

    # Dados em formato de tabela
    info = [
        ["Nº:", f"{num_contrato}", "Vencimento:", f"{ultima_parc.strftime('%d/%m/%Y')}"],
        ["Valor:", f"{fmt(total_ctr)}", "", ""],
    ]
    info_tab = Table(info, colWidths=[20*mm, 60*mm, 25*mm, 55*mm])
    info_tab.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f0f0")),
        ("BACKGROUND", (2, 0), (2, 0), colors.HexColor("#f0f0f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    els.append(info_tab)
    els.append(Spacer(1, 8*mm))

    # Corpo
    els.append(Paragraph(
        f"No vencimento acima indicado, pagarei por esta única via de NOTA PROMISSÓRIA "
        f"a quantia de <b>{fmt(total_ctr)} ({extenso})</b> ao portador desta ou à sua ordem.",
        st_corpo,
    ))

    els.append(Paragraph(
        f"Pagável em <b>{proposta.qtd_parcelas} ({num2words(proposta.qtd_parcelas, lang='pt_BR')}) "
        f"parcelas mensais</b> de <b>{fmt(parc_aplicada)}</b>, a primeira com vencimento em "
        f"<b>{proposta.primeiro_vencimento.strftime('%d/%m/%Y')}</b>.",
        st_corpo,
    ))

    els.append(Spacer(1, 5*mm))

    # Dados do emitente
    els.append(Paragraph(f"<b>Emitente:</b> {cli.nome_completo}", st_campo))
    els.append(Paragraph(f"<b>CPF:</b> {cli.cpf}", st_campo))
    els.append(Paragraph(f"<b>Endereço:</b> {endereco}", st_campo))

    els.append(Paragraph(f"Rio de Janeiro, {data_ext}.", st_data))

    # Assinatura
    els.append(Spacer(1, 20*mm))
    els.append(Paragraph("_" * 40, st_assina))
    els.append(Paragraph(f"<b>{cli.nome_completo}</b><br/>CPF: {cli.cpf}", st_assina))

    doc.build(els)
    return response


# ==============================================================================
# 6. VOTAÇÃO DO COMITÊ
# ==============================================================================

@login_required
@transaction.atomic
def votar_comite(request, proposta_id):
    """Registra voto do membro do comitê. Deferir avança, Indeferir nega."""
    from .models import VotoComite
    from django.contrib.auth import authenticate

    proposta = get_object_or_404(PropostaEmprestimo, id=proposta_id)

    if request.method != "POST":
        return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)

    senha = request.POST.get("senha", "")
    user = authenticate(username=request.user.username, password=senha)
    if not user:
        messages.error(request, "Senha inválida. Voto não registrado.")
        return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)

    if VotoComite.objects.filter(proposta=proposta, usuario=request.user).exists():
        messages.warning(request, "Você já registrou seu voto nesta proposta.")
        return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)

    decisao = request.POST.get("decisao", "")
    observacoes = request.POST.get("observacoes_voto", "")

    if decisao not in ("DEFERIDO", "INDEFERIDO"):
        messages.error(request, "Decisão inválida.")
        return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)

    VotoComite.objects.create(
        proposta=proposta,
        usuario=request.user,
        decisao=decisao,
        observacoes=observacoes,
    )

    etapa_ativa = proposta.etapa_atual_obj

    if decisao == "DEFERIDO" and etapa_ativa:
        # Aprova a etapa e avança para Formalização
        etapa_ativa.resultado = EtapaProposta.Resultado.APROVADO
        etapa_ativa.ativa = False
        etapa_ativa.finalizado_em = timezone.now()
        etapa_ativa.responsavel = request.user
        etapa_ativa.parecer = f"Deferido: {observacoes}" if observacoes else "Deferido pelo comitê"
        etapa_ativa.save()

        proxima = _proxima_etapa(etapa_ativa.etapa, proposta)
        if proxima:
            nova = EtapaProposta.objects.create(proposta=proposta, etapa=proxima)
            _criar_checklist_para_etapa(nova)
            proposta.status = proxima
            proposta.save()
            messages.success(request, f"Voto DEFERIDO — Avançou para {nova.get_etapa_display()}.")
        return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)

    elif decisao == "INDEFERIDO" and etapa_ativa:
        # Nega a proposta inteira
        etapa_ativa.resultado = EtapaProposta.Resultado.NEGADO
        etapa_ativa.ativa = False
        etapa_ativa.finalizado_em = timezone.now()
        etapa_ativa.responsavel = request.user
        etapa_ativa.parecer = f"Indeferido: {observacoes}" if observacoes else "Indeferido pelo comitê"
        etapa_ativa.save()

        proposta.status = "NEGADO"
        proposta.parecer_analise = etapa_ativa.parecer
        proposta.usuario_aprovador = request.user
        proposta.data_analise = timezone.now()
        proposta.save()

        messages.info(request, "Voto INDEFERIDO — Proposta negada.")
        return redirect("emprestimos:painel_esteira")

    messages.success(request, f"Voto registrado: {decisao}")
    return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)


# ==============================================================================
# 7. SIMULAÇÃO AJAX (chamada pelo formulário de nova proposta)
# ==============================================================================

@login_required
def buscar_bens_cliente_ajax(request):
    """Retorna bens móveis e imóveis do cliente em JSON."""
    cliente_id = request.GET.get("cliente_id")
    if not cliente_id:
        return JsonResponse({"moveis": [], "imoveis": []})

    try:
        cliente = Cliente.objects.get(id=cliente_id)
    except Cliente.DoesNotExist:
        return JsonResponse({"moveis": [], "imoveis": []})

    moveis = [
        {"id": b.id, "tipo": b.get_tipo_display(), "descricao": b.descricao, "placa": b.placa, "renavam": b.renavam}
        for b in cliente.bens_moveis.all()
    ]
    imoveis = [
        {"id": b.id, "tipo": b.get_tipo_display(), "descricao": b.descricao, "matricula": b.matricula, "endereco": b.endereco_completo}
        for b in cliente.bens_imoveis.all()
    ]

    return JsonResponse({"moveis": moveis, "imoveis": imoveis})

@login_required
def simular_ajax(request):
    """Recebe valor, taxa, parcelas e vencimento via GET e retorna a simulação em JSON."""
    from .views import to_decimal

    try:
        valor = to_decimal(request.GET.get("valor", "0"))
        taxa = Decimal(request.GET.get("taxa", "0").replace(",", "."))
        qtd = int(request.GET.get("qtd", "1"))
        vencimento_str = request.GET.get("vencimento", "")

        if valor <= 0 or qtd <= 0:
            return JsonResponse({"erro": "Valor e parcelas devem ser maiores que zero."}, status=400)

        if not vencimento_str:
            from datetime import date, timedelta
            vencimento = date.today() + timedelta(days=30)
        else:
            from datetime import date
            vencimento = date.fromisoformat(vencimento_str)

        parcela_bruta, parcela_aplicada, total_contrato, ajuste, parcelas = simular(
            valor_emprestado=valor,
            qtd_parcelas=qtd,
            taxa_juros_mensal=taxa,
            primeiro_vencimento=vencimento,
        )

        total_juros = (total_contrato - valor).quantize(Decimal("0.01"))

        parcelas_lista = [
            {
                "numero": p.numero,
                "vencimento": p.vencimento.strftime("%d/%m/%Y"),
                "valor": str(p.valor),
            }
            for p in parcelas
        ]

        return JsonResponse({
            "valor_emprestado": str(valor),
            "parcela_bruta": str(parcela_bruta),
            "parcela_aplicada": str(parcela_aplicada),
            "total_contrato": str(total_contrato),
            "total_juros": str(total_juros),
            "ajuste": str(ajuste),
            "qtd_parcelas": qtd,
            "taxa": str(taxa),
            "parcelas": parcelas_lista,
        })

    except Exception as e:
        return JsonResponse({"erro": str(e)}, status=400)


@login_required
def bens_cliente_ajax(request):
    """Retorna bens móveis e imóveis de um cliente em JSON."""
    from clientes.models import BemMovel, BemImovel

    cliente_id = request.GET.get("cliente_id")
    if not cliente_id:
        return JsonResponse({"moveis": [], "imoveis": []})

    moveis = list(BemMovel.objects.filter(cliente_id=cliente_id).values(
        "id", "tipo", "descricao", "placa", "renavam"
    ))
    for m in moveis:
        m["tipo_display"] = dict(BemMovel.TIPO_CHOICES).get(m["tipo"], m["tipo"])

    imoveis = list(BemImovel.objects.filter(cliente_id=cliente_id).values(
        "id", "tipo", "descricao", "matricula", "logradouro", "numero", "bairro", "cidade", "uf"
    ))
    for i in imoveis:
        i["tipo_display"] = dict(BemImovel.TIPO_CHOICES).get(i["tipo"], i["tipo"])

    return JsonResponse({"moveis": moveis, "imoveis": imoveis})

# ==============================================================================
# EDITAR PROPOSTA
# ==============================================================================

@login_required
@transaction.atomic
def editar_proposta(request, proposta_id):
    """Permite editar os dados de uma proposta — apenas em CAPTACAO."""
    from .views import to_decimal

    proposta = get_object_or_404(PropostaEmprestimo, id=proposta_id)

    # Só edita se estiver em CAPTACAO ou DOCUMENTACAO
    if proposta.status not in ("CAPTACAO", "DOCUMENTACAO"):
        messages.error(request, "Só é possível editar propostas nas etapas iniciais.")
        return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)

    if request.method == "POST":
        try:
            proposta.valor_solicitado = to_decimal(request.POST.get("valor", "0"))
            proposta.taxa_juros = Decimal(request.POST.get("taxa", "0").replace(",", "."))
            proposta.qtd_parcelas = int(request.POST.get("qtd_parcelas", 1))
            proposta.primeiro_vencimento = request.POST.get("vencimento", proposta.primeiro_vencimento)
            proposta.finalidade = request.POST.get("finalidade", proposta.finalidade)
            proposta.observacoes = request.POST.get("observacoes", "")

            # Multa e mora
            proposta.tem_multa = request.POST.get("tem_multa") == "on"
            proposta.multa_percent = Decimal(request.POST.get("multa_percent", "2.00").replace(",", "."))
            proposta.tem_juros_mora = request.POST.get("tem_juros_mora") == "on"
            proposta.juros_mora_percent = Decimal(request.POST.get("juros_mora_percent", "2.00").replace(",", "."))

            # IOF
            tem_iof = request.POST.get("tem_iof") == "on"
            proposta.tem_iof = tem_iof
            valor = proposta.valor_solicitado
            qtd = proposta.qtd_parcelas
            valor_iof = Decimal("0.00")
            if tem_iof:
                dias = min(qtd * 30, 365)
                iof_calc = valor * (Decimal("0.000082")) * Decimal(str(dias))
                iof_adic = valor * (Decimal("0.0038"))
                valor_iof = (iof_calc + iof_adic).quantize(Decimal("0.01"))
            proposta.valor_iof = valor_iof

            # Débitos
            deb_str = request.POST.get("valor_debitos_extras", "0")
            deb_limpo = deb_str.replace("R$", "").replace(" ", "").strip()
            if "," in deb_limpo and "." in deb_limpo:
                deb_limpo = deb_limpo.replace(".", "").replace(",", ".")
            elif "," in deb_limpo:
                deb_limpo = deb_limpo.replace(",", ".")
            proposta.valor_debitos_extras = Decimal(deb_limpo or "0").quantize(Decimal("0.01"))
            proposta.descricao_debitos = request.POST.get("descricao_debitos", "")
            proposta.valor_bruto = valor + valor_iof + proposta.valor_debitos_extras

            proposta.save()
            messages.success(request, "Proposta atualizada com sucesso.")
            return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)

        except (ValueError, Exception) as e:
            messages.error(request, str(e))

    clientes = Cliente.objects.all().order_by("nome_completo")
    return render(request, "emprestimos/esteira/editar_proposta.html", {
        "proposta": proposta,
        "clientes": clientes,
        "finalidades": PropostaEmprestimo.FINALIDADE_CHOICES,
    })


# ==============================================================================
# CHEQUES DE GARANTIA NA FORMALIZAÇÃO
# ==============================================================================

@login_required
def adicionar_cheque(request, proposta_id):
    """Adiciona cheque(s) de garantia na formalização — individual ou em lote."""
    from .models import ChequeGarantia

    proposta = get_object_or_404(PropostaEmprestimo, id=proposta_id)

    if request.method == "POST":
        def _parse(v):
            if not v:
                return Decimal("0")
            limpo = v.replace("R$", "").replace(" ", "").strip()
            if "," in limpo and "." in limpo:
                limpo = limpo.replace(".", "").replace(",", ".")
            elif "," in limpo:
                limpo = limpo.replace(",", ".")
            return Decimal(limpo)

        # Cheques em lote (campos indexados: banco_0, banco_1, ...)
        idx = 0
        criados = 0
        while True:
            banco = request.POST.get(f"banco_{idx}", "").strip()
            numero = request.POST.get(f"numero_cheque_{idx}", "").strip()
            if not banco and not numero:
                break
            ChequeGarantia.objects.create(
                proposta=proposta,
                banco=banco,
                agencia=request.POST.get(f"agencia_{idx}", ""),
                conta_corrente=request.POST.get(f"conta_{idx}", ""),
                numero_cheque=numero,
                valor=_parse(request.POST.get(f"valor_{idx}", "0")),
                vencimento=request.POST.get(f"vencimento_{idx}", timezone.localdate()),
                emitente=request.POST.get(f"emitente_{idx}", ""),
                cpf_emitente=request.POST.get(f"cpf_{idx}", ""),
                registrado_por=request.user,
            )
            criados += 1
            idx += 1

        # Fallback: cheque individual (campos sem índice)
        if criados == 0:
            banco = request.POST.get("banco", "").strip()
            if banco:
                ChequeGarantia.objects.create(
                    proposta=proposta,
                    banco=banco,
                    agencia=request.POST.get("agencia", ""),
                    conta_corrente=request.POST.get("conta_corrente", ""),
                    numero_cheque=request.POST.get("numero_cheque", ""),
                    valor=_parse(request.POST.get("valor", "0")),
                    vencimento=request.POST.get("vencimento", timezone.localdate()),
                    emitente=request.POST.get("emitente", ""),
                    cpf_emitente=request.POST.get("cpf_emitente", ""),
                    registrado_por=request.user,
                )
                criados = 1

        messages.success(request, f"{criados} cheque(s) cadastrado(s).")

    return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)


@login_required
def conferir_cheque(request, cheque_id):
    """Marca cheque como conferido."""
    from .models import ChequeGarantia

    cheque = get_object_or_404(ChequeGarantia, id=cheque_id)
    cheque.conferido = True
    cheque.conferido_por = request.user
    cheque.conferido_em = timezone.now()
    cheque.save()
    messages.success(request, f"Cheque {cheque.numero_cheque} conferido.")
    return redirect("emprestimos:esteira_detalhe", proposta_id=cheque.proposta_id)


@login_required
def excluir_cheque(request, cheque_id):
    """Exclui cheque de garantia."""
    from .models import ChequeGarantia

    cheque = get_object_or_404(ChequeGarantia, id=cheque_id)
    proposta_id = cheque.proposta_id
    cheque.delete()
    messages.info(request, "Cheque removido.")
    return redirect("emprestimos:esteira_detalhe", proposta_id=proposta_id)


# ==============================================================================
# PDF DOSSIÊ DA PROPOSTA
# ==============================================================================

@login_required
def gerar_dossie_pdf(request, proposta_id):
    """Gera o dossiê completo da proposta em PDF."""
    from django.http import HttpResponse
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib import colors
    from core.models import ConfiguracaoEmpresa
    from clientes.models import ConsultaCredito
    from .models import VotoComite, ContratoFormalizado, ChequeGarantia

    proposta = get_object_or_404(PropostaEmprestimo.objects.select_related("cliente"), id=proposta_id)
    cli = proposta.cliente
    cfg = ConfiguracaoEmpresa.get_config()
    hoje = timezone.localdate()

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="dossie_proposta_{proposta.id}.pdf"'

    doc = SimpleDocTemplate(response, pagesize=A4, topMargin=20*mm, bottomMargin=20*mm,
                            leftMargin=15*mm, rightMargin=15*mm)
    styles = getSampleStyleSheet()
    els = []

    st_titulo = ParagraphStyle("Titulo", parent=styles["Heading1"], fontSize=16, alignment=TA_CENTER, spaceAfter=3*mm)
    st_sub = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=10, alignment=TA_CENTER, spaceAfter=5*mm, textColor=colors.grey)
    st_h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12, spaceBefore=6*mm, spaceAfter=3*mm,
                           textColor=colors.HexColor("#1a3a5c"), borderWidth=0, borderPadding=0)
    st_corpo = ParagraphStyle("Corpo", parent=styles["Normal"], fontSize=9, alignment=TA_JUSTIFY, leading=13, spaceAfter=2*mm)
    st_small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=8, textColor=colors.grey)

    nome_empresa = cfg.nome_fantasia or cfg.nome_empresa or "EMPRESA"

    # === CABEÇALHO ===
    els.append(Paragraph(f"<b>{nome_empresa}</b>", ParagraphStyle("Emp", parent=styles["Normal"], fontSize=8, alignment=TA_CENTER)))
    if cfg.cnpj:
        els.append(Paragraph(f"CNPJ: {cfg.cnpj} — {cfg.endereco_completo}", st_small))
    els.append(Spacer(1, 3*mm))
    els.append(Paragraph("DOSSIÊ DE PROPOSTA DE CRÉDITO", st_titulo))
    els.append(Paragraph(f"Proposta #{proposta.id} — {hoje.strftime('%d/%m/%Y')}", st_sub))

    # === 1. DADOS DO CLIENTE ===
    els.append(Paragraph("1. DADOS DO CLIENTE", st_h2))
    dados_cli = [
        ["Nome:", cli.nome_completo, "CPF:", cli.cpf],
        ["Identidade:", cli.doc or "—", "Data Nasc.:", cli.data_nascimento.strftime("%d/%m/%Y") if cli.data_nascimento else "—"],
        ["Profissão:", cli.profissao or "—", "Estado Civil:", cli.get_estado_civil_display() if cli.estado_civil else "—"],
        ["Telefone:", cli.telefone or "—", "E-mail:", cli.email or "—"],
        ["Endereço:", f"{cli.logradouro}, {cli.numero} — {cli.bairro}", "Cidade/UF:", f"{cli.cidade}/{cli.uf}"],
    ]
    t = Table(dados_cli, colWidths=[70, 170, 70, 170])
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    els.append(t)

    # === 2. RENDA E PATRIMÔNIO ===
    els.append(Paragraph("2. RENDA E PATRIMÔNIO", st_h2))
    renda_bruta = "—"
    renda_liquida = "—"
    from clientes.models import DocumentoCliente
    comp = DocumentoCliente.objects.filter(cliente=cli, tipo="COMP_RENDA").order_by("-ano_referencia", "-mes_referencia").first()
    if comp and comp.renda_bruta:
        renda_bruta = f"R$ {comp.renda_bruta:,.2f}"
    if comp and comp.renda_liquida:
        renda_liquida = f"R$ {comp.renda_liquida:,.2f}"

    dados_renda = [
        ["Renda Bruta (comprovante):", renda_bruta, "Renda Líquida (comprovante):", renda_liquida],
        ["Renda Cadastro:", f"R$ {cli.renda_mensal:,.2f}" if cli.renda_mensal else "—",
         "Outros Rendimentos:", f"R$ {cli.outros_rendimentos:,.2f}" if cli.outros_rendimentos else "—"],
    ]
    t2 = Table(dados_renda, colWidths=[120, 120, 120, 120])
    t2.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    els.append(t2)

    # Bens
    bens_m = cli.bens_moveis.all()
    bens_i = cli.bens_imoveis.all()
    if bens_m or bens_i:
        els.append(Spacer(1, 2*mm))
        patrimonio = []
        for bm in bens_m:
            patrimonio.append(f"Veículo: {bm.get_tipo_display()} — {bm.descricao} {f'(Placa: {bm.placa})' if bm.placa else ''}")
        for bi in bens_i:
            patrimonio.append(f"Imóvel: {bi.get_tipo_display()} — {bi.endereco_completo} {f'(Mat: {bi.matricula})' if bi.matricula else ''}")
        for p in patrimonio:
            els.append(Paragraph(f"• {p}", st_corpo))

    # === 3. CONSULTA DE CRÉDITO ===
    els.append(Paragraph("3. CONSULTA DE CRÉDITO", st_h2))
    consulta = ConsultaCredito.objects.filter(cliente=cli).order_by("-criado_em").first()
    if consulta:
        els.append(Paragraph(f"<b>Resultado:</b> {consulta.get_status_display()} — Data: {consulta.criado_em.strftime('%d/%m/%Y')}", st_corpo))
        if consulta.observacoes:
            els.append(Paragraph(f"<b>Observações:</b> {consulta.observacoes}", st_corpo))
        restricoes = consulta.restricoes.all()
        if restricoes:
            dados_rest = [["CNPJ", "Empresa", "Valor", "Descrição"]]
            for r in restricoes:
                dados_rest.append([r.cnpj_credor or "—", r.nome_credor or "—", f"R$ {r.valor:,.2f}", r.descricao or "—"])
            dados_rest.append(["", "", f"TOTAL: R$ {consulta.total_restricoes:,.2f}", ""])
            tr = Table(dados_rest, colWidths=[80, 130, 80, 190])
            tr.setStyle(TableStyle([
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f8d7da")),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
                ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            els.append(tr)
    else:
        els.append(Paragraph("Nenhuma consulta de crédito registrada.", st_corpo))

    # === 4. SIMULAÇÃO DO EMPRÉSTIMO ===
    els.append(Paragraph("4. SIMULAÇÃO DO EMPRÉSTIMO", st_h2))
    from .services import simular
    try:
        _, parc, total, _, tabela = simular(proposta.valor_solicitado, proposta.qtd_parcelas, proposta.taxa_juros, proposta.primeiro_vencimento)
    except Exception:
        parc = Decimal("0")
        total = Decimal("0")
        tabela = []

    dados_sim = [
        ["Valor Solicitado (líquido):", f"R$ {proposta.valor_solicitado:,.2f}", "Taxa de Juros:", f"{proposta.taxa_juros}% a.m."],
        ["Finalidade:", proposta.get_finalidade_display() if proposta.finalidade else "—", "Parcelas:", str(proposta.qtd_parcelas)],
        ["IOF:", f"R$ {proposta.valor_iof:,.2f}" if proposta.tem_iof and proposta.valor_iof else "Não",
         "Débitos Extras:", f"R$ {proposta.valor_debitos_extras:,.2f}" if proposta.valor_debitos_extras and proposta.valor_debitos_extras > 0 else "—"],
        ["Valor Bruto (contrato):", f"R$ {proposta.valor_bruto:,.2f}" if proposta.valor_bruto and proposta.valor_bruto > 0 else "—", "Valor da Parcela:", f"R$ {parc:,.2f}"],
        ["Total a Pagar:", f"R$ {total:,.2f}", "1º Vencimento:", proposta.primeiro_vencimento.strftime("%d/%m/%Y")],
    ]
    ts = Table(dados_sim, colWidths=[120, 120, 120, 120])
    ts.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    els.append(ts)

    # === 5. GARANTIAS ===
    els.append(Paragraph("5. GARANTIAS", st_h2))
    garantias = proposta.garantias.select_related("avalista", "bem_movel", "bem_imovel").all()
    if garantias:
        for g in garantias:
            els.append(Paragraph(f"• {g}", st_corpo))
    else:
        els.append(Paragraph("Nenhuma garantia cadastrada.", st_corpo))

    # === 6. SCORE DE CRÉDITO ===
    if proposta.score_calculado is not None:
        els.append(Paragraph("6. SCORE DE CRÉDITO", st_h2))
        els.append(Paragraph(f"<b>Score: {proposta.score_calculado}/1000</b>", st_corpo))
        if proposta.score_detalhamento and "fatores" in proposta.score_detalhamento:
            for f in proposta.score_detalhamento["fatores"]:
                els.append(Paragraph(f"• {f['nome']}: {f['nota']}/1000 → {f['pontos']}pts — {f['detalhe']}", st_corpo))

    # === 7. VOTAÇÃO DO COMITÊ ===
    els.append(Paragraph("7. VOTAÇÃO DO COMITÊ", st_h2))
    votos = VotoComite.objects.filter(proposta=proposta).select_related("usuario")
    if votos:
        dados_votos = [["Membro", "Decisão", "Data", "Observações"]]
        for v in votos:
            dados_votos.append([
                v.usuario.get_full_name() or v.usuario.username,
                v.decisao,
                v.data_voto.strftime("%d/%m/%Y %H:%M") if v.data_voto else "—",
                v.observacoes or "—",
            ])
        tv = Table(dados_votos, colWidths=[120, 70, 100, 190])
        tv.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d4edda")),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        els.append(tv)
    else:
        els.append(Paragraph("Nenhum voto registrado.", st_corpo))

    # === RODAPÉ ===
    els.append(Spacer(1, 10*mm))
    if cfg.rodape_linha1:
        els.append(Paragraph(cfg.rodape_linha1, st_small))
    if cfg.rodape_linha2:
        els.append(Paragraph(cfg.rodape_linha2, st_small))

    doc.build(els)
    return response


# ==============================================================================
# ANTECIPAÇÃO DE RECEBÍVEIS VIA ESTEIRA
# ==============================================================================

@login_required
def nova_antecipacao(request):
    """Cria proposta de antecipação de recebíveis que passa pela esteira."""
    if request.method == "POST":
        from .views import to_decimal

        cliente_id = request.POST.get("cliente")
        valor = to_decimal(request.POST.get("valor", "0"))
        taxa = Decimal(request.POST.get("taxa", "0").replace(",", "."))
        qtd = int(request.POST.get("qtd_parcelas", 1))
        vencimento = request.POST.get("vencimento", "")
        tipo_recebiveis = request.POST.get("tipo_recebiveis", "")
        obs = request.POST.get("observacoes", "")

        if not cliente_id or valor <= 0 or not vencimento:
            messages.error(request, "Preencha todos os campos.")
            return redirect("emprestimos:nova_antecipacao")

        with transaction.atomic():
            proposta = PropostaEmprestimo.objects.create(
                cliente_id=int(cliente_id),
                valor_solicitado=valor,
                qtd_parcelas=qtd,
                taxa_juros=taxa,
                primeiro_vencimento=vencimento,
                finalidade="ANTECIPACAO_RECEBIVEIS",
                usuario_solicitante=request.user,
                observacoes=f"Antecipação de Recebíveis ({tipo_recebiveis}). {obs}",
                status="CAPTACAO",
            )

            etapa = EtapaProposta.objects.create(proposta=proposta, etapa="CAPTACAO")
            _criar_checklist_para_etapa(etapa)

            messages.success(request, f"Proposta de antecipação criada (#{proposta.id}).")
            return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)

    clientes = Cliente.objects.all().order_by("nome_completo")
    return render(request, "emprestimos/esteira/nova_antecipacao.html", {
        "clientes": clientes,
    })
