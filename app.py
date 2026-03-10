import os
import io
import base64
import re
import hashlib
import unicodedata
from datetime import datetime

import streamlit as st
import pandas as pd
import gspread

from openai import OpenAI
from PIL import Image

from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

st.set_page_config(page_title="APP ANT", page_icon="🏆", layout="centered")


# =========================================
# HELPERS DE SECRETS
# =========================================
def obter_secret_obrigatorio(chave):
    try:
        valor = st.secrets[chave]
        if isinstance(valor, str) and not valor.strip():
            raise KeyError
        return valor
    except Exception:
        st.error(f"Secret obrigatório ausente ou vazio: {chave}")
        st.stop()


OPENAI_API_KEY = obter_secret_obrigatorio("OPENAI_API_KEY")
GOOGLE_SHEET_ID_SUL = obter_secret_obrigatorio("GOOGLE_SHEET_ID_SUL")
GOOGLE_SHEET_ID_NORTE = obter_secret_obrigatorio("GOOGLE_SHEET_ID_NORTE")
GOOGLE_SHEET_ID_LOG = obter_secret_obrigatorio("GOOGLE_SHEET_ID_LOG")

FLYERS_MARCO_FAZER = obter_secret_obrigatorio("FLYERS_MARCO_FAZER")
FLYERS_ABRIL_FAZER = obter_secret_obrigatorio("FLYERS_ABRIL_FAZER")

GOOGLE_CLIENT_ID = obter_secret_obrigatorio("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = obter_secret_obrigatorio("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = obter_secret_obrigatorio("GOOGLE_REDIRECT_URI")
APP_SECRET_KEY = obter_secret_obrigatorio("APP_SECRET_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

if "ultimo_salvamento_fingerprint" not in st.session_state:
    st.session_state["ultimo_salvamento_fingerprint"] = None

if "drive_token_info" not in st.session_state:
    st.session_state["drive_token_info"] = None

if "drive_oauth_state" not in st.session_state:
    st.session_state["drive_oauth_state"] = None


# =========================================
# CONFIG GOOGLE
# =========================================
SERVICE_ACCOUNT_FILE = "credentials/google_service_account.json"

SCOPES_SHEETS = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SCOPES_DRIVE_OAUTH = [
    "https://www.googleapis.com/auth/drive",
]


# =========================================
# QUERY PARAMS (compatibilidade)
# =========================================
def obter_query_param(nome):
    try:
        valor = st.query_params.get(nome)
        if isinstance(valor, list):
            return valor[0] if valor else None
        return valor
    except Exception:
        params = st.experimental_get_query_params()
        valores = params.get(nome, [])
        return valores[0] if valores else None


def limpar_query_params():
    try:
        st.query_params.clear()
    except Exception:
        st.experimental_set_query_params()


# =========================================
# GOOGLE SHEETS / SERVICE ACCOUNT
# =========================================
def obter_credenciais_service_account():
    # Prioridade: Streamlit Cloud secrets
    try:
        info = dict(st.secrets["gcp_service_account"])
        return ServiceAccountCredentials.from_service_account_info(
            info,
            scopes=SCOPES_SHEETS
        )
    except Exception:
        pass

    # Fallback local
    if os.path.exists(SERVICE_ACCOUNT_FILE):
        return ServiceAccountCredentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=SCOPES_SHEETS
        )

    raise RuntimeError(
        "Credenciais da service account não encontradas. "
        "No Streamlit Cloud, adicione [gcp_service_account] nos secrets. "
        "No ambiente local, mantenha o arquivo credentials/google_service_account.json."
    )


def conectar_gsheet():
    creds = obter_credenciais_service_account()
    return gspread.authorize(creds)


def obter_planilha_por_agenda(client_gs, agenda):
    if agenda == "SUL":
        return client_gs.open_by_key(GOOGLE_SHEET_ID_SUL)
    if agenda == "NORTE":
        return client_gs.open_by_key(GOOGLE_SHEET_ID_NORTE)
    raise ValueError("Agenda inválida.")


def obter_planilha_log(client_gs):
    return client_gs.open_by_key(GOOGLE_SHEET_ID_LOG)


def salvar_linha_na_aba(planilha, nome_aba, linha):
    aba = planilha.worksheet(nome_aba)
    aba.append_row(linha, value_input_option="USER_ENTERED")


def registrar_log(
    client_gs,
    torneio,
    cidade,
    data_evento,
    agenda,
    mes_1,
    mes_2,
    nome_flyer,
    status,
    erro=""
):
    planilha_log = obter_planilha_log(client_gs)
    aba_log = planilha_log.worksheet("LOG")

    timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    linha_log = [
        timestamp,
        torneio,
        cidade,
        data_evento,
        agenda,
        mes_1,
        mes_2,
        nome_flyer,
        status,
        erro,
    ]

    aba_log.append_row(linha_log, value_input_option="USER_ENTERED")


# =========================================
# GOOGLE DRIVE (OAuth WEB - sem PKCE)
# =========================================
def obter_client_config_oauth():
    return {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "project_id": "app-ant",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }


def criar_flow_oauth_drive(state=None):
    flow = Flow.from_client_config(
        client_config=obter_client_config_oauth(),
        scopes=SCOPES_DRIVE_OAUTH,
        state=state
    )
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    return flow


def gerar_url_autorizacao_drive():
    flow = criar_flow_oauth_drive()

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent"
    )

    st.session_state["drive_oauth_state"] = state
    return authorization_url


