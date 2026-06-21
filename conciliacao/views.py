"""
Views da Conciliação Bancária.
"""
from decimal import Decimal

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone

from financeiro.models import Transacao
from .models import ContaBancaria, ExtratoImportado, LancamentoExtrato
from .parsers import parse_ofx, parse_csv
from .conciliador import conciliar_automatico, sugestoes_para_lancamento


# ==============================================================================
# 1. DASHBOARD
# ==============================================================================

@login_required
def dashboard(request):
    """Visão geral das contas bancárias e últimos extratos."""
    contas = ContaBancaria.objects.filter(ativo=True)

    # Para cada conta, pega os extratos vinculados
    for conta in contas:
        conta.ultimos_extratos = ExtratoImportado.objects.filter(conta=conta).order_by("-importado_em")[:3]

    # Contadores gerais
    total_pendentes = LancamentoExtrato.objects.filter(status="PENDENTE").count()
    total_conciliados = LancamentoExtrato.objects.filter(
        status__in=["CONCILIADO", "MANUAL", "CRIADO", "IGNORADO"]
    ).count()

    return render(request, "conciliacao/dashboard.html", {
        "contas": contas,
        "total_pendentes": total_pendentes,
        "total_conciliados": total_conciliados,
    })


# ==============================================================================
# 1B. EXTRATO POR CONTA (com filtro de período)
# ==============================================================================

@login_required
def extrato_conta(request, conta_id):
    """Extrato de uma conta bancária com filtro de período."""
    from datetime import date, timedelta
    from dateutil.relativedelta import relativedelta

    conta = get_object_or_404(ContaBancaria, id=conta_id)

    # Determina período
    periodo = request.GET.get("periodo", "mes")
    data_inicio_str = request.GET.get("data_inicio", "")
    data_fim_str = request.GET.get("data_fim", "")

    hoje = date.today()

    if periodo == "custom" and data_inicio_str and data_fim_str:
        data_inicio = date.fromisoformat(data_inicio_str)
        data_fim = date.fromisoformat(data_fim_str)
    elif periodo == "7dias":
        data_inicio = hoje - timedelta(days=7)
        data_fim = hoje
    elif periodo == "semana":
        data_inicio = hoje - timedelta(days=hoje.weekday())  # segunda
        data_fim = hoje
    elif periodo == "mes":
        data_inicio = hoje.replace(day=1)
        data_fim = hoje
    elif periodo == "mes_anterior":
        primeiro_mes_atual = hoje.replace(day=1)
        data_fim = primeiro_mes_atual - timedelta(days=1)
        data_inicio = data_fim.replace(day=1)
    else:
        data_inicio = hoje.replace(day=1)
        data_fim = hoje

    # Busca lançamentos no período
    lancamentos = LancamentoExtrato.objects.filter(
        extrato__conta=conta,
        data__gte=data_inicio,
        data__lte=data_fim,
    ).select_related("transacao", "extrato").order_by("data", "id")

    # Calcula saldo anterior (tudo antes do período)
    from django.db.models import Sum
    saldo_anterior_agg = LancamentoExtrato.objects.filter(
        extrato__conta=conta,
        data__lt=data_inicio,
        status__in=["CONCILIADO", "MANUAL", "CRIADO", "IGNORADO"],
    ).aggregate(s=Sum("valor"))["s"] or Decimal("0.00")
    saldo_anterior = conta.saldo_inicial + saldo_anterior_agg

    # Totais e saldo corrido
    total_creditos = Decimal("0.00")
    total_debitos = Decimal("0.00")
    saldo_corrido = saldo_anterior
    lancamentos_com_saldo = []

    for l in lancamentos:
        if l.tipo == "C":
            total_creditos += abs(l.valor)
            saldo_corrido += abs(l.valor)
        else:
            total_debitos += abs(l.valor)
            saldo_corrido -= abs(l.valor)
        l.saldo_corrido = saldo_corrido
        lancamentos_com_saldo.append(l)

    saldo_periodo = total_creditos - total_debitos
    saldo_final = saldo_anterior + saldo_periodo

    # Extratos importados desta conta
    extratos = ExtratoImportado.objects.filter(conta=conta).order_by("-importado_em")[:10]

    return render(request, "conciliacao/extrato_conta.html", {
        "conta": conta,
        "lancamentos": lancamentos_com_saldo,
        "periodo": periodo,
        "data_inicio": data_inicio,
        "data_fim": data_fim,
        "saldo_anterior": saldo_anterior,
        "total_creditos": total_creditos,
        "total_debitos": total_debitos,
        "saldo_periodo": saldo_periodo,
        "saldo_final": saldo_final,
        "extratos": extratos,
        "total_lancamentos": len(lancamentos_com_saldo),
    })


