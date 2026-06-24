from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Min, Sum, Count, Q
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse

# Imports dos outros apps
from emprestimos.models import Emprestimo, Parcela, ParcelaStatus
from recebiveis.models import ContratoRecebivel, ItemRecebivel
from .models import HistoricoCobranca, CartaCobranca

def calcular_acao_sugerida(dias_atraso):
    if dias_atraso <= 5:
        return "Lembrete Amigável", "success"
    elif dias_atraso <= 15:
        return "Contato Verbal / WhatsApp", "info"
    elif dias_atraso <= 30:
        return "Carta de Cobrança", "warning"
    elif dias_atraso <= 60:
        return "Negativação (SPC/Serasa)", "danger"
    else:
        return "Execução Judicial", "dark"

@login_required
def painel_cobranca(request):
    hoje = timezone.localdate()
    lista_devedores = []

    # === 1. BUSCAR EMPRÉSTIMOS EM ATRASO ===
    # Filtra apenas parcelas abertas e vencidas
    todas_parcelas_vencidas = Parcela.objects.filter(
        status=ParcelaStatus.ABERTA, 
        vencimento__lt=hoje
    ).select_related('emprestimo', 'emprestimo__cliente')

    # Correção de Duplicidade: Usar set() para IDs únicos
    emprestimos_ids = set(todas_parcelas_vencidas.values_list('emprestimo_id', flat=True))
    
    for emp_id in emprestimos_ids:
        # Pega as parcelas vencidas DESTE contrato específico
        parcelas = todas_parcelas_vencidas.filter(emprestimo_id=emp_id).order_by('vencimento')
        
        if not parcelas.exists(): continue
            
        emprestimo = parcelas.first().emprestimo
        
        primeiro_vencimento = parcelas.first().vencimento
        valor_total = parcelas.aggregate(Sum('valor'))['valor__sum']
        qtd = parcelas.count()
        
        dias_atraso = (hoje - primeiro_vencimento).days
        acao, cor = calcular_acao_sugerida(dias_atraso)
        
        ultimo_evento = HistoricoCobranca.objects.filter(emprestimo=emprestimo).first()

        lista_devedores.append({
            'tipo': 'EMPRESTIMO',
            'id_contrato': emprestimo.id,
            'codigo': emprestimo.codigo_contrato,
            'cliente': emprestimo.cliente,
            'valor_atraso': valor_total,
            'qtd_itens': f"{qtd} Parcela(s)",
            'dias_atraso': dias_atraso,
            'primeiro_atraso': primeiro_vencimento,
            'acao_sugerida': acao,
            'cor_badge': cor,
            'ultimo_evento': ultimo_evento,
            # DETALHES PARA O MODAL ANALÍTICO
            'itens_detalhe': parcelas, 
            'link_renegociar': 'emprestimos:contrato_detalhe' # Link para tela principal
        })

    # === 2. BUSCAR RECEBÍVEIS EM ATRASO ===
    todos_itens_vencidos = ItemRecebivel.objects.filter(
        status='aberto', 
        vencimento__lt=hoje
    ).select_related('contrato', 'contrato__cliente')
    
    # Correção de Duplicidade
    contratos_rec_ids = set(todos_itens_vencidos.values_list('contrato_id', flat=True))

    for rec_id in contratos_rec_ids:
        itens = todos_itens_vencidos.filter(contrato_id=rec_id).order_by('vencimento')
        if not itens.exists(): continue
        
        contrato_rec = itens.first().contrato
        
        primeiro_vencimento = itens.first().vencimento
        valor_total = itens.aggregate(Sum('valor'))['valor__sum']
        qtd = itens.count()
        tipos = list(itens.values_list('tipo', flat=True).distinct()) 
        tipos_str = ", ".join([t.title() for t in tipos])

        dias_atraso = (hoje - primeiro_vencimento).days
        acao, cor = calcular_acao_sugerida(dias_atraso)
        
        ultimo_evento = HistoricoCobranca.objects.filter(recebivel=contrato_rec).first()

        lista_devedores.append({
            'tipo': 'RECEBIVEL',
            'id_contrato': contrato_rec.id,
            'codigo': contrato_rec.contrato_id,
            'cliente': contrato_rec.cliente,
            'valor_atraso': valor_total,
            'qtd_itens': f"{qtd} ({tipos_str})",
            'dias_atraso': dias_atraso,
            'primeiro_atraso': primeiro_vencimento,
            'acao_sugerida': acao,
            'cor_badge': cor,
            'ultimo_evento': ultimo_evento,
             # DETALHES PARA O MODAL ANALÍTICO
            'itens_detalhe': itens,
            'link_renegociar': 'recebiveis:lista_contratos'
        })

    # Ordenar por dias de atraso (maior para menor)
    lista_devedores.sort(key=lambda x: x['dias_atraso'], reverse=True)

    return render(request, 'cobranca/painel.html', {'lista': lista_devedores})

