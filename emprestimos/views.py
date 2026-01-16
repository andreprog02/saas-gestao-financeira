from django.shortcuts import render

from decimal import Decimal
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

#from clientes.models import Cliente
from .forms import SelecionarClienteForm, NovoEmprestimoForm
from .models import Emprestimo, Parcela, ParcelaStatus
from .services import simular
from .utils import gerar_codigo_contrato
from clientes.models import Cliente
from django.views.decorators.http import require_http_methods
from django.conf import settings

from .models import ContratoLog
from .models import Emprestimo, EmprestimoStatus





def novo_emprestimo_busca_cliente(request):
    """
    1ª tela: busca cliente por nome/cpf.
    """
    form = SelecionarClienteForm(request.GET or None)
    clientes = None

    if form.is_valid():
        q = form.cleaned_data["q"].strip()
        clientes = (
            Cliente.objects.filter(nome_completo__icontains=q)
            | Cliente.objects.filter(cpf__icontains=q)
        ).distinct().order_by("nome_completo")[:20]

        if not clientes.exists():
            messages.warning(request, "Nenhum cliente encontrado. Cadastre o cliente primeiro.")

    return render(request, "emprestimos/novo_busca.html", {"form": form, "clientes": clientes})


def novo_emprestimo_form(request, cliente_id: int):
    """
    2ª tela: formulário para simular e cadastrar.
    """
    cliente = get_object_or_404(Cliente, id=cliente_id)

    if request.method == "POST":
        form = NovoEmprestimoForm(request.POST)
        if form.is_valid():
            # Simula sempre (para mostrar preview e também para salvar exatamente)
            parcela_bruta, parcela_aplicada, total_contrato, ajuste, parcelas = simular(
                valor_emprestado=form.cleaned_data["valor_emprestado"],
                qtd_parcelas=form.cleaned_data["qtd_parcelas"],
                taxa_juros_mensal=form.cleaned_data["taxa_juros_mensal"],
                primeiro_vencimento=form.cleaned_data["primeiro_vencimento"],
            )

            # Se clicou em "Cadastrar"
            if "confirmar_cadastro" in request.POST:
                with transaction.atomic():
                    codigo = gerar_codigo_contrato()

                    emprestimo = Emprestimo.objects.create(
                        cliente=cliente,
                        codigo_contrato=codigo,
                        valor_emprestado=form.cleaned_data["valor_emprestado"],
                        qtd_parcelas=form.cleaned_data["qtd_parcelas"],
                        taxa_juros_mensal=form.cleaned_data["taxa_juros_mensal"],
                        primeiro_vencimento=form.cleaned_data["primeiro_vencimento"],
                        valor_parcela_aplicada=parcela_aplicada,
                        total_contrato=total_contrato,
                        total_juros=(total_contrato - form.cleaned_data["valor_emprestado"]).quantize(Decimal("0.01")),
                        ajuste_arredondamento=ajuste,
                        observacoes=form.cleaned_data.get("observacoes", ""),
                    )

                    Parcela.objects.bulk_create([
                        Parcela(
                            emprestimo=emprestimo,
                            numero=p.numero,
                            vencimento=p.vencimento,
                            valor=p.valor,
                            status=ParcelaStatus.ABERTA,
                        )
                        for p in parcelas
                    ])

                messages.success(request, f"Empréstimo cadastrado com sucesso! Contrato: {codigo}")
                return redirect("emprestimos:contrato_detalhe", emprestimo_id=emprestimo.id)

            # Senão, apenas mostrar a simulação na mesma tela
            return render(
                request,
                "emprestimos/novo_form.html",
                {
                    "cliente": cliente,
                    "form": form,
                    "simulacao": {
                        "parcela_bruta": parcela_bruta,
                        "parcela_aplicada": parcela_aplicada,
                        "total_contrato": total_contrato,
                        "ajuste": ajuste,
                        "parcelas": parcelas,
                    },
                },
            )
    else:
        form = NovoEmprestimoForm(initial={"cliente_id": cliente.id})




    emp = Emprestimo.objects.create(
    cliente=cliente,
    codigo_contrato=gerar_codigo_contrato(),
    valor_emprestado=valor,
    qtd_parcelas=qtd,
    taxa_juros_mensal=taxa,
    primeiro_vencimento=venc,

    valor_parcela_aplicada=parcela_aplicada,
    total_contrato=total_contrato,
    total_juros=(total_contrato - valor),
    ajuste_arredondamento=ajuste,

    status=EmprestimoStatus.ATIVO,

    # Regras de atraso
    tem_multa_atraso=tem_multa,
    multa_atraso_percent=multa_percent,
    juros_mora_mensal_percent=juros_mora_percent,
    )

    return render(request, "emprestimos/novo_form.html", {"cliente": cliente, "form": form})


