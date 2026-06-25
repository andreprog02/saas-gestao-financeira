import json  # <--- IMPORTANTE
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.utils import timezone
from django.conf import settings 
from django.core.serializers.json import DjangoJSONEncoder # Para converter datas/decimais se precisar

# Imports do App Financeiro
from .models import Transacao, CodigoOperacao, calcular_saldo_atual
from .utils import get_client_ip

# Imports de Outros Apps
from clientes.models import Cliente
from contas.models import ContaCorrente, MovimentacaoConta

# === FUNÇÃO AUXILIAR PARA VALORES ===
def parse_valor_monetario(valor_str):
    if not valor_str:
        return Decimal("0.00")
    # Remove R$, espaços e converte formato BR para Python
    clean = str(valor_str).replace('R$', '').replace(' ', '').replace('.', '').replace(',', '.')
    try:
        return Decimal(clean)
    except:
        raise ValueError("Valor inválido.")

def index(request):
    """
    Dashboard Principal (Fluxo de Caixa).
    Atualizado com a operação 06 - Depósito Conta Corrente.
    """
    
    # 1. Inicialização GARANTIDA dos Códigos Padrão
    # Usamos get_or_create para evitar duplicidade e garantir existência
    if not CodigoOperacao.objects.filter(codigo="01").exists():
        CodigoOperacao.objects.create(codigo="01", descricao="Despesas Gerais", tipo="S", exige_cliente=False)
    
    if not CodigoOperacao.objects.filter(codigo="02").exists():
        CodigoOperacao.objects.create(codigo="02", descricao="Aporte de Capital", tipo="E", exige_cliente=False)
        
    if not CodigoOperacao.objects.filter(codigo="05").exists():
        CodigoOperacao.objects.create(codigo="05", descricao="Saque Conta Corrente (Cliente)", tipo="S", exige_cliente=True)

    # NOVO: Inicializa o Código 06 para Depósito
    if not CodigoOperacao.objects.filter(codigo="06").exists():
        CodigoOperacao.objects.create(codigo="06", descricao="Depósito Conta Corrente (Cliente)", tipo="E", exige_cliente=True)

    # 2. Processamento do Formulário (POST)
    if request.method == 'POST':
        # Captura os dados do formulário
        codigo_input = request.POST.get('codigo')
        valor_str = request.POST.get('valor', '0')
        descricao_form = request.POST.get('descricao')
        cliente_id = request.POST.get('cliente_id')

        try:
            # Converte e valida o valor
            valor = parse_valor_monetario(valor_str)
            
            if valor <= 0:
                raise ValueError("O valor deve ser maior que zero.")

            # Busca o código de operação no banco
            cod_op = CodigoOperacao.objects.filter(codigo=codigo_input).first()
            if not cod_op:
                messages.error(request, f"Código de operação '{codigo_input}' não encontrado.")
                return redirect('financeiro:index')

            # Inicia a transação atômica (tudo ou nada)
            with transaction.atomic():
                
                # === CENÁRIO A: SAQUE CONTA CORRENTE (CÓDIGO 05) ===
                if cod_op.codigo == '05':
                    if not cliente_id:
                        messages.error(request, "Selecione um cliente para realizar o saque.")
                        return redirect('financeiro:index')
                    
                    cliente = get_object_or_404(Cliente, id=cliente_id)
                    conta, _ = ContaCorrente.objects.get_or_create(cliente=cliente)

                    # Valida Saldo do Cliente
                    if conta.saldo < valor:
                        messages.error(request, f"Saldo insuficiente na conta de {cliente.nome_completo}. Disponível: R$ {conta.saldo:,.2f}")
                        return redirect('financeiro:index')
                    
                    # Valida Caixa da Empresa (Apenas aviso visual, não impede saque se tiver dinheiro físico)
                    saldo_caixa = calcular_saldo_atual()
                    if saldo_caixa < valor:
                        messages.warning(request, "Atenção: Caixa da empresa ficou negativo. Necessário aporte.")

                    # A.1: Debita Cliente (Sai da conta virtual dele)
                    MovimentacaoConta.objects.create(
                        conta=conta,
                        tipo='DEBITO',
                        origem='SAQUE',
                        valor=valor,
                        descricao=descricao_form or "Saque em Espécie (Cód 05)"
                    )

                    # A.2: Sai do Caixa da Empresa (Sai dinheiro físico)
                    Transacao.objects.create(
                        tipo='SAQUE_CC',
                        valor=-valor, # Negativo = Saída
                        descricao=f"Saque C/C: {cliente.nome_completo} - {descricao_form}",
                        codigo_operacao=cod_op,
                        ip_origem=get_client_ip(request),
                        usuario=request.user if request.user.is_authenticated else None
                    )
                    messages.success(request, f"Saque de R$ {valor:,.2f} realizado para {cliente.nome_completo}.")

                # === CENÁRIO B: DEPÓSITO CONTA CORRENTE (CÓDIGO 06) ===
                elif cod_op.codigo == '06':
                    if not cliente_id:
                        messages.error(request, "Selecione um cliente para realizar o depósito.")
                        return redirect('financeiro:index')
                    
                    cliente = get_object_or_404(Cliente, id=cliente_id)
                    conta, _ = ContaCorrente.objects.get_or_create(cliente=cliente)

                    # B.1: Credita na Conta do Cliente (Entra na conta virtual dele)
                    MovimentacaoConta.objects.create(
                        conta=conta,
                        tipo='CREDITO',
                        origem='DEPOSITO',
                        valor=valor,
                        descricao=descricao_form or "Depósito em Espécie (Cód 06)"
                    )

                    # B.2: Entrada no Caixa da Empresa (Entra dinheiro físico)
                    Transacao.objects.create(
                        tipo='DEPOSITO_CC', # Certifique-se que este tipo existe no models.py
                        valor=valor, # Positivo = Entrada
                        descricao=f"Depósito C/C: {cliente.nome_completo} - {descricao_form}",
                        codigo_operacao=cod_op,
                        ip_origem=get_client_ip(request),
                        usuario=request.user if request.user.is_authenticated else None
                    )
                    messages.success(request, f"Depósito de R$ {valor:,.2f} realizado na conta de {cliente.nome_completo}.")

                # === CENÁRIO C: OUTROS LANÇAMENTOS (01, 02, ETC) ===
                else:
                    if cod_op.tipo == 'S':
                        valor_final = -valor
                        tipo_transacao = 'DESPESA'
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
    
    # Prepara JSON para o JavaScript do frontend
    dados_codigos = list(CodigoOperacao.objects.values('codigo', 'descricao', 'tipo', 'exige_cliente'))
    codigos_json = json.dumps(dados_codigos, cls=DjangoJSONEncoder)

    return render(request, 'financeiro/index.html', {
        'transacoes': transacoes,
        'saldo_atual': saldo_atual,
        'clientes': clientes,
        'codigos_json': codigos_json
    })

