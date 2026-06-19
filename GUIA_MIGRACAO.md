# Guia de Migração — Fase 1

## IMPORTANTE: Leia tudo antes de executar

Como estamos trocando o `AUTH_USER_MODEL` (de `auth.User` para `usuarios.Usuario`),
o Django exige que isso seja feito **ANTES** de qualquer `migrate` no banco novo.
A forma mais segura é **resetar as migrations** e criar um banco limpo.

Se você tem dados em produção, veja a Seção B (migração com dados).

---

## Seção A — Banco novo (recomendado se ainda está em desenvolvimento)

### 1. Preparar o ambiente

```bash
# Navegue até a pasta do projeto
cd saas-gestao-financeira

# Crie o arquivo .env a partir do exemplo
cp .env.example .env

# Edite o .env com seus dados
nano .env   # ou code .env

# Instale as dependências limpas
pip install -r requirements_clean.txt
```

### 2. Limpar migrations antigas

```bash
# Remove TODAS as migrations existentes (menos __init__.py)
find . -path "*/migrations/*.py" -not -name "__init__.py" -delete
find . -path "*/migrations/__pycache__" -type d -exec rm -rf {} + 2>/dev/null

# Remove o banco antigo
rm -f db.sqlite3
```

### 3. Gerar migrations novas

```bash
# Cria as migrations com o novo AUTH_USER_MODEL
python manage.py makemigrations usuarios
python manage.py makemigrations clientes
python manage.py makemigrations emprestimos
python manage.py makemigrations financeiro
python manage.py makemigrations recebiveis
python manage.py makemigrations contas
python manage.py makemigrations cobranca
python manage.py makemigrations core
```

### 4. Aplicar e criar superusuário

```bash
python manage.py migrate

# Cria o admin (superuser)
python manage.py createsuperuser
```

### 5. Criar a primeira empresa via admin

```bash
python manage.py runserver
```

Acesse `http://localhost:8000/admin/` e:
1. Crie uma **Empresa** (razão social, CNPJ)
2. Edite seu superusuário → vincule à empresa, defina cargo = ADMIN
3. Agora acesse `http://localhost:8000/` normalmente

---

## Seção B — Migração COM dados existentes (produção)

Se você já tem clientes, contratos e dados no banco, o processo é mais delicado.

### Opção 1: Exportar → Resetar → Reimportar (mais seguro)

```bash
# 1. Exportar dados atuais
python manage.py dumpdata clientes emprestimos financeiro recebiveis contas cobranca \
  --natural-foreign --indent 2 > backup_dados.json

# 2. Seguir Seção A (limpar tudo e recriar)

# 3. Depois de migrate, reimportar
# ATENÇÃO: edite backup_dados.json para:
#   - trocar "auth.user" por "usuarios.usuario" nas referências
#   - adicionar campo "empresa" em cada registro
python manage.py loaddata backup_dados.json
```

### Opção 2: Migração incremental (avançado)

```bash
# 1. ANTES de mudar AUTH_USER_MODEL, crie a tabela do usuario novo
#    em paralelo com uma migration customizada (RunPython)
# 2. Copie os dados do auth_user para usuarios_usuario
# 3. Atualize as ForeignKeys
# 4. Troque o AUTH_USER_MODEL
# 5. Remove o auth_user antigo

# Isso é complexo — recomendo a Opção 1 se possível.
```

---

## Seção C — Adicionar campo `empresa` nos models existentes

Depois que o sistema novo estiver rodando, você precisa vincular os models
existentes à empresa. Faça isso **gradualmente** — não precisa ser tudo de uma vez.

### Passo 1: Adicionar o campo nos models

Edite cada `models.py` e adicione o campo:

```python
# Em clientes/models.py, emprestimos/models.py, financeiro/models.py, etc.
from usuarios.models import Empresa

class Cliente(models.Model):
    empresa = models.ForeignKey(
        Empresa, on_delete=models.CASCADE,
        null=True, blank=True,  # temporariamente nullable
        related_name="clientes"
    )
    # ... resto dos campos ...
```

### Passo 2: Gerar e aplicar migration

```bash
python manage.py makemigrations
python manage.py migrate
```

### Passo 3: Vincular dados existentes à empresa

```bash
python manage.py shell
```

```python
from usuarios.models import Empresa
from clientes.models import Cliente
from emprestimos.models import Emprestimo
from financeiro.models import Transacao
from recebiveis.models import ContratoRecebivel

# Pega (ou cria) sua empresa
empresa = Empresa.objects.first()

# Vincula tudo à empresa
Cliente.objects.filter(empresa__isnull=True).update(empresa=empresa)
Emprestimo.objects.filter(empresa__isnull=True).update(empresa=empresa)
Transacao.objects.filter(empresa__isnull=True).update(empresa=empresa)
ContratoRecebivel.objects.filter(empresa__isnull=True).update(empresa=empresa)
print("Dados vinculados com sucesso!")
```

### Passo 4: Tornar o campo obrigatório

Depois de vincular tudo, remova `null=True, blank=True` do campo empresa
e gere uma nova migration.

### Passo 5: Filtrar nas views

Em cada view, troque:

```python
# ANTES:
clientes = Cliente.objects.all()

# DEPOIS:
clientes = Cliente.objects.filter(empresa=request.empresa)
```

Ou use o manager customizado:

```python
from usuarios.managers import EmpresaManager

class Cliente(models.Model):
    # ...
    objects = EmpresaManager()

# Na view:
clientes = Cliente.objects.da_empresa(request.empresa)
```

---

## Seção D — Usando os decorators de permissão

Nas views que precisam de controle de acesso:

```python
from django.contrib.auth.decorators import login_required
from usuarios.decorators import cargo_minimo, apenas_gerente

# Qualquer logado pode ver
@login_required
def listar_contratos(request):
    ...

# Apenas analista ou acima pode analisar propostas
@cargo_minimo("ANALISTA")
def analisar_proposta(request, proposta_id):
    ...

# Apenas gerente pode cancelar contratos
@apenas_gerente
def cancelar_contrato(request, pk):
    ...

# Verificar alçada de valor
@cargo_minimo("ANALISTA")
def aprovar_proposta(request, proposta_id):
    proposta = get_object_or_404(PropostaEmprestimo, id=proposta_id)
    
    if not request.user.tem_alcada(proposta.valor_solicitado):
        messages.error(request, "Valor acima da sua alçada de aprovação.")
        return redirect("emprestimos:listar_propostas")
    
    # ... aprovar ...
```

---

## Checklist Final

- [ ] `.env` criado e configurado
- [ ] `requirements_clean.txt` renomeado para `requirements.txt`
- [ ] Migrations recriadas com `AUTH_USER_MODEL` novo
- [ ] Superusuário criado
- [ ] Empresa criada no admin
- [ ] Superusuário vinculado à empresa com cargo ADMIN
- [ ] Login funcionando em `/usuarios/login/`
- [ ] Navbar mostrando nome, cargo e empresa
- [ ] Campo `empresa` adicionado nos models principais
- [ ] Dados existentes vinculados à empresa
- [ ] Views filtradas por `request.empresa`
- [ ] Decorators aplicados nas views sensíveis
