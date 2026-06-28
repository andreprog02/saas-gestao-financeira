import uuid
from decimal import Decimal, InvalidOperation
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse

from .models import Transacao, CodigoOperacao, Caixa, MovimentacaoCaixa, calcular_saldo_atual
from clientes.models import Cliente
from contas.models import ContaCorrente, MovimentacaoConta


def parse_valor_monetario(valor_str):
    if not valor_str:
        return Decimal("0.00")
    limpo = valor_str.replace("R$", "").replace(" ", "").strip()
    if "," in limpo and "." in limpo:
        limpo = limpo.replace(".", "").replace(",", ".")
    elif "," in limpo:
        limpo = limpo.replace(",", ".")
    return Decimal(limpo).quantize(Decimal("0.01"))


# ==============================================================================
# FLUXO DE CAIXA (legado — mantém funcionando)
# ==============================================================================

@login_required
def index(request):
    if request.method == "POST":
        tipo = request.POST.get("tipo")
        valor = parse_valor_monetario(request.POST.get("valor", "0"))
        descricao = request.POST.get("descricao", "").strip()
        codigo_op_id = request.POST.get("codigo_operacao")
        cliente_id = request.POST.get("cliente")

        if valor <= 0:
            messages.error(request, "Valor deve ser positivo.")
        elif not descricao:
            messages.error(request, "Informe a descrição.")
        else:
            t = Transacao(
                tipo=tipo, valor=valor, descricao=descricao,
                usuario=request.user, ip_origem=request.META.get("REMOTE_ADDR"),
                codigo_autenticacao=str(uuid.uuid4()),
            )
            if codigo_op_id:
                t.codigo_operacao_id = codigo_op_id
            t.save()
            messages.success(request, f"Lançamento registrado: R$ {t.valor}")

    transacoes = Transacao.objects.select_related("codigo_operacao", "usuario").order_by("-data")[:50]
    saldo = calcular_saldo_atual()
    codigos = CodigoOperacao.objects.filter(ativo=True).order_by("codigo")
    clientes = Cliente.objects.all().order_by("nome_completo")

    return render(request, "financeiro/index.html", {
        "transacoes": transacoes,
        "saldo_atual": saldo,
        "codigos_operacao": codigos,
        "clientes": clientes,
        "TIPO_CHOICES": Transacao.TIPO_CHOICES,
    })


@login_required
def estornar(request, transacao_id):
    if request.method == "POST":
        senha = request.POST.get("senha")
        user = authenticate(username=request.user.username, password=senha)
        if user:
            original = get_object_or_404(Transacao, id=transacao_id)
            if Transacao.objects.filter(transacao_original=original).exists():
                messages.error(request, "Já estornado.")
                return redirect("financeiro:index")
            with transaction.atomic():
                Transacao.objects.create(
                    tipo=original.tipo,
                    valor=-original.valor,
                    descricao=f"ESTORNO: {original.descricao}",
                    usuario=request.user,
                    transacao_original=original,
                    codigo_operacao=original.codigo_operacao,
                )
            messages.success(request, "Estorno realizado.")
        else:
            messages.error(request, "Senha incorreta.")
    return redirect("financeiro:index")


# ==============================================================================
# CONTROLE DE CAIXA
# ==============================================================================

@login_required
def caixa_painel(request):
    hoje = timezone.localdate()
    caixa_hoje = Caixa.objects.filter(data=hoje).first()
    historico = Caixa.objects.exclude(data=hoje).order_by("-data")[:30]

    # Saldo físico do caixa de hoje
    saldo_fisico = Decimal("0.00")
    if caixa_hoje and caixa_hoje.status == "ABERTO":
        saldo_fisico = caixa_hoje.saldo_fisico_calculado

    return render(request, "financeiro/caixa_painel.html", {
        "caixa_hoje": caixa_hoje,
        "historico": historico,
        "saldo_fisico": saldo_fisico,
        "hoje": hoje,
    })


