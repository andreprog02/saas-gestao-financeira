from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone


class ContaPagar(models.Model):
    """Conta a pagar com fluxo: Pendente → Aprovada → Paga (ou Negada)."""

    class Status(models.TextChoices):
        PENDENTE = "PENDENTE", "Pendente de Aprovação"
        APROVADA = "APROVADA", "Aprovada para Pagamento"
        PAGA = "PAGA", "Paga"
        NEGADA = "NEGADA", "Negada"
        DEVOLVIDA = "DEVOLVIDA", "Devolvida para Correção"

    class TipoDespesa(models.TextChoices):
        ALUGUEL = "ALUGUEL", "Aluguel"
        SALARIOS = "SALARIOS", "Salários"
        IMPOSTOS = "IMPOSTOS", "Impostos"
        AGUA = "AGUA", "Água"
        ENERGIA = "ENERGIA", "Energia"
        INTERNET = "INTERNET", "Internet"
        TELEFONE = "TELEFONE", "Telefone"
        DESP_JUDICIAIS = "DESP_JUDICIAIS", "Despesas Judiciais"
        MULTAS = "MULTAS", "Multas"
        TAXAS = "TAXAS", "Taxas"
        MATERIAL = "MATERIAL", "Material de Escritório"
        MANUTENCAO = "MANUTENCAO", "Manutenção"
        TRANSPORTE = "TRANSPORTE", "Transporte"
        SEGUROS = "SEGUROS", "Seguros"
        CONTABILIDADE = "CONTABILIDADE", "Contabilidade"
        MARKETING = "MARKETING", "Marketing"
        SOFTWARE = "SOFTWARE", "Software / Assinaturas"
        OUTROS = "OUTROS", "Outros"

    # Dados da conta
    descricao = models.CharField("Nome / Descrição", max_length=200)
    tipo_despesa = models.CharField(
        "Tipo de Despesa", max_length=20,
        choices=TipoDespesa.choices, default=TipoDespesa.OUTROS,
    )
    valor = models.DecimalField("Valor (R$)", max_digits=12, decimal_places=2)
    vencimento = models.DateField("Data de Vencimento")
    observacoes = models.TextField("Observações", blank=True, default="")

    # Arquivo da fatura (PDF/imagem)
    fatura = models.FileField(
        "Fatura / Boleto", upload_to="contas_pagar/faturas/%Y/%m/",
        blank=True, null=True,
    )

    # Status e fluxo
    status = models.CharField(
        "Status", max_length=15,
        choices=Status.choices, default=Status.PENDENTE,
    )

    # Quem cadastrou
    cadastrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="contas_cadastradas",
        verbose_name="Cadastrado por",
    )
    cadastrado_em = models.DateTimeField("Cadastrado em", auto_now_add=True)

    # Aprovação
    aprovado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="contas_aprovadas",
        verbose_name="Aprovado por",
    )
    aprovado_em = models.DateTimeField("Aprovado em", null=True, blank=True)

    # Negação / Devolução
    justificativa = models.TextField("Justificativa (negação/devolução)", blank=True, default="")

    # Pagamento
    pago_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="contas_pagas",
        verbose_name="Pago por",
    )
    pago_em = models.DateTimeField("Pago em", null=True, blank=True)
    comprovante = models.FileField(
        "Comprovante de Pagamento", upload_to="contas_pagar/comprovantes/%Y/%m/",
        blank=True, null=True,
    )

    class Meta:
        ordering = ["vencimento"]
        verbose_name = "Conta a Pagar"
        verbose_name_plural = "Contas a Pagar"

    def __str__(self):
        return f"{self.descricao} — R$ {self.valor} — Venc. {self.vencimento.strftime('%d/%m/%Y')}"

    @property
    def vencida(self):
        """Retorna True se venceu e não está paga."""
        if self.status == self.Status.PAGA:
            return False
        return self.vencimento < timezone.localdate()

    @property
    def dias_ate_vencimento(self):
        """Dias até o vencimento (negativo = vencida)."""
        return (self.vencimento - timezone.localdate()).days

    @property
    def status_cor(self):
        """Cor do badge para o template."""
        cores = {
            "PENDENTE": "warning",
            "APROVADA": "info",
            "PAGA": "success",
            "NEGADA": "danger",
            "DEVOLVIDA": "secondary",
        }
        return cores.get(self.status, "secondary")
