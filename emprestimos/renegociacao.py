from decimal import Decimal
from django.contrib import messages
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

# NOVO IMPORT: Para registrar no livro caixa
from financeiro.models import Transacao

from .models import Emprestimo, Parcela, EmprestimoStatus, ParcelaStatus
from .services import simular
from .utils import gerar_codigo_contrato


@transaction.atomic
def renegociar(request, emprestimo_id: int):
    contrato = get_object_or_404(Emprestimo.objects.select_related("cliente"), id=emprestimo_id)

    if request.method != "POST":
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    if contrato.status not in (EmprestimoStatus.ATIVO, EmprestimoStatus.ATRASADO):
        messages.error(request, "Este contrato não pode ser renegociado no status atual.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    entrada = Decimal((request.POST.get("entrada") or "0").replace(",", "."))
    usar_taxa_antiga = request.POST.get("usar_taxa_antiga") == "1"
    nova_taxa = Decimal((request.POST.get("nova_taxa") or "0").replace(",", "."))
    qtd_parcelas = int(request.POST.get("qtd_parcelas") or "0")
    novo_vencimento_str = request.POST.get("novo_vencimento")

    if qtd_parcelas < 1:
        messages.error(request, "Quantidade de parcelas inválida.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    if not novo_vencimento_str:
        messages.error(request, "Informe o novo vencimento.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    try:
        novo_vencimento = timezone.datetime.fromisoformat(novo_vencimento_str).date()
    except Exception:
        messages.error(request, "Data de vencimento inválida.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    taxa = contrato.taxa_juros_mensal if usar_taxa_antiga else nova_taxa
    if taxa < 0:
        messages.error(request, "Taxa inválida.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    saldo = (
        contrato.parcelas.filter(status=ParcelaStatus.ABERTA)
        .aggregate(total=Sum("valor"))
        .get("total") or Decimal("0.00")
    )

    saldo = (saldo - entrada).quantize(Decimal("0.01"))
    if saldo <= 0:
        messages.error(request, "Saldo inválido após entrada.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    contrato.parcelas.filter(status=ParcelaStatus.ABERTA).update(
        status=ParcelaStatus.LIQUIDADA_RENEGOCIACAO
    )

    contrato.status = EmprestimoStatus.RENEGOCIADO
    contrato.save(update_fields=["status", "atualizado_em"])

    codigo = gerar_codigo_contrato()

    _, parcela_aplicada, total_contrato, ajuste, parcelas = simular(
        valor_emprestado=saldo,
        qtd_parcelas=qtd_parcelas,
        taxa_juros_mensal=taxa,
        primeiro_vencimento=novo_vencimento,
    )

    novo_contrato = Emprestimo.objects.create(
        cliente=contrato.cliente,
        contrato_origem=contrato,
        codigo_contrato=codigo,
        valor_emprestado=saldo,
        qtd_parcelas=qtd_parcelas,
        taxa_juros_mensal=taxa,
        primeiro_vencimento=novo_vencimento,
        valor_parcela_aplicada=parcela_aplicada,
        total_contrato=total_contrato,
        total_juros=(total_contrato - saldo).quantize(Decimal("0.01")),
        ajuste_arredondamento=ajuste,
        status=EmprestimoStatus.ATIVO,
        observacoes=f"Aditivo do contrato {contrato.codigo_contrato}. Entrada: R$ {entrada}",
    )

    Parcela.objects.bulk_create([
        Parcela(
            emprestimo=novo_contrato,
            numero=p.numero,
            vencimento=p.vencimento,
            valor=p.valor,
            status=ParcelaStatus.ABERTA,
        )
        for p in parcelas
    ])

    # NOVA PARTE: Registrar transações no livro caixa
    if entrada > 0:
        Transacao.objects.create(
            tipo='PAGAMENTO_ENTRADA',
            valor=entrada,
            descricao=f"Entrada na renegociação do contrato {contrato.codigo_contrato}",
            emprestimo=contrato  # Link com o contrato antigo
        )

    # Registrar quitação fictícia do saldo restante (positivo, como entrada)
    if saldo > 0:
        Transacao.objects.create(
            tipo='PAGAMENTO_ENTRADA',
            valor=saldo,
            descricao=f"Quitação por renegociação do contrato {contrato.codigo_contrato}",
            emprestimo=contrato  # Link com o contrato antigo
        )

        # Registrar saída para o novo empréstimo (negativo)
        Transacao.objects.create(
            tipo='EMPRESTIMO_SAIDA',
            valor=-saldo,  # Negativo para saída
            descricao=f"Novo empréstimo renegociado: {novo_contrato.codigo_contrato}",
            emprestimo=novo_contrato  # Link com o novo contrato
        )

    messages.success(request, f"Renegociação finalizada. Novo contrato: {codigo}")
    return redirect("emprestimos:contrato_detalhe", emprestimo_id=novo_contrato.id)