@login_required
def registrar_evento(request):
    if request.method == 'POST':
        tipo = request.POST.get('tipo_contrato')
        id_contrato = request.POST.get('id_contrato')
        data_evento = request.POST.get('data_evento')
        descricao = request.POST.get('descricao')

        if not descricao or not data_evento:
            messages.error(request, "Preencha a data e a descrição.")
            return redirect('cobranca:painel_cobranca')

        try:
            evento = HistoricoCobranca(
                data_evento=data_evento,
                descricao=descricao,
                usuario=request.user,
                tipo_contrato=tipo
            )

            if tipo == 'EMPRESTIMO':
                emp = Emprestimo.objects.get(id=id_contrato)
                evento.emprestimo = emp
                evento.cliente = emp.cliente
            elif tipo == 'RECEBIVEL':
                rec = ContratoRecebivel.objects.get(id=id_contrato)
                evento.recebivel = rec
                evento.cliente = rec.cliente
            
            evento.save()
            messages.success(request, "Evento registrado com sucesso.")
        except Exception as e:
            messages.error(request, f"Erro ao salvar: {str(e)}")

    return redirect('cobranca:painel_cobranca')

# ==============================================================================
# CARTAS DE COBRANÇA
# ==============================================================================

MESES_EXTENSO = {
    1: "janeiro", 2: "fevereiro", 3: "março", 4: "abril",
    5: "maio", 6: "junho", 7: "julho", 8: "agosto",
    9: "setembro", 10: "outubro", 11: "novembro", 12: "dezembro",
}


@login_required
def listar_inadimplentes_carta(request):
    """Lista clientes com parcelas em atraso para emissão de carta."""
    hoje = timezone.localdate()

    parcelas_vencidas = Parcela.objects.filter(
        status=ParcelaStatus.ABERTA,
        vencimento__lt=hoje,
    ).select_related("emprestimo", "emprestimo__cliente")

    # Agrupa por empréstimo
    emprestimos_ids = set(parcelas_vencidas.values_list("emprestimo_id", flat=True))
    devedores = []

    for emp_id in emprestimos_ids:
        parcelas = parcelas_vencidas.filter(emprestimo_id=emp_id).order_by("vencimento")
        if not parcelas.exists():
            continue

        emp = parcelas.first().emprestimo
        valor_total = parcelas.aggregate(s=Sum("valor"))["s"]
        qtd = parcelas.count()
        dias_atraso = (hoje - parcelas.first().vencimento).days

        devedores.append({
            "emprestimo": emp,
            "cliente": emp.cliente,
            "qtd_parcelas": qtd,
            "valor_total": valor_total,
            "dias_atraso": dias_atraso,
            "primeiro_vencimento": parcelas.first().vencimento,
        })

    devedores.sort(key=lambda d: d["dias_atraso"], reverse=True)

    return render(request, "cobranca/carta_listar.html", {
        "devedores": devedores,
    })


