# -*- coding: utf-8 -*-
"""
Alerta semanal del Radar Agronómico.
Lo ejecuta GitHub Actions cada lunes (ver .github/workflows/alerta_semanal.yml), pero también sirve local:
    python alerta_semanal.py
Variables de entorno: SMTP_USER, SMTP_PASSWORD, ALERT_TO (varios separados por coma),
LINEAS (separadas por ;), PROFUNDIDAD (rapida|profunda), RELIEFWEB_APPNAME, JOOBLE_API_KEY.
"""
import os
import sys

import radar_core as rc

RUTA = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(RUTA, "data")


def lineas_desde_env() -> list[str]:
    crudo = [x.strip() for x in os.environ.get("LINEAS", "").split(";") if x.strip()]
    return [k for k in rc.LINEAS if any(rc.norm(c) in rc.norm(k) for c in crudo)]


def main() -> int:
    hoy = rc.hoy_co()
    lineas = lineas_desde_env()
    cfg = {"profundidad": os.environ.get("PROFUNDIDAD", "profunda"), "lineas": lineas,
           "reliefweb": os.environ.get("RELIEFWEB_APPNAME"), "jooble": os.environ.get("JOOBLE_API_KEY")}
    items, log = rc.recolectar_todo(cfg)
    for fila in log:
        print(f"{fila['Fuente']:<26} {fila['Resultados brutos']:>4}  {fila['Estado']}")

    primera = rc.marcar_novedades(items, os.path.join(DATA, "historial.json"))
    for o in items:
        rc.puntuar(o, lineas, hoy, boost_nuevo=not primera)

    abiertas = [o for o in items if o.origen == "detectada"
                and rc.estado(o.inicio, o.cierre, hoy)[0] != "Cerrada"]
    por_tipo = {t: sorted([o for o in abiertas if o.tipo == t], key=lambda o: -o.puntaje)
                for t in ("estudio", "trabajo", "fondo")}
    top = sorted(abiertas, key=lambda o: -o.puntaje)[:5]
    secciones = {"🎓 Estudios": por_tipo["estudio"][:3], "💼 Trabajo": por_tipo["trabajo"][:3],
                 "💰 Fondos": por_tipo["fondo"][:2]}
    nota = ("Primera ejecución: todo se considera nuevo." if primera else
            f"{sum(1 for o in abiertas if o.descubierto and (hoy - rc.date.fromisoformat(o.descubierto)).days <= 7)} "
            "oportunidades nuevas esta semana.")
    html = rc.html_correo(top, secciones, rc.calendario_recurrente(items, hoy), hoy, nota)

    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "top_semanal.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(DATA, "radar_completo.csv"), "wb") as f:
        f.write(rc.a_csv(sorted(abiertas, key=lambda o: -o.puntaje), hoy))

    u, p, to = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD"), os.environ.get("ALERT_TO")
    if u and p and to:
        rc.enviar_correo(html, f"🌾 Radar Agronómico — Top semanal {rc.fmt_fecha(hoy)}", to, u, p)
        print("Correo enviado a", to)
    else:
        print("Sin credenciales SMTP: se guardó data/top_semanal.html y data/radar_completo.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
