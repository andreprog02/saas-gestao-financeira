from decimal import Decimal

from django.db import models
from django.core.validators import RegexValidator, MinValueValidator, MaxValueValidator
from django.utils import timezone


class Cliente(models.Model):
    nome_completo = models.CharField("Nome completo", max_length=120)

    data_nascimento = models.DateField(null=True, blank=True)

    telefone = models.CharField(
        "Telefone",
        max_length=16,
        validators=[
            RegexValidator(
                regex=r"^\(\d{2}\)\s\d\s\d{4}-\d{4}$",
                message="Telefone inválido. Use o formato (xx) x xxxx-xxxx.",
            )
        ],
    )

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