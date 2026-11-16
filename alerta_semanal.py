# -*- coding: utf-8 -*-
"""
Alerta semanal del Radar Agronómico por Telegram.
Lo ejecuta GitHub Actions cada lunes (.github/workflows/alerta_semanal.yml).
"""
from html import escape
import os
import sys

import radar_core as rc
import requests

RUTA = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(RUTA, "data")


def lineas_desde_env() -> list[str]:
  crudo = [
      x.strip() for x in os.environ.get("LINEAS", "").split(";") if x.strip()
  ]
  return [k for k in rc.LINEAS if any(rc.norm(c) in rc.norm(k) for c in crudo)]


def formatear_item(o: rc.Oportunidad, hoy) -> str:
  et, dc, _ = rc.estado(o.inicio, o.cierre, hoy)
  if o.cierre and dc is not None and dc >= 0:
    cierre = f"{rc.fmt_fecha(o.cierre)} ({dc}d)"
  elif o.cierre:
    cierre = rc.fmt_fecha(o.cierre)
  else:
    cierre = "sin confirmar"

  conf = {"Alta": "✅", "Media": "🟡", "Baja": "🔎"}.get(o.confianza, "🔎")
  cat = o.nivel or o.cargo_tipo or o.tipo.capitalize()
  titulo = escape(o.titulo[:110])
  pais = escape(o.pais)
  return (
      f'{conf} <a href="{escape(o.url)}"><b>{titulo}</b></a>\n'
      f"   📍 {pais} · {escape(cat)} · ⏳ Cierre: {cierre}"
  )


def enviar_telegram(texto: str, token: str, chat_id: str) -> None:
  url = f"https://api.telegram.org/bot{token}/sendMessage"
  r = requests.post(
      url,
      json={
          "chat_id": chat_id,
          "text": texto,
          "parse_mode": "HTML",
          "disable_web_page_preview": True,
      },
      timeout=15,
  )
  r.raise_for_status()


def main() -> int:
  hoy = rc.hoy_co()
  lineas = lineas_desde_env()
  cfg = {
      "profundidad": os.environ.get("PROFUNDIDAD", "profunda"),
      "lineas": lineas,
      "reliefweb": os.environ.get("RELIEFWEB_APPNAME"),
      "jooble": os.environ.get("JOOBLE_API_KEY"),
  }
  items, log = rc.recolectar_todo(cfg)
  for fila in log:
    print(
        f"{fila['Fuente']:<26} {fila['Resultados brutos']:>4}  {fila['Estado']}"
    )

  primera = rc.marcar_novedades(items, os.path.join(DATA, "historial.json"))
  for o in items:
    rc.puntuar(o, lineas, hoy, boost_nuevo=not primera)

  abiertas = [
      o
      for o in items
      if o.origen == "detectada"
      and rc.estado(o.inicio, o.cierre, hoy)[0] != "Cerrada"
  ]
  por_tipo = {
      t: sorted(
          [o for o in abiertas if o.tipo == t], key=lambda o: -o.puntaje
      )
      for t in ("estudio", "trabajo", "fondo")
  }
  top = sorted(abiertas, key=lambda o: -o.puntaje)[:5]

  nuevas = sum(
      1
      for o in abiertas
      if o.descubierto
      and (hoy - rc.date.fromisoformat(o.descubierto)).days <= 7
  )
  resumen = (
      "Primera ejecución del radar."
      if primera
      else f"🆕 {nuevas} oportunidades nuevas esta semana."
  )

  # Mensaje 1: Top 5 general
  msg1 = [
      f"🌾 <b>Radar Agronómico — {rc.fmt_fecha(hoy)}</b>",
      f"<i>{resumen}</i>\n",
      "🔥 <b>TOP 5 DE LA SEMANA</b>",
  ]
  msg1 += [formatear_item(o, hoy) for o in top] or [
      "Sin resultados relevantes esta semana."
  ]

  # Mensaje 2: Desglose por categoría
  msg2 = ["📂 <b>DESTACADOS POR CATEGORÍA</b>"]
  secciones = [
      ("🎓 <b>Estudios</b>", por_tipo["estudio"][:3]),
      ("💼 <b>Trabajo</b>", por_tipo["trabajo"][:3]),
      ("💰 <b>Fondos agrarios</b>", por_tipo["fondo"][:2]),
  ]
  for titulo_sec, lista in secciones:
    if lista:
      msg2.append(f"\n{titulo_sec}")
      msg2 += [formatear_item(o, hoy) for o in lista]

  os.makedirs(DATA, exist_ok=True)
  with open(os.path.join(DATA, "radar_completo.csv"), "wb") as f:
    f.write(rc.a_csv(sorted(abiertas, key=lambda o: -o.puntaje), hoy))

  token = os.environ.get("TELEGRAM_TOKEN")
  chat_id = os.environ.get("TELEGRAM_CHAT_ID")
  if token and chat_id:
    enviar_telegram("\n\n".join(msg1), token, chat_id)
    if len(msg2) > 1:
      enviar_telegram("\n\n".join(msg2), token, chat_id)
    print("Alerta enviada por Telegram.")
  else:
    print("Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
