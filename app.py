# -*- coding: utf-8 -*-
"""Radar Agronómico Global — interfaz Streamlit.  Ejecutar:  streamlit run app.py"""
import os
import re
from html import escape

import pandas as pd
import streamlit as st
import prospeccion

tab_trabajo, tab_estudios, tab_prosp = st.tabs(["💼 Trabajo", "🎓 Estudios", "🎯 Prospección"])
with tab_prosp:
    prospeccion.render()

import radar_core as rc

st.set_page_config(page_title="Radar Agronómico Global", page_icon="🌾", layout="wide")

HIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "historial.json")
HOY = rc.hoy_co()
POR_PAGINA = 15

st.markdown("""
<style>
.badge{display:inline-block;padding:2px 9px;margin:0 6px 4px 0;border-radius:999px;font-size:.78rem;
       font-weight:600;border:1px solid rgba(128,128,128,.35)}
.b-ok{background:rgba(46,160,67,.15);color:#2ea043}
.b-warn{background:rgba(210,153,34,.15);color:#d29922}
.b-bad{background:rgba(248,81,73,.15);color:#f85149}
.b-info{background:rgba(56,139,253,.15);color:#388bfd}
.b-new{background:rgba(163,113,247,.18);color:#a371f7}
.meta{opacity:.75;font-size:.92rem;margin-bottom:.3rem}
</style>
""", unsafe_allow_html=True)


# ───────────────────────── utilidades ─────────────────────────
def secreto(nombre: str):
    try:
        v = st.secrets.get(nombre)
    except Exception:
        v = None
    return v or os.environ.get(nombre)


def badge(txt: str, cls: str = "b-info") -> str:
    return f'<span class="badge {cls}">{escape(txt)}</span>'


CLS_CONF = {"Alta": "b-ok", "Media": "b-warn", "Baja": "b-bad"}
TXT_CONF = {"Alta": "✅ Verificada", "Media": "🟡 Fuente reconocida", "Baja": "🔎 Por verificar"}
CLS_ESTADO = {"Abierta": "b-ok", "Cierra pronto": "b-warn", "Próxima apertura": "b-info",
              "Cerrada": "b-bad", "Sin fecha confirmada": "b-info"}
NIVELES_OPC = [rc.NIV_MAESTRIA, rc.NIV_DOCT, rc.NIV_POSTDOC, rc.NIV_CURSO, rc.NIV_POSGRADO, rc.NIV_PASANTIA]


def tabla(df, **cfg):
    try:
        st.dataframe(df, width="stretch", hide_index=True, **cfg)
    except TypeError:  # versiones antiguas de Streamlit
        st.dataframe(df, use_container_width=True, hide_index=True, **cfg)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def cargar_radar(profundidad, fuentes, lineas, reliefweb, jooble, serper):
    cfg = {"profundidad": profundidad, "fuentes": list(fuentes), "lineas": list(lineas),
           "reliefweb": reliefweb, "jooble": jooble, "serper": serper}
    items, log = rc.recolectar_todo(cfg)
    primera = rc.marcar_novedades(items, HIST)
    return items, log, primera


# ───────────────────────── barra lateral ─────────────────────────
sb = st.sidebar
sb.header("⚙️ Radar")
lineas_sel = sb.multiselect("Líneas de investigación", list(rc.LINEAS),
                            help="Se usan para buscar más a fondo, priorizar el ranking y (opcional) filtrar.")
extra = sb.text_input("Palabra clave extra (opcional)", placeholder="p. ej. Fusarium, cacao, CRISPR")
solo_lineas = sb.checkbox("Mostrar solo lo que coincide con mis líneas", value=False,
                          disabled=not (lineas_sel or extra))
profundidad = sb.radio("Profundidad de búsqueda", ["rapida", "profunda"], horizontal=True,
                       format_func=lambda x: "⚡ Rápida" if x == "rapida" else "🔬 Profunda",
                       help="Profunda: más consultas, más páginas leídas y más portales oficiales escaneados (tarda más).")
