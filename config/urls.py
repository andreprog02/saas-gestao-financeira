from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    path("usuarios/", include("usuarios.urls")),   # <-- NOVO
    path("", include("core.urls")),
    path("clientes/", include("clientes.urls")),
    path("emprestimos/", include("emprestimos.urls")),
    path("recebiveis/", include("recebiveis.urls")),
    path("cobranca/", include("cobranca.urls")),
    path("contas/", include("contas.urls")),
    path("financeiro/", include("financeiro.urls")),
    path("conciliacao/", include("conciliacao.urls")),
]

# Servir media em desenvolvimento
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
