"""
Ayudante de configuracion inicial (correr UNA sola vez por maquina).

Abre el MISMO perfil de Chromium que usa el bot para el Modo IA
(GOOGLE_AI_PROFILE_DIR), en una ventana visible, y se queda esperando.
Mientras esta abierto, TU debes, a mano, en esa ventana:

  1. Iniciar sesion en tu cuenta de Google (la que tiene el Modo IA / AI
     Mode activado -- la misma de tu PC de casa).
  2. Aceptar el aviso de cookies / "Antes de continuar a Google" si aparece.
  3. Confirmar que se ve el buscador del Modo IA con la casilla de texto
     ("Haz una pregunta..."). Si dice "AI Mode is not currently available",
     activa AI Mode en https://www.google.com/search?udm=50 entrando a
     Search Labs (icono del matraz) y habilitando "AI Mode", o entra con
     una cuenta que ya lo tenga.

Cuando el buscador del Modo IA se vea bien, vuelve a esta terminal y
presiona ENTER: la sesion queda guardada en el perfil y el bot ya podra
usarla en las siguientes corridas sin volver a pedir login.
"""
from playwright.sync_api import sync_playwright

from app.config import GOOGLE_AI_PROFILE_DIR, GOOGLE_AI_URL

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        GOOGLE_AI_PROFILE_DIR,
        headless=False,
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(GOOGLE_AI_URL)
    print("\n" + "=" * 70)
    print("Inicia sesion en Google y deja visible el buscador del Modo IA.")
    print("Cuando lo veas bien, vuelve aqui y presiona ENTER para guardar.")
    print("=" * 70 + "\n")
    input("Presiona ENTER cuando hayas terminado el login... ")
    context.close()
    print("Sesion guardada en", GOOGLE_AI_PROFILE_DIR)
