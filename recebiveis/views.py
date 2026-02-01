from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db import transaction

from .models import ContratoRecebivel, ItemRecebivel
from .forms import ContratoRecebivelForm, ItemRecebivelForm, AtivacaoForm, RenegociacaoForm
from financeiro.models import Transacao, calcular_saldo_atual
from contas.models import ContaCorrente, MovimentacaoConta

def lista_contratos(request):
    # Alterado para prefetch_related para carregar os itens no modal sem travar o banco de dados
    contratos = ContratoRecebivel.objects.prefetch_related('itens').all().order_by('-id')
    return render(request, 'recebiveis/lista.html', {'contratos': contratos})

def criar_contrato(request):
    if request.method == 'POST':
        form = ContratoRecebivelForm(request.POST)
        if form.is_valid():
            contrato = form.save()
            return redirect('adicionar_item', contrato_id=contrato.id)
    else:
        form = ContratoRecebivelForm()
    return render(request, 'recebiveis/criar.html', {'form': form})

def adicionar_item(request, contrato_id):
    contrato = get_object_or_404(ContratoRecebivel, id=contrato_id)
    if request.method == 'POST':
        form = ItemRecebivelForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.contrato = contrato
            item.save()
            messages.success(request, 'Item adicionado. Adicione mais ou simule.')
            return redirect('adicionar_item', contrato_id=contrato.id)
    else:
        form = ItemRecebivelForm()
    return render(request, 'recebiveis/adicionar_item.html', {'form': form, 'contrato': contrato})

def simular_contrato(request, contrato_id):
    contrato = get_object_or_404(ContratoRecebivel, id=contrato_id)
    contrato.calcular_valores()
    
    valor_do_desconto = contrato.valor_bruto - contrato.valor_liquido
    
    return render(request, 'recebiveis/simulacao.html', {
        'contrato': contrato, 
        'valor_do_desconto': valor_do_desconto
    })

def editar_item(request, item_id):
    item = get_object_or_404(ItemRecebivel, id=item_id)
    contrato_id = item.contrato.id
    
    if request.method == 'POST':
        form = ItemRecebivelForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, 'Item editado com sucesso.')
        else:
            messages.error(request, 'Erro ao editar o item. Verifique os valores.')
    
    return redirect('adicionar_item', contrato_id=contrato_id)

def excluir_item(request, item_id):
    item = get_object_or_404(ItemRecebivel, id=item_id)
    contrato_id = item.contrato.id
    
    if request.method == 'POST':
        item.delete()
        messages.success(request, 'Item excluído com sucesso.')
    
    return redirect('adicionar_item', contrato_id=contrato_id)

