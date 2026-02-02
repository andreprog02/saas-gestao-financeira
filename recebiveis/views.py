from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum

from .models import ContratoRecebivel, ItemRecebivel
from .forms import ContratoRecebivelForm, ItemRecebivelForm, AtivacaoForm
from financeiro.models import Transacao, calcular_saldo_atual
from contas.models import ContaCorrente, MovimentacaoConta

def lista_contratos(request):
    """
    Lista todos os contratos, ordenados do mais recente para o mais antigo.
    Usa prefetch_related para otimizar a busca dos itens.
    """
    contratos = ContratoRecebivel.objects.prefetch_related('itens').all().order_by('-id')
    return render(request, 'recebiveis/lista.html', {'contratos': contratos})

def criar_contrato(request):
    """
    Inicia uma nova simulação de antecipação.
    """
    if request.method == 'POST':
        form = ContratoRecebivelForm(request.POST)
        if form.is_valid():
            try:
                contrato = form.save()
                messages.success(request, 'Simulação iniciada! Agora adicione os títulos/cheques.')
                return redirect('adicionar_item', contrato_id=contrato.id)
            except Exception as e:
                messages.error(request, f"Erro ao criar simulação: {e}")
    else:
        form = ContratoRecebivelForm()
    
    return render(request, 'recebiveis/criar.html', {'form': form})

def adicionar_item(request, contrato_id):
    """
    Adiciona cheques ou títulos ao contrato em simulação.
    """
    contrato = get_object_or_404(ContratoRecebivel, id=contrato_id)
    
    # Recalcula valores sempre que entra na tela para garantir consistência
    contrato.calcular_valores()
    
    if request.method == 'POST':
        form = ItemRecebivelForm(request.POST)
        if form.is_valid():
            try:
                item = form.save(commit=False)
                item.contrato = contrato
                item.save()
                
                # Atualiza totais do contrato
                contrato.calcular_valores()
                
                messages.success(request, 'Item adicionado com sucesso.')
                return redirect('adicionar_item', contrato_id=contrato.id)
            except Exception as e:
                messages.error(request, f"Erro ao adicionar item: {e}")
    else:
        form = ItemRecebivelForm()
        
    return render(request, 'recebiveis/adicionar_item.html', {'form': form, 'contrato': contrato})

def editar_item(request, item_id):
    item = get_object_or_404(ItemRecebivel, id=item_id)
    contrato_id = item.contrato.id
    
    if request.method == 'POST':
        form = ItemRecebivelForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            item.contrato.calcular_valores()
            messages.success(request, 'Item atualizado.')
        else:
            messages.error(request, 'Erro ao atualizar item. Verifique os valores.')
    
    return redirect('adicionar_item', contrato_id=contrato_id)

def excluir_item(request, item_id):
    item = get_object_or_404(ItemRecebivel, id=item_id)
    contrato = item.contrato
    
    if request.method == 'POST':
        item.delete()
        contrato.calcular_valores()
        messages.success(request, 'Item removido.')
    
    return redirect('adicionar_item', contrato_id=contrato.id)

# === NOVA FUNÇÃO: EXCLUIR CONTRATO ===
def excluir_contrato(request, contrato_id):
    """
    Permite excluir uma simulação inteira e seus itens.
    Protegido para não excluir contratos já ativos/pagos.
    """
    contrato = get_object_or_404(ContratoRecebivel, id=contrato_id)
    
    if request.method == 'POST':
        if contrato.status != 'simulado':
            messages.error(request, 'Não é possível excluir contratos Ativos ou Liquidados.')
            return redirect('lista_contratos')
            
        try:
            contrato.delete()
            messages.success(request, 'Simulação excluída com sucesso.')
        except Exception as e:
            messages.error(request, f'Erro ao excluir: {e}')
            
    return redirect('lista_contratos')

def simular_contrato(request, contrato_id):
    contrato = get_object_or_404(ContratoRecebivel, id=contrato_id)
    contrato.calcular_valores()
    
    # Cálculo para exibição apenas
    valor_do_desconto = contrato.valor_bruto - contrato.valor_liquido
    
    return render(request, 'recebiveis/simulacao.html', {
        'contrato': contrato, 
        'valor_do_desconto': valor_do_desconto
    })