@login_required
def emitir_carta(request, emprestimo_id):
    """Gera a carta de cobrança em PDF e registra no histórico."""
    from decimal import Decimal
    from num2words import num2words

    hoje = timezone.localdate()
    emp = get_object_or_404(Emprestimo, id=emprestimo_id)

    # Busca parcelas em atraso
    parcelas = Parcela.objects.filter(
        emprestimo=emp,
        status=ParcelaStatus.ABERTA,
        vencimento__lt=hoje,
    ).order_by("vencimento")

    if not parcelas.exists():
        messages.warning(request, "Este contrato não possui parcelas em atraso.")
        return redirect("cobranca:carta_listar")

    qtd = parcelas.count()
    valor_total = parcelas.aggregate(s=Sum("valor"))["s"]

    # Gera número sequencial
    ano = hoje.year
    numero = CartaCobranca.proximo_numero(ano)
    numero_fmt = CartaCobranca.gerar_numero_formatado(numero, ano)

    # Salva a carta
    carta = CartaCobranca.objects.create(
        numero=numero,
        ano=ano,
        numero_formatado=numero_fmt,
        cliente=emp.cliente,
        emprestimo=emp,
        qtd_parcelas_atraso=qtd,
        valor_total_atraso=valor_total,
        data_emissao=hoje,
        emitido_por=request.user,
    )

    # Registra evento no histórico de cobrança
    HistoricoCobranca.objects.create(
        cliente=emp.cliente,
        emprestimo=emp,
        usuario=request.user,
        descricao=f"Emissão de carta de cobrança nº {numero_fmt} — "
                  f"{qtd} parcela(s) em atraso totalizando R$ {valor_total:,.2f}",
        tipo_contrato="EMPRESTIMO",
    )

    # Valor por extenso
    valor_centavos = int((valor_total % 1) * 100)
    valor_inteiro = int(valor_total)
    extenso = num2words(valor_inteiro, lang="pt_BR")
    if valor_centavos > 0:
        extenso_centavos = num2words(valor_centavos, lang="pt_BR")
        valor_extenso = f"{extenso} reais e {extenso_centavos} centavos"
    else:
        valor_extenso = f"{extenso} reais"

    # Data por extenso
    mes_extenso = MESES_EXTENSO.get(hoje.month, "")
    data_extenso = f"{hoje.day} de {mes_extenso} de {hoje.year}"

    # === GERA O PDF ===
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm, cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT, TA_JUSTIFY
    from reportlab.lib import colors

    response = HttpResponse(content_type="application/pdf")
    nome_arquivo = f"carta_cobranca_{numero_fmt.replace('/', '_')}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{nome_arquivo}"'

    doc = SimpleDocTemplate(
        response, pagesize=A4,
        topMargin=25*mm, bottomMargin=25*mm,
        leftMargin=25*mm, rightMargin=25*mm,
    )
    styles = getSampleStyleSheet()
    elements = []

    # Estilos customizados
    estilo_nome = ParagraphStyle(
        "Nome", parent=styles["Normal"],
        fontSize=12, fontName="Helvetica-Bold",
    )
    estilo_numero = ParagraphStyle(
        "Numero", parent=styles["Normal"],
        fontSize=11, fontName="Helvetica-Bold", alignment=TA_RIGHT,
    )
    estilo_ref = ParagraphStyle(
        "Ref", parent=styles["Normal"],
        fontSize=11, fontName="Helvetica-Bold", spaceAfter=12,
    )
    estilo_corpo = ParagraphStyle(
        "Corpo", parent=styles["Normal"],
        fontSize=11, leading=18, alignment=TA_JUSTIFY,
        spaceAfter=12,
    )
    estilo_data = ParagraphStyle(
        "Data", parent=styles["Normal"],
        fontSize=11, alignment=TA_RIGHT, spaceBefore=30,
    )
    estilo_assinatura = ParagraphStyle(
        "Assinatura", parent=styles["Normal"],
        fontSize=11, alignment=TA_CENTER, spaceBefore=50,
    )

    # CABEÇALHO: nome à esquerda, número à direita
    cli = emp.cliente
    endereco = f"{cli.logradouro}, {cli.numero}"
    if cli.complemento:
        endereco += f" - {cli.complemento}"
    endereco += f" — {cli.bairro}, {cli.cidade}/{cli.uf} - CEP: {cli.cep}"

    estilo_endereco = ParagraphStyle(
        "Endereco", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#444444"),
    )

    header_data = [[
        [Paragraph(cli.nome_completo, estilo_nome), Paragraph(endereco, estilo_endereco)],
        Paragraph(f"Nº {numero_fmt}", estilo_numero),
    ]]

    header_table = Table(header_data, colWidths=[110*mm, 50*mm])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 1, colors.black),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 15*mm))

    # REF
    elements.append(Paragraph(
        f"<b>Ref:</b> Carta de cobrança referente ao contrato {emp.codigo_contrato}",
        estilo_ref,
    ))
    elements.append(Spacer(1, 5*mm))

    # SAUDAÇÃO
    elements.append(Paragraph("Prezado(a) Senhor(a),", estilo_corpo))

    # CORPO
    valor_formatado = f"R$ {valor_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    texto = (
        f"Informamos que Vossa Senhoria possui <b>{qtd} ({num2words(qtd, lang='pt_BR')}) "
        f"parcela{'s' if qtd > 1 else ''}</b> em atraso referente{'s' if qtd > 1 else ''} "
        f"ao contrato <b>{emp.codigo_contrato}</b>, totalizando o valor de "
        f"<b>{valor_formatado} ({valor_extenso})</b>, não acrescido de juros e multa contratuais."
    )
    elements.append(Paragraph(texto, estilo_corpo))

    texto2 = (
        "Solicitamos a regularização do débito no prazo de <b>15 (quinze) dias</b> "
        "a contar do recebimento desta correspondência. O não pagamento dentro do prazo "
        "estipulado acarretará as seguintes medidas:"
    )
    elements.append(Paragraph(texto2, estilo_corpo))

    # Lista de medidas
    medidas = [
        "Inscrição nos sistemas de proteção ao crédito (SPC/Serasa);",
        "Protesto do título em Cartório de Protestos;",
        "Execução judicial do débito, com acréscimo de custas processuais e honorários advocatícios.",
    ]
    for i, medida in enumerate(medidas, 1):
        elements.append(Paragraph(
            f"&nbsp;&nbsp;&nbsp;&nbsp;<b>{i}.</b> {medida}",
            estilo_corpo,
        ))

    texto3 = (
        "Colocamo-nos à disposição para negociação e esclarecimentos que se fizerem necessários."
    )
    elements.append(Paragraph(texto3, estilo_corpo))

    elements.append(Paragraph("Atenciosamente,", estilo_corpo))

    # DATA E LOCAL
    elements.append(Paragraph(
        f"Rio de Janeiro, {data_extenso}.",
        estilo_data,
    ))

    # ASSINATURA
    elements.append(Spacer(1, 20*mm))
    elements.append(Paragraph("_" * 40, estilo_assinatura))
    elements.append(Paragraph("<b>Diretor Financeiro</b>", estilo_assinatura))

    doc.build(elements)

    messages.success(request, f"Carta de cobrança nº {numero_fmt} emitida para {emp.cliente.nome_completo}.")
    return response


