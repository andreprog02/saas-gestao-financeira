from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from .models import ContratoRecebivel
from .forms import RenegociacaoForm, ItemRecebivelForm
from .services import registrar_financeiro_ajuste

def renegociar_contrato(request, contrato_id):
    contrato = get_object_or_404(ContratoRecebivel, id=contrato_id)
    if contrato.status != 'ativo':
        messages.error(request, 'Apenas contratos ativos podem ser renegociados.')
        return redirect('lista_contratos')
    
    if request.method == 'POST':
        form = RenegociacaoForm(request.POST, instance=contrato)
        if form.is_valid():
            form.save()
            contrato.status = 'renegociado'
            contrato.calcular_valores()
            registrar_financeiro_ajuste(contrato)  # Registra ajuste no livro caixa
            messages.success(request, 'Contrato renegociado com sucesso.')
            return redirect('simular_contrato', contrato_id=contrato.id)
    else:
        form = RenegociacaoForm(instance=contrato)
    
    # Opção para adicionar mais itens durante renegociação
    item_form = ItemRecebivelForm()
    if request.GET.get('add_item'):
        if request.method == 'POST':
            item_form = ItemRecebivelForm(request.POST)
            if item_form.is_valid():
                item = item_form.save(commit=False)
                item.contrato = contrato
                item.save()
                messages.success(request, 'Novo item adicionado durante renegociação.')
    
    return render(request, 'recebiveis/renegociar.html', {'form': form, 'item_form': item_form, 'contrato': contrato})