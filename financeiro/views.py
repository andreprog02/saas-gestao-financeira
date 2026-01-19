from django.shortcuts import render, redirect
from django.contrib import messages
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone
from .models import Transacao, calcular_saldo_atual
from decimal import Decimal

def index(request):
    saldo = calcular_saldo_atual()
    
    # Filtros simples (Mês atual por padrão)
    hoje = timezone.localdate()
    transacoes = Transacao.objects.filter(
        data__year=hoje.year, 
        data__month=hoje.month
    )

    if request.method == "POST":
        # Lógica de Aporte/Sangria
        tipo = request.POST.get('tipo') # 'DEPOSITO' ou 'SAQUE'
        valor = Decimal(request.POST.get('valor').replace(',', '.'))
        descricao = request.POST.get('descricao')

        if tipo == 'SAQUE':
            valor = -valor  # Saque é negativo

        Transacao.objects.create(
            tipo=tipo,
            valor=valor,
            descricao=descricao
        )
        messages.success(request, "Transação realizada com sucesso.")
        return redirect('financeiro:index')

    return render(request, 'financeiro/index.html', {
        'saldo': saldo,
        'transacoes': transacoes,
        'hoje': hoje
    })

def estornar(request, transacao_id):
    if request.method == "POST":
        senha = request.POST.get('senha')
        if senha == settings.MANAGER_PASSWORD:
            original = Transacao.objects.get(id=transacao_id)
            # Cria transação inversa
            Transacao.objects.create(
                tipo='ESTORNO',
                valor= -original.valor, # Inverte o sinal
                descricao=f"Estorno da transação #{original.id}",
                transacao_original=original  # NOVO: Vincula ao ID da original para autenticação
            )
            messages.success(request, "Estorno realizado.")
        else:
            messages.error(request, "Senha de gerente incorreta.")
    return redirect('financeiro:index')