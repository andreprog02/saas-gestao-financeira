import uuid
from decimal import Decimal
from dateutil.relativedelta import relativedelta

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.http import JsonResponse
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Case, When, Value, IntegerField

# === IMPORTS DOS MODELS ===
from .models import (
    Emprestimo, Parcela, EmprestimoStatus, ParcelaStatus, 
    PropostaEmprestimo, ContratoLog
)
from .forms import EmprestimoForm, BuscaClienteForm
from clientes.models import Cliente
from financeiro.models import Transacao
from contas.models import MovimentacaoConta, ContaCorrente

# === IMPORTS DE SERVIÇOS ===
# Tenta importar dos arquivos corretos.
# 'simular' e 'aprovar_proposta' estão em services.py
# 'gerar_dossie_cliente' está em services_analise.py

try:
    from .services import simular, aprovar_proposta
except ImportError:
    pass

try:
    from .services_analise import gerar_dossie_cliente
except ImportError:
    def gerar_dossie_cliente(cliente): return None


# === FUNÇÃO DE CONVERSÃO ROBUSTA ===
def to_decimal(val_str):
    """
    Converte string monetária para Decimal de forma inteligente.
    Suporta:
    - '9.000,00' -> 9000.00 (Padrão BR)
    - '9.000'    -> 9000.00 (Padrão BR sem decimais)
    - '9000.00'  -> 9000.00 (Padrão US/DB)
    - '9'        -> 9.00
    """
    if not val_str: return Decimal('0.00')
    
    val_str = str(val_str).replace('R$', '').strip()
    
    # Caso 1: Tem vírgula (Formato BR explícito: 1.000,00 ou 1000,00)
    if ',' in val_str:
        val_str = val_str.replace('.', '').replace(',', '.')
    
    # Caso 2: Tem ponto mas NÃO tem vírgula (Ambiguidade: 9.000 ou 5000.00)
    elif '.' in val_str:
        parts = val_str.split('.')
        # Se a parte decimal tem 3 dígitos (ex: 9.000), assumimos que é milhar BR
        if len(parts[-1]) == 3:
            val_str = val_str.replace('.', '')
        # Se tem 2 dígitos (ex: 5000.00), mantemos como ponto decimal US
    
    try:
        return Decimal(val_str)
    except:
        return Decimal('0.00')

# ==============================================================================
# 1. GESTÃO DE CONTRATOS (LISTAGEM E DETALHES)
# ==============================================================================

@login_required
def listar_contratos(request):
    """Lista todos os contratos ordenados por data"""
    contratos = Emprestimo.objects.all().order_by('-criado_em')
    return render(request, "emprestimos/contratos.html", {"contratos": contratos})

@login_required
def contrato_detalhe(request, pk):
    """Exibe detalhes, parcelas e modal de parceiro"""
    contrato = get_object_or_404(Emprestimo, pk=pk)
    parcelas = contrato.parcelas.all().order_by('numero')
    
    todos_clientes = Cliente.objects.all().order_by('nome_completo')

    return render(request, "emprestimos/contrato_detalhe.html", {
        "contrato": contrato,
        "parcelas": parcelas,
        "todos_clientes": todos_clientes,
        "hoje": timezone.localdate(),
    })

@login_required
def calcular_valores_parcela_ajax(request, parcela_id):
    """API para atualizar valores no modal de pagamento"""
    parcela = get_object_or_404(Parcela, id=parcela_id)
    dados = parcela.dados_atualizados 
    return JsonResponse({
        'valor_original': f"{dados['valor_original']:.2f}",
        'multa': f"{dados['multa']:.2f}",
        'juros': f"{dados['juros']:.2f}",
        'total': f"{dados['total']:.2f}"
    })

# ==============================================================================
# 2. OPERAÇÕES FINANCEIRAS (PAGAMENTO E SPLIT)
# ==============================================================================