fuentes = sb.multiselect("Fuentes activas", rc.FUENTES_DEFECTO, default=rc.FUENTES_DEFECTO,
                         help="ReliefWeb, Jooble y Serper requieren una clave gratuita (ver pestaña Alertas).")

sb.markdown("---")
sb.subheader("🎓 Estudios")
niveles_sel = sb.multiselect("Nivel", NIVELES_OPC, default=[rc.NIV_MAESTRIA])
incluir_sin_nivel = sb.checkbox("Incluir las de nivel no identificado", value=True)
paises_prio = sb.multiselect("Países prioritarios", rc.PAISES,
                             default=[p for p in rc.ENFOQUE_DEFECTO["paises_prioridad"] if p in rc.PAISES],
                             help="Suben en el ranking y tienen su propia sección en el Top.")
solo_paises = sb.checkbox("Mostrar solo estos países", value=False)
solo_elegibles = sb.checkbox("Ocultar las que excluyen a Colombia", value=True)
solo_financiadas = sb.checkbox("Solo con financiación / estipendio", value=False)

sb.subheader("💼 Trabajo")
regiones_trab = sb.multiselect("Dónde trabajar", rc.REGIONES, default=rc.ENFOQUE_DEFECTO["regiones_trabajo"],
                               help="Por defecto solo Colombia. Agrega Latinoamérica o Remoto si quieres ampliar.")
ciudad = sb.text_input("Ciudad o departamento (opcional)", placeholder="p. ej. Palmira, Meta, Antioquia")
cargos_sel = sb.multiselect("Tipo de cargo", rc.CARGOS)

sb.markdown("---")
estados_sel = sb.multiselect("Estado de la convocatoria",
                             ["Abierta", "Cierra pronto", "Próxima apertura", "Sin fecha confirmada", "Cerrada"],
                             default=["Abierta", "Cierra pronto", "Próxima apertura", "Sin fecha confirmada"])
conf_min = sb.selectbox("Confianza mínima", ["Cualquiera", "Fuente reconocida o mejor", "Solo verificadas"])
orden = sb.selectbox("Ordenar por", ["Mejor puntaje", "Cierre más próximo", "Más reciente"])
vista = sb.radio("Vista", ["Tarjetas", "Tabla"], horizontal=True)

if sb.button("🔄 Actualizar ahora (limpiar caché)"):
    st.cache_data.clear()
    st.rerun()

# ───────────────────────── carga ─────────────────────────
st.title("🌾 Radar Global de Oportunidades — Ingeniería Agronómica")
st.caption("Maestrías, empleo en Colombia y fondos agrarios · Brasil, Japón, China, Colombia y más · "
           "Las fechas se leen automáticamente de las páginas: confírmalas siempre en la convocatoria oficial.")

with st.spinner("🛰️ Escaneando la web y los portales oficiales… (la primera carga puede tardar 2–4 min)"):
    items, log, primera = cargar_radar(profundidad, tuple(fuentes), tuple(lineas_sel),
                                       secreto("RELIEFWEB_APPNAME"), secreto("JOOBLE_API_KEY"),
                                       secreto("SERPER_API_KEY"))

for o in items:
    rc.puntuar(o, lineas_sel, HOY, boost_nuevo=not primera, paises_prioridad=paises_prio, niveles_pref=niveles_sel)

extra_rx = re.compile(re.escape(rc.norm(extra.strip()))) if extra.strip() else None
ciudad_rx = re.compile(re.escape(rc.norm(ciudad.strip()))) if ciudad.strip() else None


# ───────────────────────── filtros ─────────────────────────
def nivel_ok(o):
    if o.nivel in ("", rc.NIV_OTRO):
        return incluir_sin_nivel
    return (not niveles_sel) or any(n in o.nivel for n in niveles_sel)


