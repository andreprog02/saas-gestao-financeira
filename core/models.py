from django.db import models
from django.utils import timezone
from decimal import Decimal


class ConfiguracaoEmpresa(models.Model):
    """Configurações white-label da empresa para documentos e PDFs."""

    # Cabeçalho
    nome_empresa = models.CharField("Nome / Razão Social", max_length=200, default="")
    nome_fantasia = models.CharField("Nome Fantasia", max_length=200, blank=True, default="")
    cnpj = models.CharField("CNPJ", max_length=18, blank=True, default="")
    inscricao_estadual = models.CharField("Inscrição Estadual", max_length=20, blank=True, default="")
    logo = models.ImageField("Logo", upload_to="config/logo/", blank=True, null=True)

    # Endereço
    logradouro = models.CharField("Logradouro", max_length=200, blank=True, default="")
    numero = models.CharField("Número", max_length=10, blank=True, default="")
    complemento = models.CharField("Complemento", max_length=50, blank=True, default="")
    bairro = models.CharField("Bairro", max_length=100, blank=True, default="")
    cidade = models.CharField("Cidade", max_length=100, blank=True, default="")
    uf = models.CharField("UF", max_length=2, blank=True, default="")
    cep = models.CharField("CEP", max_length=9, blank=True, default="")

    # Contato
    telefone = models.CharField("Telefone", max_length=20, blank=True, default="")
    telefone2 = models.CharField("Telefone 2", max_length=20, blank=True, default="")
    email = models.EmailField("E-mail", blank=True, default="")
    site = models.URLField("Site", blank=True, default="")

    # Rodapé dos documentos
    rodape_linha1 = models.CharField("Rodapé Linha 1", max_length=200, blank=True, default="")
    rodape_linha2 = models.CharField("Rodapé Linha 2", max_length=200, blank=True, default="")

    # Contrato
    nome_representante = models.CharField("Nome do Representante Legal", max_length=120, blank=True, default="")
    cargo_representante = models.CharField("Cargo do Representante", max_length=50, blank=True, default="Diretor")
    foro_comarca = models.CharField("Foro / Comarca", max_length=100, blank=True, default="")

    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuração da Empresa"
        verbose_name_plural = "Configurações da Empresa"

    def __str__(self):
        return self.nome_empresa or "Configuração"

    @property
    def endereco_completo(self):
        partes = []
        if self.logradouro:
            end = self.logradouro
            if self.numero:
                end += f", {self.numero}"
            if self.complemento:
                end += f" — {self.complemento}"
            partes.append(end)
        if self.bairro:
            partes.append(self.bairro)
        if self.cidade and self.uf:
            partes.append(f"{self.cidade}/{self.uf}")
        if self.cep:
            partes.append(f"CEP: {self.cep}")
        return " — ".join(partes)

    @classmethod
    def get_config(cls):
        """Retorna a configuração (singleton). Cria se não existir."""
        config, _ = cls.objects.get_or_create(pk=1, defaults={"nome_empresa": "Minha Empresa"})
        return config


class ConfiguracaoScore(models.Model):
    """Pesos e parâmetros do algoritmo de score de crédito."""

    # === PESOS (somam 100%) ===
    peso_historico = models.IntegerField("Peso Histórico Pagamento (%)", default=30)
    peso_comprometimento = models.IntegerField("Peso Comprometimento Renda (%)", default=20)
    peso_consulta_credito = models.IntegerField("Peso Consulta Crédito (%)", default=25)
    peso_garantias = models.IntegerField("Peso Garantias (%)", default=15)
    peso_perfil = models.IntegerField("Peso Perfil do Cliente (%)", default=10)

    # === PARÂMETROS ===
    # Comprometimento
    comprometimento_ideal = models.DecimalField("Comprometimento Ideal (%)", max_digits=5, decimal_places=2, default=25)
    comprometimento_maximo = models.DecimalField("Comprometimento Máximo (%)", max_digits=5, decimal_places=2, default=50)

    # Score de corte
    score_minimo_aprovacao = models.IntegerField("Score Mínimo p/ Aprovação", default=300)
    score_atencao = models.IntegerField("Score de Atenção", default=500)

    # Idade
    idade_minima_ideal = models.IntegerField("Idade Mínima Ideal", default=25)
    idade_maxima_ideal = models.IntegerField("Idade Máxima Ideal", default=60)

    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuração do Score"
        verbose_name_plural = "Configuração do Score"

    def __str__(self):
        return "Configuração do Score de Crédito"

    @classmethod
    def get_config(cls):
        config, _ = cls.objects.get_or_create(pk=1)
        return config
