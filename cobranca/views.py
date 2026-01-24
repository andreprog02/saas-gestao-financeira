from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Min, Sum, Count
from django.contrib.auth.decorators import login_required

# Imports dos outros apps
from emprestimos.models import Emprestimo, Parcela, ParcelaStatus
from recebiveis.models import ContratoRecebivel, ItemRecebivel
from .models import HistoricoCobranca

def calcular_acao_sugerida(dias_atraso):
    if dias_atraso <= 5:
        return "Lembrete Amigável", "success"
    elif dias_atraso <= 15:
        return "Contato Verbal / WhatsApp", "info"
    elif dias_atraso <= 30:
        return "Carta de Cobrança", "warning"
    elif dias_atraso <= 60:
        return "Negativação (SPC/Serasa)", "danger"
    else:
        return "Execução Judicial", "dark"

@login_required
def painel_cobranca(request):
    hoje = timezone.localdate()
    lista_devedores = []

    # === 1. BUSCAR EMPRÉSTIMOS EM ATRASO ===
    # Filtra apenas parcelas abertas e vencidas
    todas_parcelas_vencidas = Parcela.objects.filter(
        status=ParcelaStatus.ABERTA, 
        vencimento__lt=hoje
    ).select_related('emprestimo', 'emprestimo__cliente')

    # Correção de Duplicidade: Usar set() para IDs únicos
    emprestimos_ids = set(todas_parcelas_vencidas.values_list('emprestimo_id', flat=True))
    
    for emp_id in emprestimos_ids:
        # Pega as parcelas vencidas DESTE contrato específico
        parcelas = todas_parcelas_vencidas.filter(emprestimo_id=emp_id).order_by('vencimento')
        
        if not parcelas.exists(): continue
            
        emprestimo = parcelas.first().emprestimo
        
        primeiro_vencimento = parcelas.first().vencimento
        valor_total = parcelas.aggregate(Sum('valor'))['valor__sum']
        qtd = parcelas.count()
        
        dias_atraso = (hoje - primeiro_vencimento).days
        acao, cor = calcular_acao_sugerida(dias_atraso)
        
        ultimo_evento = HistoricoCobranca.objects.filter(emprestimo=emprestimo).first()

        lista_devedores.append({
            'tipo': 'EMPRESTIMO',
            'id_contrato': emprestimo.id,
            'codigo': emprestimo.codigo_contrato,
            'cliente': emprestimo.cliente,
            'valor_atraso': valor_total,
            'qtd_itens': f"{qtd} Parcela(s)",
            'dias_atraso': dias_atraso,
            'primeiro_atraso': primeiro_vencimento,
            'acao_sugerida': acao,
            'cor_badge': cor,
            'ultimo_evento': ultimo_evento,
            # DETALHES PARA O MODAL ANALÍTICO
            'itens_detalhe': parcelas, 
            'link_renegociar': 'emprestimos:contrato_detalhe' # Link para tela principal
        })

    # === 2. BUSCAR RECEBÍVEIS EM ATRASO ===
    todos_itens_vencidos = ItemRecebivel.objects.filter(
        status='aberto', 
        vencimento__lt=hoje
    ).select_related('contrato', 'contrato__cliente')
    
    # Correção de Duplicidade
    contratos_rec_ids = set(todos_itens_vencidos.values_list('contrato_id', flat=True))

    for rec_id in contratos_rec_ids:
        itens = todos_itens_vencidos.filter(contrato_id=rec_id).order_by('vencimento')
        if not itens.exists(): continue
        
        contrato_rec = itens.first().contrato
        
        primeiro_vencimento = itens.first().vencimento
        valor_total = itens.aggregate(Sum('valor'))['valor__sum']
        qtd = itens.count()
        tipos = list(itens.values_list('tipo', flat=True).distinct()) 
        tipos_str = ", ".join([t.title() for t in tipos])

        dias_atraso = (hoje - primeiro_vencimento).days
        acao, cor = calcular_acao_sugerida(dias_atraso)
        
        ultimo_evento = HistoricoCobranca.objects.filter(recebivel=contrato_rec).first()

        lista_devedores.append({
            'tipo': 'RECEBIVEL',
            'id_contrato': contrato_rec.id,
            'codigo': contrato_rec.contrato_id,
            'cliente': contrato_rec.cliente,
            'valor_atraso': valor_total,
            'qtd_itens': f"{qtd} ({tipos_str})",
            'dias_atraso': dias_atraso,
            'primeiro_atraso': primeiro_vencimento,
            'acao_sugerida': acao,
            'cor_badge': cor,
            'ultimo_evento': ultimo_evento,
             # DETALHES PARA O MODAL ANALÍTICO
            'itens_detalhe': itens,
            'link_renegociar': 'recebiveis:lista_contratos'
        })

    # Ordenar por dias de atraso (maior para menor)
    lista_devedores.sort(key=lambda x: x['dias_atraso'], reverse=True)

    return render(request, 'cobranca/painel.html', {'lista': lista_devedores})

@login_required
def registrar_evento(request):
    if request.method == 'POST':
        tipo = request.POST.get('tipo_contrato')
        id_contrato = request.POST.get('id_contrato')
        data_evento = request.POST.get('data_evento')
        descricao = request.POST.get('descricao')

        if not descricao or not data_evento:
            messages.error(request, "Preencha a data e a descrição.")
            return redirect('cobranca:painel_cobranca')

        try:
            evento = HistoricoCobranca(
                data_evento=data_evento,
                descricao=descricao,
                usuario=request.user,
                tipo_contrato=tipo
            )

            if tipo == 'EMPRESTIMO':
                emp = Emprestimo.objects.get(id=id_contrato)
                evento.emprestimo = emp
                evento.cliente = emp.cliente
            elif tipo == 'RECEBIVEL':
                rec = ContratoRecebivel.objects.get(id=id_contrato)
                evento.recebivel = rec
                evento.cliente = rec.cliente
            
            evento.save()
            messages.success(request, "Evento registrado com sucesso.")
        except Exception as e:
            messages.error(request, f"Erro ao salvar: {str(e)}")

    return redirect('cobranca:painel_cobranca')