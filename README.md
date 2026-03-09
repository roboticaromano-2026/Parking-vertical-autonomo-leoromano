# 🚗 Estacionamiento Vertical Autónomo (Escala 1:65) Leo Romano
> **Sistema de parking inteligente con arquitectura híbrida Python/Arduino, control de movimiento de precisión y lógica concurrente.**

![Estado del Proyecto](https://img.shields.io/badge/Estado-En%20Desarrollo-yellow)
![Licencia](https://img.shields.io/badge/Licencia-MIT-green)

Este proyecto consiste en el diseño y desarrollo de un prototipo de parking vertical automatizado para la optimización de espacios urbanos. El sistema integra hardware de potencia con una interfaz de usuario avanzada para gestionar el flujo vehicular de forma segura y eficiente. Aun está en proceso...

## 🚀 Características Técnicas
- **Arquitectura Híbrida:** Control de bajo nivel en microcontrolador (Arduino Mega) y gestión de alto nivel en PC (Python).
- **Multithreading:** Implementación de hilos en Python para garantizar la fluidez de la interfaz gráfica (GUI) mientras se procesa la comunicación Serial y el sistema de voz.
- **Protocolos de Seguridad:** Lógica de protección ante fallos de sensores, gestión de límites de carrera y sistema de búsqueda de origen (**Homing**).

## 🛠️ Stack Tecnológico
- **Software:** Python (Tkinter, Threading, PySerial), C++ (Arduino Core).
- **Hardware:** Motor paso a paso **NEMA 17**, Driver **DRV8825**, Servomotor SG90, Sensores Infrarrojos (IR) y Microswitches.
- **Cálculos de Ingeniería:** Ajuste de **VREF** para optimización de torque y modelado de diagramas de flujo funcionales.

## 🏗️ Estado Actual y Próximas Actualizaciones
El proyecto se encuentra funcional, aunque el repositorio está en fase de carga documental activa.
- [x] Código fuente de control (Arduino) e Interfaz (Python).
- [x] Documentación técnica principal (PDF).
- [ ] **Pendiente:** Terminar el desarrollo del codigo (python) para las funciones de control de hardware (movimientos fisicos). 
- [ ] **En desarrollo:** Planos mecánicos detallados del sistema de elevación.
- [ ] **En desarrollo:** Refactorización del módulo de sensores para mayor redundancia.
- [ ] **Pendiente:** Video demostrativo del prototipo en operación en Youtube.

## 📊 Metodología
El desarrollo se centró en la robustez del sistema, asegurando que el elevador responda correctamente a eventos críticos de seguridad y que la calibración del hardware permita un movimiento suave y preciso sin sobrecalentamientos en los drivers.

---
**Desarrollado por Leonardo Romano** *Técnico Superior en Automatización y Robótica (2026)*