def processar_callback_oauth_drive():
    code = obter_query_param("code")
    state = obter_query_param("state")
    error = obter_query_param("error")

    if error:
        st.error(f"Autorização do Google cancelada ou negada: {error}")
        limpar_query_params()
        return

    if not code:
        return

    state_esperado = st.session_state.get("drive_oauth_state")

    if state_esperado and state != state_esperado:
        st.error("Falha de segurança no retorno do Google (state inválido).")
        limpar_query_params()
        return

    try:
        flow = criar_flow_oauth_drive(state=state)
        flow.fetch_token(
            code=code,
            client_secret=GOOGLE_CLIENT_SECRET
        )

        creds = flow.credentials
        st.session_state["drive_token_info"] = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes,
        }

        st.session_state["drive_oauth_state"] = None
        limpar_query_params()
        st.success("Google Drive conectado com sucesso.")
        st.rerun()

    except Exception as e:
        st.error("Não foi possível concluir a autenticação do Google Drive.")
        st.code(repr(e))
        limpar_query_params()


def obter_credenciais_drive_usuario():
    token_info = st.session_state.get("drive_token_info")
    if not token_info:
        return None

    creds = UserCredentials.from_authorized_user_info(
        token_info,
        SCOPES_DRIVE_OAUTH
    )

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            st.session_state["drive_token_info"] = {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": creds.scopes,
            }
        except Exception:
            st.session_state["drive_token_info"] = None
            return None

    if not creds.valid:
        return None

    return creds


def drive_conectado():
    creds = obter_credenciais_drive_usuario()
    return creds is not None


def conectar_drive_usuario():
    creds = obter_credenciais_drive_usuario()
    if not creds:
        raise RuntimeError(
            "Google Drive não conectado. Clique em 'Conectar Google Drive' antes de salvar."
        )

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def desconectar_drive_usuario():
    st.session_state["drive_token_info"] = None
    st.session_state["drive_oauth_state"] = None
    limpar_query_params()


def obter_id_pasta_flyers(mes):
    mapa = {
        "3. Março": FLYERS_MARCO_FAZER,
        "4. Abril": FLYERS_ABRIL_FAZER,
    }
    return mapa.get(mes, "")


def upload_arquivo_drive(service, uploaded_file, folder_id, nome_arquivo=None):
    if not folder_id:
        raise ValueError("ID da pasta não encontrado.")

    file_name = nome_arquivo if nome_arquivo else uploaded_file.name

    file_metadata = {
        "name": file_name,
        "parents": [folder_id]
    }

    file_bytes = uploaded_file.getvalue()
    media = MediaIoBaseUpload(
        io.BytesIO(file_bytes),
        mimetype=uploaded_file.type,
        resumable=False
    )

    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,name,parents",
        supportsAllDrives=True
    ).execute()

    return file


