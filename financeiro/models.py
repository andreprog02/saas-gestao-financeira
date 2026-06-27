from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.conf import settings
from django.db.models import Sum


class CodigoOperacao(models.Model):
    """Códigos de operação do caixa."""
    codigo = models.CharField(max_length=5, unique=True)
    descricao = models.CharField(max_length=100)
    tipo = models.CharField(max_length=10, choices=[('E', 'Entrada'), ('S', 'Saída')])
    afeta_caixa_fisico = models.BooleanField("Afeta Caixa Físico?", default=True)
    exige_cliente = models.BooleanField("Exige Cliente?", default=False)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["codigo"]

    def __str__(self):
        fisico = "💵" if self.afeta_caixa_fisico else "🔄"
        return f"{self.codigo} — {self.descricao} {fisico}"


class Caixa(models.Model):
    """Abertura e fechamento de caixa diário."""
    STATUS_CHOICES = [
        ("ABERTO", "Aberto"),
        ("FECHADO", "Fechado"),
    ]

    data = models.DateField("Data", unique=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="ABERTO")

    saldo_abertura = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    aberto_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="caixas_abertos",
    )
    aberto_em = models.DateTimeField(null=True, blank=True)

    saldo_sistema = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    saldo_conferido = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    diferenca = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    observacoes_fechamento = models.TextField(blank=True, default="")
    fechado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="caixas_fechados",
    )
    fechado_em = models.DateTimeField(null=True, blank=True)

    contagem_cedulas = models.JSONField(default=dict, blank=True)
    contagem_moedas = models.JSONField(default=dict, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data"]

    def __str__(self):
        return f"Caixa {self.data.strftime('%d/%m/%Y')} — {self.get_status_display()}"

    @property
    def diferenca_cor(self):
        if self.diferenca > 0:
            return "success"
        elif self.diferenca < 0:
            return "danger"
        return "secondary"

    @property
    def total_entradas_fisico(self):
        return self.movimentacoes.filter(
            afetou_caixa_fisico=True, valor__gt=0, estornado=False,
        ).aggregate(s=Sum("valor"))["s"] or Decimal("0.00")

    @property
    def total_saidas_fisico(self):
        return abs(self.movimentacoes.filter(
            afetou_caixa_fisico=True, valor__lt=0, estornado=False,
        ).aggregate(s=Sum("valor"))["s"] or Decimal("0.00"))

    @property
    def saldo_fisico_calculado(self):
        mov = self.movimentacoes.filter(
            afetou_caixa_fisico=True, estornado=False,
        ).aggregate(s=Sum("valor"))["s"] or Decimal("0.00")
        return self.saldo_abertura + mov


class MovimentacaoCaixa(models.Model):
    """Cada lançamento do caixa — físico ou eletrônico."""
    caixa = models.ForeignKey(Caixa, on_delete=models.CASCADE, related_name="movimentacoes")
    codigo_operacao = models.ForeignKey(CodigoOperacao, on_delete=models.PROTECT)

    valor = models.DecimalField(max_digits=12, decimal_places=2)
    descricao = models.CharField(max_length=255)
    afetou_caixa_fisico = models.BooleanField("Afetou Caixa Físico", default=True)

    # Cliente (para operações de C/C)
    cliente = models.ForeignKey(
        "clientes.Cliente", on_delete=models.SET_NULL, null=True, blank=True,
    )
    # Empréstimo vinculado
    emprestimo = models.ForeignKey(
        "emprestimos.Emprestimo", on_delete=models.SET_NULL, null=True, blank=True,
    )

    # Auditoria
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    data_hora = models.DateTimeField(default=timezone.now)
    numero_autenticacao = models.CharField(max_length=20, blank=True, default="")

    # Estorno
    estornado = models.BooleanField(default=False)
    estornado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="estornos_caixa",
    )
    estornado_em = models.DateTimeField(null=True, blank=True)
    motivo_estorno = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        ordering = ["-data_hora"]

    def __str__(self):
        est = " [ESTORNADO]" if self.estornado else ""
        return f"{self.codigo_operacao.codigo} — {self.descricao} — R$ {self.valor}{est}"

    def save(self, *args, **kwargs):
        # Saídas sempre negativas
        if self.codigo_operacao.tipo == "S" and self.valor > 0:
            self.valor = self.valor * -1
        # Herda flag de físico do código
        if not self.pk:
            self.afetou_caixa_fisico = self.codigo_operacao.afeta_caixa_fisico
        if not self.numero_autenticacao:
            import uuid
            self.numero_autenticacao = str(uuid.uuid4())[:8].upper()
        super().save(*args, **kwargs)


# Manter compatibilidade com o Transacao existente
class Transacao(models.Model):
    TIPO_CHOICES = [
        ('EMPRESTIMO_SAIDA', 'Empréstimo (Saída)'),
        ('PAGAMENTO_ENTRADA', 'Pagamento Parc. (Entrada)'),
        ('ANTECIPACAO', 'Antecipação de Recebíveis'),
        ('DESPESA', 'Despesa Operacional'),
        ('APORTE', 'Aporte de Capital'),
        ('RETIRADA', 'Retirada de Lucro'),
        ('SAQUE_CC', 'Saque Conta Corrente'),
        ('DEPOSITO_CC', 'Depósito Conta Corrente'),
        ('OUTROS', 'Outros'),
    ]

    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    descricao = models.CharField(max_length=255)
    data = models.DateTimeField(default=timezone.now)
    emprestimo = models.ForeignKey("emprestimos.Emprestimo", on_delete=models.SET_NULL, null=True, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    ip_origem = models.GenericIPAddressField(null=True, blank=True)
    codigo_autenticacao = models.CharField(max_length=64, blank=True, null=True)
    transacao_original = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True)
    codigo_operacao = models.ForeignKey(CodigoOperacao, on_delete=models.SET_NULL, null=True, blank=True)

    def save(self, *args, **kwargs):
        tipos_saida = ['EMPRESTIMO_SAIDA', 'DESPESA', 'RETIRADA', 'SAQUE_CC', 'ANTECIPACAO']
        if self.tipo in tipos_saida and self.valor > 0:
            self.valor = self.valor * -1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.data.strftime('%d/%m')} — {self.descricao} (R$ {self.valor})"