def pasa(o, es_estudio=False, es_trabajo=False):
    if o.origen == "detectada":
        if rc.estado(o.inicio, o.cierre, HOY)[0] not in estados_sel:
            return False
    if conf_min == "Solo verificadas" and o.confianza != "Alta":
        return False
    if conf_min == "Fuente reconocida o mejor" and o.confianza == "Baja":
        return False
    if es_estudio:
        if not nivel_ok(o):
            return False
        if solo_paises and paises_prio and o.pais not in paises_prio:
            return False
        if solo_elegibles and "⚠️" in o.elegibilidad:
            return False
        if solo_financiadas and o.financiamiento != rc.FIN_COMPLETA:
            return False
    if es_trabajo:
        if regiones_trab and o.region not in regiones_trab:
            return False
        if cargos_sel and o.cargo_tipo not in cargos_sel:
            return False
        if ciudad_rx and not ciudad_rx.search(rc.norm(o.titulo + " " + o.resumen + " " + o.texto)):
            return False
    if (lineas_sel or extra_rx) and solo_lineas:
        coincide = bool(set(o.lineas) & set(lineas_sel)) if lineas_sel else False
        if extra_rx and extra_rx.search(rc.norm(o.titulo + " " + o.resumen + " " + o.texto)):
            coincide = True
        if not coincide:
            return False
    return True


def ordenar(lista):
    if orden == "Cierre más próximo":
        return sorted(lista, key=lambda o: (o.cierre is None, o.cierre or HOY, -o.puntaje))
    if orden == "Más reciente":
        return sorted(lista, key=lambda o: (o.publicado is None, -(o.publicado.toordinal() if o.publicado else 0)))
    return sorted(lista, key=lambda o: -o.puntaje)


est_det = ordenar([o for o in items if o.tipo == "estudio" and o.origen == "detectada" and pasa(o, es_estudio=True)])
est_dir = [o for o in items if o.tipo == "estudio" and o.origen == "directorio" and pasa(o, es_estudio=True)]
trab_det = ordenar([o for o in items if o.tipo == "trabajo" and o.origen == "detectada" and pasa(o, es_trabajo=True)])
trab_dir = [o for o in items if o.tipo == "trabajo" and o.origen == "directorio" and pasa(o, es_trabajo=True)]
fon_det = ordenar([o for o in items if o.tipo == "fondo" and o.origen == "detectada" and pasa(o)])
fon_dir = [o for o in items if o.tipo == "fondo" and o.origen == "directorio" and pasa(o)]


# ───────────────────────── tarjetas ─────────────────────────
def fila_fechas(o):
    if o.origen == "directorio":
        st.markdown("📅 **Fechas:** ver convocatoria vigente en el sitio oficial")
        if o.meses:
            st.caption("Ciclo habitual (referencial): " + ", ".join(rc.MES_ES[m - 1] for m in o.meses))
        return
    et, dc, di = rc.estado(o.inicio, o.cierre, HOY)
    if o.tipo == "trabajo" and not o.cierre:
        st.markdown(f"📅 **Publicada:** {rc.fmt_fecha(o.publicado) if o.publicado else 'fecha no indicada'}")
        return
    st.markdown(f"📅 **Abre:** {rc.fmt_fecha(o.inicio) if o.inicio else 'no indicada'}"
                + (f" _(en {di} d)_" if di is not None else ""))
    if o.cierre:
        st.markdown(f"⏳ **Cierra:** {rc.fmt_fecha(o.cierre)}"
                    + (f" _({dc} d)_" if dc is not None and dc >= 0 else " _(cerrada)_"))
    else:
        st.markdown("⏳ **Cierra:** sin confirmar")