def estornar(request, transacao_id):
    # (Mantém a mesma lógica de estorno que você já tem ou a do exemplo anterior)
    if request.method == "POST":
        senha = request.POST.get('senha')
        from django.contrib.auth import authenticate
        user = authenticate(username=request.user.username, password=senha)
        if user:
            original = get_object_or_404(Transacao, id=transacao_id)
            if Transacao.objects.filter(transacao_original=original).exists():
                messages.error(request, "Já estornado.")
                return redirect('financeiro:index')
            
            with transaction.atomic():
                novo_valor = -original.valor
                Transacao.objects.create(
                    tipo='OUTROS',
                    valor=novo_valor,
                    descricao=f"ESTORNO: {original.descricao}",
                    transacao_original=original,
                    usuario=request.user,
                    ip_origem=get_client_ip(request)
                )
                messages.success(request, "Estornado com sucesso.")
        else:
            messages.error(request, "Senha inválida.")
    return redirect('financeiro:index')

# ==============================================================================
# CONTROLE DE CAIXA
# ==============================================================================

@login_required
def caixa_painel(request):
    """Painel do caixa com status atual e histórico."""
    from .models import Caixa
    hoje = timezone.localdate()
    caixa_hoje = Caixa.objects.filter(data=hoje).first()
    historico = Caixa.objects.exclude(data=hoje).order_by("-data")[:30]
    saldo_atual = calcular_saldo_atual()

    return render(request, "financeiro/caixa_painel.html", {
        "caixa_hoje": caixa_hoje,
        "historico": historico,
        "saldo_atual": saldo_atual,
        "hoje": hoje,
    })


