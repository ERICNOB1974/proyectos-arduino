
#!/usr/bin/env python3
import serial
import time
import sys
import threading

PUERTO = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
BAUDIOS = 115200

errores = []
eventos = []
lock = threading.Lock()
detener = threading.Event()

INICIO = "Bomba: ENCENDIDA"
APAGADO = "Bomba: APAGADA"
ERROR_CAUDAL = "ERROR: Bomba detenida por falta de caudal"
ERROR_RPM = "ERROR: Bomba sin rotación"

try:
    esp = serial.Serial(PUERTO, BAUDIOS, timeout=0.1)
except Exception as e:
    print("ATENCION, ERROR DE FUNCIONAMIENTO")
    print(f"No se pudo conectar: {e}")
    sys.exit(1)


def lector():
    while not detener.is_set():
        try:
            linea = esp.readline().decode(
                "utf-8", errors="replace"
            ).strip()

            if linea:
                instante = time.monotonic()

                with lock:
                    eventos.append((instante, linea))

                print(f"[ESP32] {linea}", flush=True)

        except Exception as e:
            print(f"Error leyendo Serial: {e}")
            detener.set()
            break


threading.Thread(target=lector, daemon=True).start()


def enviar(mensaje):
    print(f"[PYTHON] {mensaje}", flush=True)
    esp.write((mensaje + "\n").encode())
    esp.flush()


def marca():
    with lock:
        return len(eventos)


def buscar(texto, desde=0):
    with lock:
        for instante, linea in eventos[desde:]:
            if texto in linea:
                return instante
    return None


def esperar(texto, timeout, desde=0):
    limite = time.monotonic() + timeout

    while time.monotonic() < limite:
        encontrado = buscar(texto, desde)

        if encontrado is not None:
            return encontrado

        time.sleep(0.05)

    return None


def error(mensaje):
    errores.append(mensaje)
    print("\nATENCION, ERROR DE FUNCIONAMIENTO")
    print(mensaje, "\n")


def preparar():
    print("\n===== PREPARACION =====")

    # Asegurar que el sistema no necesite riego
    enviar("H:95")
    enviar("C:1")
    enviar("B:800")

    # Esperar una lectura de humedad (periodo 30 s)
    time.sleep(33)


def probar_alarmas_juntas():
    print("\n===== PRUEBA DE ALARMAS SIMULTANEAS =====")

    # Configurar RPM insuficientes antes de encender
    enviar("C:1")
    enviar("B:450")

    time.sleep(2)

    # Provocar necesidad de riego
    indice = marca()
    enviar("H:20")

    print("\nEsperando encendido de bomba...")

    t_encendido = esperar(INICIO, 35, indice)

    if t_encendido is None:
        error("La bomba no encendió con humedad del 20%")
        return

    print("\nBomba encendida. Provocando falta de caudal.")

    # Inmediatamente después del encendido, quitar caudal
    t_corte = time.monotonic()
    enviar("C:0")

    # Esperar suficiente tiempo para ambas fallas
    time.sleep(9)

    t_rpm = buscar(ERROR_RPM, indice)
    if t_rpm is None:
        # Aceptar variante sin tilde
        t_rpm = buscar("ERROR: Bomba sin rotacion", indice)

    t_caudal = buscar(ERROR_CAUDAL, indice)
    t_apagado = buscar(APAGADO, indice)

    print("\n===== RESULTADOS =====")

    # ALARMA RPM
    if t_rpm is None:
        error("No se activó la alarma de RPM")
    else:
        demora = t_rpm - t_encendido
        print(f"Alarma RPM: {demora:.2f} s desde encendido")

        if demora < 0.5 or demora > 3.5:
            error(
                f"Alarma RPM fuera de tiempo: {demora:.2f} s"
            )

    # ALARMA CAUDAL
    if t_caudal is None:
        error("No se activó la alarma de falta de caudal")
    else:
        demora = t_caudal - t_corte
        print(f"Alarma caudal: {demora:.2f} s desde corte")

        if demora < 4 or demora > 8:
            error(
                f"Alarma caudal fuera de tiempo: {demora:.2f} s"
            )

    # APAGADO BOMBA
    if t_apagado is None:
        error("La bomba no se apagó por falta de caudal")
    else:
        demora = t_apagado - t_corte
        print(f"Apagado: {demora:.2f} s desde corte")

        if demora < 4 or demora > 8:
            error(
                f"Apagado fuera de tiempo: {demora:.2f} s"
            )

    # COMPROBAR ORDEN
    if t_rpm and t_caudal:
        if t_rpm > t_caudal:
            error(
                "La alarma RPM se activó después de "
                "la alarma de caudal"
            )

    # COMPROBAR AMBAS ALARMAS
    if t_rpm and t_caudal:
        print("\nAmbas alarmas fueron detectadas.")

    if t_apagado and t_caudal:
        if abs(t_apagado - t_caudal) > 1.5:
            error(
                "El apagado y la alarma de caudal "
                "ocurrieron con demasiado desfase"
            )


try:
    preparar()
    probar_alarmas_juntas()

except KeyboardInterrupt:
    print("\nPrueba interrumpida.")

except Exception as e:
    error(f"Excepción durante la prueba: {e}")

finally:
    print("\nRestaurando sensores...")
    try:
        enviar("H:95")
        enviar("C:1")
        enviar("B:800")
    except Exception:
        pass

    time.sleep(0.5)
    detener.set()
    esp.close()

    print("\n" + "=" * 55)
    print("RESUMEN FINAL")
    print("=" * 55)

    if errores:
        print("ATENCION, ERROR DE FUNCIONAMIENTO")
        print(f"Se detectaron {len(errores)} problemas:")

        for numero, mensaje in enumerate(errores, 1):
            print(f"{numero}. {mensaje}")

        sys.exit(1)
    else:
        print("PRUEBAS APROBADAS")
        print("No se detectaron errores en los eventos observables.")
        print("Pendiente: verificar duracion de alarmas.")
