import asyncio
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from dotenv import load_dotenv
import os
import datetime
from motor.motor_asyncio import AsyncIOMotorClient

# --- Cargar variables del .env primero ---
load_dotenv()
yaLaTienes = False

# --- URL de MongoDB Atlas ---
MONGO_URL = os.getenv("MONGO_URL")
if not MONGO_URL:
    raise ValueError("No se encontró MONGO_URL en el .env")

# --- Conexión al cluster ---
client = AsyncIOMotorClient(MONGO_URL)
db = client["reservas_clases"]        # Base de datos
coleccion = db["clases_reservadas"]   # Colección

print("✅ Conectado a MongoDB Atlas correctamente")

# --- Cargar variables del .env ---
EMAIL = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")

# --- Definir las clases de interés ---
CLASES = [
    {"dia": "lunes", "hora": "16:30", "nombre": "Fitness"},
    {"dia": "lunes", "hora": "17:30", "nombre": "Entrenamiento en suspensión"},
    {"dia": "lunes", "hora": "18:30", "nombre": "Fuerza CORE"},
    {"dia": "martes", "hora": "15:45", "nombre": "Fuerza en sala multitrabajo"},
    {"dia": "martes", "hora": "17:30", "nombre": "Fitness"},
    {"dia": "miércoles", "hora": "16:30", "nombre": "Fitness"},
    {"dia": "miércoles", "hora": "17:30", "nombre": "Entrenamiento en suspensión"},
    {"dia": "miércoles", "hora": "18:30", "nombre": "Fuerza CORE"},
    {"dia": "jueves", "hora": "15:45", "nombre": "Fuerza en sala multitrabajo"},
    {"dia": "jueves", "hora": "17:30", "nombre": "Fitness"},
    {"dia": "viernes", "hora": "15:30", "nombre": "Pilates MesD"},
    {"dia": "viernes", "hora": "16:30", "nombre": "Funcional MesD"}
]

DIAS = {
    "lunes": 0,
    "martes": 1,
    "miércoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sábado": 5,
    "domingo": 6,
}

def proxima_fecha(dia_semana, hora):
    hoy = datetime.datetime.now()
    hora_int = int(hora.split(":")[0])
    minuto_int = int(hora.split(":")[1])
    objetivo = hoy.replace(hour=hora_int, minute=minuto_int, second=0, microsecond=0)
    dias_a_sumar = (DIAS[dia_semana] - hoy.weekday() + 7) % 7
    if dias_a_sumar == 0 and hoy < objetivo:
        return objetivo
    elif dias_a_sumar == 0:
        return objetivo + datetime.timedelta(days=7)
    else:
        return objetivo + datetime.timedelta(days=dias_a_sumar)

async def cargar_reservadas():
    fecha_inicio = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    cursor = coleccion.find({"fecha": {"$gte": fecha_inicio}})
    return await cursor.to_list(length=None)

async def guardar_reservada(clase, fecha_clase):
    clase_con_fecha = dict(clase)
    clase_con_fecha["fecha"] = fecha_clase.strftime("%Y-%m-%d")
    existe = await coleccion.find_one({
        "nombre": clase_con_fecha["nombre"],
        "hora": clase_con_fecha["hora"],
        "fecha": clase_con_fecha["fecha"]
    })
    if not existe:
        await coleccion.insert_one(clase_con_fecha)
        print("💾 Guardada en MongoDB:", clase_con_fecha)
    else:
        print("ℹ️ Ya existía en MongoDB:", clase_con_fecha)

async def seleccionar_dia(page, fecha):
    fecha_str = fecha.strftime("%d/%m/%Y")
    selector_dia = f"td.day[data-day='{fecha_str}']"
    try:
        await page.wait_for_selector(selector_dia, timeout=5000)
        await page.click(selector_dia)
        print(f"✅ Día seleccionado en el calendario: {fecha_str}")
    except Exception as e:
        print(f"❌ No se pudo seleccionar el día {fecha_str} en el calendario.", e)

async def volver_a_fundi_y_actividades(page):
    selector_fundi = "article.navigation-section-widget-collection-item h4[title='La Fundi']"
    try:
        await page.wait_for_selector(selector_fundi, timeout=5000)
        await page.click(selector_fundi)
        print("✅ Click en 'La Fundi'.")
    except Exception as e:
        print("❌ No se pudo hacer click en 'La Fundi'.", e)
    selector_actividades = "article.navigation-section-widget-collection-item h4[title='Oferta de actividades por día y centro']"
    try:
        await page.wait_for_selector(selector_actividades, timeout=5000)
        await page.click(selector_actividades)
        print("✅ Click en 'Oferta de actividades por día y centro'.")
    except Exception as e:
        print("❌ No se pudo hacer click en 'Oferta de actividades por día y centro'.", e)

