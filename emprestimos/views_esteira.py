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
    ParcelaStatus, ContratoLog
)
from .services import simular
from .services_analise import gerar_dossie_cliente
from clientes.models import Cliente
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
    "COMITE": [
        ("Apresentação do caso ao comitê", True),
        ("Votos registrados", True),
        ("Ata da reunião anexada", False),
    ],
    "FORMALIZACAO": [
        ("Contrato assinado pelo cliente", True),
        ("Contrato assinado pela empresa", True),
        ("Nota promissória emitida", False),
        ("Garantias registradas", False),
    ],
    "LIBERACAO": [
        ("Valor conferido", True),
        ("Dados bancários confirmados", True),
        ("Liberação autorizada pelo gerente", True),
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

    return render(request, "emprestimos/esteira/detalhe.html", {
        "proposta": proposta,
        "etapa_ativa": etapa_ativa,
        "checklist": checklist,
        "todas_etapas": todas_etapas,
        "dossie": dossie,
        "parceiros": parceiros,
        "pode_atuar": pode_atuar,
        "progresso": progresso,
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
# 6. SIMULAÇÃO AJAX (chamada pelo formulário de nova proposta)
# ==============================================================================

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
