import os
import uuid
from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.db import transaction
from django.db.models import Q

# Importações dos Models
from .models import Emprestimo, Parcela, ParcelaStatus, EmprestimoStatus, ContratoLog
from .forms import EmprestimoForm, BuscaClienteForm
from clientes.models import Cliente
from financeiro.models import Transacao






from .models import Parcela, ParcelaStatus, ContratoLog
from financeiro.models import Transacao
from contas.models import ContaCorrente, MovimentacaoConta
from cobranca.models import CarteiraCobranca

# === CORREÇÃO AQUI: Importar do app 'contas' (o sistema real) ===
from contas.models import ContaCorrente, MovimentacaoConta 

# Tenta importar serviço de simulação, se não existir usa fallback
try:
    from .services import simular
except ImportError:
    simular = None

@login_required
def lista_contratos(request):
    contratos = Emprestimo.objects.all().order_by('-criado_em')
    return render(request, "emprestimos/contratos.html", {"contratos": contratos})

@login_required
def contrato_detalhe(request, pk):
    contrato = get_object_or_404(Emprestimo, pk=pk)
    parcelas = contrato.parcelas.all().order_by('numero')
    
    # Adicionamos isso para popular o Modal de Parceiro
    todos_clientes = Cliente.objects.all().order_by('nome_completo')

    return render(request, "emprestimos/contrato_detalhe.html", {
        "contrato": contrato,
        "parcelas": parcelas,
        "todos_clientes": todos_clientes, # <--- Enviando a lista para o template
        "hoje": timezone.localdate(),
    })

@login_required
def calcular_valores_parcela_ajax(request, parcela_id):
    parcela = get_object_or_404(Parcela, id=parcela_id)
    dados = parcela.dados_atualizados 
    return JsonResponse({
        'valor_original': f"{dados['valor_original']:.2f}",
        'multa': f"{dados['multa']:.2f}",
        'juros': f"{dados['juros']:.2f}",
        'total': f"{dados['total']:.2f}"
    })

@login_required
def pagar_parcela(request, pk):
    parcela = get_object_or_404(Parcela, pk=pk)
    contrato = parcela.emprestimo
    
    # 1. Verifica se tem Parceiro vinculado no contrato (Lógica Nova)
    parceiro = contrato.parceiro
    tem_split = parceiro is not None
    
    # Defina aqui a porcentagem padrão do parceiro (ex: 10% ou 20%)
    PORCENTAGEM_COMISSAO = Decimal('10.00') 

    # 2. Cálculos Prévios
    valor_total = parcela.valor_atual
    valor_honorarios = Decimal('0.00')
    valor_empresa = valor_total
    nome_profissional = parceiro.nome_completo if parceiro else ""

    if tem_split:
        valor_honorarios = valor_total * (PORCENTAGEM_COMISSAO / Decimal('100'))
        valor_honorarios = valor_honorarios.quantize(Decimal('0.01'))
        valor_empresa = valor_total - valor_honorarios

    if request.method == "POST":
        senha = request.POST.get("senha")
        
        # === CORREÇÃO 1: SENHA FIXA "1234" ===
        if senha != "1234":
             messages.error(request, "Senha incorreta. Tente 1234.")
             return redirect('emprestimos:contrato_detalhe', pk=contrato.id)
        # =====================================

        try:
            with transaction.atomic():
                # A. Baixa a Parcela
                parcela.status = ParcelaStatus.PAGA
                parcela.data_pagamento = timezone.now()
                parcela.valor_pago = valor_total
                parcela.save()

                # B. Registra Entrada no Livro Caixa
                descricao_cx = f"{contrato.cliente.nome_completo} pagou {valor_total}"
                if valor_honorarios > 0:
                    descricao_cx += f" (Split: {valor_honorarios} para {nome_profissional})"
                
                Transacao.objects.create(
                    tipo='ENTRADA', # Ajuste se seu model usar outro termo (ex: PAGAMENTO_ENTRADA)
                    valor=valor_total,
                    descricao=descricao_cx + f" - Parc. {parcela.numero}/{contrato.qtd_parcelas}",
                    usuario=request.user,
                    emprestimo=contrato
                )

                # C. Se tiver Parceiro, faz o repasse (Split)
                if valor_honorarios > 0 and parceiro:
                    # 1. Saída do Caixa da Empresa (Despesa)
                    Transacao.objects.create(
                        tipo='SAIDA', # Ajuste se seu model usar 'DESPESA'
                        valor=-valor_honorarios,
                        descricao=f"Comissão Parceiro - {nome_profissional} (Ref. Contrato {contrato.codigo_contrato})",
                        usuario=request.user
                    )

                    # 2. Crédito na conta do Parceiro (Carteira Virtual)
                    conta_prof, _ = ContaCorrente.objects.get_or_create(cliente=parceiro)
                    MovimentacaoConta.objects.create(
                        conta=conta_prof,
                        tipo='CREDITO',
                        origem='DEPOSITO',
                        valor=valor_honorarios,
                        descricao=f"Comissão {PORCENTAGEM_COMISSAO}% - Ref. Cliente {contrato.cliente.nome_completo}",
                        data=timezone.now()
                    )

                # D. Logs
                ContratoLog.objects.create(
                    contrato=contrato,
                    acao="PAGO",
                    usuario=request.user,
                    motivo=f"Parcela {parcela.numero} quitada.",
                    observacao=f"Valor: {valor_total} | Comissao: {valor_honorarios}"
                )
                
                messages.success(request, f"Pagamento confirmado! R$ {valor_honorarios} enviado para {nome_profissional}.")
                return redirect("emprestimos:contrato_detalhe", pk=contrato.id)

        except Exception as e:
            messages.error(request, f"Erro ao processar: {e}")
            # Se der erro, volta para a tela do contrato em vez de renderizar form quebrado
            return redirect("emprestimos:contrato_detalhe", pk=contrato.id)

    # Contexto para o Modal (caso a view seja chamada via GET, embora o modal use JS)
    context = {
        'parcela': parcela,
        'valor_total': valor_total,
        'tem_split': 'true' if tem_split else 'false',
        'nome_profissional': nome_profissional,
        'valor_honorarios': valor_honorarios,
        'percentual': PORCENTAGEM_COMISSAO,
    }
    return render(request, "emprestimos/pagar_parcela.html", context)