# =========================================
# UTILITÁRIOS GERAIS
# =========================================
def limpar_espacos(texto):
    return " ".join(str(texto).strip().split())


def remover_acentos(texto):
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def imagem_para_data_url(uploaded_file):
    bytes_imagem = uploaded_file.getvalue()
    base64_image = base64.b64encode(bytes_imagem).decode("utf-8")
    mime_type = uploaded_file.type
    return f"data:{mime_type};base64,{base64_image}"


def normalizar_ano(ano_texto):
    ano_texto = str(ano_texto).strip()
    if len(ano_texto) == 2:
        return f"20{ano_texto}"
    return ano_texto


def ano_4_para_2(ano_texto):
    ano_texto = normalizar_ano(ano_texto)
    return ano_texto[-2:]


def gerar_nome_arquivo(uf, data_evento, cidade):
    if not uf or not data_evento or not cidade:
        return ""
    dias = extrair_dias_para_nome(data_evento)
    return f"{uf} {dias} {cidade.strip()}"


def gerar_nome_flyer(uploaded_file, nome_base):
    _, extensao = os.path.splitext(uploaded_file.name)
    extensao = extensao.lower().strip()

    if not extensao:
        mime_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }
        extensao = mime_map.get(uploaded_file.type, "")

    return f"{nome_base}{extensao}"


def gerar_fingerprint_salvamento(texto_confirmado, agenda, mes_1, mes_2, flyer_final):
    nome_flyer = flyer_final.name if flyer_final else ""
    base = "||".join([
        limpar_espacos(texto_confirmado),
        agenda or "",
        mes_1 or "",
        mes_2 or "",
        nome_flyer
    ])
    return hashlib.md5(base.encode("utf-8")).hexdigest()


# =========================================
# ESTADOS
# =========================================
def uf_para_estado(uf):
    mapa = {
        "AC": "Acre",
        "AL": "Alagoas",
        "AP": "Amapá",
        "AM": "Amazonas",
        "BA": "Bahia",
        "CE": "Ceará",
        "DF": "Distrito Federal",
        "ES": "Espírito Santo",
        "GO": "Goiás",
        "MA": "Maranhão",
        "MT": "Mato Grosso",
        "MS": "Mato Grosso do Sul",
        "MG": "Minas Gerais",
        "PA": "Pará",
        "PB": "Paraíba",
        "PR": "Paraná",
        "PE": "Pernambuco",
        "PI": "Piauí",
        "RJ": "Rio de Janeiro",
        "RN": "Rio Grande do Norte",
        "RS": "Rio Grande do Sul",
        "RO": "Rondônia",
        "RR": "Roraima",
        "SC": "Santa Catarina",
        "SP": "São Paulo",
        "SE": "Sergipe",
        "TO": "Tocantins",
    }
    return mapa.get(uf.strip().upper(), "")


def normalizar_cidade_uf(cidade_uf):
    s = limpar_espacos(cidade_uf)
    if not s:
        return ""

    s = s.replace(" - ", "/")
    s = s.replace(" – ", "/")
    s = s.replace("\\", "/")

    if "/" not in s:
        return s

    partes = s.rsplit("/", 1)
    cidade = limpar_espacos(partes[0])
    uf = limpar_espacos(partes[1]).upper()

    if len(uf) > 2:
        mapa_reverso = {
            remover_acentos(v).lower(): k
            for k, v in {
                "AC": "Acre",
                "AL": "Alagoas",
                "AP": "Amapá",
                "AM": "Amazonas",
                "BA": "Bahia",
                "CE": "Ceará",
                "DF": "Distrito Federal",
                "ES": "Espírito Santo",
                "GO": "Goiás",
                "MA": "Maranhão",
                "MT": "Mato Grosso",
                "MS": "Mato Grosso do Sul",
                "MG": "Minas Gerais",
                "PA": "Pará",
                "PB": "Paraíba",
                "PR": "Paraná",
                "PE": "Pernambuco",
                "PI": "Piauí",
                "RJ": "Rio de Janeiro",
                "RN": "Rio Grande do Norte",
                "RS": "Rio Grande do Sul",
                "RO": "Rondônia",
                "RR": "Roraima",
                "SC": "Santa Catarina",
                "SP": "São Paulo",
                "SE": "Sergipe",
                "TO": "Tocantins",
            }.items()
        }
        uf_normalizada = mapa_reverso.get(remover_acentos(uf).lower(), uf[:2].upper())
        uf = uf_normalizada

    return f"{cidade}/{uf}"


