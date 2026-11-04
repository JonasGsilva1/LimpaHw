# LimpaHW - Automação de Limpeza de ONTs Huawei GPON

Aplicação em Python para auditoria e limpeza automatizada de ONTs/ONUs offline em OLTs Huawei (família **SmartAX MA5600, MA5608T, MA5680T, MA5800, MA5800-X7**).

Permite definir o período de corte em **dias, semanas ou meses**, importar uma **planilha Excel** contendo as OLTs e suas credenciais, revisar a **descrição / login no sistema ERP** de cada cliente antes da exclusão, e gerar ao final uma **planilha de auditoria** com comandos de **Rollback individual por equipamento**.

---

## 🌟 Principais Recursos

1. **Suporte a JumpServer / Bastion PAM:** Conecte diretamente via JumpServer SSH (porta 2222) usando apenas o seu usuário corporativo. O Bastion aplica automaticamente a conta de sistema configurada no cofre — **você não precisa expor senhas de OLT na planilha!**
2. **Período Limite Flexível:** Defina o tempo de corte em **Dias**, **Semanas** ou **Meses** (ex: `45 dias`, `4 semanas`, `2 meses`).
3. **Importação via Excel Simplificada:** Na planilha, basta preencher a coluna `IP_OLT` ou `Nome_OLT` (ex: `OLT-HW-01`).
4. **Validação de Contrato (Login do Cliente):** A aplicação extrai a descrição do equipamento (login do cliente no sistema/ERP) e apresenta em tela com caixa de seleção (checkbox) para que você confirme se o contrato está realmente cancelado antes de deletar.
5. **Rotina de Limpeza Rigorosa:**
   - 1. Consulta e backup: `display current-configuration ont 0/{slot}/{port} {ont_id}`
   - 2. Localização dos `service-port`
   - 3. Remoção dos service-ports: `undo service-port <id>`
   - 4. Acesso à interface GPON: `interface gpon 0/{slot}`
   - 5. Deleção da ONT: `ont delete {port} {ont_id}`
6. **Relatório em Excel e Rollback Individual:**
   - Gera um arquivo `.xlsx` completo com os equipamentos deletados, MAC, Login do cliente e data/hora.
   - Gera scripts de Rollback `.txt` individuais para restaurar cada ONT com 1 clique se necessário.
7. **Duas Interfaces Prontas:**
   - **Interface Web Moderna (Streamlit):** Visual com tabelas interativas, checkboxes e botões de download.
   - **Interface CLI (Terminal Rich):** Rápida, visual e ideal para rotinas automatizadas no prompt.

---

## 🖥️ 1. Como Usar a Interface Web (Recomendado)

### Inicialização Rápida no Windows (`.bat`)
- **Iniciar Serviço:** Dê dois cliques em **`iniciar_limpahw.bat`** (inicia o servidor em segundo plano na porta `8501` e abre automaticamente `http://localhost:8501` no navegador).
- **Finalizar Serviço:** Dê dois cliques em **`parar_limpahw.bat`** (encerra imediatamente o serviço em segundo plano).

### Ou via Terminal:
```powershell
uv run streamlit run app/web_app.py
```
*(ou execute `uv run python main.py --web`)*

O painel será aberto no endereço `http://localhost:8501`:
1. **Configuração na barra lateral:** 
   - Escolha o tempo de corte inteiro (**Dias**, **Semanas** ou **Meses**).
   - Escolha o método de conexão: **JumpServer (Bastion / PAM)** ou **SSH Direto nas OLTs**, e clique em **💾 Salvar Configurações**.
2. **Aba 1 (Importar & Varrer):** Baixe a planilha modelo caso não a tenha, preencha com os IPs/Nomes das OLTs e faça o upload. Clique em **Iniciar Varredura**.
3. **Aba 2 (Revisar & Confirmar):** Visualize a tabela apenas com as ONTs offline que atingiram o período filtrado, contendo **Login do Cliente**, **Serial**, **MAC** e **Dias Offline**. Use a caixa de seleção para desmarcar qualquer cliente que ainda tenha contrato ativo e clique em **Executar Limpeza**.
4. **Aba 3 (Relatório & Rollback):** Baixe a planilha Excel final de auditoria, os scripts `.txt` ou clique diretamente em **🔄 Reincluir Equipamento na OLT** para restaurar qualquer ONT e seus `service-ports` com 1 clique.
5. **Aba 4 (Logs de Diagnóstico):** Acompanhe em tempo real todos os comandos e retornos das OLTs.

---

## 💻 2. Como Usar via Terminal (CLI)

### Gerar a Planilha Modelo de OLTs
```powershell
uv run python main.py --gerar-modelo
```
Gera o arquivo `modelo_olts.xlsx` na pasta raiz.

### Executar a Varredura via JumpServer / Bastion
```powershell
# Simulação via JumpServer com planilha de OLTs (corte de 30 dias):
uv run python main.py --jumpserver --js-host jump.seuprovedor.com.br --excel modelo_olts.xlsx --time 30 --unit dias

# Simulação com corte de 4 semanas:
uv run python main.py --jumpserver --js-host jump.seuprovedor.com.br --excel modelo_olts.xlsx --time 4 --unit semanas

# Execução em Modo Real na OLT:
uv run python main.py --jumpserver --js-host jump.seuprovedor.com.br --excel modelo_olts.xlsx --time 60 --unit dias --no-dry-run
```

### Executar com Conexão SSH Direta nas OLTs (sem JumpServer)
```powershell
uv run python main.py --excel modelo_olts.xlsx --time 30 --unit dias
```
*(No modo real, o sistema exige digitar expressamente `DELETAR` antes de executar qualquer alteração na OLT).*

### Modo Demonstração (Sem OLT física conectada)
```powershell
uv run python main.py --demo --time 1 --unit meses
```

---

## 📊 Formato da Planilha Modelo (`modelo_olts.xlsx`)

| Nome_OLT | IP_OLT | Porta_SSH | Usuario | Senha | Senha_Enable | Slot | Porta_PON |
| :--- | :--- | :---: | :--- | :--- | :--- | :---: | :---: |
| OLT Matriz | 192.168.10.1 | 22 | admin | Secret@123 | | | |
| OLT Bairro Sul | 192.168.20.1 | 22 | admin | Secret@123 | | 1 | 0 |

> **Nota:** As colunas `Slot` e `Porta_PON` são opcionais. Se deixadas em branco, a aplicação varre todas as placas GPON ativas e todas as portas PON da OLT.

---

## 🛡️ Estrutura do Rollback Gerado

Para cada equipamento deletado, é gerado um arquivo de rollback individual (ex: `backups/rollback_192_168_10_1_slot1_pon0_ont1_...txt`) contendo a sequência exata para recriar o cliente:

```bash
config
interface gpon 0/1
ont add 0 1 sn-auth "4857544311223344" omci ont-lineprofile-id 10 ont-srvprofile-id 10 desc "maria.santos_fibra"
ont port native-vlan 0 1 eth 1 vlan 100 priority 0
quit
service-port 1050 vlan 100 gpon 0/1/0 ont 1 gemport 1 multi-service user-vlan 100 tag-transform translate
service-port 1051 vlan 200 gpon 0/1/0 ont 1 gemport 2 multi-service user-vlan 200 tag-transform translate
```

Esses mesmos comandos também são incluídos na coluna `Comandos_Rollback_CLI` do relatório Excel final.

---

## 🧪 Testes Automatizados

Para executar os 19 testes unitários que cobrem parsing de CLI Huawei, importação/exportação de Excel e rotinas de backup/rollback:

```powershell
uv run pytest
```
