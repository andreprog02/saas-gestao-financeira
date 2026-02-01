from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .forms import ClienteForm
from .models import Cliente


def clientes_lista(request):
    q = (request.GET.get("q") or "").strip()

    qs = Cliente.objects.all()
    if q:
        qs = qs.filter(Q(nome_completo__icontains=q) | Q(cpf__icontains=q))

    qs = qs.order_by("nome_completo")

    paginator = Paginator(qs, 10)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, "clientes/lista.html", {"page_obj": page_obj, "q": q})


def clientes_novo(request):
    if request.method == "POST":
        form = ClienteForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Cliente cadastrado com sucesso.")
            return redirect("clientes:lista")
    else:
        form = ClienteForm()

    return render(request, "clientes/form.html", {"form": form, "titulo": "Novo Cliente"})


def clientes_editar(request, cliente_id: int):
    cliente = get_object_or_404(Cliente, id=cliente_id)

    if request.method == "POST":
        form = ClienteForm(request.POST, instance=cliente)
        if form.is_valid():
            form.save()
            messages.success(request, "Cliente atualizado com sucesso.")
            return redirect("clientes:detalhe", cliente_id=cliente.id)
    else:
        form = ClienteForm(instance=cliente)

    return render(request, "clientes/form.html", {"form": form, "titulo": "Editar Cliente"})


def clientes_detalhe(request, cliente_id: int):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    return render(request, "clientes/detalhe.html", {"cliente": cliente})


def clientes_excluir(request, cliente_id: int):
    cliente = get_object_or_404(Cliente, id=cliente_id)

    if request.method == "POST":
        cliente.delete()
        messages.success(request, "Cliente excluído.")
        return redirect("clientes:lista")

    return render(request, "clientes/excluir.html", {"cliente": cliente})
from django.shortcuts import render

# Create your views here.
# Importe os modelos da Conta (AJUSTE AQUI)
from contas.models import ContaCorrente

def lista(request):
    q = request.GET.get('q')
    if q:
        clientes = Cliente.objects.filter(nome_completo__icontains=q) | Cliente.objects.filter(cpf__icontains=q)
    else:
        clientes = Cliente.objects.all()
    return render(request, 'clientes/lista.html', {'clientes': clientes})

def detalhe(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    
    # === LÓGICA DA CONTA CORRENTE ===
    conta = None
    extrato = []
    
    try:
        # Tenta acessar a conta via relacionamento reverso (OneToOne)
        conta = cliente.conta_corrente
        # Busca as últimas 50 movimentações
        extrato = conta.movimentacoes.all().order_by('-data')[:50]
    except Cliente.conta_corrente.RelatedObjectDoesNotExist:
        # Se não tiver conta, cria uma vazia para não quebrar a tela (Opcional)
        # conta = ContaCorrente.objects.create(cliente=cliente)
        pass

    return render(request, 'clientes/detalhe.html', {
        'cliente': cliente,
        'conta': conta,
        'extrato': extrato
    })

def novo(request):
    if request.method == 'POST':
        form = ClienteForm(request.POST)
        if form.is_valid():
            cliente = form.save()
            messages.success(request, 'Cliente cadastrado com sucesso!')
            return redirect('clientes:detalhe', cliente_id=cliente.id)
    else:
        form = ClienteForm()
    return render(request, 'clientes/form.html', {'form': form})

def editar(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    if request.method == 'POST':
        form = ClienteForm(request.POST, instance=cliente)
        if form.is_valid():
            form.save()
            messages.success(request, 'Cliente atualizado com sucesso!')
            return redirect('clientes:detalhe', cliente_id=cliente.id)
    else:
        form = ClienteForm(instance=cliente)
    return render(request, 'clientes/form.html', {'form': form})

def excluir(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    if request.method == 'POST':
        cliente.delete()
        messages.success(request, 'Cliente excluído!')
        return redirect('clientes:lista')
    return render(request, 'clientes/excluir.html', {'cliente': cliente})