def ativar_contrato(request, contrato_id):
    """
    Efetiva o contrato:
    1. Muda status para ativo.
    2. Credita Valor LÍQUIDO na conta do cliente.
    3. Se houver Saque Inicial (dinheiro na mão), debita da conta e registra saída do caixa.
    """
    contrato = get_object_or_404(ContratoRecebivel, id=contrato_id)
    contrato.calcular_valores() # Garante valores frescos

    if contrato.status != 'simulado':
        messages.warning(request, 'Este contrato já foi ativado.')
        return redirect('lista_contratos')
    
    if request.method == 'POST':
        form = AtivacaoForm(request.POST)
        if form.is_valid():
            # Verifica senha administrativa (ajuste conforme sua config)
            if form.cleaned_data['senha'] == '1234':
                
                saque_inicial = form.cleaned_data.get('saque_inicial') or Decimal('0.00')
                
                # Validação de Caixa da Empresa (apenas se for sair dinheiro físico)
                if saque_inicial > 0:
                    saldo_caixa = calcular_saldo_atual()
                    # Opcional: Bloquear ou apenas avisar. Aqui estamos bloqueando se não tiver saldo.
                    if saldo_caixa < saque_inicial:
                        messages.error(request, f'Saldo em caixa da empresa insuficiente para o saque inicial. Disponível: R$ {saldo_caixa:,.2f}')
                        return render(request, 'recebiveis/ativar.html', {'form': form, 'contrato': contrato})

                try:
                    with transaction.atomic():
                        # 1. Atualiza Status do Contrato
                        contrato.status = 'ativo'
                        contrato.data_ativacao = timezone.now()
                        contrato.save()
                        
                        # 2. Gestão da Conta Corrente do Cliente
                        conta, _ = ContaCorrente.objects.get_or_create(cliente=contrato.cliente)

                        # 2.1 CRÉDITO DO VALOR LÍQUIDO (Já descontada a taxa)
                        # O cliente recebe o direito ao valor líquido total
                        MovimentacaoConta.objects.create(
                            conta=conta,
                            tipo='CREDITO',
                            origem='ANTECIPACAO',
                            valor=contrato.valor_liquido,
                            descricao=f"Crédito Antecipação {contrato.contrato_id} (Líquido)",
                        )

                        # 2.2 DÉBITO DO SAQUE INICIAL (Se o cliente pegou dinheiro na hora)
                        if saque_inicial > 0:
                            MovimentacaoConta.objects.create(
                                conta=conta,
                                tipo='DEBITO',
                                origem='SAQUE',
                                valor=saque_inicial,
                                descricao=f"Saque Inicial Antecipação {contrato.contrato_id}"
                            )

                            # 3. Registro no Fluxo de Caixa da Empresa (Saída real de dinheiro)
                            Transacao.objects.create(
                                tipo='ANTECIPAÇÃO DE RECEBÍVEIS',
                                valor=-saque_inicial, # Negativo pois saiu do caixa
                                descricao=f'Saque Adiantamento {contrato.contrato_id} - {contrato.cliente.nome_completo}',
                                data=timezone.now()
                            )

                    messages.success(request, f'Contrato {contrato.contrato_id} ativado com sucesso! Valor líquido creditado.')
                    return redirect('lista_contratos')

                except Exception as e:
                    messages.error(request, f"Erro crítico ao ativar contrato: {e}")
                    return redirect('lista_contratos')

            else:
                messages.error(request, 'Senha de confirmação incorreta.')
    else:
        # Sugere o valor líquido total como saque inicial, mas o usuário pode mudar
        form = AtivacaoForm(initial={'saque_inicial': contrato.valor_liquido})
    
    return render(request, 'recebiveis/ativar.html', {'form': form, 'contrato': contrato})

def liquidar_item(request, item_id):
    """
    Marca um item específico (cheque) como pago/compensado.
    Gera entrada de dinheiro no caixa da empresa.
    """
    item = get_object_or_404(ItemRecebivel, id=item_id)
    
    if request.method == 'POST':
        if item.status == 'pago':
            messages.warning(request, 'Item já liquidado.')
            return redirect('lista_contratos')

        try:
            with transaction.atomic():
                item.status = 'pago'
                item.data_pagamento = timezone.now()
                item.save()

                # Verifica se todos os itens do contrato foram pagos
                contrato = item.contrato
                contrato.atualizar_status() # Método deve existir no model para checar se tudo está pago

                # Registra Entrada no Caixa da Empresa (O cheque compensou)
                Transacao.objects.create(
                    tipo='PAGAMENTO_ENTRADA',
                    valor=item.valor, # Valor Bruto do cheque entra no caixa
                    descricao=f"Compensação Item {item.numero} - {contrato.contrato_id}",
                    data=timezone.now()
                )

            messages.success(request, f'Item {item.numero} liquidado e valor creditado no caixa.')
        except Exception as e:
            messages.error(request, f"Erro ao liquidar item: {e}")
    
    return redirect('lista_contratos')

def liquidar_contrato(request, contrato_id):
    """
    Liquida todos os itens abertos de um contrato de uma vez.
    """
    contrato = get_object_or_404(ContratoRecebivel, id=contrato_id)
    
    if request.method == 'POST':
        if contrato.status == 'liquidado':
            messages.warning(request, 'Contrato já está totalmente liquidado.')
            return redirect('lista_contratos')
            
        itens_abertos = contrato.itens.filter(status='aberto')
        if not itens_abertos.exists():
            messages.info(request, 'Não há itens em aberto para liquidar.')
            return redirect('lista_contratos')

        try:
            with transaction.atomic():
                total_liquidado = Decimal('0.00')
                
                for item in itens_abertos:
                    item.status = 'pago'
                    item.data_pagamento = timezone.now()
                    item.save()
                    total_liquidado += item.valor
                
                contrato.status = 'liquidado'
                contrato.save()

                # Registra Entrada Única no Caixa
                Transacao.objects.create(
                    tipo='PAGAMENTO_ENTRADA',
                    valor=total_liquidado,
                    descricao=f"Liquidação Total Contrato {contrato.contrato_id}",
                    data=timezone.now()
                )

            messages.success(request, f'Contrato {contrato.contrato_id} liquidado completamente. R$ {total_liquidado:,.2f} entraram no caixa.')
        except Exception as e:
            messages.error(request, f"Erro ao liquidar contrato: {e}")

    return redirect('lista_contratos')