@login_required
def consultar_cartas(request):
    """Lista todas as cartas de cobrança emitidas."""
    cartas = CartaCobranca.objects.select_related("cliente", "emprestimo", "emitido_por").all()

    # Filtro por ano
    ano_filtro = request.GET.get("ano", "")
    if ano_filtro:
        cartas = cartas.filter(ano=int(ano_filtro))

    # Filtro por cliente
    busca = request.GET.get("q", "")
    if busca:
        cartas = cartas.filter(cliente__nome_completo__icontains=busca)

    # Anos disponíveis para filtro
    anos = CartaCobranca.objects.values_list("ano", flat=True).distinct().order_by("-ano")

    return render(request, "cobranca/carta_consultar.html", {
        "cartas": cartas,
        "anos": anos,
        "ano_filtro": ano_filtro,
        "busca": busca,
    })


@login_required
def reimprimir_carta(request, carta_id):
    """Reimprimir uma carta já emitida."""
    from decimal import Decimal
    from num2words import num2words
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_JUSTIFY
    from reportlab.lib import colors

    carta = get_object_or_404(CartaCobranca.objects.select_related("cliente", "emprestimo"), id=carta_id)

    valor = carta.valor_total_atraso
    qtd = carta.qtd_parcelas_atraso
    emp = carta.emprestimo

    valor_centavos = int((valor % 1) * 100)
    valor_inteiro = int(valor)
    extenso = num2words(valor_inteiro, lang="pt_BR")
    if valor_centavos > 0:
        extenso_centavos = num2words(valor_centavos, lang="pt_BR")
        valor_extenso = f"{extenso} reais e {extenso_centavos} centavos"
    else:
        valor_extenso = f"{extenso} reais"

    mes_extenso = MESES_EXTENSO.get(carta.data_emissao.month, "")
    data_extenso = f"{carta.data_emissao.day} de {mes_extenso} de {carta.data_emissao.year}"

    response = HttpResponse(content_type="application/pdf")
    nome_arquivo = f"carta_cobranca_{carta.numero_formatado.replace('/', '_')}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{nome_arquivo}"'

    doc = SimpleDocTemplate(
        response, pagesize=A4,
        topMargin=25*mm, bottomMargin=25*mm,
        leftMargin=25*mm, rightMargin=25*mm,
    )
    styles = getSampleStyleSheet()
    elements = []

    estilo_nome = ParagraphStyle("N", parent=styles["Normal"], fontSize=12, fontName="Helvetica-Bold")
    estilo_numero = ParagraphStyle("Num", parent=styles["Normal"], fontSize=11, fontName="Helvetica-Bold", alignment=TA_RIGHT)
    estilo_ref = ParagraphStyle("R", parent=styles["Normal"], fontSize=11, fontName="Helvetica-Bold", spaceAfter=12)
    estilo_corpo = ParagraphStyle("C", parent=styles["Normal"], fontSize=11, leading=18, alignment=TA_JUSTIFY, spaceAfter=12)
    estilo_data = ParagraphStyle("D", parent=styles["Normal"], fontSize=11, alignment=TA_RIGHT, spaceBefore=30)
    estilo_assinatura = ParagraphStyle("A", parent=styles["Normal"], fontSize=11, alignment=TA_CENTER, spaceBefore=50)

    cli = carta.cliente
    endereco = f"{cli.logradouro}, {cli.numero}"
    if cli.complemento:
        endereco += f" - {cli.complemento}"
    endereco += f" — {cli.bairro}, {cli.cidade}/{cli.uf} - CEP: {cli.cep}"

    estilo_endereco = ParagraphStyle(
        "Endereco", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#444444"),
    )

    header_data = [[
        [Paragraph(cli.nome_completo, estilo_nome), Paragraph(endereco, estilo_endereco)],
        Paragraph(f"Nº {carta.numero_formatado}", estilo_numero),
    ]]
    
    ht = Table(header_data, colWidths=[110*mm, 50*mm])
    ht.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 1, colors.black),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
    ]))
    elements.append(ht)
    elements.append(Spacer(1, 15*mm))

    elements.append(Paragraph(f"<b>Ref:</b> Carta de cobrança referente ao contrato {emp.codigo_contrato}", estilo_ref))
    elements.append(Spacer(1, 5*mm))
    elements.append(Paragraph("Prezado(a) Senhor(a),", estilo_corpo))

    valor_fmt = f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    elements.append(Paragraph(
        f"Informamos que Vossa Senhoria possui <b>{qtd} ({num2words(qtd, lang='pt_BR')}) "
        f"parcela{'s' if qtd > 1 else ''}</b> em atraso referente{'s' if qtd > 1 else ''} "
        f"ao contrato <b>{emp.codigo_contrato}</b>, totalizando o valor de "
        f"<b>{valor_fmt} ({valor_extenso})</b>, não acrescido de juros e multa contratuais.",
        estilo_corpo,
    ))

    elements.append(Paragraph(
        "Solicitamos a regularização do débito no prazo de <b>15 (quinze) dias</b> "
        "a contar do recebimento desta correspondência. O não pagamento dentro do prazo "
        "estipulado acarretará as seguintes medidas:",
        estilo_corpo,
    ))

    for i, m in enumerate([
        "Inscrição nos sistemas de proteção ao crédito (SPC/Serasa);",
        "Protesto do título em Cartório de Protestos;",
        "Execução judicial do débito, com acréscimo de custas processuais e honorários advocatícios.",
    ], 1):
        elements.append(Paragraph(f"&nbsp;&nbsp;&nbsp;&nbsp;<b>{i}.</b> {m}", estilo_corpo))

    elements.append(Paragraph("Colocamo-nos à disposição para negociação e esclarecimentos que se fizerem necessários.", estilo_corpo))
    elements.append(Paragraph("Atenciosamente,", estilo_corpo))
    elements.append(Paragraph(f"{carta.local_emissao}, {data_extenso}.", estilo_data))
    elements.append(Spacer(1, 20*mm))
    elements.append(Paragraph("_" * 40, estilo_assinatura))
    elements.append(Paragraph("<b>Diretor Financeiro</b>", estilo_assinatura))

    doc.build(elements)
    return response