def ativar_contrato(request, contrato_id):
    contrato = get_object_or_404(ContratoRecebivel, id=contrato_id)
    
    # Recalcula para garantir valores atualizados antes de ativar
    contrato.calcular_valores()

    if contrato.status != 'simulado':
        messages.error(request, 'Contrato já ativado ou renegociado.')
        return redirect('lista_contratos')
    
    if request.method == 'POST':
        form = AtivacaoForm(request.POST)
        if form.is_valid():
            if form.cleaned_data['senha'] == '1234':
                
                saque_inicial = form.cleaned_data.get('saque_inicial') or Decimal('0.00')
                
                # Validação de Saldo da Empresa (apenas se houver saque físico)
                if saque_inicial > 0:
                    saldo_caixa = calcular_saldo_atual()
                    if saldo_caixa < saque_inicial:
                        messages.error(request, f'Saldo em caixa insuficiente. Disponível: R$ {saldo_caixa:,.2f}')
                        return render(request, 'recebiveis/ativar.html', {'form': form, 'contrato': contrato})

                with transaction.atomic():
                    # 1. Atualiza status
                    contrato.status = 'ativo'
                    contrato.data_ativacao = timezone.now()
                    contrato.save()
                    
                    # 2. Gestão da Conta Corrente
                    conta, _ = ContaCorrente.objects.get_or_create(cliente=contrato.cliente)

                    # 2.1 Crédito do Valor Bruto (Total dos Cheques/Títulos)
                    MovimentacaoConta.objects.create(
                        conta=conta,
                        tipo='CREDITO',
                        origem='EMPRESTIMO', # Origem genérica para entrada de crédito concedido
                        valor=contrato.valor_bruto,
                        descricao=f"Crédito Antecipação {contrato.contrato_id} ({contrato.itens.count()} títulos)",
                    )

                    # 2.2 Débito do Desconto (Taxas/Juros)
                    desconto = contrato.valor_bruto - contrato.valor_liquido
                    if desconto > 0:
                        MovimentacaoConta.objects.create(
                            conta=conta,
                            tipo='DEBITO',
                            origem='TAXA',
                            valor=desconto,
                            descricao=f"Deságio/Taxas Antecipação {contrato.contrato_id}"
                        )

                    # 2.3 Saque Inicial (Opcional)
                    if saque_inicial > 0:
                        MovimentacaoConta.objects.create(
                            conta=conta,
                            tipo='DEBITO',
                            origem='SAQUE',
                            valor=saque_inicial,
                            descricao=f"Saque na Antecipação {contrato.contrato_id}"
                        )

                        # 3. Transação Financeira (Saída do Caixa Físico)
                        Transacao.objects.create(
                            tipo='ANTECIPAÇÃO DE RECEBÍVEIS',
                            valor=-saque_inicial, # Valor negativo = saída
                            descricao=f'Saque Adiantamento {contrato.contrato_id} - {contrato.cliente.nome_completo}',
                            data=timezone.now()
                        )

                messages.success(request, f'Contrato {contrato.contrato_id} ativado. Valores lançados na conta do cliente.')
                return redirect('lista_contratos')
            else:
                messages.error(request, 'Senha incorreta.')
    else:
        # Sugere o valor líquido total como saque inicial por padrão
        form = AtivacaoForm(initial={'saque_inicial': contrato.valor_liquido})
    
    return render(request, 'recebiveis/ativar.html', {'form': form, 'contrato': contrato})

# ========================================================
# NOVAS FUNÇÕES DE LIQUIDAÇÃO
# ========================================================

def liquidar_item(request, item_id):
    item = get_object_or_404(ItemRecebivel, id=item_id)
    
    if request.method == 'POST':
        if item.status == 'pago':
            messages.warning(request, 'Este item já foi liquidado.')
            return redirect('lista_contratos')

        # Atualiza Item
        item.status = 'pago'
        item.data_pagamento = timezone.now()
        item.save()

        # Atualiza status do Contrato se tudo estiver pago
        item.contrato.atualizar_status()

        # Registra no Financeiro (Entrada de Dinheiro - Compensação)
        Transacao.objects.create(
            tipo='PAGAMENTO_ENTRADA',
            valor=item.valor,
            descricao=f"Liquidação Item {item.numero} - {item.contrato.contrato_id}",
            data=timezone.now()
        )

        messages.success(request, f'Item {item.numero} liquidado com sucesso.')
    
    return redirect('lista_contratos')

def liquidar_contrato(request, contrato_id):
    contrato = get_object_or_404(ContratoRecebivel, id=contrato_id)
    
    if request.method == 'POST':
        if contrato.status == 'liquidado':
            messages.warning(request, 'Contrato já está liquidado.')
            return redirect('lista_contratos')
            
        itens_abertos = contrato.itens.filter(status='aberto')
        if not itens_abertos.exists():
            messages.info(request, 'Não há itens em aberto para liquidar.')
            return redirect('lista_contratos')

        total_liquidado = 0
        for item in itens_abertos:
            item.status = 'pago'
            item.data_pagamento = timezone.now()
            item.save()
            total_liquidado += item.valor
        
        contrato.status = 'liquidado'
        contrato.save()

        # Registra no Financeiro o valor total
        Transacao.objects.create(
            tipo='PAGAMENTO_ENTRADA',
            valor=total_liquidado,
            descricao=f"Liquidação Total Contrato {contrato.contrato_id}",
            data=timezone.now()
        )

        messages.success(request, f'Contrato {contrato.contrato_id} liquidado com sucesso.')

    return redirect('lista_contratos')