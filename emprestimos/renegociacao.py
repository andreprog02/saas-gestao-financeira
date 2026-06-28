"""
Renegociação via esteira de crédito.
Em vez de criar o contrato direto, cria uma PropostaEmprestimo
que passa pelo pipeline completo de aprovação.
"""
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import (
    Emprestimo, Parcela, EmprestimoStatus, ParcelaStatus,
    PropostaEmprestimo, EtapaProposta,
)
from .views_esteira import _criar_checklist_para_etapa


@login_required
def renegociar(request, emprestimo_id):
    """Inicia renegociação — cria proposta que vai pra esteira."""
    contrato = get_object_or_404(Emprestimo.objects.select_related("cliente"), id=emprestimo_id)

    if contrato.status not in (EmprestimoStatus.ATIVO, EmprestimoStatus.ATRASADO):
        messages.error(request, "Este contrato não pode ser renegociado no status atual.")
        return redirect("emprestimos:contrato_detalhe", pk=contrato.id)

    # Calcula dívida total atualizada
    parcelas_abertas = contrato.parcelas.filter(status=ParcelaStatus.ABERTA)
    total_divida = sum(p.valor_atual for p in parcelas_abertas) if parcelas_abertas else Decimal("0")

    if request.method == "POST":
        def limpar_valor(v):
            if not v:
                return Decimal("0")
            limpo = v.replace("R$", "").replace(" ", "").strip()
            if "," in limpo and "." in limpo:
                limpo = limpo.replace(".", "").replace(",", ".")
            elif "," in limpo:
                limpo = limpo.replace(",", ".")
            return Decimal(limpo)

        entrada = limpar_valor(request.POST.get("entrada", "0"))
        nova_taxa = Decimal((request.POST.get("nova_taxa") or "0").replace(",", "."))
        qtd_parcelas = int(request.POST.get("qtd_parcelas") or "1")
        novo_vencimento = request.POST.get("novo_vencimento", "")

        if not novo_vencimento:
            messages.error(request, "Informe o 1º vencimento.")
            return redirect("emprestimos:contrato_detalhe", pk=contrato.id)

        valor_novo = (total_divida - entrada).quantize(Decimal("0.01"))
        if valor_novo <= 0:
            messages.error(request, "O valor de entrada quita toda a dívida.")
            return redirect("emprestimos:contrato_detalhe", pk=contrato.id)

        try:
            vencimento_date = timezone.datetime.fromisoformat(novo_vencimento).date()
        except Exception:
            messages.error(request, "Data de vencimento inválida.")
            return redirect("emprestimos:contrato_detalhe", pk=contrato.id)

        with transaction.atomic():
            # Cria proposta de renegociação
            proposta = PropostaEmprestimo.objects.create(
                cliente=contrato.cliente,
                valor_solicitado=valor_novo,
                qtd_parcelas=qtd_parcelas,
                taxa_juros=nova_taxa,
                primeiro_vencimento=vencimento_date,
                finalidade="RENEGOCIACAO",
                contrato_renegociado=contrato,
                valor_divida_original=total_divida,
                valor_entrada_renegociacao=entrada,
                usuario_solicitante=request.user,
                observacoes=f"Renegociação do contrato {contrato.codigo_contrato}. "
                            f"Dívida: R$ {total_divida:.2f} | Entrada: R$ {entrada:.2f} | "
                            f"Novo valor: R$ {valor_novo:.2f}",
                status="CAPTACAO",
            )

            # Cria primeira etapa
            etapa = EtapaProposta.objects.create(proposta=proposta, etapa="CAPTACAO")
            _criar_checklist_para_etapa(etapa)

            messages.success(request,
                f"Proposta de renegociação criada (#{proposta.id}). "
                f"Valor: R$ {valor_novo:.2f} em {qtd_parcelas}x. "
                f"Encaminhe pela esteira de aprovação."
            )
            return redirect("emprestimos:esteira_detalhe", proposta_id=proposta.id)

    return render(request, "emprestimos/renegociar.html", {
        "contrato": contrato,
        "total_divida": total_divida,
        "parcelas_abertas": parcelas_abertas,
    })
