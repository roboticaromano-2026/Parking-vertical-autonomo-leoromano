// ==================================================================================
// 1. LIBRERIAS Y DEFINICIONES
// ==================================================================================
// --- LIBRERIAS REQUERIDAS ---
#include <AccelStepper.h>
#include <Servo.h>

// Pines del Motor NEMA 17 y DriverDRV8825
const int PinNemaPaso = 50;
const int PinNemaDireccion = 51;
const int PinNemaEnable = 52;

// Sensores de Nivel (Lector fijo en Plataforma)
const int PinLectorNivel3 = 9; // Sensor de alineación de Nivel 3
// Sensores de Limite de Seguridad (Microswitches - TOPE CRÍTICO Y CERO)
const int PinLimiteInferior = 10;

// Otros pines (omito comentarios para centrarme en el Homing)
const int PinServoBarrera = 4;
const int PosicionBarreraCerrada = 0;
const int PosicionBarreraAbierta = 90;
const int PinSensorAutoNuevo1 = 5;
const int PinSensorAutoNuevo2 = 6;
const int PinLectorNivel1 = 7;
const int PinLectorNivel2 = 8;
const int PinLimiteSuperior = 11;
const int PinLedRojo = 12;
const int PinLedVerde = 13;
const int PinSensorEstacionamiento1 = 22;
const int PinSensorEstacionamiento2 = 23;
const int PinSensorEstacionamiento3 = 24;


// ==================================================================================
// 2. OBJETOS GLOBALES Y POSICIONES
// ==================================================================================

AccelStepper MotorAscensor(AccelStepper::DRIVER, PinNemaPaso, PinNemaDireccion);
Servo ServoBarrera;

// POSICIONES: Nivel 3 es el punto de inicio (0)
const long PosicionNivel3 = 0;
const long PasosEntreNiveles = 4000;

// --- CONSTANTE CRÍTICA DE HOMING (OPTIMIZADA) ---
// Distancia fija en pasos para la FASE 2. Ajustada de 1000 a 500 pasos.
const long PasosEntreN3yLimite = 500;

// --- VARIABLES GLOBALES DE COMUNICACIÓN Y ESTADO ---
String ComandoRecibido = "";
int EstadoSensorAutoNuevo1 = 0;
int EstadoSensorAutoNuevo2 = 0;
int EstadoSensorEstacionamiento1 = 0;
int EstadoSensorEstacionamiento2 = 0;
int EstadoSensorEstacionamiento3 = 0;
String EstadoBarrera = "Cerrada";
int EstadoLimiteInferior = 0;
int EstadoLimiteSuperior = 0;
int MotorActivo = 0;
int EstadoAlineacionNivel1 = 0;
int EstadoAlineacionNivel2 = 0;
int EstadoAlineacionNivel3 = 0;
String NivelAlineadoTexto = "SinAlinear";


// --- PROTOTIPOS DE FUNCIONES ---
void EnviarEvento(String Evento);
void LeerSensores();
void ProcesarComando(String Comando);
void HabilitarMotor();
void DeshabilitarMotor();
void MoverAscensor(int NivelDestino);
void CalibrarSistema();


// ----------------------------------------------------------------------
// SETUP
// ----------------------------------------------------------------------

