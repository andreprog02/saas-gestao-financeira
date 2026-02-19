

# Register your models here.
from django.contrib import admin
from .models import CarteiraCobranca

@admin.register(CarteiraCobranca)
class CarteiraCobrancaAdmin(admin.ModelAdmin):
    list_display = ('cliente_devedor', 'profissional', 'percentual_comissao', 'ativo')
    search_fields = ('cliente_devedor__nome', 'profissional__nome')