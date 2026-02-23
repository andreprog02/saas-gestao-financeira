from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

import csv
import io
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required

from .forms import ClienteForm
from .models import Cliente
from contas.models import ContaCorrente

@login_required
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


@login_required
def novo_cliente(request):
    if request.method == 'POST':
        form = ClienteForm(request.POST)
        if form.is_valid():
            form.save()
            # --- CORREÇÃO FEITA AQUI ---
            return redirect('clientes:cliente_list') 
        else:
            print(form.errors)
    else:
        form = ClienteForm()
    
    return render(request, 'clientes/form.html', {'form': form, 'titulo': 'Novo Cliente'})


@login_required
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


@login_required
def clientes_detalhe(request, cliente_id: int):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    
    conta = None
    extrato = []
    
    try:
        conta = cliente.conta_corrente
        extrato = conta.movimentacoes.all().order_by('-data')[:50]
    except Exception:
        pass

    return render(request, "clientes/detalhe.html", {
        "cliente": cliente,
        "conta": conta,
        "extrato": extrato
    })


@login_required
def clientes_excluir(request, cliente_id: int):
    cliente = get_object_or_404(Cliente, id=cliente_id)

    if request.method == "POST":
        cliente.delete()
        messages.success(request, "Cliente excluído.")
        # --- CORREÇÃO FEITA AQUI TAMBÉM ---
        return redirect("clientes:cliente_list")

    return render(request, "clientes/excluir.html", {"cliente": cliente})


@login_required
def exportar_clientes_csv(request):
    """Gera um CSV com todos os dados dos clientes"""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="clientes_backup.csv"'
    
    response.write(u'\ufeff'.encode('utf8')) 
    
    writer = csv.writer(response, delimiter=';')
    
    writer.writerow([
        'nome_completo', 'cpf', 'telefone', 'data_nascimento', 
        'cep', 'logradouro', 'numero', 'complemento', 
        'bairro', 'cidade', 'uf', 'doc'
    ])
    
    for cliente in Cliente.objects.all():
        writer.writerow([
            cliente.nome_completo,
            cliente.cpf,
            cliente.telefone,
            cliente.data_nascimento.strftime('%Y-%m-%d') if cliente.data_nascimento else '',
            cliente.cep,
            cliente.logradouro,
            cliente.numero,
            cliente.complemento,
            cliente.bairro,
            cliente.cidade,
            cliente.uf,
            cliente.doc
        ])
        
    return response


@login_required
def importar_clientes_csv(request):
    """Lê um CSV e cria os clientes no banco"""
    if request.method == "POST" and request.FILES.get('arquivo_csv'):
        csv_file = request.FILES['arquivo_csv']
        
        if not csv_file.name.endswith('.csv'):
            messages.error(request, 'Por favor, envie um arquivo .csv')
            return redirect('clientes:importar_clientes')
            
        data_set = csv_file.read().decode('utf-8-sig')
        io_string = io.StringIO(data_set)
        
        next(io_string) 
        
        sucesso = 0
        erros = 0
        
        for column in csv.reader(io_string, delimiter=';'):
            try:
                if Cliente.objects.filter(cpf=column[1]).exists():
                    erros += 1
                    continue

                data_nasc = column[3] if column[3] else None
                
                Cliente.objects.create(
                    nome_completo=column[0],
                    cpf=column[1],
                    telefone=column[2],
                    data_nascimento=data_nasc,
                    cep=column[4],
                    logradouro=column[5],
                    numero=column[6],
                    complemento=column[7],
                    bairro=column[8],
                    cidade=column[9],
                    uf=column[10],
                    doc=column[11]
                )
                sucesso += 1
            except Exception as e:
                print(f"Erro na linha: {e}")
                erros += 1
                
        messages.success(request, f"Importação concluída! {sucesso} criados, {erros} ignorados/erros.")
        return redirect('clientes:cliente_list')

    return render(request, 'clientes/importar.html')