def tarjeta(o):
    et = "Directorio" if o.origen == "directorio" else rc.estado(o.inicio, o.cierre, HOY)[0]
    with st.container(border=True):
        izq, der = st.columns([3.3, 1.7])
        with izq:
            st.markdown(f"**{o.titulo}**")
            meta = " · ".join(x for x in [o.institucion, o.pais if o.pais != "No especificado" else "",
                                          o.nivel or o.cargo_tipo] if x)
            if meta:
                st.markdown(f'<div class="meta">{escape(meta)}</div>', unsafe_allow_html=True)
            b = badge(TXT_CONF[o.confianza], CLS_CONF[o.confianza])
            b += badge("📚 Directorio oficial", "b-info") if o.origen == "directorio" else badge(et, CLS_ESTADO.get(et, "b-info"))
            if o.financiamiento != rc.FIN_ND:
                b += badge("💰 " + o.financiamiento, "b-ok" if o.financiamiento == rc.FIN_COMPLETA else "b-warn")
            if o.elegibilidad != "No indicada" and o.tipo != "trabajo":
                b += badge(o.elegibilidad, "b-warn" if "⚠️" in o.elegibilidad else "b-ok")
            if not primera and o.descubierto and (HOY - rc.date.fromisoformat(o.descubierto)).days <= 7:
                b += badge("🆕 Nueva", "b-new")
            for bd in o.banderas:
                b += badge("⚠️ " + bd, "b-bad")
            st.markdown(b, unsafe_allow_html=True)
            if o.lineas:
                st.caption("🔬 " + " · ".join(o.lineas[:4]))
        with der:
            fila_fechas(o)
        with st.expander("📋 Requisitos, detalles y verificación"):
            if o.resumen:
                st.write(o.resumen[:500])
            if o.requisitos:
                st.markdown("**Requisitos / elegibilidad (extracto):**")
                st.write(o.requisitos)
            st.markdown(f"**Fuente:** {o.fuente}  ·  **Puntaje:** {o.puntaje}")
            st.caption(o.verificacion)
        b1, b2, _ = st.columns([1, 1, 3])
        b1.link_button("Ver convocatoria" if o.origen == "detectada" else "Ir al sitio", o.url)
        if o.url_oficial:
            b2.link_button("Portal oficial", o.url_oficial)


def mini(o):
    with st.container(border=True):
        st.markdown(f"**{o.titulo[:95]}**")
        if o.origen == "directorio":
            st.caption(f"📚 Directorio oficial · {o.institucion[:40]}")
        else:
            st.caption(f"{o.pais} · cierre {rc.fmt_fecha(o.cierre) if o.cierre else 'sin confirmar'} · {TXT_CONF[o.confianza]}")
        st.link_button("Abrir", o.url)


def lista(items_, key, nombre_csv):
    if not items_:
        st.info("Sin resultados con los filtros actuales. Prueba ampliar estados, países o confianza en la barra lateral.")
        return
    c1, c2 = st.columns([4, 1])
    c1.caption(f"{len(items_)} resultados")
    c2.download_button("📥 CSV", rc.a_csv(items_, HOY), f"{nombre_csv}.csv", "text/csv", key=f"csv_{key}")
    if vista == "Tabla":
        tabla(rc.a_dataframe(items_, HOY),
              column_config={"Enlace": st.column_config.LinkColumn("Enlace", display_text="Abrir"),
                             "Enlace oficial": st.column_config.LinkColumn("Enlace oficial", display_text="Abrir")})
        return
    n = st.session_state.get(f"n_{key}", POR_PAGINA)
    for o in items_[:n]:
        tarjeta(o)
    if n < len(items_) and st.button(f"Mostrar más ({len(items_) - n} restantes)", key=f"mas_{key}"):
        st.session_state[f"n_{key}"] = n + POR_PAGINA
        st.rerun()


def directorio(items_, key, nombre_csv):
    if not items_:
        return
    st.markdown("---")
    st.subheader("📚 Directorio oficial de programas e instituciones recurrentes")
    st.caption("Portales institucionales donde se publican convocatorias cada año. Entra al sitio y consulta la "
               "convocatoria vigente (si el escaneo encontró una con fecha, aparece arriba como detectada).")
    lista(items_, key, nombre_csv)


