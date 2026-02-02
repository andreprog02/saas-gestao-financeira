# Arquivo: emprestimos/views.py

from decimal import Decimal
import io
from django.http import JsonResponse
# Imports de Contas (ESSENCIAL PARA A CONTA CORRENTE)
from contas.models import ContaCorrente, MovimentacaoConta

# Django Imports
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.db import transaction, models
from django.db.models import Q
from django.utils import timezone
from django.http import FileResponse
from django.conf import settings
from django.views.decorators.http import require_POST
from django.utils.dateparse import parse_date
from .models import Parcela

# Financeiro Imports
from financeiro.models import Transacao, calcular_saldo_atual
from financeiro.utils import get_client_ip

# Third-party Imports
from num2words import num2words 

# ReportLab Imports (PDF)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

# App Imports
from clientes.models import Cliente
from .forms import SelecionarClienteForm, NovoEmprestimoForm
from .models import Emprestimo, Parcela, ParcelaStatus, ContratoLog, EmprestimoStatus
from .services import simular
from .utils import gerar_codigo_contrato


# =============================================================================
#  FUNÇÃO AUXILIAR: TRATAMENTO DE VALORES MONETÁRIOS
# =============================================================================
def parse_valor_monetario(valor_str):
    if not valor_str:
        return Decimal("0.00")
    valor_str = str(valor_str).strip()
    if ',' in valor_str:
        clean = valor_str.replace('.', '').replace(',', '.')
        return Decimal(clean)
    else:
        return Decimal(valor_str)


