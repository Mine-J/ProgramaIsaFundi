import json
import os
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import urllib.parse
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import sys
from pathlib import Path
import unicodedata
from motor.motor_asyncio import AsyncIOMotorClient
import asyncio
from html import unescape as html_unescape

# =========================
# Configuración
# =========================
URL_LOGIN = "https://deportesweb.madrid.es/DeportesWeb/Login"
URL_HOME = "https://deportesweb.madrid.es/DeportesWeb/Home"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.5",
    "X-Requested-With": "XMLHttpRequest",
    "X-MicrosoftAjax": "Delta=true",
    "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
    "Origin": "https://deportesweb.madrid.es",
    "Cache-Control": "no-cache",
    "Dnt": "1",
    "Sec-Gpc": "1",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

CLASES = [
    {"dia": "lunes", "hora": "15:45", "nombre": "Fitness"},
    {"dia": "lunes", "hora": "17:00", "nombre": "Entrenamiento en suspensión"},
    {"dia": "martes", "hora": "15:45", "nombre": "Fuerza en sala multitrabajo"},
    {"dia": "miércoles", "hora": "15:45", "nombre": "Fuerza GAP"},
    {"dia": "miércoles", "hora": "17:00", "nombre": "Entrenamiento en suspensión"},
    {"dia": "jueves", "hora": "15:45", "nombre": "Fuerza en sala multitrabajo"},
    {"dia": "viernes", "hora": "15:45", "nombre": "Pilates"},
    {"dia": "viernes", "hora": "17:00", "nombre": "Entrenamiento por intervalos"},
    {"dia": "viernes", "hora": "18:00", "nombre": "Entrenamiento en suspensión"}
]

DIAS_SEMANA = {
    "lunes": 0, "martes": 1, "miércoles": 2, "jueves": 3,
    "viernes": 4, "sábado": 5, "domingo": 6
}

HORAS_ANTES_APERTURA = 49
SEGUNDOS_PREPARACION = 60
INTENTOS_BUSQUEDA = 6
INTERVALO_BUSQUEDA = 2
HTTP_TIMEOUT = (10, 30)

# La web (deportesweb.madrid.es) trabaja SIEMPRE en hora de España.
# Todo el calendario se calcula en esa zona, así el script funciona igual
# esté el ordenador en Madrid, en Cork o donde sea.
try:
    TZ_WEB = ZoneInfo("Europe/Madrid")
except ZoneInfoNotFoundError:
    raise SystemExit(
        "❌ Falta la base de datos de zonas horarias. En Windows ejecuta:\n"
        "   pip install tzdata"
    )


def ahora_web() -> datetime:
    """Hora actual en España (con zona horaria)."""
    return datetime.now(TZ_WEB)