# ───────────────────────── pestañas ─────────────────────────
t_top, t_est, t_trab, t_fon, t_guia, t_bus, t_alert = st.tabs(
    ["🌟 Top semanal", "🎓 Estudios", "💼 Trabajo", "💰 Fondos agrarios", "📘 Guía de maestría",
     "🔗 Buscadores directos", "📩 Alertas"])

# ── Top
with t_top:
    detectadas = est_det + trab_det + fon_det
    pronto = [o for o in detectadas if rc.estado(o.inicio, o.cierre, HOY)[0] == "Cierra pronto"]
    con_fecha = [o for o in est_det + fon_det + trab_det if o.cierre]
    nuevas = [o for o in detectadas if (not primera) and o.descubierto and
              (HOY - rc.date.fromisoformat(o.descubierto)).days <= 7]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Maestrías detectadas", len(est_det))
    m2.metric("Empleos detectados", len(trab_det))
    m3.metric("Con fecha de cierre leída", len(con_fecha))
    m4.metric("Cierran en ≤ 14 días", len(pronto))
    if primera:
        st.caption("Primera ejecución: la marca «nueva» empieza a funcionar desde la próxima actualización.")

    st.markdown(f"### 🎓 Top 5 — {', '.join(niveles_sel) or 'estudios'}")
    for o in sorted(est_det, key=lambda o: -o.puntaje)[:5]:
        tarjeta(o)
    if not est_det:
        st.info("Aún no hay convocatorias detectadas con fecha. Mira el directorio oficial en la pestaña Estudios.")

    if paises_prio:
        st.markdown("### 🌎 Tus países prioritarios")
        todos_est = [o for o in items if o.tipo == "estudio" and pasa(o, es_estudio=True)]
        por_pais = rc.top_por_pais(todos_est, paises_prio, 3, HOY)
        cols = st.columns(min(len(paises_prio), 4))
        for i, (pais, lst) in enumerate(por_pais.items()):
            with cols[i % len(cols)]:
                st.markdown(f"#### {pais}")
                if not lst:
                    st.caption("Sin resultados con los filtros actuales.")
                for o in lst:
                    mini(o)

    st.markdown("### 💼 Top 5 — empleo " + ("en " + ", ".join(regiones_trab) if regiones_trab else ""))
    for o in sorted(trab_det, key=lambda o: -o.puntaje)[:5]:
        tarjeta(o)
    if not trab_det:
        st.info("Sin ofertas detectadas. Revisa «Estado de las fuentes» abajo y los Buscadores directos.")

    st.markdown("### 💰 Mejores fondos")
    cF = st.columns(3)
    for i, o in enumerate(sorted(fon_det, key=lambda x: -x.puntaje)[:3]):
        with cF[i]:
            mini(o)

    cal = rc.calendario_recurrente(items, HOY, niveles_sel)
    if cal:
        st.markdown("### 📆 Programas que suelen abrir este mes o el próximo")
        st.caption("Referencial, según ciclos de años anteriores. Confirma en el sitio oficial.")
        for o in cal:
            tarjeta(o)
    st.download_button("📥 Descargar todo lo detectado (CSV)",
                       rc.a_csv(sorted(detectadas, key=lambda o: -o.puntaje), HOY), "radar_agro_completo.csv",
                       "text/csv", key="csv_top")

# ── Estudios
with t_est:
    st.caption("Enfocado en: " + (", ".join(niveles_sel) or "todos los niveles") +
               ". Cambia el nivel y los países en la barra lateral.")
    nombres = ["Todos"] + [f"{n} ({sum(1 for o in est_det if n in o.nivel)})" for n in niveles_sel]
    if incluir_sin_nivel:
        nombres.append(f"Nivel no identificado ({sum(1 for o in est_det if o.nivel in ('', rc.NIV_OTRO))})")
    sub = st.tabs(nombres)
    with sub[0]:
        lista(est_det, "est_todos", "estudios_todos")
        directorio(est_dir, "est_dir_todos", "directorio_estudios")
    for tab, n in zip(sub[1:1 + len(niveles_sel)], niveles_sel):
        with tab:
            lista([o for o in est_det if n in o.nivel], f"est_{n}", f"estudios_{n.lower().split()[0]}")
            directorio([o for o in est_dir if n in o.nivel], f"estdir_{n}", f"directorio_{n.lower().split()[0]}")
    if incluir_sin_nivel:
        with sub[-1]:
            lista([o for o in est_det if o.nivel in ("", rc.NIV_OTRO)], "est_sin", "estudios_sin_nivel")

