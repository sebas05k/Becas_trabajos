# -*- coding: utf-8 -*-
"""
Alerta semanal del Radar Agronómico (maestrías + empleo en Colombia + Brasil/Japón/China).
Lo ejecuta GitHub Actions cada lunes (ver .github/workflows/alerta_semanal.yml), pero también sirve local:
    python alerta_semanal.py
Variables de entorno:
  SMTP_USER, SMTP_PASSWORD, ALERT_TO (varios separados por coma)
  LINEAS (separadas por ;)           NIVELES (def. "Maestría")
  PAISES_PRIORIDAD (def. "Brasil;Japón;China;Colombia")      REGIONES_TRABAJO (def. "Colombia")
  PROFUNDIDAD (rapida|profunda)      SERPER_API_KEY, RELIEFWEB_APPNAME, JOOBLE_API_KEY (opcionales)
"""
import os
import sys

import radar_core as rc

RUTA = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(RUTA, "data")


def lista_env(nombre: str, defecto: list[str]) -> list[str]:
    crudo = [x.strip() for x in os.environ.get(nombre, "").split(";") if x.strip()]
    return crudo or defecto


def lineas_desde_env() -> list[str]:
    crudo = [x.strip() for x in os.environ.get("LINEAS", "").split(";") if x.strip()]
    return [k for k in rc.LINEAS if any(rc.norm(c) in rc.norm(k) for c in crudo)]


def main() -> int:
    hoy = rc.hoy_co()
    lineas = lineas_desde_env()
    niveles = lista_env("NIVELES", rc.ENFOQUE_DEFECTO["niveles_estudio"])
    paises = lista_env("PAISES_PRIORIDAD", rc.ENFOQUE_DEFECTO["paises_prioridad"])
    regiones = lista_env("REGIONES_TRABAJO", rc.ENFOQUE_DEFECTO["regiones_trabajo"])

    cfg = {"profundidad": os.environ.get("PROFUNDIDAD", "profunda"), "lineas": lineas,
           "reliefweb": os.environ.get("RELIEFWEB_APPNAME"), "jooble": os.environ.get("JOOBLE_API_KEY"),
           "serper": os.environ.get("SERPER_API_KEY")}
    items, log = rc.recolectar_todo(cfg)
    for fila in log:
        print(f"{fila['Fuente']:<32} {fila['Resultados brutos']:>4}  {fila['Estado']}")

    primera = rc.marcar_novedades(items, os.path.join(DATA, "historial.json"))
    for o in items:
        rc.puntuar(o, lineas, hoy, boost_nuevo=not primera, paises_prioridad=paises, niveles_pref=niveles)

    est, trab, fon = rc.filtrar_enfoque(items, niveles, regiones, hoy)
    est = sorted(est, key=lambda o: -o.puntaje)
    trab = sorted(trab, key=lambda o: -o.puntaje)
    fon = sorted(fon, key=lambda o: -o.puntaje)
    top = sorted(est[:3] + trab[:3], key=lambda o: -o.puntaje)[:5]

    estudios_dir = [o for o in items if o.tipo == "estudio" and any(n in o.nivel for n in niveles)]
    secciones = {"🎓 Maestrías": est[:4]}
    for pais, lst in rc.top_por_pais(estudios_dir, paises[:3], 2, hoy).items():
        secciones[f"🌎 {pais}"] = lst
    secciones["💼 Empleo en " + ", ".join(regiones)] = trab[:4]
    secciones["💰 Fondos"] = fon[:2]

    def nuevas(lst):
        return sum(1 for o in lst if o.descubierto and (hoy - rc.date.fromisoformat(o.descubierto)).days <= 7)
    nota = ("Primera ejecución: todo se considera nuevo." if primera else
            f"{nuevas(est)} maestrías y {nuevas(trab)} empleos nuevos esta semana.")
    html = rc.html_correo(top, secciones, rc.calendario_recurrente(items, hoy, niveles), hoy, nota)

    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "top_semanal.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(DATA, "radar_completo.csv"), "wb") as f:
        f.write(rc.a_csv(est + trab + fon, hoy))

    u, p, to = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD"), os.environ.get("ALERT_TO")
    if u and p and to:
        rc.enviar_correo(html, f"🌾 Radar Agronómico — Top semanal {rc.fmt_fecha(hoy)}", to, u, p)
        print("Correo enviado a", to)
    else:
        print("Sin credenciales SMTP: se guardó data/top_semanal.html y data/radar_completo.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