# ==============================================================================
# DESPESAS DE COBRANÇA
# ==============================================================================

@login_required
def listar_despesas(request):
    """Lista todas as despesas de cobrança com filtros."""
    from .models import DespesaCobranca
    from django.db.models import Sum
    from datetime import date

    despesas = DespesaCobranca.objects.select_related(
        "emprestimo__cliente", "registrado_por"
    ).order_by("-data")

    # Filtros
    busca = request.GET.get("q", "")
    if busca:
        despesas = despesas.filter(
            Q(emprestimo__cliente__nome_completo__icontains=busca) |
            Q(emprestimo__codigo_contrato__icontains=busca) |
            Q(descricao__icontains=busca)
        )

    tipo_filtro = request.GET.get("tipo", "")
    if tipo_filtro:
        despesas = despesas.filter(tipo=tipo_filtro)

    mes = request.GET.get("mes", "")
    ano = request.GET.get("ano", str(date.today().year))
    if mes and ano:
        despesas = despesas.filter(data__month=int(mes), data__year=int(ano))
    elif ano:
        despesas = despesas.filter(data__year=int(ano))

    total = despesas.aggregate(s=Sum("valor"))["s"] or Decimal("0.00")

    return render(request, "cobranca/despesas_listar.html", {
        "despesas": despesas[:100],
        "total": total,
        "busca": busca,
        "tipo_filtro": tipo_filtro,
        "tipos": DespesaCobranca.TIPO_CHOICES,
        "mes": mes,
        "ano": ano,
    })


