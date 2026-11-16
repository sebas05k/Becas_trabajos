# -*- coding: utf-8 -*-
"""Radar Agronómico Global — interfaz Streamlit.  Ejecutar:  streamlit run app.py"""
import os
from html import escape

import streamlit as st

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


def tabla(df, **cfg):
    try:
        st.dataframe(df, width="stretch", hide_index=True, **cfg)
    except TypeError:  # versiones antiguas de Streamlit
        st.dataframe(df, use_container_width=True, hide_index=True, **cfg)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def cargar_radar(profundidad, fuentes, lineas, reliefweb, jooble):
    cfg = {"profundidad": profundidad, "fuentes": list(fuentes), "lineas": list(lineas),
           "reliefweb": reliefweb, "jooble": jooble}
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
                       help="Profunda: más consultas, más países y más páginas de detalle (tarda más).")
fuentes = sb.multiselect("Fuentes activas", rc.FUENTES_DEFECTO, default=rc.FUENTES_DEFECTO,
                         help="ReliefWeb y Jooble requieren una clave gratuita (ver pestaña Alertas).")

sb.markdown("---")
sb.subheader("🎓 Filtros de estudios")
regiones_est = sb.multiselect("Destino", rc.REGIONES, default=[r for r in rc.REGIONES if r != "Remoto"])
solo_elegibles = sb.checkbox("Ocultar las que excluyen a Colombia", value=True)
solo_financiadas = sb.checkbox("Solo con financiación / estipendio", value=False)

sb.subheader("💼 Filtros de trabajo")
regiones_trab = sb.multiselect(
    "Dónde trabajar", rc.REGIONES, default=["Colombia", "Latinoamérica", "Remoto", "Global"],
    help="Latinoamérica: Ecuador, Perú y Bolivia (Comunidad Andina), Mercosur y otros países suelen tener "
         "trámites más sencillos para colombianos. Verifica siempre los requisitos migratorios.")
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
st.caption("Becas, empleo y fondos estrictamente agrarios · Colombia, Latinoamérica, EE. UU., Europa y Asia · "
           "Las fechas se detectan automáticamente: confírmalas siempre en la convocatoria oficial.")

with st.spinner("🛰️ Rastreando fuentes y leyendo convocatorias… (la primera carga puede tardar 1–2 min)"):
    items, log, primera = cargar_radar(profundidad, tuple(fuentes), tuple(lineas_sel),
                                       secreto("RELIEFWEB_APPNAME"), secreto("JOOBLE_API_KEY"))

for o in items:
    rc.puntuar(o, lineas_sel, HOY, boost_nuevo=not primera)

extra_rx = None
if extra.strip():
    import re
    extra_rx = re.compile(re.escape(rc.norm(extra.strip())))


# ───────────────────────── filtros ─────────────────────────
def pasa(o, es_estudio=False, es_trabajo=False):
    if o.origen == "directorio":
        et = "Sin fecha confirmada"
    else:
        et = rc.estado(o.inicio, o.cierre, HOY)[0]
        if et not in estados_sel:
            return False
    if conf_min == "Solo verificadas" and o.confianza != "Alta":
        return False
    if conf_min == "Fuente reconocida o mejor" and o.confianza == "Baja":
        return False
    if es_estudio:
        if o.region not in regiones_est and o.region != "Global":
            return False
        if solo_elegibles and "⚠️" in o.elegibilidad:
            return False
        if solo_financiadas and o.financiamiento != rc.FIN_COMPLETA:
            return False
    if es_trabajo:
        if o.region not in regiones_trab:
            return False
        if cargos_sel and o.cargo_tipo not in cargos_sel:
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
            meses = ", ".join(rc.MES_ES[m - 1] for m in o.meses)
            st.caption(f"Ciclo habitual (referencial): {meses}")
        return
    et, dc, di = rc.estado(o.inicio, o.cierre, HOY)
    st.markdown(f"📅 **Abre:** {rc.fmt_fecha(o.inicio) if o.inicio else 'no indicada'}"
                + (f" _(en {di} d)_" if di is not None else ""))
    if o.cierre:
        extra_txt = f" _({dc} d)_" if dc is not None and dc >= 0 else " _(cerrada)_"
        st.markdown(f"⏳ **Cierra:** {rc.fmt_fecha(o.cierre)}{extra_txt}")
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
            if o.origen == "directorio":
                b += badge("📚 Directorio oficial", "b-info")
            else:
                b += badge(et, CLS_ESTADO.get(et, "b-info"))
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
            b2.link_button("Sitio oficial", o.url_oficial)


def lista(items_, key, nombre_csv):
    if not items_:
        st.info("Sin resultados con los filtros actuales. Prueba ampliar estados, destino o confianza en la barra lateral.")
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
    st.caption("Portales institucionales donde se publican convocatorias cada año. No tienen fecha fija aquí: "
               "entra al sitio y consulta la convocatoria vigente.")
    lista(items_, key, nombre_csv)


# ───────────────────────── pestañas ─────────────────────────
t_top, t_est, t_trab, t_fon, t_bus, t_alert = st.tabs(
    ["🌟 Top semanal", "🎓 Estudios", "💼 Trabajo", "💰 Fondos agrarios", "🔗 Buscadores directos", "📩 Alertas"])