@login_required
def extrato_conta_pdf(request, conta_id):
    """Gera PDF do extrato de uma conta por período."""
    from django.http import HttpResponse
    from datetime import date, timedelta
    from dateutil.relativedelta import relativedelta
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    conta = get_object_or_404(ContaBancaria, id=conta_id)

    # Mesmo filtro de período
    periodo = request.GET.get("periodo", "mes")
    data_inicio_str = request.GET.get("data_inicio", "")
    data_fim_str = request.GET.get("data_fim", "")
    hoje = date.today()

    if periodo == "custom" and data_inicio_str and data_fim_str:
        data_inicio = date.fromisoformat(data_inicio_str)
        data_fim = date.fromisoformat(data_fim_str)
    elif periodo == "7dias":
        data_inicio = hoje - timedelta(days=7)
        data_fim = hoje
    elif periodo == "semana":
        data_inicio = hoje - timedelta(days=hoje.weekday())
        data_fim = hoje
    elif periodo == "mes":
        data_inicio = hoje.replace(day=1)
        data_fim = hoje
    elif periodo == "mes_anterior":
        primeiro = hoje.replace(day=1)
        data_fim = primeiro - timedelta(days=1)
        data_inicio = data_fim.replace(day=1)
    else:
        data_inicio = hoje.replace(day=1)
        data_fim = hoje

    lancamentos = LancamentoExtrato.objects.filter(
        extrato__conta=conta,
        data__gte=data_inicio,
        data__lte=data_fim,
    ).order_by("data", "id")

    # Saldo anterior
    from django.db.models import Sum
    saldo_anterior_agg = LancamentoExtrato.objects.filter(
        extrato__conta=conta,
        data__lt=data_inicio,
        status__in=["CONCILIADO", "MANUAL", "CRIADO", "IGNORADO"],
    ).aggregate(s=Sum("valor"))["s"] or Decimal("0.00")
    saldo_anterior = conta.saldo_inicial + saldo_anterior_agg

    fmt = lambda v: f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    total_creditos = Decimal("0.00")
    total_debitos = Decimal("0.00")
    saldo_corrido = saldo_anterior
    linhas = []

    for l in lancamentos:
        if l.tipo == "C":
            total_creditos += abs(l.valor)
            saldo_corrido += abs(l.valor)
            cred = fmt(abs(l.valor))
            deb = ""
        else:
            total_debitos += abs(l.valor)
            saldo_corrido -= abs(l.valor)
            cred = ""
            deb = fmt(abs(l.valor))
        linhas.append([
            l.data.strftime("%d/%m/%Y"),
            l.descricao[:55],
            l.documento[:15],
            cred, deb,
            fmt(saldo_corrido),
        ])

    saldo_final = saldo_anterior + total_creditos - total_debitos

    # PDF
    response = HttpResponse(content_type="application/pdf")
    nome = f"extrato_{conta.nome}_{data_inicio}_{data_fim}.pdf".replace(" ", "_")
    response["Content-Disposition"] = f'attachment; filename="{nome}"'

    doc = SimpleDocTemplate(response, pagesize=landscape(A4), topMargin=15*mm, bottomMargin=15*mm)
    styles = getSampleStyleSheet()
    els = []

    titulo = ParagraphStyle("T", parent=styles["Title"], fontSize=14, spaceAfter=4)
    info = ParagraphStyle("I", parent=styles["Normal"], fontSize=9, spaceAfter=3)

    banco_nome = dict(ContaBancaria.BANCOS_COMUNS).get(conta.banco, conta.banco)
    els.append(Paragraph(f"Extrato Bancário — {conta.nome}", titulo))
    els.append(Paragraph(
        f"Banco: {banco_nome} | Ag: {conta.agencia} | Conta: {conta.conta} | "
        f"Período: {data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}",
        info,
    ))
    els.append(Spacer(1, 4*mm))

    # Resumo
    res = [
        ["Saldo Anterior", "Créditos", "Débitos", "Saldo Período", "Saldo Final"],
        [fmt(saldo_anterior), fmt(total_creditos), fmt(total_debitos),
         fmt(total_creditos - total_debitos), fmt(saldo_final)],
    ]
    rt = Table(res, colWidths=[45*mm]*5)
    rt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d6efd")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#f0f0f0")),
    ]))
    els.append(rt)
    els.append(Spacer(1, 6*mm))

    # Tabela
    header = ["Data", "Descrição", "Doc", "Crédito", "Débito", "Saldo"]
    data_tab = [header] + linhas
    cw = [22*mm, 110*mm, 25*mm, 30*mm, 30*mm, 32*mm]
    tab = Table(data_tab, colWidths=cw, repeatRows=1)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#343a40")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("ALIGN", (3, 0), (5, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    for i, row in enumerate(linhas, start=1):
        if row[3]:
            cmds.append(("TEXTCOLOR", (3, i), (3, i), colors.HexColor("#198754")))
        if row[4]:
            cmds.append(("TEXTCOLOR", (4, i), (4, i), colors.HexColor("#dc3545")))
    tab.setStyle(TableStyle(cmds))
    els.append(tab)

    els.append(Spacer(1, 6*mm))
    rod = ParagraphStyle("R", parent=styles["Normal"], fontSize=7, textColor=colors.grey)
    els.append(Paragraph(
        f"Gerado em {timezone.now().strftime('%d/%m/%Y %H:%M')} | {len(linhas)} lançamentos",
        rod,
    ))

    doc.build(els)
    return response


