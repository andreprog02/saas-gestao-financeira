from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

import csv
import io
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required

from .forms import ClienteForm
from .models import Cliente, DocumentoCliente, BemMovel, BemImovel, DocumentoBem, ConsultaCredito, RestricaoCredito
from contas.models import ContaCorrente
from core.validators import validar_upload

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

    documentos = cliente.documentos.all()
    tipos_doc = DocumentoCliente.TIPO_CHOICES
    bens_moveis = cliente.bens_moveis.all()
    bens_imoveis = cliente.bens_imoveis.all()
    consultas_credito = cliente.consultas_credito.prefetch_related("restricoes", "documento").all()

    return render(request, "clientes/form.html", {
        "form": form,
        "titulo": "Editar Cliente",
        "cliente": cliente,
        "documentos": documentos,
        "tipos_doc": tipos_doc,
        "bens_moveis": bens_moveis,
        "bens_imoveis": bens_imoveis,
        "tipos_movel": BemMovel.TIPO_CHOICES,
        "tipos_imovel": BemImovel.TIPO_CHOICES,
        "consultas_credito": consultas_credito,
    })


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

# ==============================================================================
# UPLOAD DE DOCUMENTOS
# ==============================================================================

@login_required
def upload_documento(request, cliente_id):
    """Upload de documento do cliente."""
    cliente = get_object_or_404(Cliente, id=cliente_id)

    if request.method == "POST":
        tipo = request.POST.get("tipo", "")
        arquivo = request.FILES.get("arquivo")
        mes_ref = request.POST.get("mes_referencia", "")
        ano_ref = request.POST.get("ano_referencia", "")
        descricao = request.POST.get("descricao", "")

        if not tipo or not arquivo:
            messages.error(request, "Selecione o tipo e o arquivo.")
            return redirect("clientes:editar", cliente_id=cliente.id)

        try:
            validar_upload(arquivo)
        except Exception as e:
            messages.error(request, str(e))
            return redirect("clientes:editar", cliente_id=cliente.id)

        doc = DocumentoCliente(
            cliente=cliente,
            tipo=tipo,
            arquivo=arquivo,
            descricao=descricao,
        )

        if mes_ref and ano_ref:
            doc.mes_referencia = int(mes_ref)
            doc.ano_referencia = int(ano_ref)

        # Renda bruta e líquida (comprovante de renda)
        def parse_brl(val):
            if not val or not val.strip():
                return None
            from decimal import Decimal
            limpo = val.replace("R$", "").replace(" ", "").strip()
            if "," in limpo and "." in limpo:
                limpo = limpo.replace(".", "").replace(",", ".")
            elif "," in limpo:
                limpo = limpo.replace(",", ".")
            return Decimal(limpo)

        renda_bruta = request.POST.get("renda_bruta", "")
        renda_liquida = request.POST.get("renda_liquida", "")
        if renda_bruta:
            doc.renda_bruta = parse_brl(renda_bruta)
        if renda_liquida:
            doc.renda_liquida = parse_brl(renda_liquida)

        doc.save()
        messages.success(request, f"Documento '{doc.get_tipo_display()}' enviado com sucesso.")

    return redirect("clientes:editar", cliente_id=cliente.id)


@login_required
def excluir_documento(request, doc_id):
    """Exclui um documento do cliente."""
    doc = get_object_or_404(DocumentoCliente, id=doc_id)
    cliente_id = doc.cliente_id
    doc.arquivo.delete(save=False)
    doc.delete()
    messages.info(request, "Documento excluído.")
    return redirect("clientes:editar", cliente_id=cliente_id)


# ==============================================================================
# BENS MÓVEIS
# ==============================================================================