@login_required
def caixa_abrir(request):
    """Abre um novo caixa — usuário escolhe identificador, conta cédulas da tesouraria."""
    from .models import Tesouraria, MovimentacaoTesouraria
    hoje = timezone.localdate()

    tesouraria = Tesouraria.objects.filter(pk=1).first()
    if not tesouraria:
        messages.error(request, "Tesouraria não inicializada. Acesse a tesouraria primeiro.")
        return redirect("financeiro:tesouraria_painel")

    if request.method == "POST":
        identificador = request.POST.get("identificador", "").strip() or "Caixa 1"

        cedulas = {}
        total_ced = Decimal("0.00")
        for val in ["200", "100", "50", "20", "10", "5", "2"]:
            qtd = int(request.POST.get(f"ced_{val}", 0) or 0)
            cedulas[val] = qtd
            total_ced += Decimal(val) * qtd

        moedas = {}
        total_moe = Decimal("0.00")
        for val, key in [("1.00", "100"), ("0.50", "050"), ("0.25", "025"), ("0.10", "010"), ("0.05", "005")]:
            qtd = int(request.POST.get(f"moe_{key}", 0) or 0)
            moedas[key] = qtd
            total_moe += Decimal(val) * qtd

        saldo = total_ced + total_moe
        if saldo <= 0:
            messages.error(request, "Informe a contagem de cédulas/moedas.")
            return redirect("financeiro:caixa_abrir")

        caixa = Caixa.objects.create(
            data=hoje, identificador=identificador, saldo_abertura=saldo,
            aberto_por=request.user, aberto_em=timezone.now(),
        )

        MovimentacaoTesouraria.objects.create(
            tesouraria=tesouraria, tipo="ENVIO_CAIXA", valor=saldo,
            descricao=f"Envio p/ {identificador} — {request.user.get_full_name() or request.user.username}",
            caixa_destino=caixa, usuario=request.user,
        )
        tesouraria.recalcular_saldo()

        messages.success(request, f"{identificador} aberto com R$ {saldo:.2f} da tesouraria.")
        return redirect("financeiro:caixa_painel")

    caixas_abertos = Caixa.objects.filter(status="ABERTO")
    proximo_num = caixas_abertos.count() + 1

    return render(request, "financeiro/caixa_abrir.html", {
        "hoje": hoje, "tesouraria": tesouraria,
        "sugestao": f"Caixa {proximo_num}",
    })


@login_required
def caixa_reabrir(request):
    """Reabre o caixa fechado de hoje (com senha)."""
    hoje = timezone.localdate()
    caixa = Caixa.objects.filter(data=hoje, status="FECHADO").first()
    if not caixa:
        messages.error(request, "Nenhum caixa fechado hoje para reabrir.")
        return redirect("financeiro:caixa_painel")

    if request.method == "POST":
        senha = request.POST.get("senha", "")
        user = authenticate(username=request.user.username, password=senha)
        if not user:
            messages.error(request, "Senha inválida.")
            return redirect("financeiro:caixa_painel")

        caixa.status = "ABERTO"
        caixa.fechado_por = None
        caixa.fechado_em = None
        caixa.saldo_conferido = Decimal("0.00")
        caixa.diferenca = Decimal("0.00")
        caixa.contagem_cedulas = {}
        caixa.contagem_moedas = {}
        caixa.observacoes_fechamento = ""
        caixa.save()
        messages.success(request, "Caixa reaberto.")

    return redirect("financeiro:caixa_painel")


# ==============================================================================
# LANÇAMENTOS DO CAIXA
# ==============================================================================

