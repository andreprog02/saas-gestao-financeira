from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Usuario, Empresa, PermissaoModulo


class PermissaoInline(admin.TabularInline):
    model = PermissaoModulo
    extra = 0


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    list_display = ("username", "first_name", "last_name", "cargo", "empresa", "is_active")
    list_filter = ("cargo", "empresa", "is_active")
    fieldsets = UserAdmin.fieldsets + (
        ("Dados Adicionais", {"fields": ("cargo", "empresa", "telefone")}),
    )
    inlines = [PermissaoInline]


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("razao_social", "cnpj", "ativo")


@admin.register(PermissaoModulo)
class PermissaoModuloAdmin(admin.ModelAdmin):
    list_display = ("usuario", "modulo", "nivel")
    list_filter = ("modulo", "nivel")
