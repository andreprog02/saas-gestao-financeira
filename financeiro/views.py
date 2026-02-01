from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import transaction
from django.utils import timezone
from django.conf import settings # Importante para a senha de gerente

# Imports do App Financeiro
from .models import Transacao, CodigoOperacao, calcular_saldo_atual
from .utils import get_client_ip

# Imports de Outros Apps
from clientes.models import Cliente
from contas.models import ContaCorrente, MovimentacaoConta

def index(request):
    """
    Dashboard Principal (Fluxo de Caixa).
    Processa lançamentos rápidos via Códigos de Operação (01, 05, etc).
    """
    
    # 1. Inicialização dos Códigos Padrão
    if not CodigoOperacao.objects.exists():
        CodigoOperacao.objects.create(codigo="01", descricao="Despesas Gerais (Água, Luz, Café)", tipo="S", exige_cliente=False)
        CodigoOperacao.objects.create(codigo="02", descricao="Aporte / Depósito de Capital", tipo="E", exige_cliente=False)
        CodigoOperacao.objects.create(codigo="05", descricao="Saque Conta Corrente (Cliente)", tipo="S", exige_cliente=True)

    # 2. Processamento do Formulário de Lançamento
    if request.method == 'POST':
        codigo_input = request.POST.get('codigo')
        valor_str = request.POST.get('valor', '0')
        descricao_form = request.POST.get('descricao')
        cliente_id = request.POST.get('cliente_id')

        try:
            if not valor_str:
                raise ValueError("O valor é obrigatório.")
            
            valor = Decimal(valor_str.replace('.', '').replace(',', '.'))
            
            if valor <= 0:
                raise ValueError("O valor deve ser maior que zero.")

            cod_op = CodigoOperacao.objects.filter(codigo=codigo_input).first()
            if not cod_op:
                messages.error(request, "Código de operação não encontrado.")
                return redirect('financeiro:index')

            with transaction.atomic():
                # === CENÁRIO A: SAQUE CONTA CORRENTE (CÓDIGO 05) ===
                if cod_op.codigo == '05':
                    if not cliente_id:
                        messages.error(request, "Para a operação 05, é obrigatório selecionar um cliente.")
                        return redirect('financeiro:index')
                    
                    cliente = get_object_or_404(Cliente, id=cliente_id)
                    conta, _ = ContaCorrente.objects.get_or_create(cliente=cliente)

                    if conta.saldo < valor:
                        messages.error(request, f"Saldo insuficiente na conta de {cliente.nome_completo}. Disponível: R$ {conta.saldo:,.2f}")
                        return redirect('financeiro:index')
                    
                    saldo_caixa = calcular_saldo_atual()
                    if saldo_caixa < valor:
                        messages.error(request, f"Caixa da empresa insuficiente para este saque. Disponível: R$ {saldo_caixa:,.2f}")
                        return redirect('financeiro:index')

                    # A.1: Debita da Conta do Cliente
                    MovimentacaoConta.objects.create(
                        conta=conta,
                        tipo='DEBITO',
                        origem='SAQUE',
                        valor=valor,
                        descricao=descricao_form or "Saque em Espécie (Cód 05)"
                    )

                    # A.2: Sai do Caixa da Empresa
                    Transacao.objects.create(
                        tipo='SAQUE_CC',
                        valor=-valor,
                        descricao=f"Saque C/C: {cliente.nome_completo} - {descricao_form}",
                        codigo_operacao=cod_op,
                        ip_origem=get_client_ip(request),
                        usuario=request.user if request.user.is_authenticated else None
                    )
                    messages.success(request, f"Saque de R$ {valor:,.2f} realizado para {cliente.nome_completo}.")

                # === CENÁRIO B: LANÇAMENTO COMUM ===
                else:
                    if cod_op.tipo == 'S':
                        valor_final = -valor
                        tipo_transacao = 'DESPESA'
                        saldo_caixa = calcular_saldo_atual()
                        if saldo_caixa < valor:
                             messages.warning(request, f"Atenção: O caixa ficou negativo após esta despesa.")
                    else:
                        valor_final = valor
                        tipo_transacao = 'APORTE'
                    
                    Transacao.objects.create(
                        tipo=tipo_transacao,
                        valor=valor_final,
                        descricao=f"{cod_op.descricao} - {descricao_form}",
                        codigo_operacao=cod_op,
                        ip_origem=get_client_ip(request),
                        usuario=request.user if request.user.is_authenticated else None
                    )
                    messages.success(request, f"Lançamento '{cod_op.descricao}' registrado com sucesso.")

        except ValueError as ve:
            messages.error(request, str(ve))
        except Exception as e:
            messages.error(request, f"Erro inesperado: {str(e)}")
        
        return redirect('financeiro:index')

    # 3. Preparação dos Dados (GET)
    transacoes = Transacao.objects.select_related('codigo_operacao').all().order_by('-data')[:20]
    saldo_atual = calcular_saldo_atual()
    clientes = Cliente.objects.all().order_by('nome_completo')
    codigos_json = list(CodigoOperacao.objects.values('codigo', 'descricao', 'tipo', 'exige_cliente'))

    return render(request, 'financeiro/index.html', {
        'transacoes': transacoes,
        'saldo_atual': saldo_atual,
        'clientes': clientes,
        'codigos_json': codigos_json
    })

def estornar(request, transacao_id):
    """
    Função para reverter uma transação do caixa.
    Se for um SAQUE_CC (Código 05), também deve devolver o dinheiro para a conta do cliente.
    """
    if request.method == "POST":
        senha = request.POST.get('senha')
        # Verifica a senha definida no settings.py (ex: "1234")
        if senha == getattr(settings, 'MANAGER_PASSWORD', '1234'):
            original = get_object_or_404(Transacao, id=transacao_id)
            
            # Evita estornar algo que já foi estornado (opcional, mas recomendado)
            if Transacao.objects.filter(transacao_original=original).exists():
                messages.error(request, "Esta transação já foi estornada.")
                return redirect('financeiro:index')

            try:
                with transaction.atomic():
                    # 1. Cria a transação inversa no Caixa
                    novo_valor = -original.valor # Inverte o sinal (se era -50, vira +50)
                    
                    estorno = Transacao.objects.create(
                        tipo='OUTROS', # Ou criar um tipo 'ESTORNO'
                        valor=novo_valor,
                        descricao=f"ESTORNO: {original.descricao}",
                        transacao_original=original,
                        usuario=request.user if request.user.is_authenticated else None,
                        ip_origem=get_client_ip(request)
                    )

                    # 2. SE FOR SAQUE DE CLIENTE (Cód 05), DEVOLVE O DINHEIRO PRA CONTA DELE
                    if original.tipo == 'SAQUE_CC':
                        # Tenta achar o cliente pela descrição ou lógica salva
                        # O ideal seria ter o cliente vinculado na Transacao, mas vamos tentar extrair
                        # Como o modelo Transacao atual não tem vínculo direto com Cliente (apenas Emprestimo),
                        # o estorno automático na conta do cliente é complexo sem alterar o model.
                        # Por segurança, apenas estornamos o caixa aqui.
                        messages.warning(request, "Atenção: O valor voltou para o Caixa, mas o saldo da Conta Corrente do cliente deve ser ajustado manualmente (Faça um Depósito/Crédito).")

                    messages.success(request, "Estorno realizado com sucesso.")
            except Exception as e:
                messages.error(request, f"Erro ao estornar: {e}")
        else:
            messages.error(request, "Senha de gerente incorreta.")
            
    return redirect('financeiro:index')