@login_required
def caixa_lancamento(request):
    """Tela de lançamentos — o caixa registra operações aqui."""
    hoje = timezone.localdate()
    caixa = Caixa.objects.filter(data=hoje, status="ABERTO").first()

    if not caixa:
        messages.error(request, "Caixa não está aberto. Abra o caixa primeiro.")
        return redirect("financeiro:caixa_painel")

    if request.method == "POST":
        codigo_id = request.POST.get("codigo_operacao")
        valor = parse_valor_monetario(request.POST.get("valor", "0"))
        descricao = request.POST.get("descricao", "").strip()
        cliente_id = request.POST.get("cliente_id", "")

        if not codigo_id or valor <= 0:
            messages.error(request, "Selecione a operação e informe o valor.")
            return redirect("financeiro:caixa_lancamento")

        codigo = get_object_or_404(CodigoOperacao, id=codigo_id)

        mov = MovimentacaoCaixa(
            caixa=caixa,
            codigo_operacao=codigo,
            valor=valor,
            descricao=descricao or codigo.descricao,
            usuario=request.user,
        )

        if cliente_id:
            mov.cliente_id = int(cliente_id)

        mov.save()

        # Se mexe em conta corrente do cliente
        if codigo.exige_cliente and cliente_id:
            try:
                cc = ContaCorrente.objects.get(cliente_id=int(cliente_id))
                if codigo.tipo == "E":
                    # Saque da C/C do cliente (entrada no caixa = saída da C/C)
                    MovimentacaoConta.objects.create(
                        conta=cc, tipo="DEBITO",
                        valor=valor, descricao=f"Caixa: {codigo.descricao}",
                    )
                else:
                    # Depósito na C/C do cliente (saída do caixa = entrada na C/C)
                    MovimentacaoConta.objects.create(
                        conta=cc, tipo="CREDITO",
                        valor=valor, descricao=f"Caixa: {codigo.descricao}",
                    )
            except ContaCorrente.DoesNotExist:
                pass

        tipo_txt = "💵 Físico" if mov.afetou_caixa_fisico else "🔄 Eletrônico"
        messages.success(request, f"Lançamento {mov.numero_autenticacao}: {codigo.descricao} — R$ {abs(mov.valor):.2f} ({tipo_txt})")
        return redirect("financeiro:caixa_lancamento")

    codigos = CodigoOperacao.objects.filter(ativo=True).order_by("codigo")
    movimentacoes = caixa.movimentacoes.select_related(
        "codigo_operacao", "usuario", "cliente"
    ).order_by("-data_hora")[:50]

    saldo_fisico = caixa.saldo_fisico_calculado
    total_eletronico = caixa.movimentacoes.filter(
        afetou_caixa_fisico=False, estornado=False,
    ).aggregate(s=Sum("valor"))["s"] or Decimal("0.00")

    clientes = Cliente.objects.all().order_by("nome_completo")

    return render(request, "financeiro/caixa_lancamento.html", {
        "caixa": caixa,
        "codigos": codigos,
        "movimentacoes": movimentacoes,
        "saldo_fisico": saldo_fisico,
        "total_eletronico": total_eletronico,
        "clientes": clientes,
        "hoje": hoje,
    })


@login_required
def caixa_estornar(request, mov_id):
    """Estorna uma movimentação do caixa (com senha)."""
    mov = get_object_or_404(MovimentacaoCaixa, id=mov_id)

    if request.method == "POST":
        senha = request.POST.get("senha", "")
        user = authenticate(username=request.user.username, password=senha)
        if not user:
            messages.error(request, "Senha inválida.")
            return redirect("financeiro:caixa_lancamento")

        if mov.estornado:
            messages.warning(request, "Já estornado.")
            return redirect("financeiro:caixa_lancamento")

        mov.estornado = True
        mov.estornado_por = request.user
        mov.estornado_em = timezone.now()
        mov.motivo_estorno = request.POST.get("motivo", "")
        mov.save()

        messages.success(request, f"Movimentação {mov.numero_autenticacao} estornada.")

    return redirect("financeiro:caixa_lancamento")


# ==============================================================================
# FECHAMENTO DE CAIXA (conferência de cédulas/moedas)
# ==============================================================================