# ==============================================================================
# 2. CONTAS BANCÁRIAS (CRUD simples)
# ==============================================================================

@login_required
def criar_conta(request):
    """Cria nova conta bancária."""
    if request.method == "POST":
        try:
            ContaBancaria.objects.create(
                nome=request.POST.get("nome", "").strip(),
                banco=request.POST.get("banco", "999"),
                agencia=request.POST.get("agencia", "").strip(),
                conta=request.POST.get("conta", "").strip(),
                tipo=request.POST.get("tipo", "CC"),
                saldo_inicial=Decimal(
                    request.POST.get("saldo_inicial", "0").replace(".", "").replace(",", ".")
                ),
            )
            messages.success(request, "Conta bancária criada!")
            return redirect("conciliacao:dashboard")
        except Exception as e:
            messages.error(request, f"Erro: {e}")

    return render(request, "conciliacao/criar_conta.html", {
        "bancos": ContaBancaria.BANCOS_COMUNS,
    })


# ==============================================================================
# 3. IMPORTAÇÃO DE EXTRATO
# ==============================================================================

@login_required
def importar_extrato(request):
    """Importa arquivo OFX ou CSV."""
    contas = ContaBancaria.objects.filter(ativo=True)

    if request.method == "POST":
        conta_id = request.POST.get("conta_id")
        arquivo = request.FILES.get("arquivo")

        if not arquivo or not conta_id:
            messages.error(request, "Selecione uma conta e um arquivo.")
            return render(request, "conciliacao/importar.html", {"contas": contas})

        conta = get_object_or_404(ContaBancaria, id=conta_id)
        nome_arquivo = arquivo.name.lower()

        try:
            conteudo = arquivo.read().decode("utf-8", errors="replace")
        except Exception:
            conteudo = arquivo.read().decode("latin-1", errors="replace")

        try:
            if nome_arquivo.endswith(".ofx"):
                lancamentos = parse_ofx(conteudo)
                formato = "OFX"
            elif nome_arquivo.endswith(".csv"):
                # Pega config de colunas do formulário
                lancamentos = parse_csv(
                    conteudo,
                    col_data=int(request.POST.get("col_data", 0)),
                    col_descricao=int(request.POST.get("col_descricao", 1)),
                    col_valor=int(request.POST.get("col_valor", 2)),
                    col_documento=int(request.POST.get("col_documento", -1)),
                    formato_data=request.POST.get("formato_data", "%d/%m/%Y"),
                    separador=request.POST.get("separador", ";"),
                    pular_linhas=int(request.POST.get("pular_linhas", 1)),
                )
                formato = "CSV"
            else:
                messages.error(request, "Formato não suportado. Use .ofx ou .csv")
                return render(request, "conciliacao/importar.html", {"contas": contas})

            if not lancamentos:
                messages.warning(request, "Nenhum lançamento encontrado no arquivo.")
                return render(request, "conciliacao/importar.html", {"contas": contas})

            # Cria o registro do extrato
            datas = [l.data for l in lancamentos]
            extrato = ExtratoImportado.objects.create(
                conta=conta,
                arquivo_nome=arquivo.name,
                formato=formato,
                status="IMPORTADO",
                total_lancamentos=len(lancamentos),
                periodo_inicio=min(datas),
                periodo_fim=max(datas),
                importado_por=request.user,
            )

            # Cria os lançamentos
            objs = []
            for l in lancamentos:
                objs.append(LancamentoExtrato(
                    extrato=extrato,
                    data=l.data,
                    valor=l.valor,
                    descricao=l.descricao,
                    documento=l.documento,
                    tipo=l.tipo,
                ))
            LancamentoExtrato.objects.bulk_create(objs)

            # Roda conciliação automática
            resultado = conciliar_automatico(extrato)

            msg = (
                f"Importados {len(lancamentos)} lançamentos. "
                f"Conciliação: {resultado['exatos']} exatos, "
                f"{resultado['sugeridos']} sugeridos, "
                f"{resultado['pendentes']} pendentes."
            )
            messages.success(request, msg)
            return redirect("conciliacao:detalhe_extrato", extrato_id=extrato.id)

        except Exception as e:
            messages.error(request, f"Erro ao processar arquivo: {e}")

    return render(request, "conciliacao/importar.html", {"contas": contas})