def contratos(request):
    qs = Emprestimo.objects.select_related("cliente").all()
    return render(request, "emprestimos/contratos.html", {"contratos": qs})


def contrato_detalhe(request, emprestimo_id: int):
    emp = get_object_or_404(Emprestimo.objects.select_related("cliente"), id=emprestimo_id)

    # atualiza status baseado em vencidas/abertas
    emp.atualizar_status()
    emp.save(update_fields=["status", "atualizado_em"])

    parcelas = emp.parcelas.all().order_by("numero")
    return render(request, "emprestimos/contrato_detalhe.html", {"contrato": emp, "parcelas": parcelas})


def a_vencer(request):
    hoje = timezone.localdate()
    parcelas = (
        Parcela.objects.select_related("emprestimo", "emprestimo__cliente")
        .filter(status=ParcelaStatus.ABERTA, vencimento__gte=hoje)
        .order_by("vencimento")
    )
    return render(request, "emprestimos/a_vencer.html", {"parcelas": parcelas})


@transaction.atomic
def renegociar(request, emprestimo_id):
    contrato = get_object_or_404(Emprestimo, id=emprestimo_id)

    if request.method != "POST":
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    entrada = Decimal(request.POST.get("entrada") or "0.00")
    usar_taxa_antiga = request.POST.get("usar_taxa_antiga") == "1"
    nova_taxa = Decimal(request.POST.get("nova_taxa") or contrato.taxa_juros_mensal)
    qtd_parcelas = int(request.POST["qtd_parcelas"])
    novo_vencimento = request.POST["novo_vencimento"]

    taxa = contrato.taxa_juros_mensal if usar_taxa_antiga else nova_taxa

    saldo = contrato.parcelas.filter(
        status=ParcelaStatus.ABERTA
    ).aggregate(
        total=models.Sum("valor")
    )["total"] or Decimal("0.00")

    saldo -= entrada
    if saldo <= 0:
        messages.error(request, "Saldo inválido para renegociação.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    # Liquida parcelas antigas
    contrato.parcelas.filter(status=ParcelaStatus.ABERTA).update(
        status=ParcelaStatus.LIQUIDADA_RENEGOCIACAO
    )

    contrato.status = EmprestimoStatus.RENEGOCIADO
    contrato.save()

    # Criar novo contrato (aditivo)
    codigo = gerar_codigo_contrato()

    parcela_bruta, parcela_aplicada, total, ajuste, parcelas = simular(
        saldo, qtd_parcelas, taxa, novo_vencimento
    )

    novo = Emprestimo.objects.create(
        cliente=contrato.cliente,
        contrato_origem=contrato,
        codigo_contrato=codigo,
        valor_emprestado=saldo,
        qtd_parcelas=qtd_parcelas,
        taxa_juros_mensal=taxa,
        primeiro_vencimento=novo_vencimento,
        valor_parcela_aplicada=parcela_aplicada,
        total_contrato=total,
        ajuste_arredondamento=ajuste,
    )

    Parcela.objects.bulk_create([
        Parcela(
            emprestimo=novo,
            numero=p.numero,
            vencimento=p.vencimento,
            valor=p.valor
        )
        for p in parcelas
    ])

    messages.success(request, f"Contrato renegociado. Novo contrato: {codigo}")
    return redirect("emprestimos:contrato_detalhe", emprestimo_id=novo.id)



def vencidos(request):
    hoje = timezone.localdate()
    parcelas = (
        Parcela.objects.select_related("emprestimo", "emprestimo__cliente")
        .filter(status=ParcelaStatus.ABERTA, vencimento__lt=hoje)
        .order_by("vencimento")
    )
    return render(request, "emprestimos/vencidos.html", {"parcelas": parcelas, "hoje": hoje})


@require_http_methods(["GET", "POST"])
def pagar_parcela(request, parcela_id: int):
    p = get_object_or_404(
        Parcela.objects.select_related("emprestimo", "emprestimo__cliente"),
        id=parcela_id
    )
    contrato = p.emprestimo

    # 1) Bloqueios de consistência / segurança
    if contrato.status == EmprestimoStatus.CANCELADO:
        messages.error(request, "Este contrato está CANCELADO. Não é possível registrar pagamento.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    if p.status != ParcelaStatus.ABERTA:
        messages.error(request, f"Esta parcela não está aberta (status atual: {p.status}).")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)
    

    # ✅ REGISTRA O LOG DO PAGAMENTO
    ContratoLog.objects.create(
        contrato=contrato,
        acao=ContratoLog.Acao.PAGO,
        usuario=request.user if request.user.is_authenticated else None,
        motivo=f"Parcela {p.numero} paga",
    )

    # 2) Se você já tem um formulário de pagamento, aqui você trataria POST
    #    Como seu fluxo atual parece ser “clicar e pagar”, vamos marcar direto.
    p.marcar_como_paga()

    messages.success(request, f"Parcela {p.numero} paga com sucesso.")
    return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

@transaction.atomic
def cancelar_contrato(request, emprestimo_id: int):
    contrato = get_object_or_404(Emprestimo.objects.select_related("cliente"), id=emprestimo_id)

    if request.method != "POST":
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    senha = (request.POST.get("senha") or "").strip()
    senha_correta = getattr(settings, "CONTRATO_DELETE_PASSWORD", "")

    if not senha_correta:
        messages.error(request, "Senha administrativa não configurada no settings.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    if senha != senha_correta:
        messages.error(request, "Senha inválida. Contrato não foi cancelado.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    motivo = (request.POST.get("motivo") or "").strip()
    observacao = (request.POST.get("observacao") or "").strip()

    # Marca parcelas em aberto como canceladas
    contrato.parcelas.filter(status=ParcelaStatus.ABERTA).update(status=ParcelaStatus.CANCELADA)

    # Marca contrato como cancelado
    contrato.status = EmprestimoStatus.CANCELADO
    contrato.cancelado_em = timezone.now()
    contrato.cancelado_por = request.user if request.user.is_authenticated else None
    contrato.motivo_cancelamento = motivo or None
    contrato.observacao_cancelamento = observacao or None

    # Também salva no observacoes (pra ficar visível e fácil)
    bloco = f"[CANCELADO] Motivo: {motivo}"
    if observacao:
        bloco += f" | Obs: {observacao}"
    contrato.observacoes = (contrato.observacoes or "").strip()
    contrato.observacoes = (contrato.observacoes + "\n" + bloco).strip()

    contrato.save()

    # Log
    ContratoLog.objects.create(
        contrato=contrato,
        acao=ContratoLog.Acao.CANCELADO,
        usuario=request.user if request.user.is_authenticated else None,
        motivo=motivo or None,
        observacao=observacao or None,
    )

    messages.success(request, "Contrato cancelado com sucesso.")
    return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

@transaction.atomic
def reabrir_contrato(request, emprestimo_id: int):
    contrato = get_object_or_404(Emprestimo, id=emprestimo_id)

    if request.method != "POST":
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    senha = (request.POST.get("senha") or "").strip()
    senha_correta = getattr(settings, "CONTRATO_DELETE_PASSWORD", "")

    if senha != senha_correta:
        messages.error(request, "Senha inválida. Contrato não foi reaberto.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    if contrato.status != EmprestimoStatus.CANCELADO:
        messages.error(request, "Este contrato não está cancelado.")
        return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)

    # Reabre parcelas canceladas (somente as canceladas)
    contrato.parcelas.filter(status=ParcelaStatus.CANCELADA).update(status=ParcelaStatus.ABERTA)

    contrato.status = EmprestimoStatus.ATIVO
    contrato.cancelado_em = None
    contrato.cancelado_por = None
    contrato.motivo_cancelamento = None
    contrato.observacao_cancelamento = None
    contrato.save()

    ContratoLog.objects.create(
        contrato=contrato,
        acao=ContratoLog.Acao.REABERTO,
        usuario=request.user if request.user.is_authenticated else None,
        motivo="Desfazer cancelamento",
    )

    messages.success(request, "Contrato reaberto com sucesso.")
    return redirect("emprestimos:contrato_detalhe", emprestimo_id=contrato.id)
