

# Create your models here.
from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from clientes.models import Cliente
from emprestimos.models import Emprestimo
from recebiveis.models import ContratoRecebivel

class HistoricoCobranca(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    
    # Vínculos opcionais (pode ser de um empréstimo ou de um recebível)
    emprestimo = models.ForeignKey(Emprestimo, on_delete=models.CASCADE, null=True, blank=True, related_name='historico_cobranca')
    recebivel = models.ForeignKey(ContratoRecebivel, on_delete=models.CASCADE, null=True, blank=True, related_name='historico_cobranca')
    
    data_evento = models.DateTimeField(default=timezone.now)
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    descricao = models.TextField(verbose_name="Descrição do Evento")
    
    # Campo auxiliar para facilitar a exibição
    tipo_contrato = models.CharField(max_length=20, default="GERAL") 

    class Meta:
        ordering = ['-data_evento']
        verbose_name = 'Histórico de Cobrança'
        verbose_name_plural = 'Históricos de Cobrança'

    def __str__(self):
        return f"{self.cliente} - {self.data_evento.strftime('%d/%m/%Y')}"