# ==============================================================================
# 4. DETALHE DO EXTRATO (tela de conciliação)
# ==============================================================================

@login_required
def detalhe_extrato(request, extrato_id):
    """Tela principal de conciliação — mostra lançamentos com saldo corrido."""
    extrato = get_object_or_404(
        ExtratoImportado.objects.select_related("conta"),
        id=extrato_id,
    )
    lancamentos = extrato.lancamentos.select_related("transacao").all()

    # Filtro por status
    filtro = request.GET.get("filtro", "todos")
    if filtro == "pendentes":
        lancamentos = lancamentos.filter(status="PENDENTE")
    elif filtro == "conciliados":
        lancamentos = lancamentos.filter(status__in=["CONCILIADO", "MANUAL", "CRIADO", "IGNORADO"])

    # Calcula totais e saldo corrido
    todos_lancamentos = extrato.lancamentos.all().order_by("data", "id")
    total_creditos = Decimal("0.00")
    total_debitos = Decimal("0.00")

    for l in todos_lancamentos:
        if l.tipo == "C":
            total_creditos += abs(l.valor)
        else:
            total_debitos += abs(l.valor)

    saldo_periodo = total_creditos - total_debitos
    saldo_inicial = extrato.conta.saldo_inicial
    saldo_final = saldo_inicial + saldo_periodo

    # Saldo corrido nos lançamentos exibidos
    saldo_corrido = saldo_inicial
    lancamentos_com_saldo = []
    for l in lancamentos.order_by("data", "id"):
        if l.tipo == "C":
            saldo_corrido += abs(l.valor)
        else:
            saldo_corrido -= abs(l.valor)
        l.saldo_corrido = saldo_corrido
        lancamentos_com_saldo.append(l)

    # Contadores
    total = extrato.total_lancamentos
    conciliados = extrato.total_conciliados
    pendentes = total - conciliados

    return render(request, "conciliacao/detalhe_extrato.html", {
        "extrato": extrato,
        "lancamentos": lancamentos_com_saldo,
        "filtro": filtro,
        "total": total,
        "conciliados": conciliados,
        "pendentes": pendentes,
        "total_creditos": total_creditos,
        "total_debitos": total_debitos,
        "saldo_periodo": saldo_periodo,
        "saldo_inicial": saldo_inicial,
        "saldo_final": saldo_final,
    })


# ==============================================================================
# 5. AÇÕES DE CONCILIAÇÃO
# ==============================================================================

