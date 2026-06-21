from django.contrib import admin
from .models import ContaPagar


@admin.register(ContaPagar)
class ContaPagarAdmin(admin.ModelAdmin):
    list_display = ("descricao", "tipo_despesa", "valor", "vencimento", "status", "cadastrado_por", "aprovado_por", "pago_por")
    list_filter = ("status", "tipo_despesa")
    search_fields = ("descricao",)
    readonly_fields = ("cadastrado_em", "aprovado_em", "pago_em")
