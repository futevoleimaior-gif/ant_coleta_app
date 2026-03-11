import os
import io
import base64
import re
import json
import hashlib
import unicodedata
from datetime import datetime

import requests
import streamlit as st
import pandas as pd
import gspread

from openai import OpenAI
from PIL import Image

from urllib.parse import urlencode

from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google.oauth2.credentials import Credentials as UserCredentials

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

FLYERS_JANEIRO_FAZER = st.secrets.get("FLYERS_JANEIRO_FAZER", "")
FLYERS_FEVEREIRO_FAZER = st.secrets.get("FLYERS_FEVEREIRO_FAZER", "")
FLYERS_MARCO_FAZER = st.secrets.get("FLYERS_MARCO_FAZER", "")
FLYERS_ABRIL_FAZER = st.secrets.get("FLYERS_ABRIL_FAZER", "")
FLYERS_MAIO_FAZER = st.secrets.get("FLYERS_MAIO_FAZER", "")
FLYERS_JUNHO_FAZER = st.secrets.get("FLYERS_JUNHO_FAZER", "")
FLYERS_JULHO_FAZER = st.secrets.get("FLYERS_JULHO_FAZER", "")
FLYERS_AGOSTO_FAZER = st.secrets.get("FLYERS_AGOSTO_FAZER", "")
FLYERS_SETEMBRO_FAZER = st.secrets.get("FLYERS_SETEMBRO_FAZER", "")
FLYERS_OUTUBRO_FAZER = st.secrets.get("FLYERS_OUTUBRO_FAZER", "")
FLYERS_NOVEMBRO_FAZER = st.secrets.get("FLYERS_NOVEMBRO_FAZER", "")
FLYERS_DEZEMBRO_FAZER = st.secrets.get("FLYERS_DEZEMBRO_FAZER", "")

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

if "drive_token_carregado_persistencia" not in st.session_state:
    st.session_state["drive_token_carregado_persistencia"] = False


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

GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URI = "https://oauth2.googleapis.com/revoke"

NOME_ABA_CONFIG = "CONFIG_APP"
CHAVE_TOKEN_DRIVE = "DRIVE_TOKEN_INFO"


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
    try:
        info = dict(st.secrets["gcp_service_account"])
        return ServiceAccountCredentials.from_service_account_info(
            info,
            scopes=SCOPES_SHEETS
        )
    except Exception:
        pass

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


def obter_aba_config(client_gs):
    planilha_log = obter_planilha_log(client_gs)

    try:
        aba = planilha_log.worksheet(NOME_ABA_CONFIG)
    except Exception:
        aba = planilha_log.add_worksheet(title=NOME_ABA_CONFIG, rows=50, cols=2)
        aba.update("A1:B1", [["chave", "valor"]])

    valores = aba.get("A1:B2")
    if not valores:
        aba.update("A1:B1", [["chave", "valor"]])
    else:
        primeira_linha = valores[0]
        if len(primeira_linha) < 2 or primeira_linha[0] != "chave" or primeira_linha[1] != "valor":
            aba.update("A1:B1", [["chave", "valor"]])

    return aba


def buscar_linha_por_chave(aba, chave):
    registros = aba.get_all_values()
    for idx, linha in enumerate(registros[1:], start=2):
        if linha and len(linha) >= 1 and linha[0] == chave:
            return idx
    return None


def carregar_token_drive_persistido():
    try:
        client_gs = conectar_gsheet()
        aba = obter_aba_config(client_gs)
        registros = aba.get_all_values()

        for linha in registros[1:]:
            if len(linha) >= 2 and linha[0] == CHAVE_TOKEN_DRIVE and linha[1].strip():
                return json.loads(linha[1])

    except Exception:
        return None

    return None


def salvar_token_drive_persistido(token_info):
    client_gs = conectar_gsheet()
    aba = obter_aba_config(client_gs)

    valor_json = json.dumps(token_info, ensure_ascii=False)
    linha_existente = buscar_linha_por_chave(aba, CHAVE_TOKEN_DRIVE)

    if linha_existente:
        aba.update(f"A{linha_existente}:B{linha_existente}", [[CHAVE_TOKEN_DRIVE, valor_json]])
    else:
        aba.append_row([CHAVE_TOKEN_DRIVE, valor_json], value_input_option="RAW")