@login_required
def caixa_fechar(request, caixa_id=None):
    """Fechamento do caixa — seleciona qual fechar, envia numerário para tesouraria."""
    from .models import Tesouraria, MovimentacaoTesouraria

    # Se não veio caixa_id, mostra lista dos abertos pra escolher
    if not caixa_id:
        caixas_abertos = Caixa.objects.filter(status="ABERTO").order_by("-data", "identificador")
        if caixas_abertos.count() == 0:
            messages.error(request, "Nenhum caixa aberto.")
            return redirect("financeiro:caixa_painel")
        if caixas_abertos.count() == 1:
            return redirect("financeiro:caixa_fechar_id", caixa_id=caixas_abertos.first().id)
        return render(request, "financeiro/caixa_selecionar_fechar.html", {
            "caixas": caixas_abertos,
        })

    caixa = get_object_or_404(Caixa, id=caixa_id, status="ABERTO")
    tesouraria = Tesouraria.objects.filter(pk=1).first()
    saldo_fisico = caixa.saldo_fisico_calculado

    if request.method == "POST":
        cedulas = {}
        total_ced = Decimal("0.00")
        for val in ["200", "100", "50", "20", "10", "5", "2"]:
            qtd = int(request.POST.get(f"ced_{val}", 0) or 0)
            cedulas[val] = qtd
            total_ced += Decimal(val) * qtd

        moedas = {}
        total_moe = Decimal("0.00")
        for val, key in [("1.00", "100"), ("0.50", "050"), ("0.25", "025"), ("0.10", "010"), ("0.05", "005")]:
            qtd = int(request.POST.get(f"moe_{key}", 0) or 0)
            moedas[key] = qtd
            total_moe += Decimal(val) * qtd

        saldo_conferido = total_ced + total_moe
        diferenca = saldo_conferido - saldo_fisico

        caixa.saldo_sistema = saldo_fisico
        caixa.saldo_conferido = saldo_conferido
        caixa.diferenca = diferenca
        caixa.contagem_cedulas = cedulas
        caixa.contagem_moedas = moedas
        caixa.observacoes_fechamento = request.POST.get("observacoes", "")
        caixa.fechado_por = request.user
        caixa.fechado_em = timezone.now()
        caixa.status = "FECHADO"
        caixa.save()

        if tesouraria and saldo_conferido > 0:
            MovimentacaoTesouraria.objects.create(
                tesouraria=tesouraria, tipo="RECEB_CAIXA", valor=saldo_conferido,
                descricao=f"Recebimento {caixa.identificador} — {request.user.get_full_name() or request.user.username}",
                caixa_destino=caixa, usuario=request.user,
            )
            tesouraria.recalcular_saldo()

        if abs(diferenca) < Decimal("0.01"):
            messages.success(request, f"{caixa.identificador} fechado — sem diferença. R$ {saldo_conferido:.2f} devolvido à tesouraria.")
        elif diferenca > 0:
            messages.warning(request, f"{caixa.identificador} fechado — SOBRA R$ {diferenca:.2f}.")
        else:
            messages.error(request, f"{caixa.identificador} fechado — FALTA R$ {abs(diferenca):.2f}.")

        return redirect("financeiro:caixa_painel")

    return render(request, "financeiro/caixa_fechar.html", {
        "caixa": caixa, "saldo_sistema": saldo_fisico,
        "hoje": caixa.data,
    })


@login_required
def caixa_detalhe(request, caixa_id):
    caixa = get_object_or_404(Caixa, id=caixa_id)
    cedulas_display = []
    total_ced = Decimal("0.00")
    for val in ["200", "100", "50", "20", "10", "5", "2"]:
        qtd = caixa.contagem_cedulas.get(val, 0)
        sub = Decimal(val) * qtd
        total_ced += sub
        cedulas_display.append({"valor": val, "qtd": qtd, "subtotal": sub})

    moedas_display = []
    total_moe = Decimal("0.00")
    for val, key in [("1.00", "100"), ("0.50", "050"), ("0.25", "025"), ("0.10", "010"), ("0.05", "005")]:
        qtd = caixa.contagem_moedas.get(key, 0)
        sub = Decimal(val) * qtd
        total_moe += sub
        moedas_display.append({"valor": val, "qtd": qtd, "subtotal": sub})

    movimentacoes = caixa.movimentacoes.select_related(
        "codigo_operacao", "usuario", "cliente"
    ).order_by("data_hora")

    return render(request, "financeiro/caixa_detalhe.html", {
        "caixa": caixa,
        "cedulas": cedulas_display, "moedas": moedas_display,
        "total_cedulas": total_ced, "total_moedas": total_moe,
        "movimentacoes": movimentacoes,
    })



