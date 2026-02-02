from decimal import Decimal
from django.contrib import messages
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

# Importa os models financeiros reais
from contas.models import ContaCorrente, MovimentacaoConta
from financeiro.models import Transacao

from .models import Emprestimo, Parcela, EmprestimoStatus, ParcelaStatus
from .services import simular
from .utils import gerar_codigo_contrato

@transaction.atomic
def renegociar(request, emprestimo_id):
    contrato = get_object_or_404(Emprestimo.objects.select_related("cliente"), id=emprestimo_id)

    if request.method != "POST":
        return redirect("emprestimos:contrato_detalhe", pk=contrato.id)

    if contrato.status not in (EmprestimoStatus.ATIVO, EmprestimoStatus.ATRASADO):
        messages.error(request, "Este contrato não pode ser renegociado no status atual.")
        return redirect("emprestimos:contrato_detalhe", pk=contrato.id)

    # --- FUNÇÃO AUXILIAR PARA LIMPAR DINHEIRO ---
    def limpar_valor(valor_str):
        if not valor_str:
            return Decimal("0")
        limpo = valor_str.replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
        try:
            return Decimal(limpo)
        except:
            return Decimal("0")

    entrada = limpar_valor(request.POST.get("entrada"))
    
    usar_taxa_antiga = request.POST.get("usar_taxa_antiga") == "1"
    nova_taxa = Decimal((request.POST.get("nova_taxa") or "0").replace(",", "."))
    qtd_parcelas = int(request.POST.get("qtd_parcelas") or "0")
    novo_vencimento_str = request.POST.get("novo_vencimento")

    # --- VALIDAÇÕES ---
    if qtd_parcelas < 1:
        messages.error(request, "Quantidade de parcelas inválida.")
        return redirect("emprestimos:contrato_detalhe", pk=contrato.id)

    if not novo_vencimento_str:
        messages.error(request, "Informe o novo vencimento.")
        return redirect("emprestimos:contrato_detalhe", pk=contrato.id)

    try:
        novo_vencimento = timezone.datetime.fromisoformat(novo_vencimento_str).date()
    except Exception:
        messages.error(request, "Data de vencimento inválida.")
        return redirect("emprestimos:contrato_detalhe", pk=contrato.id)

    taxa = contrato.taxa_juros_mensal if usar_taxa_antiga else nova_taxa
    if taxa < 0:
        messages.error(request, "Taxa inválida.")
        return redirect("emprestimos:contrato_detalhe", pk=contrato.id)

    # --- PASSO 1: CALCULAR DÍVIDA TOTAL (SALDO DEVEDOR) ---
    # Buscamos as parcelas da mais antiga para a mais nova (FIFO)
    parcelas_abertas = contrato.parcelas.filter(status=ParcelaStatus.ABERTA).order_by('vencimento')
    
    total_divida_antiga = parcelas_abertas.aggregate(total=Sum("valor")) .get("total") or Decimal("0.00")

    # Calcula o valor do NOVO empréstimo (Dívida - Entrada)
    # Ex: Dívida 30k - Entrada 10k = Novo Empréstimo de 20k
    valor_novo_emprestimo = (total_divida_antiga - entrada).quantize(Decimal("0.01"))

    if valor_novo_emprestimo <= 0:
        messages.error(request, "O valor de entrada quita toda a dívida. Use a função de liquidar antecipado.")
        return redirect("emprestimos:contrato_detalhe", pk=contrato.id)

    # Prepara a conta corrente do cliente
    conta_cliente, _ = ContaCorrente.objects.get_or_create(cliente=contrato.cliente)

    # --- PASSO 2: LIQUIDAR CONTRATO ANTIGO E DEBITAR CADA PARCELA ---
    # Aqui atendemos o requisito: "discriminando cada parcela"
    
    for p in parcelas_abertas:
        # 2.1. Marca como liquidada por renegociação
        p.status = ParcelaStatus.LIQUIDADA_RENEGOCIACAO
        p.data_pagamento = timezone.now()
        p.valor_pago = p.valor # Considera valor cheio para fins de registro
        p.save()

        # 2.2. DEBITA da conta corrente (Saída de dinheiro referente à dívida antiga)
        # Se a dívida era 30k, vai sair 30k da conta aqui, parcela por parcela.
        MovimentacaoConta.objects.create(
            conta=conta_cliente,
            tipo='DEBITO',
            origem='RENEGOCIACAO',
            valor=p.valor,
            descricao=f"Liq. Renegociação - Parc. {p.numero}/{contrato.qtd_parcelas} - Ctr {contrato.codigo_contrato}",
            data=timezone.now(),
            parcela=p
        )

    # Atualiza status do contrato antigo
    contrato.status = EmprestimoStatus.RENEGOCIADO
    contrato.save(update_fields=["status", "atualizado_em"])

    # --- PASSO 3: GERAR O NOVO CONTRATO ---
    codigo_novo = gerar_codigo_contrato(prefixo="RNG")

    _, parcela_aplicada, total_contrato_novo, ajuste, novas_parcelas_data = simular(
        valor_emprestado=valor_novo_emprestimo,
        qtd_parcelas=qtd_parcelas,
        taxa_juros_mensal=taxa,
        primeiro_vencimento=novo_vencimento,
    )

    novo_contrato = Emprestimo.objects.create(
        cliente=contrato.cliente,
        contrato_origem=contrato,
        codigo_contrato=codigo_novo,
        valor_emprestado=valor_novo_emprestimo,
        qtd_parcelas=qtd_parcelas,
        taxa_juros_mensal=taxa,
        primeiro_vencimento=novo_vencimento,
        valor_parcela_aplicada=parcela_aplicada,
        total_contrato=total_contrato_novo,
        total_juros=(total_contrato_novo - valor_novo_emprestimo).quantize(Decimal("0.01")),
        ajuste_arredondamento=ajuste,
        status=EmprestimoStatus.ATIVO,
        observacoes=f"Renegociação do contrato {contrato.codigo_contrato}. Dívida Orig: {total_divida_antiga} | Entrada: {entrada}"
    )

    # Salva as novas parcelas no banco
    Parcela.objects.bulk_create([
        Parcela(
            emprestimo=novo_contrato,
            numero=p.numero,
            vencimento=p.vencimento,
            valor=p.valor,
            status=ParcelaStatus.ABERTA,
        )
        for p in novas_parcelas_data
    ])

    # --- PASSO 4: CREDITAR O NOVO EMPRÉSTIMO NA CONTA ---
    # Aqui entra o valor refinanciado (Ex: 20k).
    # Matemática Final na Conta: 
    # Saldo Inicial (10k) - Débito Dívida (30k) + Crédito Novo (20k) = 0.00
    
    MovimentacaoConta.objects.create(
        conta=conta_cliente,
        tipo='CREDITO',
        origem='EMPRESTIMO',
        valor=valor_novo_emprestimo,
        descricao=f"Liberação Renegociação {novo_contrato.codigo_contrato}",
        data=timezone.now(),
        emprestimo=novo_contrato
    )

    # (Opcional) Registro no Caixa da Empresa (Financeiro)
    # Se houve entrada real de dinheiro "por fora" ou se consideramos o saldo da conta como dinheiro real,
    # podemos registrar apenas a 'Diferença' se necessário.
    # Mas como estamos mexendo na Conta Corrente do sistema, o fluxo já está registrado lá.
    # Se quiser registrar a ENTRADA da diferença no caixa da empresa:
    if entrada > 0:
         Transacao.objects.create(
            tipo='PAGAMENTO_ENTRADA',
            valor=entrada,
            descricao=f"Absorção de Saldo p/ Renegociação - {contrato.codigo_contrato}",
            emprestimo=contrato
        )

    messages.success(request, f"Renegociação concluída! Novo contrato: {codigo_novo}")
    return redirect("emprestimos:contrato_detalhe", pk=novo_contrato.id)