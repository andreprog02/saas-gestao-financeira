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
