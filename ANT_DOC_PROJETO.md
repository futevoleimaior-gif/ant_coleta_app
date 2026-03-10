# 🏐 APP ANT — DOCUMENTAÇÃO OFICIAL DO PROJETO

Última atualização: 2026


====================================================
1. OBJETIVO DO SISTEMA
====================================================

O APP ANT é um sistema desenvolvido para automatizar o registro de torneios de futevôlei na Agenda Nacional de Torneios (ANT).

Principais funções:

• Receber prints de torneios
• Extrair dados automaticamente com IA
• Padronizar dados conforme regras da ANT
• Validar os dados antes do registro
• Registrar torneios nas planilhas ANT
• Salvar flyers no Google Drive
• Registrar logs de salvamento para auditoria



====================================================
2. TECNOLOGIAS UTILIZADAS
====================================================

Python  
Streamlit  
OpenAI GPT  
Google Sheets API  
Google Drive API  
OAuth Google  



====================================================
3. ESTRUTURA DO PROJETO
====================================================

APP_ANT/

app.py  
requirements.txt  
ANT_DOC_PROJETO.md  

credentials/  
    google_service_account.json  
    google_oauth_client.json  
    google_oauth_token.json  

.streamlit/  
    secrets.toml



====================================================
4. FLUXO OPERACIONAL DO SISTEMA
====================================================

TELA 1 — PRÉ-ANÁLISE

Função:

Receber prints de torneios e extrair dados automaticamente.

Fluxo:

PRINT
↓
GPT
↓
mensagem pronta
↓
organizador confirma dados


Saída:

Data:
Torneio:
Cidade/ES:
Local:
Categorias:
Contato:



----------------------------------------------------

TELA 2 — REGISTRO FINAL

Função:

Registrar oficialmente o torneio.

Fluxo:

Texto confirmado
+
Flyer final
↓
Validação
↓
Registro na planilha
↓
Upload do flyer
↓
Registro no LOG



====================================================
5. ESTRUTURA DAS PLANILHAS ANT
====================================================

Existem duas planilhas principais.


AGENDA SUL E SUDESTE

Estados:

ES
MG
RJ
SP
PR
SC
RS


AGENDA NORTE / NORDESTE / CENTRO-OESTE

Estados:

AC
AP
AM
PA
RO
RR
TO

AL
BA
CE
MA
PB
PE
PI
RN
SE

GO
MT
MS
DF


Cada mês possui uma aba própria.



====================================================
6. ESTRUTURA DA LINHA DA PLANILHA
====================================================

Ordem das colunas:

Nº  
Data  
Data inicial  
Data final  
Torneio  
Cidade  
Estado  
Local  
Categorias  
Contato  
Status  

A coluna Nº é preenchida automaticamente pela macro da planilha.



====================================================
7. REGRAS DE DATA DA ANT
====================================================

Campo DATA sempre inicia com apóstrofo.

Exemplos:

'27/02/26

'27 e 28/02/26

'27, 28 e 29/02/26

'30, 31/03 e 01/04/26


DATA INICIAL

Primeiro dia do evento.

Formato:

dd/mm/aa


DATA FINAL

Último dia do evento.

Formato:

dd/mm/aa



====================================================
8. NOME AUTOMÁTICO DO FLYER
====================================================

Formato:

UF DIA DIA CIDADE.extensão

Exemplo:

SP 27 28 Sorocaba.jpg



====================================================
9. SALVAMENTO NO GOOGLE DRIVE
====================================================

Os flyers são salvos nas pastas:

FLYERS MARÇO FAZER  
FLYERS ABRIL FAZER  


Quando o torneio ocorre em virada de mês:

o flyer é salvo nas duas pastas.



====================================================
10. SISTEMA DE LOG
====================================================

Existe uma planilha chamada:

LOG APP ANT


Aba:

LOG


Colunas:

Timestamp  
Torneio  
Cidade  
Data  
Agenda  
Mês 1  
Mês 2  
Nome do flyer  
Status  
Erro  


STATUS POSSÍVEIS

SUCESSO  
ERRO  



====================================================
11. PROTEÇÃO CONTRA DUPLICIDADE
====================================================

O sistema gera um fingerprint de salvamento usando:

texto confirmado  
agenda  
mes 1  
mes 2  
nome do flyer  

Se o usuário tentar salvar novamente o mesmo torneio na mesma sessão:

o salvamento é bloqueado.



====================================================
12. INTEGRAÇÕES EXTERNAS
====================================================

OPENAI

Utilizado para:

extração automática de dados dos prints.


GOOGLE SHEETS

Utilizado para:

registro de torneios  
registro de logs.


GOOGLE DRIVE

Utilizado para:

armazenamento de flyers.



====================================================
13. REGRAS DO RECEBEDOR DE TORNEIOS
====================================================

O sistema segue a matriz oficial:

Recebedor de Torneios v3.0


Principais regras:

1 print = 1 torneio

prioridade das fontes:

texto complementar
↓
arte
↓
legenda


Campos obrigatórios:

Data  
Data inicial  
Data final  
Nome do torneio  
Cidade e estado  
Estado por extenso  
Local  
Categorias  
Contato


Separador CSV:

;



====================================================
14. EVOLUÇÕES FUTURAS DO SISTEMA
====================================================

Possíveis melhorias futuras:

Publicação do app no Streamlit Cloud

Interface mobile otimizada

PWA ou aplicativo nativo

Sistema de auditoria avançado

Controle de duplicidade global

Histórico de alterações

Registro de usuário

Automação de ranking

Integração com sistema de pontuação



====================================================
15. MANUTENÇÃO DESTE DOCUMENTO
====================================================

Este documento deve ser atualizado sempre que houver:

• alteração no fluxo do sistema  
• alteração nas planilhas ANT  
• alteração nas regras de data  
• alteração nas regras de categorias  
• novas integrações externas  
• novas funcionalidades  


Manter este documento atualizado garante continuidade do projeto mesmo sem histórico de conversa.


====================================================
DOCUMENTO OFICIAL DO SISTEMA ANT
====================================================