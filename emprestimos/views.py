from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.db import transaction

from .models import Emprestimo, Parcela, ParcelaStatus, EmprestimoStatus, ContratoLog
from .forms import EmprestimoForm, BuscaClienteForm
from clientes.models import Cliente
from financeiro.models import Transacao

@login_required
def lista_contratos(request):
    contratos = Emprestimo.objects.all().order_by('-criado_em')
    return render(request, "emprestimos/contratos.html", {"contratos": contratos})

@login_required
def contrato_detalhe(request, pk):
    """
    Exibe os detalhes de um contrato e suas parcelas.
    Envia a variável 'hoje' para permitir a comparação de atraso no template.
    """
    contrato = get_object_or_404(Emprestimo, pk=pk)
    parcelas = contrato.parcelas.all().order_by('numero')
    
    return render(request, "emprestimos/contrato_detalhe.html", {
        "contrato": contrato,
        "parcelas": parcelas,
        "hoje": timezone.localdate(),  # Necessário para destacar parcelas vencidas
    })

@login_required
def calcular_valores_parcela_ajax(request, parcela_id):
    """
    Endpoint para o modal de pagamento. 
    Retorna o cálculo em tempo real de juros e multa baseado na data atual.
    """
    parcela = get_object_or_404(Parcela, id=parcela_id)
    # Utiliza a propriedade 'dados_atualizados' definida no seu Model Parcela
    dados = parcela.dados_atualizados 
    
    return JsonResponse({
        'valor_original': f"{dados['valor_original']:.2f}",
        'multa': f"{dados['multa']:.2f}",
        'juros': f"{dados['juros']:.2f}",
        'total': f"{dados['total']:.2f}"
    })

@login_required
def pagar_parcela(request, pk):
    """
    Processa a baixa de uma parcela com verificação de senha.
    """
    parcela = get_object_or_404(Parcela, pk=pk)
    
    if request.method == "POST":
        senha = request.POST.get("senha")
        
        # Validação simples de senha (ajuste conforme seu sistema de permissões)
        if senha != "1234": 
            messages.error(request, "Senha de confirmação incorreta.")
            return redirect("emprestimos:contrato_detalhe", pk=parcela.emprestimo.id)

        if parcela.status == ParcelaStatus.PAGO:
            messages.warning(request, "Esta parcela já foi paga.")
            return redirect("emprestimos:contrato_detalhe", pk=parcela.emprestimo.id)

        with transaction.atomic():
            dados = parcela.dados_atualizados
            valor_final = dados['total']

            # 1. Atualiza a parcela
            parcela.status = ParcelaStatus.PAGO
            parcela.data_pagamento = timezone.now()
            parcela.valor_pago = valor_final
            parcela.save()

            # 2. Gera transação no financeiro
            Transacao.objects.create(
                tipo='entrada',
                valor=valor_final,
                descricao=f"Recebimento Parcela {parcela.numero} - Contrato {parcela.emprestimo.codigo_contrato}",
                categoria="Empréstimos",
                usuario=request.user
            )

            # 3. Log do Contrato
            ContratoLog.objects.create(
                emprestimo=parcela.emprestimo,
                acao="PAGAMENTO",
                usuario=request.user,
                motivo=f"Parcela {parcela.numero} paga via sistema.",
                observacao=f"Valor total: R$ {valor_final}"
            )

            messages.success(request, f"Parcela {parcela.numero} baixada com sucesso!")

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
    if request.method == "POST":
        form = EmprestimoForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                emprestimo = form.save(commit=False)
                emprestimo.cliente = cliente
                emprestimo.usuario = request.user
                emprestimo.save()
                
                # O método save do model Emprestimo gera as parcelas automaticamente
                messages.success(request, "Contrato gerado com sucesso!")
                return redirect("emprestimos:contrato_detalhe", pk=emprestimo.id)
    else:
        form = EmprestimoForm()
    
    return render(request, "emprestimos/novo_form.html", {"form": form, "cliente": cliente})

@login_required
def cancelar_contrato(request, pk):
    contrato = get_object_or_404(Emprestimo, pk=pk)
    if request.method == "POST":
        senha = request.POST.get("senha")
        if senha == "admin123": # Exemplo de verificação
            with transaction.atomic():
                contrato.status = EmprestimoStatus.CANCELADO
                contrato.cancelado_em = timezone.now()
                contrato.cancelado_por = request.user
                contrato.motivo_cancelamento = request.POST.get("motivo")
                contrato.save()
                
                # Cancela parcelas em aberto
                contrato.parcelas.filter(status=ParcelaStatus.ABERTA).update(status=ParcelaStatus.CANCELADO)
                
                messages.error(request, f"Contrato {contrato.codigo_contrato} cancelado.")
        else:
            messages.error(request, "Senha administrativa incorreta.")
            
    return redirect("emprestimos:contrato_detalhe", pk=contrato.id)