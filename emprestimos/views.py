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
from contas.models import ContaCorrente, MovimentacaoConta 

# === IMPORTS DE SERVIÇOS E UTILS ===
try:
    from .services import simular
except ImportError:
    simular = None

from .utils import gerar_codigo_contrato
from .services_analise import gerar_dossie_cliente


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
    
    # Lista de clientes para o Modal de Vínculo de Parceiro
    todos_clientes = Cliente.objects.all().order_by('nome_completo')

    return render(request, "emprestimos/contrato_detalhe.html", {
        "contrato": contrato,
        "parcelas": parcelas,
        "todos_clientes": todos_clientes,
        "hoje": timezone.localdate(),
    })

@login_required
def calcular_valores_parcela_ajax(request, parcela_id):
    """API para atualizar valores no modal de pagamento (com juros/multa em tempo real)"""
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
    
    # Configurações do Split
    parceiro = contrato.parceiro
    tem_split = parceiro is not None
    PORCENTAGEM_COMISSAO = Decimal('10.00') # Defina a % do parceiro aqui

    # Cálculos
    valor_total = parcela.valor_atual
    valor_honorarios = Decimal('0.00')
    nome_profissional = parceiro.nome_completo if parceiro else ""

    if tem_split:
        valor_honorarios = valor_total * (PORCENTAGEM_COMISSAO / Decimal('100'))
        valor_honorarios = valor_honorarios.quantize(Decimal('0.01'))

    if request.method == "POST":
        senha = request.POST.get("senha")
        
        # Validação Simples de Senha
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
                # O dinheiro entra integralmente na empresa primeiro
                descricao_cx = f"{contrato.cliente.nome_completo} pagou {valor_total}"
                if valor_honorarios > 0:
                    descricao_cx += f" (Split: {valor_honorarios} para {nome_profissional})"
                
                Transacao.objects.create(
                    tipo='PAGAMENTO_ENTRADA',
                    valor=abs(valor_total), # Força positivo
                    descricao=descricao_cx + f" - Parc. {parcela.numero}/{contrato.qtd_parcelas}",
                    usuario=request.user,
                    emprestimo=contrato
                )

                # C. Split: Repasse ao Parceiro (se houver)
                if valor_honorarios > 0 and parceiro:
                    # 1. PARTIDA 1: SAÍDA do Caixa da Empresa (NEGATIVO)
                    # Registra que houve uma despesa de comissão
                    Transacao.objects.create(
                        tipo='DESPESA',
                        valor=-abs(valor_honorarios), # Força negativo
                        descricao=f"Comissão Parceiro - {nome_profissional} (Ref. Contrato {contrato.codigo_contrato})",
                        usuario=request.user,
                        emprestimo=contrato
                    )

                    # === AJUSTE DE COMISSÃO ===
                    # 2. PARTIDA INTERMEDIÁRIA: Entrada Compensatória (POSITIVO)
                    # O dinheiro da comissão saiu do "Lucro" mas ficou no "Caixa" (na conta do parceiro)
                    Transacao.objects.create(
                        tipo='DEPOSITO_CC',
                        valor=abs(valor_honorarios), # Força positivo
                        descricao=f"Depósito C/C Parceiro (Retido) - {nome_profissional}",
                        usuario=request.user,
                        emprestimo=contrato
                    )
                    # ==========================

                    # 3. PARTIDA 2: ENTRADA na Conta do Parceiro (POSITIVO)
                    conta_prof, _ = ContaCorrente.objects.get_or_create(cliente=parceiro)
                    MovimentacaoConta.objects.create(
                        conta=conta_prof,
                        tipo='CREDITO',
                        origem='DEPOSITO',
                        valor=abs(valor_honorarios),
                        descricao=f"Comissão {PORCENTAGEM_COMISSAO}% - Ref. Cliente {contrato.cliente.nome_completo}",
                        data=timezone.now()
                    )

                # D. Logs de Auditoria
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

    # Contexto para renderização (GET)
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
                    
                    # Gera Código Único
                    emprestimo.codigo_contrato = gerar_codigo_contrato()
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

                    # === CORREÇÃO CONTÁBIL ===
                    
                    # 3. PARTIDA 1: SAÍDA do Caixa da Empresa (NEGATIVO)
                    Transacao.objects.create(
                        tipo='EMPRESTIMO_SAIDA',
                        valor=-abs(valor), # Força negativo
                        descricao=f"Saída Empréstimo Direto - {emprestimo.codigo_contrato}",
                        emprestimo=emprestimo,
                        usuario=request.user
                    )

                    # === AJUSTE DE CONTRA-PARTIDA ===
                    Transacao.objects.create(
                        tipo='DEPOSITO_CC',
                        valor=abs(valor), # Força positivo
                        descricao=f"Depósito C/C (Disponível p/ Saque) - {emprestimo.codigo_contrato}",
                        emprestimo=emprestimo,
                        usuario=request.user
                    )
                    # ================================

                    # 4. PARTIDA 2: ENTRADA na Conta Corrente do Cliente (POSITIVO)
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

                    messages.success(request, f"Contrato {emprestimo.codigo_contrato} criado e valor transferido para a conta do cliente!")
                    return redirect("emprestimos:contrato_detalhe", pk=emprestimo.id)

    else:
        form = EmprestimoForm()
    
    return render(request, "emprestimos/novo_form.html", {"form": form, "cliente": cliente})

