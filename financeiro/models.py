from django.db import models
from django.utils import timezone
from decimal import Decimal

class Transacao(models.Model):
    TIPO_CHOICES = [
        ('DEPOSITO', 'Aporte/Depósito'),
        ('SAQUE', 'Sangria/Saque'),
        ('EMPRESTIMO_SAIDA', 'Empréstimo (Saída)'),
        ('PAGAMENTO_ENTRADA', 'Pagamento de Parcela (Entrada)'),
        ('ESTORNO', 'Estorno'),
    ]

    valor = models.DecimalField(max_digits=12, decimal_places=2)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES)
    descricao = models.CharField(max_length=255)
    data = models.DateTimeField(default=timezone.now)
    
    # NOVO CAMPO: Para vincular estornos à transação original (ID de autenticação/vinculação)
    transacao_original = models.ForeignKey(
        'self', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='estornos',
        verbose_name='Transação Original (ID Autenticação)'
    )
    
    # Opcional: Linkar com um empréstimo se for um pagamento ou saída
    # Usamos string 'emprestimos.Emprestimo' para evitar import circular
    emprestimo = models.ForeignKey(
        'emprestimos.Emprestimo', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='transacoes'
    )

    class Meta:
        ordering = ['-data']
        verbose_name = 'Transação'
        verbose_name_plural = 'Transações'

    def __str__(self):
        return f"{self.get_tipo_display()} - R$ {self.valor}"

def calcular_saldo_atual():
    # Soma todos os valores da tabela. 
    # Como saídas são salvas como negativo e entradas como positivo, basta somar.
    saldo = Transacao.objects.aggregate(total=models.Sum('valor'))['total']
    return saldo or Decimal('0.00')


from django.db import models

class LancamentoFinanceiro(models.Model):
    data = models.DateField()
    descricao = models.CharField(max_length=255)
    debito = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    credito = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)

    class Meta:
        verbose_name = 'Lançamento Financeiro'
        verbose_name_plural = 'Lançamentos Financeiros'