@login_required
def caixa_abrir(request):
    """Abre o caixa do dia."""
    from .models import Caixa
    hoje = timezone.localdate()

    if Caixa.objects.filter(data=hoje).exists():
        messages.warning(request, "O caixa de hoje já foi aberto.")
        return redirect("financeiro:caixa_painel")

    if request.method == "POST":
        saldo = parse_valor_monetario(request.POST.get("saldo_abertura", "0"))
        Caixa.objects.create(
            data=hoje,
            saldo_abertura=saldo,
            aberto_por=request.user,
            aberto_em=timezone.now(),
        )
        messages.success(request, f"Caixa aberto com saldo de R$ {saldo:.2f}")
        return redirect("financeiro:caixa_painel")

    # Sugere saldo do último fechamento
    ultimo = Caixa.objects.filter(status="FECHADO").order_by("-data").first()
    saldo_sugerido = ultimo.saldo_conferido if ultimo else Decimal("0.00")

    return render(request, "financeiro/caixa_abrir.html", {
        "hoje": hoje,
        "saldo_sugerido": saldo_sugerido,
    })


@login_required
def caixa_fechar(request):
    """Fechamento do caixa com conferência de cédulas e moedas."""
    from .models import Caixa
    hoje = timezone.localdate()
    caixa = Caixa.objects.filter(data=hoje, status="ABERTO").first()

    if not caixa:
        messages.error(request, "Nenhum caixa aberto hoje.")
        return redirect("financeiro:caixa_painel")

    saldo_sistema = calcular_saldo_atual()

    if request.method == "POST":
        # Contagem de cédulas
        cedulas = {}
        total_ced = Decimal("0.00")
        for val in ["200", "100", "50", "20", "10", "5", "2"]:
            qtd = int(request.POST.get(f"ced_{val}", 0) or 0)
            cedulas[val] = qtd
            total_ced += Decimal(val) * qtd

        # Contagem de moedas
        moedas = {}
        total_moe = Decimal("0.00")
        for val, key in [("1.00", "100"), ("0.50", "050"), ("0.25", "025"), ("0.10", "010"), ("0.05", "005")]:
            qtd = int(request.POST.get(f"moe_{key}", 0) or 0)
            moedas[key] = qtd
            total_moe += Decimal(val) * qtd

        saldo_conferido = total_ced + total_moe
        diferenca = saldo_conferido - saldo_sistema
        obs = request.POST.get("observacoes", "")

        caixa.saldo_sistema = saldo_sistema
        caixa.saldo_conferido = saldo_conferido
        caixa.diferenca = diferenca
        caixa.contagem_cedulas = cedulas
        caixa.contagem_moedas = moedas
        caixa.observacoes_fechamento = obs
        caixa.fechado_por = request.user
        caixa.fechado_em = timezone.now()
        caixa.status = "FECHADO"
        caixa.save()

        if abs(diferenca) < Decimal("0.01"):
            messages.success(request, "Caixa fechado — conferência OK, sem diferença.")
        elif diferenca > 0:
            messages.warning(request, f"Caixa fechado — SOBRA de R$ {diferenca:.2f}")
        else:
            messages.error(request, f"Caixa fechado — FALTA de R$ {abs(diferenca):.2f}")

        return redirect("financeiro:caixa_painel")

    return render(request, "financeiro/caixa_fechar.html", {
        "caixa": caixa,
        "saldo_sistema": saldo_sistema,
        "hoje": hoje,
    })


@login_required
def caixa_detalhe(request, caixa_id):
    """Detalhe de um caixa fechado."""
    from .models import Caixa
    caixa = get_object_or_404(Caixa, id=caixa_id)

    cedulas_display = []
    total_ced = Decimal("0.00")
    for val in ["200", "100", "50", "20", "10", "5", "2"]:
        qtd = caixa.contagem_cedulas.get(val, 0)
        subtotal = Decimal(val) * qtd
        total_ced += subtotal
        cedulas_display.append({"valor": val, "qtd": qtd, "subtotal": subtotal})

    moedas_display = []
    total_moe = Decimal("0.00")
    for val, key in [("1.00", "100"), ("0.50", "050"), ("0.25", "025"), ("0.10", "010"), ("0.05", "005")]:
        qtd = caixa.contagem_moedas.get(key, 0)
        subtotal = Decimal(val) * qtd
        total_moe += subtotal
        moedas_display.append({"valor": val, "qtd": qtd, "subtotal": subtotal})

    return render(request, "financeiro/caixa_detalhe.html", {
        "caixa": caixa,
        "cedulas": cedulas_display,
        "moedas": moedas_display,
        "total_cedulas": total_ced,
        "total_moedas": total_moe,
    })