def separar_cidade_uf(cidade_uf):
    cidade_uf = normalizar_cidade_uf(cidade_uf)

    if "/" not in cidade_uf:
        return cidade_uf.strip(), "", ""

    partes = cidade_uf.rsplit("/", 1)
    cidade = partes[0].strip()
    uf = partes[1].strip().upper()
    estado = uf_para_estado(uf)
    return cidade, uf, estado


# =========================================
# DATAS
# =========================================
def extrair_partes_data(data_texto):
    s = limpar_espacos(data_texto)
    padrao = r"(\d{1,2})(?:/(\d{1,2}))?(?:/(\d{2,4}))?"
    return re.findall(padrao, s)


def reconstruir_datas_completas(data_texto):
    partes = extrair_partes_data(data_texto)
    if not partes:
        return []

    registros = []
    for dia, mes, ano in partes:
        registros.append({
            "dia": dia.zfill(2),
            "mes": mes.zfill(2) if mes else None,
            "ano": normalizar_ano(ano) if ano else None
        })

    ano_corrente = None
    for i in range(len(registros) - 1, -1, -1):
        if registros[i]["ano"]:
            ano_corrente = registros[i]["ano"]
        else:
            registros[i]["ano"] = ano_corrente

    mes_corrente = None
    for i in range(len(registros) - 1, -1, -1):
        if registros[i]["mes"]:
            mes_corrente = registros[i]["mes"]
        else:
            registros[i]["mes"] = mes_corrente

    datas = []
    for r in registros:
        if r["dia"] and r["mes"] and r["ano"]:
            datas.append(f'{r["dia"]}/{r["mes"]}/{r["ano"]}')

    return datas


def extrair_data_inicial_final(data_texto):
    datas = reconstruir_datas_completas(data_texto)
    if not datas:
        return "", ""
    return datas[0], datas[-1]


def normalizar_data_visual_ant(data_texto):
    s = limpar_espacos(data_texto)
    if not s:
        return ""

    datas = reconstruir_datas_completas(s)
    if not datas:
        return f"'{s}"

    if len(datas) == 1:
        d, m, a = datas[0].split("/")
        return f"'{d}/{m}/{a[-2:]}"

    meses = [d.split("/")[1] for d in datas]
    anos = [d.split("/")[2][-2:] for d in datas]

    if len(set(meses)) == 1 and len(set(anos)) == 1:
        mes = meses[0]
        ano2 = anos[0]
        dias = [d.split("/")[0] for d in datas]

        if len(dias) == 2:
            return f"'{dias[0]} e {dias[1]}/{mes}/{ano2}"

        return f"'{', '.join(dias[:-1])} e {dias[-1]}/{mes}/{ano2}"

    grupos = []
    grupo_atual = {"mes": None, "ano2": None, "dias": []}

    for data in datas:
        dia, mes, ano = data.split("/")
        ano2 = ano[-2:]

        if grupo_atual["mes"] == mes and grupo_atual["ano2"] == ano2:
            grupo_atual["dias"].append(dia)
        else:
            if grupo_atual["dias"]:
                grupos.append(grupo_atual)
            grupo_atual = {"mes": mes, "ano2": ano2, "dias": [dia]}

    if grupo_atual["dias"]:
        grupos.append(grupo_atual)

    blocos = []
    for i, g in enumerate(grupos):
        dias_txt = ", ".join(g["dias"])
        if i == len(grupos) - 1:
            blocos.append(f"{dias_txt}/{g['mes']}/{g['ano2']}")
        else:
            blocos.append(f"{dias_txt}/{g['mes']}")

    if len(blocos) == 2:
        return f"'{blocos[0]} e {blocos[1]}"

    return f"'{', '.join(blocos[:-1])} e {blocos[-1]}"