# ── Trabajo
with t_trab:
    st.caption("Enfocado en: " + (", ".join(regiones_trab) or "todas las regiones") +
               ". Las ofertas suelen tener fecha de publicación, no de cierre.")
    lista(trab_det, "trab", "empleos_agronomia")
    directorio(trab_dir, "trab_dir", "empleadores_clave")

# ── Fondos
with t_fon:
    st.caption("Fondos, incentivos y convocatorias de proyectos agropecuarios (Colombia, región y organismos internacionales).")
    lista(fon_det, "fon", "fondos_agrarios")
    directorio(fon_dir, "fon_dir", "directorio_fondos")

# ── Guía
with t_guia:
    st.markdown("""
### Qué necesitas para hacer una maestría después del pregrado
Los requisitos exactos cambian por programa y por país: **revisa siempre el edital o la convocatoria**.
Lo que casi siempre piden:

1. **Título de pregrado** (Ingeniero Agrónomo) ya otorgado, o certificado de que estás próximo a graduarte.
2. **Documentos académicos**: diploma, certificado de notas y promedio. Para estudiar fuera suelen exigir
   **apostilla** (Colombia es parte del Convenio de La Haya) y a veces traducción oficial.
3. **Hoja de vida y carta de motivación.**
4. **Dos o tres cartas de recomendación** de profesores o directores de tesis.
5. **Anteproyecto o propuesta de investigación** (clave en Brasil, Japón y China, y en toda maestría de investigación).
6. **Idioma**: inglés para programas en inglés (muchos piden IELTS ~6.0–6.5 o TOEFL iBT ~80, según el programa);
   portugués en Brasil (varias maestrías piden prueba o entrevista; algunas aceptan inglés); inglés o chino/japonés
   según el idioma de enseñanza en China y Japón.
7. **Contactar antes a un profesor/orientador**: en Brasil (orientador), Japón (supervisor) y China (supervisor)
   es muy común, y a veces obligatorio, para que te acepten o te patrocinen.
8. **Pruebas internas**: examen escrito, análisis de currículo y/o entrevista (muy frecuente en Brasil y Colombia).

**Ayudan, pero no siempre son obligatorios:** buen promedio, tesis de pregrado, semilleros de investigación,
pósters o artículos, experiencia en laboratorio o campo.

**Tiempos:** empieza **8–12 meses antes**. Apostillar documentos, certificar el idioma y conseguir cartas toma semanas,
y muchas becas (MEXT, CSC, GKS, Chevening, DAAD) cierran entre 6 y 12 meses antes de que inicie el programa.

**Maestría de investigación vs. profundización:** si piensas hacer doctorado después, elige una *maestría de investigación*
(con tesis); suele abrir más puertas a becas y programas doctorales.

**Financiación:** ICETEX (Colombia), MEXT (Japón), CSC (China), GKS (Corea), CAPES/CNPq (Brasil), Chevening, DAAD,
Fulbright, Erasmus Mundus. Mira cuáles incluyen a Colombia en el directorio de la pestaña Estudios.
""")

