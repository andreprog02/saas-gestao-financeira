from django.db import models
from django.utils import timezone
from emprestimos.models import Emprestimo

class CodigoOperacao(models.Model):
    """
    Tabela para armazenar os códigos de operação do Fluxo de Caixa.
    Ex: 01 - Despesas Gerais, 05 - Saque Conta Corrente.
    """
    codigo = models.CharField(max_length=5, unique=True, help_text="Ex: 01, 05")
    descricao = models.CharField(max_length=100, help_text="Ex: Despesas Gerais, Saque C/C")
    tipo = models.CharField(max_length=10, choices=[('E', 'Entrada'), ('S', 'Saída')])
    exige_cliente = models.BooleanField(default=False, help_text="Se marcado, o sistema pedirá para selecionar um cliente.")

    def __str__(self):
        return f"{self.codigo} - {self.descricao}"

class Transacao(models.Model):
    TIPO_CHOICES = [
        ('EMPRESTIMO_SAIDA', 'Empréstimo (Saída)'),
        ('PAGAMENTO_ENTRADA', 'Pagamento Parc. (Entrada)'),
        ('ANTECIPACAO', 'Antecipação de Recebíveis'),
        ('DESPESA', 'Despesa Operacional'),
        ('APORTE', 'Aporte de Capital'),
        ('RETIRADA', 'Retirada de Lucro'),
        ('SAQUE_CC', 'Saque Conta Corrente (Cód 05)'), # Novo tipo para o Saque 05
         ('DEPOSITO_CC', 'Depósito Conta Corrente (Cód 06)'), # <--- ADICIONE ESSA LINHA
        ('OUTROS', 'Outros'),
    ]

    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    descricao = models.CharField(max_length=255)
    data = models.DateTimeField(default=timezone.now)
    
    # Relacionamentos
    emprestimo = models.ForeignKey(Emprestimo, on_delete=models.SET_NULL, null=True, blank=True)
    usuario = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True)
    
    # Auditoria e Rastreabilidade
    ip_origem = models.GenericIPAddressField(null=True, blank=True)
    codigo_autenticacao = models.CharField(max_length=64, blank=True, null=True)
    transacao_original = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True)
    
    # Vínculo com o Código de Operação (01, 05, etc)
    codigo_operacao = models.ForeignKey(CodigoOperacao, on_delete=models.SET_NULL, null=True, blank=True)

    def save(self, *args, **kwargs):
        # Lógica para garantir que saídas sejam sempre negativas no banco
        tipos_saida = [
            'EMPRESTIMO_SAIDA', 
            'DESPESA', 
            'RETIRADA', 
            'SAQUE_CC', 
            'ANTECIPACAO'
        ]
        
        # Se for um tipo de saída e o valor vier positivo, converte para negativo
        if self.tipo in tipos_saida and self.valor > 0:
            self.valor = self.valor * -1
            
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.data.strftime('%d/%m')} - {self.descricao} (R$ {self.valor})"

def calcular_saldo_atual():
    """Calcula o saldo total somando todas as transações"""
    from django.db.models import Sum
    total = Transacao.objects.aggregate(saldo=Sum('valor'))['saldo']
    return total or 0.00