void setup() {
    Serial.begin(9600);
    // 1. Pines de Entrada: Todos con Pullup
    pinMode(PinLectorNivel1, INPUT_PULLUP);
    pinMode(PinLectorNivel2, INPUT_PULLUP);
    pinMode(PinLectorNivel3, INPUT_PULLUP);
    pinMode(PinLimiteInferior, INPUT_PULLUP);
    pinMode(PinLimiteSuperior, INPUT_PULLUP);
    pinMode(PinSensorAutoNuevo1, INPUT_PULLUP);
    pinMode(PinSensorAutoNuevo2, INPUT_PULLUP);
    pinMode(PinSensorEstacionamiento1, INPUT_PULLUP);
    pinMode(PinSensorEstacionamiento2, INPUT_PULLUP);
    pinMode(PinSensorEstacionamiento3, INPUT_PULLUP);
   
    // 2. Pines de Salida
    pinMode(PinLedRojo, OUTPUT);
    pinMode(PinLedVerde, OUTPUT);
    pinMode(PinNemaEnable, OUTPUT);
    pinMode(PinNemaDireccion, OUTPUT);
    pinMode(PinNemaPaso, OUTPUT);
    digitalWrite(PinLedVerde, LOW);
    digitalWrite(PinLedRojo, LOW);
   
    // 3. Configuracion de Actuadores
    ServoBarrera.attach(PinServoBarrera);
    ServoBarrera.write(PosicionBarreraCerrada);
   
    // Velocidad Máxima Global (Controla FASE 2 y movimientos normales)
    MotorAscensor.setMaxSpeed(3500.0);
    // Aceleración media para movimientos seguros.
    MotorAscensor.setAcceleration(800.0);
   
    DeshabilitarMotor();

    EnviarEvento("SistemaInicializado");
}

// ----------------------------------------------------------------------
// LOOP
// ----------------------------------------------------------------------

void loop() {
    // 1. Lectura y Procesamiento de Comandos Seriales de Python
    if (Serial.available() > 0) {
        char CaracterEntrante = Serial.read();
       
        if (CaracterEntrante == '\n') {
            ProcesarComando(ComandoRecibido);
            ComandoRecibido = "";
        } else {
            ComandoRecibido += CaracterEntrante;
        }
    }

    // 2. Lectura y Detección de Sensores CRÍTICOS (Límites)
    LeerSensores();
   
    // 3. Manejo de Movimiento del Motor (Debe ejecutarse continuamente)
    MotorAscensor.run();
    MotorActivo = MotorAscensor.distanceToGo() != 0;

    // 4. Chequeo de Movimiento Finalizado
    if (MotorAscensor.distanceToGo() == 0 && MotorActivo) {
        EnviarEvento("MovimientoFinalizado");
        MotorAscensor.stop();
        DeshabilitarMotor();
        MotorActivo = 0;
    }

    // 5. Reporte Periódico de Estado
    static unsigned long TiempoUltimoReporte = 0;
    if (millis() - TiempoUltimoReporte >= 250) { // Reporta cada 250ms
        EnviarEvento("ReporteEstado");
        TiempoUltimoReporte = millis();
    }
}

// ----------------------------------------------------------------------
// FUNCIONES AUXILIARES
// ----------------------------------------------------------------------

void EnviarEvento(String Evento) {
    if (Evento.startsWith("ACK_") || Evento.startsWith("ERROR:") || Evento.startsWith("CRITICO_")) {
        Serial.print(Evento);
        Serial.print('\n');
        return;
    }
   
    if (Evento == "ReporteEstado") {
        String Trama = "Estacionamiento1:" + String(EstadoSensorEstacionamiento1) +
                       ",Estacionamiento2:" + String(EstadoSensorEstacionamiento2) +
                       ",Estacionamiento3:" + String(EstadoSensorEstacionamiento3) +
                       ",AutoNuevo1:" + String(EstadoSensorAutoNuevo1) +
                       ",AutoNuevo2:" + String(EstadoSensorAutoNuevo2) +
                       ",PlataformaAlineada:" + NivelAlineadoTexto +
                       ",AlineacionNivel1:" + String(EstadoAlineacionNivel1) +
                       ",AlineacionNivel2:" + String(EstadoAlineacionNivel2) +
                       ",AlineacionNivel3:" + String(EstadoAlineacionNivel3) +
                       ",EstadoBarrera:" + EstadoBarrera +
                       ",LimiteInferior:" + String(EstadoLimiteInferior) +
                       ",LimiteSuperior:" + String(EstadoLimiteSuperior) +
                       ",MotorActivo:" + String(MotorAscensor.distanceToGo() != 0);
       
        Serial.print(Trama);
        Serial.print('\n');
        return;
    }

    Serial.print(Evento);
    Serial.print('\n');
}

