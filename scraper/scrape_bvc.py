#!/usr/bin/env python3
"""
Extrae Nemotécnico y Variación porcentual del mercado local de renta variable
de la Bolsa de Valores de Colombia y guarda docs/data/bvc.json

Estrategia:
  1. Intercepta las respuestas JSON de la página. Si alguna trae la tabla,
     se usa esa (es más estable que el DOM).
  2. Si no, lee la tabla renderizada, con reintentos y recarga.
  3. Si todo falla, deja evidencia en debug/ (captura, HTML y URLs de red)
     para poder diagnosticar qué ve el runner.

La tabla de la BVC viene ordenada por Volúmenes (descendente), así que las
primeras 10 filas son las 10 especies más negociadas del día.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import Response, TimeoutError as PWTimeout, sync_playwright

URL = "https://www.bvc.com.co/mercado-local-en-linea?tab=renta-variable_mercado-local"
BOGOTA = ZoneInfo("America/Bogota")
TOP_N = 10
RAIZ = Path(__file__).resolve().parents[1]
OUTPUT = RAIZ / "docs" / "data" / "bvc.json"
DEBUG = RAIZ / "debug"
INTENTOS = 3

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


# --------------------------------------------------------------------------- #
# Calendario
# --------------------------------------------------------------------------- #
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
    pascuales_fijos = [p - timedelta(days=3), p - timedelta(days=2)]
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


# --------------------------------------------------------------------------- #
# Scraping
# --------------------------------------------------------------------------- #
def aceptar_cookies(pagina) -> None:
    """La BVC muestra un banner que puede tapar el contenido."""
    for etiqueta in ("Aceptar", "Aceptar todas", "Acepto", "Entendido", "Continuar"):
        try:
            boton = pagina.get_by_role("button", name=re.compile(etiqueta, re.I))
            if boton.count() and boton.first.is_visible():
                boton.first.click(timeout=3000)
                pagina.wait_for_timeout(800)
                return
        except Exception:  # noqa: BLE001
            continue


def activar_pestana(pagina) -> None:
    """Fuerza la pestaña de renta variable por si el parámetro de URL no basta."""
    for etiqueta in ("Renta variable", "Mercado local"):
        try:
            tab = pagina.get_by_text(re.compile(etiqueta, re.I)).first
            if tab.count() and tab.is_visible():
                tab.click(timeout=3000)
                pagina.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001
            continue


def guardar_debug(pagina, red: list[dict]) -> None:
    DEBUG.mkdir(parents=True, exist_ok=True)
    try:
        pagina.screenshot(path=str(DEBUG / "pantalla.png"), full_page=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        (DEBUG / "pagina.html").write_text(pagina.content(), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    (DEBUG / "red.json").write_text(json.dumps(red, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Evidencia guardada en {DEBUG}", file=sys.stderr)


def raspar() -> dict:
    red: list[dict] = []
    capturas: list[dict] = []

    def registrar(resp: Response) -> None:
        tipo = (resp.headers or {}).get("content-type", "")
        if "json" not in tipo.lower():
            return
        red.append({"url": resp.url, "status": resp.status})
        try:
            cuerpo = resp.json()
        except Exception:  # noqa: BLE001
            return
        crudo = json.dumps(cuerpo, ensure_ascii=False)
        if "nemo" in crudo.lower() or "variacion" in crudo.lower():
            capturas.append({"url": resp.url, "datos": cuerpo})

    with sync_playwright() as p:
        navegador = p.chromium.launch(args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ])
        contexto = navegador.new_context(
            viewport={"width": 1600, "height": 1000},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            locale="es-CO",
            timezone_id="America/Bogota",
            extra_http_headers={
                "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        pagina = contexto.new_page()
        pagina.on("response", registrar)

        filas: list[dict] = []
        fecha_pagina = ""
        ultimo_error = ""

        for intento in range(1, INTENTOS + 1):
            try:
                pagina.goto(URL, wait_until="domcontentloaded", timeout=120_000)
                aceptar_cookies(pagina)
                activar_pestana(pagina)
                pagina.wait_for_selector("table tbody tr", timeout=60_000, state="attached")
                pagina.wait_for_timeout(4000)  # deja terminar la hidratación
                crudo = pagina.evaluate(EXTRACT_JS)
                filas = crudo["datos"]
                fecha_pagina = (crudo.get("fecha") or "").strip()
                if filas:
                    break
                ultimo_error = "la tabla existe pero llegó vacía"
            except PWTimeout as error:
                ultimo_error = f"timeout: {error}"
            print(f"Intento {intento}/{INTENTOS} sin datos ({ultimo_error})", file=sys.stderr)
            pagina.wait_for_timeout(5000)

        if not filas:
            guardar_debug(pagina, red)
            if capturas:
                DEBUG.mkdir(parents=True, exist_ok=True)
                (DEBUG / "capturas.json").write_text(
                    json.dumps(capturas, ensure_ascii=False, indent=2)[:2_000_000],
                    encoding="utf-8")
                print("Hay respuestas JSON candidatas en debug/capturas.json", file=sys.stderr)
            navegador.close()
            raise RuntimeError(f"La tabla de la BVC no devolvió filas ({ultimo_error})")

        navegador.close()

    ahora = datetime.now(BOGOTA)
    if fecha_pagina:
        fecha_datos, origen_fecha = fecha_pagina, "selector de la BVC"
    else:
        fecha_datos = ultimo_dia_habil(ahora.date()).isoformat()
        origen_fecha = "calculada (último día hábil)"

    especies = []
    for f in filas[:TOP_N]:
        especies.append({
            "nemotecnico": f["nemotecnico"],
            "emisor": f["emisor"],
            "precio": f["precio"],
            "volumen": f["volumen"],
            "variacion": f["variacion"] if f["variacion"] not in ("", "-") else "—",
            "variacion_valor": a_float(f["variacion"]),
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