@login_required
def adicionar_bem_movel(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    if request.method == "POST":
        bem = BemMovel.objects.create(
            cliente=cliente,
            tipo=request.POST.get("tipo", "OUTRO"),
            descricao=request.POST.get("descricao", "").strip(),
            placa=request.POST.get("placa", "").strip().upper(),
            renavam=request.POST.get("renavam", "").strip(),
        )
        # Upload de documentos
        for f in request.FILES.getlist("documentos"):
            DocumentoBem.objects.create(bem_movel=bem, arquivo=f, descricao=f.name)
        messages.success(request, f"Bem móvel '{bem.get_tipo_display()}' adicionado.")
    return redirect("clientes:editar", cliente_id=cliente.id)


@login_required
def excluir_bem_movel(request, bem_id):
    bem = get_object_or_404(BemMovel, id=bem_id)
    cliente_id = bem.cliente_id
    for doc in bem.documentos.all():
        doc.arquivo.delete(save=False)
    bem.delete()
    messages.info(request, "Bem móvel excluído.")
    return redirect("clientes:editar", cliente_id=cliente_id)


@login_required
def upload_doc_movel(request, bem_id):
    bem = get_object_or_404(BemMovel, id=bem_id)
    if request.method == "POST":
        arquivo = request.FILES.get("arquivo")
        if arquivo:
            DocumentoBem.objects.create(
                bem_movel=bem, arquivo=arquivo,
                descricao=request.POST.get("descricao", arquivo.name),
            )
            messages.success(request, "Documento adicionado ao bem móvel.")
    return redirect("clientes:editar", cliente_id=bem.cliente_id)


# ==============================================================================
# BENS IMÓVEIS
# ==============================================================================

@login_required
def adicionar_bem_imovel(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    if request.method == "POST":
        bem = BemImovel.objects.create(
            cliente=cliente,
            tipo=request.POST.get("tipo", "OUTRO"),
            descricao=request.POST.get("descricao", "").strip(),
            matricula=request.POST.get("matricula", "").strip(),
            logradouro=request.POST.get("logradouro", "").strip(),
            numero=request.POST.get("numero_imovel", "").strip(),
            bairro=request.POST.get("bairro", "").strip(),
            cidade=request.POST.get("cidade", "").strip(),
            uf=request.POST.get("uf", "").strip().upper(),
            cep=request.POST.get("cep", "").strip(),
        )
        for f in request.FILES.getlist("documentos"):
            DocumentoBem.objects.create(bem_imovel=bem, arquivo=f, descricao=f.name)
        messages.success(request, f"Bem imóvel '{bem.get_tipo_display()}' adicionado.")
    return redirect("clientes:editar", cliente_id=cliente.id)


@login_required
def excluir_bem_imovel(request, bem_id):
    bem = get_object_or_404(BemImovel, id=bem_id)
    cliente_id = bem.cliente_id
    for doc in bem.documentos.all():
        doc.arquivo.delete(save=False)
    bem.delete()
    messages.info(request, "Bem imóvel excluído.")
    return redirect("clientes:editar", cliente_id=cliente_id)


@login_required
def upload_doc_imovel(request, bem_id):
    bem = get_object_or_404(BemImovel, id=bem_id)
    if request.method == "POST":
        arquivo = request.FILES.get("arquivo")
        if arquivo:
            DocumentoBem.objects.create(
                bem_imovel=bem, arquivo=arquivo,
                descricao=request.POST.get("descricao", arquivo.name),
            )
            messages.success(request, "Documento adicionado ao bem imóvel.")
    return redirect("clientes:editar", cliente_id=bem.cliente_id)


@login_required
def excluir_doc_bem(request, doc_id):
    doc = get_object_or_404(DocumentoBem, id=doc_id)
    cliente_id = (doc.bem_movel.cliente_id if doc.bem_movel else doc.bem_imovel.cliente_id)
    doc.arquivo.delete(save=False)
    doc.delete()
    messages.info(request, "Documento excluído.")
    return redirect("clientes:editar", cliente_id=cliente_id)


# ==============================================================================
# CONSULTA DE CRÉDITO
# ==============================================================================

@login_required
def adicionar_consulta_credito(request, cliente_id):
    """Cadastra consulta de crédito com status e restrições."""
    from decimal import Decimal, InvalidOperation

    cliente = get_object_or_404(Cliente, id=cliente_id)

    if request.method == "POST":
        status = request.POST.get("status_consulta", "NADA_CONSTA")
        observacoes = request.POST.get("observacoes_consulta", "")

        # Upload do documento vinculado
        arquivo = request.FILES.get("arquivo_consulta")
        doc = None
        if arquivo:
            doc = DocumentoCliente.objects.create(
                cliente=cliente,
                tipo="CONSULTA_CREDITO",
                arquivo=arquivo,
            )

        consulta = ConsultaCredito.objects.create(
            cliente=cliente,
            documento=doc,
            status=status,
            observacoes=observacoes,
            registrado_por=request.user,
        )

        # Alertas (status ALERTA)
        if status == "ALERTA":
            cnpj_al = request.POST.get("alerta_cnpj", "").strip()
            desc_al = request.POST.get("alerta_descricao", "").strip()
            valor_al = request.POST.get("alerta_valor", "")
            if cnpj_al or desc_al:
                RestricaoCredito.objects.create(
                    consulta=consulta,
                    cnpj_credor=cnpj_al,
                    descricao=desc_al,
                    valor=_parse_brl(valor_al),
                )

        # Restrições (status COM_RESTRICAO)
        if status == "COM_RESTRICAO":
            idx = 0
            while True:
                cnpj = request.POST.get(f"rest_cnpj_{idx}", "").strip()
                nome = request.POST.get(f"rest_nome_{idx}", "").strip()
                valor = request.POST.get(f"rest_valor_{idx}", "")
                desc = request.POST.get(f"rest_desc_{idx}", "").strip()
                if not cnpj and not nome and not valor:
                    break
                RestricaoCredito.objects.create(
                    consulta=consulta,
                    cnpj_credor=cnpj,
                    nome_credor=nome,
                    valor=_parse_brl(valor),
                    descricao=desc,
                )
                idx += 1

        cor = {"NADA_CONSTA": "success", "ALERTA": "warning", "COM_RESTRICAO": "danger"}.get(status, "info")
        messages.success(request, f"Consulta de crédito registrada: {consulta.get_status_display()}")

    return redirect("clientes:editar", cliente_id=cliente.id)


def _parse_brl(valor_str):
    """Converte BRL string para Decimal."""
    from decimal import Decimal
    if not valor_str or not valor_str.strip():
        return Decimal("0.00")
    limpo = valor_str.replace("R$", "").replace(" ", "").strip()
    if "," in limpo and "." in limpo:
        limpo = limpo.replace(".", "").replace(",", ".")
    elif "," in limpo:
        limpo = limpo.replace(",", ".")
    try:
        return Decimal(limpo)
    except Exception:
        return Decimal("0.00")


@login_required
def excluir_consulta_credito(request, consulta_id):
    """Exclui uma consulta de crédito."""
    consulta = get_object_or_404(ConsultaCredito, id=consulta_id)
    cliente_id = consulta.cliente_id
    consulta.delete()
    messages.info(request, "Consulta de crédito excluída.")
    return redirect("clientes:editar", cliente_id=cliente_id)