# ── Top
with t_top:
    detectadas = est_det + trab_det + fon_det
    abiertas = [o for o in detectadas if rc.estado(o.inicio, o.cierre, HOY)[0] in ("Abierta", "Cierra pronto")]
    pronto = [o for o in detectadas if rc.estado(o.inicio, o.cierre, HOY)[0] == "Cierra pronto"]
    nuevas = [o for o in detectadas if (not primera) and o.descubierto and
              (HOY - rc.date.fromisoformat(o.descubierto)).days <= 7]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Oportunidades detectadas", len(detectadas))
    m2.metric("Con postulación abierta", len(abiertas))
    m3.metric("Cierran en ≤ 14 días", len(pronto))
    m4.metric("Nuevas esta semana", len(nuevas) if not primera else "—")
    if primera:
        st.caption("Primera ejecución: el contador de «nuevas» empieza a funcionar desde la próxima actualización.")

    top = sorted(detectadas, key=lambda o: -o.puntaje)[:5]
    st.markdown("### 🔥 Top 5 de la semana")
    st.caption("Ranking por: relevancia agronómica, confianza de la fuente, nivel/financiación, urgencia de la "
               "fecha de cierre, coincidencia con tus líneas y cercanía a Colombia.")
    for o in top:
        tarjeta(o)
    cA, cB, cC = st.columns(3)
    for col, titulo, lst in ((cA, "🎓 Mejores estudios", est_det), (cB, "💼 Mejores empleos", trab_det),
                             (cC, "💰 Mejores fondos", fon_det)):
        with col:
            st.markdown(f"#### {titulo}")
            for o in sorted(lst, key=lambda x: -x.puntaje)[:3]:
                with st.container(border=True):
                    st.markdown(f"**{o.titulo[:110]}**")
                    st.caption(f"{o.pais} · cierre {rc.fmt_fecha(o.cierre) if o.cierre else 'sin confirmar'} · "
                               f"{TXT_CONF[o.confianza]}")
                    st.link_button("Abrir", o.url)
    cal = rc.calendario_recurrente(items, HOY)
    if cal:
        st.markdown("### 📆 Programas que suelen abrir este mes o el próximo")
        st.caption("Referencial, según ciclos de años anteriores. Confirma en el sitio oficial.")
        for o in cal:
            tarjeta(o)
    st.download_button("📥 Descargar Top + todo lo detectado (CSV)",
                       rc.a_csv(sorted(detectadas, key=lambda o: -o.puntaje), HOY), "radar_agro_completo.csv",
                       "text/csv", key="csv_top")

# ── Estudios (separados por nivel)
with t_est:
    NIVELES = [("Maestría", rc.NIV_MAESTRIA), ("Doctorado (PhD)", rc.NIV_DOCT), ("Postdoctorado", rc.NIV_POSTDOC),
               ("Cursos", rc.NIV_CURSO), ("Posgrado / Esp.", rc.NIV_POSGRADO), ("Pasantías", rc.NIV_PASANTIA),
               ("Otros", rc.NIV_OTRO)]
    nombres = ["Todos"] + [f"{n} ({sum(1 for o in est_det if k in o.nivel)})" for n, k in NIVELES]
    sub = st.tabs(nombres)
    with sub[0]:
        lista(est_det, "est_todos", "estudios_todos")
        directorio(est_dir, "est_dir_todos", "directorio_estudios")
    for tab, (n, k) in zip(sub[1:], NIVELES):
        with tab:
            lista([o for o in est_det if k in o.nivel], f"est_{k}", f"estudios_{n.lower().replace(' ', '_')}")
            directorio([o for o in est_dir if k in o.nivel], f"estdir_{k}", f"directorio_{k.lower()[:6]}")

# ── Trabajo
with t_trab:
    st.caption("Prioriza Colombia y países de la región donde un ingeniero agrónomo colombiano puede trabajar con "
               "menos barreras. Ajusta el destino en la barra lateral.")
    lista(trab_det, "trab", "empleos_agronomia")
    directorio(trab_dir, "trab_dir", "empleadores_clave")

# ── Fondos
with t_fon:
    st.caption("Fondos, incentivos y convocatorias de proyectos agropecuarios (Colombia, región y organismos internacionales).")
    lista(fon_det, "fon", "fondos_agrarios")
    directorio(fon_dir, "fon_dir", "directorio_fondos")

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
        "Cada lunes a las 7:00 a. m. (Colombia) ejecuta `alerta_semanal.py`, arma el Top 5 y te lo envía por correo.\n\n"
        "1. Sube esta carpeta a un repositorio de GitHub.\n"
        "2. En *Settings → Secrets and variables → Actions* crea: `SMTP_USER` (tu Gmail), `SMTP_PASSWORD` "
        "(contraseña de aplicación de Google), `ALERT_TO` (correo destino), y opcionalmente "
        "`RELIEFWEB_APPNAME` y `JOOBLE_API_KEY`.\n"
        "3. En *Variables* crea `LINEAS` con tus líneas separadas por `;` (p. ej. `Fitomejoramiento;Fitopatología`).\n"
        "4. Ejecuta el workflow una vez con *Run workflow* para probarlo.")
    st.markdown("##### Vista previa del correo con los datos actuales")
    top5 = sorted(est_det + trab_det + fon_det, key=lambda o: -o.puntaje)[:5]
    html = rc.html_correo(top5, {"🎓 Estudios": sorted(est_det, key=lambda o: -o.puntaje)[:3],
                                 "💼 Trabajo": sorted(trab_det, key=lambda o: -o.puntaje)[:3],
                                 "💰 Fondos": sorted(fon_det, key=lambda o: -o.puntaje)[:2]},
                          rc.calendario_recurrente(items, HOY), HOY)
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
    tabla(__import__("pandas").DataFrame(log))
    st.caption("Si una fuente aparece con ⚠️, el resto del radar sigue funcionando. Los portales comerciales pueden "
               "bloquear consultas automáticas; usa la pestaña «Buscadores directos» como respaldo.")
