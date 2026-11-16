# -*- coding: utf-8 -*-
"""
radar_core.py — Núcleo del Radar Agronómico Global

Recolección multi-fuente (becas, empleo, fondos), filtro estricto de agronomía,
clasificación por nivel, extracción de fechas de apertura/cierre, nivel de
confianza, ranking y correo semanal.

No depende de Streamlit: lo usan app.py (interfaz) y alerta_semanal.py (correo).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import smtplib
import ssl
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from html import escape
from typing import Optional
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ════════════════════════════════════════════════════════════════════
# 1. HTTP
# ════════════════════════════════════════════════════════════════════
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 RadarAgronomico/2.0"
    ),
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8,pt;q=0.7",
}
TIMEOUT = 15


def _crear_sesion() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    retry = Retry(total=2, backoff_factor=0.6, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET", "POST"))
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s


SESION = _crear_sesion()


def http_get(url: str, **kw) -> requests.Response:
    r = SESION.get(url, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


def hoy_co() -> date:
    """Fecha actual en Colombia (UTC-5)."""
    return datetime.now(timezone(timedelta(hours=-5))).date()


# ════════════════════════════════════════════════════════════════════
# 2. Utilidades de texto
# ════════════════════════════════════════════════════════════════════
def norm(t: str) -> str:
    """Minúsculas y sin tildes, conservando la longitud (para usar índices)."""
    out = []
    for ch in t or "":
        b = unicodedata.normalize("NFD", ch)[0]
        low = b.lower()
        out.append(low if len(low) == 1 else b)
    return "".join(out)


def limpiar_html(h: str) -> str:
    return BeautifulSoup(h or "", "html.parser").get_text(" ", strip=True)


MES_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def fmt_fecha(d: Optional[date]) -> str:
    return f"{d.day} {MES_ES[d.month - 1]} {d.year}" if d else "—"


def _url_canonica(u: str) -> str:
    if not u:
        return ""
    p = urlparse(u)
    q = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith(("utm_", "fbclid", "gclid"))]
    host = p.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return f"{host}{p.path.rstrip('/')}" + (("?" + urlencode(q)) if q else "")


def _es_institucional(url: str) -> bool:
    host = urlparse(url or "").netloc.lower().split(":")[0]
    partes = set(host.split("."))
    return bool(partes & {"edu", "gov", "gob", "int", "ac", "mil"})


# ════════════════════════════════════════════════════════════════════
# 3. Fechas de apertura y cierre
# ════════════════════════════════════════════════════════════════════
MESES = {
    "enero": 1, "ene": 1, "january": 1, "jan": 1, "janeiro": 1,
    "febrero": 2, "feb": 2, "february": 2, "fevereiro": 2, "fev": 2,
    "marzo": 3, "mar": 3, "march": 3, "marco": 3,
    "abril": 4, "abr": 4, "april": 4, "apr": 4,
    "mayo": 5, "may": 5, "maio": 5,
    "junio": 6, "jun": 6, "june": 6, "junho": 6,
    "julio": 7, "jul": 7, "july": 7, "julho": 7,
    "agosto": 8, "ago": 8, "august": 8, "aug": 8,
    "septiembre": 9, "setiembre": 9, "sep": 9, "sept": 9, "september": 9, "setembro": 9, "set": 9,
    "octubre": 10, "oct": 10, "october": 10, "outubro": 10, "out": 10,
    "noviembre": 11, "nov": 11, "november": 11, "novembro": 11,
    "diciembre": 12, "dic": 12, "december": 12, "dec": 12, "dezembro": 12, "dez": 12,
}
_MES = "|".join(sorted(MESES, key=len, reverse=True))
_SUF = r"(?:st|nd|rd|th|º|°|o)?"
RE_DMY = re.compile(rf"\b(\d{{1,2}}){_SUF}\s*(?:de\s+|of\s+)?({_MES})\b\.?,?\s*(?:de\s+|del\s+)?(\d{{4}})\b")
RE_MDY = re.compile(rf"\b({_MES})\b\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b")
RE_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
RE_NUM = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](20\d{2})\b")
# "del 1 de marzo al 30 de abril de 2027" / "1 to 15 March 2027"
RE_RANGO_CORTO = re.compile(
    rf"\b(\d{{1,2}})(?:\s*(?:de\s+)?({_MES}))?\s*(?:al|a|-|–|—|to|hasta(?: el)?)\s*"
    rf"(\d{{1,2}})\s*(?:de\s+|of\s+)?({_MES})\b\.?,?\s*(?:de\s+|del\s+)?(\d{{4}})\b"
)
_RE_CONECTOR = re.compile(r"(?:a|al|to|hasta|until|through|thru|-|–|—|y|and|ate|e)")

_KW_FIN_FUERTE = re.compile(
    r"(deadline|closing date|closes?|closing|apply (?:by|before)|due (?:date|by)|no later than|last date|"
    r"fecha (?:limite|de cierre|maxima)|cierre|cierra|plazo|vence|vencimiento|prazo|encerramento|encerra|expires?)\b")
_KW_FIN_GEN = re.compile(r"(until|hasta(?: el)?|ate|before|by|limite)\b")
_KW_INI_ESP = re.compile(
    r"(opens?|opening|open (?:from|on)|applications? (?:open|start|begin)|apertura|abre|abren|abierta desde|"
    r"inscripciones abiertas|inscricoes abertas|abertura)\b")
_KW_INI_GEN = re.compile(r"(desde(?: el)?|from|a partir del?|begins?|starting)\b")
_KW_APLIC = re.compile(
    r"(postulac\w*|inscri\w*|application\w*|apply|applic\w*|convocatoria|registration|register|admission|"
    r"admisi\w*|solicitud\w*|candidatur\w*|abiert\w*|abren|open\w*|recepcion|receipt)")


def _mk(y: int, m: int, d: int, hoy: date) -> Optional[date]:
    try:
        f = date(y, m, d)
    except ValueError:
        return None
    return f if hoy.year - 1 <= f.year <= hoy.year + 2 else None


def _fechas_en(t: str, hoy: date) -> list[tuple[int, int, date]]:
    """Todas las fechas completas (con año) del texto normalizado: (inicio, fin, fecha)."""
    res = []
    for m in RE_DMY.finditer(t):
        res.append((m.start(), m.end(), _mk(int(m[3]), MESES[m[2]], int(m[1]), hoy)))
    for m in RE_MDY.finditer(t):
        res.append((m.start(), m.end(), _mk(int(m[3]), MESES[m[1]], int(m[2]), hoy)))
    for m in RE_ISO.finditer(t):
        res.append((m.start(), m.end(), _mk(int(m[1]), int(m[2]), int(m[3]), hoy)))
    for m in RE_NUM.finditer(t):
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        d, mo = (a, b) if (a > 12 or b <= 12) else (b, a)  # por defecto dd/mm (Latinoamérica)
        res.append((m.start(), m.end(), _mk(y, mo, d, hoy)))
    res = [r for r in res if r[2]]
    res.sort(key=lambda r: r[0])
    limpio, ult = [], -1
    for s, e, d in res:
        if s >= ult:
            limpio.append((s, e, d))
            ult = e
    return limpio


def extraer_fechas(texto: str, hoy: Optional[date] = None) -> tuple[Optional[date], Optional[date]]:
    """Devuelve (inicio_postulacion, cierre_postulacion) detectadas por contexto. None si no hay certeza."""
    hoy = hoy or hoy_co()
    t = norm(texto)
    ini, fin = [], []

    # 1) Rangos cortos con contexto de postulación
    ocupados = []
    for m in RE_RANGO_CORTO.finditer(t):
        if not _KW_APLIC.search(t[max(0, m.start() - 70):m.start()] + " " + t[m.end():m.end() + 25]):
            continue
        d1, m1, d2, m2, y = int(m[1]), m[2], int(m[3]), m[4], int(m[5])
        mes2 = MESES[m2]
        mes1 = MESES[m1] if m1 else mes2
        y1 = y - 1 if mes1 > mes2 else y
        a, b = _mk(y1, mes1, d1, hoy), _mk(y, mes2, d2, hoy)
        if a and b and a <= b:
            ini.append(a)
            fin.append(b)
            ocupados.append((m.start(), m.end()))

    # 2) Fechas completas
    fechas = [f for f in _fechas_en(t, hoy)
              if not any(o[0] <= f[0] < o[1] for o in ocupados)]
    prev_fin, i = 0, 0
    while i < len(fechas):
        s, e, d = fechas[i]
        # rango de dos fechas completas (solo si hay contexto de postulación)
        if i + 1 < len(fechas):
            s2, _, d2 = fechas[i + 1]
            medio = t[e:s2].strip(" ,;:.")
            ventana = t[max(prev_fin, s - 70):s]
            if len(medio) <= 8 and _RE_CONECTOR.fullmatch(medio) and d2 >= d and _KW_APLIC.search(ventana):
                ini.append(d)
                fin.append(d2)
                prev_fin = fechas[i + 1][1]
                i += 2
                continue
        win = t[max(prev_fin, s - 55):s]
        aplic = bool(_KW_APLIC.search(t[max(0, s - 120):s]))  # contexto más amplio solo para palabras genéricas
        pos_fin = max([m.end() for m in _KW_FIN_FUERTE.finditer(win)] +
                      ([m.end() for m in _KW_FIN_GEN.finditer(win)] if aplic else []) + [-1])
        pos_ini = max([m.end() for m in _KW_INI_ESP.finditer(win)] +
                      ([m.end() for m in _KW_INI_GEN.finditer(win)] if aplic else []) + [-1])
        if pos_fin > pos_ini:
            fin.append(d)
        elif pos_ini > pos_fin:
            ini.append(d)
        prev_fin = e
        i += 1

    cierre = None
    if fin:
        futuras = sorted(d for d in fin if d >= hoy)
        cierre = futuras[0] if futuras else max(fin)
    inicio = min(ini) if ini else None
    if inicio and cierre and inicio > cierre:
        inicio = None
    return inicio, cierre


def estado(inicio: Optional[date], cierre: Optional[date], hoy: Optional[date] = None):
    """(etiqueta, días_para_cierre, días_para_apertura)"""
    hoy = hoy or hoy_co()
    if inicio and inicio > hoy:
        return "Próxima apertura", ((cierre - hoy).days if cierre else None), (inicio - hoy).days
    if cierre:
        d = (cierre - hoy).days
        if d < 0:
            return "Cerrada", d, None
        return ("Cierra pronto" if d <= 14 else "Abierta"), d, None
    return "Sin fecha confirmada", None, None


# ════════════════════════════════════════════════════════════════════
# 4. Relevancia agronómica y líneas de investigación
# ════════════════════════════════════════════════════════════════════
_STRONG = re.compile(
    r"\b(agronom\w*|agricult\w*|agricol\w*|agropecuari\w*|agrari\w*|fitomejor\w*|plant breeding|"
    r"crop (?:science\w*|breeding|protection|production|improvement|genetic\w*|pathology)|fitopatolog\w*|"
    r"plant patholog\w*|phytopatholog\w*|horticult\w*|agroecolog\w*|soil (?:science|fertility|health|management|biology)|"
    r"ciencias? del suelo|fitotecni\w*|fitossanid\w*|melhoramento (?:de plantas|genetico)|plant science\w*|"
    r"plant physiolog\w*|fisiologia vegetal|genetica vegetal|plant genetic\w*|entomolog\w*|agroforest\w*|"
    r"poscosecha|post-?harvest|agrotecnolog\w*|agrobiotecnolog\w*|agribusiness|agronegoci\w*|fruticultur\w*|"
    r"viticultur\w*|agri-?food|agroalimentari\w*|seed (?:science|technology)|tecnologia de semillas)\b")
_WEAK = re.compile(
    r"\b(crops?|cultivos?|soils?|suelos?|seeds?|semillas?|plant\w*|irrigation|riego|fertiliz\w*|harvest|cosecha|"
    r"farm\w*|finca|rural|campesin\w*|food security|seguridad alimentaria|cacao|cocoa|coffee|cafe|palma|palm oil|"
    r"rice|arroz|maiz|maize|banano|banana|plantain|platano|potato|papa|sugarcane|cana de azucar)\b")
_NEG = re.compile(r"\b(software|developer|nurse|enfermer\w*|accountant|contador|abogado|lawyer|chef|driver|conductor)\b")


def relevancia_agro(titulo: str, texto: str) -> tuple[int, bool]:
    tn, xn = norm(titulo), norm(texto)
    st_t, st_x = len(_STRONG.findall(tn)), len(_STRONG.findall(xn))
    wk_t, wk_x = len(_WEAK.findall(tn)), len(_WEAK.findall(xn))
    score = min(100, st_t * 30 + min(st_x, 5) * 8 + min(wk_t, 3) * 8 + min(wk_x, 6) * 3)
    es_agro = st_t >= 1 or st_x >= 2 or (st_x >= 1 and wk_x >= 2) or wk_t >= 2
    if _NEG.search(tn) and st_t == 0:
        es_agro = False
    return score, es_agro


LINEAS = {
    "Fitomejoramiento y genética": dict(
        rx=r"fitomejor|plant breeding|crop breeding|mejoramiento genetico|melhoramento|genetica vegetal|plant genetic|genomic|"
           r"biotecnolog|biotechnolog|marcadores moleculares|molecular marker|germoplasma|germplasm|cultivar",
        es="fitomejoramiento", en="plant breeding", pt="melhoramento genético de plantas"),
    "Fitopatología": dict(
        rx=r"fitopatolog|plant patholog|phytopatholog|fitossanid|enfermedades de (?:las )?plantas|plant disease|nematod|"
           r"fungal|hongos|micolog|virolog",
        es="fitopatología", en="plant pathology", pt="fitopatologia"),
    "Suelos y nutrición vegetal": dict(
        rx=r"\bsuelos?\b|\bsoils?\b|fertilidad|fertility|nutricion vegetal|plant nutrition|edafolog|fertiliz|agroquimic",
        es="ciencia del suelo", en="soil science", pt="ciência do solo"),
    "Entomología y manejo de plagas": dict(
        rx=r"entomolog|plagas?\b|pests?\b|\bmip\b|\bipm\b|manejo integrado|integrated pest|biocontrol|control biologico",
        es="entomología agrícola", en="agricultural entomology", pt="entomologia agrícola"),
    "Fisiología vegetal y estrés": dict(
        rx=r"fisiolog\w* vegetal|plant physiolog|abiotic stress|estres abiotico|sequia|drought|fotosintes|photosynth",
        es="fisiología vegetal", en="plant physiology", pt="fisiologia vegetal"),
    "Agroecología y sostenibilidad": dict(
        rx=r"agroecolog|sostenib|sustainab|regenerativ|agroforest|organic farming|agricultura organica",
        es="agroecología", en="agroecology", pt="agroecologia"),
    "Poscosecha y calidad": dict(
        rx=r"poscosecha|post-?harvest|pos-?colheita|calidad del fruto|inocuidad|food safety",
        es="poscosecha", en="postharvest", pt="pós-colheita"),
    "Riego y recursos hídricos": dict(
        rx=r"riego|irrigation|recursos hidricos|water management|manejo del agua|hidrolog",
        es="riego agrícola", en="agricultural irrigation", pt="irrigação"),
    "Agricultura de precisión y digital": dict(
        rx=r"precision agri|agricultura de precision|remote sensing|sensores remotos|teledeteccion|drones?\b|digital agri|"
           r"agtech|machine learning|inteligencia artificial|\biot\b|fenotipado|phenotyping",
        es="agricultura de precisión", en="precision agriculture", pt="agricultura de precisão"),
    "Cambio climático y resiliencia": dict(
        rx=r"cambio climatico|climate change|climate[- ]smart|resilienc|adaptacion al|mitigacion",
        es="agricultura y cambio climático", en="climate-smart agriculture", pt="agricultura e mudanças climáticas"),
    "Semillas y recursos fitogenéticos": dict(
        rx=r"semillas?\b|\bseeds?\b|recursos fitogeneticos|plant genetic resources|bancos? de germoplasma|genebank",
        es="semillas recursos fitogenéticos", en="seed science plant genetic resources", pt="sementes recursos genéticos"),
    "Cultivos tropicales (café, cacao, palma, caña, arroz…)": dict(
        rx=r"cafe\b|coffee|cacao|cocoa|palma\b|palm oil|cana de azucar|sugarcane|arroz|rice\b|banano|platano|banana|"
           r"plantain|yuca|cassava|aguacate|avocado|maiz|maize|frijol|\bbeans?\b|papa\b|potato",
        es="cultivos tropicales café cacao", en="tropical crops coffee cocoa", pt="culturas tropicais"),
    "Economía y agronegocios": dict(
        rx=r"agronegoci|agribusiness|economia agr|agricultural economic|cadenas? de valor|value chain|politica agr",
        es="agronegocios", en="agribusiness", pt="agronegócio"),
}
_LINEAS_RX = {k: re.compile(v["rx"]) for k, v in LINEAS.items()}


def detectar_lineas(texto: str) -> list[str]:
    t = norm(texto)
    return [k for k, rx in _LINEAS_RX.items() if rx.search(t)]


# ════════════════════════════════════════════════════════════════════
# 5. Clasificación: nivel, cargo, país/región, financiamiento, elegibilidad
# ════════════════════════════════════════════════════════════════════
NIV_MAESTRIA, NIV_DOCT, NIV_POSTDOC = "Maestría", "Doctorado (PhD)", "Postdoctorado"
NIV_POSGRADO, NIV_CURSO, NIV_PASANTIA, NIV_OTRO = (
    "Posgrado / Especialización", "Curso / Entrenamiento", "Pasantía / Movilidad", "Otro")

_NIVELES = [
    (NIV_POSTDOC, re.compile(r"\b(post-?doc\w*|posdoc\w*|post-?doctor\w*|pos-?doutor\w*)\b")),
    (NIV_DOCT, re.compile(r"\b(ph\.?\s?d|doctorad\w*|doctorat\w*|doctoral|doutorad\w*|dphil)\b")),
    (NIV_MAESTRIA, re.compile(r"\b(master\w*|maestr\w*|mestrad\w*|m\.?sc|magister|mphil)\b")),
    (NIV_POSGRADO, re.compile(r"\b(especializaci\w*|specialization|specialisation|diplomad\w*|postgraduate|posgrado|postgrado|pos-?graduac\w*)\b")),
    (NIV_CURSO, re.compile(r"\b(cursos?|courses?|training|capacitaci\w*|workshop|taller|summer school|escuela de verano|webinar|bootcamp|certificat\w*|treinamento|diploma)\b")),
    (NIV_PASANTIA, re.compile(r"\b(internship|pasantia|intern|estancia|research stay|visiting|movilidad|mobility|exchange|intercambio|estagio|fellowship)\b")),
]


def clasificar_nivel(titulo: str, cuerpo: str = "") -> str:
    for ambito in (titulo, cuerpo[:500], cuerpo):
        n = norm(ambito)
        if not n:
            continue
        for nivel, rx in _NIVELES:
            if rx.search(n):
                return nivel
    return NIV_OTRO


_CARGOS = [
    ("Pasantía / Junior", r"pasante|practicante|trainee|\bintern\b|internship|junior|aprendiz|estagiari"),
    ("Docencia", r"docente|profesor|catedr|lecturer|professor|instructor"),
    ("Investigación", r"investigador|investigacion|research|scientist|cientifico|fitomejorador|breeder|laboratorio|i\+d"),
    ("Gerencia / Coordinación", r"gerente|director|coordinador|jefe|manager|head of|lider|supervisor|superintendente"),
    ("Técnico-comercial", r"comercial|ventas|sales|representante tecnico|promotor"),
    ("Asistencia técnica", r"asistente tecnico|asistencia tecnica|extension|extensionista|tecnico agricola|technical assistant|field technician|tecnico de campo|agronomo de campo"),
    ("Consultoría", r"consultor|consultant|asesor|advisor"),
    ("Cooperación / ONG", r"oficial de|officer|programme|proyecto|\bfao\b|iica|\bgiz\b|pnud|undp|\bong\b|\bngo\b"),
]
CARGOS = [c for c, _ in _CARGOS] + ["Agronomía (general)", "Empleador clave"]
_CARGOS_RX = [(c, re.compile(r)) for c, r in _CARGOS]


def clasificar_cargo(titulo: str) -> str:
    t = norm(titulo)
    for c, rx in _CARGOS_RX:
        if rx.search(t):
            return c
    return "Agronomía (general)"


_PAISES_DEF = [
    # (nombre, región, regex sobre texto normalizado)
    ("Colombia", "Colombia", r"colombia|bogota|medellin|palmira|bucaramanga|barranquilla|\bcali\b|agrosavia|cenicafe|cenicana|cenipalma"),
    ("Argentina", "Latinoamérica", r"argentina|buenos aires|\binta\b"),
    ("Bolivia", "Latinoamérica", r"bolivia"),
    ("Brasil", "Latinoamérica", r"brasil|brazil|sao paulo|embrapa|\bcapes\b|\bcnpq\b|piracicaba|vicosa|lavras"),
    ("Chile", "Latinoamérica", r"\bchile\b|santiago de chile"),
    ("Costa Rica", "Latinoamérica", r"costa rica|catie"),
    ("Cuba", "Latinoamérica", r"\bcuba\b"),
    ("Ecuador", "Latinoamérica", r"ecuador|\bquito\b|guayaquil"),
    ("El Salvador", "Latinoamérica", r"el salvador"),
    ("Guatemala", "Latinoamérica", r"guatemala"),
    ("Honduras", "Latinoamérica", r"honduras|zamorano"),
    ("México", "Latinoamérica", r"(?<!new )mexico|cimmyt|colpos|texcoco"),
    ("Nicaragua", "Latinoamérica", r"nicaragua"),
    ("Panamá", "Latinoamérica", r"panama"),
    ("Paraguay", "Latinoamérica", r"paraguay"),
    ("Perú", "Latinoamérica", r"\bperu\b"),
    ("República Dominicana", "Latinoamérica", r"republica dominicana"),
    ("Uruguay", "Latinoamérica", r"uruguay"),
    ("Venezuela", "Latinoamérica", r"venezuela"),
    ("EE. UU.", "Norteamérica", r"united states|\busa\b|\bu\.s\.|estados unidos|\beeuu\b|ee\.?\s?uu|usda|fulbright|cornell|purdue|uc davis|florida|iowa|texas"),
    ("Canadá", "Norteamérica", r"canada|canadian"),
    ("Alemania", "Europa", r"germany|alemania|deutschland|\bdaad\b"),
    ("Francia", "Europa", r"\bfrance\b|francia|campus france|eiffel"),
    ("España", "Europa", r"\bspain\b|espana|fundacion carolina|madrid|barcelona|valencia"),
    ("Italia", "Europa", r"\bitaly\b|italia"),
    ("Países Bajos", "Europa", r"netherlands|holland|paises bajos|wageningen|dutch"),
    ("Reino Unido", "Europa", r"united kingdom|\buk\b|england|scotland|reino unido|chevening"),
    ("Suecia", "Europa", r"sweden|suecia|swedish"),
    ("Noruega", "Europa", r"norway|noruega"),
    ("Dinamarca", "Europa", r"denmark|dinamarca"),
    ("Finlandia", "Europa", r"finland|finlandia"),
    ("Suiza", "Europa", r"switzerland|suiza|\beth\b"),
    ("Bélgica", "Europa", r"belgium|belgica"),
    ("Austria", "Europa", r"\baustria\b"),
    ("Irlanda", "Europa", r"\bireland\b|irlanda"),
    ("Polonia", "Europa", r"poland|polonia"),
    ("Hungría", "Europa", r"hungary|hungria|stipendium hungaricum"),
    ("Portugal", "Europa", r"portugal"),
    ("Rep. Checa", "Europa", r"czech|republica checa"),
    ("China", "Asia", r"\bchina\b|chinese|\bcsc\b"),
    ("Japón", "Asia", r"\bjapan\b|japon|\bmext\b|\bjica\b"),
    ("Corea del Sur", "Asia", r"korea|corea|\bgks\b|koica"),
    ("India", "Asia", r"\bindia\b"),
    ("Tailandia", "Asia", r"thailand|tailandia"),
    ("Malasia", "Asia", r"malaysia|malasia"),
    ("Filipinas", "Asia", r"philippines|filipinas|\birri\b"),
    ("Indonesia", "Asia", r"indonesia"),
    ("Taiwán", "Asia", r"taiwan"),
    ("Singapur", "Asia", r"singapore|singapur"),
    ("Vietnam", "Asia", r"vietnam"),
    ("Sudáfrica", "África", r"south africa|sudafrica"),
    ("Kenia", "África", r"\bkenya\b|kenia"),
    ("Etiopía", "África", r"ethiopia|etiopia"),
    ("Nigeria", "África", r"nigeria"),
    ("Ghana", "África", r"\bghana\b"),
    ("Ruanda", "África", r"rwanda|ruanda"),
    ("Uganda", "África", r"uganda"),
    ("Tanzania", "África", r"tanzania"),
    ("Egipto", "África", r"egypt|egipto"),
    ("Marruecos", "África", r"morocco|marruecos"),
    ("Australia", "Oceanía", r"australia"),
    ("Nueva Zelanda", "Oceanía", r"new zealand|nueva zelanda"),
    ("Latinoamérica (varios)", "Latinoamérica", r"latin america|latinoamerica|america latina|iberoamerica"),
]
_PAISES_RX = [(n, r, re.compile(x)) for n, r, x in _PAISES_DEF]
REGION_DE = {n: r for n, r, _ in _PAISES_DEF}
REGION_DE.update({"Global": "Global", "Remoto / Internacional": "Remoto", "Europa (varios)": "Europa",
                  "Asia (varios)": "Asia", "Latinoamérica": "Latinoamérica"})
REGIONES = ["Colombia", "Latinoamérica", "Norteamérica", "Europa", "Asia", "África", "Oceanía", "Remoto", "Global"]
_RE_REMOTO = re.compile(r"\b(remote|remoto|teletrabajo|home office|work from home|trabajo remoto|100% remot\w*)\b")


def detectar_pais(titulo: str, texto: str = "") -> tuple[str, str]:
    tn, xn = norm(titulo), norm(texto[:4000])
    if _RE_REMOTO.search(tn):
        return "Remoto / Internacional", "Remoto"
    mejor, puntos = None, 0
    for nombre, region, rx in _PAISES_RX:
        p = 5 * len(rx.findall(tn)) + len(rx.findall(xn))
        if p > puntos:
            mejor, puntos = (nombre, region), p
    if mejor:
        return mejor
    if _RE_REMOTO.search(xn[:800]):
        return "Remoto / Internacional", "Remoto"
    return "No especificado", "Global"


_FIN_COMPLETA = re.compile(
    r"fully[- ]funded|full (?:scholarship|funding)|beca completa|financiacion completa|financiamiento completo|"
    r"cobertura total|cubre (?:la )?matricula|tuition (?:waiver|fees? covered)|stipend|living allowance|monthly allowance|"
    r"manutencao|sostenimiento|assistantship|todos los gastos|all expenses|bolsa de")
_FIN_PARCIAL = re.compile(r"partial (?:scholarship|funding)|beca parcial|financiacion parcial|credito condonable|co-?financ")
FIN_COMPLETA, FIN_PARCIAL, FIN_ND = "Completa / con estipendio", "Parcial / crédito", "No especificado"


def detectar_financiamiento(texto: str) -> str:
    t = norm(texto)
    if _FIN_COMPLETA.search(t):
        return FIN_COMPLETA
    if _FIN_PARCIAL.search(t):
        return FIN_PARCIAL
    return FIN_ND


def evaluar_elegibilidad(texto_elegible: str, texto_total: str) -> str:
    e = norm(texto_elegible or "")
    abierta = re.compile(r"international|all countries|any countr|all nationalit|developing countr|low and middle|"
                         r"latin america|latinoam|worldwide|non-?eu|all students|todos los paises")
    if e:
        if "colombia" in e:
            return "✅ Colombia incluida"
        if abierta.search(e):
            return "✅ Abierta a internacionales (verifica Colombia)"
        return "⚠️ Lista específica de países: verifica Colombia"
    t = norm(texto_total)
    if re.search(r"colombia\w*.{0,60}(eligible|elegible|can apply|pueden)|(eligible|elegible|nationals? of).{0,80}colombia", t):
        return "✅ Menciona a Colombia"
    if re.search(r"international students|estudiantes internacionales|todos los paises|extranjeros", t):
        return "✅ Abierta a internacionales (verifica Colombia)"
    return "No indicada"


_FRAUDE = re.compile(r"western union|pago por adelantado|pay (?:a|an) (?:application )?fee|deposito previo|"
                     r"transferencia previa|inversion inicial|ganancias garantizadas|gana dinero rapido|money gram")


def extraer_requisitos(texto: str, n: int = 700) -> str:
    t = norm(texto)
    m = re.search(r"\b(eligibility|eligible|requirements?|who can apply|requisitos|elegibilidad|criterios de|"
                  r"perfil del|quem pode|requisitos para)\b", t)
    return re.sub(r"\s+", " ", texto[m.start(): m.start() + n]).strip() if m else ""


# ════════════════════════════════════════════════════════════════════
# 6. Modelo de datos
# ════════════════════════════════════════════════════════════════════
@dataclass
class Oportunidad:
    tipo: str                      # estudio | trabajo | fondo
    titulo: str
    url: str
    fuente: str
    institucion: str = ""
    pais: str = "No especificado"
    region: str = "Global"
    nivel: str = ""                # estudios
    cargo_tipo: str = ""           # trabajo / fondos
    inicio: Optional[date] = None  # apertura de postulaciones
    cierre: Optional[date] = None  # cierre de postulaciones
    publicado: Optional[date] = None
    financiamiento: str = FIN_ND
    elegibilidad: str = "No indicada"
    resumen: str = ""
    requisitos: str = ""
    texto: str = ""
    url_oficial: str = ""
    confianza: str = "Baja"        # Alta | Media | Baja
    verificacion: str = ""
    origen: str = "detectada"      # detectada | directorio
    lineas: list = field(default_factory=list)
    meses: list = field(default_factory=list)    # meses típicos de apertura (solo directorio)
    banderas: list = field(default_factory=list)
    relevancia: int = 0
    puntaje: int = 0
    descubierto: str = ""
    pendiente_detalle: bool = False
    agro_garantizada: bool = False

    @property
    def id(self) -> str:
        return hashlib.md5((_url_canonica(self.url) or norm(self.titulo)).encode()).hexdigest()[:12]

    def a_dict(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        return d


CONF_FUENTE = {"Directorio oficial": "Alta", "ReliefWeb": "Alta", "Scholars4Dev": "Media",
               "Computrabajo": "Media", "Jooble": "Media", "Prensa (Google News)": "Baja"}
NOTA_FUENTE = {
    "ReliefWeb": "Publicado por la organización empleadora vía ReliefWeb (ONU/ONG).",
    "Scholars4Dev": "Agregador reconocido de becas; confirma requisitos y fechas en el sitio oficial.",
    "Computrabajo": "Portal de empleo; confirma la empresa y la vigencia antes de postular.",
    "Jooble": "Metabuscador de empleo; confirma en la página original.",
    "Prensa (Google News)": "Detectada en prensa/blogs: úsala como pista y confirma en la fuente oficial.",
    "Directorio oficial": "Portal institucional oficial. Revisa la convocatoria vigente; las fechas cambian cada año.",
}


# ════════════════════════════════════════════════════════════════════
# 7. Recolectores
# ════════════════════════════════════════════════════════════════════
def _parse_fecha_rss(s: Optional[str]) -> Optional[date]:
    try:
        return parsedate_to_datetime(s).date() if s else None
    except Exception:
        return None


def _leer_rss(url: str) -> list[dict]:
    r = http_get(url)
    root = ET.fromstring(r.content)
    out = []
    for it in root.iter("item"):
        out.append({
            "titulo": (it.findtext("title") or "").strip(),
            "url": (it.findtext("link") or "").strip(),
            "fecha": _parse_fecha_rss(it.findtext("pubDate")),
            "desc": limpiar_html(it.findtext("description") or ""),
            "fuente": (it.findtext("source") or "").strip(),
        })
    return out


def recolectar_scholars4dev(hoy: date, paginas: int = 3) -> list[Oportunidad]:
    base = "https://www.scholars4dev.com/category/field-of-study/agriculture-related-scholarships/feed/"
    out = []
    for pg in range(1, paginas + 1):
        u = base if pg == 1 else f"{base}?paged={pg}"
        try:
            items = _leer_rss(u)
        except Exception:
            if pg == 1:
                raise
            break
        for it in items:
            if not it["titulo"] or not it["url"]:
                continue
            out.append(Oportunidad(
                tipo="estudio", titulo=it["titulo"], url=it["url"], fuente="Scholars4Dev",
                resumen=it["desc"][:400], publicado=it["fecha"], pendiente_detalle=True, agro_garantizada=True))
    return out


_RE_TIPO = {
    "estudio": re.compile(r"beca|scholarship|fellowship|bolsa|edital|maestria|master|msc|doctorad|phd|postdoc|posgrado|"
                          r"postgrado|curso|course|training|capacitacion|convocatoria|admission|admisiones|seleccion|stipend"),
    "trabajo": re.compile(r"vacante|empleo|oferta laboral|oferta de trabajo|trabajo|job|hiring|contratacion|contrata|"
                          r"se necesita|convocatoria|opening|position|busca"),
    "fondo": re.compile(r"convocatoria|fondo|financiacion|financiamiento|funding|grant|call for proposals|subsidio|"
                        r"incentivo|cofinanci|capital semilla|proyectos"),
}


def _consultas_gnews(prof: str, lineas: list[str]) -> list[tuple]:
    ES, EN, PT = ("es-419", "CO", "CO:es-419"), ("en", "US", "US:en"), ("pt-419", "BR", "BR:pt-419")
    Q: list[tuple] = []

    def q(tipo, texto, loc, prio=1):
        Q.append((tipo, texto, loc[0], loc[1], loc[2], prio))

    # Estudios — base
    q("estudio", "convocatoria beca maestría agronomía", ES)
    q("estudio", "convocatoria beca doctorado ciencias agrarias", ES)
    q("estudio", "becas posgrado ingeniería agronómica Colombia", ES)
    q("estudio", "fully funded PhD plant science OR plant pathology OR plant breeding", EN)
    q("estudio", "MSc agronomy OR agriculture scholarship international students", EN)
    q("estudio", "edital mestrado OR doutorado agronomia fitotecnia", PT)
    q("estudio", "scholarship agriculture Korea OR Japan OR China international students", EN, 2)
    q("estudio", "agricultural sciences PhD scholarship Europe fully funded", EN, 2)
    q("estudio", "curso capacitación agricultura becas internacional convocatoria", ES, 2)
    q("estudio", "short course agriculture scholarship applications open", EN, 2)
    q("estudio", "convocatoria beca agricultura México OR Chile OR Argentina OR Perú posgrado", ES, 2)
    # Trabajo — base
    q("trabajo", "vacante ingeniero agrónomo Colombia", ES)
    q("trabajo", "oferta empleo agrónomo asistencia técnica Colombia", ES)
    q("trabajo", "convocatoria Agrosavia OR Cenicaña OR Cenicafé OR Cenipalma vacantes", ES)
    q("trabajo", "agronomist OR plant breeder job Colombia OR Ecuador OR Peru", EN, 2)
    q("trabajo", "convocatoria investigador agrícola OR fitomejoramiento empleo", ES, 2)
    # Fondos — base
    q("fondo", "convocatoria Minciencias proyectos agropecuarios", ES)
    q("fondo", "convocatoria Finagro OR ADR OR Fontagro proyectos agropecuarios", ES)
    q("fondo", "agricultural research grant call for proposals Latin America", EN, 2)
    q("fondo", "convocatoria fondo innovación agropecuaria financiación", ES, 2)
    # Por línea de investigación elegida
    for nombre in lineas:
        L = LINEAS.get(nombre)
        if not L:
            continue
        q("estudio", f"convocatoria beca doctorado OR maestría {L['es']}", ES)
        q("estudio", f"fully funded PhD OR scholarship {L['en']}", EN)
        q("estudio", f"edital mestrado OR doutorado {L['pt']}", PT, 2)
        q("trabajo", f"vacante OR empleo {L['es']} Colombia", ES)
        q("trabajo", f"{L['en']} job Latin America", EN, 2)
        q("fondo", f"convocatoria proyectos financiación {L['es']}", ES, 2)
    return Q if prof == "profunda" else [x for x in Q if x[5] == 1]


def recolectar_gnews(hoy: date, prof: str, lineas: list[str]) -> list[Oportunidad]:
    consultas = _consultas_gnews(prof, lineas)
    dias = 45 if prof == "profunda" else 30

    def una(c):
        tipo, texto, hl, gl, ceid, _ = c
        url = "https://news.google.com/rss/search?" + urlencode(
            {"q": f"{texto} when:{dias}d", "hl": hl, "gl": gl, "ceid": ceid})
        try:
            return tipo, _leer_rss(url)[:30], True
        except Exception:
            return tipo, [], False

    out, fallidas = [], 0
    with ThreadPoolExecutor(6) as ex:
        for tipo, items, ok in ex.map(una, consultas):
            fallidas += 0 if ok else 1
            for it in items:
                titulo, medio = it["titulo"], it["fuente"]
                if medio and titulo.endswith(" - " + medio):
                    titulo = titulo[: -len(medio) - 3]
                if not _RE_TIPO[tipo].search(norm(titulo)):
                    continue
                out.append(Oportunidad(
                    tipo=tipo, titulo=titulo, url=it["url"], fuente="Prensa (Google News)", institucion=medio,
                    resumen=titulo, publicado=it["fecha"]))
    if consultas and fallidas == len(consultas):
        raise RuntimeError("no se pudo consultar Google News (sin conexión o bloqueo temporal)")
    return out


def recolectar_reliefweb(appname: Optional[str], hoy: date) -> list[Oportunidad]:
    if not appname:
        raise RuntimeError("omitida: falta RELIEFWEB_APPNAME (gratuito, ver instrucciones)")
    out = []
    consulta = ('(agriculture OR agricultural OR agronomy OR agronomist OR "plant breeding" OR "plant pathology" '
                'OR "soil science" OR "crop")')
    for endpoint, tipo in (("jobs", "trabajo"), ("training", "estudio")):
        payload = {"limit": 100, "sort": ["date.created:desc"],
                   "query": {"value": consulta, "operator": "AND"},
                   "fields": {"include": ["title", "url", "date", "country", "source", "body"]}}
        r = SESION.post(f"https://api.reliefweb.int/v2/{endpoint}?appname={appname}", json=payload, timeout=TIMEOUT)
        r.raise_for_status()
        for row in r.json().get("data", []):
            f = row.get("fields", {})
            fe = f.get("date", {}) or {}
            paises = [c.get("name", "") for c in (f.get("country") or [])]
            fuentes = [s.get("name", "") for s in (f.get("source") or [])]

            def _d(x):
                try:
                    return date.fromisoformat(x[:10]) if x else None
                except Exception:
                    return None
            o = Oportunidad(
                tipo=tipo, titulo=f.get("title", ""), url=f.get("url", ""), fuente="ReliefWeb",
                institucion=", ".join(fuentes[:2]), publicado=_d(fe.get("created")),
                cierre=_d(fe.get("closing") or fe.get("registration")),
                resumen=limpiar_html(f.get("body", ""))[:400], texto=limpiar_html(f.get("body", ""))[:6000])
            if paises:
                o.pais, o.region = detectar_pais(paises[0])
            if tipo == "estudio" and fe.get("start"):
                o.resumen = f"Inicio del curso: {fe['start'][:10]}. " + o.resumen
            out.append(o)
    return out


def _publicado_relativo(texto: str, hoy: date) -> Optional[date]:
    t = norm(texto)
    m = re.search(r"hace\s+(\d+)\s+(hora|dia|semana|mes)", t)
    if m:
        n, u = int(m[1]), m[2]
        return hoy - timedelta(days=0 if u == "hora" else n * {"dia": 1, "semana": 7, "mes": 30}[u])
    if "ayer" in t:
        return hoy - timedelta(days=1)
    if re.search(r"\bhoy\b", t):
        return hoy
    return None


COMPUTRABAJO = [
    ("co", "Colombia", ["trabajo-de-ingeniero-agronomo", "trabajo-de-agronomo", "trabajo-de-asistente-tecnico-agricola"]),
    ("ec", "Ecuador", ["trabajo-de-ingeniero-agronomo"]),
    ("pe", "Perú", ["trabajo-de-ingeniero-agronomo"]),
    ("mx", "México", ["trabajo-de-ingeniero-agronomo"]),
    ("cl", "Chile", ["trabajo-de-ingeniero-agronomo"]),
    ("ar", "Argentina", ["trabajo-de-ingeniero-agronomo"]),
    ("cr", "Costa Rica", ["trabajo-de-ingeniero-agronomo"]),
    ("pa", "Panamá", ["trabajo-de-ingeniero-agronomo"]),
]


def recolectar_computrabajo(hoy: date, prof: str) -> list[Oportunidad]:
    out, errores = [], 0
    destinos = COMPUTRABAJO if prof == "profunda" else COMPUTRABAJO[:4]
    for cc, pais, slugs in destinos:
        for slug in slugs:
            url = f"https://{cc}.computrabajo.com/{slug}"
            try:
                soup = BeautifulSoup(http_get(url).text, "html.parser")
            except Exception:
                errores += 1
                continue
            for art in soup.select("article.box_offer, article[class*='box_offer']"):
                a = art.select_one("h2 a, a.js-o-link")
                if not a or not a.get("href"):
                    continue
                emp = art.select_one("a[href*='/empresas/'], p a.fc_base")
                ubic = art.select_one("span.mr10, p.fs16 span")
                texto = art.get_text(" ", strip=True)
                o = Oportunidad(
                    tipo="trabajo", titulo=a.get_text(" ", strip=True), url=urljoin(url, a["href"]),
                    fuente="Computrabajo", institucion=emp.get_text(" ", strip=True) if emp else "",
                    pais=pais, region=REGION_DE.get(pais, "Latinoamérica"),
                    resumen=texto[:400], publicado=_publicado_relativo(texto, hoy), pendiente_detalle=True)
                if ubic:
                    o.resumen = f"{ubic.get_text(' ', strip=True)} · {o.resumen}"
                out.append(o)
    if not out and errores:
        raise RuntimeError("sin respuesta: el portal bloqueó las consultas (Cloudflare) o no hubo conexión. Usa «Buscadores directos».")
    return out


def recolectar_jooble(clave: Optional[str], hoy: date) -> list[Oportunidad]:
    if not clave:
        raise RuntimeError("omitida: falta JOOBLE_API_KEY (gratuita en jooble.org/api/about)")
    consultas = [("ingeniero agrónomo", "Colombia"), ("agrónomo asistencia técnica", "Colombia"),
                 ("fitomejoramiento", "Colombia"), ("fitopatología", "Colombia"),
                 ("investigador agrícola", "Colombia"), ("agronomist", "Latin America")]
    out = []
    for kw, loc in consultas:
        r = SESION.post(f"https://jooble.org/api/{clave}", json={"keywords": kw, "location": loc, "page": "1"},
                        timeout=TIMEOUT)
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            o = Oportunidad(
                tipo="trabajo", titulo=limpiar_html(j.get("title", "")), url=j.get("link", ""), fuente="Jooble",
                institucion=j.get("company", "") or j.get("source", ""),
                resumen=limpiar_html(j.get("snippet", ""))[:400], texto=limpiar_html(j.get("snippet", "")))
            try:
                o.publicado = date.fromisoformat((j.get("updated") or "")[:10])
            except Exception:
                pass
            o.pais, o.region = detectar_pais(j.get("location", ""), "")
            if o.pais == "No especificado" and loc == "Colombia":
                o.pais, o.region = "Colombia", "Colombia"
            out.append(o)
    return out


# ── Enriquecimiento: abre la página de detalle para sacar fechas, requisitos y enlace oficial ──
_CAMPOS_S4D = ["Application Deadline", "Course Level", "Study Subject", "Scholarship Provider",
               "Scholarship can be taken at", "Eligible Countries", "Scholarship Award",
               "Number of Scholarships Offered", "Nationality"]


def _campos_s4d(texto: str) -> dict:
    pat = "|".join(re.escape(c) for c in _CAMPOS_S4D)
    out = {}
    for m in re.finditer(rf"({pat})\s*:\s*(.+?)(?=\s(?:{pat})\s*:|$)", texto, flags=re.I | re.S):
        out.setdefault(m.group(1).lower(), m.group(2).strip()[:400])
    return out


def enriquecer(o: Oportunidad) -> None:
    r = http_get(o.url)
    soup = BeautifulSoup(r.text, "html.parser")
    for t in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        t.decompose()
    cont = soup.select_one("div.entry, div.post, article, main, #content") or soup.body or soup
    o.texto = re.sub(r"\s+", " ", cont.get_text(" ", strip=True))[:14000]
    host = urlparse(o.url).netloc
    if not o.url_oficial:
        for a in cont.find_all("a", href=True):
            h = a["href"]
            if (h.startswith("http") and urlparse(h).netloc != host and
                    re.search(r"official|oficial|website|sitio|apply|solicitud|more information",
                              a.get_text(" ").lower())):
                o.url_oficial = h
                break
    if o.fuente == "Scholars4Dev":
        c = _campos_s4d(o.texto)
        if c.get("scholarship provider"):
            o.institucion = c["scholarship provider"][:120]
        if c.get("course level"):
            o.nivel = clasificar_nivel(c["course level"])
        if c.get("scholarship can be taken at"):
            p, rg = detectar_pais(c["scholarship can be taken at"])
            if p != "No especificado":
                o.pais, o.region = p, rg
        if c.get("eligible countries") or c.get("nationality"):
            o.elegibilidad = evaluar_elegibilidad(c.get("eligible countries") or c.get("nationality"), "")
        if c.get("scholarship award"):
            o.financiamiento = detectar_financiamiento(c["scholarship award"] + " " + o.texto[:1500])


def _enriquecer_seguro(o: Oportunidad) -> bool:
    try:
        enriquecer(o)
        return True
    except Exception:
        return False


# ════════════════════════════════════════════════════════════════════
# 8. Directorio oficial (programas e instituciones recurrentes)
# ════════════════════════════════════════════════════════════════════
def _catalogo() -> list[Oportunidad]:
    L: list[Oportunidad] = []

    def d(tipo, titulo, inst, pais, cat, url, resumen, fin=FIN_ND, meses=None, elig="No indicada"):
        o = Oportunidad(
            tipo=tipo, titulo=titulo, url=url, fuente="Directorio oficial", institucion=inst, pais=pais,
            region=REGION_DE.get(pais, "Global"), resumen=resumen, financiamiento=fin, elegibilidad=elig,
            confianza="Alta", verificacion=NOTA_FUENTE["Directorio oficial"], origen="directorio",
            meses=meses or [], relevancia=60)
        if tipo == "estudio":
            o.nivel = cat
        else:
            o.cargo_tipo = cat
        L.append(o)

    M, D, MD = NIV_MAESTRIA, NIV_DOCT, f"{NIV_MAESTRIA} / {NIV_DOCT}"
    CO = "✅ Dirigida a colombianos"
    # ── ESTUDIOS: Colombia
    d("estudio", "ICETEX — créditos y becas para posgrado (Colombia y exterior)", "ICETEX", "Colombia", f"{MD} / {NIV_POSGRADO}",
      "https://www.icetex.gov.co", "Crédito educativo y becas en convenio para posgrados nacionales e internacionales.", FIN_PARCIAL, elig=CO)
    d("estudio", "Minciencias — convocatorias de formación de alto nivel (doctorados)", "Minciencias", "Colombia", D,
      "https://minciencias.gov.co/convocatorias", "Revisa la sección «Convocatorias»: apertura y cierre varían cada año.", FIN_COMPLETA, elig=CO)
    d("estudio", "APC-Colombia — oportunidades de cooperación internacional (becas y cursos)", "APC-Colombia", "Colombia",
      f"{MD} / {NIV_CURSO}", "https://www.apccolombia.gov.co", "Concentra ofertas de becas y cursos de JICA, KOICA, gobiernos y organismos para colombianos.", elig=CO)
    d("estudio", "Universidad Nacional de Colombia — posgrados en Ciencias Agrarias", "UNAL (Bogotá, Medellín, Palmira)", "Colombia",
      f"{MD}", "https://unal.edu.co", "Maestrías y doctorados agrarios; admisiones semestrales y becas de posgrado para sobresalientes.", elig=CO)
    d("estudio", "SENA — cursos complementarios agropecuarios (gratuitos)", "SENA", "Colombia", NIV_CURSO,
      "https://www.sena.edu.co", "Oferta continua de formación complementaria en el sector agropecuario.", FIN_COMPLETA, elig=CO)
    # ── ESTUDIOS: Latinoamérica
    d("estudio", "Brasil — CAPES / CNPq: programas para extranjeros y posgrado (mestrado/doutorado)", "CAPES / CNPq", "Brasil", MD,
      "https://www.gov.br/capes/pt-br", "Cada programa de posgrado publica su edital (suele haber 1–2 procesos al año). Las becas CAPES/CNPq las asigna cada programa.", FIN_COMPLETA)
    d("estudio", "ESALQ-USP (Piracicaba) — posgrado en Fitotecnia, Fitopatología, Suelos, Entomología", "Universidade de São Paulo", "Brasil", MD,
      "https://www.esalq.usp.br", "Referente en ciencias agrarias en Latinoamérica. Procesos selectivos por programa.")
    d("estudio", "UFV (Viçosa) — posgrado en Fitotecnia, Fitopatología, Genética y Mejoramiento", "Universidade Federal de Viçosa", "Brasil", MD,
      "https://www.ufv.br", "Programas con alta tradición en melhoramento genético y fitopatología; editais por programa.")
    d("estudio", "UFLA (Lavras) — posgrado en Agronomía/Fitotecnia, Fitopatología, Ciencia del Suelo", "Universidade Federal de Lavras", "Brasil", MD,
      "https://www.ufla.br", "Editais por programa; becas CAPES/CNPq/FAPEMIG según disponibilidad.")
    d("estudio", "CATIE (Costa Rica) — maestrías en agricultura ecológica, agroforestería y afines", "CATIE", "Costa Rica", M,
      "https://www.catie.ac.cr", "Posgrados con becas para estudiantes latinoamericanos.")
    d("estudio", "Colegio de Postgraduados (México) — maestría y doctorado en ciencias agrícolas", "Colpos", "México", MD,
      "https://www.colpos.mx", "Posgrados en genética, fitopatología, edafología, entre otros; becas para extranjeros vía programas del gobierno mexicano.")
    d("estudio", "OEA — becas académicas para posgrado", "Organización de los Estados Americanos", "Global", MD,
      "https://www.oas.org/en/scholarships/", "Verifica la vigencia del programa y las universidades aliadas en el sitio oficial.")
    d("estudio", "AUIP — becas y movilidad de posgrado iberoamericano", "AUIP", "Latinoamérica (varios)", f"{NIV_PASANTIA} / {MD}",
      "https://www.auip.org", "Movilidad y becas de posgrado entre universidades de Iberoamérica.")
    # ── ESTUDIOS: EE. UU., Europa, Asia
    d("estudio", "Fulbright Colombia — becas de maestría y doctorado en EE. UU.", "Comisión Fulbright Colombia", "EE. UU.", MD,
      "https://fulbright.edu.co", "Convocatoria anual para colombianos.", FIN_COMPLETA, elig=CO)
    d("estudio", "Universidades land-grant de EE. UU. (UC Davis, Cornell, Purdue…) — PhD con assistantship", "UC Davis / Cornell CALS", "EE. UU.", D,
      "https://grad.ucdavis.edu", "En EE. UU. el doctorado en ciencias agrícolas suele financiarse con assistantship (matrícula + estipendio) vía el grupo de investigación: contacta al profesor antes de aplicar.", FIN_COMPLETA)
    d("estudio", "Borlaug Fellowship (USDA-FAS) — estancias de investigación", "USDA Foreign Agricultural Service", "EE. UU.", NIV_PASANTIA,
      "https://www.fas.usda.gov/programs/borlaug-fellowship-program", "Estancias de investigación para científicos jóvenes en agricultura; convocatoria anual.", FIN_COMPLETA)
    d("estudio", "Cochran Fellowship (USDA-FAS) — capacitación técnica", "USDA Foreign Agricultural Service", "EE. UU.", NIV_CURSO,
      "https://www.fas.usda.gov/programs/cochran-fellowship-program", "Capacitación de corta duración para profesionales agrícolas.", FIN_COMPLETA)
    d("estudio", "DAAD — becas de posgrado en Alemania (incl. programas de agricultura)", "DAAD", "Alemania", MD,
      "https://www.daad.de/en/", "Programas «Development-Related Postgraduate Courses» y becas de investigación. Ciclo habitual: segundo semestre.", FIN_COMPLETA, meses=[8, 9, 10])
    d("estudio", "Chevening — maestría en Reino Unido", "Gobierno del Reino Unido", "Reino Unido", M,
      "https://www.chevening.org", "Beca anual para maestrías de un año (se postula a universidades en paralelo).", FIN_COMPLETA, meses=[8, 9, 10, 11])
    d("estudio", "Erasmus Mundus — catálogo de másteres conjuntos (agricultura, plantas, suelos)", "Comisión Europea (EACEA)", "Europa (varios)", M,
      "https://www.eacea.ec.europa.eu/scholarships/erasmus-mundus-catalogue_en", "Cada máster fija su propia fecha; muchas cierran entre diciembre y febrero.", FIN_COMPLETA, meses=[12, 1, 2])
    d("estudio", "Programa Eiffel de excelencia (Francia)", "Campus France", "Francia", MD,
      "https://www.campusfrance.org/en/eiffel-scholarship-program-of-excellence", "La candidatura la presenta la universidad francesa; ciclo habitual a inicios de año.", FIN_COMPLETA, meses=[11, 12, 1])
    d("estudio", "Wageningen University — másteres y PhD en ciencias agrarias", "Wageningen University & Research", "Países Bajos", MD,
      "https://www.wur.nl", "Referente mundial en agricultura; revisa becas WUR y vacantes de PhD.")
    d("estudio", "Fundación Carolina — becas de posgrado (España)", "Fundación Carolina", "España", MD,
      "https://www.fundacioncarolina.es", "Convocatoria anual para latinoamericanos.", FIN_PARCIAL, meses=[1, 2, 3])
    d("estudio", "Stipendium Hungaricum (Hungría)", "Gobierno de Hungría", "Hungría", MD,
      "https://stipendiumhungaricum.hu", "Verifica que Colombia participe en la convocatoria vigente.", FIN_COMPLETA, meses=[12, 1])
    d("estudio", "Swedish Institute Scholarships (Suecia)", "Swedish Institute", "Suecia", M,
      "https://si.se/en/apply/scholarships/", "Verifica la lista de países elegibles de cada programa.", FIN_COMPLETA)
    d("estudio", "Chinese Government Scholarship (CSC)", "China Scholarship Council", "China", MD,
      "https://www.campuschina.org", "Postulación por vía universidad o embajada; ciclo habitual primer semestre.", FIN_COMPLETA, meses=[1, 2, 3, 4])
    d("estudio", "Global Korea Scholarship (GKS)", "NIIED — Gobierno de Corea", "Corea del Sur", MD,
      "https://www.studyinkorea.go.kr", "Dos vías: embajada y universidades.", FIN_COMPLETA, meses=[2, 3, 9])
    d("estudio", "MEXT — becas del gobierno de Japón", "MEXT", "Japón", MD,
      "https://www.studyinjapan.go.jp/en/", "Postulación vía embajada de Japón en Colombia.", FIN_COMPLETA, meses=[4, 5])
    d("estudio", "JICA — Knowledge Co-Creation Program (cursos en agricultura)", "JICA", "Japón", NIV_CURSO,
      "https://www.jica.go.jp/english/", "Cursos cortos financiados; la postulación suele canalizarse vía APC-Colombia.", FIN_COMPLETA)
    d("estudio", "KOICA — becas y cursos de capacitación", "KOICA", "Corea del Sur", f"{NIV_CURSO} / {M}",
      "https://www.koica.go.kr", "Cursos y maestrías para países socios; consulta con APC-Colombia.", FIN_COMPLETA)
    d("estudio", "FAO eLearning Academy — cursos en línea de agricultura y seguridad alimentaria", "FAO", "Global", NIV_CURSO,
      "https://elearning.fao.org", "Cursos gratuitos con certificado.", FIN_COMPLETA)
    d("estudio", "IICA — capacitación y cursos para el sector agropecuario", "IICA", "Global", NIV_CURSO,
      "https://iica.int", "Cursos y formación en línea para profesionales del agro de las Américas.")
    # ── FONDOS
    F = "Fondo / convocatoria"
    d("fondo", "Minciencias — convocatorias de proyectos de CTeI", "Minciencias", "Colombia", F,
      "https://minciencias.gov.co/convocatorias", "Proyectos de investigación e innovación agropecuaria; fechas por convocatoria.", FIN_COMPLETA, elig=CO)
    d("fondo", "Finagro — incentivos y crédito agropecuario (ICR, LEC)", "Finagro", "Colombia", F,
      "https://www.finagro.com.co", "Incentivos y líneas de crédito para productores y proyectos agropecuarios.", FIN_PARCIAL, elig=CO)
    d("fondo", "ADR — proyectos integrales de desarrollo agropecuario y rural", "Agencia de Desarrollo Rural", "Colombia", F,
      "https://www.adr.gov.co", "Cofinanciación de proyectos productivos con organizaciones rurales.", FIN_PARCIAL, elig=CO)
    d("fondo", "Ministerio de Agricultura — convocatorias y fondos parafiscales", "MinAgricultura", "Colombia", F,
      "https://www.minagricultura.gov.co", "Convocatorias sectoriales y fondos de fomento (cacao, arroz, palma, etc. vía gremios).", elig=CO)
    d("fondo", "Fondo Emprender (SENA) — capital semilla", "SENA", "Colombia", F,
      "https://www.fondoemprender.com", "Capital semilla para emprendimientos, incluidos agro.", FIN_COMPLETA, elig=CO)
    d("fondo", "iNNpulsa Colombia — convocatorias de innovación y emprendimiento", "iNNpulsa", "Colombia", F,
      "https://www.innpulsacolombia.com", "Convocatorias abiertas para emprendimiento e innovación (incluye agtech).", elig=CO)
    d("fondo", "FONTAGRO — proyectos de innovación agroalimentaria", "FONTAGRO", "Latinoamérica (varios)", F,
      "https://www.fontagro.org", "Convocatorias competitivas para consorcios de la región.", FIN_PARCIAL)
    d("fondo", "GEF Small Grants Programme — proyectos comunitarios", "PNUD / GEF", "Global", F,
      "https://sgp.undp.org", "Pequeñas donaciones para proyectos agroambientales comunitarios.", FIN_COMPLETA)
    d("fondo", "Grand Challenges (Fundación Gates) — convocatorias en agricultura", "Gates Foundation", "Global", F,
      "https://gcgh.grandchallenges.org", "Convocatorias temáticas, algunas en agricultura y nutrición.")
    d("fondo", "Horizon Europe — portal de Funding & Tenders", "Comisión Europea", "Europa (varios)", F,
      "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/home", "Cluster 6 (alimentos, bioeconomía, agricultura). Colombia suele participar como socio.")
    d("fondo", "USDA NIFA — subvenciones de investigación agrícola", "USDA NIFA", "EE. UU.", F,
      "https://www.nifa.usda.gov/grants", "Principalmente para instituciones de EE. UU.; útil como socio o para ubicar grupos con fondos.")
    # ── EMPLEO: empleadores clave
    E = "Empleador clave"
    for titulo, inst, pais, url, nota in [
        ("AGROSAVIA — convocatorias de talento humano", "AGROSAVIA", "Colombia", "https://www.agrosavia.co", "Principal entidad pública de investigación agropecuaria; publica convocatorias de investigadores y profesionales."),
        ("Alianza Bioversity–CIAT (Palmira) — vacantes", "CGIAR", "Colombia", "https://alliancebioversityciat.org", "Centro internacional de investigación en fitomejoramiento y recursos genéticos con sede en Palmira."),
        ("Cenicaña — oportunidades laborales", "Cenicaña", "Colombia", "https://www.cenicana.org", "Investigación en caña de azúcar (Valle del Cauca)."),
        ("Cenicafé — oportunidades laborales", "Cenicafé / FNC", "Colombia", "https://www.cenicafe.org", "Investigación cafetera (Manizales)."),
        ("Cenipalma — oportunidades laborales", "Cenipalma", "Colombia", "https://www.cenipalma.org", "Investigación en palma de aceite."),
        ("ICA — Instituto Colombiano Agropecuario", "ICA", "Colombia", "https://www.ica.gov.co", "Sanidad vegetal y semillas; concursos y contratación."),
        ("Servicio Público de Empleo (Colombia)", "SPE", "Colombia", "https://serviciodeempleo.gov.co", "Vacantes de todo el país; filtra por «ingeniero agrónomo»."),
        ("Universidad Nacional de Colombia — convocatorias docentes", "UNAL", "Colombia", "https://unal.edu.co", "Concursos docentes e investigación."),
        ("SENA — instructores y contratistas", "SENA", "Colombia", "https://www.sena.edu.co", "Instructores en formación agropecuaria."),
        ("Federación Nacional de Cafeteros", "FNC", "Colombia", "https://federaciondecafeteros.org", "Extensión rural y asistencia técnica cafetera."),
        ("Augura / Asocaña / Fedearroz — gremios agrícolas", "Gremios", "Colombia", "https://www.fedearroz.com.co", "Asistencia técnica y proyectos por cultivo."),
        ("FAO — vacantes (Colombia y región)", "FAO", "Global", "https://jobs.fao.org", "Cargos técnicos y de cooperación; revisa sedes en LatAm."),
        ("IICA — oportunidades de empleo", "IICA", "Global", "https://iica.int", "Cooperación técnica agropecuaria en las Américas."),
        ("CGIAR — vacantes", "CGIAR", "Global", "https://www.cgiar.org", "Red de centros de investigación agrícola (CIAT, CIMMYT, CIP…)."),
        ("Bayer Crop Science — carreras", "Bayer", "Global", "https://career.bayer.com", "Investigación y asistencia técnica en el sector privado."),
        ("Corteva Agriscience — carreras", "Corteva", "Global", "https://careers.corteva.com", "Fitomejoramiento y desarrollo de producto."),
    ]:
        d("trabajo", titulo, inst, pais, E, url, nota)
    return L


# ════════════════════════════════════════════════════════════════════
# 9. Procesamiento, deduplicación y ranking
# ════════════════════════════════════════════════════════════════════
_PRIO_CONF = {"Alta": 0, "Media": 1, "Baja": 2}


def deduplicar(items: list[Oportunidad]) -> list[Oportunidad]:
    vu, vt, out = set(), set(), []
    for o in sorted(items, key=lambda x: (_PRIO_CONF.get(x.confianza, 2), 0 if x.cierre else 1)):
        ku = _url_canonica(o.url)
        kt = re.sub(r"[^a-z0-9]", "", norm(o.titulo + o.institucion))[:80]
        if (ku and ku in vu) or (kt and kt in vt):
            continue
        vu.add(ku)
        vt.add(kt)
        out.append(o)
    return out


def finalizar(o: Oportunidad, hoy: date) -> Oportunidad:
    base = " ".join([o.titulo, o.resumen, o.texto])
    if o.origen == "directorio":
        o.lineas = detectar_lineas(base)
        return o
    if o.pais in ("", "No especificado"):
        o.pais, o.region = detectar_pais(o.titulo + " " + o.institucion, o.resumen + " " + o.texto)
    if o.tipo == "trabajo" and clasificar_nivel(o.titulo) in (NIV_DOCT, NIV_POSTDOC, NIV_MAESTRIA):
        o.tipo, o.nivel = "estudio", clasificar_nivel(o.titulo)       # PhD/Postdoc → pestaña de estudios
    if o.tipo == "estudio" and not o.nivel:
        o.nivel = clasificar_nivel(o.titulo, o.resumen + " " + o.texto[:600])
    if o.tipo == "trabajo" and not o.cargo_tipo:
        o.cargo_tipo = clasificar_cargo(o.titulo)
    if o.tipo in ("estudio", "fondo"):
        if o.financiamiento == FIN_ND:
            o.financiamiento = detectar_financiamiento(base)
        if o.elegibilidad == "No indicada":
            o.elegibilidad = evaluar_elegibilidad("", base)
    if not (o.inicio and o.cierre):
        i, c = extraer_fechas(base, hoy)
        o.inicio, o.cierre = o.inicio or i, o.cierre or c
    if not o.requisitos:
        o.requisitos = extraer_requisitos(o.texto or o.resumen)
    if not o.resumen:
        o.resumen = o.texto[:350]
    o.lineas = detectar_lineas(base[:6000])
    o.confianza = CONF_FUENTE.get(o.fuente, "Baja")
    notas = [NOTA_FUENTE.get(o.fuente, "")]
    if o.url_oficial:
        notas.append(f"Enlace oficial detectado: {urlparse(o.url_oficial).netloc}.")
        if _es_institucional(o.url_oficial) and o.cierre and o.confianza == "Media":
            o.confianza = "Alta"
            notas.append("Dominio institucional + fecha de cierre detectada.")
    if _es_institucional(o.url) and o.confianza != "Alta":
        o.confianza = "Alta"
        notas.append("La convocatoria está en un dominio institucional.")
    if o.cierre:
        notas.append("Fechas detectadas automáticamente en el texto: confírmalas en la convocatoria.")
    else:
        notas.append("No se encontró fecha de cierre: confírmala en la convocatoria.")
    if _FRAUDE.search(norm(base)):
        o.banderas.append("Posible fraude: pide pagos/transferencias")
        o.confianza = "Baja"
    o.verificacion = " ".join(n for n in notas if n)
    o.texto = o.texto[:2500]
    return o


def puntuar(o: Oportunidad, lineas_usuario: Optional[list[str]] = None, hoy: Optional[date] = None,
            boost_nuevo: bool = False) -> int:
    hoy = hoy or hoy_co()
    p = o.relevancia * 0.3
    p += {"Alta": 25, "Media": 15, "Baja": 4}.get(o.confianza, 4)
    if o.tipo == "estudio":
        pesos = {NIV_DOCT: 20, NIV_MAESTRIA: 18, NIV_POSTDOC: 16, NIV_POSGRADO: 12, NIV_CURSO: 8, NIV_PASANTIA: 8}
        p += max((v for k, v in pesos.items() if k in o.nivel), default=3)
    elif o.tipo == "trabajo":
        p += {"Investigación": 16, "Gerencia / Coordinación": 13, "Docencia": 12, "Cooperación / ONG": 12,
              "Asistencia técnica": 11, "Consultoría": 11, "Técnico-comercial": 9, "Pasantía / Junior": 6}.get(o.cargo_tipo, 8)
    else:
        p += 12
    if o.financiamiento == FIN_COMPLETA:
        p += 15
    elif o.financiamiento == FIN_PARCIAL:
        p += 7
    et, dc, _ = estado(o.inicio, o.cierre, hoy)
    if et == "Cierra pronto":
        p += 14 if (dc or 0) >= 3 else 10
    elif et == "Abierta":
        p += 10 if (dc or 99) <= 60 else 6
    elif et == "Próxima apertura":
        p += 8
    elif et == "Cerrada":
        p -= 100
    else:
        p -= 3
    if o.cierre:
        p += 6
    if o.publicado and (hoy - o.publicado).days <= 7:
        p += 6
    p += min(len(set(o.lineas) & set(lineas_usuario or [])), 2) * 12
    if o.region == "Colombia":
        p += 8 if o.tipo != "estudio" else 4
    elif o.region == "Latinoamérica":
        p += 5
    if "✅" in o.elegibilidad:
        p += 6
    elif "⚠️" in o.elegibilidad:
        p -= 8
    if o.banderas:
        p -= 40
    if boost_nuevo and o.descubierto:
        try:
            if (hoy - date.fromisoformat(o.descubierto)).days <= 7:
                p += 8
        except ValueError:
            pass
    o.puntaje = int(p)
    return o.puntaje


def marcar_novedades(items: list[Oportunidad], ruta: str) -> bool:
    """Registra la primera vez que se vio cada oferta. Devuelve True si es la primera ejecución."""
    hoy_s = hoy_co().isoformat()
    try:
        with open(ruta, encoding="utf-8") as f:
            hist = json.load(f)
    except Exception:
        hist = {}
    primera = not hist
    for o in items:
        if o.origen != "directorio":
            o.descubierto = hist.setdefault(o.id, hoy_s)
    limite = (hoy_co() - timedelta(days=365)).isoformat()
    hist = {k: v for k, v in hist.items() if v >= limite}
    try:
        os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(hist, f)
    except Exception:
        pass
    return primera


FUENTES_DEFECTO = ["Scholars4Dev", "Prensa (Google News)", "ReliefWeb", "Computrabajo", "Jooble"]


def recolectar_todo(cfg: dict) -> tuple[list[Oportunidad], list[dict]]:
    """cfg: profundidad ('rapida'|'profunda'), fuentes, lineas, reliefweb, jooble."""
    hoy = hoy_co()
    prof = cfg.get("profundidad", "rapida")
    fuentes = set(cfg.get("fuentes", FUENTES_DEFECTO))
    lineas = list(cfg.get("lineas", []))
    tareas = []
    if "Scholars4Dev" in fuentes:
        tareas.append(("Scholars4Dev", lambda: recolectar_scholars4dev(hoy, 5 if prof == "profunda" else 2)))
    if "Prensa (Google News)" in fuentes:
        tareas.append(("Prensa (Google News)", lambda: recolectar_gnews(hoy, prof, lineas)))
    if "ReliefWeb" in fuentes:
        tareas.append(("ReliefWeb", lambda: recolectar_reliefweb(cfg.get("reliefweb"), hoy)))
    if "Computrabajo" in fuentes:
        tareas.append(("Computrabajo", lambda: recolectar_computrabajo(hoy, prof)))
    if "Jooble" in fuentes:
        tareas.append(("Jooble", lambda: recolectar_jooble(cfg.get("jooble"), hoy)))

    def correr(t):
        nombre, fn = t
        try:
            return nombre, fn(), None
        except Exception as e:  # una fuente caída no debe tumbar el radar
            return nombre, [], str(e)[:160]

    log, crudos = [], []
    with ThreadPoolExecutor(max(1, len(tareas))) as ex:
        for nombre, its, err in ex.map(correr, tareas):
            log.append({"Fuente": nombre, "Resultados brutos": len(its),
                        "Estado": "✅ OK" if not err else f"⚠️ {err}"})
            crudos += its

    # Filtro estricto de agronomía
    candidatos = []
    for o in crudos:
        o.relevancia, es_agro = relevancia_agro(o.titulo, o.resumen)
        if es_agro or o.agro_garantizada:
            o.confianza = CONF_FUENTE.get(o.fuente, "Baja")
            candidatos.append(o)
    candidatos = deduplicar(candidatos)

    # Enriquecer con la página de detalle (fechas, requisitos, enlace oficial)
    max_enr = 90 if prof == "profunda" else 30
    pend = sorted([o for o in candidatos if o.pendiente_detalle],
                  key=lambda x: x.publicado or date.min, reverse=True)[:max_enr]
    with ThreadPoolExecutor(8) as ex:
        ok = list(ex.map(_enriquecer_seguro, pend))
    log.append({"Fuente": "Páginas de detalle", "Resultados brutos": len(pend),
                "Estado": f"✅ {sum(ok)} leídas · ⚠️ {len(ok) - sum(ok)} con error" if pend else "—"})

    final = [finalizar(o, hoy) for o in candidatos]
    for o in _catalogo():
        final.append(finalizar(o, hoy))
    return deduplicar(final), log


def calendario_recurrente(items: list[Oportunidad], hoy: Optional[date] = None) -> list[Oportunidad]:
    """Programas del directorio que suelen abrir este mes o el siguiente (referencial)."""
    hoy = hoy or hoy_co()
    meses = {hoy.month, hoy.month % 12 + 1}
    return [o for o in items if o.origen == "directorio" and set(o.meses) & meses]


# ════════════════════════════════════════════════════════════════════
# 10. Exportación, buscadores directos y correo
# ════════════════════════════════════════════════════════════════════
def a_dataframe(items: list[Oportunidad], hoy: Optional[date] = None) -> pd.DataFrame:
    hoy = hoy or hoy_co()
    filas = []
    for o in items:
        et, dc, _ = estado(o.inicio, o.cierre, hoy)
        filas.append({
            "Tipo": o.tipo.capitalize(), "Título": o.titulo, "Institución": o.institucion or o.fuente,
            "País": o.pais, "Región": o.region, "Nivel / Cargo": o.nivel or o.cargo_tipo,
            "Inicio postulación": o.inicio.isoformat() if o.inicio else "",
            "Cierre postulación": o.cierre.isoformat() if o.cierre else "",
            "Estado": et, "Días para cierre": dc if dc is not None else "",
            "Financiamiento": o.financiamiento, "Elegibilidad": o.elegibilidad,
            "Confianza": o.confianza, "Fuente": o.fuente, "Líneas": ", ".join(o.lineas),
            "Puntaje": o.puntaje, "Descubierta": o.descubierto, "Requisitos (extracto)": o.requisitos[:300],
            "Enlace": o.url, "Enlace oficial": o.url_oficial,
        })
    return pd.DataFrame(filas)


def a_csv(items: list[Oportunidad], hoy: Optional[date] = None) -> bytes:
    return a_dataframe(items, hoy).to_csv(index=False).encode("utf-8-sig")  # abre bien en Excel


def enlaces_directos(es: str, en: str, pt: str) -> dict[str, list[tuple[str, str]]]:
    """Búsquedas precargadas en portales (siempre funcionan, sin scraping)."""
    def g(site, q):
        return "https://www.google.com/search?q=" + quote_plus(f"site:{site} {q}")

    def gl(q):
        return "https://www.google.com/search?q=" + quote_plus(q)
    E, Q, P = quote_plus(en), quote_plus(es), quote_plus(pt)
    return {
        "🎓 Estudios": [
            ("FindAPhD (PhD en inglés)", f"https://www.findaphd.com/phds/?Keywords={E}"),
            ("Scholars4Dev (becas)", f"https://www.scholars4dev.com/?s={E}"),
            ("EURAXESS (PhD/postdocs Europa)", f"https://euraxess.ec.europa.eu/jobs/search?keywords={E}"),
            ("jobs.ac.uk (PhD/postdocs Reino Unido)", f"https://www.jobs.ac.uk/search/?keywords={E}"),
            ("Academic Positions", g("academicpositions.com", en)),
            ("Mastersportal (másteres)", g("mastersportal.com", en)),
            ("DAAD (Alemania)", g("daad.de", en)),
            ("Brasil · editais de mestrado/doutorado", gl(f"edital seleção mestrado doutorado {pt} 2027")),
            ("Brasil · CAPES", g("gov.br/capes", pt)),
            ("Convocatorias de becas (ES)", gl(f"convocatoria beca posgrado {es} 2027")),
            ("Asia · becas (Corea/Japón/China)", gl(f"scholarship {en} Korea OR Japan OR China international students 2027")),
        ],
        "💼 Trabajo": [
            ("LinkedIn · Colombia", f"https://www.linkedin.com/jobs/search/?keywords={Q}&location=Colombia"),
            ("LinkedIn · Latinoamérica", f"https://www.linkedin.com/jobs/search/?keywords={Q}&location=Latin%20America"),
            ("Indeed · Colombia", f"https://co.indeed.com/jobs?q={Q}"),
            ("Computrabajo · Colombia", g("co.computrabajo.com", es)),
            ("elempleo.com", g("elempleo.com", es)),
            ("Servicio Público de Empleo", g("serviciodeempleo.gov.co", es)),
            ("ReliefWeb (ONU/ONG)", f"https://reliefweb.int/jobs?search={E}"),
            ("UN Jobs", g("unjobs.org", en)),
            ("CGIAR · vacantes", g("cgiar.org", f"careers {en}")),
        ],
        "💰 Fondos": [
            ("Minciencias", g("minciencias.gov.co", f"convocatoria {es}")),
            ("FONTAGRO", g("fontagro.org", f"convocatoria {es}")),
            ("Finagro / ADR", gl(f"convocatoria Finagro OR ADR {es} 2027")),
            ("Convocatorias de fondos agrarios (ES)", gl(f"convocatoria fondo proyectos {es} Colombia 2027")),
            ("Grants (EN)", gl(f"call for proposals grant {en} Latin America 2027")),
        ],
    }


def _tarjeta_html(o: Oportunidad, hoy: date) -> str:
    et, dc, _ = estado(o.inicio, o.cierre, hoy)
    cierre = (f"Cierra: {fmt_fecha(o.cierre)}" + (f" ({dc} d)" if dc is not None and dc >= 0 else "")) \
        if o.cierre else "Cierre: ver convocatoria"
    abre = f" · Abre: {fmt_fecha(o.inicio)}" if o.inicio else ""
    conf = {"Alta": "✅ Verificada", "Media": "🟡 Fuente reconocida", "Baja": "🔎 Por verificar"}[o.confianza]
    meta = " · ".join(x for x in [o.institucion, o.pais if o.pais != "No especificado" else "",
                                  o.nivel or o.cargo_tipo] if x)
    return (f'<div style="border:1px solid #d0d7de;border-radius:10px;padding:12px 14px;margin:10px 0;'
            f'font-family:Arial,sans-serif">'
            f'<a href="{escape(o.url)}" style="font-size:15px;font-weight:bold;color:#0b5cad;text-decoration:none">'
            f'{escape(o.titulo)}</a><br>'
            f'<span style="color:#57606a;font-size:13px">{escape(meta)}</span><br>'
            f'<span style="font-size:13px">⏳ {escape(cierre)}{escape(abre)} · {conf} · {escape(et)}</span></div>')


def html_correo(top: list[Oportunidad], secciones: dict[str, list[Oportunidad]],
                calendario: list[Oportunidad], hoy: Optional[date] = None, nota: str = "") -> str:
    hoy = hoy or hoy_co()
    partes = [f'<div style="max-width:720px;margin:auto;font-family:Arial,sans-serif">'
              f'<h2>🌾 Radar Agronómico — Top semanal ({fmt_fecha(hoy)})</h2>']
    if nota:
        partes.append(f'<p style="color:#57606a">{escape(nota)}</p>')
    partes.append("<h3>🔥 Top 5 de la semana</h3>")
    partes += [_tarjeta_html(o, hoy) for o in top] or ["<p>Sin resultados esta semana.</p>"]
    for titulo, lista in secciones.items():
        if lista:
            partes.append(f"<h3>{escape(titulo)}</h3>")
            partes += [_tarjeta_html(o, hoy) for o in lista]
    if calendario:
        partes.append("<h3>📆 Programas que suelen abrir este mes o el próximo (referencial)</h3><ul>")
        partes += [f'<li><a href="{escape(o.url)}">{escape(o.titulo)}</a> — {escape(o.pais)}</li>' for o in calendario[:10]]
        partes.append("</ul>")
    partes.append('<p style="color:#8c959f;font-size:12px">Fechas detectadas automáticamente; confírmalas siempre en la '
                  'convocatoria oficial antes de postular.</p></div>')
    return "".join(partes)


def enviar_correo(html: str, asunto: str, destinatario: str, usuario: str, clave: str,
                  host: str = "smtp.gmail.com", puerto: int = 465) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = asunto, usuario, destinatario
    msg.attach(MIMEText("Abre este correo en un cliente compatible con HTML.", "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL(host, puerto, context=ssl.create_default_context()) as s:
        s.login(usuario, clave)
        s.sendmail(usuario, [x.strip() for x in destinatario.split(",")], msg.as_string())