@login_required
def conciliar_manual(request, lancamento_id):
    """Concilia manualmente um lançamento com uma transação do sistema."""
    lanc = get_object_or_404(LancamentoExtrato, id=lancamento_id)

    if request.method == "POST":
        transacao_id = request.POST.get("transacao_id")

        if transacao_id:
            transacao = get_object_or_404(Transacao, id=transacao_id)
            lanc.transacao = transacao
            lanc.status = "MANUAL"
            lanc.conciliado_por = request.user
            lanc.conciliado_em = timezone.now()
            lanc.save()
            lanc.extrato.atualizar_contadores()
            messages.success(request, f"Lançamento conciliado com: {transacao.descricao[:50]}")
        else:
            messages.error(request, "Selecione uma transação.")

        return redirect("conciliacao:detalhe_extrato", extrato_id=lanc.extrato_id)

    # GET — mostra candidatas
    sugestoes = sugestoes_para_lancamento(lanc)
    return render(request, "conciliacao/conciliar_manual.html", {
        "lancamento": lanc,
        "sugestoes": sugestoes,
    })


@login_required
def confirmar_sugestao(request, lancamento_id):
    """Confirma a sugestão de match que o motor encontrou."""
    lanc = get_object_or_404(LancamentoExtrato, id=lancamento_id)

    if lanc.transacao and lanc.status == "PENDENTE":
        lanc.status = "MANUAL"
        lanc.conciliado_por = request.user
        lanc.conciliado_em = timezone.now()
        lanc.save()
        lanc.extrato.atualizar_contadores()
        messages.success(request, "Sugestão confirmada!")

    return redirect("conciliacao:detalhe_extrato", extrato_id=lanc.extrato_id)


@login_required
def ignorar_lancamento(request, lancamento_id):
    """Marca um lançamento como ignorado (não precisa conciliar)."""
    lanc = get_object_or_404(LancamentoExtrato, id=lancamento_id)
    lanc.status = "IGNORADO"
    lanc.save()
    lanc.extrato.atualizar_contadores()
    messages.info(request, "Lançamento ignorado.")
    return redirect("conciliacao:detalhe_extrato", extrato_id=lanc.extrato_id)


@login_required
def criar_transacao_de_lancamento(request, lancamento_id):
    """Cria uma transação no sistema a partir de um lançamento do extrato."""
    lanc = get_object_or_404(LancamentoExtrato, id=lancamento_id)

    if request.method == "POST":
        tipo = request.POST.get("tipo", "OUTROS")
        descricao = request.POST.get("descricao", lanc.descricao)

        transacao = Transacao.objects.create(
            tipo=tipo,
            valor=lanc.valor,
            descricao=descricao,
            data=timezone.make_aware(
                timezone.datetime.combine(lanc.data, timezone.datetime.min.time())
            ),
            usuario=request.user,
        )

        lanc.transacao = transacao
        lanc.status = "CRIADO"
        lanc.conciliado_por = request.user
        lanc.conciliado_em = timezone.now()
        lanc.save()
        lanc.extrato.atualizar_contadores()

        messages.success(request, f"Transação criada e conciliada: {descricao[:50]}")
        return redirect("conciliacao:detalhe_extrato", extrato_id=lanc.extrato_id)

    return render(request, "conciliacao/criar_transacao.html", {
        "lancamento": lanc,
        "tipos": Transacao.TIPO_CHOICES,
    })


@login_required
def reconciliar(request, extrato_id):
    """Re-executa a conciliação automática nos lançamentos pendentes."""
    extrato = get_object_or_404(ExtratoImportado, id=extrato_id)
    resultado = conciliar_automatico(extrato)
    msg = (
        f"Re-conciliação: {resultado['exatos']} exatos, "
        f"{resultado['sugeridos']} sugeridos, "
        f"{resultado['pendentes']} pendentes."
    )
    messages.info(request, msg)
    return redirect("conciliacao:detalhe_extrato", extrato_id=extrato.id)


# ==============================================================================
# 8. EXPORTAR EXTRATO EM PDF
# ==============================================================================