@login_required
def pagar_parcela(request, pk):
    """Processa o pagamento de uma parcela com cálculo de Split para Parceiro"""
    parcela = get_object_or_404(Parcela, pk=pk)
    contrato = parcela.emprestimo
    
    parceiro = contrato.parceiro
    tem_split = parceiro is not None
    # Usa o percentual gravado no contrato, ou 10% se não houver
    PORCENTAGEM_COMISSAO = contrato.percentual_comissao if contrato.percentual_comissao else Decimal('10.00')

    valor_total = parcela.valor_atual
    valor_honorarios = Decimal('0.00')
    nome_profissional = parceiro.nome_completo if parceiro else ""

    if tem_split:
        valor_honorarios = valor_total * (PORCENTAGEM_COMISSAO / Decimal('100'))
        valor_honorarios = valor_honorarios.quantize(Decimal('0.01'))

    if request.method == "POST":
        senha = request.POST.get("senha")
        
        if senha != "1234":
             messages.error(request, "Senha incorreta. Operação cancelada.")
             return redirect('emprestimos:contrato_detalhe', pk=contrato.id)

        try:
            with transaction.atomic():
                # A. Baixa a Parcela
                parcela.status = ParcelaStatus.PAGA
                parcela.data_pagamento = timezone.now()
                parcela.valor_pago = valor_total
                parcela.save()

                # B. Registra Entrada no Fluxo de Caixa (POSITIVO)
                descricao_cx = f"{contrato.cliente.nome_completo} pagou {valor_total}"
                if valor_honorarios > 0:
                    descricao_cx += f" (Split: {valor_honorarios} para {nome_profissional})"
                
                Transacao.objects.create(
                    tipo='PAGAMENTO_ENTRADA',
                    valor=abs(valor_total),
                    descricao=descricao_cx + f" - Parc. {parcela.numero}/{contrato.qtd_parcelas}",
                    usuario=request.user,
                    emprestimo=contrato
                )

                # C. Split: Repasse ao Parceiro (se houver)
                if valor_honorarios > 0 and parceiro:
                    # 1. Despesa da Empresa (Saída)
                    Transacao.objects.create(
                        tipo='DESPESA',
                        valor=-abs(valor_honorarios),
                        descricao=f"Comissão Parceiro - {nome_profissional} (Ref. Contrato {contrato.codigo_contrato})",
                        usuario=request.user,
                        emprestimo=contrato
                    )

                    # 2. Entrada Compensatória (Depósito na Conta Interna do Parceiro)
                    Transacao.objects.create(
                        tipo='DEPOSITO_CC',
                        valor=abs(valor_honorarios),
                        descricao=f"Depósito C/C Parceiro (Retido) - {nome_profissional}",
                        usuario=request.user,
                        emprestimo=contrato
                    )

                    # 3. Crédito na Carteira Digital do Parceiro
                    conta_prof, _ = ContaCorrente.objects.get_or_create(cliente=parceiro)
                    MovimentacaoConta.objects.create(
                        conta=conta_prof,
                        tipo='CREDITO',
                        origem='COMISSAO',
                        valor=abs(valor_honorarios),
                        descricao=f"Comissão {PORCENTAGEM_COMISSAO}% - Ref. Cliente {contrato.cliente.nome_completo}",
                        data=timezone.now()
                    )

                ContratoLog.objects.create(
                    contrato=contrato,
                    acao="PAGO",
                    usuario=request.user,
                    motivo=f"Parcela {parcela.numero} quitada.",
                    observacao=f"Valor: {valor_total} | Comissao: {valor_honorarios}"
                )
                
                messages.success(request, f"Pagamento confirmado! R$ {valor_honorarios} de comissão gerado.")
                return redirect("emprestimos:contrato_detalhe", pk=contrato.id)

        except Exception as e:
            messages.error(request, f"Erro ao processar: {e}")
            return redirect("emprestimos:contrato_detalhe", pk=contrato.id)

    context = {
        'parcela': parcela,
        'valor_total': valor_total,
        'tem_split': 'true' if tem_split else 'false',
        'nome_profissional': nome_profissional,
        'valor_honorarios': valor_honorarios,
        'percentual': PORCENTAGEM_COMISSAO,
    }
    return render(request, "emprestimos/pagar_parcela.html", context)

# ==============================================================================
# 3. CRIAÇÃO DE EMPRÉSTIMO (LEGADO/DIRETO)
# ==============================================================================

@login_required
def novo_emprestimo_busca(request):
    """Tela de busca de cliente para iniciar novo empréstimo"""
    form = BuscaClienteForm(request.GET or None)
    clientes = None
    if form.is_valid():
        q = form.cleaned_data.get('query')
        clientes = Cliente.objects.filter(Q(nome_completo__icontains=q) | Q(cpf__icontains=q))
    return render(request, "emprestimos/novo_busca.html", {"form": form, "clientes": clientes})

