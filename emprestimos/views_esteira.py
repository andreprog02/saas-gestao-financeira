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
                )

                # Garantias
                # Cheques
                cheque_numero = request.POST.get("cheque_numero", "").strip()
                if cheque_numero:
                    cheque_valor_str = request.POST.get("cheque_valor", "0").replace(".", "").replace(",", ".")
                    GarantiaProposta.objects.create(
                        proposta=proposta,
                        tipo="CHEQUE",
                        cheque_banco=request.POST.get("cheque_banco", ""),
                        cheque_numero=cheque_numero,
                        cheque_valor=Decimal(cheque_valor_str) if cheque_valor_str else None,
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
        HIERARQUIA = {"OPERADOR": 1, "ANALISTA": 2, "GERENTE": 3, "ADMIN": 4}
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

    # Contrato formalizado (se estiver na FORMALIZACAO)
    contrato_formal = ContratoFormalizado.objects.filter(proposta=proposta).first()
     # Checklist da formalização para exibir na liberação
    checklist_formalizacao = []
    if etapa_ativa and etapa_ativa.etapa == "LIBERACAO":
        etapa_form = todas_etapas.filter(etapa="FORMALIZACAO", resultado="APROVADO").last()
        if etapa_form:
            checklist_formalizacao = etapa_form.checklist.all()

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
        "docs_tipos": DocumentoCliente.TIPO_CHOICES,
        "contratos_abertos": contratos_abertos,
        "historico_pagamentos": historico_pagamentos,
        "docs_tipos": DocumentoCliente.TIPO_CHOICES,
        "contrato_formal": contrato_formal,
        "is_formalizacao": is_formalizacao,
        "garantias": garantias,
        "checklist_formalizacao": checklist_formalizacao,
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
    """Devolve a proposta para a etapa anterior."""
    proposta = get_object_or_404(PropostaEmprestimo, id=proposta_id)
    etapa_ativa = proposta.etapa_atual_obj

    if not etapa_ativa or request.method != "POST":
        return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)

    motivo = request.POST.get("motivo", "Sem motivo informado")
    anterior = _etapa_anterior(etapa_ativa.etapa)

    if not anterior:
        messages.error(request, "Não é possível devolver da primeira etapa.")
        return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)

    # Finaliza etapa atual como devolvida
    etapa_ativa.resultado = EtapaProposta.Resultado.DEVOLVIDO
    etapa_ativa.ativa = False
    etapa_ativa.finalizado_em = timezone.now()
    etapa_ativa.responsavel = request.user
    etapa_ativa.parecer = f"DEVOLVIDO: {motivo}"
    etapa_ativa.save()

    # Cria nova etapa na posição anterior
    nova = EtapaProposta.objects.create(
        proposta=proposta,
        etapa=anterior,
    )
    _criar_checklist_para_etapa(nova)
    proposta.status = anterior
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
    """Registra voto do membro do comitê com validação de senha."""
    from .models import VotoComite
    from django.contrib.auth import authenticate

    proposta = get_object_or_404(PropostaEmprestimo, id=proposta_id)

    if request.method != "POST":
        return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)

    # Valida senha do usuário
    senha = request.POST.get("senha", "")
    user = authenticate(username=request.user.username, password=senha)
    if not user:
        messages.error(request, "Senha inválida. Voto não registrado.")
        return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)

    # Verifica se já votou
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