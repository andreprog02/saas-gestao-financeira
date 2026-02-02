from django.shortcuts import render
from django.db.models import Sum, Count
from django.utils import timezone
from decimal import Decimal

# Importando os modelos dos outros apps
from emprestimos.models import Emprestimo, Parcela, ParcelaStatus, EmprestimoStatus
from recebiveis.models import ContratoRecebivel, ItemRecebivel
from financeiro.models import Transacao, calcular_saldo_atual

def dashboard(request):
    hoje = timezone.localdate()
    
    # 1. Saldo em Caixa (Vem do app Financeiro)
    saldo_caixa = calcular_saldo_atual()
    
    # 2. Resumo de Empréstimos (Ativos e Atrasados)
    # Filtramos apenas os que não foram quitados ou cancelados
    emprestimos_vivos = Emprestimo.objects.filter(
        status__in=[EmprestimoStatus.ATIVO, EmprestimoStatus.ATRASADO]
    )
    total_emprestimos_qtd = emprestimos_vivos.count()
    total_emprestimos_valor = emprestimos_vivos.aggregate(Sum('valor_emprestado'))['valor_emprestado__sum'] or Decimal('0.00')
    
    # 3. Resumo de Recebíveis (Apenas Ativos)
    recebiveis_ativos = ContratoRecebivel.objects.filter(status='ativo')
    total_recebiveis_qtd = recebiveis_ativos.count()
    total_recebiveis_valor = recebiveis_ativos.aggregate(Sum('valor_bruto'))['valor_bruto__sum'] or Decimal('0.00')
    
    # 4. Gestão de Inadimplência (Contratos com parcelas vencidas)
    # Buscamos parcelas de empréstimos vencidas
    parcelas_atrasadas = Parcela.objects.filter(
        status=ParcelaStatus.ABERTA, 
        vencimento__lt=hoje
    )
    valor_atraso_emp = parcelas_atrasadas.aggregate(Sum('valor'))['valor__sum'] or Decimal('0.00')
    contratos_emp_atrasados_ids = parcelas_atrasadas.values_list('emprestimo', flat=True).distinct()
    
    # Buscamos itens de recebíveis (cheques/títulos) vencidos
    itens_atrasados = ItemRecebivel.objects.filter(
        status='aberto', 
        vencimento__lt=hoje
    )
    valor_atraso_rec = itens_atrasados.aggregate(Sum('valor'))['valor__sum'] or Decimal('0.00')
    contratos_rec_atrasados_ids = itens_atrasados.values_list('contrato', flat=True).distinct()
    
    # Consolidação do Atraso
    total_contratos_atrasados = len(set(list(contratos_emp_atrasados_ids) + [f"R{i}" for i in contratos_rec_atrasados_ids]))
    total_valor_atrasado = valor_atraso_emp + valor_atraso_rec

    # 5. Próximos Vencimentos (Lista rápida para o resumo)
    proximos_7_dias = hoje + timezone.timedelta(days=7)
    prox_recebimentos = Parcela.objects.filter(
        status=ParcelaStatus.ABERTA,
        vencimento__range=[hoje, proximos_7_dias]
    ).select_related('emprestimo', 'emprestimo__cliente').order_by('vencimento')[:5]

    context = {
        'saldo_caixa': saldo_caixa,
        'total_emprestimos_qtd': total_emprestimos_qtd,
        'total_emprestimos_valor': total_emprestimos_valor,
        'total_recebiveis_qtd': total_recebiveis_qtd,
        'total_recebiveis_valor': total_recebiveis_valor,
        'total_contratos_atrasados': total_contratos_atrasados,
        'total_valor_atrasado': total_valor_atrasado,
        'prox_recebimentos': prox_recebimentos,
        'hoje': hoje,
    }
    
    return render(request, "core/dashboard.html", context)

def contrato_detalhe(request, pk):
    contrato = get_object_or_404(Emprestimo, pk=pk)
    # ESSENCIAL: Enviar o 'hoje' para o template
    return render(request, "emprestimos/contrato_detalhe.html", {
        "contrato": contrato,
        "parcelas": contrato.parcelas.all().order_by('numero'),
        "hoje": timezone.localdate(), # <--- Adicione isso aqui
    })

def calcular_valores_ajax(request, parcela_id):
    parcela = get_object_or_404(Parcela, id=parcela_id)
    dados = parcela.dados_atualizados() # Usa sua lógica do models.py
    return JsonResponse({
        'multa': f"{dados['multa']:.2f}",
        'juros': f"{dados['juros']:.2f}",
        'total': f"{dados['valor_total']:.2f}"
    })