# =============================================================================
#  PDF GENERATION
# =============================================================================
def contrato_pdf(request, emprestimo_id: int):
    contrato = get_object_or_404(Emprestimo.objects.select_related("cliente"), id=emprestimo_id)
    cliente = contrato.cliente
    parcelas = contrato.parcelas.all().order_by("numero")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.5*cm, leftMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm
    )

    styles = getSampleStyleSheet()
    style_titulo = ParagraphStyle('Titulo', parent=styles['Heading1'], alignment=TA_CENTER, fontSize=14, spaceAfter=10)
    style_normal = ParagraphStyle('Normal_Justificado', parent=styles['Normal'], alignment=TA_JUSTIFY, fontSize=10, leading=12, spaceAfter=6)
    style_centro = ParagraphStyle('Centro', parent=styles['Normal'], alignment=TA_CENTER, fontSize=10)

    elements = []
    empresa_nome = "SUA EMPRESA DE CRÉDITO LTDA"
    empresa_cnpj = "00.000.000/0001-00"
    cidade = "São Paulo"

    def data_pt(data_obj):
        meses = {1: 'janeiro', 2: 'fevereiro', 3: 'março', 4: 'abril', 5: 'maio', 6: 'junho', 7: 'julho', 8: 'agosto', 9: 'setembro', 10: 'outubro', 11: 'novembro', 12: 'dezembro'}
        return f"{data_obj.day} de {meses[data_obj.month]} de {data_obj.year}"

    data_atual_extenso = data_pt(timezone.localdate())

    def fmt_valor(v):
        val_str = f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        try:
            extenso = num2words(v, lang='pt_BR', to='currency')
        except:
            extenso = str(v)
        return f"{val_str} ({extenso})"

    elements.append(Paragraph(f"CONTRATO DE MÚTUO Nº {contrato.codigo_contrato}", style_titulo))
    elements.append(Spacer(1, 0.5*cm))
    
    texto_partes = f"""
    <b>MUTUANTE:</b> {empresa_nome}, inscrita no CNPJ sob nº {empresa_cnpj}.<br/><br/>
    <b>MUTUÁRIO:</b> {cliente.nome_completo}, CPF nº {cliente.cpf}, 
    residente e domiciliado em {cliente.logradouro}, {cliente.numero} - {cliente.bairro}, 
    {cliente.cidade}/{cliente.uf}.
    """
    elements.append(Paragraph(texto_partes, style_normal))

    elements.append(Paragraph("<b>1. DO OBJETO</b>", style_normal))
    texto_obj = f"""
    O presente contrato tem por objeto o empréstimo da quantia de 
    <b>{fmt_valor(contrato.valor_emprestado)}</b>, entregues ao MUTUÁRIO neste ato.
    """
    elements.append(Paragraph(texto_obj, style_normal))

    elements.append(Paragraph("<b>2. DO PAGAMENTO</b>", style_normal))
    texto_pag = f"""
    O MUTUÁRIO pagará à MUTUANTE a quantia total de 
    <b>{fmt_valor(contrato.total_contrato)}</b>, através de 
    <b>{contrato.qtd_parcelas}</b> parcelas mensais e sucessivas de 
    <b>{fmt_valor(contrato.valor_parcela_aplicada)}</b>.
    """
    elements.append(Paragraph(texto_pag, style_normal))

    elements.append(Paragraph("<b>3. DOS ENCARGOS</b>", style_normal))
    texto_juros = f"O valor do empréstimo foi calculado com juros de {contrato.taxa_juros_mensal}% ao mês."
    
    if contrato.tem_multa_atraso:
        texto_juros += f"""<br/>
        <b>Parágrafo Único:</b> O atraso no pagamento de qualquer parcela implicará na cobrança de multa de 
        {contrato.multa_atraso_percent}% e juros de mora de 
        {contrato.juros_mora_mensal_percent}% ao mês, calculados pro rata die.
        """
    else:
        texto_juros += "<br/>Não há multa por atraso contratada."
    elements.append(Paragraph(texto_juros, style_normal))

    elements.append(Paragraph("<b>4. DEMONSTRATIVO DAS PARCELAS</b>", style_normal))
    
    dados_tabela = [["Parcela", "Vencimento", "Valor"]]
    for p in parcelas:
        dados_tabela.append([str(p.numero), p.vencimento.strftime("%d/%m/%Y"), f"R$ {p.valor:,.2f}"])

    tabela = Table(dados_tabela, colWidths=[2.5*cm, 4*cm, 4*cm])
    style_table = TableStyle([
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('TOPPADDING', (0,0), (-1,-1), 2),
    ])
    tabela.setStyle(style_table)
    elements.append(tabela)

    elements.append(Spacer(1, 1.0*cm))
    elements.append(Paragraph(f"{cidade}, {data_atual_extenso}", style_centro))
    elements.append(Spacer(1, 1.5*cm))

    assinaturas = [
        ["_______________________________", "_______________________________"],
        [f"{empresa_nome}", f"{cliente.nome_completo}"],
        ["Mutuante", "Mutuário"]
    ]
    tab_ass = Table(assinaturas, colWidths=[8*cm, 8*cm])
    tab_ass.setStyle(TableStyle([('ALIGN', (0,0), (-1,-1), 'CENTER'), ('VALIGN', (0,0), (-1,-1), 'TOP'), ('FONTSIZE', (0,0), (-1,-1), 10)]))
    elements.append(tab_ass)

    elements.append(PageBreak()) 

    vencimento_final = parcelas.last().vencimento
    data_venc_extenso = data_pt(vencimento_final)
    
    try:
        total_extenso_upper = num2words(contrato.total_contrato, lang='pt_BR', to='currency').upper()
    except:
        total_extenso_upper = "VALOR LEGÍVEL"

    conteudo_np = []
    linha_topo = [[f"Nº {contrato.codigo_contrato}", f"R$ {contrato.total_contrato:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")]]
    tb_topo = Table(linha_topo, colWidths=[8*cm, 8*cm])
    tb_topo.setStyle(TableStyle([('ALIGN', (0,0), (0,0), 'LEFT'), ('ALIGN', (1,0), (1,0), 'RIGHT'), ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'), ('FONTSIZE', (0,0), (-1,-1), 12)]))
    conteudo_np.append(tb_topo)
    
    conteudo_np.append(Paragraph("<b>NOTA PROMISSÓRIA</b>", style_titulo))
    conteudo_np.append(Spacer(1, 1*cm))
    
    texto_promissoria = f"""
    No dia <b>{data_venc_extenso}</b>, pagarei(emos) por esta única via de NOTA PROMISSÓRIA 
    a <b>{empresa_nome}</b>, inscrita no CNPJ {empresa_cnpj}, ou à sua ordem, a quantia de:
    <br/><br/><br/>
    <b>{total_extenso_upper}</b>
    <br/><br/><br/>
    Em moeda corrente deste país, pagável em {cidade}.
    """
    conteudo_np.append(Paragraph(texto_promissoria, style_normal))
    conteudo_np.append(Spacer(1, 2*cm))
    
    texto_emitente = f"<b>Emitente:</b> {cliente.nome_completo}<br/><b>CPF/CNPJ:</b> {cliente.cpf}<br/><b>Endereço:</b> {cliente.logradouro}, {cliente.numero} - {cliente.bairro} - {cliente.cidade}/{cliente.uf}"
    conteudo_np.append(Paragraph(texto_emitente, style_normal))
    
    conteudo_np.append(Spacer(1, 2.5*cm))
    conteudo_np.append(Paragraph("____________________________________________________", style_centro))
    conteudo_np.append(Paragraph(f"{cliente.nome_completo}", style_centro))

    tabela_borda = Table([[conteudo_np]], colWidths=[17*cm])
    tabela_borda.setStyle(TableStyle([('BOX', (0,0), (-1,-1), 2, colors.black), ('TOPPADDING', (0,0), (-1,-1), 20), ('BOTTOMPADDING', (0,0), (-1,-1), 20), ('LEFTPADDING', (0,0), (-1,-1), 20), ('RIGHTPADDING', (0,0), (-1,-1), 20)]))
    
    elements.append(Spacer(1, 2*cm))
    elements.append(tabela_borda)

    doc.build(elements)
    buffer.seek(0)
    return FileResponse(buffer, as_attachment=False, filename=f"Contrato_{contrato.codigo_contrato}.pdf")


# =============================================================================
#  VIEWS DE CADASTRO E BUSCA
# =============================================================================
def novo_emprestimo_busca_cliente(request):
    form = SelecionarClienteForm(request.GET or None)
    clientes = None
    if form.is_valid():
        q = form.cleaned_data["q"].strip()
        clientes = (Cliente.objects.filter(nome_completo__icontains=q) | Cliente.objects.filter(cpf__icontains=q)).distinct().order_by("nome_completo")[:20]
        if not clientes.exists():
            messages.warning(request, "Nenhum cliente encontrado. Cadastre o cliente primeiro.")
    return render(request, "emprestimos/novo_busca.html", {"form": form, "clientes": clientes})


def novo_emprestimo_form(request, cliente_id: int):
    cliente = get_object_or_404(Cliente, id=cliente_id)

    if request.method == "POST":
        form = NovoEmprestimoForm(request.POST)
        if form.is_valid():
            parcela_bruta, parcela_aplicada, total_contrato, ajuste, parcelas = simular(
                valor_emprestado=form.cleaned_data["valor_emprestado"],
                qtd_parcelas=form.cleaned_data["qtd_parcelas"],
                taxa_juros_mensal=form.cleaned_data["taxa_juros_mensal"],
                primeiro_vencimento=form.cleaned_data["primeiro_vencimento"],
            )

            if "confirmar_cadastro" in request.POST:
                valor_solicitado = form.cleaned_data["valor_emprestado"]
                
                # Obtém saque_inicial (Opcional, se o cliente quiser sacar na hora)
                saque_inicial = form.cleaned_data.get("saque_inicial") or Decimal("0.00")
                
                # Se houver saque, verifica o caixa da empresa
                if saque_inicial > 0:
                    impacto_caixa = saque_inicial
                    saldo_atual = calcular_saldo_atual()
                    if saldo_atual < impacto_caixa:
                        messages.error(request, f"Saldo em caixa insuficiente para o saque inicial. Disponível: R$ {saldo_atual:,.2f}. Necessário: R$ {impacto_caixa:,.2f}")
                        return render(request, "emprestimos/novo_form.html", {"cliente": cliente, "form": form, "simulacao": {"parcela_bruta": parcela_bruta, "parcela_aplicada": parcela_aplicada, "total_contrato": total_contrato, "ajuste": ajuste, "parcelas": parcelas}})

                with transaction.atomic():
                    codigo = gerar_codigo_contrato()
                    tem_multa = form.cleaned_data.get("tem_multa_atraso", False)
                    multa_percent = form.cleaned_data.get("multa_atraso_percent") or Decimal("0.00")
                    juros_mora_percent = form.cleaned_data.get("juros_mora_mensal_percent") or Decimal("0.00")

                    # 1. Criar o Empréstimo
                    emprestimo = Emprestimo.objects.create(
                        cliente=cliente, codigo_contrato=codigo, valor_emprestado=valor_solicitado, qtd_parcelas=form.cleaned_data["qtd_parcelas"], taxa_juros_mensal=form.cleaned_data["taxa_juros_mensal"], primeiro_vencimento=form.cleaned_data["primeiro_vencimento"], valor_parcela_aplicada=parcela_aplicada, total_contrato=total_contrato, total_juros=(total_contrato - valor_solicitado).quantize(Decimal("0.01")), ajuste_arredondamento=ajuste, observacoes=form.cleaned_data.get("observacoes", ""), tem_multa_atraso=tem_multa, multa_atraso_percent=multa_percent, juros_mora_mensal_percent=juros_mora_percent,
                    )

                    Parcela.objects.bulk_create([Parcela(emprestimo=emprestimo, numero=p.numero, vencimento=p.vencimento, valor=p.valor, status=ParcelaStatus.ABERTA) for p in parcelas])
                    
                    ContratoLog.objects.create(contrato=emprestimo, acao=ContratoLog.Acao.CRIADO, usuario=request.user if request.user.is_authenticated else None, motivo="Cadastro inicial")

                    # 2. Gestão da Conta Corrente
                    conta, _ = ContaCorrente.objects.get_or_create(cliente=cliente)

                    # 2.1 CRÉDITO do Valor do Empréstimo na Conta do Cliente (AZUL no extrato)
                    MovimentacaoConta.objects.create(
                        conta=conta,
                        tipo='CREDITO',
                        origem='EMPRESTIMO',
                        valor=valor_solicitado,
                        descricao=f"Crédito ref. Contrato {emprestimo.codigo_contrato}",
                        emprestimo=emprestimo
                    )

                    # 2.2 SAQUE INICIAL (Se o cliente pediu para retirar na hora)
                    if saque_inicial > 0:
                        # Debita da conta do cliente (VERMELHO no extrato)
                        MovimentacaoConta.objects.create(
                            conta=conta,
                            tipo='DEBITO',
                            origem='SAQUE',
                            valor=saque_inicial,
                            descricao=f"Saque na contratação {emprestimo.codigo_contrato}",
                            emprestimo=emprestimo
                        )

                        # Registra no Livro Caixa (Saída da Empresa)
                        ip_cliente = get_client_ip(request)
                        Transacao.objects.create(
                            tipo='EMPRESTIMO_SAIDA', 
                            valor=-saque_inicial, 
                            descricao=f"Saque Inicial Contrato {emprestimo.codigo_contrato} - {cliente.nome_completo}", 
                            emprestimo=emprestimo,
                            usuario=request.user if request.user.is_authenticated else None,
                            ip_origem=ip_cliente
                        )

                messages.success(request, f"Empréstimo cadastrado com sucesso! Contrato: {codigo}")
                return redirect("emprestimos:contrato_detalhe", emprestimo_id=emprestimo.id)

            return render(request, "emprestimos/novo_form.html", {"cliente": cliente, "form": form, "simulacao": {"parcela_bruta": parcela_bruta, "parcela_aplicada": parcela_aplicada, "total_contrato": total_contrato, "ajuste": ajuste, "parcelas": parcelas}})
    else:
        form = NovoEmprestimoForm(initial={"cliente_id": cliente.id})

    return render(request, "emprestimos/novo_form.html", {"cliente": cliente, "form": form})


# =============================================================================
#  VIEWS DE LISTAGEM E DETALHES
# =============================================================================
def contratos(request):
    qs = Emprestimo.objects.select_related("cliente").all()
    return render(request, "emprestimos/contratos.html", {"contratos": qs})


def contrato_detalhe(request, emprestimo_id: int):
    emp = get_object_or_404(Emprestimo.objects.select_related("cliente"), id=emprestimo_id)
    emp.atualizar_status()
    emp.save(update_fields=["status", "atualizado_em"])
    parcelas = emp.parcelas.all().order_by("numero")
    return render(request, "emprestimos/contrato_detalhe.html", {"contrato": emp, "parcelas": parcelas})


def a_vencer(request):
    hoje = timezone.localdate()
    queryset = Parcela.objects.select_related('emprestimo', 'emprestimo__cliente').filter(status=ParcelaStatus.ABERTA, vencimento__gte=hoje)

    q = request.GET.get('q')
    if q: queryset = queryset.filter(Q(emprestimo__cliente__nome_completo__icontains=q) | Q(emprestimo__codigo_contrato__icontains=q))

    data_inicio = request.GET.get('data_inicio')
    data_fim = request.GET.get('data_fim')
    if data_inicio: queryset = queryset.filter(vencimento__gte=data_inicio)
    if data_fim: queryset = queryset.filter(vencimento__lte=data_fim)

    ordenacao = request.GET.get('ordenacao')
    if ordenacao == 'maior_valor': queryset = queryset.order_by('-valor')
    elif ordenacao == 'menor_valor': queryset = queryset.order_by('valor')
    elif ordenacao == 'cliente_az': queryset = queryset.order_by('emprestimo__cliente__nome_completo')
    elif ordenacao == 'contrato': queryset = queryset.order_by('emprestimo__codigo_contrato')
    elif ordenacao == 'vencimento_longe': queryset = queryset.order_by('-vencimento')
    else: queryset = queryset.order_by('vencimento')

    return render(request, "emprestimos/a_vencer.html", {"parcelas": queryset, "hoje": hoje, "titulo_pagina": "Parcelas a Vencer"})


def vencidos(request):
    hoje = timezone.localdate()
    queryset = Parcela.objects.select_related('emprestimo', 'emprestimo__cliente').filter(status=ParcelaStatus.ABERTA, vencimento__lt=hoje)

    q = request.GET.get('q')
    if q: queryset = queryset.filter(Q(emprestimo__cliente__nome_completo__icontains=q) | Q(emprestimo__codigo_contrato__icontains=q))

    data_inicio = request.GET.get('data_inicio')
    data_fim = request.GET.get('data_fim')
    if data_inicio: queryset = queryset.filter(vencimento__gte=data_inicio)
    if data_fim: queryset = queryset.filter(vencimento__lte=data_fim)

    ordenacao = request.GET.get('ordenacao')
    if ordenacao == 'maior_valor': queryset = queryset.order_by('-valor')
    elif ordenacao == 'menor_valor': queryset = queryset.order_by('valor')
    elif ordenacao == 'cliente_az': queryset = queryset.order_by('emprestimo__cliente__nome_completo')
    else: queryset = queryset.order_by('vencimento')

    return render(request, "emprestimos/vencidos.html", {"parcelas": queryset, "hoje": hoje, "titulo_pagina": "Parcelas Vencidas", "is_vencidos": True})


# =============================================================================
#  AÇÕES: PAGAMENTO, RENEGOCIAÇÃO E CANCELAMENTO
# =============================================================================

@require_POST
@transaction.atomic
def pagar_parcela(request, parcela_id: int):
    p = get_object_or_404(Parcela.objects.select_related("emprestimo", "emprestimo__cliente"), id=parcela_id)
    contrato = p.emprestimo

    senha = request.POST.get("senha", "").strip()
    if senha != "1234":
        messages.error(request, "Senha de pagamento incorreta.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    parcela_anterior = contrato.parcelas.filter(status=ParcelaStatus.ABERTA, numero__lt=p.numero).order_by('numero').first()
    if parcela_anterior:
        messages.error(request, f"Bloqueado! A parcela {parcela_anterior.numero} (Venc: {parcela_anterior.vencimento.strftime('%d/%m/%Y')}) está em aberto.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    if contrato.status == EmprestimoStatus.CANCELADO:
        messages.error(request, "Contrato CANCELADO. Operação não permitida.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    if p.status != ParcelaStatus.ABERTA:
        messages.warning(request, f"A parcela {p.numero} já consta como paga.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)
    
    # === CORREÇÃO: CALCULAR JUROS E MULTA NO PAGAMENTO ===
    dados = p.dados_atualizados  # Pega o dicionário com valor_original, multa, juros e total
    valor_final = dados['total']
    
    # Baixa a parcela com o valor ATUALIZADO
    p.marcar_como_paga(valor_pago=valor_final)
    
    # Log detalhado
    detalhes_log = f"Parcela {p.numero} paga manualmente."
    if dados['multa'] > 0 or dados['juros'] > 0:
        detalhes_log += f" (Multa: {dados['multa']} + Juros: {dados['juros']})"

    ContratoLog.objects.create(
        contrato=contrato, 
        acao=ContratoLog.Acao.PAGO, 
        usuario=request.user if request.user.is_authenticated else None, 
        motivo=detalhes_log
    )
    
    # Registro Financeiro com VALOR TOTAL
    ip_cliente = get_client_ip(request)
    Transacao.objects.create(
        tipo='PAGAMENTO_ENTRADA', 
        valor=valor_final, 
        descricao=f"Recebimento Parc. {p.numero}/{contrato.qtd_parcelas} - {contrato.cliente.nome_completo}", 
        emprestimo=contrato,
        usuario=request.user if request.user.is_authenticated else None,
        ip_origem=ip_cliente
    )

    msg_sucesso = f"Parcela {p.numero} paga com sucesso! Valor: R$ {valor_final:,.2f}"
    messages.success(request, msg_sucesso)
    
    # Redireciona de volta para onde o usuário estava (se possível) ou para detalhes
    return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)


@transaction.atomic
def renegociar(request, emprestimo_id):
    contrato = get_object_or_404(Emprestimo, id=emprestimo_id)

    if request.method != "POST":
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    try:
        entrada = parse_valor_monetario(request.POST.get("entrada", ""))
        usar_taxa_antiga = request.POST.get("usar_taxa_antiga") == "1"
        if not usar_taxa_antiga:
            nova_taxa = parse_valor_monetario(request.POST.get("nova_taxa", ""))
        else:
            nova_taxa = contrato.taxa_juros_mensal
        
        novo_vencimento_str = request.POST.get("novo_vencimento")
        novo_vencimento = parse_date(novo_vencimento_str)
        if not novo_vencimento:
            raise ValueError("Data de vencimento inválida.")

        qtd_parcelas = int(request.POST["qtd_parcelas"])
        taxa = contrato.taxa_juros_mensal if usar_taxa_antiga else nova_taxa

        saldo_total_antigo = contrato.parcelas.filter(status=ParcelaStatus.ABERTA).aggregate(total=models.Sum("valor"))["total"] or Decimal("0.00")
        valor_novo_emprestimo = saldo_total_antigo - entrada

        if valor_novo_emprestimo <= 0:
            messages.error(request, "A entrada cobre todo o saldo. Use a quitação antecipada.")
            return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

        ip_cliente = get_client_ip(request)
        usuario_logado = request.user if request.user.is_authenticated else None

        if entrada > 0:
            Transacao.objects.create(
                tipo='DEPOSITO', 
                valor=entrada, 
                descricao=f"Entrada Renegociação {contrato.codigo_contrato}", 
                emprestimo=contrato,
                usuario=usuario_logado,
                ip_origem=ip_cliente
            )

        Transacao.objects.create(
            tipo='PAGAMENTO_ENTRADA', 
            valor=valor_novo_emprestimo, 
            descricao=f"Liq. Saldo Anterior {contrato.codigo_contrato} (Refinanciamento)", 
            emprestimo=contrato,
            usuario=usuario_logado,
            ip_origem=ip_cliente
        )
        
        transacao_saida = Transacao.objects.create(
            tipo='EMPRESTIMO_SAIDA', 
            valor=-valor_novo_emprestimo, 
            descricao=f"Renegociação {contrato.codigo_contrato} (Novo Saldo)", 
            emprestimo=None,
            usuario=usuario_logado,
            ip_origem=ip_cliente
        )

        contrato.parcelas.filter(status=ParcelaStatus.ABERTA).update(status=ParcelaStatus.LIQUIDADA_RENEGOCIACAO)
        contrato.status = EmprestimoStatus.RENEGOCIADO
        contrato.save()

        codigo_novo = gerar_codigo_contrato()
        parcela_bruta, parcela_aplicada, total, ajuste, parcelas = simular(valor_novo_emprestimo, qtd_parcelas, taxa, novo_vencimento)

        novo = Emprestimo.objects.create(
            cliente=contrato.cliente, contrato_origem=contrato, codigo_contrato=codigo_novo, valor_emprestado=valor_novo_emprestimo, qtd_parcelas=qtd_parcelas, taxa_juros_mensal=taxa, primeiro_vencimento=novo_vencimento, valor_parcela_aplicada=parcela_aplicada, total_contrato=total, ajuste_arredondamento=ajuste, status=EmprestimoStatus.ATIVO
        )

        Parcela.objects.bulk_create([Parcela(emprestimo=novo, numero=p.numero, vencimento=p.vencimento, valor=p.valor) for p in parcelas])

        transacao_saida.emprestimo = novo
        transacao_saida.descricao = f"Renegociação {contrato.codigo_contrato} (Gerou {novo.codigo_contrato})"
        transacao_saida.save()

        ContratoLog.objects.create(contrato=contrato, acao=ContratoLog.Acao.RENEGOCIADO, usuario=request.user if request.user.is_authenticated else None, motivo=f"Gerou contrato {novo.codigo_contrato}")
        ContratoLog.objects.create(contrato=novo, acao=ContratoLog.Acao.CRIADO, usuario=request.user if request.user.is_authenticated else None, motivo=f"Origem: {contrato.codigo_contrato}")

        messages.success(request, f"Renegociação concluída! Novo contrato: {codigo_novo}")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=novo.id)

    except Exception as e:
        messages.error(request, f"Erro ao renegociar: {str(e)}")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)


@transaction.atomic
def cancelar_contrato(request, emprestimo_id: int):
    contrato = get_object_or_404(Emprestimo.objects.select_related("cliente"), id=emprestimo_id)

    if request.method != "POST":
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    senha = (request.POST.get("senha") or "").strip()
    senha_correta = getattr(settings, "CONTRATO_DELETE_PASSWORD", "")

    if not senha_correta:
        messages.error(request, "Senha administrativa não configurada no settings.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    if senha != senha_correta:
        messages.error(request, "Senha inválida.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    motivo = (request.POST.get("motivo") or "").strip()
    observacao = (request.POST.get("observacao") or "").strip()

    contrato.parcelas.filter(status=ParcelaStatus.ABERTA).update(status=ParcelaStatus.CANCELADA)
    contrato.status = EmprestimoStatus.CANCELADO
    contrato.cancelado_em = timezone.now()
    contrato.cancelado_por = request.user if request.user.is_authenticated else None
    contrato.motivo_cancelamento = motivo or None
    contrato.observacao_cancelamento = observacao or None
    contrato.observacoes = (contrato.observacoes or "") + f"\n[CANCELADO] Motivo: {motivo} | Obs: {observacao}"
    contrato.save()

    ContratoLog.objects.create(contrato=contrato, acao=ContratoLog.Acao.CANCELADO, usuario=request.user if request.user.is_authenticated else None, motivo=motivo, observacao=observacao)

    messages.success(request, "Contrato cancelado com sucesso.")
    return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)


@transaction.atomic
def reabrir_contrato(request, emprestimo_id: int):
    contrato = get_object_or_404(Emprestimo, id=emprestimo_id)

    if request.method != "POST":
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    senha = (request.POST.get("senha") or "").strip()
    senha_correta = getattr(settings, "CONTRATO_DELETE_PASSWORD", "")

    if senha != senha_correta:
        messages.error(request, "Senha inválida.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    if contrato.status != EmprestimoStatus.CANCELADO:
        messages.error(request, "Este contrato não está cancelado.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    contrato.parcelas.filter(status=ParcelaStatus.CANCELADA).update(status=ParcelaStatus.ABERTA)
    contrato.status = EmprestimoStatus.ATIVO
    contrato.cancelado_em = None
    contrato.cancelado_por = None
    contrato.motivo_cancelamento = None
    contrato.observacao_cancelamento = None
    contrato.save()

    ContratoLog.objects.create(contrato=contrato, acao=ContratoLog.Acao.REABERTO, usuario=request.user if request.user.is_authenticated else None, motivo="Desfazer cancelamento")

    messages.success(request, "Contrato reaberto com sucesso.")
    return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)


@transaction.atomic
def pagar_parcial(request, parcela_id: int):
    parcela = get_object_or_404(Parcela, id=parcela_id)
    contrato = parcela.emprestimo

    if request.method == "POST":
        valor_juros_pago = parse_valor_monetario(request.POST.get('valor_pago'))
        nova_data_vencimento = request.POST.get('nova_data')
        
        proxima_parcela = contrato.parcelas.filter(numero=parcela.numero + 1).first()
        if proxima_parcela and nova_data_vencimento >= str(proxima_parcela.vencimento):
            messages.error(request, "Erro: A nova data deve ser ANTERIOR ao vencimento da próxima parcela.")
            return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)
        
        ip_cliente = get_client_ip(request)
        Transacao.objects.create(
            tipo='PAGAMENTO_ENTRADA', 
            valor=valor_juros_pago, 
            descricao=f"Pagamento Parcial (Juros) Parc. {parcela.numero} - {contrato.codigo_contrato}", 
            emprestimo=contrato,
            usuario=request.user if request.user.is_authenticated else None,
            ip_origem=ip_cliente
        )

        data_antiga = parcela.vencimento
        parcela.vencimento = nova_data_vencimento
        parcela.save()

        ContratoLog.objects.create(contrato=contrato, acao='RENEGOCIADO', motivo=f"Pagamento parcial de R$ {valor_juros_pago}. Vencimento alterado de {data_antiga} para {nova_data_vencimento}")

        messages.success(request, "Pagamento parcial registrado e vencimento prorrogado.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

def detalhes_pagamento_json(request, parcela_id):
    parcela = get_object_object_or_404(Parcela, id=parcela_id)
    # O método dados_atualizados já calcula Multa e Juros com base na data atual
    dados = parcela.dados_atualizados() 
    
    return JsonResponse({
        'valor_original': dados['valor_original'],
        'multa': dados['multa'],
        'juros': dados['juros'],
        'total': dados['valor_total'],
        'dias_atraso': dados['dias_atraso']
    })

def calcular_valores_parcela_json(request, parcela_id):
    parcela = get_object_or_404(Parcela, id=parcela_id)
    # Usamos o método que você já tem no models.py para pegar os juros/multa reais
    dados = parcela.dados_atualizados() 
    
    return JsonResponse({
        'valor_nominal': f"{dados['valor_original']:.2f}",
        'multa': f"{dados['multa']:.2f}",
        'juros': f"{dados['juros']:.2f}",
        'total': f"{dados['valor_total']:.2f}"
    })

def calcular_valores_parcela_ajax(request, parcela_id):
    parcela = get_object_or_404(Parcela, id=parcela_id)
    # Chama a propriedade do model que calcula Multa e Juros em tempo real
    dados = parcela.dados_atualizados  
    
    return JsonResponse({
        'valor_original': f"{dados['valor_original']:.2f}",
        'multa': f"{dados['multa']:.2f}",
        'juros': f"{dados['juros']:.2f}",
        'total': f"{dados['total']:.2f}"
    })