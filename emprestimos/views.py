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
    return render(request, "emprestimos/contrato_detalhe.html", {
        "contrato": contrato,
        "parcelas": parcelas,
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
    
    if request.method == "POST":
        senha = request.POST.get("senha")
        
        if senha != "1234": 
            messages.error(request, "Senha de confirmação incorreta.")
            return redirect("emprestimos:contrato_detalhe", pk=parcela.emprestimo.id)

        if parcela.status == ParcelaStatus.PAGA:
            messages.warning(request, "Esta parcela já foi paga.")
            return redirect("emprestimos:contrato_detalhe", pk=parcela.emprestimo.id)

        with transaction.atomic():
            dados = parcela.dados_atualizados
            valor_final = dados['total']

            # 1. Baixa a Parcela
            parcela.status = ParcelaStatus.PAGA
            parcela.data_pagamento = timezone.now()
            parcela.valor_pago = valor_final
            parcela.save()

            # 2. Caixa da Empresa (Entrada de Dinheiro)
            Transacao.objects.create(
                tipo='PAGAMENTO_ENTRADA',
                valor=valor_final,
                descricao=f"Recebimento Parcela {parcela.numero} - {parcela.emprestimo.codigo_contrato}",
                usuario=request.user,
                emprestimo=parcela.emprestimo
            )

            # 3. Conta Corrente do Cliente (DÉBITO - O cliente pagou)
            try:
                # Pega a conta REAL do cliente (do app contas)
                conta_cliente, created = ContaCorrente.objects.get_or_create(cliente=parcela.emprestimo.cliente)
                
                # Cria a movimentação (isso atualiza o saldo automaticamente no model MovimentacaoConta)
                MovimentacaoConta.objects.create(
                    conta=conta_cliente,
                    tipo='DEBITO',
                    origem='PAGAMENTO_PARCELA',
                    valor=valor_final,
                    descricao=f"Pgto Parcela {parcela.numero} - Contrato {parcela.emprestimo.codigo_contrato}",
                    data=timezone.now(),
                    parcela=parcela
                )
            except Exception as e:
                print(f"ERRO CRÍTICO AO DEBITAR: {e}")

            # 4. Log do Sistema
            ContratoLog.objects.create(
                contrato=parcela.emprestimo,
                acao="PAGAMENTO",
                usuario=request.user,
                motivo=f"Parcela {parcela.numero} paga via sistema.",
                observacao=f"Valor total: R$ {valor_final}"
            )

            messages.success(request, f"Parcela {parcela.numero} paga e debitada da conta do cliente!")

    return redirect("emprestimos:contrato_detalhe", pk=parcela.emprestimo.id)

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