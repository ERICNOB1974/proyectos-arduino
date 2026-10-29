#include <regex>

#if CONFIG_FREERTOS_UNICORE
#define TASK_RUNNING_CORE 0
#else
#define TASK_RUNNING_CORE 1
#endif

// Constantes
#define HUMEDAD_MINIMA_SUELO 30    // %
#define HUMEDAD_MAXIMA_SUELO 90    // %
#define RPM_MINIMAS_BOMBA 600      // rpm
#define TIEMPO_INTERVALO_RIEGO 30  // segundos
#define TIEMPO_ENCENDIDO_BOMBA 10  // segundos
#define TIEMPO_SIN_CAUDAL 5        // segundos
#define TIEMPO_VERIFICACION_RPM 1  // segundos
#define TIEMPO_ALARMA_CAUDAL 15    // segundos
#define TIEMPO_ALARMA_ROTACION 30  // segundos

typedef struct {
  uint32_t wcet;
  uint32_t periodo;
  UBaseType_t prioridad;
} TareaConfig;

// Variables globales
int humedadActual = 50;
int velocidadRPM = 800;

bool hayCaudal = true;
bool bombaEncendida = false;
bool necesidadRiego = false;
bool rpmVerificado = false;
bool contandoSinCaudal = false;

bool alarmaFaltaDeCaudalActiva = false;
bool alarmaRPMSinRotacionActiva = false;

unsigned long momentoEncendido = 0;
unsigned long momentoSinCaudal = 0;
unsigned long momentoAlarmaCaudal = 0;
unsigned long momentoAlarmaRotacion = 0;
unsigned long ultimoInicioRiego = millis() - TIEMPO_INTERVALO_RIEGO * 1000;

// Configuracion de tareas
static const TareaConfig leerSensorCaudal = { 0, 1000, configMAX_PRIORITIES - 1 };  // prioridad mas alta, si la bomba esta encendida, lee si hay caudal
static const TareaConfig evaluarBomba = { 0, 1000, configMAX_PRIORITIES - 2 };      // verificar al menos 600 rpm y enciende alarma
static const TareaConfig leerSensorHumedad = { 0, 30000, configMAX_PRIORITIES - 3 };

void setup() {
  Serial.begin(115200);

  xTaskCreatePinnedToCore(
    LeerSensorCaudal,
    "Leer sensor caudal",
    4096,
    (void *)&leerSensorCaudal,
    leerSensorCaudal.prioridad,
    NULL,
    TASK_RUNNING_CORE);

  xTaskCreatePinnedToCore(
    EvaluarBomba,
    "Evaluar bomba",
    4096,
    (void *)&evaluarBomba,
    evaluarBomba.prioridad,
    NULL,
    TASK_RUNNING_CORE);

  xTaskCreatePinnedToCore(
    LeerSensorHumedad,
    "Leer sensor humedad",
    4096,
    (void *)&leerSensorHumedad,
    leerSensorHumedad.prioridad,
    NULL,
    TASK_RUNNING_CORE);
}

void loop() {
}

/*
   Lee si hay caudal o no cuando la bomba esta encedida
*/
void LeerSensorCaudal(void *pvParameters) {
  const TareaConfig *config = (const TareaConfig *)pvParameters;

  TickType_t inicio = xTaskGetTickCount();

  for (;;) {

    if (Serial.available()) {
      String entrada = Serial.readStringUntil('\n');
      entrada.trim();
      if (entrada == "C:0") {
        hayCaudal = false;
      } else if (entrada == "C:1") {
        hayCaudal = true;
      }
    }
    vTaskDelayUntil(
      &inicio,
      pdMS_TO_TICKS(config->periodo));
  }
}

