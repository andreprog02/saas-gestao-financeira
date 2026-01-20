from django.db import models
from django.utils import timezone
from clientes.models import Cliente
from django.db.models import Sum
from decimal import Decimal

class ContratoRecebivel(models.Model):
    STATUS_CHOICES = (
        ('simulado', 'Simulado'),
        ('ativo', 'Ativo'),
        ('renegociado', 'Renegociado'),
    )

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    contrato_id = models.CharField(max_length=10, unique=True, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='simulado')
    taxa_desconto = models.DecimalField(max_digits=5, decimal_places=2, default=0.05)  # Ex: 4.30 para 4.3%
    valor_bruto = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    valor_liquido = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    data_criacao = models.DateField(default=timezone.now)
    data_ativacao = models.DateField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.contrato_id and self.status == 'ativo':
            last = ContratoRecebivel.objects.filter(contrato_id__startswith='REC').order_by('-id').first()
            seq = int(last.contrato_id[3:]) + 1 if last else 1
            self.contrato_id = f'REC{seq:03d}'
        
        if self.contrato_id == '':
            self.contrato_id = None
            
        super().save(*args, **kwargs)

    def calcular_valores(self):
        """Calcula bruto e liquido considerando a taxa mensal pro-rata dia."""
        self.valor_bruto = Decimal('0.00')
        total_desconto = Decimal('0.00')
        
        # Converte a taxa de 4.3 para 0.043
        taxa_percentual = self.taxa_desconto / Decimal('100')
        
        itens = self.itens.all()
        
        # Se não houver itens, zera tudo e salva
        if not itens:
            self.valor_liquido = Decimal('0.00')
            self.save()
            return

        for item in itens:
            self.valor_bruto += item.valor
            
            # Calcula dias entre a criação (hoje) e o vencimento
            dias = (item.vencimento - self.data_criacao).days
            if dias < 0: dias = 0
            
            # Cálculo de Adiantamento (Desconto Comercial Simples)
            # Fórmula: Valor * (Taxa/100) * (Dias / 30)
            fator_tempo = Decimal(dias) / Decimal('30')
            desconto_item = item.valor * taxa_percentual * fator_tempo
            
            total_desconto += desconto_item

        self.valor_liquido = self.valor_bruto - total_desconto
        self.save()

    class Meta:
        app_label = 'recebiveis'
        verbose_name = 'Contrato de Adiantamento de Recebíveis'
        verbose_name_plural = 'Contratos de Adiantamento de Recebíveis'
    
    def __str__(self):
        return f"{self.contrato_id or 'Simulação'} - {self.cliente.nome_completo}"

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