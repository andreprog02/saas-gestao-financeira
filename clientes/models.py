from decimal import Decimal

from django.db import models
from django.core.validators import RegexValidator, MinValueValidator, MaxValueValidator
from django.utils import timezone


class Cliente(models.Model):
    nome_completo = models.CharField("Nome completo", max_length=120)

    data_nascimento = models.DateField(null=True, blank=True)

    telefone = models.CharField("Telefone", max_length=20, blank=True, default="")
    cpf = models.CharField(
        "CPF",
        max_length=14,
        unique=True,
        validators=[
            RegexValidator(
                regex=r"^\d{3}\.\d{3}\.\d{3}-\d{2}$",
                message="CPF inválido. Use o formato xxx.xxx.xxx-xx.",
            )
        ],
    )

    doc = models.CharField("Documento (RG/Doc)", max_length=30, blank=True, default="")

    # === DADOS PESSOAIS ADICIONAIS ===
    ESTADO_CIVIL_CHOICES = [
        ("SOLTEIRO", "Solteiro(a)"),
        ("CASADO", "Casado(a)"),
        ("DIVORCIADO", "Divorciado(a)"),
        ("VIUVO", "Viúvo(a)"),
        ("UNIAO_ESTAVEL", "União Estável"),
        ("SEPARADO", "Separado(a)"),
    ]

    profissao = models.CharField("Profissão", max_length=100, blank=True, default="")
    renda_mensal = models.DecimalField(
        "Renda Mensal (R$)", max_digits=12, decimal_places=2,
        null=True, blank=True,
    )
    estado_civil = models.CharField(
        "Estado Civil", max_length=15,
        choices=ESTADO_CIVIL_CHOICES, blank=True, default="",
    )
    # ==================================

    cep = models.CharField(
        "CEP",
        max_length=9,
        validators=[
            RegexValidator(
                regex=r"^\d{5}-\d{3}$",
                message="CEP inválido. Use o formato xxxxx-xxx.",
            )
        ],
    )
    logradouro = models.CharField("Logradouro", max_length=120, blank=True, default="")
    numero = models.CharField("Número", max_length=10)
    complemento = models.CharField("Complemento", max_length=60, blank=True, default="")
    bairro = models.CharField("Bairro", max_length=80, blank=True, default="")
    cidade = models.CharField("Cidade", max_length=80, blank=True, default="")
    uf = models.CharField("UF", max_length=2, blank=True, default="")

    # === NOVOS CAMPOS PARA COMISSÃO ===
    parceiro_padrao = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="indicados",
        verbose_name="Parceiro/Comissionado Padrão",
        help_text="Quem receberá comissão pelos contratos deste cliente automaticamente."
    )
    
    percentual_comissao_padrao = models.DecimalField(
        " % Comissão Padrão",
        max_digits=5, 
        decimal_places=2, 
        default=Decimal("10.00"),
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))],
        help_text="Percentual padrão de comissão para este cliente."
    )
    # ==================================

    criado_em = models.DateTimeField(default=timezone.now, editable=False)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nome_completo"]
        indexes = [
            models.Index(fields=["cpf"]),
            models.Index(fields=["nome_completo"]),
        ]

    def __str__(self):
        return f"{self.nome_completo} ({self.cpf})"

    @property
    def documentos_dict(self):
        """Retorna dict {tipo: documento_mais_recente} para acesso rápido."""
        docs = {}
        for doc in self.documentos.order_by("-criado_em"):
            if doc.tipo not in docs:
                docs[doc.tipo] = doc
        return docs


