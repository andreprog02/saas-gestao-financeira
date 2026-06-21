from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone
from financeiro.models import Transacao


class ContaBancaria(models.Model):
    """Conta bancária da empresa para conciliação."""

    BANCOS_COMUNS = [
        ("001", "Banco do Brasil"),
        ("033", "Santander"),
        ("033", "XP Investimentos"),
        ("104", "Caixa Econômica"),
        ("237", "Bradesco"),
        ("341", "Itaú"),
        ("077", "Inter"),
        ("260", "Nubank"),
        ("336", "C6 Bank"),
        ("756", "Sicoob"),
        ("748", "Sicredi"),

        ("999", "Outro"),
    ]

    nome = models.CharField("Nome / Apelido", max_length=100, help_text="Ex: Conta Principal Itaú")
    banco = models.CharField("Banco", max_length=5, choices=BANCOS_COMUNS)
    agencia = models.CharField("Agência", max_length=10, blank=True, default="")
    conta = models.CharField("Conta", max_length=20, blank=True, default="")
    tipo = models.CharField(
        "Tipo",
        max_length=10,
        choices=[("CC", "Conta Corrente"), ("CP", "Conta Poupança")],
        default="CC",
    )
    saldo_inicial = models.DecimalField("Saldo Inicial", max_digits=12, decimal_places=2, default=Decimal("0.00"))
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Conta Bancária"
        verbose_name_plural = "Contas Bancárias"
        ordering = ["nome"]

    def __str__(self):
        banco_nome = dict(self.BANCOS_COMUNS).get(self.banco, self.banco)
        return f"{self.nome} ({banco_nome})"

    @property
    def saldo_calculado(self):
        """Saldo inicial + soma de todos os lançamentos dos extratos desta conta."""
        from django.db.models import Sum
        total = LancamentoExtrato.objects.filter(
            extrato__conta=self,
            status__in=["CONCILIADO", "MANUAL", "CRIADO", "IGNORADO"]
        ).aggregate(s=Sum("valor"))["s"] or Decimal("0.00")
        return self.saldo_inicial + total


class ExtratoImportado(models.Model):
    """Registro de cada arquivo de extrato importado."""

    STATUS_CHOICES = [
        ("PROCESSANDO", "Processando"),
        ("IMPORTADO", "Importado"),
        ("CONCILIADO", "Conciliado"),
        ("ERRO", "Erro"),
    ]

    conta = models.ForeignKey(ContaBancaria, on_delete=models.CASCADE, related_name="extratos")
    arquivo_nome = models.CharField("Nome do Arquivo", max_length=255)
    formato = models.CharField("Formato", max_length=10, choices=[("OFX", "OFX"), ("CSV", "CSV")])
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="PROCESSANDO")
    total_lancamentos = models.IntegerField("Total de Lançamentos", default=0)
    total_conciliados = models.IntegerField("Conciliados", default=0)
    periodo_inicio = models.DateField("Período Início", null=True, blank=True)
    periodo_fim = models.DateField("Período Fim", null=True, blank=True)
    importado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    importado_em = models.DateTimeField(auto_now_add=True)
    observacoes = models.TextField("Observações", blank=True, default="")

    class Meta:
        verbose_name = "Extrato Importado"
        verbose_name_plural = "Extratos Importados"
        ordering = ["-importado_em"]

    def __str__(self):
        return f"{self.arquivo_nome} — {self.conta.nome} ({self.get_status_display()})"

    def atualizar_contadores(self):
        """Recalcula total de lançamentos e conciliados."""
        self.total_lancamentos = self.lancamentos.count()
        self.total_conciliados = self.lancamentos.filter(
            status__in=["CONCILIADO", "MANUAL", "CRIADO", "IGNORADO"]
        ).count()
        if self.total_lancamentos > 0 and self.total_conciliados == self.total_lancamentos:
            self.status = "CONCILIADO"
        elif self.total_conciliados > 0:
            self.status = "IMPORTADO"
        self.save()


class LancamentoExtrato(models.Model):
    """Cada linha do extrato bancário importado."""

    STATUS_CHOICES = [
        ("PENDENTE", "Pendente"),
        ("CONCILIADO", "Conciliado (Auto)"),
        ("MANUAL", "Conciliado (Manual)"),
        ("IGNORADO", "Ignorado"),
        ("CRIADO", "Lançamento Criado"),
    ]

    extrato = models.ForeignKey(ExtratoImportado, on_delete=models.CASCADE, related_name="lancamentos")
    data = models.DateField("Data")
    valor = models.DecimalField("Valor", max_digits=12, decimal_places=2)
    descricao = models.CharField("Descrição / Histórico", max_length=300)
    documento = models.CharField("Documento / Nº", max_length=50, blank=True, default="")
    tipo = models.CharField(
        "Tipo",
        max_length=1,
        choices=[("C", "Crédito"), ("D", "Débito")],
    )
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="PENDENTE")

    # Vínculo com transação do sistema (quando conciliado)
    transacao = models.ForeignKey(
        Transacao,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lancamentos_extrato",
        verbose_name="Transação Vinculada",
    )

    conciliado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="conciliacoes",
    )
    conciliado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Lançamento do Extrato"
        verbose_name_plural = "Lançamentos do Extrato"
        ordering = ["data", "id"]

    def __str__(self):
        sinal = "+" if self.tipo == "C" else "-"
        return f"{self.data.strftime('%d/%m')} {sinal} R$ {abs(self.valor)} — {self.descricao[:50]}"

    @property
    def valor_absoluto(self):
        return abs(self.valor)

    @property
    def is_credito(self):
        return self.tipo == "C"