@login_required
def caixa_historico(request):
    """Histórico de lançamentos por dia."""
    data_str = request.GET.get("data", "")
    data = None
    caixa = None
    movimentacoes = []
    totais = {}

    if data_str:
        from datetime import date
        data = date.fromisoformat(data_str)
        caixa = Caixa.objects.filter(data=data).first()
        if caixa:
            movimentacoes = caixa.movimentacoes.select_related(
                "codigo_operacao", "usuario", "cliente"
            ).order_by("data_hora")

            totais = {
                "entradas_fisico": caixa.movimentacoes.filter(afetou_caixa_fisico=True, valor__gt=0, estornado=False).aggregate(s=Sum("valor"))["s"] or Decimal("0.00"),
                "saidas_fisico": abs(caixa.movimentacoes.filter(afetou_caixa_fisico=True, valor__lt=0, estornado=False).aggregate(s=Sum("valor"))["s"] or Decimal("0.00")),
                "entradas_eletronico": caixa.movimentacoes.filter(afetou_caixa_fisico=False, valor__gt=0, estornado=False).aggregate(s=Sum("valor"))["s"] or Decimal("0.00"),
                "saidas_eletronico": abs(caixa.movimentacoes.filter(afetou_caixa_fisico=False, valor__lt=0, estornado=False).aggregate(s=Sum("valor"))["s"] or Decimal("0.00")),
                "total_estornos": caixa.movimentacoes.filter(estornado=True).count(),
            }

    return render(request, "financeiro/caixa_historico.html", {
        "data_selecionada": data_str,
        "data": data,
        "caixa": caixa,
        "movimentacoes": movimentacoes,
        "totais": totais,
    })

@login_required
def buscar_cliente_ajax(request):
    """Busca clientes por nome/CPF para o lançamento."""
    q = request.GET.get("q", "")
    if len(q) < 2:
        return JsonResponse({"resultados": []})
    clientes = Cliente.objects.filter(
        Q(nome_completo__icontains=q) | Q(cpf__icontains=q)
    )[:10]
    return JsonResponse({"resultados": [
        {"id": c.id, "nome": c.nome_completo, "cpf": c.cpf}
        for c in clientes
    ]})


# ==============================================================================
# TESOURARIA
# ==============================================================================

@login_required
def tesouraria_painel(request):
    """Painel da tesouraria — cofre permanente com saldo contínuo."""
    from .models import Tesouraria, MovimentacaoTesouraria
    hoje = timezone.localdate()

    # Tesouraria é singleton — cria se não existir
    tesouraria, criada = Tesouraria.objects.get_or_create(
        pk=1, defaults={
            "data": hoje, "saldo_abertura": Decimal("0.00"),
            "saldo_atual": Decimal("0.00"), "status": "ABERTA",
            "aberto_por": request.user, "aberto_em": timezone.now(),
        }
    )
    tesouraria.recalcular_saldo()

    caixas_abertos = Caixa.objects.filter(data=hoje, status="ABERTO")

    # Filtro por data
    data_filtro = request.GET.get("data", "")
    movimentacoes = tesouraria.movimentacoes.select_related("caixa_destino", "usuario").order_by("-data_hora")
    if data_filtro:
        movimentacoes = movimentacoes.filter(data_hora__date=data_filtro)
    else:
        movimentacoes = movimentacoes[:30]

    return render(request, "financeiro/tesouraria_painel.html", {
        "tesouraria": tesouraria,
        "caixas_abertos": caixas_abertos,
        "movimentacoes": movimentacoes,
        "hoje": hoje,
        "data_filtro": data_filtro,
    })