def segundos_hasta(momento: datetime) -> float:
    """Segundos reales hasta un momento (independiente de la zona del PC)."""
    return (momento.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()


def hora_local_str(momento: datetime) -> str:
    """Formatea un momento en la hora del ordenador (p. ej. Cork)."""
    return momento.astimezone().strftime("%d/%m/%Y %H:%M")

# =========================
# Gestión de BD
# =========================

class DatabaseManager:
    def __init__(self, mongo_url: str):
        self.client = AsyncIOMotorClient(mongo_url)
        self.db = self.client["reservas_clases"]
        self.coleccion = self.db["clases_reservadas"]

    async def verificar_conexion(self):
        await self.client.admin.command("ping")
        print("✅ Conectado a MongoDB")

    async def cargar_reservadas_recientes(self, dias_atras: int = 7):
        """Carga las clases ya reservadas para filtrarlas del plan"""
        await self.coleccion.delete_many({"fecha": {"$lt": (ahora_web() - timedelta(days=1)).strftime("%Y-%m-%d")}})
        fecha_inicio = (ahora_web() - timedelta(days=dias_atras)).strftime("%Y-%m-%d")
        cursor = self.coleccion.find({"fecha": {"$gte": fecha_inicio}})
        reservadas = await cursor.to_list(length=None)

        if reservadas:
            print(f"\n📚 Reservas en BD (últimos {dias_atras} días): {len(reservadas)}")
            for r in reservadas:
                print(f"   - {r['nombre']} | {r['fecha']} {r['hora']}")

        return reservadas

    async def guardar_reserva(self, clase: dict, fecha_clase: datetime) -> bool:
        """Guarda una reserva nueva o reconocida como existente, sin duplicarla."""
        documento = {
            "nombre": clase["nombre"],
            "hora": clase["hora"],
            "dia": clase["dia"],
            "fecha": fecha_clase.strftime("%Y-%m-%d"),
            "timestamp": datetime.now(timezone.utc)
        }

        existe = await self.coleccion.find_one({
            "nombre": documento["nombre"],
            "hora": documento["hora"],
            "fecha": documento["fecha"]
        })

        if not existe:
            await self.coleccion.insert_one(documento)
            print(f"💾 Guardada en BD: {documento['nombre']} - {documento['fecha']} {documento['hora']}")
            return True
        else:
            print(f"ℹ️ Ya existe en BD: {documento['nombre']} - {documento['fecha']} {documento['hora']}")
            return False

    def cerrar(self):
        self.client.close()
        print("👋 Conexión a MongoDB cerrada")

# =========================
# Cálculo de fechas
# =========================

def calcular_proxima_fecha_clase(dia_semana: str, hora: str) -> datetime:
    ahora = ahora_web()
    dia_num = DIAS_SEMANA[dia_semana.lower()]
    hora_int, minuto_int = map(int, hora.split(":"))

    dias_hasta = (dia_num - ahora.weekday() + 7) % 7

    if dias_hasta == 0:
        fecha_clase = ahora.replace(hour=hora_int, minute=minuto_int, second=0, microsecond=0)
        if ahora >= fecha_clase:
            dias_hasta = 7

    if dias_hasta == 0:
        fecha_clase = ahora.replace(hour=hora_int, minute=minuto_int, second=0, microsecond=0)
    else:
        fecha_clase = ahora + timedelta(days=dias_hasta)
        fecha_clase = fecha_clase.replace(hour=hora_int, minute=minuto_int, second=0, microsecond=0)

    return fecha_clase

def calcular_hora_apertura(fecha_clase: datetime) -> datetime:
    # La regla son 49 horas reales, también cuando cambia el horario de verano.
    if fecha_clase.tzinfo is None:
        fecha_clase = fecha_clase.replace(tzinfo=TZ_WEB)
    return (fecha_clase.astimezone(timezone.utc) - timedelta(hours=HORAS_ANTES_APERTURA)).astimezone(TZ_WEB)

def calcular_fecha_para_post(fecha_clase: datetime) -> str:
    return fecha_clase.astimezone(TZ_WEB).strftime("%Y-%m-%d")

async def preparar_plan_de_reservas(db_manager=None):
    ahora = ahora_web()
    plan = []

    # Solo considerar clases en los próximos 2 días completos (hasta el final del día +2)
    limite_fecha = (ahora + timedelta(days=2)).replace(hour=23, minute=59, second=59)

    reservadas = []
    if db_manager:
        reservadas = await db_manager.cargar_reservadas_recientes()

    for clase in CLASES:
        fecha_clase = calcular_proxima_fecha_clase(clase["dia"], clase["hora"])

        # Filtrar: solo clases dentro de los próximos 2 días completos
        if fecha_clase > limite_fecha:
            print(f"⏭️ Saltando {clase['nombre']} {fecha_clase.strftime('%d/%m')} {clase['hora']} (más de 2 días)")
            continue

        hora_apertura = calcular_hora_apertura(fecha_clase)
        fecha_para_post = calcular_fecha_para_post(fecha_clase)
        tiempo_hasta_apertura = segundos_hasta(hora_apertura)

        ya_reservada = any(
            r["nombre"] == clase["nombre"] and
            r["hora"] == clase["hora"] and
            r["fecha"] == fecha_clase.strftime("%Y-%m-%d")
            for r in reservadas
        )

        if ya_reservada:
            print(f"⏭️ Saltando {clase['nombre']} {fecha_clase.strftime('%d/%m')} {clase['hora']} (ya en BD)")
            continue

        plan.append({
            "clase": clase,
            "fecha_clase": fecha_clase,
            "hora_apertura": hora_apertura,
            "fecha_para_post": fecha_para_post,
            "tiempo_hasta_apertura": tiempo_hasta_apertura,
            "ya_abierta": tiempo_hasta_apertura <= 0
        })

    plan.sort(key=lambda x: x["hora_apertura"])
    return plan

def mostrar_plan_de_reservas(plan):
    print("\n" + "="*80)
    print("📅 PLAN DE RESERVAS")
    print("="*80)

    ahora = ahora_web()
    abiertas = sum(1 for p in plan if p["ya_abierta"])
    cerradas = len(plan) - abiertas

    print(f"\n📊 Resumen: {abiertas} abiertas 🟢 | {cerradas} cerradas 🔴\n")

    for i, item in enumerate(plan, 1):
        clase = item["clase"]
        fecha_clase = item["fecha_clase"]
        hora_apertura = item["hora_apertura"]
        ya_abierta = item["ya_abierta"]

        if ya_abierta:
            tiempo_pasado = abs(item["tiempo_hasta_apertura"])
            horas = int(tiempo_pasado // 3600)
            minutos = int((tiempo_pasado % 3600) // 60)
            estado = f"🟢 Abierta hace {horas}h {minutos}m"
        else:
            tiempo_restante = item["tiempo_hasta_apertura"]
            horas = int(tiempo_restante // 3600)
            minutos = int((tiempo_restante % 3600) // 60)
            estado = f"🔴 Abre en {horas}h {minutos}m"

        print(f"{i}. {estado}")
        print(f"   📍 {clase['nombre']}")
        print(f"   📅 {clase['dia'].capitalize()} {fecha_clase.strftime('%d/%m/%Y')} {clase['hora']}")
        print(f"   🔓 Abre: {hora_apertura.strftime('%d/%m/%Y %H:%M')} (hora España) → {hora_local_str(hora_apertura)} tu hora")
        print(f"   📤 POST: {item['fecha_para_post']}")
        print()

    print("="*80 + "\n")

# =========================
# Funciones ASP.NET
# =========================

def parse_initial_state(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    state = {}
    for key in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        tag = soup.find("input", {"name": key}) or soup.find("input", {"id": key})
        if tag is not None and tag.get("value") is not None:
            state[key] = tag["value"]
    if "__VIEWSTATE" not in state or "__VIEWSTATEGENERATOR" not in state:
        raise RuntimeError("La página no contiene el estado ASP.NET esperado; revisa la respuesta de la web.")
    return state

def extract_hidden_field(delta_text: str, field: str) -> str | None:
    token = f"|hiddenField|{field}|"
    if token not in delta_text:
        return None
    return delta_text.split(token, 1)[1].split("|", 1)[0]

def update_state_from_delta(state: dict, delta_text: str) -> None:
    for key in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        new_val = extract_hidden_field(delta_text, key)
        if new_val:
            state[key] = new_val

def is_login_success(response_text: str, session: requests.Session) -> bool:
    if "pageRedirect" in response_text:
        return True
    if "Token" in session.cookies.get_dict():
        return True
    return False

# =========================
# Navegación
# =========================

def select_facility(session: requests.Session, facility_code: str, facility_name: str, state: dict):
    r = session.get(URL_HOME, headers={**HEADERS, "Referer": URL_HOME}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    state["__VIEWSTATE"] = soup.find("input", {"id": "__VIEWSTATE"})["value"]
    state["__VIEWSTATEGENERATOR"] = soup.find("input", {"id": "__VIEWSTATEGENERATOR"})["value"]
    ev_tag = soup.find("input", {"id": "__EVENTVALIDATION"})
    if ev_tag:
        state["__EVENTVALIDATION"] = ev_tag["value"]

    script_manager = soup.find("input", {"id": "ctl00_ScriptManager1"})
    script_manager_value = (
        f"{script_manager['id']}|{script_manager['id'].replace('_', '$')}|ContentPlaceHolder1_UpdatePanel"
        if script_manager else "ctl00$ContentFixedSection$uSecciones$uAlert$uplAlert|ContentFixedSection_uSecciones_uAlert_uplAlert"
    )

    post_data = {
        "ctl00$ScriptManager1": script_manager_value,
        "__EVENTTARGET": "ContentFixedSection_uSecciones_uAlert_uplAlert",
        "__EVENTARGUMENT": json.dumps({
            "action": "SelectFacility",
            "args": {
                "facility_code": facility_code,
                "facility_name": facility_name,
                "submenu_code": None
            }
        }),
        "__ASYNCPOST": "true",
        **state
    }
    if "__EVENTVALIDATION" in state:
        post_data["__EVENTVALIDATION"] = state["__EVENTVALIDATION"]

    r = session.post(URL_HOME, data=post_data, headers={**HEADERS, "Referer": URL_HOME}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.text

def select_centro_menu_post(session: requests.Session, token: str, menu_code: str, menu_title: str, state: dict):
    url_centro = f"https://deportesweb.madrid.es/DeportesWeb/Centro?token={token}"
    r = session.get(url_centro, headers={"User-Agent": HEADERS["User-Agent"], "Referer": URL_HOME}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    state["__VIEWSTATE"] = soup.find("input", {"id": "__VIEWSTATE"})["value"]
    state["__VIEWSTATEGENERATOR"] = soup.find("input", {"id": "__VIEWSTATEGENERATOR"})["value"]
    ev_tag = soup.find("input", {"id": "__EVENTVALIDATION"})
    if ev_tag:
        state["__EVENTVALIDATION"] = ev_tag["value"]

    script_manager_value = "ctl00$ContentFixedSection$uCentro$uSecciones$uAlert$uplAlert|ContentFixedSection_uCentro_uSecciones_uAlert_uplAlert"
    post_data = {
        "ctl00$ScriptManager1": script_manager_value,
        "__EVENTTARGET": "ContentFixedSection_uCentro_uSecciones_uAlert_uplAlert",
        "__EVENTARGUMENT": json.dumps({
            "action": "SelectMenu",
            "args": {
                "menu_code": menu_code,
                "menu_title": menu_title,
                "menu_type": 9,
                "submenu_code": None
            }
        }),
        "__ASYNCPOST": "true",
        **state
    }

    r = session.post(url_centro, data=post_data, headers={**HEADERS, "Referer": url_centro}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.text

def get_alta_eventos(session: requests.Session, token: str, referer: str):
    url_alta_eventos = f"https://deportesweb.madrid.es/DeportesWeb/Modulos/VentaServicios/Eventos/AltaEventos?token={token}"
    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": referer,
        "Dnt": "1",
        "Upgrade-Insecure-Requests": "1",
    }

    r = session.get(url_alta_eventos, headers=headers, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    validar_sesion(r)
    return r.text

def extraer_cod_sesion(html_response: str, nombre_clase: str, hora_clase: str, fecha_esperada: str) -> dict | None:
    sesiones = leer_sesiones(html_response)
    coincidencias = [s for s in sesiones
                    if normalizar_nombre(s["nom_evento"]) == normalizar_nombre(nombre_clase)
                    and s["hora_desde"] == hora_clase and s["fecha"] == fecha_esperada]
    if len(coincidencias) > 1:
        raise RuntimeError("Hay varias sesiones con ese nombre, fecha y hora; no se puede elegir una de forma inequívoca.")
    if not coincidencias:
        print(f"   🔎 Sesiones leídas: {len(sesiones)}. Sin coincidencia: {nombre_clase} | {fecha_esperada} {hora_clase}")
        return None
    sesion = coincidencias[0]
    plazas = sesion["plazas_disponibles"]
    if plazas is None:
        print(f"   ✅ Clase encontrada: {nombre_clase}. La respuesta no permite leer el número de plazas.")
    else:
        print(f"   ✅ Clase encontrada: {nombre_clase} | plazas: {plazas}/{sesion['plazas_totales']}")
    return sesion


def load_events_for_date(session: requests.Session, token: str, fecha: str, state: dict):
    """Carga los eventos de una fecha específica"""
    url_alta_eventos = f"https://deportesweb.madrid.es/DeportesWeb/Modulos/VentaServicios/Eventos/AltaEventos?token={token}"

    event_argument = json.dumps({
        "controlID": "ContentFixedSection_uAltaEventos_uAltaEventosFechas",
        "action": "Load",
        "args": {
            "availability": False,
            "date": fecha
        }
    })

    post_data = {
        "ctl00$ScriptManager1": "ctl00$uAlert$uplAlert|uAlert_uplAlert",
        "__EVENTTARGET": "uAlert_uplAlert",
        "__EVENTARGUMENT": event_argument,
        "__VIEWSTATE": state["__VIEWSTATE"],
        "__VIEWSTATEGENERATOR": state["__VIEWSTATEGENERATOR"],
        "ContentFixedSection_uAltaEventos_uAltaEventosFechas_availability_filter": "on",
        "__ASYNCPOST": "true",
    }

    if "__EVENTVALIDATION" in state:
        post_data["__EVENTVALIDATION"] = state["__EVENTVALIDATION"]

    encoded_data = urllib.parse.urlencode(post_data)
    content_length = len(encoded_data)

    print(f"\n{'='*60}")
    print(f"📅 Cargando eventos para: {fecha}")
    print(f"📊 Content-Length: {content_length} bytes")

    headers = {
        **HEADERS,
        "Referer": url_alta_eventos,
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
    }

    r = session.post(url_alta_eventos, data=post_data, headers=headers, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    validar_sesion(r)

    update_state_from_delta(state, r.text)

    print(f"✅ Respuesta recibida ({len(r.text)} caracteres)")
    print(f"HTTP: {r.status_code}")
    for campo in ("NOM_EVENTO", "COD_SESION", "HORA_DESDE"):
        print(f"🔎 {campo}: {r.text.count(campo)} apariciones")
    mostrar_sesiones(leer_sesiones(r.text))
    print(f"{'='*60}\n")

    return r.text


def seleccionar_clase(session: requests.Session, token: str, sesion_data: dict, person_code: str, state: dict):
    """
    Hace el POST para seleccionar/reservar una clase específica.

    Args:
        session: Sesión de requests
        token: Token de AltaEventos
        sesion_data: Datos de la sesión obtenidos de extraer_cod_sesion
        person_code: Código de persona del usuario
        state: Estado de ASP.NET (viewstate, etc.)

    Returns:
        Texto de la respuesta del servidor
    """
    if not person_code:
        raise ValueError("No hay PERSON_CODE para seleccionar la clase.")

    url_alta_eventos = f"https://deportesweb.madrid.es/DeportesWeb/Modulos/VentaServicios/Eventos/AltaEventos?token={token}"

    event_argument = json.dumps({
        "controlID": "ContentFixedSection_uAltaEventos_uAltaEventosFechas",
        "action": "Seleccionar",
        "args": {
            "room_code": sesion_data["cod_sala"],
            "room_name": sesion_data["nom_sala"],
            "event_code": sesion_data["cod_evento"],
            "event_name": sesion_data["nom_evento"],
            "session_code": sesion_data["cod_sesion"],
            "date": sesion_data["fecha"],
            "from_hour": sesion_data["hora_desde"],
            "to_hour": sesion_data["hora_hasta"],
            "enable_reservations_limit": sesion_data["habilitar_limite_reservas"],
            "reservations_limit": sesion_data["limite_reservas"],
            "multiple_rooms": sesion_data["salas_multiples"],
            "personCode": person_code
        }
    })

    post_data = {
        "ctl00$ScriptManager1": "ctl00$uAlert$uplAlert|uAlert_uplAlert",
        "__EVENTTARGET": "uAlert_uplAlert",
        "__EVENTARGUMENT": event_argument,
        "__VIEWSTATE": state["__VIEWSTATE"],
        "__VIEWSTATEGENERATOR": state["__VIEWSTATEGENERATOR"],
        "ContentFixedSection_uAltaEventos_uAltaEventosFechas_availability_filter": "on",
        "__ASYNCPOST": "true",
    }

    if "__EVENTVALIDATION" in state:
        post_data["__EVENTVALIDATION"] = state["__EVENTVALIDATION"]

    print(f"\n{'='*60}")
    print(f"🎫 Seleccionando clase: {sesion_data['nom_evento']}")
    print(f"   📅 Fecha: {sesion_data['fecha']}")
    print(f"   ⏰ Hora: {sesion_data['hora_desde']} - {sesion_data['hora_hasta']}")
    print(f"   📍 Sala: {sesion_data['nom_sala']}")
    print(f"   🔑 COD_SESION: {sesion_data['cod_sesion']}")

    headers = {
        **HEADERS,
        "Referer": url_alta_eventos,
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
    }

    r = session.post(url_alta_eventos, data=post_data, headers=headers, timeout=HTTP_TIMEOUT)
    r.raise_for_status()

    update_state_from_delta(state, r.text)

    print(f"✅ Respuesta recibida ({len(r.text)} bytes)")
    print(f"{'='*60}\n")

    return r.text


def confirmar_carrito(session: requests.Session, referer: str, state: dict):
    """
    Hace el GET a CarritoConfirmar para cargar la página de confirmación.

    Args:
        session: Sesión de requests (ya tiene las cookies necesarias)
        referer: URL del referer (AltaEventos)
        state: Diccionario de estado ASP.NET (se actualizará con los nuevos valores)

    Returns:
        Texto HTML de la respuesta
    """
    url_carrito = "https://deportesweb.madrid.es/DeportesWeb/Modulos/VentaServicios/CarritoConfirmar"

    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": referer,
        "Dnt": "1",
        "Sec-Gpc": "1",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
    }

    print(f"\n{'='*60}")
    print(f"🛒 Accediendo a CarritoConfirmar...")

    r = session.get(url_carrito, headers=headers, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    validar_sesion(r)

    # Parsear el HTML para obtener el nuevo state
    soup = BeautifulSoup(r.text, "html.parser")
    state["__VIEWSTATE"] = soup.find("input", {"id": "__VIEWSTATE"})["value"]
    state["__VIEWSTATEGENERATOR"] = soup.find("input", {"id": "__VIEWSTATEGENERATOR"})["value"]
    ev_tag = soup.find("input", {"id": "__EVENTVALIDATION"})
    if ev_tag:
        state["__EVENTVALIDATION"] = ev_tag["value"]

    print(f"✅ Respuesta recibida ({len(r.text)} bytes)")
    print(f"{'='*60}\n")

    return r.text


def finalizar_reserva(session: requests.Session, state: dict, nombre: str, apellidos: str, correo: str):
    """
    Hace el POST final para confirmar la reserva en el carrito.

    Args:
        session: Sesión de requests
        state: Estado ASP.NET (viewstate, etc.)
        nombre: Nombre del usuario
        apellidos: Apellidos del usuario
        correo: Correo electrónico del usuario

    Returns:
        Texto de la respuesta del servidor
    """
    url_carrito = "https://deportesweb.madrid.es/DeportesWeb/Modulos/VentaServicios/CarritoConfirmar"

    event_argument = json.dumps({
        "action": "ConfirmCart",
        "args": {}
    })

    post_data = {
        "ctl00$ScriptManager1": "ctl00$ContentFixedSection$uCarritoConfirmar$uAlert$uplAlert|ContentFixedSection_uCarritoConfirmar_uAlert_uplAlert",
        "__EVENTTARGET": "ContentFixedSection_uCarritoConfirmar_uAlert_uplAlert",
        "__EVENTARGUMENT": event_argument,
        "__VIEWSTATE": state["__VIEWSTATE"],
        "__VIEWSTATEGENERATOR": state["__VIEWSTATEGENERATOR"],
        "ctl00$ContentFixedSection$uCarritoConfirmar$txtNombre": nombre,
        "ctl00$ContentFixedSection$uCarritoConfirmar$txtApellidos": apellidos,
        "ctl00$ContentFixedSection$uCarritoConfirmar$txtCorreoElectronico": correo,
        "__ASYNCPOST": "true",
    }

    if "__EVENTVALIDATION" in state:
        post_data["__EVENTVALIDATION"] = state["__EVENTVALIDATION"]

    headers = {
        **HEADERS,
        "Referer": url_carrito,
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
    }

    print(f"\n{'='*60}")
    print(f"✅ Finalizando reserva...")


    r = session.post(url_carrito, data=post_data, headers=headers, timeout=HTTP_TIMEOUT)
    r.raise_for_status()

    update_state_from_delta(state, r.text)

    print(f"✅ Respuesta recibida ({len(r.text)} bytes)")
    print(f"{'='*60}\n")

    return r.text


# =========================
# MAIN
# =========================

def extraer_person_code(html: str, debug_file: str | None = None) -> str | None:
    """Usa campos explícitos de persona; un token cercano no identifica al usuario."""
    texto = html_unescape(html)
    candidatos = set()
    clave = r"(?:person[_-]?code|cod[_-]?persona)"
    for match in re.finditer(rf'''(?i)(?<![\w-])["']?{clave}["']?\s*[:=]\s*["']([A-Za-z0-9]{{16,}})["']''', texto):
        candidatos.add(match.group(1))
    soup = BeautifulSoup(texto, "html.parser")
    for tag in soup.find_all(True):
        for atributo, valor in tag.attrs.items():
            if (re.search(rf"(?i)(?:^|[-_$]){clave}$", atributo)
                    and isinstance(valor, str) and re.fullmatch(r"[A-Za-z0-9]{16,}", valor)):
                candidatos.add(valor)
        if tag.name == "input":
            for atributo in ("id", "name"):
                if re.search(rf"(?i)(?:^|[-_$])(?:hf|hdn)?{clave}$", tag.get(atributo, "")):
                    valor = tag.get("value", "").strip()
                    if re.fullmatch(r"[A-Za-z0-9]{16,}", valor):
                        candidatos.add(valor)
    if len(candidatos) == 1:
        return next(iter(candidatos))
    if len(candidatos) > 1:
        print(f"   ⚠️ Hay {len(candidatos)} códigos de persona distintos; utiliza PERSON_CODE en .env.")
    if debug_file:
        guardar_diagnostico(html, Path(debug_file).name)
    return None

async def main():
    load_dotenv()
    config = {clave: (os.getenv(clave.upper()) or "").strip()
              for clave in ("email", "nombre", "apellidos", "person_code")}
    config["password"] = os.getenv("PASSWORD")
    if not all(config[clave] for clave in ("email", "password", "nombre", "apellidos")):
        raise ValueError("Faltan EMAIL, PASSWORD, NOMBRE o APELLIDOS en .env / GitHub Secrets.")
    mongo_url = os.getenv("MONGO_URL")
    db_manager = DatabaseManager(mongo_url) if mongo_url else None
    contexto = None
    resultados = []
    try:
        print("\n🎯 SISTEMA DE RESERVAS AUTOMÁTICO")
        if db_manager:
            await db_manager.verificar_conexion()
        plan = await preparar_plan_de_reservas(db_manager)
        if not plan:
            print("✅ No hay clases pendientes dentro del periodo del plan.")
            return 0
        mostrar_plan_de_reservas(plan)
        abiertas = [p for p in plan if p["ya_abierta"]]
        cerradas = [p for p in plan if not p["ya_abierta"]]
        for item in abiertas:
            print(f"\n🎯 Clase abierta: {item['clase']['nombre']} | {item['fecha_clase']:%d/%m/%Y %H:%M} Madrid")
            if contexto is None:
                contexto = iniciar_sesion(config["email"], config["password"], config["person_code"])
            else:
                recargar_eventos(contexto)
            resultados.append(await procesar_clase(contexto, item, config, db_manager))

        if cerradas:
            item = cerradas[0]
            apertura = item["hora_apertura"]
            print(f"\n🎯 Objetivo: {item['clase']['nombre']} | {item['fecha_clase']:%d/%m/%Y %H:%M} Madrid")
            print(f"   🔓 Abre: {apertura:%d/%m/%Y %H:%M} Madrid = {hora_local_str(apertura)} en tu ordenador")
            if contexto is not None:
                contexto["session"].close()
                contexto = None
            preparacion = apertura - timedelta(seconds=SEGUNDOS_PREPARACION)
            await esperar_hasta(preparacion)
            # Autenticar cerca de la apertura, después de la espera larga.
            contexto = iniciar_sesion(config["email"], config["password"], config["person_code"])
            await esperar_hasta(apertura)
            print("   🔔 Apertura alcanzada. Consultando la lista actual de clases...")
            resultados.append(await procesar_clase(contexto, item, config, db_manager))

        confirmadas = resultados.count("confirmada")
        existentes = resultados.count("ya_reservada")
        fallidas = resultados.count("sin_confirmar")
        print(f"\n📊 Resultado: {confirmadas} nuevas confirmadas | {existentes} ya reservadas | {fallidas} sin confirmar")
        return 1 if fallidas else 0
    finally:
        if contexto is not None:
            contexto["session"].close()
        if db_manager:
            db_manager.cerrar()


class SesionCaducada(RuntimeError):
    """La consulta ha vuelto al formulario de acceso."""


def validar_sesion(r) -> None:
    texto = html_unescape(urllib.parse.unquote(r.text))
    ruta = urllib.parse.urlsplit(r.url).path.rstrip("/").lower()
    if (ruta.endswith("/login")
            or re.search(r"pageRedirect\|\|[^|]*?/Login(?:[?|]|$)", texto, re.I)
            or 'id="ContentFixedSection_uLogin_txtContrasena"' in texto):
        raise SesionCaducada("La web ha devuelto la página de acceso; hay que renovar la sesión.")


def guardar_diagnostico(texto: str, nombre: str) -> None:
    destino = Path(__file__).resolve().parent / "debug_fundi" / nombre
    try:
        destino.parent.mkdir(exist_ok=True)
        destino.write_text(texto, encoding="utf-8")
        print(f"   📝 Respuesta guardada en: {destino}")
    except OSError as error:
        print(f"   ⚠️ No se pudo guardar el diagnóstico: {error}")


def normalizar_nombre(texto: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", html_unescape(texto)).split()).casefold()


def decodificar_string_js(texto: str) -> str:
    def convertir(match):
        escape = match.group(0)[1:]
        if escape.startswith(("u", "x")):
            return chr(int(escape[1:], 16))
        return {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}.get(escape, escape)
    return re.sub(r"\\(?:u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", convertir, texto)


def leer_sesiones(html_response: str) -> list[dict]:
    """Lee cada objeto de clase; las plazas se buscan dentro de su propio bloque."""
    texto = html_response
    patron = re.compile(
        r'''\.on\s*\(\s*["']click["']\s*,\s*(\{(?:[^{}"']|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')*\})''',
        re.S,
    )
    pares = re.compile(
        r'''["']?([A-Za-z_]\w*)["']?\s*:\s*(?:'((?:\\.|[^'\\])*)'|"((?:\\.|[^"\\])*)"|(true|false|\d+))''',
        re.I,
    )
    bloques = list(patron.finditer(texto))
    if not bloques:
        texto = html_unescape(html_response)
        bloques = list(patron.finditer(texto))
    sesiones = {}
    for indice, bloque in enumerate(bloques):
        datos = {}
        for par in pares.finditer(bloque.group(1)):
            valor = next(v for v in par.groups()[1:] if v is not None)
            datos[par.group(1)] = html_unescape(decodificar_string_js(valor))
        if not datos.get("NOM_EVENTO") or not datos.get("COD_SESION"):
            continue

        # No usar una ventana fija de 800 caracteres ni leer la clase siguiente.
        fin = bloques[indice + 1].start() if indice + 1 < len(bloques) else len(texto)
        contenido = texto[bloque.end():fin]
        cifras = re.findall(r'''\.append\s*\(\s*["'](/?\d+)["']\s*\)''', contenido)
        plazas = [(int(a), int(b[1:])) for a, b in zip(cifras, cifras[1:])
                  if not a.startswith("/") and b.startswith("/") and int(a) <= int(b[1:])]
        disponibles, total = plazas[0] if len(plazas) == 1 else (None, None)
        sesion = {
            "cod_sesion": datos.get("COD_SESION"),
            "cod_sala": datos.get("COD_SALA"), "nom_sala": datos.get("NOM_SALA"),
            "cod_evento": datos.get("COD_EVENTO"), "nom_evento": datos.get("NOM_EVENTO"),
            "fecha": datos.get("FECHA"), "hora_desde": datos.get("HORA_DESDE"),
            "hora_hasta": datos.get("HORA_HASTA"),
            "habilitar_limite_reservas": datos.get("HABILITAR_LIMITE_RESERVAS"),
            "limite_reservas": datos.get("LIMITE_RESERVAS"),
            "salas_multiples": datos.get("SALAS_MULTIPLES"),
            "plazas_disponibles": disponibles, "plazas_totales": total,
        }
        sesiones[(sesion["cod_sesion"], sesion["fecha"], sesion["hora_desde"])] = sesion
    return list(sesiones.values())


def mostrar_sesiones(sesiones: list[dict]) -> None:
    print(f"\n📋 Clases leídas: {len(sesiones)} sesiones")
    if not sesiones:
        print("   No se han podido extraer sesiones; la lista puede estar vacía o tener otro formato.")
    for sesion in sesiones:
        plazas = sesion["plazas_disponibles"]
        estado = ("plazas sin determinar" if plazas is None
                  else f"{plazas}/{sesion['plazas_totales']} plazas")
        print(f"   - {sesion['nom_evento']!r} | fecha {sesion['fecha']!r} "
              f"| hora {sesion['hora_desde']!r} | {estado} | sesión {sesion['cod_sesion']}")


def recargar_eventos(contexto: dict) -> None:
    html = get_alta_eventos(
        contexto["session"], contexto["alta_token"],
        f"https://deportesweb.madrid.es/DeportesWeb/Centro?token={contexto['token']}",
    )
    nuevo_state = parse_initial_state(html)
    contexto["state"].clear()
    contexto["state"].update(nuevo_state)
    if not contexto["person_code"]:
        contexto["person_code"] = extraer_person_code(html)


def iniciar_sesion(email: str, password: str, person_code: str | None) -> dict:
    session = requests.Session()
    try:
        print("\n🔐 Iniciando una sesión nueva...")
        r = session.get(URL_LOGIN, headers=HEADERS, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        state = parse_initial_state(r.text)
        menu = {
            "ctl00$ScriptManager1": "ctl00$ContentFixedSection$uSecciones$uAlert$uplAlert|ContentFixedSection_uSecciones_uAlert_uplAlert",
            "__EVENTTARGET": "ContentFixedSection_uSecciones_uAlert_uplAlert",
            "__EVENTARGUMENT": json.dumps({"action": "SelectMenu", "args": {
                "menu_code": "5143", "menu_title": "Correo y contraseña", "menu_type": 29,
                "authentication_provider_code": "4", "submenu_code": None}}),
            "__ASYNCPOST": "true", **state,
        }
        r = session.post(URL_LOGIN, data=menu, headers=HEADERS, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        update_state_from_delta(state, r.text)
        login = {
            "ctl00$ScriptManager1": "ctl00$ContentFixedSection$uLogin$uAlert$uplAlert|ContentFixedSection_uLogin_uAlert_uplAlert",
            "__EVENTTARGET": "ContentFixedSection_uLogin_uAlert_uplAlert",
            "__EVENTARGUMENT": json.dumps({"action": "Login", "args": {"authentication_provider_code": "4"}}),
            "ctl00$ContentFixedSection$uLogin$txtIdentificador": email,
            "ctl00$ContentFixedSection$uLogin$txtContrasena": password,
            "ctl00$ContentFixedSection$uLogin$chkNoCerrarSesion": "on",
            "__ASYNCPOST": "true", **state,
        }
        r = session.post(URL_LOGIN, data=login, headers=HEADERS, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        update_state_from_delta(state, r.text)
        if not is_login_success(r.text, session):
            guardar_diagnostico(r.text, "login.html")
            raise RuntimeError("Login fallido.")
        print("✅ LOGIN CORRECTO")
        respuesta = select_facility(session, "2", "La Fundi", state)
        match = re.search(r"pageRedirect\|\|/DeportesWeb/Centro\?token=([A-Z0-9]+)", urllib.parse.unquote(respuesta))
        if not match:
            guardar_diagnostico(respuesta, "centro.html")
            raise RuntimeError("No se pudo abrir La Fundi.")
        token = match.group(1)
        respuesta = select_centro_menu_post(session, token, "8580", "Oferta de actividades por día y centro", state)
        match = re.search(r"pageRedirect\|\|/DeportesWeb/Modulos/VentaServicios/Eventos/AltaEventos\?token=([A-Z0-9]+)", urllib.parse.unquote(respuesta))
        if not match:
            guardar_diagnostico(respuesta, "menu_eventos.html")
            raise RuntimeError("No se pudo abrir la página de actividades.")
        contexto = {"session": session, "state": state, "token": token,
                    "alta_token": match.group(1), "person_code": person_code}
        recargar_eventos(contexto)
        print("✅ Página de actividades y estado ASP.NET preparados")
        return contexto
    except Exception:
        session.close()
        raise


async def esperar_hasta(momento: datetime) -> None:
    restante = segundos_hasta(momento)
    if restante > 0:
        print(f"   ⏳ Esperando hasta {momento.astimezone(TZ_WEB):%d/%m/%Y %H:%M:%S} Madrid "
              f"= {hora_local_str(momento)} en tu ordenador ({int(restante)} s)")
    # Recalcular permite recoger ajustes del reloj y la reanudación del equipo.
    while (restante := segundos_hasta(momento)) > 0:
        await asyncio.sleep(min(restante, 60))


async def buscar_sesion_actualizada(contexto: dict, item: dict, email: str, password: str, person_config: str | None) -> dict | None:
    respuesta = ""
    motivo = "No apareció la sesión solicitada."
    renovada = False
    for intento in range(1, INTENTOS_BUSQUEDA + 1):
        print(f"\n🔎 Consulta actualizada {intento}/{INTENTOS_BUSQUEDA} a las {ahora_web():%H:%M:%S} Madrid")
        try:
            if intento > 1:
                recargar_eventos(contexto)
            respuesta = load_events_for_date(contexto["session"], contexto["alta_token"],
                                            item["fecha_para_post"], contexto["state"])
            if not contexto["person_code"]:
                contexto["person_code"] = extraer_person_code(respuesta)
            sesion = extraer_cod_sesion(respuesta, item["clase"]["nombre"],
                                       item["clase"]["hora"], item["fecha_para_post"])
            if sesion is not None and sesion["plazas_disponibles"] != 0:
                return sesion
            if sesion is not None:
                motivo = "La clase aparece, pero la web indica 0 plazas."
            elif leer_sesiones(respuesta):
                motivo = "La lista contiene clases, pero no coincide el nombre, la fecha y la hora solicitados."
            else:
                motivo = "No se pudieron leer sesiones en la respuesta; puede ser una lista vacía o un formato diferente."
        except SesionCaducada as error:
            motivo = str(error)
            if renovada:
                raise
            print("   🔄 La consulta ha vuelto al login. Renovando sesión...")
            contexto["session"].close()
            nuevo = iniciar_sesion(email, password, person_config)
            contexto.clear()
            contexto.update(nuevo)
            renovada = True
        except (requests.Timeout, requests.ConnectionError) as error:
            motivo = f"Falló la consulta de actividades: {type(error).__name__}."
        print(f"   ⚠️ {motivo}")
        if intento < INTENTOS_BUSQUEDA:
            await asyncio.sleep(INTERVALO_BUSQUEDA)

    print(f"   ❌ Búsqueda agotada. {motivo}")
    nombre = f"eventos_{item['fecha_para_post']}_{item['clase']['hora'].replace(':', '')}.html"
    guardar_diagnostico(respuesta, nombre)
    return None


def indica_reserva_existente(respuesta: str) -> bool:
    """El aviso de una reserva por persona se trata como reserva ya existente."""
    texto = html_unescape(urllib.parse.unquote(respuesta))
    soup = BeautifulSoup(texto, "html.parser")
    aviso = soup.find(id="uAlert_spnAlertDanger")
    contenido = aviso.get_text(" ", strip=True) if aviso else soup.get_text(" ", strip=True)
    contenido = " ".join(contenido.split())
    return re.search(
        r"\bLa sesión seleccionada no permite más de 1 reserva\(s\) por persona\b",
        contenido, re.I,
    ) is not None


async def procesar_clase(contexto: dict, item: dict, config: dict, db_manager) -> str:
    sesion = await buscar_sesion_actualizada(contexto, item, config["email"], config["password"], config["person_code"])
    if sesion is None:
        return "sin_confirmar"
    if not contexto["person_code"]:
        raise RuntimeError("No se encontró PERSON_CODE. Pon en .env el personCode capturado al seleccionar una clase con la misma cuenta que EMAIL.")
    requeridos = ("cod_sesion", "cod_sala", "nom_sala", "cod_evento", "nom_evento", "fecha", "hora_desde",
                  "hora_hasta", "habilitar_limite_reservas", "limite_reservas", "salas_multiples")
    faltan = [k for k in requeridos if sesion.get(k) in (None, "")]
    if faltan:
        raise RuntimeError("La clase aparece, pero faltan datos para seleccionarla: " + ", ".join(faltan))

    # Solo la consulta se reintenta. Una selección o confirmación con resultado
    # desconocido se comunica como tal, sin repetirla automáticamente.
    respuesta = seleccionar_clase(contexto["session"], contexto["alta_token"], sesion,
                                  contexto["person_code"], contexto["state"])
    if indica_reserva_existente(respuesta):
        print("   ✅ Ya reservada: la web indica que ya tienes una reserva para esta sesión.")
        if db_manager:
            await db_manager.guardar_reserva(item["clase"], item["fecha_clase"])
        return "ya_reservada"
    texto = html_unescape(urllib.parse.unquote(respuesta))
    if "pageRedirect" not in texto or "CarritoConfirmar" not in texto:
        print("   ❌ La web no ha enviado la selección al carrito.")
        guardar_diagnostico(respuesta, f"seleccion_{sesion['cod_sesion']}.html")
        return "sin_confirmar"
    print("   ✅ Clase añadida al carrito; falta confirmar.")
    referer = f"https://deportesweb.madrid.es/DeportesWeb/Modulos/VentaServicios/Eventos/AltaEventos?token={contexto['alta_token']}"
    confirmar_carrito(contexto["session"], referer, contexto["state"])
    respuesta = finalizar_reserva(contexto["session"], contexto["state"], config["nombre"], config["apellidos"], config["email"])
    texto = urllib.parse.unquote(respuesta)
    if "pageRedirect" not in texto or "CarritoResultado" not in texto:
        print("   ❌ No se recibió la confirmación esperada de la web.")
        guardar_diagnostico(respuesta, f"confirmacion_{sesion['cod_sesion']}.html")
        return "sin_confirmar"
    print("   🎉 ¡RESERVA CONFIRMADA!")
    if db_manager:
        await db_manager.guardar_reserva(item["clase"], item["fecha_clase"])
    return "confirmada"


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n⏹️ Interrumpido")
        sys.exit(130)
    except Exception as error:
        print(f"\n❌ Error: {error}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
