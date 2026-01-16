

# Register your models here.
from django.contrib import admin
from .models import Emprestimo, Parcela

class ParcelaInline(admin.TabularInline):
    model = Parcela
    extra = 0

@admin.register(Emprestimo)
class EmprestimoAdmin(admin.ModelAdmin):
    list_display = ("codigo_contrato", "cliente", "status", "valor_emprestado", "qtd_parcelas", "primeiro_vencimento")
    search_fields = ("codigo_contrato", "cliente__nome_completo", "cliente__cpf")
    list_filter = ("status",)
    inlines = [ParcelaInline]

@admin.register(Parcela)
class ParcelaAdmin(admin.ModelAdmin):
    list_display = ("emprestimo", "numero", "vencimento", "valor", "status")
    list_filter = ("status",)
    search_fields = ("emprestimo__codigo_contrato", "emprestimo__cliente__nome_completo", "emprestimo__cliente__cpf")