@login_required
def novo_emprestimo_busca(request):
    form = BuscaClienteForm(request.GET or None)
    clientes = None
    if form.is_valid():
        q = form.cleaned_data.get('query')
        clientes = Cliente.objects.filter(nome_completo__icontains=q) | Cliente.objects.filter(cpf__icontains=q)
    return render(request, "emprestimos/novo_busca.html", {"form": form, "clientes": clientes})

@login_required
def novo_emprestimo_form(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    simulacao = None

    if request.method == "POST":
        form = EmprestimoForm(request.POST)
        
        if form.is_valid():
            # Dados limpos
            valor = form.cleaned_data['valor_emprestado']
            taxa = form.cleaned_data['taxa_juros_mensal']
            qtd = form.cleaned_data['qtd_parcelas']
            primeiro_venc = form.cleaned_data['primeiro_vencimento']

            # --- LÓGICA DE SIMULAÇÃO ---
            if 'simular' in request.POST:
                juros_total = valor * (taxa / 100) * qtd
                montante_final = valor + juros_total
                valor_parcela = montante_final / qtd

                lista_parcelas = []
                from dateutil.relativedelta import relativedelta
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
                messages.info(request, "Simulação atualizada. Confira os valores.")
                return render(request, "emprestimos/novo_form.html", {
                    "form": form, "cliente": cliente, "simulacao": simulacao
                })

            # --- LÓGICA DE CONFIRMAÇÃO (SALVAR) ---
            elif 'confirmar_cadastro' in request.POST:
                with transaction.atomic():
                    emprestimo = form.save(commit=False)
                    emprestimo.cliente = cliente
                    emprestimo.usuario = request.user
                    
                    # Gera Código Único
                    agora = timezone.now()
                    uuid_code = str(uuid.uuid4())[:4].upper()
                    emprestimo.codigo_contrato = f"{agora.strftime('%Y%m%d')}-{cliente.id}-{uuid_code}"
                    
                    emprestimo.save() 

                    # Gera as Parcelas
                    juros_total = valor * (taxa / 100) * qtd
                    montante_final = valor + juros_total
                    valor_parcela = montante_final / qtd
                    
                    from dateutil.relativedelta import relativedelta
                    data_atual = primeiro_venc
                    
                    for i in range(1, qtd + 1):
                        Parcela.objects.create(
                            emprestimo=emprestimo,
                            numero=i,
                            vencimento=data_atual,
                            valor=valor_parcela
                        )
                        data_atual = data_atual + relativedelta(months=1)

                    # === CORREÇÃO: CREDITAR NA CONTA REAL DO CLIENTE ===
                    try:
                        # 1. Garante que o cliente tem uma conta no sistema 'contas'
                        conta_real, created = ContaCorrente.objects.get_or_create(cliente=cliente)
                        
                        # 2. Cria a movimentação no sistema correto
                        MovimentacaoConta.objects.create(
                            conta=conta_real,
                            tipo='CREDITO',
                            origem='EMPRESTIMO', # Usando o choice correto do seu model
                            valor=valor,
                            descricao=f"Liberação Empréstimo {emprestimo.codigo_contrato}",
                            data=timezone.now(),
                            emprestimo=emprestimo
                        )
                    except Exception as e:
                        print(f"ERRO CRÍTICO AO CREDITAR: {e}")

                    messages.success(request, f"Contrato {emprestimo.codigo_contrato} gerado e valor creditado na conta!")
                    return redirect("emprestimos:contrato_detalhe", pk=emprestimo.id)

    else:
        form = EmprestimoForm()
    
    return render(request, "emprestimos/novo_form.html", {"form": form, "cliente": cliente})

@login_required
def cancelar_contrato(request, pk):
    contrato = get_object_or_404(Emprestimo, pk=pk)
    if request.method == "POST":
        senha = request.POST.get("senha")
        if senha == "admin123": 
            with transaction.atomic():
                contrato.status = EmprestimoStatus.CANCELADO
                contrato.cancelado_em = timezone.now()
                contrato.cancelado_por = request.user
                contrato.motivo_cancelamento = request.POST.get("motivo")
                contrato.save()
                contrato.parcelas.filter(status=ParcelaStatus.ABERTA).update(status=ParcelaStatus.CANCELADO)
                
                messages.error(request, f"Contrato {contrato.codigo_contrato} cancelado.")
        else:
            messages.error(request, "Senha administrativa incorreta.")
    return redirect("emprestimos:contrato_detalhe", pk=contrato.id)

@login_required
def a_vencer(request):
    hoje = timezone.localdate()
    parcelas = Parcela.objects.filter(status=ParcelaStatus.ABERTA, vencimento__gte=hoje).order_by('vencimento')
    q = request.GET.get('q')
    if q:
        parcelas = parcelas.filter(
            Q(emprestimo__cliente__nome_completo__icontains=q) | 
            Q(emprestimo__codigo_contrato__icontains=q)
        )
    return render(request, "emprestimos/a_vencer.html", {"parcelas": parcelas, "hoje": hoje})

@login_required
def vencidos(request):
    hoje = timezone.localdate()
    parcelas = Parcela.objects.filter(status=ParcelaStatus.ABERTA, vencimento__lt=hoje).order_by('vencimento')
    return render(request, "emprestimos/vencidos.html", {"parcelas": parcelas, "hoje": hoje})


@login_required
def vincular_parceiro(request, pk):
    contrato = get_object_or_404(Emprestimo, pk=pk)
    
    if request.method == "POST":
        parceiro_id = request.POST.get("parceiro_id")
        
        # Lógica para auditoria (Log)
        antigo_parceiro = contrato.parceiro.nome_completo if contrato.parceiro else "Nenhum"
        
        if parceiro_id:
            novo_parceiro = get_object_or_404(Cliente, id=parceiro_id)
            contrato.parceiro = novo_parceiro
            nome_novo = novo_parceiro.nome_completo
            msg = f"Parceiro alterado de {antigo_parceiro} para {nome_novo}"
        else:
            contrato.parceiro = None
            nome_novo = "Nenhum"
            msg = f"Parceiro removido (Era: {antigo_parceiro})"
            
        contrato.save()
        
        # Cria Log
        ContratoLog.objects.create(
            contrato=contrato,
            acao="RENEGOCIADO", # Ou crie um tipo 'ALTERACAO_CADASTRO' se preferir
            usuario=request.user,
            motivo="Alteração de Parceiro/Recebedor",
            observacao=msg
        )
        
        messages.success(request, f"Sucesso: {msg}")
        
    return redirect("emprestimos:contrato_detalhe", pk=contrato.id)