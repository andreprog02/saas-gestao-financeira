"""
Django settings for config project.
Atualizado com: dotenv, auth customizado, multi-tenancy, permissões.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Carrega variáveis do .env
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# === SEGURANÇA ===
SECRET_KEY = os.getenv("SECRET_KEY", "django-insecure-TROQUE-ESTA-CHAVE-EM-PRODUCAO")
DEBUG = os.getenv("DEBUG", "True").lower() in ("true", "1", "yes")
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

# === APPS ===
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",

    # apps do projeto
    "usuarios",      # <-- NOVO: deve vir antes dos outros
    "core",
    "clientes",
    "financeiro",
    "recebiveis",
    "emprestimos",
    "contas",
    "cobranca",
]

# === USUÁRIO CUSTOMIZADO ===
AUTH_USER_MODEL = "usuarios.Usuario"

# === MIDDLEWARE ===
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "usuarios.middleware.EmpresaMiddleware",   # <-- NOVO: multi-tenancy
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"

# === BANCO DE DADOS ===
DATABASE_URL = os.getenv("DATABASE_URL", "")

if DATABASE_URL.startswith("postgres"):
    # Parse simples da DATABASE_URL para PostgreSQL
    # Formato: postgres://user:pass@host:port/dbname
    import re
    match = re.match(r"postgres(?:ql)?://(.+):(.+)@(.+):(\d+)/(.+)", DATABASE_URL)
    if match:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "USER": match.group(1),
                "PASSWORD": match.group(2),
                "HOST": match.group(3),
                "PORT": match.group(4),
                "NAME": match.group(5),
            }
        }
    else:
        # Fallback SQLite
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": BASE_DIR / "db.sqlite3",
            }
        }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# === VALIDAÇÃO DE SENHAS ===
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# === INTERNACIONALIZAÇÃO ===
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True

# === ARQUIVOS ESTÁTICOS ===
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# WhiteNoise para servir estáticos em produção
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# === MEDIA (uploads) ===
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# === PRIMARY KEY ===
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# === AUTENTICAÇÃO ===
LOGIN_URL = "/usuarios/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/usuarios/login/"

# === CONFIGURAÇÕES DO SISTEMA ===
# Senha do gestor para operações críticas (estorno, cancelamento manual)
MANAGER_PASSWORD = os.getenv("MANAGER_PASSWORD", "1234")

# === SESSÃO ===
SESSION_COOKIE_AGE = 28800   # 8 horas
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