void LeerSensores() {
    // Lectura de Límites (PinMode INPUT_PULLUP, LOW cuando es presionado -> Lógica Invertida)
    EstadoLimiteInferior = !digitalRead(PinLimiteInferior);
    EstadoLimiteSuperior = !digitalRead(PinLimiteSuperior);
   
    // ----------------------------------------------------------
    // LÓGICA DE SEGURIDAD CRÍTICA (¡ESENCIAL! - Detiene al tocar un límite)
    // ----------------------------------------------------------
    if (EstadoLimiteSuperior == 1 || EstadoLimiteInferior == 1) {
        // Solo actuar si el motor está intentando moverse
        if (MotorAscensor.speed() != 0 || MotorAscensor.distanceToGo() != 0) {
            MotorAscensor.stop();
           
            // Forzar la posición a 0 si es el límite inferior, o al máximo si es el superior
            if (EstadoLimiteInferior == 1) {
                MotorAscensor.setCurrentPosition(PosicionNivel3);
            } else {
                MotorAscensor.setCurrentPosition(PosicionNivel3 + (PasosEntreNiveles * 2));
            }

            DeshabilitarMotor();
           
            EnviarEvento("CRITICO_LimiteTocado");
            return;
        }
    }
   
    // --- LECTURA DE SENSORES NO CRÍTICOS ---
    EstadoSensorAutoNuevo1 = !digitalRead(PinSensorAutoNuevo1);
    EstadoSensorAutoNuevo2 = !digitalRead(PinSensorAutoNuevo2);
    EstadoSensorEstacionamiento1 = !digitalRead(PinSensorEstacionamiento1);
    EstadoSensorEstacionamiento2 = !digitalRead(PinSensorEstacionamiento2);
    EstadoSensorEstacionamiento3 = !digitalRead(PinSensorEstacionamiento3);
    EstadoAlineacionNivel1 = !digitalRead(PinLectorNivel1);
    EstadoAlineacionNivel2 = !digitalRead(PinLectorNivel2);
    EstadoAlineacionNivel3 = !digitalRead(PinLectorNivel3);
   
    // Actualización del texto de alineación
    if (EstadoAlineacionNivel1 == 1) {  
        NivelAlineadoTexto = "Nivel1";
    } else if (EstadoAlineacionNivel2 == 1) {
        NivelAlineadoTexto = "Nivel2";
    } else if (EstadoAlineacionNivel3 == 1) {
        NivelAlineadoTexto = "Nivel3";
    } else {
        NivelAlineadoTexto = "SinAlinear";  
    }
}

void ProcesarComando(String Comando) {
    if (Comando.startsWith("Abrir") || Comando.startsWith("Cerrar") || Comando.startsWith("Mover") || Comando.startsWith("Calibrar")) {
        EnviarEvento("ACK_" + Comando);
    }

    if (Comando == "AbrirBarrera") {
        ServoBarrera.write(PosicionBarreraAbierta);
        delay(500);
        EstadoBarrera = "Abierta";
    } else if (Comando == "CerrarBarrera") {
        ServoBarrera.write(PosicionBarreraCerrada);
        delay(500);
        EstadoBarrera = "Cerrada";
    } else if (Comando == "MoverAscensorPiso1") {
        MoverAscensor(1);
    } else if (Comando == "MoverAscensorPiso2") {
        MoverAscensor(2);
    } else if (Comando == "MoverAscensorPiso3") {
        MoverAscensor(3);
    } else if (Comando == "PararAscensor") {
        MotorAscensor.stop();
        DeshabilitarMotor();
        EnviarEvento("ParadaManual");
    } else if (Comando == "CalibrarSistema") {
        CalibrarSistema();
    }
}


void HabilitarMotor() {
    digitalWrite(PinNemaEnable, LOW);
}

void DeshabilitarMotor() {
    digitalWrite(PinNemaEnable, HIGH);
}