@login_required
def novo_emprestimo_form(request, cliente_id):
    """Formulário completo de simulação e criação direta de empréstimo"""
    cliente = get_object_or_404(Cliente, id=cliente_id)
    simulacao = None

    if request.method == "POST":
        form = EmprestimoForm(request.POST)
        
        if form.is_valid():
            valor = form.cleaned_data['valor_emprestado']
            taxa = form.cleaned_data['taxa_juros_mensal']
            qtd = form.cleaned_data['qtd_parcelas']
            primeiro_venc = form.cleaned_data['primeiro_vencimento']

            # --- AÇÃO: SIMULAR ---
            if 'simular' in request.POST:
                juros_total = valor * (taxa / 100) * qtd
                montante_final = valor + juros_total
                valor_parcela = montante_final / qtd

                lista_parcelas = []
                data_atual = primeiro_venc
                for i in range(1, qtd + 1):
                    lista_parcelas.append({
                        'numero': i,
                        'vencimento': data_atual,
                        'valor': valor_parcela
                    })
                    data_atual = data_atual + relativedelta(months=1)

                simulacao = {
                    'parcela_aplicada': valor_parcela,
                    'total_contrato': montante_final,
                    'total_juros': juros_total,
                    'parcelas': lista_parcelas
                }
                messages.info(request, "Simulação atualizada.")
                return render(request, "emprestimos/novo_form.html", {
                    "form": form, "cliente": cliente, "simulacao": simulacao
                })

            # --- AÇÃO: CONFIRMAR (CRIAR) ---
            elif 'confirmar_cadastro' in request.POST:
                with transaction.atomic():
                    # 1. Cria o Objeto Empréstimo
                    emprestimo = form.save(commit=False)
                    emprestimo.cliente = cliente
                    emprestimo.usuario = request.user
                    emprestimo.codigo_contrato = f"EMP{timezone.now().strftime('%Y%m%d%H%M')}"
                    emprestimo.save() 

                    # 2. Gera as Parcelas no Banco
                    juros_total = valor * (taxa / 100) * qtd
                    montante_final = valor + juros_total
                    valor_parcela = montante_final / qtd
                    
                    data_atual = primeiro_venc
                    for i in range(1, qtd + 1):
                        Parcela.objects.create(
                            emprestimo=emprestimo,
                            numero=i,
                            vencimento=data_atual,
                            valor=valor_parcela
                        )
                        data_atual = data_atual + relativedelta(months=1)

                    # 3. Saída do Caixa
                    Transacao.objects.create(
                        tipo='EMPRESTIMO_SAIDA',
                        valor=-abs(valor), 
                        descricao=f"Saída Empréstimo Direto - {emprestimo.codigo_contrato}",
                        emprestimo=emprestimo,
                        usuario=request.user
                    )

                    # 4. Depósito na Conta (Contra-partida)
                    Transacao.objects.create(
                        tipo='DEPOSITO_CC',
                        valor=abs(valor),
                        descricao=f"Depósito C/C (Disponível p/ Saque) - {emprestimo.codigo_contrato}",
                        emprestimo=emprestimo,
                        usuario=request.user
                    )

                    # 5. Crédito no App do Cliente
                    conta_real, _ = ContaCorrente.objects.get_or_create(cliente=cliente)
                    MovimentacaoConta.objects.create(
                        conta=conta_real,
                        tipo='CREDITO',
                        origem='EMPRESTIMO',
                        valor=abs(valor),
                        descricao=f"Liberação Empréstimo {emprestimo.codigo_contrato}",
                        data=timezone.now(),
                        emprestimo=emprestimo
                    )

                    messages.success(request, f"Contrato {emprestimo.codigo_contrato} criado e valor creditado!")
                    return redirect("emprestimos:contrato_detalhe", pk=emprestimo.id)

    else:
        form = EmprestimoForm()
    
    return render(request, "emprestimos/novo_form.html", {"form": form, "cliente": cliente})

# ==============================================================================
# 4. AÇÕES ADMINISTRATIVAS
# ==============================================================================

@login_required
def cancelar_contrato(request, pk):
    contrato = get_object_or_404(Emprestimo, pk=pk)
    if request.method == "POST":
        senha = request.POST.get("senha")
        if senha == "1234":
            with transaction.atomic():
                contrato.status = EmprestimoStatus.CANCELADO
                contrato.cancelado_em = timezone.now()
                contrato.cancelado_por = request.user
                contrato.motivo_cancelamento = request.POST.get("motivo")
                contrato.save()
                
                contrato.parcelas.filter(status=ParcelaStatus.ABERTA).update(status=ParcelaStatus.CANCELADA)
                
                messages.warning(request, f"Contrato {contrato.codigo_contrato} foi cancelado.")
        else:
            messages.error(request, "Senha administrativa incorreta.")
    return redirect("emprestimos:contrato_detalhe", pk=contrato.id)

