from django.db import models
from django.utils import timezone
from clientes.models import Cliente  # Assuma que existe; ajuste se necessário
from django.db.models import Sum

class ContratoRecebivel(models.Model):
    STATUS_CHOICES = (
        ('simulado', 'Simulado'),
        ('ativo', 'Ativo'),
        ('renegociado', 'Renegociado'),
    )

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    contrato_id = models.CharField(max_length=10, unique=True, blank=True)  # Gerado como REC001
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='simulado')
    taxa_desconto = models.DecimalField(max_digits=5, decimal_places=2, default=0.05)  # 5%
    valor_bruto = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    valor_liquido = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    data_criacao = models.DateField(default=timezone.now)
    data_ativacao = models.DateField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.contrato_id and self.status == 'ativo':
            last = ContratoRecebivel.objects.filter(contrato_id__startswith='REC').order_by('-id').first()
            seq = int(last.contrato_id[3:]) + 1 if last else 1
            self.contrato_id = f'REC{seq:03d}'
        super().save(*args, **kwargs)

    def calcular_valores(self):
        """Calcula bruto e liquido baseado em itens."""
        agregados = self.itens.aggregate(total=Sum('valor'))
        self.valor_bruto = agregados['total'] or 0
        desconto = self.valor_bruto * self.taxa_desconto
        self.valor_liquido = self.valor_bruto - desconto
        self.save()

    class Meta:
        app_label = 'recebiveis'  # Adicione esta linha
        verbose_name = 'Contrato de Adiantamento de Recebíveis'
        verbose_name_plural = 'Contratos de Adiantamento de Recebíveis'

class ItemRecebivel(models.Model):
    TIPO_CHOICES = (
        ('cheque', 'Cheque'),
        ('nota_fiscal', 'Nota Fiscal'),
        ('cartao', 'Cartão'),
    )

    contrato = models.ForeignKey(ContratoRecebivel, related_name='itens', on_delete=models.CASCADE)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    numero = models.CharField(max_length=50)
    vencimento = models.DateField()
    valor = models.DecimalField(max_digits=15, decimal_places=2)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.contrato.calcular_valores()

    class Meta:
        app_label = 'recebiveis'  # Adicione esta linha
        verbose_name = 'Item de Recebível'
        verbose_name_plural = 'Itens de Recebíveis'

class ItemRecebivel(models.Model):
    TIPO_CHOICES = (
        ('cheque', 'Cheque'),
        ('nota_fiscal', 'Nota Fiscal'),
        ('cartao', 'Cartão'),
    )

    contrato = models.ForeignKey(ContratoRecebivel, related_name='itens', on_delete=models.CASCADE)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    numero = models.CharField(max_length=50)
    vencimento = models.DateField()
    valor = models.DecimalField(max_digits=15, decimal_places=2)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.contrato.calcular_valores()

    class Meta:
        app_label = 'recebiveis'
        verbose_name = 'Item de Recebível'
        verbose_name_plural = 'Itens de Recebíveis'

    def __str__(self):
        return f"{self.get_tipo_display()} {self.numero} - R$ {self.valor}"