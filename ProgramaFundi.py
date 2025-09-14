import asyncio
from playwright.async_api import async_playwright
from dotenv import load_dotenv
import os
import datetime
import json
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

RESERVADAS_FILE = os.path.join(os.path.dirname(__file__), "clases_reservadas.json")


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
        try:
            await page.click("div.navigation-section-widget-collection-item-image-icon-square")
            print("✅ Click realizado en el div de la clase.")
        except:
            print("❌ No se pudo hacer click en el div (puede que no sea necesario).")
        # --- Login automático ---
        await page.fill("input[name='ctl00$ContentFixedSection$uLogin$txtIdentificador']", EMAIL)
        await page.fill("input[name='ctl00$ContentFixedSection$uLogin$txtContrasena']", PASSWORD)
        await page.click("button#ContentFixedSection_uLogin_btnLogin")
        print("⌛ Login enviado, esperando a que cargue tu perfil...")

        # --- Esperar a que cargue el perfil ---
        try:
            await page.wait_for_selector("div#ctl00_divProfile", timeout=30000)
            print("✅ Login completado correctamente")
        except:
            print("❌ No se pudo iniciar sesión")
            await browser.close()
            return

        # Selector específico para el artículo "La Fundi"
        selector = "article.navigation-section-widget-collection-item h4[title='La Fundi']"
        try:
            await page.wait_for_selector(selector, timeout=5000)
            await page.click(selector)
            print("✅ Click correcto disparado en el artículo 'La Fundi'.")
        except Exception as e:
            print("❌ No se pudo hacer click en el artículo 'La Fundi'.", e)

        # Selector para el artículo "Oferta de actividades por día y centro"
        selector_actividades = "article.navigation-section-widget-collection-item h4[title='Oferta de actividades por día y centro']"
        try:
            await page.wait_for_selector(selector_actividades, timeout=5000)
            await page.click(selector_actividades)
            print("✅ Click correcto en 'Oferta de actividades por día y centro'.")
            print(f"🕒 Hora actual: {datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}", flush=True)
        except Exception as e:
            print("❌ No se pudo hacer click en 'Oferta de actividades por día y centro'.", e)

            
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
            objetivo = hoy.replace(hour=int(hora.split(":")[0]), minute=int(hora.split(":")[1]), second=0, microsecond=0)
            dias_a_sumar = (DIAS[dia_semana] - hoy.weekday() + 7) % 7
            if dias_a_sumar == 0 and hoy < objetivo:
                return objetivo
            elif dias_a_sumar == 0:
                return objetivo + datetime.timedelta(days=7)
            else:
                return objetivo + datetime.timedelta(days=dias_a_sumar)

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
            panels = page.locator("div.panel-body")
            count_panels = await panels.count()

            for i in range(count_panels):
                panel = panels.nth(i)
                nombre_web = await panel.locator("h4.media-heading").first.inner_text()
                if nombre_web.strip().lower() != nombre.strip().lower():
                    continue  # Este no es el panel correcto

                slots = panel.locator(f"li.media:has(h4.media-heading:text-is('{hora}'))")
                count_slots = await slots.count()
                for j in range(count_slots):
                    elemento = slots.nth(j)
                    span = elemento.locator("span").first
                    plazas_texto = await span.inner_text()
                    if plazas_texto.strip() == "0":
                        continue  # saltar si no hay plazas
                    print(f"🎉 Clase '{nombre} {hora}' disponible!")
                    # Esperar 1 segundo extra para asegurar que la hora de la web ha cambiado
                    await asyncio.sleep(1)
                    await elemento.click()
                    # Espera el mensaje específico por ID
                    try:
                        await page.wait_for_selector("#ContentFixedSection_uAltaEventos_uAltaEventosFechas_uAlert_divAlertDanger", timeout=1000)
                        # Extrae el texto del mensaje
                        mensaje = await page.locator("#ContentFixedSection_uAltaEventos_uAltaEventosFechas_uAlert_spnAlertDanger").inner_text()
                        if "no permite más de 1 reserva" in mensaje:
                            print("❌ Ya tienes reservada esta clase. Cerrando programa.")
                            global yaLaTienes
                            yaLaTienes = True
                            return 0
                        elif "estará disponible a las" in mensaje:
                            print(f"⏳ La clase estará disponible más tarde: {mensaje}. Sigo intentando...")
                            return False
                        else:
                            print(f"❌ Mensaje inesperado: {mensaje}. Sigo con el flujo.")
                            return False
                    except Exception:
                        print("✅ No hay mensaje de alerta, la reserva probablemente fue correcta.")

                    # Reservar
                    
                    try:
                        boton_confirmar = "button#ContentFixedSection_uCarritoConfirmar_btnConfirmCart"
                        await page.click(boton_confirmar)
                    except Exception:
                        print("⚠️ No hay botón de confirmar, puede que ya esté reservada o no disponible.")

                    

                    # Salir y volver a la Home
                    try:
                        boton_salir = "a.btn.btn-default[href='../../Home']"
                        await page.wait_for_selector(boton_salir, timeout=5000)
                        await page.click(boton_salir)
                        print("🔄 Has vuelto a la página principal.")
                    except Exception:
                        await page.goto("https://deportesweb.madrid.es/DeportesWeb/Home")
                        print("🔄 Has vuelto a la página principal (por URL).")

                    # Esperar a que cargue la Home
                    selector_home = "div#ctl00_divProfile"
                    max_intentos = 5
                    intento = 0
                    while intento < max_intentos:
                        try:
                            await page.wait_for_selector(selector_home, timeout=60000)
                            print("🏠 Página Home cargada correctamente.")
                            break
                        except Exception:
                            intento += 1
                            print(f"⏳ Intento {intento}/{max_intentos}: aún no está la Home...")
                    else:
                        print("❌ No se pudo cargar la página Home después de varios intentos. Saliendo...")
                        return False

                    await volver_a_fundi_y_actividades(page)
                    return True

            print(f"⏭ No se encontró la clase '{nombre} {hora}' disponible.")
            return False

        
        # --- Cargar clases reservadas recientes ---
        reservadas = await cargar_reservadas()

        ahora = datetime.datetime.now()
        CLASES_PENDIENTES = [
            clase for clase in CLASES
            if not any(
                r["nombre"] == clase["nombre"] and
                r["hora"] == clase["hora"] and
                r["fecha"] == proxima_fecha(clase["dia"], clase["hora"]).strftime("%Y-%m-%d")
                for r in reservadas
            )
            and ahora >= proxima_fecha(clase["dia"], clase["hora"]) - datetime.timedelta(hours=51)
        ]

        # --- Si no hay clases pendientes, termina el programa ---
        if not CLASES_PENDIENTES:
            print("⏹️ No hay ninguna clase pendiente para reservar. Cerrando programa.")
            await browser.close()
            return

        

       

       

        # --- Intentar hasta conseguir la reserva ---
        while CLASES_PENDIENTES:
            try:
                for clase in list(CLASES_PENDIENTES):
                    fecha_clase = proxima_fecha(clase["dia"], clase["hora"])
                    hora_apertura_clase = fecha_clase - datetime.timedelta(hours=49)
                    ahora = datetime.datetime.now()
                    # Solo intenta reservar si ya está desbloqueada
                    if ahora < hora_apertura_clase:
                        continue

                    # Selecciona el día en el calendario ANTES de intentar reservar
                    await seleccionar_dia(page, fecha_clase)

                    exito = await reservar_clase(page, clase["nombre"], clase["hora"])
                    if yaLaTienes:
                        await browser.close()
                        return 0
                    if exito:
                        await guardar_reservada(clase, fecha_clase)
                        print(f"🎉 Clase '{clase['nombre']} {clase['hora']}' reservada correctamente")
                        await browser.close()
                        return 0
                    else:
                        print(f"🚫 Quitando la clase '{clase['nombre']} {clase['hora']}' de la lista de pendientes.")
                        CLASES_PENDIENTES.remove(clase)
                        # Si ya no quedan clases pendientes, termina aquí mismo
                        if not CLASES_PENDIENTES:
                            print("⏹️ No hay ninguna clase pendiente para reservar. Cerrando programa.")
                            await browser.close()
                            return

            except Exception as e:
                print("⚠️ Error en el intento, reintentando...", e)
                await asyncio.sleep(2)
                

asyncio.run(main())