#!/usr/bin/env python3
"""
Extrae Nemotécnico y Variación porcentual del mercado local de renta variable
de la Bolsa de Valores de Colombia y guarda docs/data/bvc.json

La tabla de la BVC viene ordenada por Volúmenes (descendente), así que las
primeras 10 filas son las 10 especies más negociadas del día.

Fines de semana y festivos: la página trae en su selector de fecha el último
día hábil disponible. Ese valor es el que se publica. Si por alguna razón no
se puede leer, se calcula el último día hábil según el calendario colombiano.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

URL = "https://www.bvc.com.co/mercado-local-en-linea?tab=renta-variable_mercado-local"
BOGOTA = ZoneInfo("America/Bogota")
TOP_N = 10
OUTPUT = Path(__file__).resolve().parents[1] / "docs" / "data" / "bvc.json"

EXTRACT_JS = """
() => {
  const filas = Array.from(document.querySelectorAll('table tbody tr'));
  const titulo = (td) => {
    if (!td) return '';
    const d = td.querySelector('div[title]');
    return d ? (d.getAttribute('title') || '').trim() : (td.innerText || '').trim();
  };
  const datos = filas.map((tr) => {
    const tds = tr.querySelectorAll('td');
    const celdaVar = tds[2];
    let variacion = '';
    if (celdaVar) {
      const p = celdaVar.querySelector('p[title]');
      variacion = p ? (p.getAttribute('title') || '') : (celdaVar.innerText || '');
      variacion = variacion.replace(/%+/g, '%').trim();
    }
    return {
      nemotecnico: titulo(tds[0]),
      precio: titulo(tds[1]),
      variacion: variacion,
      volumen: titulo(tds[3]),
      emisor: titulo(tds[10]),
    };
  }).filter((f) => f.nemotecnico);

  const input = document.querySelector('input[name="date"]');
  return { datos, fecha: input ? input.value : '' };
}
"""


def festivos_colombia(anio: int) -> set[date]:
    """Festivos colombianos del año, aplicando la Ley Emiliani (traslado al lunes)."""

    def pascua(y: int) -> date:
        a, b, c = y % 19, y // 100, y % 100
        d, e = b // 4, b % 4
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i, k = c // 4, c % 4
        l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        mes = (h + l - 7 * m + 114) // 31
        dia = ((h + l - 7 * m + 114) % 31) + 1
        return date(y, mes, dia)

    def lunes(d: date) -> date:
        return d + timedelta(days=(7 - d.weekday()) % 7)

    p = pascua(anio)
    fijos = [date(anio, 1, 1), date(anio, 5, 1), date(anio, 7, 20),
             date(anio, 8, 7), date(anio, 12, 8), date(anio, 12, 25)]
    trasladables = [date(anio, 1, 6), date(anio, 3, 19), date(anio, 6, 29),
                    date(anio, 8, 15), date(anio, 10, 12), date(anio, 11, 1),
                    date(anio, 11, 11)]
    pascuales_fijos = [p - timedelta(days=3), p - timedelta(days=2)]  # jueves y viernes santo
    pascuales_lunes = [p + timedelta(days=43), p + timedelta(days=64), p + timedelta(days=71)]

    dias = set(fijos) | set(pascuales_fijos)
    dias |= {lunes(d) for d in trasladables}
    dias |= set(pascuales_lunes)
    return dias


def ultimo_dia_habil(desde: date) -> date:
    d = desde
    while d.weekday() >= 5 or d in festivos_colombia(d.year):
        d -= timedelta(days=1)
    return d


def a_float(texto: str) -> float | None:
    limpio = texto.replace("%", "").replace(",", "").strip()
    try:
        return float(limpio)
    except ValueError:
        return None


def raspar() -> dict:
    with sync_playwright() as p:
        navegador = p.chromium.launch(args=["--no-sandbox"])
        pagina = navegador.new_page(
            viewport={"width": 1600, "height": 1000},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
            locale="es-CO",
        )
        pagina.goto(URL, wait_until="domcontentloaded", timeout=90_000)
        pagina.wait_for_selector("table tbody tr", timeout=90_000)
        pagina.wait_for_timeout(3000)  # deja que termine de hidratar la tabla
        crudo = pagina.evaluate(EXTRACT_JS)
        navegador.close()

    filas = crudo["datos"][:TOP_N]
    if not filas:
        raise RuntimeError("La tabla de la BVC no devolvió filas")

    ahora = datetime.now(BOGOTA)
    fecha_pagina = (crudo.get("fecha") or "").strip()
    if fecha_pagina:
        fecha_datos = fecha_pagina
        origen_fecha = "selector de la BVC"
    else:
        fecha_datos = ultimo_dia_habil(ahora.date()).isoformat()
        origen_fecha = "calculada (último día hábil)"

    especies = []
    for f in filas:
        valor = a_float(f["variacion"])
        especies.append({
            "nemotecnico": f["nemotecnico"],
            "emisor": f["emisor"],
            "precio": f["precio"],
            "volumen": f["volumen"],
            "variacion": f["variacion"] if f["variacion"] not in ("", "-") else "—",
            "variacion_valor": valor,
        })

    return {
        "fuente": URL,
        "fecha_datos": fecha_datos,
        "origen_fecha": origen_fecha,
        "actualizado": ahora.strftime("%Y-%m-%d %H:%M"),
        "zona_horaria": "America/Bogota",
        "especies": especies,
    }


def main() -> int:
    try:
        datos = raspar()
    except Exception as error:  # noqa: BLE001
        print(f"Error al raspar la BVC: {error}", file=sys.stderr)
        return 1

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(datos['especies'])} especies guardadas en {OUTPUT} "
          f"(datos del {datos['fecha_datos']}, actualizado {datos['actualizado']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