# ==============================================================================
# 4. AÇÕES ADMINISTRATIVAS (CANCELAR, VINCULAR PARCEIRO, FILTROS)
# ==============================================================================

@login_required
def cancelar_contrato(request, pk):
    contrato = get_object_or_404(Emprestimo, pk=pk)
    if request.method == "POST":
        senha = request.POST.get("senha")
        if senha == "1234": # Senha padrão ou settings.MANAGER_PASSWORD
            with transaction.atomic():
                contrato.status = EmprestimoStatus.CANCELADO
                contrato.cancelado_em = timezone.now()
                contrato.cancelado_por = request.user
                contrato.motivo_cancelamento = request.POST.get("motivo")
                contrato.save()
                
                # Cancela parcelas em aberto
                contrato.parcelas.filter(status=ParcelaStatus.ABERTA).update(status=ParcelaStatus.CANCELADA)
                
                # Opcional: Estornar lançamentos financeiros aqui se necessário
                
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
        
        # Log
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
    """Painel da Esteira de Crédito"""
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
            valor_str = request.POST.get('valor', '0').replace('R$', '').replace('.', '').replace(',', '.')
            taxa_str = request.POST.get('taxa', '0').replace(',', '.')
            
            PropostaEmprestimo.objects.create(
                cliente_id=request.POST.get('cliente_id'),
                valor_solicitado=Decimal(valor_str),
                qtd_parcelas=int(request.POST.get('qtd_parcelas')),
                taxa_juros=Decimal(taxa_str),
                primeiro_vencimento=request.POST.get('vencimento'),
                usuario_solicitante=request.user,
                observacoes=request.POST.get('observacoes')
            )
            messages.success(request, "Proposta enviada para análise!")
            return redirect('emprestimos:listar_propostas')
        except Exception as e:
            messages.error(request, f"Erro: {e}")
    
    clientes = Cliente.objects.all().order_by('nome_completo')
    return render(request, 'emprestimos/propostas/form.html', {'clientes': clientes})

