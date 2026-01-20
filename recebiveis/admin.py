from django.contrib import admin
from .models import ContratoRecebivel, ItemRecebivel
# Register your models here.
admin.site.register([ContratoRecebivel, ItemRecebivel])