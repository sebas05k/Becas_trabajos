"""
prospeccion.py - Pestaña "Prospección directa" para Radar Agronómico.

Busca empresas agrícolas en Google Maps vía Serper, lee reseñas de las peor
calificadas, detecta problemas recurrentes (demoras, soporte, servicio...) y
genera un borrador de mensaje para contactar al dueño.

Uso en app.py:
    import prospeccion
    tab_trabajo, tab_estudios, tab_prosp = st.tabs(["💼 Trabajo", "🎓 Estudios", "🎯 Prospección"])
    with tab_prosp:
        prospeccion.render()

Clave de Serper:
    - Local:  .streamlit/secrets.toml  ->  SERPER_API_KEY = "tu_clave"
    - Nube:   Streamlit Cloud -> Settings -> Secrets (mismo formato)
    - Nunca la escribas en el código ni la subas a GitHub.
"""

import os
import re
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

SERPER_PLACES_URL = "https://google.serper.dev/places"
SERPER_REVIEWS_URL = "https://google.serper.dev/reviews"

TIPOS_EMPRESA = [
    "floricultoras",
    "exportadoras de flores",
    "laboratorios de análisis de suelos",
    "laboratorios agrícolas",
    "viveros",
    "empresas de insumos agrícolas",
    "exportadoras de frutas",
    "cooperativas agrícolas",
    "otro (escribir)",
]

# Palabras que, en reseñas de 3 estrellas o menos, señalan un problema ofrecible.
PALABRAS_DOLOR = {
    "Demoras / incumplimiento": [
        "demora", "demoran", "tarde", "retraso", "retrasos", "incumpl",
        "no llegó", "no llego", "tardan", "esperar",
    ],
    "Servicio al cliente": [
        "mala atención", "mala atencion", "no responden", "no contestan",
        "nadie contesta", "no atienden", "grosero", "pésimo servicio",
        "pesimo servicio",
    ],
    "Soporte técnico": [
        "soporte", "asesoría", "asesoria", "asistencia técnica",
        "asistencia tecnica", "postventa", "garantía", "garantia",
        "sin seguimiento",
    ],
    "Calidad": [
        "mala calidad", "baja calidad", "mal estado", "dañado", "danado",
        "plaga", "enfermedad", "hongo", "contamin", "pérdida", "perdida",
    ],
}

FRASES_TEMA = {
    "Demoras / incumplimiento": "cumplir tiempos de entrega y despacho",
    "Servicio al cliente": "mejorar la atención y comunicación con sus clientes",
    "Soporte técnico": "contar con acompañamiento técnico y seguimiento en campo",
    "Calidad": "reducir pérdidas por calidad y sanidad del cultivo",
}


# --------------------------------------------------------------------------
# Serper
# --------------------------------------------------------------------------
def _api_key():
    try:
        key = st.secrets.get("SERPER_API_KEY")
    except Exception:
        key = None
    return key or os.getenv("SERPER_API_KEY")