def calcular_saldo_atual():
    total = Transacao.objects.aggregate(saldo=Sum('valor'))['saldo']
    return total or Decimal("0.00")


# ==============================================================================
# TESOURARIA
# ==============================================================================

class Tesouraria(models.Model):
    """Cofre central — distribui e recebe dinheiro dos caixas."""
    data = models.DateField("Data", unique=True)
    saldo_abertura = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    saldo_atual = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    STATUS_CHOICES = [("ABERTA", "Aberta"), ("FECHADA", "Fechada")]
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="ABERTA")

    aberto_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="tesourarias_abertas")
    aberto_em = models.DateTimeField(null=True, blank=True)
    fechado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="tesourarias_fechadas")
    fechado_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data"]

    def __str__(self):
        return f"Tesouraria {self.data.strftime('%d/%m/%Y')} — R$ {self.saldo_atual}"

    def recalcular_saldo(self):
        total = self.movimentacoes.filter(estornado=False).aggregate(s=Sum("valor"))["s"] or Decimal("0")
        self.saldo_atual = self.saldo_abertura + total
        self.save(update_fields=["saldo_atual"])


class MovimentacaoTesouraria(models.Model):
    """Cada movimentação da tesouraria."""
    TIPO_CHOICES = [
        ("APORTE", "Aporte / Entrada"),
        ("RETIRADA", "Retirada"),
        ("ENVIO_CAIXA", "Envio para Caixa"),
        ("RECEB_CAIXA", "Recebimento do Caixa"),
        ("AJUSTE", "Ajuste"),
    ]

    tesouraria = models.ForeignKey(Tesouraria, on_delete=models.CASCADE, related_name="movimentacoes")
    tipo = models.CharField(max_length=15, choices=TIPO_CHOICES)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    descricao = models.CharField(max_length=255, blank=True, default="")
    caixa_destino = models.ForeignKey(Caixa, on_delete=models.SET_NULL, null=True, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    data_hora = models.DateTimeField(default=timezone.now)
    estornado = models.BooleanField(default=False)
    estornado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="estornos_tesouraria")
    estornado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-data_hora"]

    def __str__(self):
        return f"{self.get_tipo_display()} — R$ {self.valor}"

    def save(self, *args, **kwargs):
        if self.tipo in ["RETIRADA", "ENVIO_CAIXA"] and self.valor > 0:
            self.valor = self.valor * -1
        super().save(*args, **kwargs)


# ==============================================================================
# CUSTÓDIA DE CHEQUES
# ==============================================================================

class ChequeCustodia(models.Model):
    """Cheques recebidos em custódia."""
    STATUS_CHOICES = [
        ("EM_CUSTODIA", "Em Custódia"),
        ("ENVIADO_COMPENSACAO", "Enviado p/ Compensação"),
        ("COMPENSADO", "Compensado"),
        ("DEVOLVIDO", "Devolvido"),
    ]

    banco = models.CharField("Banco", max_length=50)
    agencia = models.CharField("Agência", max_length=10)
    conta = models.CharField("Conta", max_length=20)
    numero_cheque = models.CharField("Nº Cheque", max_length=20)
    valor = models.DecimalField("Valor (R$)", max_digits=12, decimal_places=2)
    vencimento = models.DateField("Vencimento / Bom Para")
    emitente = models.CharField("Emitente", max_length=150)
    cpf_emitente = models.CharField("CPF Emitente", max_length=18, blank=True, default="")

    cliente = models.ForeignKey("clientes.Cliente", on_delete=models.SET_NULL, null=True, blank=True)
    emprestimo = models.ForeignKey("emprestimos.Emprestimo", on_delete=models.SET_NULL, null=True, blank=True)
    cheque_garantia = models.ForeignKey("emprestimos.ChequeGarantia", on_delete=models.SET_NULL, null=True, blank=True)

    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default="EM_CUSTODIA")
    data_entrada = models.DateField("Data Entrada", default=timezone.now)
    data_envio_compensacao = models.DateField(null=True, blank=True)
    data_compensacao = models.DateField(null=True, blank=True)
    data_devolucao = models.DateField(null=True, blank=True)
    motivo_devolucao = models.CharField(max_length=200, blank=True, default="")

    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["vencimento"]

    def __str__(self):
        return f"Ch. {self.numero_cheque} — {self.banco} — R$ {self.valor} — {self.get_status_display()}"

    @property
    def dias_ate_vencimento(self):
        from datetime import date
        return (self.vencimento - date.today()).days

    @property
    def vencido(self):
        from datetime import date
        return self.vencimento < date.today() and self.status == "EM_CUSTODIA"

    @property
    def status_cor(self):
        return {"EM_CUSTODIA": "primary", "ENVIADO_COMPENSACAO": "warning",
                "COMPENSADO": "success", "DEVOLVIDO": "danger"}.get(self.status, "secondary")