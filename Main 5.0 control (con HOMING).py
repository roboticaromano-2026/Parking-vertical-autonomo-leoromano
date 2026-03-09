# =========================================================================
# Parking System - Parte 1: BASE, ESTADO Y COMUNICACIÓN SERIAL
# =========================================================================
import serial
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog # ttk es necesario para el Scrollbar
import pyttsx3
import random
import string
import sys
import os

# --- Variables de Control Globales ---
NivelRetiroSeleccionado = None
BienvenidaReproducida = False
DNI_INTENTOS_MAX = 3
# ParkingAsignado: 1 para Flujo Ingreso DNI | 0 para Flujo Egreso DNI | None para Inactivo
ParkingAsignado = None
# SIMULACIÓN: {DNI: {"codigo": "XXXXXX", "nivel_asignado": N}}
RegistroVehiculos = {
    # Ejemplo para pruebas de Egreso
    "12345678": {"codigo": "ABCD12", "nivel_asignado": 1}
}
DNI_ACTUAL = {"DNI": "", "INTENTOS": 0, "TIEMPO_INICIO": 0}
TIEMPO_LIMITE_DNI_S = 60

# --- ESTADO CRÍTICO (NUEVO/REFORZADO) ---
EstadoCritico = False
MensajeCritico = ""

# --- Estado Crudo (Directamente del Serial) ---
# Claves deben coincidir con la trama enviada por Arduino
EstadoParking = {
    "Estacionamiento1": 0, "Estacionamiento2": 0, "Estacionamiento3": 0,
    "AutoNuevo1": 0, "AutoNuevo2": 0,
    "Plataforma en PB": "SinAlinear",
    "EstadoBarrera": "Cerrada",
    "LimiteInferior": 0, "LimiteSuperior": 0,
    "AlineacionNivel1": 0, "AlineacionNivel2": 0, "AlineacionNivel3": 0,
    "MotorActivo": 0
}

# --- Constantes de la Aplicación ---
INTERVALO_SUPERVISION_MS = 250
# ¡ATENCIÓN! Asegúrate de que este puerto coincida con el puerto de tu Arduino
COM_PORT = 'COM3'

# =========================================================================
# 2. MOTOR DE TEXTO A VOZ (TTS)
# =========================================================================

tts_engine = None
try:
    # Intentar inicializar el motor TTS
    tts_engine = pyttsx3.init()
    voices = tts_engine.getProperty('voices')
    # Buscar una voz en español
    spanish_voice = next((v for v in voices if 'es' in v.id or 'spanish' in v.name.lower()), None)
    if spanish_voice:
        tts_engine.setProperty('voice', spanish_voice.id)
    tts_engine.setProperty('rate', 150) # Velocidad de voz
except Exception as e:
    print(f"Advertencia: No se pudo inicializar pyttsx3. {e}")
    tts_engine = None

def tts_speak(text, wait=False):
    """Reproduce el texto usando TTS."""
    if tts_engine:
        if tts_engine._inLoop:
            tts_engine.stop()
           
        tts_engine.say(text)
        if wait:
            # Espera forzada para garantizar la reproducción inmediata en el flujo de botones
            tts_engine.runAndWait()
        else:
            # Hilo de fondo para mensajes no críticos
            threading.Thread(target=tts_engine.runAndWait, daemon=True).start()

# --- NUEVA FUNCIÓN HELPER GLOBAL ---
def generate_retrieval_code():
    """Genera un código de retiro alfanumérico aleatorio de 6 caracteres."""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(6))
# -----------------------------------

# =========================================================================
# 3. COMUNICACIÓN SERIAL (Clase Central)
# =========================================================================