void MoverAscensor(int NivelDestino) {
    if (EstadoLimiteSuperior == 1 || EstadoLimiteInferior == 1) {
        EnviarEvento("ERROR: Limite bloqueado");
        return;
    }
   
    long PosicionDestino = 0;
    HabilitarMotor();

    // LÓGICA: Nivel 3 (Posición 0) está abajo, Nivel 1 (Posición 8000) está arriba.
    if (NivelDestino == 3) {
        PosicionDestino = PosicionNivel3; // 0 pasos
    } else if (NivelDestino == 2) {
        PosicionDestino = PosicionNivel3 + PasosEntreNiveles; // 4000 pasos
    } else if (NivelDestino == 1) {
        PosicionDestino = PosicionNivel3 + (PasosEntreNiveles * 2); // 8000 pasos
    }
   
    MotorAscensor.moveTo(PosicionDestino);
}

// ----------------------------------------------------------------------
// FUNCIÓN FINAL: HOMING (Triple Seguridad)
// ----------------------------------------------------------------------

void CalibrarSistema() {
    EnviarEvento("CalibracionIniciada");
    HabilitarMotor();
   
    // FASE 1: Búsqueda Rápida de Nivel 3 (Pre-parada)
    // Velocidad optimizada por el usuario.
    const float VelocidadBusquedaInicial = 1200.0;
   
    if (digitalRead(PinLectorNivel3) == HIGH) {
        MotorAscensor.setSpeed(VelocidadBusquedaInicial);
       
        // Mover hacia abajo hasta que se active el Sensor Nivel 3 (LOW) o el Límite Crítico.
        while (digitalRead(PinLectorNivel3) == HIGH && digitalRead(PinLimiteInferior) == HIGH) {
            MotorAscensor.runSpeed();
            LeerSensores();
            if (EstadoLimiteInferior == 1) break;
        }
       
        MotorAscensor.stop();
        while (MotorAscensor.distanceToGo() != 0) { MotorAscensor.run(); } // Esperar la parada
    }
   
    // ----------------------------------------------------------
    // FASE 2: Movimiento de Distancia Fija (Optimización de velocidad - Usa 3500.0)
    // ----------------------------------------------------------
   
    // Retroceder 5 pasos para liberar el sensor N3 (Si estaba activo)
    MotorAscensor.move(-5);
    while (MotorAscensor.distanceToGo() != 0) { MotorAscensor.run(); }
   
    // Mover la distancia fija conocida (AHORA 500 pasos) con la MaxSpeed.
    // PasosEntreN3yLimite ahora es 500.
    MotorAscensor.move(PasosEntreN3yLimite);
    while (MotorAscensor.distanceToGo() != 0) { MotorAscensor.run(); }

    // ----------------------------------------------------------
    // FASE 3: Toque de Precisión (Límite Absoluto - La clave de la seguridad)
    // ----------------------------------------------------------
   
    // Velocidad EXTREMADAMENTE lenta y segura (50.0) para eliminar la inercia.
    const float VelocidadBusquedaLenta = 50.0;  
   
    if (digitalRead(PinLimiteInferior) == HIGH) {
        MotorAscensor.setSpeed(VelocidadBusquedaLenta);
       
        // Mover muy lentamente hasta que el PinLimiteInferior se active (LOW)
        while (digitalRead(PinLimiteInferior) == HIGH) {
            MotorAscensor.runSpeed();
            LeerSensores();
            if (EstadoLimiteInferior == 1) break;
        }
    } else {
        EnviarEvento("Alerta: Limite Inferior ya activo. Asumiendo Cero.");
    }
   
    // Parada, Retroceso y Set de Cero
    MotorAscensor.stop();
   
    // Retroceder un pequeño margen (ej: 5 pasos) para liberar el sensor de límite.
    MotorAscensor.move(-5);
    while (MotorAscensor.distanceToGo() != 0) {
        MotorAscensor.run();
    }

    // Setear la posición actual como 0 (Punto de Origen - Nivel 3 alineado en Planta Baja)
    MotorAscensor.setCurrentPosition(PosicionNivel3);
   
    DeshabilitarMotor();
    EnviarEvento("CalibracionFinalizada");
}

