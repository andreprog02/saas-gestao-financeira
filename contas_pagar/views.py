"""
Views do módulo Contas a Pagar.

Fluxo: Cadastro → Aprovação (com senha) → Pagamento (com comprovante) → Paga
"""
from decimal import Decimal, InvalidOperation
from datetime import date, timedelta

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, Q

from .models import ContaPagar


def _parse_valor_brl(valor_str):
    """Converte valor no formato BRL (1.234,56) para Decimal."""
    if not valor_str:
        return Decimal("0.00")
    limpo = valor_str.replace("R$", "").replace(" ", "").strip()
    if "," in limpo and "." in limpo:
        limpo = limpo.replace(".", "").replace(",", ".")
    elif "," in limpo:
        limpo = limpo.replace(",", ".")
    return Decimal(limpo).quantize(Decimal("0.01"))


# ==============================================================================
# 1. PAINEL PRINCIPAL (Kanban-style)
# ==============================================================================

@login_required
def painel(request):
    """Visão geral das contas organizadas por status."""
    hoje = timezone.localdate()

    # Filtro de período
    periodo = request.GET.get("periodo", "todos")
    data_inicio_str = request.GET.get("data_inicio", "")
    data_fim_str = request.GET.get("data_fim", "")

    filtro_data = Q()
    if periodo == "hoje":
        filtro_data = Q(vencimento=hoje)
    elif periodo == "semana":
        filtro_data = Q(vencimento__gte=hoje, vencimento__lte=hoje + timedelta(days=7))
    elif periodo == "mes":
        filtro_data = Q(vencimento__month=hoje.month, vencimento__year=hoje.year)
    elif periodo == "vencidas":
        filtro_data = Q(vencimento__lt=hoje) & ~Q(status="PAGA")
    elif periodo == "custom" and data_inicio_str and data_fim_str:
        filtro_data = Q(
            vencimento__gte=date.fromisoformat(data_inicio_str),
            vencimento__lte=date.fromisoformat(data_fim_str),
        )

    pendentes = ContaPagar.objects.filter(filtro_data, status="PENDENTE").order_by("vencimento")
    aprovadas = ContaPagar.objects.filter(filtro_data, status="APROVADA").order_by("vencimento")
    pagas = ContaPagar.objects.filter(filtro_data, status="PAGA").order_by("-pago_em")[:20]
    negadas = ContaPagar.objects.filter(filtro_data, status__in=["NEGADA", "DEVOLVIDA"]).order_by("-cadastrado_em")[:10]

    # Totais
    total_pendente = pendentes.aggregate(s=Sum("valor"))["s"] or Decimal("0.00")
    total_aprovado = aprovadas.aggregate(s=Sum("valor"))["s"] or Decimal("0.00")
    total_pago = pagas.aggregate(s=Sum("valor"))["s"] or Decimal("0.00")
    total_vencidas = ContaPagar.objects.filter(
        vencimento__lt=hoje, status__in=["PENDENTE", "APROVADA"]
    ).aggregate(s=Sum("valor"))["s"] or Decimal("0.00")
    qtd_vencidas = ContaPagar.objects.filter(
        vencimento__lt=hoje, status__in=["PENDENTE", "APROVADA"]
    ).count()

    return render(request, "contas_pagar/painel.html", {
        "pendentes": pendentes,
        "aprovadas": aprovadas,
        "pagas": pagas,
        "negadas": negadas,
        "total_pendente": total_pendente,
        "total_aprovado": total_aprovado,
        "total_pago": total_pago,
        "total_vencidas": total_vencidas,
        "qtd_vencidas": qtd_vencidas,
        "periodo": periodo,
        "data_inicio": data_inicio_str,
        "data_fim": data_fim_str,
        "hoje": hoje,
    })


# ==============================================================================
# 2. CADASTRAR CONTA
# ==============================================================================

@login_required
def cadastrar(request):
    """Cadastra nova conta a pagar."""
    if request.method == "POST":
        try:
            descricao = request.POST.get("descricao", "").strip()
            tipo = request.POST.get("tipo_despesa", "OUTROS")
            valor = _parse_valor_brl(request.POST.get("valor", "0"))
            vencimento = request.POST.get("vencimento", "")
            observacoes = request.POST.get("observacoes", "")
            fatura = request.FILES.get("fatura")

            if not descricao:
                raise ValueError("Informe a descrição da conta.")
            if valor <= 0:
                raise ValueError("Valor deve ser maior que zero.")
            if not vencimento:
                raise ValueError("Informe a data de vencimento.")

            conta = ContaPagar.objects.create(
                descricao=descricao,
                tipo_despesa=tipo,
                valor=valor,
                vencimento=vencimento,
                observacoes=observacoes,
                fatura=fatura,
                cadastrado_por=request.user,
                status=ContaPagar.Status.PENDENTE,
            )

            messages.success(request, f"Conta '{descricao}' cadastrada — aguardando aprovação.")
            return redirect("contas_pagar:painel")

        except (ValueError, InvalidOperation) as e:
            messages.error(request, str(e))

    tipos = ContaPagar.TipoDespesa.choices
    return render(request, "contas_pagar/cadastrar.html", {"tipos": tipos})