# ── Buscadores directos
with t_bus:
    st.markdown("Búsquedas **ya armadas** en los portales más útiles (funcionan siempre, sin depender del rastreo). "
                "Cambia los términos si quieres afinar.")
    base_es = rc.LINEAS[lineas_sel[0]]["es"] if lineas_sel else "agronomía"
    base_en = rc.LINEAS[lineas_sel[0]]["en"] if lineas_sel else "agronomy"
    base_pt = rc.LINEAS[lineas_sel[0]]["pt"] if lineas_sel else "agronomia"
    c1, c2, c3 = st.columns(3)
    q_es = c1.text_input("Español", base_es, key=f"qes_{base_es}")
    q_en = c2.text_input("Inglés", base_en, key=f"qen_{base_en}")
    q_pt = c3.text_input("Portugués", base_pt, key=f"qpt_{base_pt}")
    for grupo, enlaces in rc.enlaces_directos(q_es, q_en, q_pt).items():
        st.markdown(f"#### {grupo}")
        cols = st.columns(3)
        for i, (nombre, url) in enumerate(enlaces):
            cols[i % 3].link_button(nombre, url)

# ── Alertas
with t_alert:
    st.markdown("### 📩 Alerta semanal automática")
    st.markdown(
        "Streamlit no corre tareas programadas, así que la alerta vive en **GitHub Actions** (gratis). "
        "Cada lunes a las 7:00 a. m. (Colombia) ejecuta `alerta_semanal.py` y te envía por correo: "
        "**maestrías**, **mejores opciones en Brasil, Japón y China** y **empleo en Colombia**.\n\n"
        "1. Sube esta carpeta a un repositorio de GitHub.\n"
        "2. En *Settings → Secrets and variables → Actions* crea los **Secrets**: `SMTP_USER` (tu Gmail), "
        "`SMTP_PASSWORD` (contraseña de aplicación de Google), `ALERT_TO` (correo destino). Opcionales y gratuitos: "
        "`SERPER_API_KEY` (serper.dev, búsqueda web tipo Google), `RELIEFWEB_APPNAME`, `JOOBLE_API_KEY`.\n"
        "3. En *Variables* puedes definir: `LINEAS` (p. ej. `Fitomejoramiento;Fitopatología`), `NIVELES` "
        "(por defecto `Maestría`), `PAISES_PRIORIDAD` (por defecto `Brasil;Japón;China;Colombia`) y "
        "`REGIONES_TRABAJO` (por defecto `Colombia`).\n"
        "4. Ejecuta el workflow una vez con *Run workflow* para probarlo.")
    st.markdown("##### Vista previa del correo con los datos actuales")
    top5 = sorted(est_det + trab_det, key=lambda o: -o.puntaje)[:5]
    secs = {"🎓 Maestrías": sorted(est_det, key=lambda o: -o.puntaje)[:4]}
    for pais, lst in rc.top_por_pais(est_det + est_dir, paises_prio[:3], 2, HOY).items():
        secs[f"🌎 {pais}"] = lst
    secs["💼 Empleo en " + (", ".join(regiones_trab) or "Colombia")] = sorted(trab_det, key=lambda o: -o.puntaje)[:4]
    secs["💰 Fondos"] = sorted(fon_det, key=lambda o: -o.puntaje)[:2]
    html = rc.html_correo(top5, secs, rc.calendario_recurrente(items, HOY, niveles_sel), HOY)
    st.components.v1.html(html, height=520, scrolling=True)
    u, p = secreto("SMTP_USER"), secreto("SMTP_PASSWORD")
    dest = st.text_input("Enviar prueba a:", value=secreto("ALERT_TO") or "")
    if st.button("✉️ Enviar correo de prueba", disabled=not (u and p and dest)):
        try:
            rc.enviar_correo(html, "🌾 Radar Agronómico — prueba", dest, u, p)
            st.success("Correo enviado.")
        except Exception as e:
            st.error(f"No se pudo enviar: {e}")
    if not (u and p):
        st.caption("Para probar el envío desde aquí agrega SMTP_USER y SMTP_PASSWORD en `.streamlit/secrets.toml`.")

# ── Estado de fuentes
with st.expander("🩺 Estado de las fuentes"):
    tabla(pd.DataFrame(log))
    st.caption("Si una fuente aparece con ⚠️, el resto del radar sigue funcionando. Los portales comerciales pueden "
               "bloquear consultas automáticas; usa «Buscadores directos» como respaldo.")
