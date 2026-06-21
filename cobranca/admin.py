from django.contrib import admin
from .models import HistoricoCobranca, CarteiraCobranca, CartaCobranca


@admin.register(HistoricoCobranca)
class HistoricoAdmin(admin.ModelAdmin):
    list_display = ("cliente", "data_evento", "descricao", "usuario")
    list_filter = ("tipo_contrato",)
    search_fields = ("cliente__nome_completo", "descricao")


@admin.register(CarteiraCobranca)
class CarteiraAdmin(admin.ModelAdmin):
    list_display = ("cliente_devedor", "profissional", "percentual_comissao", "ativo")
    list_filter = ("ativo",)


@admin.register(CartaCobranca)
class CartaCobrancaAdmin(admin.ModelAdmin):
    list_display = ("numero_formatado", "cliente", "emprestimo", "valor_total_atraso", "data_emissao", "emitido_por")
    list_filter = ("ano",)
    search_fields = ("cliente__nome_completo", "numero_formatado")
    readonly_fields = ("numero", "ano", "numero_formatado")