@login_required
def vincular_parceiro(request, pk):
    contrato = get_object_or_404(Emprestimo, pk=pk)
    
    if request.method == "POST":
        parceiro_id = request.POST.get("parceiro_id")
        antigo_parceiro = contrato.parceiro.nome_completo if contrato.parceiro else "Nenhum"
        
        if parceiro_id:
            novo_parceiro = get_object_or_404(Cliente, id=parceiro_id)
            contrato.parceiro = novo_parceiro
            msg = f"Parceiro alterado de {antigo_parceiro} para {novo_parceiro.nome_completo}"
        else:
            contrato.parceiro = None
            msg = f"Parceiro removido (Era: {antigo_parceiro})"
            
        contrato.save()
        
        ContratoLog.objects.create(
            contrato=contrato,
            acao="RENEGOCIADO",
            usuario=request.user,
            motivo="Alteração de Parceiro",
            observacao=msg
        )
        
        messages.success(request, msg)
        
    return redirect("emprestimos:contrato_detalhe", pk=contrato.id)

@login_required
def a_vencer(request):
    hoje = timezone.localdate()
    parcelas = Parcela.objects.filter(status=ParcelaStatus.ABERTA, vencimento__gte=hoje).order_by('vencimento')
    q = request.GET.get('q')
    if q:
        parcelas = parcelas.filter(Q(emprestimo__cliente__nome_completo__icontains=q))
    return render(request, "emprestimos/a_vencer.html", {"parcelas": parcelas, "hoje": hoje})

@login_required
def vencidos(request):
    hoje = timezone.localdate()
    parcelas = Parcela.objects.filter(status=ParcelaStatus.ABERTA, vencimento__lt=hoje).order_by('vencimento')
    return render(request, "emprestimos/vencidos.html", {"parcelas": parcelas, "hoje": hoje})

# ==============================================================================
# 5. ESTEIRA DE CRÉDITO (PROPOSTAS E ANÁLISE)
# ==============================================================================

