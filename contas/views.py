from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.utils import timezone

from .models import ContaCorrente, MovimentacaoConta
from clientes.models import Cliente
from financeiro.models import Transacao, calcular_saldo_atual
from financeiro.utils import get_client_ip

def operacao_saque(request):
    """
    Tela específica para Operação 05 - Saque em Espécie.
    O usuário seleciona o cliente e o valor.
    Afeta: Saldo do Cliente (Débito) e Caixa da Empresa (Saída).
    """
    # Busca todos os clientes para o Select2/Dropdown
    clientes = Cliente.objects.all().order_by('nome_completo')
    
    if request.method == 'POST':
        cliente_id = request.POST.get('cliente_id')
        valor_str = request.POST.get('valor', '0')
        descricao_usuario = request.POST.get('descricao', '')
        
        # Tratamento do valor (formato BRL 1.000,00 -> Python 1000.00)
        try:
            valor = Decimal(valor_str.replace('.', '').replace(',', '.'))
        except:
            messages.error(request, "Valor inválido.")
            return render(request, 'contas/operacao_saque.html', {'clientes': clientes})
        
        if not cliente_id:
            messages.error(request, "Selecione um cliente.")
            return render(request, 'contas/operacao_saque.html', {'clientes': clientes})

        cliente = get_object_or_404(Cliente, id=cliente_id)
        
        # Garante que a conta existe
        conta, created = ContaCorrente.objects.get_or_create(cliente=cliente)

        # 1. Validação: Valor Positivo
        if valor <= 0:
            messages.error(request, "O valor do saque deve ser positivo.")
            return render(request, 'contas/operacao_saque.html', {'clientes': clientes})

        # 2. Validação: Saldo do Cliente
        if conta.saldo < valor:
            messages.error(request, f"Saldo insuficiente na conta do cliente {cliente.nome_completo}. Saldo atual: R$ {conta.saldo:,.2f}")
            return render(request, 'contas/operacao_saque.html', {'clientes': clientes})
            
        # 3. Validação: Saldo do Caixa da Empresa (Tem dinheiro físico?)
        saldo_caixa = calcular_saldo_atual()
        if saldo_caixa < valor:
             messages.error(request, f"Caixa da empresa insuficiente para realizar este saque. Disponível em caixa: R$ {saldo_caixa:,.2f}")
             return render(request, 'contas/operacao_saque.html', {'clientes': clientes})

        # Execução Atômica (Segurança Financeira)
        try:
            with transaction.atomic():
                descricao_final = f"Saque (Cód 05) - {descricao_usuario}" if descricao_usuario else "Saque em Espécie (Cód 05)"

                # A. Debita da Conta do Cliente
                MovimentacaoConta.objects.create(
                    conta=conta,
                    tipo='DEBITO',
                    origem='SAQUE',
                    valor=valor,
                    descricao=descricao_final
                )
                
                # B. Debita do Caixa da Empresa (Livro Caixa)
                Transacao.objects.create(
                    tipo='EMPRESTIMO_SAIDA', # Usamos um tipo que represente saída, ou crie 'SAQUE' no model Transacao
                    valor=-valor, # Valor negativo para representar saída no fluxo
                    descricao=f"Saque C/C: {cliente.nome_completo}",
                    ip_origem=get_client_ip(request),
                    usuario=request.user if request.user.is_authenticated else None,
                    data=timezone.now()
                )
                
            messages.success(request, f"Saque de R$ {valor:,.2f} realizado com sucesso para {cliente.nome_completo}!")
            return redirect('clientes:detalhe', cliente_id=cliente.id)
            
        except Exception as e:
            messages.error(request, f"Erro ao processar transação: {str(e)}")
            return render(request, 'contas/operacao_saque.html', {'clientes': clientes})

    return render(request, 'contas/operacao_saque.html', {'clientes': clientes})

def realizar_saque(request, cliente_id):
    """
    View de atalho chamada diretamente do perfil do cliente (botão 'Realizar Saque').
    Funciona igual à operacao_saque, mas já sabe quem é o cliente.
    """
    cliente = get_object_or_404(Cliente, id=cliente_id)
    
    if request.method == 'POST':
        valor_str = request.POST.get('valor', '0')
        try:
            valor = Decimal(valor_str.replace('.', '').replace(',', '.'))
        except:
            messages.error(request, "Valor inválido.")
            return redirect('clientes:detalhe', cliente_id=cliente.id)
            
        conta, _ = ContaCorrente.objects.get_or_create(cliente=cliente)
        
        # Validacoes
        if valor <= 0:
            messages.error(request, "Valor deve ser positivo.")
            return redirect('clientes:detalhe', cliente_id=cliente.id)

        if conta.saldo < valor:
            messages.error(request, f"Saldo insuficiente. Disponível: {conta.saldo}")
            return redirect('clientes:detalhe', cliente_id=cliente.id)
            
        saldo_caixa = calcular_saldo_atual()
        if saldo_caixa < valor:
            messages.error(request, f"Caixa da empresa insuficiente. Disponível: {saldo_caixa}")
            return redirect('clientes:detalhe', cliente_id=cliente.id)
            
        with transaction.atomic():
            # Debita Cliente
            MovimentacaoConta.objects.create(
                conta=conta, 
                tipo='DEBITO', 
                origem='SAQUE', 
                valor=valor, 
                descricao="Saque Rápido via Perfil"
            )
            
            # Debita Caixa
            Transacao.objects.create(
                tipo='EMPRESTIMO_SAIDA', 
                valor=-valor, 
                descricao=f"Saque Rápido - {cliente.nome_completo}",
                ip_origem=get_client_ip(request),
                usuario=request.user if request.user.is_authenticated else None
            )
        
        messages.success(request, "Saque realizado com sucesso!")
        
    return redirect('clientes:detalhe', cliente_id=cliente.id)