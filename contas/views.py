from decimal import Decimal
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.db import transaction
from .models import ContaCorrente, MovimentacaoConta
from financeiro.models import Transacao, calcular_saldo_atual
from financeiro.utils import get_client_ip

def realizar_saque(request, cliente_id):
    conta = get_object_or_404(ContaCorrente, cliente_id=cliente_id)
    
    if request.method == 'POST':
        try:
            valor = Decimal(request.POST.get('valor', '0').replace(',', '.'))
            
            if valor <= 0:
                messages.error(request, 'O valor deve ser positivo.')
                return redirect('clientes:detalhe', cliente_id=cliente_id)

            if valor > conta.saldo:
                messages.error(request, 'Saldo insuficiente na conta do cliente.')
            else:
                # Verifica saldo do CAIXA DA EMPRESA antes de liberar o dinheiro físico
                saldo_caixa = calcular_saldo_atual()
                if saldo_caixa < valor:
                    messages.error(request, f'Caixa da empresa insuficiente para realizar este saque. Disponível: {saldo_caixa}')
                    return redirect('clientes:detalhe', cliente_id=cliente_id)

                with transaction.atomic():
                    # 1. Debita da conta do cliente
                    MovimentacaoConta.objects.create(
                        conta=conta,
                        tipo='DEBITO',
                        origem='SAQUE',
                        valor=valor,
                        descricao="Saque manual em espécie/transferência"
                    )
                    
                    # 2. Registra a saída no caixa da empresa
                    Transacao.objects.create(
                        tipo='SAQUE', # ou outro tipo de saída
                        valor=-valor,
                        descricao=f"Saque Conta Corrente - {conta.cliente.nome_completo}",
                        ip_origem=get_client_ip(request),
                        usuario=request.user if request.user.is_authenticated else None
                    )

                messages.success(request, 'Saque realizado com sucesso.')
                
        except Exception as e:
            messages.error(request, f'Erro ao processar: {e}')
            
    return redirect('clientes:detalhe', cliente_id=cliente_id)