def formatar_data_curta(data_completa):
    if not data_completa:
        return ""
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", data_completa)
    if not m:
        return data_completa
    return f"{m.group(1)}/{m.group(2)}/{m.group(3)[-2:]}"


def extrair_dias_para_nome(data_texto):
    datas = reconstruir_datas_completas(data_texto)
    if not datas:
        return ""
    dias = [d.split("/")[0] for d in datas]
    if len(dias) == 1:
        return dias[0]
    return " ".join(dias[:2])


# =========================================
# CATEGORIAS
# =========================================
def aplicar_maiusculas_niveis(texto):
    texto = re.sub(
        r"\b([a-z])\+([a-z])\b",
        lambda m: f"{m.group(1).upper()}+{m.group(2).upper()}",
        texto,
        flags=re.IGNORECASE
    )

    texto = re.sub(
        r"\b(a|b|c|d)\b",
        lambda m: m.group(1).upper(),
        texto,
        flags=re.IGNORECASE
    )

    return texto


def normalizar_categoria_individual(cat):
    cat = limpar_espacos(cat)
    if not cat:
        return ""

    cat = cat.lower()
    cat = aplicar_maiusculas_niveis(cat)

    return cat


def padronizar_categorias(texto):
    texto = str(texto).replace("Categorias:", "").strip()

    if not texto:
        return "não encontrado"

    texto = re.sub(r"\s+\+\s+", ", ", texto)
    texto = re.sub(r"\s*/\s*", ", ", texto)
    texto = re.sub(r"\s*;\s*", ", ", texto)
    texto = re.sub(r"\s+[–-]\s+", ", ", texto)
    texto = re.sub(r"\s+e\s+", ", ", texto, flags=re.IGNORECASE)

    partes = [p.strip() for p in texto.split(",") if p.strip()]

    if not partes:
        return "não encontrado"

    partes = [normalizar_categoria_individual(p) for p in partes if p]

    if not partes:
        return "não encontrado"

    partes[0] = partes[0][:1].upper() + partes[0][1:] if partes[0] else partes[0]

    if len(partes) == 1:
        return partes[0]

    return ", ".join(partes[:-1]) + " e " + partes[-1]


# =========================================
# CONTATO
# =========================================
def normalizar_contato(contato):
    contato = limpar_espacos(contato)
    if not contato:
        return "não encontrado"

    telefone = re.search(r"\(?\d{2}\)?\s*\d{4,5}[-\s]?\d{4}", contato)
    if telefone:
        numeros = re.sub(r"\D", "", telefone.group(0))
        if len(numeros) == 11:
            return f"({numeros[:2]}) {numeros[2:7]}-{numeros[7:]}"
        if len(numeros) == 10:
            return f"({numeros[:2]}) {numeros[2:6]}-{numeros[6:]}"
        return telefone.group(0)

    instagram = re.search(r"@\w[\w\.]*", contato)
    if instagram:
        return instagram.group(0)

    return contato


# =========================================
# EXTRAÇÃO DE CAMPOS
# =========================================
def extrair_campos_confirmados(texto):
    data = re.search(r"Data:\s*(.*)", texto, re.IGNORECASE)
    torneio = re.search(r"Torneio:\s*(.*)", texto, re.IGNORECASE)
    cidade = re.search(r"Cidade.*:\s*(.*)", texto, re.IGNORECASE)
    local = re.search(r"Local:\s*(.*)", texto, re.IGNORECASE)
    categorias = re.search(r"Categorias:\s*(.*)", texto, re.IGNORECASE)
    contato = re.search(r"Contato:\s*(.*)", texto, re.IGNORECASE)

    return {
        "data": limpar_espacos(data.group(1)) if data else "",
        "torneio": limpar_espacos(torneio.group(1)) if torneio else "",
        "cidade_uf": limpar_espacos(cidade.group(1)) if cidade else "",
        "local": limpar_espacos(local.group(1)) if local else "",
        "categorias": limpar_espacos(categorias.group(1)) if categorias else "",
        "contato": limpar_espacos(contato.group(1)) if contato else "",
    }


