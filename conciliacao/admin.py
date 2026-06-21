from django.contrib import admin
from .models import ContaBancaria, ExtratoImportado, LancamentoExtrato


@admin.register(ContaBancaria)
class ContaBancariaAdmin(admin.ModelAdmin):
    list_display = ("nome", "banco", "agencia", "conta", "tipo", "saldo_inicial", "ativo")
    list_filter = ("banco", "ativo")


class LancamentoInline(admin.TabularInline):
    model = LancamentoExtrato
    extra = 0
    readonly_fields = ("data", "valor", "descricao", "tipo", "status", "transacao")


@admin.register(ExtratoImportado)
class ExtratoAdmin(admin.ModelAdmin):
    list_display = ("arquivo_nome", "conta", "formato", "status", "total_lancamentos", "total_conciliados", "importado_em")
    list_filter = ("status", "formato", "conta")
    inlines = [LancamentoInline]


@admin.register(LancamentoExtrato)
class LancamentoAdmin(admin.ModelAdmin):
    list_display = ("data", "descricao", "valor", "tipo", "status", "transacao")
    list_filter = ("status", "tipo", "extrato__conta")
    search_fields = ("descricao", "documento")