@login_required
def listar_propostas(request):
    propostas = PropostaEmprestimo.objects.annotate(
        prioridade=Case(
            When(status='PENDENTE', then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    ).order_by('prioridade', '-data_solicitacao')
    
    return render(request, 'emprestimos/propostas/lista.html', {'propostas': propostas})

@login_required
def criar_proposta(request):
    """Vendedor insere proposta para análise"""
    if request.method == 'POST':
        try:
            # Pega os valores brutos
            valor_post = request.POST.get('valor')
            taxa_post = request.POST.get('taxa')
            
            # Converte usando a função segura to_decimal
            # (Certifique-se que def to_decimal(val_str) está definida no topo do seu views.py conforme passo anterior)
            valor_decimal = to_decimal(valor_post)
            taxa_decimal = to_decimal(taxa_post)
            
            PropostaEmprestimo.objects.create(
                cliente_id=request.POST.get('cliente_id'),
                valor_solicitado=valor_decimal,
                qtd_parcelas=int(request.POST.get('qtd_parcelas')),
                taxa_juros=taxa_decimal,
                primeiro_vencimento=request.POST.get('vencimento'),
                usuario_solicitante=request.user,
                observacoes=request.POST.get('observacoes')
            )
            messages.success(request, "Proposta enviada para análise com sucesso!")
            return redirect('emprestimos:listar_propostas')
            
        except Exception as e:
            messages.error(request, f"Erro ao criar proposta: {e}")
    
    clientes = Cliente.objects.all().order_by('nome_completo')
    return render(request, 'emprestimos/propostas/form.html', {'clientes': clientes})

@login_required
@transaction.atomic
def analisar_proposta(request, proposta_id):
    """
    Esteira de Crédito:
    1. Analista ajusta valores e comissão.
    2. Aprova -> Gera Contrato -> Gera Parcelas.
    3. FINANCEIRO -> Tira do Caixa da Empresa -> Põe na Conta do Cliente.
    """
    proposta = get_object_or_404(PropostaEmprestimo, id=proposta_id)
    
    # IMPORTANTE: Busca a função de dossiê do services_analise
    try:
        dossie = gerar_dossie_cliente(proposta.cliente)
    except:
        dossie = None
    
    todos_parceiros = Cliente.objects.all().order_by('nome_completo')

    if request.method == 'POST':
        acao = request.POST.get('acao')
        parecer = request.POST.get('parecer')
        
        try:
            novo_valor = abs(to_decimal(request.POST.get('valor_aprovado')))
            nova_taxa = to_decimal(request.POST.get('taxa_aprovada'))
            nova_comissao = to_decimal(request.POST.get('percentual_comissao'))
            novo_qtd = int(request.POST.get('qtd_aprovada', 1))
            
            parceiro_id = request.POST.get('parceiro')
            novo_parceiro = None
            if parceiro_id:
                novo_parceiro = Cliente.objects.filter(id=parceiro_id).first()

        except Exception as e:
            messages.error(request, f"Erro nos valores: {e}")
            return redirect(request.path)

        proposta.usuario_aprovador = request.user
        proposta.data_analise = timezone.now()
        proposta.parecer_analise = parecer
        
        if acao == 'NEGAR':
            proposta.status = 'NEGADO'
            proposta.save()
            messages.info(request, "Proposta negada.")
            
        elif acao == 'APROVAR':
            # 1. Atualiza Proposta
            proposta.valor_solicitado = novo_valor
            proposta.taxa_juros = nova_taxa
            proposta.qtd_parcelas = novo_qtd
            proposta.parceiro = novo_parceiro
            proposta.percentual_comissao = nova_comissao
            proposta.status = 'APROVADO'
            
            # 2. Gera Contrato (Cálculos)
            if 'simular' in globals():
                _, parc_aplicada, total_ctr, _, parcelas_simuladas = simular(
                    valor_emprestado=novo_valor,
                    qtd_parcelas=novo_qtd,
                    taxa_juros_mensal=nova_taxa,
                    primeiro_vencimento=proposta.primeiro_vencimento
                )
            else:
                total_ctr = novo_valor * (1 + (nova_taxa/100 * novo_qtd))
                parc_aplicada = total_ctr / novo_qtd
                parcelas_simuladas = []

            codigo_novo = f"EMP{timezone.now().strftime('%Y%m%d%H%M')}"
            
            emprestimo = Emprestimo.objects.create(
                cliente=proposta.cliente,
                codigo_contrato=codigo_novo,
                valor_emprestado=novo_valor,
                qtd_parcelas=novo_qtd,
                taxa_juros_mensal=nova_taxa,
                primeiro_vencimento=proposta.primeiro_vencimento,
                valor_parcela_aplicada=parc_aplicada,
                total_contrato=total_ctr,
                parceiro=novo_parceiro,
                percentual_comissao=nova_comissao
            )
            
            # 3. Gera Parcelas
            if parcelas_simuladas:
                lista_parcelas_db = []
                for p_simulada in parcelas_simuladas:
                    num = p_simulada.numero if hasattr(p_simulada, 'numero') else p_simulada['numero']
                    venc = p_simulada.vencimento if hasattr(p_simulada, 'vencimento') else p_simulada['vencimento']
                    val = p_simulada.valor if hasattr(p_simulada, 'valor') else p_simulada['valor']

                    lista_parcelas_db.append(Parcela(
                        emprestimo=emprestimo,
                        numero=num,
                        vencimento=venc,
                        valor=val
                    ))
                Parcela.objects.bulk_create(lista_parcelas_db)
            
            # 4. Movimentação Financeira
            # A) Saída do Caixa
            Transacao.objects.create(
                tipo='EMPRESTIMO_SAIDA', 
                valor=-abs(novo_valor), 
                descricao=f"Liberação Contrato {codigo_novo} - {proposta.cliente.nome_completo}",
                emprestimo=emprestimo,
                usuario=request.user,
                data=timezone.now()
            )

            # B) Depósito na Conta Digital (Contra-partida)
            Transacao.objects.create(
                tipo='DEPOSITO_CC',
                valor=abs(novo_valor), 
                descricao=f"Depósito C/C (Disponível p/ Saque) - {codigo_novo}",
                emprestimo=emprestimo,
                usuario=request.user,
                data=timezone.now()
            )

            # C) Crédito na Conta Cliente (App)
            conta_cli, _ = ContaCorrente.objects.get_or_create(cliente=proposta.cliente)
            MovimentacaoConta.objects.create(
                conta=conta_cli,
                tipo='CREDITO',
                origem='EMPRESTIMO',
                valor=abs(novo_valor),
                descricao=f"Liberação de Empréstimo {codigo_novo}",
                data=timezone.now(),
                emprestimo=emprestimo
            )
            
            proposta.emprestimo_gerado = emprestimo
            proposta.save()
            
            messages.success(request, f"Contrato {codigo_novo} aprovado e valor creditado na conta do cliente.")
            
        return redirect('emprestimos:listar_propostas')

    return render(request, 'emprestimos/propostas/analise.html', {
        'p': proposta,
        'dossie': dossie,
        'todos_parceiros': todos_parceiros
    })