/*
   Lee humedad por la entrada serial con el formato "H:XX" donde XX es un numero que representa del porcentaje de humedad del suelo
*/
void LeerSensorHumedad(void *pvParameters) {
  const TareaConfig *config = (const TareaConfig *)pvParameters;

  TickType_t inicio = xTaskGetTickCount();

  for (;;) {

    if (Serial.available()) {
      String entrada = Serial.readStringUntil('\n');

      if (entrada.startsWith("H:") && entrada.length() > 2) {

        entrada.trim();

        int separador = entrada.indexOf(':');

        String valorStr = entrada.substring(separador + 1);

        if (std::regex_match(valorStr.c_str(), std::regex("\\d+"))) {

          int valor = valorStr.toInt();

          if (valor >= 0 && valor <= 100) {
            humedadActual = valor;
            Serial.printf("Humedad leida: %d%%\n", humedadActual);
          } else {
            Serial.printf("Humedad fuera de rango. Valor actual: %d%%\n", humedadActual);
          }

        } else {
          Serial.printf("Humedad actual: %d%%\n", humedadActual);
        }

      } else {
        Serial.printf("Humedad actual: %d%%\n", humedadActual);
      }

    } else {
      Serial.printf("Humedad actual: %d%%\n", humedadActual);
    }

    vTaskDelayUntil(
      &inicio,
      pdMS_TO_TICKS(config->periodo));
  }
}

/*
   Si la humedad actual es < 30, se activa la bomba de riego durante 10 minutos.
   Si la humeda actual > 90, se apaga la bomba de riego.
*/
void EvaluarBomba(void *pvParameters) {
  const TareaConfig *config = (const TareaConfig *)pvParameters;

  TickType_t inicio = xTaskGetTickCount();

  for (;;) {
    unsigned long ahora = millis();

    if (humedadActual < HUMEDAD_MINIMA_SUELO) {
      necesidadRiego = true;
    } else if (humedadActual > HUMEDAD_MAXIMA_SUELO) {
      necesidadRiego = false;
      bombaEncendida = false;
      Serial.println("Bomba: APAGADA");
    }

    //si se necesita riego, la bomba está apagada y el tiempo desde que se regó por ultima vez es mayor a 30
    if (necesidadRiego && !bombaEncendida && (ahora - ultimoInicioRiego >= TIEMPO_INTERVALO_RIEGO * 1000)) {
      //prender bomba
      bombaEncendida = true;
      ultimoInicioRiego = ahora;
      momentoEncendido = ahora;
      rpmVerificado = false;
      Serial.println("Bomba: ENCENDIDA");
    }

    if (bombaEncendida && !hayCaudal) {

      if (!contandoSinCaudal) {
        momentoSinCaudal = ahora;
        contandoSinCaudal = true;
      }

      //tiempo actual - primer momento sin caudal >= 5 segundos
      if (ahora - momentoSinCaudal >= TIEMPO_SIN_CAUDAL * 1000) {
        bombaEncendida = false;
        contandoSinCaudal = false;

        alarmaFaltaDeCaudalActiva = true;

        //cuando arranca la alarma de sin caudal
        momentoAlarmaCaudal = ahora;

        Serial.println("Bomba: APAGADA");
        Serial.println("ERROR: Bomba detenida por falta de caudal");
      }

    } else {
      contandoSinCaudal = false;
    }


    if (bombaEncendida && !rpmVerificado && (ahora - momentoEncendido >= TIEMPO_VERIFICACION_RPM * 1000)) {
      rpmVerificado = true;
      if (velocidadRPM < RPM_MINIMAS_BOMBA) {
        Serial.println("ERROR: Bomba sin rotación");
        alarmaRPMSinRotacionActiva = true;
        momentoAlarmaRotacion = ahora;
      }
    }

    // La bomba ya trabajó por 10 segundos, por lo tanto, se apaga
    if (bombaEncendida && (ahora - momentoEncendido >= TIEMPO_ENCENDIDO_BOMBA * 1000)) {
      bombaEncendida = false;
      Serial.println("Bomba: APAGADA");
    }

    if (alarmaFaltaDeCaudalActiva && (ahora - momentoAlarmaCaudal >= TIEMPO_ALARMA_CAUDAL * 1000)) {
      alarmaFaltaDeCaudalActiva = false;
    }

    if (alarmaRPMSinRotacionActiva && (ahora - momentoAlarmaRotacion >= TIEMPO_ALARMA_ROTACION * 1000)) {
      alarmaRPMSinRotacionActiva = false;
    }

    vTaskDelayUntil(
      &inicio,
      pdMS_TO_TICKS(config->periodo));
  }
}