class ComunicacionParking:
    def __init__(self, port):
        self.port = port
        self.baud_rate = 9600
        self.ser = None
        self.running = True
        self.lock = threading.Lock()
        self.command_queue = []
       
        self.connect()

        self.read_thread = threading.Thread(target=self._read_from_serial, daemon=True)
        self.read_thread.start()
       
        self.write_thread = threading.Thread(target=self._write_to_serial, daemon=True)
        self.write_thread.start()

    def connect(self):
        """Intenta establecer la conexión serial."""
        try:
            self.ser = serial.Serial(self.port, self.baud_rate, timeout=0.5)
            print(f"Conectado exitosamente a {self.port}")
        except serial.SerialException as e:
            print(f"ERROR al conectar al puerto {self.port}: {e}")
            self.ser = None

    def cerrarConexion(self):
        """Detiene los hilos y cierra la conexión serial de forma segura."""
        self.running = False
        if self.ser and self.ser.is_open:
            self.ser.close()
            print("Conexión serial cerrada.")

    def send_command(self, command):
        """Añade un comando a la cola de escritura."""
        with self.lock:
            self.command_queue.append(command)
       
    def _write_to_serial(self):
        """Hilo dedicado a enviar comandos desde la cola."""
        while self.running:
            if self.ser and self.ser.is_open:
                if self.command_queue:
                    with self.lock:
                        command = self.command_queue.pop(0)
                    try:
                        self.ser.write(f"{command}\n".encode('utf-8'))
                        print(f"[TX] Enviado: {command}")
                        time.sleep(0.1)
                    except Exception as e:
                        print(f"ERROR al enviar comando: {e}")
                        if self.ser and self.ser.is_open:
                            self.ser.close()
                        self.ser = None
            time.sleep(0.05)
           
    def _read_from_serial(self):
        """Hilo dedicado para la lectura constante del puerto serial."""
        while self.running:
            if self.ser and self.ser.is_open:
                try:
                    line = self.ser.readline().decode('utf-8').strip()
                    if line:
                        self._process_serial_data(line)
                except serial.SerialTimeoutException:
                    pass
                except Exception as e:
                    print(f"ERROR de lectura serial: {e}")
                    time.sleep(1)
            else:
                self.connect()
                time.sleep(0.5)

    def _trigger_critical_error(self, message, source="Arduino"):
        """Función helper para activar el estado crítico de forma segura."""
        global EstadoCritico, MensajeCritico
        if not EstadoCritico:
            MensajeCritico = message
            EstadoCritico = True
            print(f"[RX] !!! ALERTA CRÍTICA DETECTADA ({source}): {MensajeCritico} !!!")
            # Usar wait=True para asegurar que el audio de alerta se reproduzca
            tts_speak(f"ALERTA CRÍTICA. {message}. Ejecutando parada de emergencia.", wait=True)
            # Enviar comandos de seguridad al Arduino
            self.send_command("PararAscensor")
            self.send_command("CerrarBarrera")
           
    def _get_critical_error_cause(self):
        """Revisa el estado actual de los sensores y devuelve un mensaje de error si existe, o None."""
        global EstadoParking
       
        # Check 1: Límites Inferior y Superior ACTIVOS a la vez (FALLO CRÍTICO DE SENSOR)
        if EstadoParking.get('LimiteInferior', 0) == 1 and EstadoParking.get('LimiteSuperior', 0) == 1:
            return "Fallo en sensores de limite: ambos activos simultáneamente."
           
        # Check 2: Múltiples Sensores de Alineación de Nivel ACTIVOS a la vez (FALLO CRÍTICO DE ALINEACIÓN)
        active_alignments = (EstadoParking.get('AlineacionNivel1', 0) +
                             EstadoParking.get('AlineacionNivel2', 0) +
                             EstadoParking.get('AlineacionNivel3', 0))
        if active_alignments > 1:
            return "Múltiples sensores de nivel activos simultáneamente."
       
        return None # No hay error crítico detectado en los sensores

    def _process_serial_data(self, data):
        """Analiza la línea de datos recibida y actualiza el EstadoGlobal."""
        global EstadoParking, EstadoCritico, MensajeCritico
       
        # 1. LÓGICA DE ALARMAS Y ERRORES CRÍTICO S (del Arduino)
        if data.startswith("CRITICO_"):
            arduino_message = data.replace("CRITICO_", "")
            self._trigger_critical_error(f"Fallo reportado por Arduino: {arduino_message}", source="Arduino")
            return
           
        # 2. LÓGICA DE LIMPIEZA/INICIALIZACIÓN POR ARDUINO
        elif data == "SistemaInicializado" or data == "CalibracionFinalizada":
            # Si el sistema reporta una inicialización o calibración exitosa, limpiamos el estado.
            if EstadoCritico:
                EstadoCritico = False
                MensajeCritico = ""
                print("[RX] Estado Crítico Limpiado por Inicialización/Calibración Exitosa (Arduino).")
            # Mensajes de voz de inicialización y calibración ELIMINADOS.
            return
               
        # 3. PROCESAMIENTO DE TRAMA DE ESTADO
        elif data.startswith("Estacionamiento1:"):
            parts = data.split(',')
            new_data = {}
            for part in parts:
                if ':' in part:
                    key, value = part.split(':')
                    new_data[key] = value

            # Actualizar el diccionario global de estado
            for key, value in new_data.items():
                if key in EstadoParking:
                    if key in ["Estacionamiento1", "Estacionamiento2", "Estacionamiento3", "AutoNuevo1", "AutoNuevo2", "LimiteInferior", "LimiteSuperior", "MotorActivo", "AlineacionNivel1", "AlineacionNivel2", "AlineacionNivel3"]:
                        EstadoParking[key] = int(value)
                    else:
                        EstadoParking[key] = value
           
            # --- NUEVA LÓGICA: VERIFICACIÓN Y LIMPIEZA DE ESTADO CRÍTICO EN PYTHON ---
            error_cause = self._get_critical_error_cause()
           
            if error_cause is not None:
                # Condición de error CUMPLIDA. Si no estaba crítico, lo activamos.
                if not EstadoCritico:
                    self._trigger_critical_error(error_cause, "Python")
            else:
                # Condición de error NO CUMPLIDA. Si estaba crítico, lo limpiamos.
                if EstadoCritico:
                    EstadoCritico = False
                    MensajeCritico = ""
                    print("[RX] Estado Crítico Limpiado por Resolución de Condición de Sensor (Python).")
                    # Usar wait=False aquí, no es crítico
                    tts_speak("Condición crítica resuelta. Sistema operativo nuevamente.")


        # 4. OTROS EVENTOS SIMPLES
        else:
            if data.startswith("ACK_") or data.startswith("ERROR:") or data.startswith("Alerta:"):
                print(f"[RX] MENSAJE/ALARMA: {data}")
            elif data == "MovimientoFinalizado":
                print("--- Movimiento del ascensor terminado. ---")
            else:
                print(f"[RX] EVENTO SIMPLE: {data}")

# =========================================================================
# Parking System - Parte 2: VENTANA DE OPERADOR (Añadido Scrollbar)
# =========================================================================

