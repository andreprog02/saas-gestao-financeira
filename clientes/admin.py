

# Register your models here.
from django.contrib import admin
from .models import Cliente

@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("nome_completo", "cpf", "telefone", "cidade", "uf")
    search_fields = ("nome_completo", "cpf")
