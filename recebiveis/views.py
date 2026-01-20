from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from .models import ContratoRecebivel
from .forms import ContratoRecebivelForm, ItemRecebivelForm, AtivacaoForm, RenegociacaoForm
from .services import registrar_financeiro  # Para integração com livro caixa
from .models import ItemRecebivel

def lista_contratos(request):
    contratos = ContratoRecebivel.objects.all()
    return render(request, 'recebiveis/lista.html', {'contratos': contratos})

def criar_contrato(request):
    if request.method == 'POST':
        form = ContratoRecebivelForm(request.POST)
        if form.is_valid():
            contrato = form.save()
            return redirect('adicionar_item', contrato_id=contrato.id)
    else:
        form = ContratoRecebivelForm()
    return render(request, 'recebiveis/criar.html', {'form': form})

def adicionar_item(request, contrato_id):
    contrato = get_object_or_404(ContratoRecebivel, id=contrato_id)
    if request.method == 'POST':
        form = ItemRecebivelForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.contrato = contrato
            item.save()
            messages.success(request, 'Item adicionado. Adicione mais ou simule.')
            return redirect('adicionar_item', contrato_id=contrato.id)
    else:
        form = ItemRecebivelForm()
    return render(request, 'recebiveis/adicionar_item.html', {'form': form, 'contrato': contrato})

def simular_contrato(request, contrato_id):
    contrato = get_object_or_404(ContratoRecebivel, id=contrato_id)
    contrato.calcular_valores()  # Garante atualização
    return render(request, 'recebiveis/simulacao.html', {'contrato': contrato})

def editar_item(request, item_id):
    item = get_object_or_404(ItemRecebivel, id=item_id)
    contrato_id = item.contrato.id  # Para redirecionar de volta
    if request.method == 'POST':
        form = ItemRecebivelForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, 'Item editado com sucesso.')
            return redirect('adicionar_item', contrato_id=contrato_id)
    else:
        form = ItemRecebivelForm(instance=item)
    # Note: Como o form está no modal, essa view é para processar POST; o GET não é necessário para renderizar form separado

def excluir_item(request, item_id):
    item = get_object_or_404(ItemRecebivel, id=item_id)
    contrato_id = item.contrato.id  # Para redirecionar de volta
    if request.method == 'POST':
        item.delete()
        messages.success(request, 'Item excluído com sucesso.')
        return redirect('adicionar_item', contrato_id=contrato_id)

def ativar_contrato(request, contrato_id):
    contrato = get_object_or_404(ContratoRecebivel, id=contrato_id)
    if contrato.status != 'simulado':
        messages.error(request, 'Contrato já ativado ou renegociado.')
        return redirect('lista_contratos')
    if request.method == 'POST':
        form = AtivacaoForm(request.POST)
        if form.is_valid():
            if form.cleaned_data['senha'] == 'senha123':  # Altere para produção
                contrato.status = 'ativo'
                contrato.data_ativacao = timezone.now()
                contrato.save()  # Gera ID com prefixo REC
                registrar_financeiro(contrato)  # Registra no livro caixa
                messages.success(request, f'Contrato {contrato.contrato_id} ativado.')
                return redirect('lista_contratos')
            else:
                messages.error(request, 'Senha incorreta.')
    else:
        form = AtivacaoForm()
    return render(request, 'recebiveis/ativar.html', {'form': form, 'contrato': contrato})
# Create your views here.