@login_required
def adicionar_despesa(request, emprestimo_id):
    """Adiciona despesa de cobrança a um contrato."""
    from .models import DespesaCobranca

    emprestimo = get_object_or_404(Emprestimo, id=emprestimo_id)

    if request.method == "POST":
        tipo = request.POST.get("tipo", "OUTROS")
        descricao = request.POST.get("descricao", "").strip()
        valor_str = request.POST.get("valor", "0")
        data = request.POST.get("data", "")
        comprovante = request.FILES.get("comprovante")

        # Parse valor BRL
        limpo = valor_str.replace("R$", "").replace(" ", "").strip()
        if "," in limpo and "." in limpo:
            limpo = limpo.replace(".", "").replace(",", ".")
        elif "," in limpo:
            limpo = limpo.replace(",", ".")

        try:
            valor = Decimal(limpo)
        except Exception:
            messages.error(request, "Valor inválido.")
            return redirect("emprestimos:contrato_detalhe", pk=emprestimo.id)

        DespesaCobranca.objects.create(
            emprestimo=emprestimo,
            tipo=tipo,
            descricao=descricao,
            valor=valor,
            data=data or timezone.localdate(),
            comprovante=comprovante,
            registrado_por=request.user,
        )

        # Registra no histórico
        HistoricoCobranca.objects.create(
            cliente=emprestimo.cliente,
            emprestimo=emprestimo,
            usuario=request.user,
            descricao=f"Despesa de cobrança: {dict(DespesaCobranca.TIPO_CHOICES).get(tipo, tipo)} — R$ {valor:.2f}",
        )

        messages.success(request, f"Despesa de R$ {valor:.2f} registrada no contrato {emprestimo.codigo_contrato}.")
        return redirect("emprestimos:contrato_detalhe", pk=emprestimo.id)

    return redirect("emprestimos:contrato_detalhe", pk=emprestimo.id)


@login_required
def excluir_despesa(request, despesa_id):
    """Exclui uma despesa de cobrança."""
    from .models import DespesaCobranca

    despesa = get_object_or_404(DespesaCobranca, id=despesa_id)
    contrato_id = despesa.emprestimo_id
    if despesa.comprovante:
        despesa.comprovante.delete(save=False)
    despesa.delete()
    messages.info(request, "Despesa excluída.")
    return redirect("emprestimos:contrato_detalhe", pk=contrato_id)