@login_required
def tesouraria_lancamento(request):
    """Registra movimentação na tesouraria."""
    from .models import Tesouraria, MovimentacaoTesouraria

    tesouraria = Tesouraria.objects.filter(pk=1).first()
    if not tesouraria:
        messages.error(request, "Tesouraria não inicializada.")
        return redirect("financeiro:tesouraria_painel")

    if request.method == "POST":
        tipo = request.POST.get("tipo", "")
        valor = parse_valor_monetario(request.POST.get("valor", "0"))
        descricao = request.POST.get("descricao", "")
        caixa_id = request.POST.get("caixa_id", "")

        if not tipo or valor <= 0:
            messages.error(request, "Informe tipo e valor.")
            return redirect("financeiro:tesouraria_painel")

        mov = MovimentacaoTesouraria(
            tesouraria=tesouraria, tipo=tipo, valor=valor,
            descricao=descricao, usuario=request.user,
        )
        if caixa_id:
            mov.caixa_destino_id = int(caixa_id)
        mov.save()
        tesouraria.recalcular_saldo()

        messages.success(request, f"{mov.get_tipo_display()}: R$ {abs(mov.valor):.2f}")

    return redirect("financeiro:tesouraria_painel")


# ==============================================================================
# CUSTÓDIA DE CHEQUES
# ==============================================================================

@login_required
def custodia_painel(request):
    """Painel de custódia de cheques."""
    from .models import ChequeCustodia
    from emprestimos.models import ChequeGarantia

    filtro = request.GET.get("status", "")
    cheques = ChequeCustodia.objects.select_related("cliente", "emprestimo").all()

    if filtro:
        cheques = cheques.filter(status=filtro)

    totais = {
        "custodia": ChequeCustodia.objects.filter(status="EM_CUSTODIA").count(),
        "compensacao": ChequeCustodia.objects.filter(status="ENVIADO_COMPENSACAO").count(),
        "compensados": ChequeCustodia.objects.filter(status="COMPENSADO").count(),
        "devolvidos": ChequeCustodia.objects.filter(status="DEVOLVIDO").count(),
        "valor_custodia": ChequeCustodia.objects.filter(status="EM_CUSTODIA").aggregate(s=Sum("valor"))["s"] or Decimal("0"),
        "vencendo_hoje": ChequeCustodia.objects.filter(status="EM_CUSTODIA", vencimento=timezone.localdate()).count(),
    }

    return render(request, "financeiro/custodia_painel.html", {
        "cheques": cheques[:100],
        "totais": totais,
        "filtro": filtro,
    })


@login_required
def custodia_entrada(request):
    """Registra entrada de cheque na custódia (manual ou vindo da formalização)."""
    from .models import ChequeCustodia

    if request.method == "POST":
        ChequeCustodia.objects.create(
            banco=request.POST.get("banco", ""),
            agencia=request.POST.get("agencia", ""),
            conta=request.POST.get("conta", ""),
            numero_cheque=request.POST.get("numero_cheque", ""),
            valor=parse_valor_monetario(request.POST.get("valor", "0")),
            vencimento=request.POST.get("vencimento", timezone.localdate()),
            emitente=request.POST.get("emitente", ""),
            cpf_emitente=request.POST.get("cpf_emitente", ""),
            cliente_id=request.POST.get("cliente_id") or None,
            registrado_por=request.user,
        )
        messages.success(request, "Cheque registrado na custódia.")

    return redirect("financeiro:custodia_painel")