def montar_mensagem(texto):
    campos = extrair_campos_confirmados(texto)

    data_visual = normalizar_data_visual_ant(campos["data"])
    cidade_uf = normalizar_cidade_uf(campos["cidade_uf"])
    categorias = padronizar_categorias(campos["categorias"])
    contato = normalizar_contato(campos["contato"])

    data_final_msg = data_visual if data_visual else "não encontrado"
    torneio_final = campos["torneio"] if campos["torneio"] else "não encontrado"
    cidade_final = cidade_uf if cidade_uf else "não encontrado"
    local_final = campos["local"] if campos["local"] else "não encontrado"

    return f"""Data: {data_final_msg}
Torneio: {torneio_final}
Cidade/ES: {cidade_final}
Local: {local_final}
Categorias: {categorias}
Contato: {contato}"""


# =========================================
# PROCESSA CALLBACK OAUTH ANTES DA UI
# =========================================
processar_callback_oauth_drive()


# =========================================
# UI
# =========================================
st.title("🏆 APP ANT")

aba1, aba2 = st.tabs(["Pré-análise do post", "Registro final do torneio"])

with aba1:
    st.subheader("Tela 1 — Pré-análise do post")
    st.write("Modo 1 print = 1 torneio (texto > arte) ativado.")

    st.divider()

    print_principal = st.file_uploader(
        "Upload do PRINT principal",
        type=["jpg", "jpeg", "png"],
        key="print_principal"
    )

    prints_adicionais = st.file_uploader(
        "Uploads adicionais (opcional)",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key="prints_adicionais"
    )

    informacao_complementar = st.text_area(
        "Texto complementar / legenda / observações (opcional)",
        placeholder="Ex.: Local: Arena Verão | Contato: @arenaverao | Cidade: Sorocaba/SP",
        height=120,
        key="info_complementar"
    )

    st.divider()

    if st.button("Extrair informações", key="btn_extrair"):
        if print_principal is None:
            st.error("Envie o print principal.")
        else:
            imagem = Image.open(print_principal)
            st.image(imagem, use_container_width=True)

            st.write("Analisando imagem...")

            prompt = f"""
Você está operando no modo fixo: 1 print = 1 torneio.

Extraia apenas UM torneio.

Prioridade obrigatória das fontes:
1. Texto complementar do usuário
2. Imagens enviadas

Se houver conflito, o texto complementar prevalece.

Extraia estes campos:
- Data
- Torneio
- Cidade/ES
- Local
- Categorias
- Contato

Regras obrigatórias:
- Se houver telefone ou WhatsApp, ele tem prioridade como contato.
- Se não houver telefone, use @perfil do Instagram.
- Se um campo não for encontrado, escreva: não encontrado.
- Não invente informações.
- Não una dois torneios.
- Mantenha nomes próprios como estão.

Texto complementar do usuário:
{informacao_complementar if informacao_complementar.strip() else "nenhum"}

Responda exatamente neste formato:

Data:
Torneio:
Cidade/ES:
Local:
Categorias:
Contato:
"""

            imagens = [print_principal] + (prints_adicionais if prints_adicionais else [])
            conteudo = [{"type": "input_text", "text": prompt}]

            for img in imagens:
                conteudo.append(
                    {
                        "type": "input_image",
                        "image_url": imagem_para_data_url(img)
                    }
                )

            response = client.responses.create(
                model="gpt-5",
                input=[{"role": "user", "content": conteudo}]
            )

            resultado = response.output_text
            mensagem = montar_mensagem(resultado)

            st.divider()
            st.subheader("Mensagem pronta")

            st.text_area(
                "Copie e envie ao organizador",
                value=mensagem,
                height=250,
                key="mensagem_pronta"
            )