class VentanaOperador:
    def __init__(self, master, comm_instance):
        self.master = master
        self.parkCom = comm_instance
        self.window = tk.Toplevel(master)
        self.window.title("Ventana del Operador")
        self.window.geometry("850x750")
        self.window.configure(bg='#2c3e50')
       
        # Estilos
        self.font_title = ('Arial', 16, 'bold')
        self.font_label = ('Arial', 10)
        self.font_btn = ('Arial', 10, 'bold')
        self.style_bg = '#34495e'
        self.style_fg = 'white'

        # Variables de estado para la GUI
        self.status_text = tk.StringVar(value="Intentando Conectar...")
        self.critical_text = tk.StringVar(value="SISTEMA OK")
       
        # Inicializar la interfaz de usuario con Scrollbar
        self.create_widgets()
       
        self.actualizar_gui()
       
    def _on_frame_configure(self, event):
        """Ajusta la región de desplazamiento del canvas al tamaño del frame."""
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_resize(self, event):
        """Ajusta el ancho del frame de contenido al ancho del canvas."""
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def create_widgets(self):
        # --- Configuración de Canvas y Scrollbar ---
        self.canvas = tk.Canvas(self.window, bg='#2c3e50', highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.window, orient="vertical", command=self.canvas.yview)
       
        self.scrollable_frame = tk.Frame(self.canvas, padx=10, pady=10, bg='#2c3e50')
       
        # Conectar Canvas y Scrollbar
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        # Colocar el frame dentro del canvas
        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")

        # Configurar eventos para el scroll
        self.scrollable_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind('<Configure>', self._on_canvas_resize)
       
        # El frame principal para el resto del código es self.scrollable_frame
        main_frame = self.scrollable_frame
        # --------------------------------------------

        # ----------------------------------------------------
        # Título y Estado de Conexión
        # ----------------------------------------------------
        tk.Label(main_frame, text="Panel de Control del Parking Inteligente", font=self.font_title, bg='#2c3e50', fg=self.style_fg).pack(pady=5)
       
        status_frame = tk.Frame(main_frame, bg='#2c3e50')
        status_frame.pack(pady=5)
        tk.Label(status_frame, text="Estado Serial:", font=self.font_label, bg='#2c3e50', fg=self.style_fg).pack(side='left', padx=5)
        self.status_label = tk.Label(status_frame, textvariable=self.status_text, font=self.font_btn, bg='#2c3e50', fg='red')
        self.status_label.pack(side='left', padx=5)
       
        # ----------------------------------------------------
        # ALERTA CRÍTICA
        # ----------------------------------------------------
        critical_frame = tk.Frame(main_frame, bg='black', padx=10, pady=10, bd=5, relief=tk.RAISED)
        critical_frame.pack(fill='x', pady=10)
       
        self.critical_label = tk.Label(critical_frame, textvariable=self.critical_text,
                                       font=('Arial', 14, 'bold'), bg='black', fg='lime', wraplength=800)
        self.critical_label.pack(fill='x')
       
        # ----------------------------------------------------
        # 1. Panel de CONTROL CRÍTICO Y HOMING
        # ----------------------------------------------------
        safety_frame = tk.LabelFrame(main_frame, text="Control Crítico y Homing", font=self.font_title, padx=10, pady=10, bg='#505050', fg='white')
        safety_frame.pack(fill='x', pady=10)
       
        self.btn_calibrar = tk.Button(safety_frame, text="CALIBRAR SISTEMA (HOMING)", command=self.send_homing_command,
                  font=self.font_btn, bg='#3498db', fg='white', relief=tk.RAISED)
        self.btn_calibrar.grid(row=0, column=0, padx=10, pady=5)
                 
        self.btn_parada = tk.Button(safety_frame, text="PARADA MANUAL CRÍTICA", command=lambda: self.parkCom.send_command("PararAscensor"),
                  font=self.font_btn, bg='#e74c3c', fg='black', relief=tk.RAISED)
        self.btn_parada.grid(row=0, column=1, padx=10, pady=5)
       
        # ----------------------------------------------------
        # 2. Panel de CONTROL DE FLUJO
        # ----------------------------------------------------
        flow_control_frame = tk.LabelFrame(main_frame, text="Control de Flujo (Ingreso/Egreso)", font=self.font_title, padx=10, pady=10, bg=self.style_bg, fg=self.style_fg)
        flow_control_frame.pack(fill='x', pady=10)

        tk.Label(flow_control_frame, text="El inicio de los flujos de Ingreso y Egreso se ha movido a la Ventana del Usuario.",
                 font=self.font_label, bg=self.style_bg, fg='#f1c40f').pack(padx=10, pady=10)

        # ----------------------------------------------------
        # 3. Panel de Sensores (Lectura de Estado)
        # ----------------------------------------------------
        sensor_frame = tk.LabelFrame(main_frame, text="Reporte de Sensores y Estados", font=self.font_title, padx=10, pady=10, bg=self.style_bg, fg=self.style_fg)
        sensor_frame.pack(fill='x', pady=10)

        est_frame = tk.Frame(sensor_frame, bg=self.style_bg)
        est_frame.grid(row=0, column=0, padx=20, pady=5, sticky='n')
        tk.Label(est_frame, text="OCUPACIÓN DE PISOS", font=self.font_btn, bg=self.style_bg, fg='yellow').pack()
        self.labels_est = {}
        for i in range(1, 4):
            key = f"Estacionamiento{i}"
            label = tk.Label(est_frame, text=f"Piso {i}: LIBRE", font=self.font_label, bg=self.style_bg, fg=self.style_fg)
            label.pack(anchor='w', pady=1)
            self.labels_est[key] = label

        alarm_frame = tk.Frame(sensor_frame, bg=self.style_bg)
        alarm_frame.grid(row=0, column=1, padx=20, pady=5, sticky='n')
        tk.Label(alarm_frame, text="LÍMITES / BARRERA / MOTOR", font=self.font_btn, bg=self.style_bg, fg='yellow').pack()
        self.labels_alarm = {}
        for key in ["LimiteInferior", "LimiteSuperior", "MotorActivo", "EstadoBarrera", "Plataforma en PB"]:
            text_map = {"LimiteInferior": "Límite Inferior", "LimiteSuperior": "Límite Superior",
                        "MotorActivo": "Motor Activo", "EstadoBarrera": "Barrera",
                        "Plataforma en PB": "Plat. Planta Baja"}
            label = tk.Label(alarm_frame, text=f"{text_map.get(key, key)}: DESCONOCIDO", font=self.font_label, bg=self.style_bg, fg=self.style_fg)
            label.pack(anchor='w', pady=1)
            self.labels_alarm[key] = label
           
        det_frame = tk.Frame(sensor_frame, bg=self.style_bg)
        det_frame.grid(row=0, column=2, padx=20, pady=5, sticky='n')
        tk.Label(det_frame, text="DETECCIÓN DE VEHÍCULO", font=self.font_btn, bg=self.style_bg, fg='yellow').pack()
        self.labels_auto = {}
        self.labels_auto["AutoNuevo1"] = tk.Label(det_frame, text="Sensor 1 (Entrada): NO", font=self.font_label, bg=self.style_bg, fg=self.style_fg)
        self.labels_auto["AutoNuevo1"].pack(anchor='w', pady=1)
        self.labels_auto["AutoNuevo2"] = tk.Label(det_frame, text="Sensor 2 (Posición): NO", font=self.font_label, bg=self.style_bg, fg=self.style_fg)
        self.labels_auto["AutoNuevo2"].pack(anchor='w', pady=1)
       
        align_frame = tk.Frame(sensor_frame, bg=self.style_bg)
        align_frame.grid(row=1, column=0, columnspan=3, pady=10)
        tk.Label(align_frame, text="POSICIÓN VERTICAL DE PLATAFORMA (Sensores de Nivel)", font=self.font_btn, bg=self.style_bg, fg='yellow').pack()
        self.labels_align = {}
        for i in range(1, 4):
            key = f"AlineacionNivel{i}"
            label = tk.Label(align_frame, text=f"Sensor Nivel {i}: INACTIVO", font=self.font_label, bg=self.style_bg, fg=self.style_fg)
            label.pack(side='left', padx=10)
            self.labels_align[key] = label
       
           
        # ----------------------------------------------------
        # 4. Control Directo (Debugging)
        # ----------------------------------------------------
        direct_control_frame = tk.LabelFrame(main_frame, text="Control Directo (Debug)", font=self.font_title, padx=10, pady=10, bg=self.style_bg, fg=self.style_fg)
        direct_control_frame.pack(fill='x', pady=10)
       
        tk.Label(direct_control_frame, text="Mover a Nivel:", font=self.font_label, bg=self.style_bg, fg=self.style_fg).grid(row=0, column=0, padx=5, pady=5, sticky='w')
        tk.Button(direct_control_frame, text="1 (Alto)", command=lambda: self.parkCom.send_command("MoverAscensorPiso1"), bg='#bdc3c7').grid(row=0, column=1, padx=5, pady=5)
        tk.Button(direct_control_frame, text="2 (Medio)", command=lambda: self.parkCom.send_command("MoverAscensorPiso2"), bg='#bdc3c7').grid(row=0, column=2, padx=5, pady=5)
        tk.Button(direct_control_frame, text="3 (Bajo)", command=lambda: self.parkCom.send_command("MoverAscensorPiso3"), bg='#bdc3c7').grid(row=0, column=3, padx=5, pady=5)
       
        tk.Label(direct_control_frame, text="Barrera:", font=self.font_label, bg=self.style_bg, fg=self.style_fg).grid(row=0, column=4, padx=(20, 5), pady=5, sticky='w')
        tk.Button(direct_control_frame, text="Abrir", command=lambda: self.parkCom.send_command("AbrirBarrera"), bg='#3498db').grid(row=0, column=5, padx=5, pady=5)
        tk.Button(direct_control_frame, text="Cerrar", command=lambda: self.parkCom.send_command("CerrarBarrera"), bg='#bdc3c7').grid(row=0, column=6, padx=5, pady=5)


    # =========================================================================
    # LÓGICA DE FLUJO DE TRABAJO Y COMANDOS (Funciones mantenidas, no accesibles por botón)
    # =========================================================================
   
    def send_homing_command(self):
        """Comando específico para la Calibración/Homing."""
        global EstadoCritico, MensajeCritico
        if EstadoCritico:
            messagebox.showerror("ERROR CRÍTICO", f"Operación bloqueada. Resuelva: {MensajeCritico}")
            return
           
        if messagebox.askyesno("Confirmar Homing", "¿Está seguro de iniciar el proceso de Calibración (Homing)? El ascensor se moverá al límite inferior."):
            self.parkCom.send_command("CalibrarSistema")
           
    # Las funciones dummy start_parking_flow, start_retrieval_flow y find_free_spot
    # han sido removidas de aquí, ya que el control está en VentanaUsuario.
       
    def actualizar_gui(self):
        """Actualiza los indicadores de la GUI del operador basándose en el EstadoGlobal."""
        global EstadoCritico, MensajeCritico, EstadoParking
       
        # 1. Actualizar estado crítico y bloquear/habilitar botones
        if EstadoCritico:
            self.critical_text.set(MensajeCritico)
            self.critical_label.config(bg='#e74c3c', fg='black')
            self.btn_calibrar.config(state=tk.NORMAL, bg='#3498db', fg='white')
            self.btn_parada.config(state=tk.NORMAL, bg='#e74c3c', fg='black')
        else:
            self.critical_text.set("SISTEMA OPERACIONAL (OK)")
            self.critical_label.config(bg='black', fg='lime')
            self.btn_calibrar.config(state=tk.NORMAL, bg='#3498db', fg='white')
            self.btn_parada.config(state=tk.NORMAL, bg='#e74c3c', fg='black')

        # 2. Actualizar estado de conexión
        if self.parkCom.ser and self.parkCom.ser.is_open:
            self.status_text.set(f"CONECTADO: {self.parkCom.port}")
            self.status_label.config(fg='green')
        else:
            self.status_text.set("DESCONECTADO/ERROR")
            self.status_label.config(fg='red')

        # 3. Actualizar Sensores de Estacionamiento
        for i in range(1, 4):
            key = f"Estacionamiento{i}"
            state = EstadoParking.get(key, 0)
            if state == 1:
                self.labels_est[key].config(text=f"Piso {i}: OCUPADO", fg='#e74c3c', font=self.font_btn)
            else:
                self.labels_est[key].config(text=f"Piso {i}: LIBRE", fg='#2ecc71', font=self.font_btn)

        # 4. Actualizar Detección de Plataforma
        self.labels_auto["AutoNuevo1"].config(text=f"Sensor 1 (Entrada): {'SÍ' if EstadoParking.get('AutoNuevo1') == 1 else 'NO'}",
                                               fg='#e74c3c' if EstadoParking.get('AutoNuevo1') == 1 else self.style_fg)
        self.labels_auto["AutoNuevo2"].config(text=f"Sensor 2 (Posición): {'SÍ' if EstadoParking.get('AutoNuevo2') == 1 else 'NO'}",
                                               fg='#e74c3c' if EstadoParking.get('AutoNuevo2') == 1 else self.style_fg)

        # 5. Actualizar Límites y Alarmas
        self.labels_alarm["LimiteInferior"].config(text=f"Límite Inferior: {'ACTIVO' if EstadoParking.get('LimiteInferior') == 1 else 'INACTIVO'}",
                                                   fg='red' if EstadoParking.get('LimiteInferior') == 1 else self.style_fg)
        self.labels_alarm["LimiteSuperior"].config(text=f"Límite Superior: {'ACTIVO' if EstadoParking.get('LimiteSuperior') == 1 else 'INACTIVO'}",
                                                   fg='red' if EstadoParking.get('LimiteSuperior') == 1 else self.style_fg)
        self.labels_alarm["MotorActivo"].config(text=f"Motor Activo: {'SÍ' if EstadoParking.get('MotorActivo') == 1 else 'NO'}",
                                                fg='red' if EstadoParking.get('MotorActivo') == 1 else self.style_fg)
        self.labels_alarm["EstadoBarrera"].config(text=f"Barrera: {EstadoParking.get('EstadoBarrera', 'Cerrada')}",
                                                  fg='red' if EstadoParking.get('EstadoBarrera') == 'Cerrada' else '#3498db')
       
        estado_pb = EstadoParking.get('Plataforma en PB', 'No')
        self.labels_alarm["Plataforma en PB"].config(text=f"Plat. Planta Baja: {estado_pb}",
                                                    fg='#2ecc71' if estado_pb == 'Si' else self.style_fg)
       
        # 6. Actualizar POSICIÓN VERTICAL DE PLATAFORMA (Sensores de Nivel)
        for i in range(1, 4):
            key = f"AlineacionNivel{i}"
            state = EstadoParking.get(key, 0)
           
            if state == 1:
                text = f"NIVEL {i}: ALINEADO / EN PLANTA"
                color = '#2ecc71'
                font_style = self.font_btn
            else:
                text = f"Nivel {i}: INACTIVO"
                color = self.style_fg
                font_style = self.font_label
               
            self.labels_align[key].config(text=text, fg=color, font=font_style)
                                                   

        self.window.after(INTERVALO_SUPERVISION_MS, self.actualizar_gui)