@login_required
@transaction.atomic
def analisar_proposta(request, proposta_id):
    """Gerente analisa, ajusta valores e aprova/nega"""
    proposta = get_object_or_404(PropostaEmprestimo, id=proposta_id)
    dossie = gerar_dossie_cliente(proposta.cliente)
    
    if request.method == 'POST':
        acao = request.POST.get('acao')
        parecer = request.POST.get('parecer')
        
        try:
            # Tratamento de input number/text
            valor_str = request.POST.get('valor_aprovado', '0').replace(',', '.')
            taxa_str = request.POST.get('taxa_aprovada', '0').replace(',', '.')
            
            # === CORREÇÃO: Garante que o valor seja positivo aqui ===
            novo_valor = abs(Decimal(valor_str))
            # ========================================================
            
            nova_taxa = Decimal(taxa_str)
            novo_qtd = int(request.POST.get('qtd_aprovada'))
        except:
            novo_valor = proposta.valor_solicitado
            nova_taxa = proposta.taxa_juros
            novo_qtd = proposta.qtd_parcelas

        proposta.usuario_aprovador = request.user
        proposta.data_analise = timezone.now()
        proposta.parecer_analise = parecer
        
        if acao == 'NEGAR':
            proposta.status = 'NEGADO'
            proposta.save()
            messages.info(request, "Proposta negada.")
            
        elif acao == 'APROVAR':
            # 1. Atualiza proposta
            proposta.valor_solicitado = novo_valor
            proposta.taxa_juros = nova_taxa
            proposta.qtd_parcelas = novo_qtd
            proposta.status = 'APROVADO'
            
            # 2. Gera Empréstimo Real
            if simular:
                _, parc_aplicada, total_ctr, _, parcelas_simuladas = simular(
                    valor_emprestado=novo_valor,
                    qtd_parcelas=novo_qtd,
                    taxa_juros_mensal=nova_taxa,
                    primeiro_vencimento=proposta.primeiro_vencimento
                )
            else:
                total_ctr = novo_valor 
                parc_aplicada = novo_valor / novo_qtd
                parcelas_simuladas = []

            codigo_novo = gerar_codigo_contrato()
            
            emprestimo = Emprestimo.objects.create(
                cliente=proposta.cliente,
                codigo_contrato=codigo_novo,
                valor_emprestado=novo_valor,
                qtd_parcelas=novo_qtd,
                taxa_juros_mensal=nova_taxa,
                primeiro_vencimento=proposta.primeiro_vencimento,
                valor_parcela_aplicada=parc_aplicada,
                total_contrato=total_ctr
            )
            
            # 3. Cria Parcelas
            lista_parcelas_db = []
            for p_simulada in parcelas_simuladas:
                try:
                    lista_parcelas_db.append(Parcela(
                        emprestimo=emprestimo,
                        numero=p_simulada.numero,
                        vencimento=p_simulada.vencimento,
                        valor=p_simulada.valor
                    ))
                except AttributeError:
                    lista_parcelas_db.append(Parcela(
                        emprestimo=emprestimo,
                        numero=p_simulada['numero'],
                        vencimento=p_simulada['vencimento'],
                        valor=p_simulada['valor']
                    ))
            Parcela.objects.bulk_create(lista_parcelas_db)
            
            # === CORREÇÃO CONTÁBIL NA APROVAÇÃO ===

            # 4. PARTIDA 1: SAÍDA do Caixa da Empresa (NEGATIVO)
            Transacao.objects.create(
                tipo='EMPRESTIMO_SAIDA',
                valor=-abs(novo_valor), # Força negativo
                descricao=f"Aprovado via Esteira - {codigo_novo}",
                emprestimo=emprestimo,
                usuario=request.user
            )

            # === AJUSTE DE CONTRA-PARTIDA ===
            Transacao.objects.create(
                tipo='DEPOSITO_CC',
                valor=abs(novo_valor), # Força positivo
                descricao=f"Depósito C/C (Disponível p/ Saque) - {codigo_novo}",
                emprestimo=emprestimo,
                usuario=request.user
            )
            # ================================

            # 5. PARTIDA 2: ENTRADA na Conta do Cliente (POSITIVO)
            conta_cli, _ = ContaCorrente.objects.get_or_create(cliente=proposta.cliente)
            MovimentacaoConta.objects.create(
                conta=conta_cli,
                tipo='CREDITO',
                origem='EMPRESTIMO',
                valor=abs(novo_valor),
                descricao=f"Liberação Contrato {codigo_novo}",
                data=timezone.now(),
                emprestimo=emprestimo
            )
            
            proposta.emprestimo_gerado = emprestimo
            proposta.save()
            
            messages.success(request, f"Aprovado! Valor depositado na conta do cliente.")
            
        return redirect('emprestimos:listar_propostas')

    return render(request, 'emprestimos/propostas/analise.html', {
        'p': proposta, 
        'dossie': dossie
    })