@login_required
def custodia_acao(request, cheque_id):
    """Muda status do cheque com integração à conta corrente do cliente."""
    from .models import ChequeCustodia
    from contas.models import ContaCorrente, MovimentacaoConta

    cheque = get_object_or_404(ChequeCustodia, id=cheque_id)
    acao = request.POST.get("acao", "")

    if acao == "enviar_compensacao":
        cheque.status = "ENVIADO_COMPENSACAO"
        cheque.data_envio_compensacao = timezone.localdate()
        cheque.save()

        # Credita na C/C como saldo BLOQUEADO
        if cheque.cliente:
            try:
                cc = ContaCorrente.objects.get(cliente=cheque.cliente)
                MovimentacaoConta.objects.create(
                    conta=cc, tipo="CREDITO_BLOQUEADO",
                    origem="CHEQUE_COMPENSACAO",
                    valor=cheque.valor,
                    descricao=f"Cheque {cheque.numero_cheque} ({cheque.banco}) em compensação — BLOQUEADO",
                    cheque_custodia=cheque,
                    emprestimo=cheque.emprestimo,
                )
            except ContaCorrente.DoesNotExist:
                pass

        messages.info(request, f"Cheque {cheque.numero_cheque} enviado para compensação. Saldo bloqueado na C/C.")

    elif acao == "compensar":
        cheque.status = "COMPENSADO"
        cheque.data_compensacao = timezone.localdate()
        cheque.save()

        # Desbloqueia e credita na C/C
        if cheque.cliente:
            try:
                cc = ContaCorrente.objects.get(cliente=cheque.cliente)
                MovimentacaoConta.objects.create(
                    conta=cc, tipo="DESBLOQUEIO",
                    origem="CHEQUE_COMPENSADO",
                    valor=cheque.valor,
                    descricao=f"Cheque {cheque.numero_cheque} ({cheque.banco}) COMPENSADO — saldo liberado",
                    cheque_custodia=cheque,
                    emprestimo=cheque.emprestimo,
                )
            except ContaCorrente.DoesNotExist:
                pass

        messages.success(request, f"Cheque {cheque.numero_cheque} compensado. Saldo creditado na C/C.")

    elif acao == "devolver":
        alinea = request.POST.get("alinea", "")
        motivo = request.POST.get("motivo", "")
        motivo_completo = f"Alínea {alinea}: {dict(MovimentacaoConta.ALINEA_CHOICES).get(alinea, '')}. {motivo}".strip() if alinea else motivo

        cheque.status = "DEVOLVIDO"
        cheque.data_devolucao = timezone.localdate()
        cheque.motivo_devolucao = motivo_completo
        cheque.save()

        # Estorna o saldo bloqueado
        if cheque.cliente:
            try:
                cc = ContaCorrente.objects.get(cliente=cheque.cliente)
                # Busca a movimentação bloqueada original
                mov_bloq = MovimentacaoConta.objects.filter(
                    cheque_custodia=cheque, tipo="CREDITO_BLOQUEADO", estornado=False
                ).first()
                if mov_bloq:
                    mov_bloq.estornado = True
                    mov_bloq.save()

                # Remove do saldo bloqueado
                cc.saldo_bloqueado = max(Decimal("0"), cc.saldo_bloqueado - cheque.valor)
                cc.save()

                # Registra o estorno
                MovimentacaoConta.objects.create(
                    conta=cc, tipo="DEBITO",
                    origem="CHEQUE_DEVOLVIDO",
                    valor=Decimal("0"),  # não debita saldo real, só registra
                    descricao=f"Cheque {cheque.numero_cheque} DEVOLVIDO — {motivo_completo}",
                    cheque_custodia=cheque,
                    alinea=alinea,
                    emprestimo=cheque.emprestimo,
                )
            except ContaCorrente.DoesNotExist:
                pass

        messages.warning(request, f"Cheque {cheque.numero_cheque} devolvido. Alínea: {alinea}. Saldo bloqueado estornado.")

    elif acao == "reapresentar":
        if cheque.status != "DEVOLVIDO":
            messages.error(request, "Só é possível reapresentar cheques devolvidos.")
            return redirect("financeiro:custodia_painel")

        cheque.status = "ENVIADO_COMPENSACAO"
        cheque.data_envio_compensacao = timezone.localdate()
        cheque.data_devolucao = None
        cheque.motivo_devolucao = ""
        cheque.save()

        # Bloqueia novamente na C/C
        if cheque.cliente:
            try:
                cc = ContaCorrente.objects.get(cliente=cheque.cliente)
                MovimentacaoConta.objects.create(
                    conta=cc, tipo="CREDITO_BLOQUEADO",
                    origem="CHEQUE_COMPENSACAO",
                    valor=cheque.valor,
                    descricao=f"Cheque {cheque.numero_cheque} REAPRESENTADO — 2ª via — BLOQUEADO",
                    cheque_custodia=cheque,
                    emprestimo=cheque.emprestimo,
                )
            except ContaCorrente.DoesNotExist:
                pass

        messages.info(request, f"Cheque {cheque.numero_cheque} reapresentado. Saldo bloqueado novamente.")

    return redirect("financeiro:custodia_painel")
