from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages

from .models import ConfiguracaoEmpresa, ConfiguracaoScore


@login_required
def configuracoes(request):
    """Tela de configuração white-label da empresa."""
    config = ConfiguracaoEmpresa.get_config()
    score_cfg = ConfiguracaoScore.get_config()

    if request.method == "POST":
        secao = request.POST.get("secao", "empresa")

        if secao == "score":
            score_cfg.peso_historico = int(request.POST.get("peso_historico", 30))
            score_cfg.peso_comprometimento = int(request.POST.get("peso_comprometimento", 20))
            score_cfg.peso_consulta_credito = int(request.POST.get("peso_consulta_credito", 25))
            score_cfg.peso_garantias = int(request.POST.get("peso_garantias", 15))
            score_cfg.peso_perfil = int(request.POST.get("peso_perfil", 10))
            score_cfg.comprometimento_ideal = request.POST.get("comprometimento_ideal", 25)
            score_cfg.comprometimento_maximo = request.POST.get("comprometimento_maximo", 50)
            score_cfg.score_minimo_aprovacao = int(request.POST.get("score_minimo_aprovacao", 300))
            score_cfg.score_atencao = int(request.POST.get("score_atencao", 500))
            score_cfg.save()
            messages.success(request, "Configurações do Score salvas.")
            return redirect("core:configuracoes")

        # Empresa
        config.nome_empresa = request.POST.get("nome_empresa", config.nome_empresa)
        config.nome_fantasia = request.POST.get("nome_fantasia", "")
        config.cnpj = request.POST.get("cnpj", "")
        config.inscricao_estadual = request.POST.get("inscricao_estadual", "")

        config.logradouro = request.POST.get("logradouro", "")
        config.numero = request.POST.get("numero", "")
        config.complemento = request.POST.get("complemento", "")
        config.bairro = request.POST.get("bairro", "")
        config.cidade = request.POST.get("cidade", "")
        config.uf = request.POST.get("uf", "")
        config.cep = request.POST.get("cep", "")

        config.telefone = request.POST.get("telefone", "")
        config.telefone2 = request.POST.get("telefone2", "")
        config.email = request.POST.get("email", "")
        config.site = request.POST.get("site", "")

        config.rodape_linha1 = request.POST.get("rodape_linha1", "")
        config.rodape_linha2 = request.POST.get("rodape_linha2", "")

        config.nome_representante = request.POST.get("nome_representante", "")
        config.cargo_representante = request.POST.get("cargo_representante", "")
        config.foro_comarca = request.POST.get("foro_comarca", "")

        logo = request.FILES.get("logo")
        if logo:
            config.logo = logo

        config.save()
        messages.success(request, "Configurações salvas com sucesso.")
        return redirect("core:configuracoes")

    return render(request, "core/configuracoes.html", {"config": config, "score_cfg": score_cfg})
