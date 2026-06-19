from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Usuario, Empresa


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("razao_social", "nome_fantasia", "cnpj", "ativo", "criado_em")
    list_filter = ("ativo",)
    search_fields = ("razao_social", "nome_fantasia", "cnpj")


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    list_display = ("username", "get_full_name", "cargo", "empresa", "is_active")
    list_filter = ("cargo", "empresa", "is_active")

    # Adiciona os campos customizados nas telas de edição
    fieldsets = UserAdmin.fieldsets + (
        ("Dados da Empresa", {
            "fields": ("empresa", "cargo", "telefone", "foto"),
        }),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        ("Dados da Empresa", {
            "fields": ("empresa", "cargo"),
        }),
    )