# =========================================================================
# Parking System - Parte 3: VENTANA DE USUARIO Y BLOQUE MAIN (Scrollbar OK)
# =========================================================================

# 5. VENTANA DE USUARIO (Pantalla y Teclado)

class VentanaUsuario:
    def __init__(self, master, comm_instance):
        self.master = master
        self.parkCom = comm_instance
        self.window = tk.Toplevel(master)
        self.window.title("Ventana del Usuario")
        self.window.geometry("500x700")
        self.window.configure(bg='#1c1c1c')
        self.window.resizable(False, False) # Bloquear redimensionamiento
       
        # Estilos
        self.font_h1 = ('Arial', 20, 'bold')
        self.font_h2 = ('Arial', 14, 'bold')
        self.font_display = ('Arial', 18, 'bold')
        self.font_btn = ('Arial', 14, 'bold')
        self.style_bg = '#1c1c1c'
        self.style_fg = '#2ecc71'
       
        # --- Configuración de Canvas y Scrollbar ---
        self.canvas = tk.Canvas(self.window, bg=self.style_bg, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.window, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
       
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.current_frame = None # Frame de contenido actual
        self.canvas_window = None # ID de la ventana creada en el canvas

        # Variables de control
        self.dni_input_var = tk.StringVar()
        self.dni_display_var = tk.StringVar(value="------")
       
        global BienvenidaReproducida
        if not BienvenidaReproducida:
            # Mensaje de bienvenida solicitado por el usuario.
            # Usar wait=False ya que es una voz de inicialización
            tts_speak("Bienvenido al Parking vertical, Ingrese una opcion para inicar.")
            BienvenidaReproducida = True
           
        # Empezar con la ventana de menú de opciones
        self.show_menu_frame()
       
        self.actualizar_gui()
       
    def _on_frame_configure(self, event):
        """Ajusta la región de desplazamiento del canvas al tamaño del frame."""
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
       
    def _on_canvas_resize(self, event):
        """Ajusta el ancho del frame de contenido al ancho del canvas."""
        if self.canvas_window:
            self.canvas.itemconfig(self.canvas_window, width=event.width)

    def clear_frame(self):
        """Destruye el frame actual y limpia el canvas."""
        if self.current_frame:
            self.current_frame.destroy()
            self.current_frame = None
            self.canvas.delete("all")
            self.canvas_window = None
           
    # =========================================================================
    # LÓGICA DE VISTAS
    # =========================================================================
   
    def show_menu_frame(self):
        """Muestra el menú inicial con los botones INGRESAR y RETIRAR."""
        self.clear_frame()
       
        # Crear un frame para el contenido dentro del Canvas
        menu_frame = tk.Frame(self.canvas, padx=20, pady=20, bg=self.style_bg)
        self.current_frame = menu_frame
       
        # Agregar el frame al canvas y configurar el scroll
        self.canvas_window = self.canvas.create_window((0, 0), window=menu_frame, anchor="nw")
        menu_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind('<Configure>', self._on_canvas_resize)
       
        tk.Label(menu_frame, text="PARKING AUTOMÁTICO", font=self.font_h1, bg=self.style_bg, fg=self.style_fg).pack(pady=50)
       
        # Botón INGRESAR AUTO
        tk.Button(menu_frame, text="INGRESAR AUTO",
                  command=self.start_parking_flow,
                  font=self.font_btn, bg='#2ecc71', fg='white',
                  width=25, height=3, relief=tk.RAISED, bd=5).pack(pady=20)
       
        # Botón RETIRAR AUTO
        tk.Button(menu_frame, text="RETIRAR AUTO",
                  command=self.start_retrieval_flow,
                  font=self.font_btn, bg='#f39c12', fg='black',
                  width=25, height=3, relief=tk.RAISED, bd=5).pack(pady=20)
                 
        self.label_mensaje = tk.Label(menu_frame, text="Seleccione una opción para continuar.",
                                      font=self.font_h2, bg=self.style_bg, fg='yellow', wraplength=450)
        self.label_mensaje.pack(pady=30)


    def show_dni_input_frame(self):
        """Muestra la interfaz del teclado numérico y entrada de DNI."""
        self.clear_frame()
       
        main_frame = tk.Frame(self.canvas, padx=20, pady=20, bg=self.style_bg)
        self.current_frame = main_frame
       
        # Agregar el frame al canvas y configurar el scroll
        self.canvas_window = self.canvas.create_window((0, 0), window=main_frame, anchor="nw")
        main_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind('<Configure>', self._on_canvas_resize)

        tk.Label(main_frame, text="PARKING AUTOMÁTICO", font=self.font_h1, bg=self.style_bg, fg=self.style_fg).pack(pady=10)
       
        # La etiqueta de mensaje se actualizará con actualizar_gui
        self.label_mensaje = tk.Label(main_frame, text="Esperando Instrucción...",
                                      font=self.font_h2, bg=self.style_bg, fg='yellow', wraplength=450)
        self.label_mensaje.pack(pady=20)
       
        dni_frame = tk.Frame(main_frame, bg='#333333', padx=15, pady=15, relief='raised', bd=3)
        dni_frame.pack(pady=15, fill='x')
       
        flow_text = "INGRESE DNI (6-8 DÍGITOS)" if ParkingAsignado == 1 else "INGRESE DNI / CÓDIGO (6-8 DÍGITOS)"
        tk.Label(dni_frame, text=flow_text, font=self.font_h2, bg='#333333', fg='white').pack()
       
        self.dni_display = tk.Label(dni_frame, textvariable=self.dni_display_var, font=self.font_display,
                                     bg='#000000', fg='#f1c40f', width=10, pady=10)
        self.dni_display.pack(pady=10)

        self.keyboard_frame = tk.Frame(main_frame, bg=self.style_bg)
        self.keyboard_frame.pack(pady=20)

        buttons = [
            '1', '2', '3',
            '4', '5', '6',
            '7', '8', '9',
            'LIMPIAR', '0', 'CONFIRMAR'
        ]
       
        self.key_buttons = {}
        row_val = 0
        col_val = 0
        for button in buttons:
            action = lambda x=button: self.button_click(x)
           
            if button in ['LIMPIAR', 'CONFIRMAR']:
                color = '#e74c3c' if button == 'LIMPIAR' else '#2ecc71'
                width = 10
            else:
                color = '#555555'
                width = 5
               
            btn = tk.Button(self.keyboard_frame, text=button, width=width, height=2, font=self.font_btn,
                      bg=color, fg='white', command=action, relief=tk.RAISED, bd=3)
            self.key_buttons[button] = btn
            btn.grid(row=row_val, column=col_val, padx=5, pady=5)
           
            col_val += 1
            if col_val > 2:
                col_val = 0
                row_val += 1
               
        # Botón de Regreso/Cancelar
        tk.Button(main_frame, text="<< VOLVER AL MENÚ",
                  command=self.cancel_flow_and_return_to_menu,
                  font=('Arial', 10), bg='#34495e', fg='white').pack(pady=10)
       
    def show_dni_confirmation_frame(self, identifier):
        """Muestra la pantalla de confirmación del DNI/Código ingresado."""
        self.clear_frame()
       
        main_frame = tk.Frame(self.canvas, padx=20, pady=20, bg=self.style_bg)
        self.current_frame = main_frame
       
        self.canvas_window = self.canvas.create_window((0, 0), window=main_frame, anchor="nw")
        main_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind('<Configure>', self._on_canvas_resize)

        flow_type = "INGRESO" if ParkingAsignado == 1 else "EGRESO"
       
        tk.Label(main_frame, text=f"FLUJO DE {flow_type}", font=self.font_h1, bg=self.style_bg, fg=self.style_fg).pack(pady=10)
       
        # 1. Mensaje de pregunta
        self.label_mensaje = tk.Label(main_frame, text="¿El DNI / Código ingresado es correcto?",
                                      font=self.font_h2, bg=self.style_bg, fg='yellow', wraplength=450)
        self.label_mensaje.pack(pady=30)
       
        # 2. Display del DNI/Código ingresado
        tk.Label(main_frame, text="DNI / CÓDIGO", font=('Arial', 16, 'bold'), bg='#333333', fg='white').pack()
        tk.Label(main_frame, text=identifier, font=self.font_display,
                 bg='#000000', fg='#f1c40f', width=10, pady=10).pack(pady=10)
       
        # 3. Botones de confirmación
        button_frame = tk.Frame(main_frame, bg=self.style_bg)
        button_frame.pack(pady=40)
       
        # SI, el DNI es CORRECTO
        tk.Button(button_frame, text="SI, es CORRECTO",
                  command=lambda: self.handle_dni_confirmation(True, identifier),
                  font=self.font_btn, bg='#2ecc71', fg='white',
                  width=18, height=3, relief=tk.RAISED, bd=5).grid(row=0, column=0, padx=10)
       
        # NO, no es CORRECTO
        tk.Button(button_frame, text="NO, no es CORRECTO",
                  command=lambda: self.handle_dni_confirmation(False, identifier),
                  font=self.font_btn, bg='#e74c3c', fg='black',
                  width=18, height=3, relief=tk.RAISED, bd=5).grid(row=0, column=1, padx=10)

        # >>> CORRECCIÓN CLAVE <<<
        # Forzar la actualización inmediata de la GUI antes del audio bloqueante
        self.window.update()
       
        # 🔊 TTS: Pregunta de confirmación. Usar wait=True para asegurar que se escuche.
        tts_speak("Confirme si el DNI o código es correcto", wait=True)

    def show_parking_assigned_frame(self, dni, nivel_asignado, codigo_retiro):
        """Muestra el resultado final del ingreso: nivel asignado y código de retiro."""
        self.clear_frame()
       
        main_frame = tk.Frame(self.canvas, padx=20, pady=20, bg=self.style_bg)
        self.current_frame = main_frame
       
        self.canvas_window = self.canvas.create_window((0, 0), window=main_frame, anchor="nw")
        main_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind('<Configure>', self._on_canvas_resize)

        tk.Label(main_frame, text="PROCESO DE INGRESO EXITOSO", font=self.font_h1, bg=self.style_bg, fg=self.style_fg).pack(pady=10)
       
        # 1. Mensaje de bienvenida/instrucción
        self.label_mensaje = tk.Label(main_frame, text="Estacione su vehículo en la plataforma. La barrera está abierta.",
                                      font=self.font_h2, bg=self.style_bg, fg='yellow', wraplength=450)
        self.label_mensaje.pack(pady=20)
       
        # 2. Información del Estacionamiento
        info_frame = tk.Frame(main_frame, bg='#333333', padx=15, pady=15, relief='raised', bd=3)
        info_frame.pack(pady=15)
       
        tk.Label(info_frame, text=f"DNI INGRESADO: {dni}", font=('Arial', 14, 'bold'), bg='#333333', fg='white').pack()
        tk.Label(info_frame, text=f"NIVEL ASIGNADO:", font=('Arial', 16, 'bold'), bg='#333333', fg='lime').pack(pady=(10, 0))
        tk.Label(info_frame, text=f"PISO {nivel_asignado}", font=self.font_display,
                 bg='#000000', fg='#f1c40f', width=10, pady=10).pack(pady=5)
                 
        tk.Label(info_frame, text=f"CÓDIGO DE RETIRO:", font=('Arial', 16, 'bold'), bg='#333333', fg='lime').pack(pady=(15, 0))
        tk.Label(info_frame, text=f"{codigo_retiro}", font=self.font_display,
                 bg='#000000', fg='#f1c40f', width=10, pady=10).pack(pady=5)

        # 3. TTS y comandos
        self.window.update()
        tts_speak(f"Bienvenido. Se le ha asignado el piso {nivel_asignado}. Su código de retiro es {', '.join(list(codigo_retiro))}. Recuerde su código.", wait=True)
       
        # 4. Comandos de salida del flujo: Abrir barrera y temporizador para volver al menú
        self.parkCom.send_command("AbrirBarrera") # Abrir barrera para el ingreso
       
        tk.Label(main_frame, text="La barrera se cerrará y el ascensor subirá automáticamente después de unos segundos.",
                                      font=('Arial', 10), bg=self.style_bg, fg='gray', wraplength=450).pack(pady=10)
       
        # Volver al menú después de 10 segundos para dar tiempo a estacionar y ver el código
        self.window.after(10000, self.show_menu_frame)


    # =========================================================================
    # LÓGICA DE FLUJO DE TRABAJO (Desde el Menú del Usuario)
    # =========================================================================

    def find_free_spot(self):
        """Busca el primer piso libre (prioridad de abajo hacia arriba: 3, 2, 1)."""
        global EstadoParking
        if EstadoParking.get('Estacionamiento3', 0) == 0:
            return 3
        if EstadoParking.get('Estacionamiento2', 0) == 0:
            return 2
        if EstadoParking.get('Estacionamiento1', 0) == 0:
            return 1
        return 0 # 0 significa lleno o no disponible
   
    def cancel_flow_and_return_to_menu(self):
        """Cancela el flujo activo y regresa al menú inicial."""
        global ParkingAsignado, DNI_ACTUAL, EstadoParking
       
        ParkingAsignado = None
        DNI_ACTUAL["TIEMPO_INICIO"] = 0
        self.dni_input_var.set("")
        self.dni_display_var.set("------")
       
        # En el flujo actual, solo cerramos la barrera si por alguna razón quedó abierta (de un flujo anterior).
        if EstadoParking.get('EstadoBarrera') == 'Abierta':
             self.parkCom.send_command("CerrarBarrera")
             # Usar wait=False
             tts_speak("Proceso cancelado. Barrera de entrada cerrada.")

        self.show_menu_frame()

    def start_parking_flow(self):
        """
        Inicia el proceso de ingreso de un vehículo.
        """
        global DNI_ACTUAL, ParkingAsignado, EstadoCritico
       
        if EstadoCritico:
            if hasattr(self, 'label_mensaje'): self.label_mensaje.config(text="Operación bloqueada. El sistema está en estado crítico.", fg='#e74c3c')
            tts_speak("Sistema crítico. Operación no disponible.", wait=True)
            return

        # Solo marcamos el flujo de ingreso activo (bandera 1)
        print(f"--- INICIANDO FLUJO DE INGRESO (Validación DNI) ---")
        ParkingAsignado = 1
       
        # Reset de intentos de DNI y setear tiempo de inicio
        DNI_ACTUAL = {"DNI": "", "INTENTOS": 0, "TIEMPO_INICIO": time.time()}
       
        # 1. Cambiar la vista INMEDIATAMENTE al teclado numérico
        self.show_dni_input_frame()
       
        # 2. 🔊 TTS INMEDIATO (USANDO wait=True para GARANTIZAR la reproducción)
        tts_speak("Por favor, Ingrese su DNI", wait=True)
       
        # 3. Actualizar la etiqueta visual
        if hasattr(self, 'label_mensaje'):
             self.label_mensaje.config(text="INGRESAR AUTO: Ingrese su DNI para continuar.", fg='yellow')


    def start_retrieval_flow(self):
        """Inicia el proceso de egreso de un vehículo. Pide DNI/Código y muestra teclado al inicio."""
        global EstadoCritico, ParkingAsignado, DNI_ACTUAL
       
        if EstadoCritico:
            if hasattr(self, 'label_mensaje'): self.label_mensaje.config(text="Operación bloqueada. El sistema está en estado crítico.", fg='#e74c3c')
            tts_speak("Sistema crítico. Operación no disponible.", wait=True)
            return
           
        # 0 indica flujo de egreso
        ParkingAsignado = 0
        DNI_ACTUAL = {"DNI": "", "INTENTOS": 0, "TIEMPO_INICIO": time.time()}

        # 1. Cambiar la vista INMEDIATAMENTE al teclado numérico
        self.show_dni_input_frame()
       
        # 2. 🔊 TTS INMEDIATO (USANDO wait=True para GARANTIZAR la reproducción)
        tts_speak("Por favor, Ingrese su DNI o código de retiro", wait=True)
       
        # 3. Actualizar la etiqueta visual
        if hasattr(self, 'label_mensaje'):
             self.label_mensaje.config(text="RETIRAR AUTO: Ingrese su DNI o Código de Retiro.", fg='yellow')
       

    def set_keyboard_state(self, state):
        """Habilita o deshabilita los botones del teclado."""
        if hasattr(self, 'key_buttons') and self.current_frame and self.current_frame.winfo_exists():
            for btn in self.key_buttons.values():
                btn.config(state=state)

    def button_click(self, char):
        """Maneja la lógica del teclado numérico."""
        current_dni = self.dni_input_var.get()
        global EstadoCritico, DNI_ACTUAL, EstadoParking
       
        if EstadoCritico or DNI_ACTUAL["TIEMPO_INICIO"] == 0:
            return
             
        if char.isdigit():
            # Permitir hasta 8 dígitos para el DNI
            if len(current_dni) < 8:
                new_dni = current_dni + char
                self.dni_input_var.set(new_dni)
                self.update_dni_display(new_dni)
               
        elif char == 'LIMPIAR':
            self.dni_input_var.set("")
            self.update_dni_display("")
           
        elif char == 'CONFIRMAR':
            self.process_dni_confirmation(current_dni)

    # =========================================================================
    # LÓGICA DE FLUJO DE USUARIO (DNI/CÓDIGO)
    # =========================================================================

    def update_dni_display(self, dni):
        """Formatea el DNI en la pantalla con guiones si es necesario."""
        # Se muestra la entrada hasta 8 dígitos
        display = dni.ljust(8, '-')
        self.dni_display_var.set(display[:8])
       
    def process_dni_confirmation(self, identifier):
        """Lógica al confirmar el DNI/Código (simulación). Ahora lleva a la pantalla de confirmación."""
        global DNI_ACTUAL, ParkingAsignado
       
        # Requerir un mínimo de 6 dígitos (DNI de 6, 7 u 8, o Código de 6)
        if len(identifier) < 6:
            DNI_ACTUAL["INTENTOS"] += 1
            msg = "Identificador incompleto"
            self.label_mensaje.config(text=f"ERROR: {msg}. Intento {DNI_ACTUAL['INTENTOS']}/{DNI_INTENTOS_MAX}.", fg='#e74c3c')
            tts_speak("Error de ingreso. Identificador incompleto. Por favor, intente de nuevo.", wait=False)
            self.dni_input_var.set("")
            self.update_dni_display("")
            return
       
        # Si la longitud es válida, pasamos a la pantalla de confirmación.
       
        # Resetear intentos de DNI, ya que el usuario va a confirmar.
        DNI_ACTUAL["INTENTOS"] = 0
       
        # Mover a la pantalla de confirmación
        self.show_dni_confirmation_frame(identifier)

    def handle_dni_confirmation(self, is_correct, identifier):
        """Maneja la acción de los botones SI/NO en la pantalla de confirmación."""
        global DNI_ACTUAL, ParkingAsignado, RegistroVehiculos, EstadoParking
       
        if not is_correct:
            # Opción NO: Regresar al teclado y limpiar el input.
            self.dni_input_var.set("")
            self.update_dni_display("")
            self.show_dni_input_frame()
            tts_speak("Por favor, Ingrese nuevamente su DNI o código", wait=True)
            return
           
        # Opción SI: Continuar con la lógica del flujo (Ingreso o Egreso)
       
        # --- Lógica de INGRESO (ParkingAsignado == 1) ---
        if ParkingAsignado == 1:
             
             nivel_asignado = self.find_free_spot()
             
             if nivel_asignado == 0:
                 # Parking Lleno
                 self.show_menu_frame() # Vuelve al menú, luego muestra el error
                 messagebox.showerror("Parking Lleno", "Lo sentimos, el estacionamiento está completo. Por favor, espere a que se libere un lugar.")
                 tts_speak("Parking completo. No es posible el ingreso.", wait=True)
                 # Limpiar variables
                 ParkingAsignado = None
                 DNI_ACTUAL["TIEMPO_INICIO"] = 0
                 return

             # 1. Generar Código y Registrar el Vehículo
             codigo_retiro = generate_retrieval_code()
             RegistroVehiculos[identifier] = {
                 "codigo": codigo_retiro,
                 "nivel_asignado": nivel_asignado
             }
             
             # 2. Mover la plataforma a la Planta Baja (Si no está ahí)
             # Esto debería hacerse como parte del proceso de ingreso, antes de pedir que estacione.
             if EstadoParking.get('Plataforma en PB') != 'Si':
                  # Asumimos que MoverAscensorPiso3 lleva la plataforma al nivel de la barrera (PB/Salida)
                  self.parkCom.send_command("MoverAscensorPiso3")
                 
             # 3. Mostrar la pantalla de asignación con el código
             self.show_parking_assigned_frame(identifier, nivel_asignado, codigo_retiro)
             
             # 4. Limpiar variables
             ParkingAsignado = None # El flujo de validación se completa aquí.
             DNI_ACTUAL["TIEMPO_INICIO"] = 0
             self.dni_input_var.set("")
             self.update_dni_display("")

             
        # --- Lógica de EGRESO (ParkingAsignado == 0) ---
        elif ParkingAsignado == 0:
             
             nivel_a_sacar = None
             
             # 1. Búsqueda por DNI (si es 6-8 dígitos y está registrado como llave DNI)
             if identifier in RegistroVehiculos:
                 nivel_a_sacar = RegistroVehiculos[identifier]["nivel_asignado"]
             
             # 2. Búsqueda por Código (si la búsqueda por DNI falla)
             if nivel_a_sacar is None:
                 for dni, data in RegistroVehiculos.items():
                     if data["codigo"] == identifier:
                         nivel_a_sacar = data["nivel_asignado"]
                         break
                         
             if nivel_a_sacar is not None:
                 # Iniciar el movimiento de egreso
                 if EstadoParking.get(f'Estacionamiento{nivel_a_sacar}') == 1:
                      self.parkCom.send_command(f"MoverAscensorPiso{nivel_a_sacar}")
                      self.label_mensaje.config(text=f"EGRESO APROBADO: El vehículo será movido del Nivel {nivel_a_sacar} a la salida.", fg='#2ecc71')
                      tts_speak(f"Egreso aprobado. El vehículo será llevado a la planta baja.", wait=False)
                     
                      # Eliminar el registro (simulado)
                      key_to_delete = None
                      for dni, data in RegistroVehiculos.items():
                          if data.get("nivel_asignado") == nivel_a_sacar:
                              key_to_delete = dni
                              break
                      if key_to_delete:
                          del RegistroVehiculos[key_to_delete]
                         
                 else:
                      self.label_mensaje.config(text="ERROR: El nivel asignado aparece vacío.", fg='#e74c3c')
                      tts_speak("Error de sistema. El estacionamiento aparece vacío.", wait=False)

                 # Cerrar flujo y regresar al menú
                 ParkingAsignado = None
                 DNI_ACTUAL["TIEMPO_INICIO"] = 0
                 self.dni_input_var.set("")
                 self.update_dni_display("")
                 self.window.after(3000, self.show_menu_frame)
                 
             else:
                 # Identificador no encontrado (Debe volver al teclado para un nuevo intento)
                 DNI_ACTUAL["INTENTOS"] += 1
                 self.label_mensaje.config(text=f"ERROR: DNI o Código no encontrado. Intento {DNI_ACTUAL['INTENTOS']}/{DNI_INTENTOS_MAX}.", fg='#e74c3c')
                 tts_speak("Documento o código no válidos. Por favor, intente de nuevo.", wait=False)
                 
                 # Si falla después de confirmar, limpiamos y volvemos al teclado
                 self.dni_input_var.set("")
                 self.update_dni_display("")
                 self.window.after(1500, self.show_dni_input_frame)

        # --- Flujo desconocido/Inactivo ---
        else:
             self.label_mensaje.config(text="ERROR: Opción no disponible. Vuelva al menú inicial.", fg='#e74c3c')
             self.dni_input_var.set("")
             self.update_dni_display("")
             self.window.after(2000, self.show_menu_frame)


    def actualizar_gui(self):
        """Actualiza la GUI del usuario."""
        global EstadoCritico, DNI_ACTUAL, ParkingAsignado, EstadoParking, DNI_INTENTOS_MAX, TIEMPO_LIMITE_DNI_S
       
        # 1. Manejo de Estado Crítico (siempre visible)
        if EstadoCritico:
            mensaje = "El parking está en MANTENIMIENTO.\nPor favor, regrese más tarde.\nDisculpe las molestias."
            if hasattr(self, 'label_mensaje') and self.current_frame and self.current_frame.winfo_exists():
                self.label_mensaje.config(text=mensaje, fg='#e74c3c')
            self.set_keyboard_state(tk.DISABLED)
            if hasattr(self, 'dni_display_var'): self.dni_display_var.set("FALLO")
            self.window.after(INTERVALO_SUPERVISION_MS, self.actualizar_gui)
            return
           
        # 2. Lógica de flujo de DNI (solo si estamos en la vista de teclado)
        if self.current_frame and hasattr(self, 'keyboard_frame') and self.keyboard_frame.winfo_exists():
            self.set_keyboard_state(tk.NORMAL)
           
            tiempo_transcurrido = time.time() - DNI_ACTUAL["TIEMPO_INICIO"]
           
            # Chequeo de intentos y tiempo límite para ambos flujos (Ingreso y Egreso)
            if DNI_ACTUAL["INTENTOS"] >= DNI_INTENTOS_MAX or tiempo_transcurrido > TIEMPO_LIMITE_DNI_S:
                self.label_mensaje.config(text="Demasiados fallos/Tiempo expirado. Proceso cancelado.", fg='#e74c3c')
                ParkingAsignado = None
                DNI_ACTUAL["TIEMPO_INICIO"] = 0
                self.window.after(2000, self.show_menu_frame)
                return

            # 2a. Manejo de Flujo de Ingreso (ParkingAsignado == 1)
            # 1 es la bandera para el flujo de validación de DNI
            if ParkingAsignado == 1:
                self.label_mensaje.config(text=f"INGRESAR AUTO: Ingrese su DNI. (Intento {DNI_ACTUAL['INTENTOS'] + 1}/{DNI_INTENTOS_MAX})", fg='yellow')

            # 2b. Manejo de Flujo de Egreso (ParkingAsignado == 0)
            elif ParkingAsignado == 0:
                 self.label_mensaje.config(text=f"RETIRAR AUTO: Ingrese su DNI/Código. (Intento {DNI_ACTUAL['INTENTOS'] + 1}/{DNI_INTENTOS_MAX})", fg='yellow')
                     
        # 3. Estado de Espera (Por defecto - Menú activo)
        # Si estamos en la vista de menú o en la de confirmación, la etiqueta es manejada por el llamado a la vista.

        self.window.after(INTERVALO_SUPERVISION_MS, self.actualizar_gui)


# 6. VENTANA DE CONFIRMACIÓN (Pequeña ventana flotante para mensajes)

class VentanaConfirmacion:
    """Ventana placeholder para confirmaciones o mensajes que no sean del usuario/operador."""
    def __init__(self, master):
        self.master = master
        self.window = tk.Toplevel(master)
        self.window.title("Confirmación/Alerta")
        self.window.geometry("300x100+100+100")
        self.window.withdraw()
        tk.Label(self.window, text="Sistema de Log/Confirmación", bg='lightgray').pack(fill='both', expand=True)


# 7. INICIO DE LA APLICACIÓN PRINCIPAL (Root Tkinter)

if __name__ == '__main__':
    # Puerto Serial. ¡Asegúrate de que este puerto sea el correcto!
    COM_PORT = 'COM3'

    parkCom = ComunicacionParking(COM_PORT)
   
    root = tk.Tk()
    root.title("Control Central (No visible)")
    root.withdraw() # Oculta la ventana principal
   
    NivelRetiroSeleccionado = tk.StringVar(root, value="")
   
    # Abrir ventanas (Operador y Usuario)
    ventana_op = VentanaOperador(root, parkCom)
    ventana_user = VentanaUsuario(root, parkCom)
   
    # Ventana de Confirmación Externa (La pequeña) - Solo placeholder
    ventana_confirmacion = VentanaConfirmacion(root)
   
    def on_closing():
        """Maneja el cierre seguro de la aplicación."""
        if tts_engine:
            tts_engine.stop()
        parkCom.cerrarConexion()
        # Asegurarse de cerrar la aplicación completamente
        root.quit()
        sys.exit()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()