async def reservar_clase(page, nombre, hora):
    """
    Intento simple de reserva:
    - Busca el panel con el nombre de la clase
    - Busca los elementos li.media que contengan la hora
    - Si plazas == "0" -> devuelve "PLAZAS_0"
    - Hace click en el elemento y luego intenta confirmar -> si OK devuelve "RESERVADA"
    - Si aparece mensaje que ya tienes una -> "YA_TIENE"
    - Si aparece mensaje que indica que todavía no está abierta -> "NO_ABIERTA"
    - Si no encuentra -> "NO_ENCONTRADA" o "ERROR"
    """
    try:
        panels = page.locator("div.panel-body")
        count_panels = await panels.count()

        for i in range(count_panels):
            panel = panels.nth(i)
            # Nombre del bloque (ej. sala / actividad)
            try:
                nombre_web = await panel.locator("h4.media-heading").first.inner_text()
            except Exception:
                nombre_web = ""
            if nombre_web.strip().lower() != nombre.strip().lower():
                continue

            print(f"🔍 Buscando clase '{nombre} {hora}' en panel {i}...")

            slots = panel.locator(f"li.media:has-text('{hora}')")
            if not await slots.count():
                print(f"⏭ No hay elementos con la hora '{hora}' en este panel.")
                continue

            # Recorremos los slots encontrados (puede haber varios)
            for j in range(await slots.count()):
                elemento = slots.nth(j)
                # Intentamos leer las plazas (primer span que tenga texto)
                try:
                    span = elemento.locator("span").first
                    plazas_texto = (await span.inner_text()).strip()
                except Exception:
                    plazas_texto = ""

                if plazas_texto == "0":
                    print("🚫 Plazas = 0 para este slot.")
                    return "PLAZAS_0"

                # Si hay plazas (o no podemos leer), intentamos click
                try:
                    await elemento.click()
                    # tras el click debemos intentar confirmar
                    boton_confirmar = "button#ContentFixedSection_uCarritoConfirmar_btnConfirmCart"
                    try:
                        # esperamos un poco a que aparezca el botón de confirmar
                        await page.wait_for_selector(boton_confirmar, timeout=3000)
                        await page.click(boton_confirmar)
                        # si ha funcionado, devolvemos reservado
                        print(f"✅ Intento de confirmar realizado para '{nombre} {hora}'")
                        # Esperar un poco para que aparezca confirmación o modal
                        await asyncio.sleep(0.5)
                        # comprobar si hay mensajes "ya tienes" o similar
                        content = await page.content()
                        low = content.lower()
                        if "solo puedes tener" in low or "ya tienes" in low or "sólo puedes tener" in low:
                            return "YA_TIENE"
                        return "RESERVADA"
                    except PlaywrightTimeoutError:
                        # No apareció el botón de confirmar: comprobar si el sistema muestra que no está abierta
                        content = await page.content()
                        low = content.lower()
                        if "se abre a las" in low or "se abrirá a las" in low or "no está abierta" in low:
                            return "NO_ABIERTA"
                        if "solo puedes tener" in low or "ya tienes" in low or "sólo puedes tener" in low:
                            return "YA_TIENE"
                        # Si no sabemos, intentar cerrar modal y seguir
                        print("⚠️ No apareció el botón confirmar tras click; revisa manualmente.")
                        return "ERROR"
                except Exception as e_click:
                    # Click fallido: inspeccionar HTML por mensajes claro
                    content = await page.content()
                    low = content.lower()
                    if "solo puedes tener" in low or "ya tienes" in low or "sólo puedes tener" in low:
                        return "YA_TIENE"
                    if "se abre a las" in low or "se abrirá a las" in low or "no está abierta" in low:
                        return "NO_ABIERTA"
                    print("⚠️ Error al hacer click en slot:", e_click)
                    return "ERROR"

        print(f"⏭ No se encontró la clase '{nombre} {hora}' en los paneles disponibles.")
        return "NO_ENCONTRADA"

    except Exception as e:
        print("❌ Error inesperado en reservar_clase:", e)
        return "ERROR"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context()
        page = await context.new_page()

        # --- Abrir página de login ---
        await page.goto("https://deportesweb.madrid.es/DeportesWeb/login")
        print("✅ Página de login abierta")

        # Intentar click inicial (opcional)
        try:
            await page.click("div.navigation-section-widget-collection-item-image-icon-square")
            print("✅ Click realizado en el div de la clase.")
        except Exception:
            print("ℹ️ No se pudo hacer click en el div inicial (no es crítico).")

        # --- Login automático ---
        await page.fill("input[name='ctl00$ContentFixedSection$uLogin$txtIdentificador']", EMAIL)
        await page.fill("input[name='ctl00$ContentFixedSection$uLogin$txtContrasena']", PASSWORD)
        await page.click("button#ContentFixedSection_uLogin_btnLogin")
        print("⌛ Login enviado, esperando a que cargue tu perfil...")

        try:
            await page.wait_for_selector("div#ctl00_divProfile", timeout=30000)
            print("✅ Login completado correctamente")
        except Exception:
            print("❌ No se pudo iniciar sesión")
            await browser.close()
            return

        # Ir a La Fundi y Oferta de actividades
        try:
            selector = "article.navigation-section-widget-collection-item h4[title='La Fundi']"
            await page.wait_for_selector(selector, timeout=5000)
            await page.click(selector)
            print("✅ Click correcto disparado en el artículo 'La Fundi'.")
        except Exception as e:
            print("❌ No se pudo hacer click en el artículo 'La Fundi'.", e)

        try:
            selector_actividades = "article.navigation-section-widget-collection-item h4[title='Oferta de actividades por día y centro']"
            await page.wait_for_selector(selector_actividades, timeout=5000)
            await page.click(selector_actividades)
            print("✅ Click correcto en 'Oferta de actividades por día y centro'.")
            print(f"🕒 Hora actual: {datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}", flush=True)
        except Exception as e:
            print("❌ No se pudo hacer click en 'Oferta de actividades por día y centro'.", e)

        # --- Cargar clases reservadas recientes ---
        reservadas = await cargar_reservadas()
        ahora = datetime.datetime.now()

        descartadas = set()  # (nombre, hora, fecha)

        while True:
            # --- Crear lista de candidatas con su hora de apertura ---
            candidatas = []
            for clase in CLASES:
                fecha_clase = proxima_fecha(clase["dia"], clase["hora"])
                hora_apertura = fecha_clase - datetime.timedelta(hours=49)
                fecha_str = fecha_clase.strftime("%Y-%m-%d")

                ya = any(
                    r["nombre"] == clase["nombre"] and
                    r["hora"] == clase["hora"] and
                    r["fecha"] == fecha_str
                    for r in reservadas
                )

                if ya or (clase["nombre"], clase["hora"], fecha_str) in descartadas:
                    continue
                if ahora < fecha_clase:
                    candidatas.append((hora_apertura, clase, fecha_clase))

            if not candidatas:
                print("⏹️ No hay más clases próximas para reservar. Cerrando programa.")
                await browser.close()
                return

            # Separar entre clases ya abiertas y no abiertas
            abiertas = [c for c in candidatas if c[0] <= ahora]
            no_abiertas = [c for c in candidatas if c[0] > ahora]

            if abiertas:
                abiertas.sort(key=lambda x: x[0])
                hora_apertura, clase_objetivo, fecha_clase = abiertas[0]
            else:
                no_abiertas.sort(key=lambda x: x[0])
                hora_apertura, clase_objetivo, fecha_clase = no_abiertas[0]

            print(f"🎯 Próxima clase a reservar: {clase_objetivo['nombre']} {clase_objetivo['hora']}")
            print(f"    Fecha de la clase: {fecha_clase.strftime('%d/%m/%Y %H:%M')}")
            print(f"    Se abre (49h antes): {hora_apertura.strftime('%d/%m/%Y %H:%M')}")

            # Seleccionar día en el calendario
            await seleccionar_dia(page, fecha_clase)
            await page.wait_for_selector(f"div.panel-body h4.media-heading:text('{clase_objetivo['nombre']}')", timeout=15000)

            # Esperar hasta la hora de apertura si toca
            ahora = datetime.datetime.now()
            if ahora < hora_apertura:
                espera = (hora_apertura - ahora).total_seconds()
                print(f"⏳ Esperando {int(espera)} segundos hasta la apertura ({hora_apertura.strftime('%d/%m %H:%M:%S')})...")
                await asyncio.sleep(espera)

            # Intentar reservar durante 1 minuto
            tiempo_total_intento = 60
            deadline = datetime.datetime.now() + datetime.timedelta(seconds=tiempo_total_intento)
            print(f"🚀 Intentando reservar {clase_objetivo['nombre']} {clase_objetivo['hora']} durante {tiempo_total_intento}s...")

            intento_exitoso = False

            while datetime.datetime.now() < deadline:
                estado = await reservar_clase(page, clase_objetivo["nombre"], clase_objetivo["hora"])

                if estado == "RESERVADA":
                    await guardar_reservada(clase_objetivo, fecha_clase)
                    print(f"🎉 Clase '{clase_objetivo['nombre']} {clase_objetivo['hora']}' reservada correctamente")
                    intento_exitoso = True
                    break

                elif estado in ["PLAZAS_0", "YA_TIENE"]:
                    print(f"⚠️ Clase {clase_objetivo['nombre']} descartada ({estado}). Pasando a la siguiente...")
                    descartadas.add((clase_objetivo["nombre"], clase_objetivo["hora"], fecha_clase.strftime("%Y-%m-%d")))
                    break

                elif estado == "NO_ABIERTA":
                    print("⏳ La clase todavía no está abierta. Reintentando...")

                elif estado == "NO_ENCONTRADA":
                    print("⚠️ Clase no encontrada todavía, refrescando el día...")
                    await seleccionar_dia(page, fecha_clase)
                    await page.wait_for_selector(f"div.panel-body h4.media-heading:text('{clase_objetivo['nombre']}')", timeout=10000)

                else:
                    print("⚠️ Estado inesperado:", estado, " — reintentando...")

                await asyncio.sleep(0.5)

            if intento_exitoso:
                # Reiniciamos la carga de reservadas y continuamos con la siguiente clase
                reservadas = await cargar_reservadas()
            else:
                ahora = datetime.datetime.now()  # actualizar hora y pasar a siguiente


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("⏹️ Programa interrumpido por el usuario.")
