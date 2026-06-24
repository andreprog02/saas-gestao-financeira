"""
Validadores de upload de arquivos.
Uso: nas views que recebem request.FILES, chame validar_upload(arquivo).
"""
import os
from django.conf import settings
from django.core.exceptions import ValidationError


EXTENSOES_PERMITIDAS = getattr(
    settings, "UPLOAD_ALLOWED_EXTENSIONS",
    [".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx", ".xls", ".xlsx", ".ofx", ".csv"]
)

TAMANHO_MAX_MB = getattr(settings, "UPLOAD_MAX_SIZE_MB", 10)


def validar_upload(arquivo):
    """
    Valida extensão e tamanho do arquivo.
    Levanta ValidationError se inválido.
    """
    if not arquivo:
        return

    # Verifica extensão
    _, ext = os.path.splitext(arquivo.name)
    ext = ext.lower()
    if ext not in EXTENSOES_PERMITIDAS:
        raise ValidationError(
            f"Tipo de arquivo não permitido ({ext}). "
            f"Permitidos: {', '.join(EXTENSOES_PERMITIDAS)}"
        )

    # Verifica tamanho
    tamanho_mb = arquivo.size / (1024 * 1024)
    if tamanho_mb > TAMANHO_MAX_MB:
        raise ValidationError(
            f"Arquivo muito grande ({tamanho_mb:.1f} MB). "
            f"Máximo permitido: {TAMANHO_MAX_MB} MB."
        )

    return True