with aba2:
    st.subheader("Tela 2 — Registro final do torneio")
    st.write("Cole o texto confirmado pelo organizador e prepare a linha final para a planilha da macro.")

    st.divider()

    st.markdown("### 1. Texto confirmado")

    texto_confirmado = st.text_area(
        "Cole aqui o texto confirmado pelo organizador",
        height=220,
        key="texto_confirmado"
    )

    st.divider()

    st.markdown("### 2. Flyer final")

    flyer_final = st.file_uploader(
        "Upload do FLYER final",
        type=["jpg", "jpeg", "png"],
        key="flyer_final"
    )

    st.divider()

    st.markdown("### 3. Organização da agenda")

    agenda = st.selectbox(
        "Agenda",
        ["", "SUL", "NORTE"],
        key="agenda_final"
    )

    meses = [
        "",
        "1. Janeiro",
        "2. Fevereiro",
        "3. Março",
        "4. Abril",
        "5. Maio",
        "6. Junho",
        "7. Julho",
        "8. Agosto",
        "9. Setembro",
        "10. Outubro",
        "11. Novembro",
        "12. Dezembro"
    ]

    mes_1 = st.selectbox("Mês principal", meses, key="mes_1")

    virada_mes = st.checkbox("Torneio em virada de mês?", key="virada_mes")

    mes_2 = ""
    if virada_mes:
        mes_2 = st.selectbox("Segundo mês", meses, key="mes_2")

    st.divider()

    st.markdown("### 3.1 Conexão com Google Drive")

    if drive_conectado():
        st.success("Google Drive conectado.")
        if st.button("Desconectar Google Drive", key="btn_desconectar_drive"):
            desconectar_drive_usuario()
            st.rerun()
    else:
        st.warning("Google Drive ainda não conectado.")
        url_autorizacao = gerar_url_autorizacao_drive()
        st.link_button("Conectar Google Drive", url_autorizacao)

    st.divider()

    st.markdown("### 4. Pré-visualização da linha da macro")

    campos = extrair_campos_confirmados(texto_confirmado)

    data_evento_visual = normalizar_data_visual_ant(campos["data"])
    torneio = campos["torneio"]
    cidade_uf = normalizar_cidade_uf(campos["cidade_uf"])
    local_evento = campos["local"]
    categorias = padronizar_categorias(campos["categorias"])
    contato = normalizar_contato(campos["contato"])

    cidade, uf, estado_extenso = separar_cidade_uf(cidade_uf)
    data_inicial_completa, data_final_completa = extrair_data_inicial_final(campos["data"])
    data_inicial = formatar_data_curta(data_inicial_completa)
    data_final = formatar_data_curta(data_final_completa)

    nome_arquivo = gerar_nome_arquivo(uf, campos["data"], cidade)

    linha_macro = [
        "",
        data_evento_visual,
        data_inicial,
        data_final,
        torneio,
        cidade_uf,
        estado_extenso,
        local_evento,
        categorias,
        contato,
        ""
    ]

    linha_macro_preview = {
        "Nº": "",
        "Data": data_evento_visual,
        "Data inicial": data_inicial,
        "Data final": data_final,
        "Torneio": torneio,
        "Cidade": cidade_uf,
        "Estado": estado_extenso,
        "Local": local_evento,
        "Categorias": categorias,
        "Contato": contato,
        "Status": ""
    }

    st.write("**Nome sugerido do arquivo:**", nome_arquivo if nome_arquivo else "-")

    df_preview = pd.DataFrame([linha_macro_preview])
    st.dataframe(df_preview, use_container_width=True, hide_index=True)

    st.divider()

    erros = []

    if not texto_confirmado.strip():
        erros.append("Cole o texto confirmado.")
    if flyer_final is None:
        erros.append("Envie o flyer final.")
    if not agenda:
        erros.append("Selecione a agenda.")
    if not mes_1:
        erros.append("Selecione o mês principal.")
    if virada_mes and not mes_2:
        erros.append("Selecione o segundo mês.")
    if not data_evento_visual:
        erros.append("Não foi possível identificar a data.")
    if not torneio:
        erros.append("Não foi possível identificar o torneio.")
    if not cidade_uf:
        erros.append("Não foi possível identificar Cidade/ES.")
    if not estado_extenso:
        erros.append("Não foi possível identificar o estado por extenso.")
    if not local_evento:
        erros.append("Não foi possível identificar o local.")
    if not categorias or categorias == "não encontrado":
        erros.append("Não foi possível identificar as categorias.")
    if not contato or contato == "não encontrado":
        erros.append("Não foi possível identificar o contato.")
    if not nome_arquivo:
        erros.append("Não foi possível gerar o nome automático do flyer.")
    if not data_inicial:
        erros.append("Não foi possível identificar a data inicial.")
    if not data_final:
        erros.append("Não foi possível identificar a data final.")
    if not drive_conectado():
        erros.append("Conecte o Google Drive antes de salvar.")

    salvamento_atual_fingerprint = gerar_fingerprint_salvamento(
        texto_confirmado=texto_confirmado,
        agenda=agenda,
        mes_1=mes_1,
        mes_2=mes_2,
        flyer_final=flyer_final
    )

    if st.button("Validar linha final", key="btn_validar_linha_final"):
        if erros:
            st.error("A linha final ainda não está pronta.")
            for erro in erros:
                st.write(f"- {erro}")
        else:
            st.success("Linha validada com sucesso.")

    if st.button("Salvar na Google Sheet e no Drive", key="btn_salvar_completo"):
        if erros:
            st.error("Não foi possível salvar porque ainda há pendências.")
            for erro in erros:
                st.write(f"- {erro}")
        elif st.session_state["ultimo_salvamento_fingerprint"] == salvamento_atual_fingerprint:
            st.warning("Este torneio já foi salvo nesta sessão. Altere algum dado antes de tentar salvar novamente.")
        else:
            client_gs = None
            try:
                client_gs = conectar_gsheet()
                planilha = obter_planilha_por_agenda(client_gs, agenda)

                salvar_linha_na_aba(planilha, mes_1, linha_macro)
                if virada_mes and mes_2 and mes_2 != mes_1:
                    salvar_linha_na_aba(planilha, mes_2, linha_macro)

                nome_flyer_final = gerar_nome_flyer(flyer_final, nome_arquivo)

                drive_service = conectar_drive_usuario()

                pasta_flyers_mes_1 = obter_id_pasta_flyers(mes_1)
                flyer_upload_1 = upload_arquivo_drive(
                    drive_service,
                    flyer_final,
                    pasta_flyers_mes_1,
                    nome_arquivo=nome_flyer_final
                )

                st.write("Flyer salvo em:", mes_1)
                st.write("Nome no Drive:", flyer_upload_1.get("name"))
                st.write("ID do arquivo:", flyer_upload_1.get("id"))

                if virada_mes and mes_2 and mes_2 != mes_1:
                    pasta_flyers_mes_2 = obter_id_pasta_flyers(mes_2)
                    flyer_upload_2 = upload_arquivo_drive(
                        drive_service,
                        flyer_final,
                        pasta_flyers_mes_2,
                        nome_arquivo=nome_flyer_final
                    )

                    st.write("Flyer também salvo em:", mes_2)
                    st.write("Nome no Drive (2º mês):", flyer_upload_2.get("name"))
                    st.write("ID do arquivo (2º mês):", flyer_upload_2.get("id"))

                registrar_log(
                    client_gs=client_gs,
                    torneio=torneio,
                    cidade=cidade_uf,
                    data_evento=data_evento_visual,
                    agenda=agenda,
                    mes_1=mes_1,
                    mes_2=mes_2,
                    nome_flyer=nome_flyer_final,
                    status="SUCESSO",
                    erro=""
                )

                st.session_state["ultimo_salvamento_fingerprint"] = salvamento_atual_fingerprint

                st.success("Linha salva na Google Sheet, flyer enviado ao Google Drive e LOG registrado com sucesso.")

            except Exception as e:
                if client_gs is not None:
                    try:
                        registrar_log(
                            client_gs=client_gs,
                            torneio=torneio,
                            cidade=cidade_uf,
                            data_evento=data_evento_visual,
                            agenda=agenda,
                            mes_1=mes_1,
                            mes_2=mes_2,
                            nome_flyer=flyer_final.name if flyer_final else "",
                            status="ERRO",
                            erro=repr(e)
                        )
                    except Exception:
                        pass

                st.error("Ocorreu um erro ao salvar na Google Sheet e/ou no Google Drive.")
                st.code(repr(e))
                
                