def _post(url, payload, key):
    r = requests.post(
        url,
        json=payload,
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def buscar_empresas(tipo, ubicacion, paginas, _key):
    """1 llamada (crédito) por página. Devuelve lista de empresas sin duplicados."""
    empresas, vistos = [], set()
    for pagina in range(1, paginas + 1):
        data = _post(
            SERPER_PLACES_URL,
            {"q": f"{tipo} en {ubicacion}", "gl": "co", "hl": "es", "page": pagina},
            _key,
        )
        lugares = data.get("places", [])
        if not lugares:
            break
        for lugar in lugares:
            clave = lugar.get("cid") or (lugar.get("title"), lugar.get("address"))
            if clave in vistos:
                continue
            vistos.add(clave)
            empresas.append(
                {
                    "empresa": lugar.get("title", ""),
                    "categoria": lugar.get("category", ""),
                    "calificacion": lugar.get("rating"),
                    "n_resenas": lugar.get("ratingCount") or 0,
                    "telefono": lugar.get("phoneNumber", ""),
                    "direccion": lugar.get("address", ""),
                    "web": lugar.get("website", ""),
                    "cid": lugar.get("cid"),
                    "fid": lugar.get("fid"),
                    "place_id": lugar.get("placeId"),
                }
            )
    return empresas


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def traer_resenas(cid, fid, place_id, paginas, _key):
    """1 llamada (crédito) por página de reseñas."""
    payload = {"gl": "co", "hl": "es"}
    if cid:
        payload["cid"] = cid
    if fid:
        payload["fid"] = fid
    if place_id:
        payload["placeId"] = place_id
    if len(payload) == 2:  # sin identificador no se puede consultar
        return []

    resenas = []
    for _ in range(paginas):
        data = _post(SERPER_REVIEWS_URL, payload, _key)
        resenas.extend(data.get("reviews", []))
        token = data.get("nextPageToken")
        if not token:
            break
        payload["nextPageToken"] = token
    return resenas


# --------------------------------------------------------------------------
# Análisis
# --------------------------------------------------------------------------
def analizar_resenas(resenas):
    """Devuelve (n_negativas, {tema: [fragmentos]})."""
    negativas, temas = 0, {}
    for r in resenas:
        rating = r.get("rating")
        if rating is None or rating > 3:
            continue
        negativas += 1
        texto = (r.get("snippet") or r.get("text") or "").strip()
        bajo = texto.lower()
        for tema, palabras in PALABRAS_DOLOR.items():
            if any(p in bajo for p in palabras):
                temas.setdefault(tema, []).append(texto[:220])
    return negativas, temas


def puntaje(empresa):
    rating = empresa.get("calificacion") or 5
    return round(
        empresa.get("negativas", 0) * 1.0
        + len(empresa.get("temas", {})) * 3.0
        + max(0, 4.5 - rating) * 2.0,
        1,
    )


# --------------------------------------------------------------------------
# Mensajes
# --------------------------------------------------------------------------
def borrador_mensaje(empresa, temas, mi_nombre, mi_servicio):
    # No se citan las reseñas del cliente: suena a reclamo. Se habla del sector.
    if temas:
        foco = " y ".join(FRASES_TEMA[t] for t in list(temas)[:2])
        gancho = f"Trabajo con empresas del sector ayudándoles a {foco}."
    else:
        gancho = "Trabajo con empresas del sector apoyándolas en lo técnico y en producción."
    return (
        f"Buenos días, equipo de {empresa}. Soy {mi_nombre}, ingeniero agrónomo. "
        f"{gancho} Ofrezco: {mi_servicio}. "
        "¿Podría contarles en 10 minutos cómo lo haría en su operación? "
        "Quedo atento. Gracias."
    )


def link_whatsapp(telefono, mensaje):
    d = re.sub(r"\D", "", telefono or "")
    if d.startswith("57") and len(d) == 12:
        d = d[2:]
    if len(d) == 10 and d.startswith("3"):  # celular colombiano
        return f"https://wa.me/57{d}?text={quote(mensaje)}"
    return None


# --------------------------------------------------------------------------
# Interfaz
# --------------------------------------------------------------------------
def render():
    st.subheader("🎯 Prospección directa")
    st.caption(
        "Encuentra empresas agrícolas de tu zona, detecta problemas en sus reseñas "
        "de Google Maps y contacta al dueño con una solución concreta."
    )

    key = _api_key()
    if not key:
        st.warning("No encontré SERPER_API_KEY en secrets ni en variables de entorno.")
        key = st.text_input("Clave de Serper (solo para esta sesión)", type="password")
        if not key:
            return

    with st.expander("✍️ Tu oferta (se usa en los borradores de mensaje)", expanded=False):
        mi_nombre = st.text_input("Tu nombre", key="prosp_nombre")
        mi_servicio = st.text_area(
            "Qué ofreces",
            "asesoría técnica en manejo de cultivo, sanidad vegetal y control de calidad",
            key="prosp_servicio",
        )

    with st.form("form_prospeccion"):
        c1, c2 = st.columns(2)
        tipo = c1.selectbox("Tipo de empresa", TIPOS_EMPRESA)
        if tipo.startswith("otro"):
            tipo = c1.text_input("¿Cuál?", "empresas agrícolas")
        ubicacion = c2.text_input("Ciudad / región", "Rionegro, Antioquia")

        c3, c4, c5 = st.columns(3)
        paginas = c3.slider("Páginas de resultados", 1, 10, 3, help="Cada página = 1 crédito.")
        n_leer = c4.slider("Leer reseñas de las N peor calificadas", 0, 30, 10)
        pag_res = c5.slider("Páginas de reseñas por empresa", 1, 3, 1)

        c6, c7 = st.columns(2)
        rating_max = c6.slider("Calificación máxima a considerar", 1.0, 5.0, 4.5, 0.1)
        min_resenas = c7.slider("Mínimo de reseñas", 0, 100, 5)

        st.caption(f"Costo estimado: hasta ~{paginas + n_leer * pag_res} créditos de Serper.")
        enviado = st.form_submit_button("🔎 Buscar empresas")

    if enviado:
        try:
            with st.spinner("Buscando empresas..."):
                empresas = buscar_empresas(tipo, ubicacion, paginas, key)

            candidatas = [
                e for e in empresas
                if e["calificacion"] is not None
                and e["calificacion"] <= rating_max
                and e["n_resenas"] >= min_resenas
            ]
            candidatas.sort(key=lambda e: (e["calificacion"], -e["n_resenas"]))

            for e in candidatas:
                e["negativas"], e["temas"] = 0, {}

            barra = st.progress(0.0, text="Leyendo reseñas...") if n_leer else None
            for i, e in enumerate(candidatas[:n_leer]):
                resenas = traer_resenas(e["cid"], e["fid"], e["place_id"], pag_res, key)
                e["negativas"], e["temas"] = analizar_resenas(resenas)
                barra.progress((i + 1) / n_leer, text=f"Leyendo reseñas {i + 1}/{n_leer}")
            if barra:
                barra.empty()

            for e in candidatas:
                e["puntaje"] = puntaje(e)
            candidatas.sort(key=lambda e: e["puntaje"], reverse=True)

            st.session_state["prosp_resultados"] = candidatas
            st.session_state["prosp_total"] = len(empresas)
        except requests.HTTPError as err:
            code = err.response.status_code if err.response is not None else "?"
            if code in (401, 403):
                st.error("Serper rechazó la clave (401/403). Revisa SERPER_API_KEY.")
            elif code == 429:
                st.error("Límite de Serper alcanzado (429). Espera un momento o revisa tus créditos.")
            else:
                st.error(f"Error de Serper (HTTP {code}).")
            return
        except requests.RequestException as err:
            st.error(f"No pude conectar con Serper: {err}")
            return

    resultados = st.session_state.get("prosp_resultados")
    if resultados is None:
        return
    if not resultados:
        st.info("No hubo empresas con esos filtros. Sube la calificación máxima o baja el mínimo de reseñas.")
        return

    st.success(
        f"{len(resultados)} empresas candidatas de {st.session_state.get('prosp_total', 0)} encontradas."
    )

    df = pd.DataFrame(
        [
            {
                "empresa": e["empresa"],
                "categoria": e["categoria"],
                "calificacion": e["calificacion"],
                "n_resenas": e["n_resenas"],
                "resenas_negativas_leidas": e["negativas"],
                "problemas_detectados": "; ".join(e["temas"]),
                "puntaje_oportunidad": e["puntaje"],
                "telefono": e["telefono"],
                "direccion": e["direccion"],
                "web": e["web"],
            }
            for e in resultados
        ]
    )
    st.download_button(
        "⬇️ Descargar CSV",
        df.to_csv(index=False).encode("utf-8-sig"),  # utf-8-sig: Excel abre bien las tildes
        file_name="prospeccion_empresas.csv",
        mime="text/csv",
    )

    vista = st.radio("Vista", ["Tarjetas", "Tabla"], horizontal=True)
    if vista == "Tabla":
        st.dataframe(df, use_container_width=True, hide_index=True)
        return

    nombre = mi_nombre or "[tu nombre]"
    for i, e in enumerate(resultados[:30]):
        with st.container(border=True):
            st.markdown(f"**{e['empresa']}** · {e['categoria']}")
            st.caption(
                f"⭐ {e['calificacion']} ({e['n_resenas']} reseñas) · "
                f"Puntaje de oportunidad: {e['puntaje']}"
            )
            if e["direccion"]:
                st.write(f"📍 {e['direccion']}")
            if e["telefono"]:
                st.write(f"📞 {e['telefono']}")
            if e["web"]:
                st.markdown(f"🌐 [{e['web']}]({e['web']})")

            if e["temas"]:
                st.markdown("**Problemas detectados:** " + ", ".join(e["temas"]))
                with st.expander("Ver fragmentos de reseñas"):
                    for tema, frags in e["temas"].items():
                        st.markdown(f"*{tema}*")
                        for f in frags[:3]:
                            st.write(f"- {f}")

            msg = borrador_mensaje(e["empresa"], e["temas"], nombre, mi_servicio)
            st.text_area("Borrador de mensaje", msg, height=130, key=f"prosp_msg_{i}")
            wa = link_whatsapp(e["telefono"], msg)
            if wa:
                st.markdown(f"[💬 Abrir en WhatsApp]({wa})")
