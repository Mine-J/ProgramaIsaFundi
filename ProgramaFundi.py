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
    await page.wait_for_selector(selector_dia, state="attached", timeout=15000)

    dia = page.locator(selector_dia)

    # Intentar click hasta que funcione o pasen X segundos
    timeout = 10
    for _ in range(timeout*2):
        try:
            if await dia.is_visible():
                await dia.click()
                print(f"✅ Día seleccionado en el calendario: {fecha_str}")
                break
        except:
            pass
        await asyncio.sleep(0.5)



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
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context()
        page = await context.new_page()

        # --- Abrir página de login ---
        await page.goto("https://deportesweb.madrid.es/DeportesWeb/login")
        print("✅ Página de login abierta")

        try:
            await page.click("div.navigation-section-widget-collection-item-image-icon-square")
            print("✅ Click realizado en el div de la clase.")
        except Exception:
            print("ℹ️ No se pudo hacer click en el div inicial (no crítico).")

        # --- Login automático ---
        await page.fill("input[name='ctl00$ContentFixedSection$uLogin$txtIdentificador']", EMAIL)
        await page.fill("input[name='ctl00$ContentFixedSection$uLogin$txtContrasena']", PASSWORD)
        await page.click("button#ContentFixedSection_uLogin_btnLogin")
        print("⌛ Login enviado, esperando a que cargue tu perfil...")
        await asyncio.sleep(5)

        try:
            await page.wait_for_selector("div#ctl00_divProfile", timeout=30000)
            print("✅ Login completado correctamente")
        except Exception:
            print("❌ No se pudo iniciar sesión")
            await browser.close()
            return

        # Ir a La Fundi y Oferta de actividades
        await volver_a_fundi_y_actividades(page)

        # --- Cargar clases reservadas recientes ---
        reservadas = await cargar_reservadas()
        ahora = datetime.datetime.now()

        # --- Crear lista de candidatas ---
        candidatas = []
        for clase in CLASES:
            fecha_clase = proxima_fecha(clase["dia"], clase["hora"])
            hora_apertura = fecha_clase - datetime.timedelta(hours=49)

            ya = any(
                r["nombre"] == clase["nombre"] and
                r["hora"] == clase["hora"] and
                r["fecha"] == fecha_clase.strftime("%Y-%m-%d")
                for r in reservadas
            )
            if ya or ahora > fecha_clase:
                continue

            candidatas.append((hora_apertura, clase, fecha_clase))

        if not candidatas:
            print("⏹️ No hay clases próximas para reservar. Cerrando programa.")
            await browser.close()
            return

        # --- Ordenar clases: abiertas primero, luego próximas a abrir ---
        abiertas = [c for c in candidatas if c[0] <= ahora]
        no_abiertas = [c for c in candidatas if c[0] > ahora]

        while abiertas or no_abiertas:
            if abiertas:
                abiertas.sort(key=lambda x: x[0])
                hora_apertura, clase_objetivo, fecha_clase = abiertas.pop(0)
                print(f"🎯 Clase ya abierta: {clase_objetivo['nombre']} {clase_objetivo['hora']}")
            else:
                no_abiertas.sort(key=lambda x: x[0])
                hora_apertura, clase_objetivo, fecha_clase = no_abiertas.pop(0)
                ahora = datetime.datetime.now()
                espera = (hora_apertura - ahora).total_seconds()
                if espera > 0:
                    horas, resto = divmod(int(espera), 3600)
                    minutos, segundos = divmod(resto, 60)
                    print(f"⏳ Esperando {horas:02d}:{minutos:02d}:{segundos:02d} hasta la apertura ({hora_apertura.strftime('%d/%m/%Y %H:%M')})...")
                    await asyncio.sleep(espera)
                print(f"🎯 Próxima clase a abrir: {clase_objetivo['nombre']} {clase_objetivo['hora']}")

            # Seleccionar día
            await seleccionar_dia(page, fecha_clase)

            # Esperar que los paneles carguen
            try:
                await page.wait_for_selector("div.panel-body h4.media-heading", timeout=20000)
            except Exception:
                print("⚠️ Paneles no cargaron correctamente, intentando volver a 'La Fundi'...")
                await volver_a_fundi_y_actividades(page)
                await seleccionar_dia(page, fecha_clase)
                await page.wait_for_selector("div.panel-body h4.media-heading", timeout=20000)

            # Intentar reservar la clase
            tiempo_total_intento = 60
            deadline = datetime.datetime.now() + datetime.timedelta(seconds=tiempo_total_intento)
            reservado = False

            while datetime.datetime.now() < deadline:
                estado = await reservar_clase(page, clase_objetivo["nombre"], clase_objetivo["hora"])

                if estado == "RESERVADA":
                    await guardar_reservada(clase_objetivo, fecha_clase)
                    print(f"🎉 Clase '{clase_objetivo['nombre']} {clase_objetivo['hora']}' reservada correctamente")
                    reservado = True
                    break
                elif estado == "PLAZAS_0":
                    print(f"⚠️ Clase {clase_objetivo['nombre']} {clase_objetivo['hora']} descartada (PLAZAS_0). Pasando a la siguiente...")
                    break
                elif estado == "YA_TIENE":
                    print("⚠️ El sistema indica que ya tienes una clase incompatible. Pasando a la siguiente...")
                    break
                elif estado == "NO_ABIERTA":
                    print("⏳ La clase todavía no está abierta. Reintentando...")
                elif estado == "NO_ENCONTRADA":
                    print("⚠️ Clase no encontrada todavía, refrescando el día...")
                    await seleccionar_dia(page, fecha_clase)
                    await asyncio.sleep(1)
                else:
                    print("⚠️ Estado inesperado:", estado, " — reintentando...")

                await asyncio.sleep(0.5)

            if reservado:
                break  # Salimos si hemos reservado exitosamente

        print("❌ No se consiguieron reservar más clases o todas fueron descartadas.")
        await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("⏹️ Programa interrumpido por el usuario.")