# ==============================================================================
# 3. DETALHE DA CONTA
# ==============================================================================

@login_required
def detalhe(request, conta_id):
    """Detalhe da conta com ações disponíveis."""
    conta = get_object_or_404(ContaPagar, id=conta_id)
    return render(request, "contas_pagar/detalhe.html", {"conta": conta})


# ==============================================================================
# 4. APROVAR / NEGAR / DEVOLVER
# ==============================================================================

@login_required
def aprovar(request, conta_id):
    """Aprova a conta para pagamento — exige senha."""
    conta = get_object_or_404(ContaPagar, id=conta_id)

    if request.method != "POST":
        return redirect("contas_pagar:detalhe", conta_id=conta.id)

    senha = request.POST.get("senha", "")
    user = authenticate(username=request.user.username, password=senha)
    if not user:
        messages.error(request, "Senha inválida. Aprovação não registrada.")
        return redirect("contas_pagar:detalhe", conta_id=conta.id)

    conta.status = ContaPagar.Status.APROVADA
    conta.aprovado_por = request.user
    conta.aprovado_em = timezone.now()
    conta.save()

    messages.success(request, f"Conta '{conta.descricao}' aprovada para pagamento.")
    return redirect("contas_pagar:painel")


@login_required
def negar(request, conta_id):
    """Nega a conta com justificativa."""
    conta = get_object_or_404(ContaPagar, id=conta_id)

    if request.method != "POST":
        return redirect("contas_pagar:detalhe", conta_id=conta.id)

    justificativa = request.POST.get("justificativa", "")
    if not justificativa.strip():
        messages.error(request, "Informe a justificativa.")
        return redirect("contas_pagar:detalhe", conta_id=conta.id)

    conta.status = ContaPagar.Status.NEGADA
    conta.justificativa = justificativa
    conta.save()

    messages.info(request, f"Conta '{conta.descricao}' negada.")
    return redirect("contas_pagar:painel")


@login_required
def devolver(request, conta_id):
    """Devolve a conta para correção."""
    conta = get_object_or_404(ContaPagar, id=conta_id)

    if request.method != "POST":
        return redirect("contas_pagar:detalhe", conta_id=conta.id)

    justificativa = request.POST.get("justificativa", "")
    conta.status = ContaPagar.Status.DEVOLVIDA
    conta.justificativa = justificativa
    conta.save()

    messages.warning(request, f"Conta '{conta.descricao}' devolvida para correção.")
    return redirect("contas_pagar:painel")


@login_required
def reenviar(request, conta_id):
    """Reenvia uma conta devolvida/negada para aprovação."""
    conta = get_object_or_404(ContaPagar, id=conta_id)

    if request.method == "POST":
        conta.descricao = request.POST.get("descricao", conta.descricao)
        conta.valor = _parse_valor_brl(request.POST.get("valor", str(conta.valor)))
        conta.vencimento = request.POST.get("vencimento", conta.vencimento)
        conta.observacoes = request.POST.get("observacoes", conta.observacoes)

        nova_fatura = request.FILES.get("fatura")
        if nova_fatura:
            conta.fatura = nova_fatura

        conta.status = ContaPagar.Status.PENDENTE
        conta.justificativa = ""
        conta.save()

        messages.success(request, f"Conta '{conta.descricao}' reenviada para aprovação.")
        return redirect("contas_pagar:painel")

    tipos = ContaPagar.TipoDespesa.choices
    return render(request, "contas_pagar/reenviar.html", {"conta": conta, "tipos": tipos})


# ==============================================================================
# 5. CONFIRMAR PAGAMENTO
# ==============================================================================

@login_required
def confirmar_pagamento(request, conta_id):
    """Confirma pagamento e faz upload do comprovante."""
    conta = get_object_or_404(ContaPagar, id=conta_id)

    if request.method != "POST":
        return redirect("contas_pagar:detalhe", conta_id=conta.id)

    comprovante = request.FILES.get("comprovante")

    conta.status = ContaPagar.Status.PAGA
    conta.pago_por = request.user
    conta.pago_em = timezone.now()
    if comprovante:
        conta.comprovante = comprovante
    conta.save()

    messages.success(request, f"Pagamento de '{conta.descricao}' confirmado.")
    return redirect("contas_pagar:painel")