@login_required
def exportar_pdf(request, extrato_id):
    """Gera PDF do extrato com saldo corrido e totais."""
    from django.http import HttpResponse
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

    extrato = get_object_or_404(
        ExtratoImportado.objects.select_related("conta"),
        id=extrato_id,
    )
    lancamentos = extrato.lancamentos.all().order_by("data", "id")

    # Calcula totais
    total_creditos = Decimal("0.00")
    total_debitos = Decimal("0.00")
    saldo_corrido = extrato.conta.saldo_inicial

    linhas_dados = []
    for l in lancamentos:
        if l.tipo == "C":
            total_creditos += abs(l.valor)
            saldo_corrido += abs(l.valor)
            credito_str = f"R$ {abs(l.valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            debito_str = ""
        else:
            total_debitos += abs(l.valor)
            saldo_corrido -= abs(l.valor)
            credito_str = ""
            debito_str = f"R$ {abs(l.valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        saldo_str = f"R$ {saldo_corrido:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        status_txt = dict(LancamentoExtrato.STATUS_CHOICES).get(l.status, l.status)

        linhas_dados.append([
            l.data.strftime("%d/%m/%Y"),
            l.descricao[:55],
            l.documento[:15],
            credito_str,
            debito_str,
            saldo_str,
            status_txt,
        ])

    saldo_periodo = total_creditos - total_debitos
    saldo_final = extrato.conta.saldo_inicial + saldo_periodo

    # === GERA PDF ===
    response = HttpResponse(content_type="application/pdf")
    nome_arquivo = f"extrato_{extrato.conta.nome}_{extrato.periodo_inicio}_{extrato.periodo_fim}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{nome_arquivo}"'

    doc = SimpleDocTemplate(response, pagesize=landscape(A4), topMargin=15*mm, bottomMargin=15*mm)
    styles = getSampleStyleSheet()
    elements = []

    # Título
    titulo_style = ParagraphStyle("Titulo", parent=styles["Title"], fontSize=14, spaceAfter=6)
    elements.append(Paragraph(f"Extrato Bancário — {extrato.conta.nome}", titulo_style))

    # Info
    info_style = ParagraphStyle("Info", parent=styles["Normal"], fontSize=9, spaceAfter=3)
    banco_nome = dict(ContaBancaria.BANCOS_COMUNS).get(extrato.conta.banco, extrato.conta.banco)
    elements.append(Paragraph(
        f"Banco: {banco_nome} | Ag: {extrato.conta.agencia} | Conta: {extrato.conta.conta} | "
        f"Período: {extrato.periodo_inicio.strftime('%d/%m/%Y')} a {extrato.periodo_fim.strftime('%d/%m/%Y')}",
        info_style,
    ))
    elements.append(Spacer(1, 4*mm))

    # Resumo
    fmt = lambda v: f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    resumo_data = [
        ["Saldo Inicial", "Total Créditos", "Total Débitos", "Saldo do Período", "Saldo Final"],
        [fmt(extrato.conta.saldo_inicial), fmt(total_creditos), fmt(total_debitos), fmt(saldo_periodo), fmt(saldo_final)],
    ]
    resumo_table = Table(resumo_data, colWidths=[45*mm, 45*mm, 45*mm, 45*mm, 45*mm])
    resumo_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d6efd")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#f0f0f0")),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
    ]))
    elements.append(resumo_table)
    elements.append(Spacer(1, 6*mm))

    # Tabela de lançamentos
    header = ["Data", "Descrição", "Doc", "Crédito", "Débito", "Saldo", "Status"]
    tabela_data = [header] + linhas_dados

    col_widths = [22*mm, 95*mm, 25*mm, 28*mm, 28*mm, 30*mm, 22*mm]
    tabela = Table(tabela_data, colWidths=col_widths, repeatRows=1)

    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#343a40")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("ALIGN", (3, 0), (5, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (6, 0), (6, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]

    # Colorir créditos e débitos
    for i, row in enumerate(linhas_dados, start=1):
        if row[3]:  # Crédito
            style_cmds.append(("TEXTCOLOR", (3, i), (3, i), colors.HexColor("#198754")))
        if row[4]:  # Débito
            style_cmds.append(("TEXTCOLOR", (4, i), (4, i), colors.HexColor("#dc3545")))

    tabela.setStyle(TableStyle(style_cmds))
    elements.append(tabela)

    # Rodapé
    elements.append(Spacer(1, 6*mm))
    rodape_style = ParagraphStyle("Rodape", parent=styles["Normal"], fontSize=7, textColor=colors.grey)
    elements.append(Paragraph(
        f"Gerado em {timezone.now().strftime('%d/%m/%Y %H:%M')} | "
        f"Arquivo: {extrato.arquivo_nome} | "
        f"Total de lançamentos: {extrato.total_lancamentos}",
        rodape_style,
    ))

    doc.build(elements)
    return response