def limpar_token_drive_persistido():
    try:
        client_gs = conectar_gsheet()
        aba = obter_aba_config(client_gs)
        linha_existente = buscar_linha_por_chave(aba, CHAVE_TOKEN_DRIVE)

        if linha_existente:
            aba.update(f"A{linha_existente}:B{linha_existente}", [[CHAVE_TOKEN_DRIVE, ""]])
    except Exception:
        pass


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
# GOOGLE DRIVE (OAuth WEB MANUAL)
# =========================================
def gerar_state_seguro():
    base = f"{APP_SECRET_KEY}-{datetime.now().timestamp()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def gerar_url_autorizacao_drive():
    state = gerar_state_seguro()
    st.session_state["drive_oauth_state"] = state

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES_DRIVE_OAUTH),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }

    return f"{GOOGLE_AUTH_URI}?{urlencode(params)}"


def trocar_code_por_token(code):
    payload = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }

    response = requests.post(GOOGLE_TOKEN_URI, data=payload, timeout=30)
    try:
        data = response.json()
    except Exception:
        data = {"raw_text": response.text}

    if response.status_code != 200:
        raise RuntimeError(f"Falha ao obter token: {data}")

    return data


def renovar_token_google(refresh_token):
    payload = {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    response = requests.post(GOOGLE_TOKEN_URI, data=payload, timeout=30)
    try:
        data = response.json()
    except Exception:
        data = {"raw_text": response.text}

    if response.status_code != 200:
        raise RuntimeError(f"Falha ao renovar token: {data}")

    return data


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
        token_data = trocar_code_por_token(code)

        token_info = {
            "token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "token_uri": GOOGLE_TOKEN_URI,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "scopes": SCOPES_DRIVE_OAUTH,
        }

        st.session_state["drive_token_info"] = token_info
        salvar_token_drive_persistido(token_info)

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

    if not token_info.get("token"):
        return None

    creds = UserCredentials(
        token=token_info.get("token"),
        refresh_token=token_info.get("refresh_token"),
        token_uri=token_info.get("token_uri"),
        client_id=token_info.get("client_id"),
        client_secret=token_info.get("client_secret"),
        scopes=token_info.get("scopes"),
    )

    if creds.expired and creds.refresh_token:
        try:
            novo_token = renovar_token_google(creds.refresh_token)

            token_atualizado = {
                "token": novo_token.get("access_token"),
                "refresh_token": token_info.get("refresh_token"),
                "token_uri": GOOGLE_TOKEN_URI,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "scopes": SCOPES_DRIVE_OAUTH,
            }

            st.session_state["drive_token_info"] = token_atualizado
            salvar_token_drive_persistido(token_atualizado)

            creds = UserCredentials(
                token=novo_token.get("access_token"),
                refresh_token=token_info.get("refresh_token"),
                token_uri=GOOGLE_TOKEN_URI,
                client_id=GOOGLE_CLIENT_ID,
                client_secret=GOOGLE_CLIENT_SECRET,
                scopes=SCOPES_DRIVE_OAUTH,
            )
        except Exception:
            st.session_state["drive_token_info"] = None
            limpar_token_drive_persistido()
            return None

    if not creds.valid:
        return None

    return creds


def carregar_token_persistido_na_sessao():
    if st.session_state.get("drive_token_carregado_persistencia"):
        return

    st.session_state["drive_token_carregado_persistencia"] = True

    if st.session_state.get("drive_token_info"):
        return

    token_info = carregar_token_drive_persistido()
    if token_info:
        st.session_state["drive_token_info"] = token_info


def drive_conectado():
    carregar_token_persistido_na_sessao()
    creds = obter_credenciais_drive_usuario()
    return creds is not None


def conectar_drive_usuario():
    carregar_token_persistido_na_sessao()
    creds = obter_credenciais_drive_usuario()
    if not creds:
        raise RuntimeError(
            "Google Drive não conectado. Clique em 'Conectar Google Drive' antes de salvar."
        )

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def desconectar_drive_usuario():
    token_info = st.session_state.get("drive_token_info")
    access_token = token_info.get("token") if token_info else None

    if access_token:
        try:
            requests.post(
                GOOGLE_REVOKE_URI,
                params={"token": access_token},
                headers={"content-type": "application/x-www-form-urlencoded"},
                timeout=15
            )
        except Exception:
            pass

    st.session_state["drive_token_info"] = None
    st.session_state["drive_oauth_state"] = None
    limpar_token_drive_persistido()
    limpar_query_params()


def obter_id_pasta_flyers(mes):
    mapa = {
        "1. Janeiro": FLYERS_JANEIRO_FAZER,
        "2. Fevereiro": FLYERS_FEVEREIRO_FAZER,
        "3. Março": FLYERS_MARCO_FAZER,
        "4. Abril": FLYERS_ABRIL_FAZER,
        "5. Maio": FLYERS_MAIO_FAZER,
        "6. Junho": FLYERS_JUNHO_FAZER,
        "7. Julho": FLYERS_JULHO_FAZER,
        "8. Agosto": FLYERS_AGOSTO_FAZER,
        "9. Setembro": FLYERS_SETEMBRO_FAZER,
        "10. Outubro": FLYERS_OUTUBRO_FAZER,
        "11. Novembro": FLYERS_NOVEMBRO_FAZER,
        "12. Dezembro": FLYERS_DEZEMBRO_FAZER,
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


def capitalizar_texto_padrao(texto):
    texto = limpar_espacos(texto)
    if not texto:
        return ""

    partes = re.split(r"(\s+|/|-)", texto)
    novas = []

    for parte in partes:
        if not parte or re.fullmatch(r"(\s+|/|-)", parte):
            novas.append(parte)
        else:
            parte = parte.lower()
            novas.append(parte[:1].upper() + parte[1:])

    return "".join(novas)


def imagem_para_data_url(uploaded_file):
    bytes_imagem = uploaded_file.getvalue()
    base64_image = base64.b64encode(bytes_imagem).decode("utf-8")
    mime_type = uploaded_file.type
    return f"data:{mime_type};base64,{base64_image}"


def normalizar_ano(ano_texto):
    ano_texto = str(ano_texto).strip()
    if not ano_texto:
        return ""
    if len(ano_texto) == 2:
        return f"20{ano_texto}"
    return ano_texto


def ano_4_para_2(ano_texto):
    ano_texto = normalizar_ano(ano_texto)
    return ano_texto[-2:] if ano_texto else ""


def gerar_nome_arquivo(uf, data_evento, cidade):
    if not uf or not data_evento or not cidade:
        return ""
    dias = extrair_dias_para_nome(data_evento)
    cidade_formatada = capitalizar_texto_padrao(cidade)
    return f"{uf} {dias} {cidade_formatada.strip()}"


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
    s = s.replace(", ", "/")
    s = s.replace(",", "/")

    if "/" not in s:
        return capitalizar_texto_padrao(s)

    partes = s.rsplit("/", 1)
    cidade = capitalizar_texto_padrao(limpar_espacos(partes[0]))
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
        uf = mapa_reverso.get(remover_acentos(uf).lower(), uf[:2].upper())

    return f"{cidade}/{uf}"


def normalizar_cidade_uf_tela2(cidade_uf):
    s = limpar_espacos(cidade_uf)
    if not s:
        return ""

    s = s.replace(" - ", "/")
    s = s.replace(" – ", "/")
    s = s.replace("\\", "/")
    s = s.replace(", ", "/")
    s = s.replace(",", "/")

    if "/" not in s:
        return s

    partes = s.rsplit("/", 1)
    cidade = limpar_espacos(partes[0])
    uf = limpar_espacos(partes[1]).upper()

    return f"{cidade}/{uf}"


def separar_cidade_uf(cidade_uf):
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
    s = limpar_espacos(str(data_texto).replace("'", ""))
    padrao = r"(\d{1,2})(?:/(\d{1,2}))?(?:/(\d{2,4}))?"
    return re.findall(padrao, s)


def reconstruir_datas_completas(data_texto):
    partes = extrair_partes_data(data_texto)
    if not partes:
        return []

    ano_atual = str(datetime.now().year)

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

    for r in registros:
        if not r["ano"]:
            r["ano"] = ano_atual

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
    s = limpar_espacos(str(data_texto))
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

    blocos = []
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
    torneio = capitalizar_texto_padrao(campos["torneio"])
    local = capitalizar_texto_padrao(campos["local"])

    data_final_msg = data_visual if data_visual else "não encontrado"
    torneio_final = torneio if torneio else "não encontrado"
    cidade_final = cidade_uf if cidade_uf else "não encontrado"
    local_final = local if local else "não encontrado"

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
carregar_token_persistido_na_sessao()


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

            ano_corrente_2 = str(datetime.now().year)[-2:]

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
- Padronize a data no formato ANT. Exemplos:
  10/04/{ano_corrente_2}
  10 e 11/04/{ano_corrente_2}
  10, 11 e 12/04/{ano_corrente_2}
  30, 31/03 e 01/04/{ano_corrente_2}
- Se o ano não estiver informado, assuma o ano corrente ({ano_corrente_2}).
- Cidade/ES deve sempre estar no formato Cidade/UF.
- No nome do torneio, cidade e local, use capitalização padronizada:
  primeira letra de cada palavra maiúscula e demais minúsculas.

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
    cidade_uf = normalizar_cidade_uf_tela2(campos["cidade_uf"])
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
                