class DocumentoCliente(models.Model):
    """Documentos digitalizados do cliente."""

    TIPO_CHOICES = [
        ("CNH_FRENTE", "CNH / Identidade (Frente)"),
        ("CNH_VERSO", "CNH / Identidade (Verso)"),
        ("ESTADO_CIVIL", "Certidão de Estado Civil"),
        ("COMP_RENDA", "Comprovante de Renda"),
        ("COMP_RESIDENCIA", "Comprovante de Residência"),
        ("CONSULTA_CREDITO", "Consulta Órgãos de Crédito"),
    ]

    # Tipos que expiram após 3 meses
    TIPOS_COM_VALIDADE = ["COMP_RENDA", "COMP_RESIDENCIA"]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="documentos")
    tipo = models.CharField("Tipo", max_length=20, choices=TIPO_CHOICES)
    arquivo = models.FileField("Arquivo", upload_to="documentos_clientes/%Y/%m/")
    descricao = models.CharField("Descrição", max_length=100, blank=True, default="")

    # Mês/Ano de referência (para comprovantes)
    mes_referencia = models.IntegerField("Mês Referência", null=True, blank=True)
    ano_referencia = models.IntegerField("Ano Referência", null=True, blank=True)

    # Renda (só para comprovante de renda)
    renda_bruta = models.DecimalField("Renda Bruta", max_digits=12, decimal_places=2, null=True, blank=True)
    renda_liquida = models.DecimalField("Renda Líquida", max_digits=12, decimal_places=2, null=True, blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "Documento do Cliente"
        verbose_name_plural = "Documentos dos Clientes"

    def __str__(self):
        return f"{self.get_tipo_display()} — {self.cliente.nome_completo}"

    @property
    def vencido(self):
        """Retorna True se o documento está vencido (mais de 3 meses)."""
        if self.tipo not in self.TIPOS_COM_VALIDADE:
            return False
        if not self.mes_referencia or not self.ano_referencia:
            return True  # Sem referência = considerar vencido

        from datetime import date
        from dateutil.relativedelta import relativedelta
        data_ref = date(self.ano_referencia, self.mes_referencia, 1)
        limite = date.today() - relativedelta(months=3)
        return data_ref < limite

    @property
    def status_texto(self):
        if self.tipo in self.TIPOS_COM_VALIDADE:
            if self.vencido:
                return "Desatualizado"
            return "Vigente"
        return "OK"


class BemMovel(models.Model):
    """Veículos e bens móveis do cliente."""

    TIPO_CHOICES = [
        ("CARRO", "Carro"),
        ("MOTO", "Moto"),
        ("CAMINHAO", "Caminhão"),
        ("ONIBUS", "Ônibus"),
        ("VAN", "Van"),
        ("OUTRO", "Outro"),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="bens_moveis")
    tipo = models.CharField("Tipo", max_length=15, choices=TIPO_CHOICES)
    descricao = models.CharField("Descrição / Modelo", max_length=150, blank=True, default="")
    placa = models.CharField("Placa", max_length=10, blank=True, default="")
    renavam = models.CharField("RENAVAM", max_length=20, blank=True, default="")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "Bem Móvel"
        verbose_name_plural = "Bens Móveis"

    def __str__(self):
        return f"{self.get_tipo_display()} — {self.descricao or self.placa} ({self.cliente.nome_completo})"


class BemImovel(models.Model):
    """Imóveis do cliente."""

    TIPO_CHOICES = [
        ("CASA", "Casa"),
        ("APARTAMENTO", "Apartamento"),
        ("CHACARA", "Chácara"),
        ("SITIO", "Sítio"),
        ("FAZENDA", "Fazenda"),
        ("TERRENO", "Terreno"),
        ("COMERCIAL", "Imóvel Comercial"),
        ("OUTRO", "Outro"),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="bens_imoveis")
    tipo = models.CharField("Tipo", max_length=15, choices=TIPO_CHOICES)
    descricao = models.CharField("Descrição", max_length=150, blank=True, default="")
    matricula = models.CharField("Nº Matrícula", max_length=30, blank=True, default="")

    # Endereço completo do imóvel
    logradouro = models.CharField("Rua", max_length=120, blank=True, default="")
    numero = models.CharField("Número", max_length=10, blank=True, default="")
    bairro = models.CharField("Bairro", max_length=80, blank=True, default="")
    cidade = models.CharField("Cidade", max_length=80, blank=True, default="")
    uf = models.CharField("Estado", max_length=2, blank=True, default="")
    cep = models.CharField("CEP", max_length=9, blank=True, default="")

    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "Bem Imóvel"
        verbose_name_plural = "Bens Imóveis"

    def __str__(self):
        return f"{self.get_tipo_display()} — {self.descricao or self.logradouro} ({self.cliente.nome_completo})"

    @property
    def endereco_completo(self):
        partes = [self.logradouro]
        if self.numero:
            partes[0] += f", {self.numero}"
        if self.bairro:
            partes.append(self.bairro)
        if self.cidade and self.uf:
            partes.append(f"{self.cidade}/{self.uf}")
        if self.cep:
            partes.append(f"CEP: {self.cep}")
        return " — ".join(partes)


class DocumentoBem(models.Model):
    """Documentos vinculados a bens móveis ou imóveis."""

    bem_movel = models.ForeignKey(
        BemMovel, on_delete=models.CASCADE, null=True, blank=True, related_name="documentos"
    )
    bem_imovel = models.ForeignKey(
        BemImovel, on_delete=models.CASCADE, null=True, blank=True, related_name="documentos"
    )
    descricao = models.CharField("Descrição", max_length=100, blank=True, default="")
    arquivo = models.FileField("Arquivo", upload_to="bens_documentos/%Y/%m/")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "Documento do Bem"
        verbose_name_plural = "Documentos dos Bens"

    def __str__(self):
        ref = self.bem_movel or self.bem_imovel
        return f"Doc — {ref}"


class ContaCorrente(models.Model):
    TIPO_CHOICES = (
        ('CREDITO', 'Crédito (Entrada)'),
        ('DEBITO', 'Débito (Saída)'),
    )
    
    cliente = models.ForeignKey('Cliente', on_delete=models.CASCADE, related_name='movimentacoes')
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    descricao = models.CharField(max_length=255)
    data = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.get_tipo_display()} - R$ {self.valor} ({self.data.strftime('%d/%m/%Y')})"