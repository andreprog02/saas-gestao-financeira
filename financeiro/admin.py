from django.contrib import admin
from .models import Transacao

@admin.register(Transacao)
class TransacaoAdmin(admin.ModelAdmin):
    # Colunas que aparecem na lista
    list_display = ('tipo', 'valor', 'data', 'descricao', 'get_emprestimo')
    
    # Filtros na barra lateral
    list_filter = ('tipo', 'data')
    
    # Barra de pesquisa
    search_fields = ('descricao',)

    # Mostrar o código do contrato se houver empréstimo vinculado
    def get_emprestimo(self, obj):
        return obj.emprestimo.codigo_contrato if obj.emprestimo else '-'
    get_emprestimo.short_description = 'Contrato'